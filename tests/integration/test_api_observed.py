"""Step 27-b: the public HTTP API over the observed datasets.

Every response carries the PIT context it was answered in, with any default
resolved to an explicit instant (CLAUDE.md §59), and every row its
`available_at`, `recorded_at` and provenance (the fetch and its raw file's
SHA-256, §27). Visibility is `stock_data_center.v2.visibility`'s (§19); the API
only parses, validates and renders. Clients see dataset names, never table
names (§55). A column a source never publishes is omitted for that source and
named in `unsourced`; a NULL anywhere else is a value the source did not give
for that row. Every data request needs the API key (owner, 2026-09-25).
"""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from stock_data_center import api
from stock_data_center.db.base import metadata
from stock_data_center.db.schema_v2 import (
    corporate_actions,
    daily_prices,
    index_prices,
    monthly_revenues,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import exchange_daily as xd
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

KEY = "test-key-0123456789"
DAY = date(2024, 7, 1)
RELEASED = xd.available_from(DAY)


@pytest.fixture
def client(db):
    app = api.create_app(api_key=KEY, connect=lambda: nullcontext(db))
    with TestClient(app) as client:
        yield client


def _get(client, path, **params):
    return client.get(path, params=params, headers={"X-API-Key": KEY})


@pytest.fixture
def fetch(db, tmp_path):
    store = LocalRawArtifactStore(tmp_path)

    def make(content: bytes = b"raw"):
        return record_fetch(
            db,
            FetchRecord("daily_price", "twse_mi_index", "t", None, "gap_fill", "t", "abc",
                        datetime(2024, 1, 1, tzinfo=UTC)),
            content=content, status="succeeded", store=store)

    first = make()
    for stock_id in ("2330", "2317"):
        db.execute(sa.text("INSERT INTO stocks (stock_id, name, fetch_id) "
                           "VALUES (:s, :n, :f) ON CONFLICT DO NOTHING"),
                   {"s": stock_id, "n": stock_id, "f": first})
        db.execute(sa.text("INSERT INTO listings (stock_id, market, fetch_id) "
                           "VALUES (:s, 'sii', :f) ON CONFLICT DO NOTHING"),
                   {"s": stock_id, "f": first})
    return make


def _price(db, fetch_id, close, recorded_at, *, stock_id="2330", day=DAY):
    db.execute(sa.insert(daily_prices).values(
        stock_id=stock_id, source="twse_mi_index", trade_date=day, close_price=Decimal(close),
        volume=10, fetch_id=fetch_id, recorded_at=recorded_at))


# ---------------------------------------------------------------- access


def test_every_request_needs_the_api_key(client) -> None:
    assert client.get("/v1/datasets").status_code == 401
    assert client.get("/v1/datasets", headers={"X-API-Key": "wrong"}).status_code == 401
    assert _get(client, "/v1/datasets").status_code == 200


def test_the_app_refuses_to_start_without_a_key(db) -> None:
    with pytest.raises(ValueError, match="API key"):
        api.create_app(api_key="", connect=lambda: nullcontext(db))


def test_datasets_are_named_apart_from_tables(client) -> None:
    # §55: clients know dataset concepts, not PostgreSQL table names.
    listing = _get(client, "/v1/datasets").json()["datasets"]
    names = {d["name"] for d in listing}
    assert {d["name"] for d in listing if d["kind"] == "observed"} == {
        "daily-prices", "indices", "official-valuations", "institutional-flows",
        "institutional-market-flows", "foreign-holdings", "margin-trading",
        "securities-lending", "shareholding-distributions", "monthly-revenues",
        "corporate-actions", "financial-reports", "industry-classifications",
    }
    assert not names & set(metadata.tables)
    daily = next(d for d in listing if d["name"] == "daily-prices")
    assert daily["period"] == "trade_date"
    assert "sources" in daily and "unsourced" in daily


def test_an_unknown_dataset_is_404(client) -> None:
    assert _get(client, "/v1/datasets/daily_prices", start=DAY, end=DAY).status_code == 404


# ---------------------------------------------------------------- PIT context


def test_no_pit_parameter_means_latest_resolved_to_one_instant(client, db, fetch) -> None:
    _price(db, fetch(), "100", RELEASED)
    before = datetime.now(UTC)
    body = _get(client, "/v1/datasets/daily-prices", start=DAY, end=DAY).json()
    after = datetime.now(UTC)
    pit = body["pit"]
    assert pit["mode"] == "market"
    information = datetime.fromisoformat(pit["information_as_of"])
    assert before <= information <= after
    assert pit["knowledge_as_of"] == pit["information_as_of"]
    assert pit["defaulted"] == ["information_as_of", "knowledge_as_of"]
    assert len(body["rows"]) == 1


def test_latest_and_now_are_aliases_resolved_before_the_query(client) -> None:
    body = _get(client, "/v1/datasets/daily-prices", start=DAY, end=DAY,
                information_as_of="latest", knowledge_as_of="now").json()
    assert body["pit"]["information_as_of"] == body["pit"]["knowledge_as_of"]
    assert body["pit"]["defaulted"] == []
    assert body["pit"]["aliases"] == {"information_as_of": "latest", "knowledge_as_of": "now"}


def test_an_instant_needs_an_offset(client) -> None:
    response = _get(client, "/v1/datasets/daily-prices", start=DAY, end=DAY,
                    information_as_of="2024-07-02T12:00:00")
    assert response.status_code == 400
    assert "offset" in response.json()["detail"]


def test_market_and_system_pit_do_not_mix(client) -> None:
    response = _get(client, "/v1/datasets/daily-prices", start=DAY, end=DAY,
                    information_as_of="2024-07-02T12:00:00+08:00",
                    system_as_of="2024-07-02T12:00:00+08:00")
    assert response.status_code == 400


def test_an_unknown_parameter_is_refused_not_ignored(client) -> None:
    # A misspelled PIT parameter must not silently become "latest".
    response = _get(client, "/v1/datasets/daily-prices", start=DAY, end=DAY,
                    infomation_as_of="2024-07-02T12:00:00+08:00")
    assert response.status_code == 400
    assert "infomation_as_of" in response.json()["detail"]


def test_a_repeated_parameter_is_refused_not_resolved_to_one(client) -> None:
    # Code review of #61: Starlette keeps only the last value of a repeated key,
    # so an instant followed by "latest" would silently mean latest.
    for name, value in (("information_as_of", "2024-07-02T12:00:00+08:00"),
                        ("knowledge_as_of", "2024-07-02T12:00:00+08:00"),
                        ("system_as_of", "2024-07-02T12:00:00+08:00"),
                        ("start", DAY.isoformat()), ("end", DAY.isoformat())):
        params = [("start", DAY), ("end", DAY), (name, value), (name, "latest")]
        if name in ("start", "end"):
            params = [("start", DAY), ("end", DAY), (name, value)]
        response = client.get("/v1/datasets/daily-prices", params=params,
                              headers={"X-API-Key": KEY})
        assert response.status_code == 400, name
        assert name in response.json()["detail"]


def test_market_pit_answers_as_of_both_instants(client, db, fetch) -> None:
    fetch_id = fetch()
    _price(db, fetch_id, "100", RELEASED)
    corrected = RELEASED + timedelta(days=10)
    _price(db, fetch_id, "101", corrected)

    def close(**pit):
        rows = _get(client, "/v1/datasets/daily-prices", start=DAY, end=DAY, **pit).json()["rows"]
        return [row["close_price"] for row in rows]

    before = (RELEASED - timedelta(seconds=1)).isoformat()
    assert close(information_as_of=before) == []
    assert close(information_as_of=RELEASED.isoformat()) == [100]
    assert close(information_as_of=corrected.isoformat()) == [101]
    assert close(knowledge_as_of=(corrected - timedelta(seconds=1)).isoformat()) == [100]


def test_system_pit(client, db, fetch) -> None:
    _price(db, fetch(), "99", RELEASED - timedelta(hours=5))
    at = (RELEASED - timedelta(hours=5)).isoformat()
    body = _get(client, "/v1/datasets/daily-prices", start=DAY, end=DAY, system_as_of=at).json()
    assert body["pit"] == {"mode": "system", "system_as_of": at, "defaulted": [],
                           "aliases": {}}
    assert [r["close_price"] for r in body["rows"]] == [99]


# ---------------------------------------------------------------- rows


def test_a_row_carries_its_times_and_provenance_and_no_storage_detail(client, db, fetch) -> None:
    fetch_id = fetch(b"the raw file")
    _price(db, fetch_id, "100.25", RELEASED + timedelta(hours=1))
    [row] = _get(client, "/v1/datasets/daily-prices", start=DAY, end=DAY).json()["rows"]
    assert row["stock_id"] == "2330" and row["source"] == "twse_mi_index"
    assert row["trade_date"] == "2024-07-01"
    assert datetime.fromisoformat(row["available_at"]) == RELEASED
    assert datetime.fromisoformat(row["recorded_at"]) == RELEASED + timedelta(hours=1)
    assert row["provenance"] == {"fetch_id": str(fetch_id),
                                 "raw_sha256": hashlib.sha256(b"the raw file").hexdigest()}
    assert "fetch_id" not in row


def test_a_decimal_is_rendered_exactly(client, db, fetch) -> None:
    # Its published digits, trailing zero included: a float would print 1234.5.
    _price(db, fetch(), "1234.50", RELEASED)
    response = _get(client, "/v1/datasets/daily-prices", start=DAY, end=DAY)
    assert '"close_price":1234.50' in response.text
    assert json.loads(response.text)["rows"][0]["close_price"] == 1234.5


def test_a_column_the_source_never_publishes_is_omitted_and_named(client, db, fetch) -> None:
    # CLAUDE.md §52: whole-list index sources publish close and changes only.
    db.execute(sa.insert(index_prices).values(
        source="tpex_index_summary", index_name="櫃買指數", trade_date=DAY,
        close_value=Decimal("250.1"), change_points=Decimal("1.2"), fetch_id=fetch(),
        recorded_at=RELEASED))
    body = _get(client, "/v1/datasets/indices", start=DAY, end=DAY).json()
    [row] = body["rows"]
    assert "open_value" not in row and "high_value" not in row and "low_value" not in row
    assert row["close_value"] == 250.1
    assert set(body["unsourced"]["tpex_index_summary"]) == {"open_value", "high_value",
                                                            "low_value"}


def test_filters_by_stock_and_source(client, db, fetch) -> None:
    fetch_id = fetch()
    _price(db, fetch_id, "1", RELEASED)
    _price(db, fetch_id, "2", RELEASED, stock_id="2317")
    rows = client.get("/v1/datasets/daily-prices", headers={"X-API-Key": KEY}, params=[
        ("start", DAY), ("end", DAY), ("stock_id", "2330"), ("stock_id", "2317"),
        ("source", "twse_mi_index")]).json()["rows"]
    assert sorted(r["stock_id"] for r in rows) == ["2317", "2330"]
    rows = _get(client, "/v1/datasets/daily-prices", start=DAY, end=DAY, stock_id="2317").json()
    assert [r["stock_id"] for r in rows["rows"]] == ["2317"]


def test_a_whole_market_query_is_bounded(client) -> None:
    long = DAY + timedelta(days=api.WHOLE_MARKET_DAYS)
    assert _get(client, "/v1/datasets/daily-prices", start=DAY, end=long).status_code == 400
    assert _get(client, "/v1/datasets/daily-prices", start=DAY, end=long,
                stock_id="2330").status_code == 200


def test_the_range_is_required_and_ordered(client) -> None:
    assert _get(client, "/v1/datasets/daily-prices", end=DAY).status_code == 400
    assert _get(client, "/v1/datasets/daily-prices", start=DAY,
                end=DAY - timedelta(days=1)).status_code == 400


def test_a_stock_filter_on_a_table_without_stocks_is_400(client) -> None:
    assert _get(client, "/v1/datasets/indices", start=DAY, end=DAY,
                stock_id="2330").status_code == 400


def _index(db, fetch_id, name, close, *, source="tpex_index_summary"):
    db.execute(sa.insert(index_prices).values(
        source=source, index_name=name, trade_date=DAY, close_value=Decimal(close),
        fetch_id=fetch_id, recorded_at=RELEASED))


def test_indices_are_filtered_by_name_in_the_query(client, db, fetch) -> None:
    # Step 37-b (owner, 2026-09-26): a whole source's indices over its history
    # are tens of megabytes; index_name narrows the query itself, repeatable
    # like stock_id.
    fetch_id = fetch()
    _index(db, fetch_id, "指數:櫃買指數", "250.1")
    _index(db, fetch_id, "指數:半導體業", "600.2")
    _index(db, fetch_id, "報酬指數:櫃買指數", "300.3")
    rows = client.get("/v1/datasets/indices", headers={"X-API-Key": KEY}, params=[
        ("start", DAY), ("end", DAY), ("index_name", "指數:櫃買指數"),
        ("index_name", "指數:半導體業")]).json()["rows"]
    assert sorted(r["index_name"] for r in rows) == ["指數:半導體業", "指數:櫃買指數"]
    body = _get(client, "/v1/datasets/indices", start=DAY, end=DAY,
                index_name="報酬指數:櫃買指數").json()
    assert [r["close_value"] for r in body["rows"]] == [300.3]
    assert body["query"]["index_name"] == ["報酬指數:櫃買指數"]
    assert len(_get(client, "/v1/datasets/indices", start=DAY, end=DAY).json()["rows"]) == 3


def test_an_index_name_nothing_publishes_is_an_empty_answer(client, db, fetch) -> None:
    _index(db, fetch(), "指數:櫃買指數", "250.1")
    body = _get(client, "/v1/datasets/indices", start=DAY, end=DAY, index_name="櫃買").json()
    assert body["rows"] == []


def test_only_indices_take_an_index_name(client) -> None:
    for name in ("daily-prices", "institutional-market-flows", "technical-indicators"):
        response = _get(client, f"/v1/datasets/{name}", start=DAY, end=DAY,
                        index_name="指數:櫃買指數")
        assert response.status_code == 400, name
        assert "index_name" in response.json()["detail"]


def test_an_unknown_publication_is_invisible_to_market_pit_only(client, db, fetch) -> None:
    recorded = datetime(2026, 9, 19, tzinfo=UTC)
    db.execute(sa.insert(monthly_revenues).values(
        stock_id="2330", source="mops_t21sc03_sii", revenue_month=date(2024, 6, 1),
        revenue=100, fetch_id=fetch(), recorded_at=recorded))
    params = {"start": date(2024, 6, 1), "end": date(2024, 6, 1)}
    assert _get(client, "/v1/datasets/monthly-revenues", **params).json()["rows"] == []
    rows = _get(client, "/v1/datasets/monthly-revenues", **params,
                system_as_of=recorded.isoformat()).json()["rows"]
    assert [r["revenue"] for r in rows] == [100]
    assert "published_at" not in rows[0]


def test_a_corporate_action_names_both_raw_files(client, db, fetch) -> None:
    listed, detail = fetch(b"list"), fetch(b"detail")
    ex = date(2024, 7, 10)
    db.execute(sa.insert(corporate_actions).values(
        stock_id="2330", source="twse_twt49u", ex_date=ex, event_type="息",
        reference_price=Decimal(90), cash_dividend_per_share=Decimal("4.5"),
        fetch_id=listed, detail_fetch_id=detail, recorded_at=datetime(2024, 7, 11, tzinfo=UTC)))
    [row] = _get(client, "/v1/datasets/corporate-actions", start=ex, end=ex).json()["rows"]
    assert row["provenance"] == {
        "fetch_id": str(listed), "raw_sha256": hashlib.sha256(b"list").hexdigest(),
        "detail_fetch_id": str(detail), "detail_raw_sha256": hashlib.sha256(b"detail").hexdigest(),
    }
    assert "retracted" not in row and "old_shares" not in row
    assert row["cash_dividend_per_share"] == 4.5


def test_reads_are_read_only(engine) -> None:
    with api.read_only(engine) as connection:
        assert connection.scalar(sa.text("SHOW transaction_read_only")) == "on"
