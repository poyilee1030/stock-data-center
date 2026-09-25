"""Step 27-c: financial reports, derived data and reference data over the public API.

A financial report is served as the version a PIT context sees, carrying that
version's own facts (CLAUDE.md §20). A stored derived row follows the latest
inputs (§43) and has no knowledge axis, so it is filtered by
`information_as_of` alone: row D is served only once every input it was
computed from is public, which is D's own release instant unless a later
correction of an input it reads came after (owner, 2026-09-25). A historical
`knowledge_as_of` or `system_as_of` is refused: the table is overwritten and
cannot answer one. `technical_indicators_pit:v1` is computed on demand under
full PIT. The stock list and the trading calendar are reference data, not PIT.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from stock_data_center import api
from stock_data_center.db.base import metadata
from stock_data_center.db.schema_v2 import (
    daily_prices,
    financial_report_facts,
    financial_reports,
    institutional_streaks,
    margin_metrics,
    margin_trading,
    shareholding_concentration,
    technical_indicators,
    trading_days,
    valuation_metrics,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import exchange_daily as xd
from stock_data_center.v2.derived import TechnicalIndicators
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch
from stock_data_center.v2.release_rules import tdcc_available_from

pytestmark = pytest.mark.integration

KEY = "test-key-0123456789"
DAY = date(2024, 7, 2)  # a Tuesday
PREVIOUS = date(2024, 7, 1)
COMPUTED = datetime(2026, 9, 24, tzinfo=UTC)


@pytest.fixture
def client(db):
    app = api.create_app(api_key=KEY, connect=lambda: nullcontext(db))
    with TestClient(app) as client:
        yield client


def _get(client, path, **params):
    return client.get(path, params=params, headers={"X-API-Key": KEY})


@pytest.fixture
def fetch_id(db, tmp_path):
    fetch = record_fetch(
        db,
        FetchRecord("daily_price", "twse_mi_index", "t", None, "gap_fill", "t", "abc",
                    datetime(2024, 1, 1, tzinfo=UTC)),
        content=b"raw", status="succeeded", store=LocalRawArtifactStore(tmp_path))
    for stock_id, market in (("2330", "sii"), ("6488", "otc")):
        db.execute(sa.text("INSERT INTO stocks (stock_id, name, market, industry, fetch_id) "
                           "VALUES (:s, :n, :m, 'x', :f) ON CONFLICT DO NOTHING"),
                   {"s": stock_id, "n": f"name {stock_id}", "m": market, "f": fetch})
    return fetch


# ---------------------------------------------------------------- listing


def test_every_dataset_is_listed_under_a_public_name(client) -> None:
    listing = {d["name"]: d for d in _get(client, "/v1/datasets").json()["datasets"]}
    new = {"financial-reports", "technical-indicators", "institutional-streaks",
           "institutional-cumulative-flows", "shareholding-concentrations", "margin-metrics",
           "short-interest-metrics", "valuation-metrics", "technical-indicators-pit"}
    assert new <= set(listing)
    assert not set(listing) & set(metadata.tables)
    assert listing["financial-reports"]["kind"] == "observed"
    assert listing["technical-indicators"]["kind"] == "derived"
    assert listing["technical-indicators-pit"]["kind"] == "derived_on_demand"


def test_a_derived_dataset_names_its_definition_and_inputs_by_public_name(client) -> None:
    # §42: the definition identifies the semantics; §55: inputs are datasets, not tables.
    listing = {d["name"]: d for d in _get(client, "/v1/datasets").json()["datasets"]}
    derivation = listing["valuation-metrics"]["derivation"]
    assert derivation["dataset_code"] == "valuation_metrics"
    assert derivation["derivation_version"] == "v1"
    assert derivation["inputs"] == ["daily-prices", "financial-reports"]
    assert derivation["formula"] and derivation["price_adjustment_convention"]
    for d in listing.values():
        for name in d.get("derivation", {}).get("inputs", ()):
            assert name in listing and name not in metadata.tables
    assert listing["technical-indicators-pit"]["derivation"]["dataset_code"] == \
        "technical_indicators_pit"


# ---------------------------------------------------------------- financial reports


PUBLISHED = datetime(2024, 8, 10, 6, tzinfo=UTC)
RESTATED = datetime(2024, 11, 1, tzinfo=UTC)


def _report(db, fetch_id, recorded_at, facts, *, published_at=PUBLISHED, stock_id="2330",
            year=2024, quarter=2) -> int:
    report_id = db.scalar(sa.insert(financial_reports).values(
        stock_id=stock_id, report_year=year, report_quarter=quarter,
        report_category="consolidated", published_at=published_at, recorded_at=recorded_at,
        fetch_id=fetch_id).returning(financial_reports.c.id))
    db.execute(sa.insert(financial_report_facts), [
        {"report_id": report_id, "statement": statement, "account_code": code,
         "concept": f"{{http://www.xbrl.org/tifrs}}c{code}", "period_start": None,
         "period_end": date(year, quarter * 3, 30), "unit": "TWD", "value": Decimal(value)}
        for statement, code, value in facts])
    return report_id


def _reports(client, **params):
    return _get(client, "/v1/datasets/financial-reports", start=date(2024, 6, 1),
                end=date(2024, 6, 30), stock_id="2330", **params)


def test_a_report_is_served_as_its_visible_version_with_its_own_facts(client, db,
                                                                       fetch_id) -> None:
    _report(db, fetch_id, PUBLISHED + timedelta(hours=1),
            [("balance_sheet", "1XXX", "100.50"), ("income_statement", "9750", "2.10")])
    # A restatement drops a fact: the new version carries only what it states (§20).
    _report(db, fetch_id, RESTATED, [("balance_sheet", "1XXX", "101.00")])

    first = _reports(client, information_as_of=(RESTATED - timedelta(seconds=1)).isoformat())
    [row] = first.json()["rows"]
    assert row["available_at"] == PUBLISHED.isoformat()
    assert {(f["account_code"], f["value"]) for f in row["facts"]} == {
        ("1XXX", 100.50), ("9750", 2.10)}
    assert '"value":100.50' in first.text  # exact digits, never a float

    [row] = _reports(client).json()["rows"]
    assert row["available_at"] == RESTATED.isoformat()
    assert [f["account_code"] for f in row["facts"]] == ["1XXX"]
    assert row["provenance"]["raw_sha256"]
    for storage in ("id", "published_at", "fetch_id"):
        assert storage not in row
    assert set(row["facts"][0]) == {"statement", "account_code", "concept", "period_start",
                                    "period_end", "unit", "value"}


def test_a_report_without_proven_publication_is_market_invisible(client, db,
                                                                  fetch_id) -> None:
    # §31: NULL published_at is never market visible, but the Data Center recorded it.
    _report(db, fetch_id, PUBLISHED, [("income_statement", "9750", "1.00")], published_at=None)
    assert _reports(client).json()["rows"] == []
    [row] = _reports(client, system_as_of="latest").json()["rows"]
    assert row["available_at"] is None


def test_facts_narrow_by_statement_and_account_code(client, db, fetch_id) -> None:
    _report(db, fetch_id, PUBLISHED, [("balance_sheet", "1XXX", "1"), ("balance_sheet", "2XXX", "2"),
                                      ("income_statement", "9750", "3")])
    [row] = _reports(client, statement="balance_sheet").json()["rows"]
    assert sorted(f["account_code"] for f in row["facts"]) == ["1XXX", "2XXX"]
    [row] = client.get("/v1/datasets/financial-reports",
                       params=[("start", "2024-06-01"), ("end", "2024-06-30"),
                               ("stock_id", "2330"), ("account_code", "9750"),
                               ("account_code", "2XXX")],
                       headers={"X-API-Key": KEY}).json()["rows"]
    assert sorted(f["account_code"] for f in row["facts"]) == ["2XXX", "9750"]
    assert _reports(client, statement="cash_flows").status_code == 400


def test_a_report_query_is_bounded_by_its_facts(client, db, fetch_id, monkeypatch) -> None:
    _report(db, fetch_id, PUBLISHED, [("balance_sheet", "1XXX", "1"), ("balance_sheet", "2XXX", "2")])
    monkeypatch.setattr(api, "MAX_FACTS", 1)
    response = _reports(client)
    assert response.status_code == 400
    assert "account_code" in response.json()["detail"]
    assert _reports(client, account_code="1XXX").status_code == 200


def test_fact_filters_belong_to_financial_reports_only(client) -> None:
    response = _get(client, "/v1/datasets/daily-prices", start=DAY, end=DAY, statement="x")
    assert response.status_code == 400


# ---------------------------------------------------------------- stored derived


def _derived(db, table, day=DAY, *, stock_id="2330", source="twse_mi_index", **values):
    period = "snapshot_date" if "snapshot_date" in table.c else "trade_date"
    db.execute(sa.insert(table).values(stock_id=stock_id, source=source, computed_at=COMPUTED,
                                       **{period: day}, **values))


def _price(db, fetch_id, day, close, recorded_at, *, source="twse_mi_index"):
    db.execute(sa.insert(daily_prices).values(
        stock_id="2330", source=source, trade_date=day, close_price=Decimal(close), volume=10,
        fetch_id=fetch_id, recorded_at=recorded_at))


def _served(client, name, day=DAY, **params) -> list[dict]:
    response = _get(client, f"/v1/datasets/{name}", start=day, end=day, stock_id="2330",
                    **params)
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def test_a_derived_row_waits_for_its_own_release(client, db, fetch_id) -> None:
    _derived(db, technical_indicators, ma5=101.5)
    released = xd.available_from(DAY)
    assert _served(client, "technical-indicators",
                   information_as_of=(released - timedelta(seconds=1)).isoformat()) == []
    body = _get(client, "/v1/datasets/technical-indicators", start=DAY, end=DAY,
                stock_id="2330", information_as_of=released.isoformat()).json()
    [row] = body["rows"]
    assert row["ma5"] == 101.5
    assert row["available_at"] == released.isoformat()
    assert row["computed_at"] == COMPUTED.isoformat()
    assert body["inputs"] == "latest"
    assert body["derivation"]["dataset_code"] == "technical_indicators"


def test_a_later_correction_of_an_earlier_input_delays_every_row_after_it(client, db,
                                                                          fetch_id) -> None:
    # The stored row for DAY was computed from the corrected PREVIOUS close, which
    # was not public until the correction was recorded; an exponential metric reads
    # the whole series, so every later row waits for it.
    settled = xd.available_from(PREVIOUS)
    corrected = xd.available_from(DAY) + timedelta(days=3)
    _price(db, fetch_id, PREVIOUS, "100", settled)
    _price(db, fetch_id, PREVIOUS, "101", corrected)
    _price(db, fetch_id, DAY, "102", xd.available_from(DAY))
    earlier = PREVIOUS - timedelta(days=4)
    for day in (earlier, PREVIOUS, DAY):
        _derived(db, technical_indicators, day, ma5=1.0)

    at = (corrected - timedelta(seconds=1)).isoformat()
    assert [r["trade_date"] for r in _served(client, "technical-indicators", PREVIOUS,
                                             information_as_of=at)] == []
    assert _served(client, "technical-indicators", information_as_of=at) == []
    [row] = _served(client, "technical-indicators", earlier, information_as_of=at)
    assert row["available_at"] == xd.available_from(earlier).isoformat()
    [row] = _served(client, "technical-indicators", information_as_of=corrected.isoformat())
    assert row["available_at"] == corrected.isoformat()


def test_a_correction_of_a_same_day_input_delays_only_that_day(client, db, fetch_id) -> None:
    # A margin metric reads its own day's row only.
    for day, values in ((PREVIOUS, (10, 11)), (DAY, (12,))):
        for i, balance in enumerate(values):
            db.execute(sa.insert(margin_trading).values(
                stock_id="2330", source="twse_mi_margn", trade_date=day, margin_balance=balance,
                fetch_id=fetch_id,
                recorded_at=xd.available_from(day) + timedelta(days=5 * i)))
        _derived(db, margin_metrics, day, source="twse_mi_margn", margin_usage_ratio=1.0)
    at = (xd.available_from(DAY) + timedelta(days=1)).isoformat()
    assert _served(client, "margin-metrics", PREVIOUS, information_as_of=at) == []
    assert len(_served(client, "margin-metrics", information_as_of=at)) == 1


def test_a_streak_waits_for_a_correction_of_the_price_days_it_counts_over(client, db,
                                                                         fetch_id) -> None:
    # twse_t86 streaks count over twse_mi_index trading days: another source's input.
    corrected = xd.available_from(DAY) + timedelta(days=2)
    _price(db, fetch_id, PREVIOUS, "100", xd.available_from(PREVIOUS))
    _price(db, fetch_id, PREVIOUS, "101", corrected)
    _derived(db, institutional_streaks, source="twse_t86", foreign_streak_days=1,
             trust_streak_days=0, dealer_streak_days=-1)
    [row] = _served(client, "institutional-streaks", information_as_of="latest")
    assert row["available_at"] == corrected.isoformat()


def test_valuation_waits_for_a_restatement_of_a_report_public_by_then(client, db,
                                                                      fetch_id) -> None:
    public = datetime(2024, 5, 10, 6, tzinfo=UTC)
    restated = xd.available_from(DAY) + timedelta(days=7)
    _report(db, fetch_id, public, [("income_statement", "9750", "1")], published_at=public,
            quarter=1)
    _report(db, fetch_id, restated, [("income_statement", "9750", "2")], published_at=public,
            quarter=1)
    # A report first public after DAY is not an input of DAY, whatever happens to it.
    later = datetime(2024, 8, 10, tzinfo=UTC)
    _report(db, fetch_id, later, [("income_statement", "9750", "1")], published_at=later)
    _report(db, fetch_id, later + timedelta(days=90), [("income_statement", "9750", "3")],
            published_at=later)
    _derived(db, valuation_metrics, pe_ratio=12.5)
    [row] = _served(client, "valuation-metrics", information_as_of="latest")
    assert row["available_at"] == restated.isoformat()


def test_concentration_waits_for_the_tdcc_release(client, db, fetch_id) -> None:
    snapshot = date(2024, 7, 5)  # a Friday
    _derived(db, shareholding_concentration, snapshot, source="tdcc_opendata",
             small_holder_ratio=1.0)
    [row] = _served(client, "shareholding-concentrations", snapshot)
    assert row["available_at"] == tdcc_available_from(snapshot).isoformat()


def test_a_derived_table_refuses_a_historical_knowledge_or_system_cutoff(client, db,
                                                                         fetch_id) -> None:
    # The tables follow the latest inputs and are overwritten: they cannot say
    # what the Data Center knew before (owner, 2026-09-25).
    _derived(db, technical_indicators, ma5=1.0)
    past = "2025-01-01T00:00:00+08:00"
    for params in ({"knowledge_as_of": past}, {"system_as_of": past}):
        response = _get(client, "/v1/datasets/technical-indicators", start=DAY, end=DAY,
                        stock_id="2330", **params)
        assert response.status_code == 400, params
        assert "latest" in response.json()["detail"]
    assert len(_served(client, "technical-indicators", system_as_of="latest")) == 1
    assert len(_served(client, "technical-indicators", knowledge_as_of="now")) == 1


# ---------------------------------------------------------------- on-demand PIT reference


def _series(db, fetch_id, days=8):
    first = date(2024, 6, 3)
    out = []
    for i in range(days):
        day = first + timedelta(days=i)
        _price(db, fetch_id, day, str(100 + i), xd.available_from(day))
        out.append(day)
    return out


def test_the_pit_reference_answers_what_compute_answers(client, db, fetch_id) -> None:
    days = _series(db, fetch_id)
    at = xd.available_from(days[5])
    body = _get(client, "/v1/datasets/technical-indicators-pit", stock_id="2330",
                start=days[0], end=days[-1], information_as_of=at.isoformat(),
                knowledge_as_of=at.isoformat()).json()
    expected = TechnicalIndicators(git_commit="x").compute(
        db, stock_id="2330", start_date=days[0], end_date=days[-1], information_as_of=at,
        knowledge_as_of=at)
    assert [r["trade_date"] for r in body["rows"]] == \
        [e.observation_date.isoformat() for e in expected]
    assert len(body["rows"]) == 6
    for got, want in zip(body["rows"], expected, strict=True):
        assert got["ma5"] == want.metrics["ma5"]
        assert got["input_fingerprint"] == want.input_fingerprint
        assert got["information_as_of"] == at.isoformat()
    assert body["derivation"]["git_commit"]
    assert body["view"] == "as_of"


def test_the_rolling_view_computes_each_date_at_its_own_release(client, db, fetch_id) -> None:
    days = _series(db, fetch_id)
    body = _get(client, "/v1/datasets/technical-indicators-pit", stock_id="2330",
                start=days[0], end=days[-1], view="rolling").json()
    assert [r["information_as_of"] for r in body["rows"]] == \
        [xd.available_from(day).isoformat() for day in days]


def test_the_pit_reference_refuses_what_it_cannot_answer(client, db, fetch_id) -> None:
    days = _series(db, fetch_id, 2)
    path = "/v1/datasets/technical-indicators-pit"
    base = {"start": days[0], "end": days[-1]}
    assert _get(client, path, **base).status_code == 400  # one stock at a time
    assert _get(client, path, **base, stock_id="9999").status_code == 400  # not on the list
    assert _get(client, path, **base, stock_id="2330", view="rolling",
                information_as_of="latest").status_code == 400
    assert _get(client, path, **base, stock_id="2330", system_as_of="latest").status_code == 400
    assert _get(client, path, **base, stock_id="2330", view="weekly").status_code == 400


# ---------------------------------------------------------------- reference data


def test_the_stock_list_is_todays_universe_with_provenance(client, db, fetch_id) -> None:
    body = _get(client, "/v1/stocks").json()
    rows = {r["stock_id"]: r for r in body["rows"]}
    assert set(rows) >= {"2330", "6488"}
    assert rows["6488"]["market"] == "otc"
    assert rows["2330"]["provenance"]["raw_sha256"]
    assert "ADR-0026" in body["universe"]
    assert [r["stock_id"] for r in _get(client, "/v1/stocks", market="otc").json()["rows"]
            if r["stock_id"] in rows] == ["6488"]
    assert _get(client, "/v1/stocks", information_as_of="latest").status_code == 400
    assert _get(client, "/v1/stocks", market="rotc").status_code == 400


def test_the_trading_calendar(client, db, fetch_id) -> None:
    for day in (PREVIOUS, DAY):
        db.execute(postgresql.insert(trading_days).values(trade_date=day, fetch_id=fetch_id)
                   .on_conflict_do_nothing())
    rows = _get(client, "/v1/trading-days", start=PREVIOUS, end=DAY).json()["rows"]
    assert [r["trade_date"] for r in rows] == [PREVIOUS.isoformat(), DAY.isoformat()]
    assert rows[0]["provenance"]["fetch_id"]
    assert _get(client, "/v1/trading-days", start=PREVIOUS).status_code == 400
