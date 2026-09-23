"""Step 35-b-1: the v2 write path — fetch, keep raw, parse, append on change.

Acceptance (ROADMAP Step 35-b-1): a rerun of the same day adds no row; a parse
failure keeps the raw file and logs `quarantined`; stocks outside `stocks` are
not written; visibility is tested for an original value and for a correction.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.schema_v2 import daily_prices, index_prices
from stock_data_center.ingestion.models import FetchedArtifact
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import exchange_daily as xd
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DAY = date(2026, 9, 11)
DAILY = xd.JOBS["daily_prices/twse_mi_index"]
MI_INDEX = (FIXTURES / "twse_mi_index_allbut0999_20260911.json").read_bytes()


class Replay:
    """A fetcher that answers each request with the next queued response."""

    def __init__(self, *responses: bytes | Exception, at: datetime | None = None) -> None:
        self.responses = list(responses)
        self.requests: list[str] = []
        self.at = at

    def fetch(self, resource) -> FetchedArtifact:
        self.requests.append(resource.resource_key)
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return FetchedArtifact(
            content=answer,
            source_uri=resource.source_uri,
            fetched_at=self.at or datetime.now(UTC),
            media_type="application/json",
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
        content=b"isin",
        status="succeeded",
        store=store,
    )
    for stock_id, market in (("2330", "sii"), ("2317", "sii"), ("6488", "otc")):
        db.execute(
            sa.text("INSERT INTO stocks (stock_id, name, market, fetch_id) "
                    "VALUES (:s, :n, :m, :f) ON CONFLICT DO NOTHING"),
            {"s": stock_id, "n": stock_id, "m": market, "f": fetch_id},
        )


def _ingest(db, store, job, period, *responses, purpose="gap_fill", at=None) -> xd.Outcome:
    return xd.ingest(
        db, job, period, fetcher=Replay(*responses, at=at), git_commit="abc",
        purpose=purpose, store=store,
    )


def _prices(db) -> list[tuple]:
    return db.execute(
        sa.select(daily_prices.c.stock_id, daily_prices.c.trade_count)
        .where(daily_prices.c.source == "twse_mi_index", daily_prices.c.trade_date == DAY)
        .order_by(daily_prices.c.stock_id, daily_prices.c.recorded_at)
    ).all()


def _with_trade_count(stock_id: str, trade_count: str) -> bytes:
    payload = json.loads(MI_INDEX)
    for table in payload["tables"]:
        if table.get("fields", [None])[0] == "證券代號":
            for row in table["data"]:
                if row[0] == stock_id:
                    row[3] = trade_count
    return json.dumps(payload, ensure_ascii=False).encode()


def _raw(store: LocalRawArtifactStore, fetch: dict) -> bytes:
    digest = fetch["sha256"].hex()
    root = Path(store.configuration_identity["root"])
    return store.read(storage_uri=str(root / digest[:2] / digest),
                      expected_digest=digest, expected_byte_size=fetch["byte_size"])


def _fetch(db, fetch_id) -> dict:
    return dict(db.execute(sa.text("SELECT * FROM fetches WHERE id = :i"), {"i": fetch_id})
                .mappings().one())


def test_only_stocks_in_the_universe_are_written(db, store, universe) -> None:
    outcome = _ingest(db, store, DAILY, DAY, MI_INDEX)
    assert outcome.status == "succeeded"
    assert _prices(db) == [("2317", 24204), ("2330", 164402)]
    assert (outcome.appended, outcome.unchanged) == (2, 0)
    assert outcome.out_of_scope == outcome.parsed - 2 > 1000
    fetch = _fetch(db, outcome.fetch_id)
    assert (fetch["dataset"], fetch["source"], fetch["resource_key"]) == (
        "daily_price", "twse_mi_index", "twse_mi_index:daily-quotes:2026-09-11"
    )
    assert _raw(store, fetch) == MI_INDEX


def test_the_same_day_again_adds_no_row_but_logs_the_fetch(db, store, universe) -> None:
    first = _ingest(db, store, DAILY, DAY, MI_INDEX)
    again = _ingest(db, store, DAILY, DAY, MI_INDEX)
    assert (again.appended, again.unchanged) == (0, 2)
    assert again.fetch_id != first.fetch_id
    assert len(_prices(db)) == 2
    fetched = db.scalar(sa.text(
        "SELECT count(*) FROM fetches WHERE resource_key = 'twse_mi_index:daily-quotes:2026-09-11'"
    ))
    assert fetched == 2


def test_a_changed_value_appends_a_row_and_keeps_the_old_one(db, store, universe) -> None:
    _ingest(db, store, DAILY, DAY, MI_INDEX)
    corrected = _ingest(db, store, DAILY, DAY, _with_trade_count("2330", "164,403"))
    assert (corrected.appended, corrected.unchanged) == (1, 1)
    assert _prices(db) == [("2317", 24204), ("2330", 164402), ("2330", 164403)]


def test_visibility_uses_the_rule_for_an_original_and_recorded_at_for_a_correction(
    db, store, universe
) -> None:
    _ingest(db, store, DAILY, DAY, MI_INDEX)
    _ingest(db, store, DAILY, DAY, _with_trade_count("2330", "164,403"))
    released = xd.available_from(DAY)
    corrected_at = db.scalar(sa.text(
        "SELECT max(recorded_at) FROM daily_prices WHERE stock_id = '2330'"
    ))

    def seen(as_of: datetime) -> dict[str, int]:
        rows = xd.visible(db, daily_prices, as_of=as_of, start=DAY, end=DAY)
        return {row["stock_id"]: row["trade_count"] for row in rows}

    # Before the rule instant nothing is public, although both rows are stored.
    assert seen(released - timedelta(seconds=1)) == {}
    # The original is public from the rule instant, though recorded much later.
    assert seen(released) == {"2317": 24204, "2330": 164402}
    assert seen(corrected_at - timedelta(microseconds=1))["2330"] == 164402
    # The correction only from the moment the Data Center recorded it.
    assert seen(corrected_at) == {"2317": 24204, "2330": 164403}


def test_the_sql_rule_instant_agrees_with_the_python_one(db) -> None:
    for day in (date(2020, 1, 2), DAY, date(2024, 12, 31)):
        assert db.scalar(sa.select(xd.available_from_sql(sa.literal(day)))) == (
            xd.available_from(day)
        )


def test_a_file_that_does_not_parse_is_kept_and_quarantined(db, store, universe) -> None:
    broken = b'{"stat": "OK", "date": "20260911", "tables": []}'
    outcome = _ingest(db, store, DAILY, DAY, broken)
    assert outcome.status == "quarantined" and outcome.appended == 0
    fetch = _fetch(db, outcome.fetch_id)
    assert fetch["status"] == "quarantined"
    assert fetch["reason_code"] == "schema_mismatch"
    assert fetch["sha256"] is not None
    assert _raw(store, fetch) == broken
    assert _prices(db) == []


def test_a_date_without_data_is_logged_empty(db, store, universe) -> None:
    closed = (FIXTURES / "twse_mi_index_allbut0999_20240724_closed.json").read_bytes()
    outcome = _ingest(db, store, DAILY, date(2024, 7, 24), closed)
    assert outcome.status == "empty"
    assert _fetch(db, outcome.fetch_id)["reason_code"] == "no_data_for_date"


def test_a_failed_request_is_logged_without_a_raw_file(db, store, universe) -> None:
    outcome = _ingest(db, store, DAILY, DAY, httpx.ConnectError("refused"))
    fetch = _fetch(db, outcome.fetch_id)
    assert (fetch["status"], fetch["reason_code"], fetch["sha256"]) == (
        "failed", "fetch_error", None
    )


def test_a_maintenance_page_is_retried_once_live(db, store, universe) -> None:
    outcome = _ingest(db, store, DAILY, DAY, b"<html>maintenance</html>", MI_INDEX)
    assert outcome.status == "succeeded"
    statuses = db.execute(sa.text(
        "SELECT status, reason_code, attempt FROM fetches WHERE source = 'twse_mi_index' "
        "ORDER BY attempt"
    )).all()
    assert statuses == [("failed", "invalid_json", 1), ("succeeded", None, 2)]


def test_resume_skips_what_succeeded_or_was_empty_and_retries_the_rest(
    db, store, universe
) -> None:
    other = date(2026, 9, 10)
    closed = date(2024, 7, 24)
    _ingest(db, store, DAILY, DAY, MI_INDEX)
    _ingest(db, store, DAILY, other, httpx.ConnectError("refused"))
    _ingest(db, store, DAILY, closed,
            (FIXTURES / "twse_mi_index_allbut0999_20240724_closed.json").read_bytes())
    assert xd.pending(db, DAILY, [closed, other, DAY]) == [other]


def test_market_flows_have_no_stock_filter(db, store) -> None:
    job = xd.JOBS["institutional_market_flows/twse_bfi82u"]
    outcome = _ingest(db, store, job, DAY, (FIXTURES / "twse_bfi82u_20260911.json").read_bytes())
    assert (outcome.status, outcome.appended, outcome.out_of_scope) == ("succeeded", 6, 0)


def test_an_otc_mops_page_writes_foreign_holdings(db, store, universe) -> None:
    job = xd.JOBS["foreign_holdings/mops_t13sa150_otc"]
    outcome = _ingest(db, store, job, DAY,
                      (FIXTURES / "mops_t13sa150_otc_20260911.html").read_bytes())
    assert (outcome.status, outcome.appended) == ("succeeded", 1)
    held = db.scalar(sa.text("SELECT held_shares FROM foreign_holdings WHERE stock_id = '6488'"))
    assert held == 131239769


TAIEX = xd.JOBS["index_prices/twse_mi_5mins_hist"]
TAIEX_MONTH = (FIXTURES / "twse_mi_5mins_hist_202601.json").read_bytes()


def _list_close(db, store, close: str) -> None:
    fetch_id = record_fetch(
        db,
        FetchRecord("market_index", "twse_mi_index", "t", None, "gap_fill", "t", "abc",
                    datetime.now(UTC)),
        content=b"list", status="succeeded", store=store,
    )
    db.execute(sa.insert(index_prices).values(
        source="twse_mi_index", index_name="指數/臺灣證券交易所:發行量加權股價指數",
        trade_date=date(2026, 1, 2), close_value=Decimal(close), fetch_id=fetch_id,
    ))


def test_taiex_ohlc_is_written_when_its_close_matches_the_list(db, store) -> None:
    _list_close(db, store, "29349.81")
    outcome = _ingest(db, store, TAIEX, date(2026, 1, 1), TAIEX_MONTH)
    assert outcome.status == "succeeded" and outcome.appended == outcome.parsed > 15


def test_taiex_ohlc_whose_close_disagrees_is_quarantined(db, store) -> None:
    _list_close(db, store, "29349.80")
    outcome = _ingest(db, store, TAIEX, date(2026, 1, 1), TAIEX_MONTH)
    assert outcome.status == "quarantined" and outcome.appended == 0
    fetch = _fetch(db, outcome.fetch_id)
    assert fetch["reason_code"] == "close_mismatch"
    assert "2026-01-02" in fetch["reason_detail"]


class _CrashingAdapter:
    """The real adapter's request, but a parser that dies mid-file."""

    def __init__(self, real) -> None:
        self._real = real
        self.source, self.version, self.dataset_code = real.source, real.version, real.dataset_code

    def resource(self, request):
        return self._real.resource(request)

    def parse(self, content, request):
        raise RuntimeError("parser crashed")


def test_the_raw_file_is_on_disk_before_anything_parses_it(db, store, universe) -> None:
    from dataclasses import replace

    job = replace(DAILY, adapter=_CrashingAdapter(DAILY.adapter))
    with pytest.raises(RuntimeError, match="parser crashed"):
        _ingest(db, store, job, DAY, MI_INDEX)
    digest = hashlib.sha256(MI_INDEX).hexdigest()
    root = Path(store.configuration_identity["root"])
    assert (root / digest[:2] / digest).read_bytes() == MI_INDEX


def _trading_days(db, store, *days: date) -> None:
    fetch_id = record_fetch(
        db,
        FetchRecord("trading_calendar", "twse", "cal", None, "gap_fill", "t", "abc",
                    datetime.now(UTC)),
        content=b"cal", status="succeeded", store=store,
    )
    for day in days:
        db.execute(sa.text("INSERT INTO trading_days VALUES (:d, :f)"), {"d": day, "f": fetch_id})


def test_a_backfill_walks_the_stored_trading_days(db, store) -> None:
    from stock_data_center.v2.backfill import periods

    _trading_days(db, store, date(2026, 1, 30), date(2026, 2, 2), date(2026, 2, 3))
    assert periods(db, DAILY, date(2026, 1, 1), date(2026, 2, 2)) == [
        date(2026, 1, 30), date(2026, 2, 2)
    ]
    # A monthly job asks once per month that has a trading day in the range.
    assert periods(db, TAIEX, date(2026, 1, 1), date(2026, 2, 28)) == [
        date(2026, 1, 1), date(2026, 2, 1)
    ]


def test_a_backfill_resumes_and_reports(db, store, universe) -> None:
    from stock_data_center.v2.backfill import run

    _trading_days(db, store, date(2024, 7, 24), DAY)
    closed = (FIXTURES / "twse_mi_index_allbut0999_20240724_closed.json").read_bytes()
    fetcher = Replay(closed, MI_INDEX)
    report = run(db, [DAILY], date(2024, 1, 1), DAY, fetcher=fetcher,
                 git_commit="abc", purpose="gap_fill", store=store)
    assert report["daily_prices/twse_mi_index"] == {
        "periods": 2, "skipped": 0, "succeeded": 1, "empty": 1, "appended": 2,
        "unchanged": 0, "out_of_scope": report["daily_prices/twse_mi_index"]["out_of_scope"],
    }
    again = run(db, [DAILY], date(2024, 1, 1), DAY, fetcher=Replay(), git_commit="abc",
                purpose="gap_fill", store=store)
    assert again["daily_prices/twse_mi_index"]["skipped"] == 2
    # --refetch fetches again, and an unchanged day appends nothing.
    refetched = run(db, [DAILY], DAY, DAY, fetcher=Replay(MI_INDEX), git_commit="abc",
                    purpose="correction_check", store=store, refetch=True)
    assert (refetched["daily_prices/twse_mi_index"]["appended"],
            refetched["daily_prices/twse_mi_index"]["unchanged"]) == (0, 2)


def test_a_date_fetched_before_it_settles_is_stored_and_fetched_again(
    db, store, universe
) -> None:
    # Everything seen is stored; when a client may see it is `visible`'s job.
    early = xd.available_from(DAY) - timedelta(minutes=1)
    outcome = _ingest(db, store, DAILY, DAY, MI_INDEX, at=early)
    assert (outcome.status, outcome.appended) == ("succeeded", 2)
    # The file can still change until it settles, so the date is still to do.
    assert xd.pending(db, DAILY, [DAY]) == [DAY]
    again = _ingest(db, store, DAILY, DAY, MI_INDEX, at=xd.available_from(DAY))
    assert (again.appended, again.unchanged) == (0, 2)
    assert xd.pending(db, DAILY, [DAY]) == []


def test_a_month_is_done_only_once_its_last_day_has_settled(db, store) -> None:
    _list_close(db, store, "29349.81")
    month = date(2026, 1, 1)
    outcome = _ingest(db, store, TAIEX, month, TAIEX_MONTH, at=xd.available_from(date(2026, 1, 15)))
    assert outcome.appended == outcome.parsed
    assert xd.pending(db, TAIEX, [month]) == [month]
    later = _ingest(db, store, TAIEX, month, TAIEX_MONTH, at=xd.available_from(date(2026, 1, 31)))
    assert (later.appended, later.unchanged) == (0, outcome.parsed)
    assert xd.pending(db, TAIEX, [month]) == []

def test_an_empty_answer_before_the_rule_instant_is_asked_again(db, store, universe) -> None:
    closed = (FIXTURES / "twse_mi_index_allbut0999_20240724_closed.json").read_bytes()
    day = date(2024, 7, 24)
    _ingest(db, store, DAILY, day, closed, at=xd.available_from(day) - timedelta(hours=8))
    assert xd.pending(db, DAILY, [day]) == [day]
