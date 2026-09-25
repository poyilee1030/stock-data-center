"""Step 36: `adjusted-prices-pit` over the public API.

Computed on demand for one stock under the request's PIT context, market or
system (CLAUDE.md §43). Each row carries its raw prices beside the adjusted
ones and the cumulative factor between them, so raw and adjusted stay
distinguishable (§85); the response lists the events applied, each with its
factor, `available_at` and provenance, so the adjustment can be audited.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from stock_data_center import api
from stock_data_center.db.schema_v2 import corporate_actions, daily_prices
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import exchange_daily as xd
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

KEY = "test-key-0123456789"
PATH = "/v1/datasets/adjusted-prices-pit"
D1, D2, D3 = date(2024, 7, 1), date(2024, 7, 2), date(2024, 7, 3)


@pytest.fixture
def client(db):
    app = api.create_app(api_key=KEY, connect=lambda: nullcontext(db), git_commit="abc")
    with TestClient(app) as client:
        yield client


def _get(client, path=PATH, **params):
    return client.get(path, params=params, headers={"X-API-Key": KEY})


@pytest.fixture
def fetch_id(db, tmp_path):
    fetch = record_fetch(
        db,
        FetchRecord("daily_price", "tpex_otc_quotes", "t", None, "gap_fill", "t", "abc",
                    datetime(2024, 1, 1, tzinfo=UTC)),
        content=b"raw", status="succeeded", store=LocalRawArtifactStore(tmp_path))
    db.execute(sa.text("INSERT INTO stocks (stock_id, name, fetch_id) "
                       "VALUES ('6488', '環球晶', :f) ON CONFLICT DO NOTHING"), {"f": fetch})
    for day, close in ((D1, "100"), (D2, "100"), (D3, "91")):
        db.execute(sa.insert(daily_prices).values(
            stock_id="6488", source="tpex_otc_quotes", trade_date=day, open_price=close,
            high_price=close, low_price=close, close_price=close, volume=1000,
            fetch_id=fetch, recorded_at=xd.available_from(day)))
    db.execute(sa.insert(corporate_actions).values(
        stock_id="6488", source="tpex_exdailyq", ex_date=D3, event_type="除息",
        close_before=Decimal(100), reference_price=Decimal(90), fetch_id=fetch,
        recorded_at=datetime(2024, 7, 3, 1, tzinfo=UTC)))
    return fetch


def test_it_is_listed_with_its_definition(client) -> None:
    listing = {d["name"]: d for d in _get(client, "/v1/datasets").json()["datasets"]}
    entry = listing["adjusted-prices-pit"]
    assert entry["kind"] == "derived_on_demand"
    assert entry["derivation"]["dataset_code"] == "adjusted_prices_pit"
    assert entry["derivation"]["inputs"] == ["daily-prices", "corporate-actions"]


def test_rows_carry_raw_and_adjusted_prices_and_the_events_behind_them(client,
                                                                        fetch_id) -> None:
    body = _get(client, stock_id="6488", start=D1.isoformat(), end=D3.isoformat()).json()
    assert body["dataset"] == "adjusted-prices-pit"
    assert body["derivation"]["git_commit"] == "abc"
    assert body["pit"]["mode"] == "market"
    first = body["rows"][0]
    assert (first["trade_date"], first["source"]) == (D1.isoformat(), "tpex_otc_quotes")
    assert first["close_price"] == 100
    assert first["adjustment_factor"] == pytest.approx(0.9)
    assert first["adjusted_close_price"] == pytest.approx(90)
    assert body["rows"][-1]["adjusted_close_price"] == 91
    [event] = body["events"]
    assert event["ex_date"] == D3.isoformat()
    assert event["source"] == "tpex_exdailyq"
    assert event["factor"] == pytest.approx(0.9)
    assert event["provenance"]["raw_sha256"]
    assert event["available_at"]


def test_system_pit_is_answered(client, fetch_id) -> None:
    # Recorded before the event: the rows the Data Center had, unadjusted.
    body = _get(client, stock_id="6488", start=D1.isoformat(), end=D3.isoformat(),
                system_as_of="2024-07-03T08:00:00+08:00").json()
    assert body["pit"]["mode"] == "system"
    assert body["events"] == []
    assert [r["adjustment_factor"] for r in body["rows"]] == [1, 1]


def test_it_refuses_what_it_cannot_answer(client, fetch_id) -> None:
    base = {"start": D1.isoformat(), "end": D3.isoformat()}
    assert _get(client, **base).status_code == 400  # one stock at a time
    assert _get(client, **base, stock_id="9999").status_code == 400
    assert _get(client, **base, stock_id="6488", view="rolling").status_code == 400
    assert _get(client, **base, stock_id=["6488", "6488"]).status_code == 400
