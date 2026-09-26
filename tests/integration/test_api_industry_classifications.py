"""Step 39-c: the `industry-classifications` dataset and the stock list's industry.

Acceptance (ROADMAP Step 39 "39-c"): periods are derived at query time from the
announcements, the by-category quotes and today's ISIN list; `start`/`end`
returns the periods overlapping the range and `date` those in effect that day;
`stock_id` narrows them; the three PIT parameters apply, and under market PIT no
period's `available_at` is later than `information_as_of`; every period names
its provenance; a delisted company in `/v1/stocks` carries its last known
industry, labelled as such.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from stock_data_center import api
from stock_data_center.db.schema_v2 import (
    industry_changes,
    industry_observations,
    listings,
    stocks,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import visibility as vis
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

KEY = "test-key-0123456789"
PATH = "/v1/datasets/industry-classifications"
TAIPEI = ZoneInfo("Asia/Taipei")
ISIN_FETCHED = datetime(2026, 9, 25, 16, 19, tzinfo=UTC)
RECORDED = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)


@pytest.fixture
def client(db):
    app = api.create_app(api_key=KEY, connect=lambda: nullcontext(db))
    with TestClient(app) as client:
        yield client


def _get(client, path=PATH, **params):
    return client.get(path, params=params, headers={"X-API-Key": KEY})


@pytest.fixture
def seeded(db, tmp_path):
    store = LocalRawArtifactStore(tmp_path)

    def fetch(dataset, source, content, at):
        return record_fetch(db, FetchRecord(dataset, source, f"{source}:{content!r}", None,
                                            "gap_fill", "t", "abc", at),
                            content=content, status="succeeded", store=store)

    isin = fetch("stocks", "twse_isin", b"isin", ISIN_FETCHED)
    notice = fetch("industry_changes", "twse_announcement", b"notice", RECORDED)
    otc_notice = fetch("industry_changes", "tpex_announcement", b"otc notice", RECORDED)
    page = fetch("industry_observations", "twse_mi_index", b"page 17", RECORDED)
    otc_page = fetch("industry_observations", "tpex_otc_quotes", b"page 35", RECORDED)
    db.execute(sa.insert(stocks), [
        {"stock_id": "3130", "name": "一零四", "industry": "數位雲端", "fetch_id": isin},
        {"stock_id": "2330", "name": "台積電", "industry": "半導體業", "fetch_id": isin},
        {"stock_id": "8476", "name": "台境", "industry": "綠能環保", "fetch_id": isin},
        {"stock_id": "2888", "name": "新光金", "industry": None, "fetch_id": isin}])
    def span(stock_id, market, listed_on=None, delisted_on=None):
        return {"stock_id": stock_id, "market": market, "listed_on": listed_on,
                "delisted_on": delisted_on, "fetch_id": isin,
                "listed_fetch_id": isin if listed_on else None,
                "delisted_fetch_id": isin if delisted_on else None}

    db.execute(sa.insert(listings), [
        span("3130", "sii"), span("2330", "sii"),
        span("8476", "otc", date(2017, 3, 28), date(2023, 10, 31)),
        span("8476", "sii", date(2023, 10, 31)),
        span("2888", "sii", date(2002, 2, 19), date(2025, 7, 24))])
    db.execute(sa.insert(industry_changes), [
        {"stock_id": "3130", "source": "twse_announcement", "effective_date": date(2023, 7, 3),
         "recorded_at": RECORDED, "announced_on": date(2023, 5, 22),
         "document_number": "臺證上一字第1121802250號", "old_industry": "資訊服務業",
         "new_industry": "數位雲端", "fetch_id": notice},
        {"stock_id": "8476", "source": "tpex_announcement", "effective_date": date(2023, 7, 3),
         "recorded_at": RECORDED, "announced_on": date(2023, 5, 23),
         "document_number": "證櫃監字第11202011201號", "old_industry": "其他",
         "new_industry": "綠能環保", "fetch_id": otc_notice}])
    db.execute(sa.insert(industry_observations), [
        # An earlier sweep, not the last trading day: the anchor is the last one.
        {"stock_id": "2888", "source": "twse_mi_index", "trade_date": date(2024, 4, 3),
         "recorded_at": RECORDED, "industry_code": "20", "fetch_id": page},
        {"stock_id": "2888", "source": "twse_mi_index", "trade_date": date(2025, 7, 11),
         "recorded_at": RECORDED, "industry_code": "17", "fetch_id": page},
        {"stock_id": "8476", "source": "tpex_otc_quotes", "trade_date": date(2023, 10, 30),
         "recorded_at": RECORDED, "industry_code": "35", "fetch_id": otc_page}])
    return {"isin": isin, "notice": notice, "page": page}


def _periods(body) -> list[tuple]:
    return [(r["stock_id"], r["market"], r["industry_code"], r["effective_from"],
             r["effective_to"]) for r in body["rows"]]


# ---------------------------------------------------------------- the dataset


def test_the_dataset_is_described(client) -> None:
    [described] = [d for d in _get(client, "/v1/datasets").json()["datasets"]
                   if d["name"] == "industry-classifications"]
    assert described["keys"] == ["stock_id", "market", "effective_from"]
    assert described["period"] == "effective_from"
    assert {"industry_code", "industry_name", "effective_to", "available_at",
            "recorded_at"} <= set(described["columns"])
    assert set(described["sources"]) == {"twse_announcement", "tpex_announcement", "twse_isin",
                                         "tpex_otc_quotes", "twse_mi_index"}


def test_a_date_gives_the_category_in_effect_that_day(client, seeded) -> None:
    body = _get(client, date="2023-06-30").json()
    assert body["dataset"] == "industry-classifications" and body["pit"]["mode"] == "market"
    assert {(r["stock_id"], r["market"]): (r["industry_code"], r["industry_name"])
            for r in body["rows"]} == {
        ("2330", "sii"): ("24", "半導體業"), ("3130", "sii"): ("30", "資訊服務業"),
        ("8476", "otc"): ("20", "其他業"), ("2888", "sii"): ("17", "金融保險業")}
    on_change = {(r["stock_id"], r["market"]): r["industry_code"]
                 for r in _get(client, date="2023-07-03").json()["rows"]}
    assert on_change[("3130", "sii")] == "36" and on_change[("8476", "otc")] == "35"
    assert ("8476", "sii") not in on_change  # listed on TWSE from 2023-10-31
    assert "2888" not in {r["stock_id"] for r in _get(client, date="2025-07-24").json()["rows"]}


def test_a_range_gives_every_period_overlapping_it(client, seeded) -> None:
    rows = _periods(_get(client, start="2020-01-02", end="2026-09-26", stock_id="8476").json())
    assert rows == [("8476", "otc", "20", "2020-01-02", "2023-07-03"),
                    ("8476", "otc", "35", "2023-07-03", "2023-10-31"),
                    ("8476", "sii", "35", "2023-10-31", None)]
    rows = _periods(_get(client, start="2024-01-01", end="2024-12-31", stock_id="3130").json())
    assert rows == [("3130", "sii", "36", "2023-07-03", None)]


def test_a_period_names_its_basis_source_and_provenance(client, seeded) -> None:
    rows = {(r["stock_id"], r["effective_from"]): r
            for r in _get(client, start="2020-01-02", end="2026-09-26").json()["rows"]}
    before, after = rows["3130", "2020-01-02"], rows["3130", "2023-07-03"]
    assert (before["basis"], after["basis"]) == ("before_change", "change")
    assert before["source"] == after["source"] == "twse_announcement"
    assert after["provenance"]["fetch_id"] == str(seeded["notice"])
    assert after["provenance"]["raw_sha256"]
    assert after["available_at"] == "2023-05-23T00:00:00+08:00"
    assert before["available_at"] == "2020-01-02T00:00:00+08:00"
    assert after["recorded_at"].startswith("2026-09-26")
    delisted = rows["2888", "2020-01-02"]
    assert (delisted["basis"], delisted["source"], delisted["effective_to"]) == (
        "anchor", "twse_mi_index", "2025-07-24")
    assert delisted["provenance"]["fetch_id"] == str(seeded["page"])
    listed = rows["2330", "2020-01-02"]
    assert listed["source"] == "twse_isin"
    assert datetime.fromisoformat(listed["recorded_at"]) == ISIN_FETCHED


def test_market_pit_returns_only_what_was_public(client, seeded) -> None:
    instant = "2023-05-22T12:00:00+08:00"
    body = _get(client, start="2020-01-02", end="2026-09-26", information_as_of=instant).json()
    assert body["pit"]["information_as_of"] == instant
    assert all(datetime.fromisoformat(r["available_at"]) <= datetime.fromisoformat(instant)
               for r in body["rows"])
    # The 2023 change is not public yet, so nothing says the period will end.
    assert [(r["industry_code"], r["effective_to"]) for r in body["rows"]
            if r["stock_id"] == "3130"] == [("30", None)]


def test_knowledge_before_anything_was_recorded_sees_nothing(client, seeded) -> None:
    body = _get(client, date="2023-06-30", knowledge_as_of="2026-09-01T00:00:00+08:00").json()
    assert body["rows"] == []


def test_system_pit_is_answered(client, seeded) -> None:
    body = _get(client, date="2023-06-30", system_as_of="latest").json()
    assert body["pit"]["mode"] == "system" and len(body["rows"]) == 4


@pytest.mark.parametrize("params", [
    {},  # neither a range nor a date
    {"date": "2023-06-30", "start": "2023-01-01", "end": "2023-12-31"},
    {"start": "2023-01-01"},
    {"start": "2023-12-31", "end": "2023-01-01"},
    {"date": "2019-12-31"},
    {"date": "2023-06-31"},
    {"date": "2023-06-30", "source": "twse_isin"},
    {"date": "2023-06-30", "market": "sii"},
    {"date": "2023-06-30", "stock_id": [str(n) for n in range(1000, 1201)]},
])
def test_a_request_it_cannot_answer_is_400(client, params) -> None:
    assert _get(client, **params).status_code == 400


def test_the_whole_market_over_the_whole_window_is_one_request(client, seeded) -> None:
    body = _get(client, start="2020-01-02", end="2026-09-26").json()
    assert {r["stock_id"] for r in body["rows"]} == {"3130", "2330", "8476", "2888"}


# ---------------------------------------------------------------- the stock list


def test_a_delisted_company_carries_its_last_known_industry(client, seeded) -> None:
    rows = {r["stock_id"]: r for r in _get(client, "/v1/stocks").json()["rows"]}
    assert (rows["2888"]["industry"], rows["2888"]["industry_source"]) == (
        "金融保險業", "last_period")
    assert (rows["2330"]["industry"], rows["2330"]["industry_source"]) == ("半導體業", "isin")


# ---------------------------------------------------------------- visibility, directly


def test_the_periods_come_from_one_place(db, seeded) -> None:
    periods = vis.industry_periods(db, vis.MarketPIT(vis.FOREVER, vis.FOREVER),
                                   start=date(2023, 6, 30), end=date(2023, 6, 30))
    assert {(p.stock_id, p.industry_code) for p in periods} == {
        ("2330", "24"), ("3130", "30"), ("8476", "20"), ("2888", "17")}
    assert vis.industry_periods(db, vis.MarketPIT(vis.FOREVER, vis.FOREVER),
                                start=date(2023, 6, 30), end=date(2023, 6, 30),
                                stock_ids=["3130"])[0].stock_id == "3130"
