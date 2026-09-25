"""Step 35-c-2: the v2 write path for monthly revenue and TDCC.

Monthly revenue is filed by each issuer on its own day, so the first row of a
key stores when it was public: the fetch time of a first capture, NULL for a
gap fill, which cannot prove when the row appeared (CLAUDE.md §32). A later
row is a correction and is public from its own `recorded_at`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.ingestion.models import FetchedArtifact
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import exchange_daily as xd
from stock_data_center.v2 import monthly_revenue as mr
from stock_data_center.v2 import shareholding as sh
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
JULY = date(2026, 7, 1)
DOMESTIC = mr.JOBS["monthly_revenues/mops_t21sc03_sii"]
FOREIGN = mr.JOBS["monthly_revenues/mops_t21sc03_sii/foreign"]
TDCC = sh.JOBS["shareholding_distributions/tdcc_opendata"]
PAGE = (FIXTURES / "mops_t21sc03_sii_115_7_0.html").read_bytes()
KY_PAGE = (FIXTURES / "mops_t21sc03_sii_115_7_1.html").read_bytes()
WEEK = (FIXTURES / "tdcc_od_1_5_20190628_slashed.csv").read_bytes()
SEEN = datetime(2026, 8, 5, 9, 30, tzinfo=UTC)


class Replay:
    def __init__(self, *responses: bytes, at: datetime | None = None) -> None:
        self.responses = list(responses)
        self.requests: list[str] = []
        self.at = at

    def fetch(self, resource) -> FetchedArtifact:
        self.requests.append(resource.resource_key)
        return FetchedArtifact(
            content=self.responses.pop(0), source_uri=resource.source_uri,
            fetched_at=self.at or datetime.now(UTC), media_type="text/html",
        )


@pytest.fixture
def store(tmp_path) -> LocalRawArtifactStore:
    return LocalRawArtifactStore(tmp_path)


@pytest.fixture
def universe(db: Connection, store) -> None:
    fetch_id = record_fetch(
        db,
        FetchRecord("stocks", "twse_isin", "twse_isin:test", None, "first_capture",
                    "test:v1", "abc", datetime.now(UTC)),
        content=b"isin", status="succeeded", store=store,
    )
    for stock_id, market in (("2330", "sii"), ("1101", "sii"), ("1256", "sii")):
        db.execute(
            sa.text("INSERT INTO stocks (stock_id, name, fetch_id) "
                    "VALUES (:s, :n, :f) ON CONFLICT DO NOTHING"),
            {"s": stock_id, "n": stock_id, "f": fetch_id},
        )
        db.execute(
            sa.text("INSERT INTO listings (stock_id, market, fetch_id) "
                    "VALUES (:s, :m, :f) ON CONFLICT DO NOTHING"),
            {"s": stock_id, "m": market, "f": fetch_id},
        )


def _ingest(db, store, job, period, content, *, purpose="gap_fill", at=None) -> xd.Outcome:
    return xd.ingest(db, job, period, fetcher=Replay(content, at=at), git_commit="abc",
                     purpose=purpose, store=store)


def _revenues(db) -> list[sa.Row]:
    return db.execute(sa.text(
        "SELECT stock_id, revenue, published_at FROM monthly_revenues "
        "WHERE revenue_month = '2026-07-01' ORDER BY stock_id, recorded_at")).all()


def _with_revenue(content: bytes, code: str, old: str, new: str) -> bytes:
    text = content.decode("cp950")
    at = text.index(f">{code}<")
    return (text[:at] + text[at:].replace(old, new, 1)).encode("cp950")


def test_only_stocks_in_the_universe_are_written(db, store, universe) -> None:
    outcome = _ingest(db, store, DOMESTIC, JULY, PAGE)
    assert (outcome.status, outcome.appended) == ("succeeded", 2)
    assert outcome.out_of_scope == outcome.parsed - 2
    assert [row.stock_id for row in _revenues(db)] == ["1101", "2330"]


def test_a_first_capture_is_public_from_its_fetch_time(db, store, universe) -> None:
    _ingest(db, store, DOMESTIC, JULY, PAGE, purpose="first_capture", at=SEEN)
    assert {row.published_at for row in _revenues(db)} == {SEEN}


def test_a_gap_fill_cannot_prove_when_a_row_was_public(db, store, universe) -> None:
    _ingest(db, store, DOMESTIC, JULY, PAGE, purpose="gap_fill", at=SEEN)
    assert {row.published_at for row in _revenues(db)} == {None}


def test_the_same_page_again_adds_no_row(db, store, universe) -> None:
    _ingest(db, store, DOMESTIC, JULY, PAGE, purpose="first_capture", at=SEEN)
    again = _ingest(db, store, DOMESTIC, JULY, PAGE, purpose="first_capture")
    assert (again.appended, again.unchanged) == (0, 2)
    assert len(_revenues(db)) == 2


def test_a_correction_is_a_new_row_public_from_its_recorded_at(db, store, universe) -> None:
    _ingest(db, store, DOMESTIC, JULY, PAGE, purpose="first_capture", at=SEEN)
    corrected = _with_revenue(PAGE, "1101", "13,744,103", "13,744,104")
    # Even a first capture does not make a correction public at its fetch time.
    outcome = _ingest(db, store, DOMESTIC, JULY, corrected, purpose="first_capture")
    assert (outcome.appended, outcome.unchanged) == (1, 1)
    rows = [row for row in _revenues(db) if row.stock_id == "1101"]
    assert [(row.revenue, row.published_at) for row in rows] == [
        (13_744_103_000, SEEN), (13_744_104_000, None)]


def test_the_foreign_page_writes_ky_issuers(db, store, universe) -> None:
    outcome = _ingest(db, store, FOREIGN, JULY, KY_PAGE)
    assert (outcome.status, outcome.appended) == ("succeeded", 1)
    assert [row.stock_id for row in _revenues(db)] == ["1256"]


def test_a_month_is_asked_again_until_the_month_after_next(db, store, universe) -> None:
    settled = DOMESTIC.settled_at(JULY)
    _ingest(db, store, DOMESTIC, JULY, PAGE, at=settled - timedelta(minutes=1))
    assert xd.pending(db, DOMESTIC, [JULY]) == [JULY]
    _ingest(db, store, DOMESTIC, JULY, PAGE, at=settled)
    assert xd.pending(db, DOMESTIC, [JULY]) == []


def test_a_backfill_asks_each_month_with_a_trading_day(db, store) -> None:
    from stock_data_center.v2.backfill import periods

    fetch_id = record_fetch(
        db, FetchRecord("trading_calendar", "twse", "cal", None, "gap_fill", "t", "abc",
                        datetime.now(UTC)),
        content=b"cal", status="succeeded", store=store,
    )
    for day in (date(2026, 6, 30), date(2026, 7, 1), date(2026, 7, 31)):
        db.execute(sa.text("INSERT INTO trading_days VALUES (:d, :f)"), {"d": day, "f": fetch_id})
    assert periods(db, DOMESTIC, date(2026, 6, 1), date(2026, 7, 31)) == [
        date(2026, 6, 1), JULY]


def test_tdcc_writes_the_week_the_file_serves(db, store, universe) -> None:
    outcome = _ingest(db, store, TDCC, None, WEEK)
    assert (outcome.status, outcome.appended) == ("succeeded", 1)  # 0061 is not a stock
    row = db.execute(sa.text(
        "SELECT stock_id, snapshot_date, total_shares FROM shareholding_distributions")).one()
    assert tuple(row) == ("2330", date(2019, 6, 28), 25_930_380_458)
    again = _ingest(db, store, TDCC, None, WEEK)
    assert (again.appended, again.unchanged) == (0, 1)


def test_a_tdcc_backfill_fetches_the_latest_week_once(db, store, universe) -> None:
    from stock_data_center.v2.backfill import run

    fetcher = Replay(WEEK)
    report = run(db, [TDCC], date(2026, 1, 1), date(2026, 9, 18), fetcher=fetcher,
                 git_commit="abc", purpose="first_capture", store=store)
    assert fetcher.requests == ["tdcc_opendata:opendata:1-5"]
    assert report["shareholding_distributions/tdcc_opendata"]["appended"] == 1


def test_a_tdcc_backfill_fetches_again_on_every_run(db, store, universe) -> None:
    # One resource key serves every week, so a finished fetch must not make the
    # next run skip the week published since.
    from stock_data_center.v2.backfill import run

    long_after = datetime(2026, 1, 1, tzinfo=UTC)
    _ingest(db, store, TDCC, None, WEEK, at=long_after)
    fetcher = Replay(WEEK)
    run(db, [TDCC], date(2019, 6, 1), date(2019, 6, 28), fetcher=fetcher,
        git_commit="abc", purpose="first_capture", store=store)
    assert fetcher.requests == ["tdcc_opendata:opendata:1-5"]


SEPTEMBER = date(2026, 9, 1)
NOT_YET = (FIXTURES / "mops_t21sc03_sii_115_9_0_no_data.html").read_bytes()


def test_a_month_not_published_yet_is_empty_and_asked_again(db, store, universe) -> None:
    # Code review of #49: the revenue adapter's own code is no_data_for_period.
    outcome = _ingest(db, store, DOMESTIC, SEPTEMBER, NOT_YET)
    assert (outcome.status, outcome.reason_code) == ("empty", "no_data_for_period")
    assert xd.pending(db, DOMESTIC, [SEPTEMBER]) == [SEPTEMBER]


def test_a_value_beyond_its_column_quarantines_the_page_not_the_run(db, store, universe) -> None:
    # Code review of #49: a third decimal used to reach the INSERT, whose
    # IntegrityError stopped the backfill and rolled the fetch row back.
    from stock_data_center.v2.backfill import run

    fetch_id = record_fetch(
        db, FetchRecord("trading_calendar", "twse", "cal", None, "gap_fill", "t", "abc",
                        datetime.now(UTC)),
        content=b"cal", status="succeeded", store=store,
    )
    db.execute(sa.text("INSERT INTO trading_days VALUES (:d, :f)"),
               {"d": date(2026, 7, 31), "f": fetch_id})
    bad = _with_revenue(PAGE, "1101", ">                  2.70<", ">                  2.705<")
    report = run(db, [DOMESTIC], JULY, date(2026, 7, 31), fetcher=Replay(bad),
                 git_commit="abc", purpose="gap_fill", store=store)
    assert report["monthly_revenues/mops_t21sc03_sii"]["quarantined"] == 1
    assert _revenues(db) == []
    logged = db.execute(sa.text(
        "SELECT status, reason_code FROM fetches WHERE source = 'mops_t21sc03_sii'")).one()
    assert tuple(logged) == ("quarantined", "out_of_range")


def test_a_tdcc_percentage_of_a_thousand_is_out_of_range() -> None:
    from stock_data_center.ingestion.models import SourceDataError

    row = TDCC.rows(TDCC.adapter.parse(WEEK, TDCC.request(None)))[0]
    with pytest.raises(SourceDataError) as refused:
        xd.check_precision(TDCC.table, [{**row, "total_percent": xd.Decimal("1000.00")}])
    assert refused.value.reason_code == "out_of_range"
