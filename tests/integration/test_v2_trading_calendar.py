"""Step 28-a: the v2 writer of `trading_days`, from TWSE FMTQIK.

Every daily job takes its periods from `trading_days`, so the calendar decides
what the scheduler expects. A day enters it only because the exchange listed
it; a day the exchange stops listing is never deleted quietly — the fetch
quarantines and says which day, and a person decides.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.schema_v2 import fetches, trading_days
from stock_data_center.ingestion.models import FetchedArtifact
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import trading_calendar as tc

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
JULY_2024 = date(2024, 7, 1)
REAL_JULY = (FIXTURES / "twse_fmtqik_202407.json").read_bytes()
FIELDS = ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"]


def _month(*roc_dates: str, stat: str = "OK") -> bytes:
    return json.dumps({
        "stat": stat, "fields": FIELDS,
        "data": [[day, "1", "1", "1", "1", "0"] for day in roc_dates],
    }, ensure_ascii=False).encode()


class Replay:
    def __init__(self, *responses: bytes) -> None:
        self.responses = list(responses)
        self.requests: list[str] = []

    def fetch(self, resource) -> FetchedArtifact:
        self.requests.append(resource.resource_key)
        return FetchedArtifact(content=self.responses.pop(0), source_uri=resource.source_uri,
                               fetched_at=datetime.now(UTC), media_type="application/json")


@pytest.fixture
def store(tmp_path) -> LocalRawArtifactStore:
    return LocalRawArtifactStore(tmp_path)


def _ingest(db, store, month, content, purpose="gap_fill"):
    return tc.ingest(db, month, fetcher=Replay(content), git_commit="abc", purpose=purpose,
                     store=store)


def _days(db: Connection, month: date) -> list[date]:
    return db.scalars(
        sa.select(trading_days.c.trade_date)
        .where(sa.func.date_trunc("month", trading_days.c.trade_date) == month)
        .order_by(trading_days.c.trade_date)).all()


def test_each_listed_day_is_written_and_names_its_fetch(db, store) -> None:
    outcome = _ingest(db, store, JULY_2024, REAL_JULY)

    days = _days(db, JULY_2024)
    assert (outcome.status, outcome.appended) == ("succeeded", len(days))
    # 2024-07-24/25 were typhoon closures: absent, never flagged.
    assert date(2024, 7, 23) in days and date(2024, 7, 24) not in days
    assert set(db.scalars(sa.select(trading_days.c.fetch_id)
                          .where(trading_days.c.trade_date.in_(days)))) == {outcome.fetch_id}


def test_the_same_month_again_writes_nothing_and_keeps_the_first_fetch(db, store) -> None:
    first = _ingest(db, store, JULY_2024, REAL_JULY)
    again = _ingest(db, store, JULY_2024, REAL_JULY)

    assert (again.status, again.appended, again.unchanged) == (
        "succeeded", 0, first.appended)
    assert set(db.scalars(sa.select(trading_days.c.fetch_id)
                          .where(trading_days.c.trade_date >= JULY_2024,
                                 trading_days.c.trade_date < date(2024, 8, 1)))) == {
        first.fetch_id}


def test_a_month_in_progress_grows_one_day_at_a_time(db, store) -> None:
    month = date(2026, 10, 1)
    _ingest(db, store, month, _month("115/10/01"))
    grown = _ingest(db, store, month, _month("115/10/01", "115/10/02"))

    assert (grown.appended, grown.unchanged) == (1, 1)
    assert _days(db, month) == [date(2026, 10, 1), date(2026, 10, 2)]


def test_a_day_the_exchange_stops_listing_quarantines_and_is_kept(db, store) -> None:
    month = date(2026, 10, 1)
    _ingest(db, store, month, _month("115/10/01", "115/10/02"))
    dropped = _ingest(db, store, month, _month("115/10/02", "115/10/05"))

    assert (dropped.status, dropped.reason_code) == ("quarantined", "calendar_day_removed")
    detail = db.scalar(sa.select(fetches.c.reason_detail).where(fetches.c.id == dropped.fetch_id))
    assert "2026-10-01" in detail
    # Nothing from that answer is written, and nothing is deleted.
    assert _days(db, month) == [date(2026, 10, 1), date(2026, 10, 2)]


def test_no_trading_day_yet_is_empty_not_a_closed_month(db, store) -> None:
    outcome = _ingest(db, store, date(2026, 10, 1),
                      _month(stat="很抱歉，沒有符合條件的資料!"))

    assert (outcome.status, outcome.reason_code) == ("empty", "no_data_for_period")
    assert _days(db, date(2026, 10, 1)) == []


def test_pending_months_are_the_ones_not_fetched_after_they_ended(db, store) -> None:
    _ingest(db, store, JULY_2024, REAL_JULY)  # fetched now, long after July 2024 ended

    assert tc.pending(db, [JULY_2024, date(2024, 8, 1)]) == [date(2024, 8, 1)]


def test_a_month_fetched_before_it_ended_stays_pending(db, store) -> None:
    month = date(2099, 1, 1)
    _ingest(db, store, month, _month("188/01/02"))

    assert tc.pending(db, [month]) == [month]
