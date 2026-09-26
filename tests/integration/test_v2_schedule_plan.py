"""Step 28-a: the pending job set, listed from stored rows before anything is fetched.

ROADMAP Step 28 acceptance: "在任何抓取發生之前，就可以列出待處理的 job 集合". The plan
reads `trading_days`, `stocks`/`listings` and `fetches`; it takes no fetcher, so
it cannot fetch.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.schema_v2 import fetches
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import schedule as s
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=TAIPEI)
DAYS = (date(2026, 8, 31), date(2026, 9, 14), date(2026, 9, 15))


@pytest.fixture
def store(tmp_path) -> LocalRawArtifactStore:
    return LocalRawArtifactStore(tmp_path)


def _fetch(db, store, dataset, source, resource_key, fetched_at, status="succeeded",
           reason=None, purpose="gap_fill"):
    return record_fetch(
        db, FetchRecord(dataset, source, resource_key, None, purpose, "test:v1", "abc",
                        fetched_at),
        content=None if status in ("failed",) else resource_key.encode(),
        status=status, reason_code=reason, store=store)


@pytest.fixture
def seeded(db: Connection, store) -> None:
    calendar = _fetch(db, store, "trading_calendar", "twse", "twse:trading-calendar:2026-09",
                      datetime(2026, 9, 16, 3, 36, tzinfo=UTC))
    for day in DAYS:
        db.execute(sa.text("INSERT INTO trading_days (trade_date, fetch_id) VALUES (:d, :f)"),
                   {"d": day, "f": calendar})
    universe = _fetch(db, store, "stocks", "twse_isin", "twse_isin:test",
                      datetime(2026, 9, 25, tzinfo=UTC))
    db.execute(sa.text("INSERT INTO stocks (stock_id, name, fetch_id) VALUES ('2330', '台積電', :f)"),
               {"f": universe})
    db.execute(sa.text("INSERT INTO listings (stock_id, market, fetch_id) VALUES ('2330', 'sii', :f)"),
               {"f": universe})


def _planned(plan: s.Plan, key: str) -> dict:
    return {item.period: item.decision for item in plan.items if item.key == key}


def test_the_plan_lists_every_missing_trading_day_without_fetching(db, seeded) -> None:
    before = db.scalar(sa.select(sa.func.count()).select_from(fetches))
    plan = s.plan(db, NOW)

    prices = _planned(plan, "daily_prices/twse_mi_index")
    assert sorted(prices) == list(DAYS)
    assert {(d.state, d.dispatch, d.purpose) for d in prices.values()} == {
        ("missing", True, "gap_fill")}
    assert db.scalar(sa.select(sa.func.count()).select_from(fetches)) == before


def test_a_day_fetched_after_it_settled_and_rechecked_is_not_listed(db, store, seeded) -> None:
    _fetch(db, store, "daily_price", "twse_mi_index", "twse_mi_index:daily-quotes:2026-09-14",
           datetime(2026, 9, 15, 3, 30, tzinfo=TAIPEI))
    _fetch(db, store, "daily_price", "twse_mi_index", "twse_mi_index:daily-quotes:2026-09-14",
           datetime(2026, 9, 22, 4, 0, tzinfo=TAIPEI), purpose="correction_check")
    _fetch(db, store, "daily_price", "twse_mi_index", "twse_mi_index:daily-quotes:2026-09-15",
           datetime(2026, 9, 16, 3, 30, tzinfo=TAIPEI))

    prices = _planned(s.plan(db, NOW), "daily_prices/twse_mi_index")
    assert date(2026, 9, 14) not in prices
    # 09-15 settled at 09-16 03:00 and is due its correction check at 09-23 03:00.
    assert (prices[date(2026, 9, 15)].state, prices[date(2026, 9, 15)].purpose) == (
        "recheck", "correction_check")


def test_monthly_revenue_inside_its_window_is_a_first_capture(db, seeded) -> None:
    revenue = _planned(s.plan(db, NOW), "monthly_revenues/mops_t21sc03_sii")
    august = revenue[date(2026, 8, 1)]
    assert (august.state, august.dispatch, august.purpose) == ("missing", True, "first_capture")
    assert date(2026, 9, 1) not in revenue  # public from October: not due


def test_financial_reports_follow_the_listed_stocks_and_their_windows(db, seeded) -> None:
    reports = _planned(s.plan(db, NOW), "financial_reports/mops_t164sb01")
    assert (reports[("2330", 2026, 2)].dispatch, reports[("2330", 2026, 2)].purpose) == (
        True, "gap_fill")
    assert ("2330", 2026, 3) not in reports  # its window opens 2026-10-11
    # A filer missing an old quarter is usually one that was not public then:
    # history is a backfill's business, not the hourly loop's.
    assert ("2330", 2026, 1) not in reports
    assert ("2330", 2020, 1) not in reports


def test_a_stored_financial_version_counts_as_its_fetch(db, store, seeded) -> None:
    """Step 23-c wrote most versions from the legacy archive, under its own
    resource keys: the version, not the resource key, says the quarter is held."""
    archive = _fetch(db, store, "financial_filing", "mops_t164sb01",
                     "mops_t164sb01:financial_filing_archive:2330:2026Q2",
                     datetime(2026, 9, 21, tzinfo=UTC))
    db.execute(sa.text(
        "INSERT INTO financial_reports (stock_id, report_year, report_quarter, "
        "report_category, fetch_id) VALUES ('2330', 2026, 2, 'consolidated', :f)"), {"f": archive})

    reports = _planned(s.plan(db, NOW), "financial_reports/mops_t164sb01")
    assert ("2330", 2026, 2) not in reports


def test_the_calendar_asks_for_the_current_month(db, seeded) -> None:
    calendar = _planned(s.plan(db, NOW), "trading_days/twse")
    september = calendar[date(2026, 9, 1)]
    # Fetched 09-16: more than an hour old, and the month has not ended.
    assert (september.state, september.dispatch, september.purpose) == (
        "refresh", True, "first_capture")


def test_done_periods_are_counted_not_listed(db, store, seeded) -> None:
    for day in DAYS:  # each fetched after it settled and after its correction check fell due
        _fetch(db, store, "daily_price", "twse_mi_index",
               f"twse_mi_index:daily-quotes:{day.isoformat()}", datetime(2026, 9, 26, tzinfo=TAIPEI))
    plan = s.plan(db, NOW)
    assert _planned(plan, "daily_prices/twse_mi_index") == {}
    assert plan.counts["daily_prices/twse_mi_index"]["done"] == len(DAYS)
