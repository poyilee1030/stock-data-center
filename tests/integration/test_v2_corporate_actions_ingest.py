"""Step 35-c-3: the v2 write path for corporate actions (ADR-0027 "35-c 定案").

One list file per feed and year; a TWSE dividend or reduction row publishes its
terms only on its own detail page, which is fetched for today's common stocks
only. A key is (stock, feed, ex-date); a row the feed stops listing inside the
executed range is retracted by a new row, never deleted (CLAUDE.md §51.5).
Expected values are the v1 ones in `stockdc_step19d`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.ingestion.models import FetchedArtifact
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import corporate_actions as ca
from stock_data_center.v2.backfill import _unit
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXDAILYQ_2024 = (FIXTURES / "tpex_exdailyq_2024.json").read_bytes()
_TWT49U_2024 = (FIXTURES / "twse_twt49u_2024.json").read_bytes()
TWT49U_2026 = (FIXTURES / "twse_twt49u_2026_fetched_20260916.json").read_bytes()
DETAIL_2454 = (FIXTURES / "twse_detail_49_2454_20240104.json").read_bytes()
END_OF_2024 = date(2024, 12, 31)


class Replay:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list[str] = []

    def fetch(self, resource) -> FetchedArtifact:
        self.requests.append(resource.resource_key)
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return FetchedArtifact(content=answer, source_uri=resource.source_uri,
                               fetched_at=datetime.now(UTC), media_type="application/json")


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
    for stock_id, market in (("2454", "sii"), ("6629", "otc")):
        db.execute(
            sa.text("INSERT INTO stocks (stock_id, name, market, fetch_id) "
                    "VALUES (:s, :n, :m, :f) ON CONFLICT DO NOTHING"),
            {"s": stock_id, "n": stock_id, "m": market, "f": fetch_id},
        )


def _ingest(db, store, source, year, *responses, purpose="gap_fill", executed_through=None):
    fetcher = Replay(*responses)
    outcome = ca.ingest(db, source, year, fetcher=fetcher, git_commit="abc", purpose=purpose,
                        store=store, unit=_unit,
                        executed_through=executed_through or date(year, 12, 31))
    return outcome, fetcher


def _actions(db) -> list[sa.Row]:
    return db.execute(sa.text(
        "SELECT stock_id, source, ex_date, event_type, close_before, reference_price, "
        "rights_dividend_value, cash_dividend_per_share, retracted FROM corporate_actions "
        "ORDER BY stock_id, ex_date, recorded_at")).all()


def _without(content: bytes, code: str) -> bytes:
    payload = json.loads(content)
    for table in payload.get("tables", [payload]):
        if "data" in table:
            table["data"] = [row for row in table["data"] if row[1] != code and row[0] != code]
        if "aaData" in table:
            table["aaData"] = [row for row in table["aaData"] if code not in row[:2]]
    return json.dumps(payload, ensure_ascii=False).encode()


def _keep_only(content: bytes, code: str, roc_date: str) -> bytes:
    """The file with `code`'s other events removed: 2454 went ex twice in 2024."""
    payload = json.loads(content)
    payload["data"] = [row for row in payload["data"] if row[1] != code or row[0] == roc_date]
    return json.dumps(payload, ensure_ascii=False).encode()


TWT49U_2024 = _keep_only(_TWT49U_2024, "2454", "113年01月04日")


def test_a_tpex_row_carries_its_terms_and_needs_no_detail(db, store, universe) -> None:
    outcome, fetcher = _ingest(db, store, "tpex_exdailyq", 2024, EXDAILYQ_2024)
    assert fetcher.requests == ["tpex_exdailyq:2024-01-01:2024-12-31"]
    assert outcome.status == "succeeded"
    first = [row for row in _actions(db) if row.ex_date == date(2024, 1, 3)]
    assert [tuple(row) for row in first] == [(
        "6629", "tpex_exdailyq", date(2024, 1, 3), "除息", Decimal(55), Decimal("53.5"),
        Decimal("1.5"), Decimal("1.5"), False)]
    assert {row.stock_id for row in _actions(db)} == {"6629"}  # only today's stocks


def test_a_twse_dividend_takes_its_terms_from_the_detail_page(db, store, universe) -> None:
    outcome, fetcher = _ingest(db, store, "twse_twt49u", 2024, TWT49U_2024, DETAIL_2454)
    # One detail request: 2454 is the only stock of the universe in this file.
    assert fetcher.requests == [
        "twse_twt49u:2024-01-01:2024-12-31", "twse_twt49u:detail:2454:TWT49U:20240104"]
    assert outcome.appended == 1
    (row,) = _actions(db)
    assert tuple(row) == ("2454", "twse_twt49u", date(2024, 1, 4), "息", Decimal(953),
                          Decimal("928.4"), Decimal("24.6"), Decimal("24.6"), False)


def test_a_stored_event_is_not_asked_for_its_detail_again(db, store, universe) -> None:
    _ingest(db, store, "twse_twt49u", 2024, TWT49U_2024, DETAIL_2454)
    again, fetcher = _ingest(db, store, "twse_twt49u", 2024, TWT49U_2024)
    assert fetcher.requests == ["twse_twt49u:2024-01-01:2024-12-31"]
    assert (again.appended, again.unchanged) == (0, 1)
    # A correction check does ask, because a detail can change on its own.
    checked, fetcher = _ingest(db, store, "twse_twt49u", 2024, TWT49U_2024, DETAIL_2454,
                               purpose="correction_check")
    assert len(fetcher.requests) == 2 and checked.unchanged == 1


def test_a_failed_detail_holds_back_its_row_only(db, store, universe) -> None:
    outcome, _ = _ingest(db, store, "twse_twt49u", 2024, TWT49U_2024,
                         httpx.ConnectError("refused"))
    assert (outcome.status, outcome.reason_code, outcome.appended) == (
        "succeeded", "rows_rejected", 0)
    assert ca.pending(db, "twse_twt49u", [2024]) == [2024]
    retried, fetcher = _ingest(db, store, "twse_twt49u", 2024, TWT49U_2024, DETAIL_2454)
    assert retried.appended == 1 and len(fetcher.requests) == 2


def test_rows_after_executed_through_are_counted_not_stored(db, store, universe) -> None:
    # Without 2454, whose two 2026 events would each ask for a detail page.
    outcome, _ = _ingest(db, store, "twse_twt49u", 2026, _without(TWT49U_2026, "2454"),
                         executed_through=date(2026, 9, 10))
    assert outcome.not_yet_executed > 0
    assert all(row.ex_date <= date(2026, 9, 10) for row in _actions(db))


def test_an_event_the_feed_drops_is_retracted_by_a_new_row(db, store, universe) -> None:
    _ingest(db, store, "tpex_exdailyq", 2024, EXDAILYQ_2024)
    before = len(_actions(db))
    dropped = _without(EXDAILYQ_2024, "6629")
    outcome, _ = _ingest(db, store, "tpex_exdailyq", 2024, dropped)
    assert outcome.retracted == before
    rows = _actions(db)
    assert len(rows) == 2 * before
    assert [row.retracted for row in rows if row.ex_date == date(2024, 1, 3)] == [False, True]
    # Listed again: a third row, no longer retracted.
    back, _ = _ingest(db, store, "tpex_exdailyq", 2024, EXDAILYQ_2024)
    assert back.appended == before
    assert [row.retracted for row in _actions(db) if row.ex_date == date(2024, 1, 3)] == [
        False, True, False]


def test_a_year_is_done_once_fetched_after_it_ended(db, store, universe) -> None:
    _ingest(db, store, "tpex_exdailyq", 2024, EXDAILYQ_2024)
    assert ca.pending(db, "tpex_exdailyq", [2024, 2025]) == [2025]
    # The current year is never done: its file still grows.
    assert ca.settled_at(2026) > datetime(2026, 9, 24, tzinfo=UTC)


def test_the_six_result_feeds_and_their_backfill_keys() -> None:
    from stock_data_center.v2.backfill import ALL_KEYS

    assert set(ca.FEEDS) == {"twse_twt49u", "twse_twtauu", "twse_twtb8u",
                             "tpex_exdailyq", "tpex_revivt", "tpex_pvchgrslt"}
    assert {f"corporate_actions/{source}" for source in ca.FEEDS} <= set(ALL_KEYS)


def test_a_retraction_is_written_once(db, store, universe) -> None:
    _ingest(db, store, "tpex_exdailyq", 2024, EXDAILYQ_2024)
    dropped = _without(EXDAILYQ_2024, "6629")
    _ingest(db, store, "tpex_exdailyq", 2024, dropped)
    again, _ = _ingest(db, store, "tpex_exdailyq", 2024, dropped)
    assert again.retracted == 0


def test_an_event_whose_detail_fails_is_still_listed_not_retracted(db, store, universe) -> None:
    _ingest(db, store, "twse_twt49u", 2024, TWT49U_2024, DETAIL_2454)
    checked, _ = _ingest(db, store, "twse_twt49u", 2024, TWT49U_2024,
                         httpx.ConnectError("refused"), purpose="correction_check")
    assert (checked.retracted, checked.rejected) == (0, 1)
    assert [row.retracted for row in _actions(db)] == [False]


def _with(content: bytes, code: str, event_date: str, column: int, value: str) -> bytes:
    """The file with one cell of `code`'s `event_date` row replaced."""
    payload = json.loads(content)
    for table in payload.get("tables", [payload]):
        for row in table.get("data", []):
            if row[1] == code and row[0] == event_date:
                row[column] = value
    return json.dumps(payload, ensure_ascii=False).encode()


def test_a_stored_event_whose_row_is_rejected_is_still_listed_not_retracted(
    db, store, universe
) -> None:
    _ingest(db, store, "tpex_exdailyq", 2024, EXDAILYQ_2024)
    before = len(_actions(db))
    # 除權息前收盤價 beyond numeric(10, 2): the row is held back, not withdrawn.
    broken = _with(EXDAILYQ_2024, "6629", "113/01/03", 3, "123456789.00")
    outcome, _ = _ingest(db, store, "tpex_exdailyq", 2024, broken)
    assert (outcome.retracted, outcome.rejected) == (0, 1)
    assert len(_actions(db)) == before


def test_a_failed_detail_is_not_counted_unchanged(db, store, universe) -> None:
    outcome, _ = _ingest(db, store, "twse_twt49u", 2024, TWT49U_2024,
                         httpx.ConnectError("refused"))
    assert (outcome.unchanged, outcome.rejected) == (0, 1)


def test_a_corrected_list_price_of_a_stored_event_asks_for_its_detail(
    db, store, universe
) -> None:
    _ingest(db, store, "twse_twt49u", 2024, TWT49U_2024, DETAIL_2454)
    corrected = _with(TWT49U_2024, "2454", "113年01月04日", 3, "954.00")
    outcome, fetcher = _ingest(db, store, "twse_twt49u", 2024, corrected, DETAIL_2454)
    assert len(fetcher.requests) == 2
    assert (outcome.appended, outcome.unchanged) == (1, 0)
    assert [row.close_before for row in _actions(db)] == [Decimal(953), Decimal(954)]


def test_a_year_not_yet_begun_is_not_requested(db, store, universe) -> None:
    fetcher = Replay(EXDAILYQ_2024)
    report = ca.run(db, ["tpex_exdailyq"], date(2024, 1, 1), date(2025, 12, 31),
                    fetcher=fetcher, git_commit="abc", purpose="gap_fill", unit=_unit,
                    store=store, today=date(2024, 12, 31))
    assert fetcher.requests == ["tpex_exdailyq:2024-01-01:2024-12-31"]
    assert report["corporate_actions/tpex_exdailyq"]["periods"] == 1


def test_today_is_the_taipei_date() -> None:
    # 2026-12-31 23:30 UTC is already 2027-01-01 in Taipei.
    assert ca.executed_through(datetime(2026, 12, 31, 23, 30, tzinfo=UTC)) == date(2027, 1, 1)
