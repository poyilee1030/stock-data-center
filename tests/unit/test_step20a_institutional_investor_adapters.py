"""Step 20-a — the per-security institutional-flow adapters, against captured bytes.

Every fixture is a live response from 2026-09-18, fetched with the URL the
adapter builds. `*_20260913_closed.json` is a Sunday. TPEx labels its 21 value
columns with three repeated names only; the group each belongs to is proven by
the `<template id="theads">` of the official page that loads this table
(`major-institutional/detail/day.html`), recorded in audit §4.3.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from stock_data_center.ingestion.adapters import (
    TPExInstitutionalInvestorAdapter,
    TWSEInstitutionalInvestorAdapter,
)
from stock_data_center.ingestion.models import (
    InstitutionalInvestorRequest,
    SourceDataError,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_t86_20260911.json").read_bytes()
TWSE_2020 = (FIXTURES / "twse_t86_20200102.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_t86_20260913_closed.json").read_bytes()
TPEX = (FIXTURES / "tpex_insti_daily_trade_20260911.json").read_bytes()
TPEX_2020 = (FIXTURES / "tpex_insti_daily_trade_20200102.json").read_bytes()
TPEX_CLOSED = (FIXTURES / "tpex_insti_daily_trade_20260913_closed.json").read_bytes()

DAY = date(2026, 9, 11)
FIRST = date(2020, 1, 2)
SUNDAY = date(2026, 9, 13)


def twse(content: bytes = TWSE, day: date = DAY):
    return TWSEInstitutionalInvestorAdapter().parse(
        content, InstitutionalInvestorRequest(day)
    )


def tpex(content: bytes = TPEX, day: date = DAY):
    return TPExInstitutionalInvestorAdapter().parse(
        content, InstitutionalInvestorRequest(day)
    )


def mutate(raw: bytes, edit) -> bytes:
    payload = json.loads(raw)
    edit(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def one(parsed, code: str):
    matches = [row for row in parsed.rows if row.security_code == code]
    assert len(matches) == 1, f"{code} appears {len(matches)} times"
    return matches[0].observation


def shares(observation) -> dict[str, int | None]:
    names = (
        "foreign_buy", "foreign_sell", "foreign_net",
        "foreign_dealer_buy", "foreign_dealer_sell", "foreign_dealer_net",
        "trust_buy", "trust_sell", "trust_net",
        "dealer_self_buy", "dealer_self_sell", "dealer_self_net",
        "dealer_hedge_buy", "dealer_hedge_sell", "dealer_hedge_net",
        "dealer_net", "total_net",
    )
    return {
        name: None if getattr(observation, name) is None
        else int(getattr(observation, name).value)
        for name in names
    }


def reason(call) -> str:
    with pytest.raises(SourceDataError) as caught:
        call()
    return caught.value.reason_code


# --- identity and requests ---------------------------------------------------


def test_the_flow_sources_are_their_own() -> None:
    assert TWSEInstitutionalInvestorAdapter.source == "twse_t86"
    assert TPExInstitutionalInvestorAdapter.source == "tpex_insti_daily_trade"
    for adapter in (TWSEInstitutionalInvestorAdapter, TPExInstitutionalInvestorAdapter):
        assert adapter.dataset_code == "institutional_investor"


def test_twse_requests_every_security_but_warrants_as_json() -> None:
    resource = TWSEInstitutionalInvestorAdapter().resource(
        InstitutionalInvestorRequest(DAY)
    )
    url = urlsplit(resource.source_uri)
    assert f"{url.scheme}://{url.netloc}{url.path}" == (
        "https://www.twse.com.tw/rwd/zh/fund/T86"
    )
    assert parse_qs(url.query) == {
        "date": ["20260911"], "selectType": ["ALLBUT0999"], "response": ["json"],
    }
    assert resource.resource_key == "twse_t86:institutional:2026-09-11"


def test_tpex_requests_the_daily_report_without_warrants_as_json() -> None:
    # `type` is mandatory: without it TPEx answers {"stat": "參數輸入錯誤"}.
    # `sect=EW` is 所有證券(不含權證、牛熊證), the TPEx counterpart of
    # TWSE's ALLBUT0999 and the selector the legacy scraper used.
    resource = TPExInstitutionalInvestorAdapter().resource(
        InstitutionalInvestorRequest(DAY)
    )
    url = urlsplit(resource.source_uri)
    assert f"{url.scheme}://{url.netloc}{url.path}" == (
        "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
    )
    assert parse_qs(url.query) == {
        "date": ["2026/09/11"], "type": ["Daily"], "sect": ["EW"],
        "response": ["json"],
    }
    assert resource.resource_key == "tpex_insti_daily_trade:institutional:2026-09-11"


# --- TWSE T86 ------------------------------------------------------------------


def test_twse_reads_every_published_row() -> None:
    parsed = twse()
    assert parsed.market == "TWSE"
    assert parsed.trade_date == DAY
    assert len(parsed.rows) == 1330
    assert parsed.header_variant == "t86_19"
    assert parsed.source_fields[0] == "證券代號"


def test_twse_maps_each_column_to_its_named_flow_in_shares() -> None:
    assert shares(one(twse(), "2609")) == {
        "foreign_buy": 46546290, "foreign_sell": 23548005, "foreign_net": 22998285,
        "foreign_dealer_buy": 0, "foreign_dealer_sell": 0, "foreign_dealer_net": 0,
        "trust_buy": 105000, "trust_sell": 2894, "trust_net": 102106,
        "dealer_self_buy": 363568, "dealer_self_sell": 350420, "dealer_self_net": 13148,
        "dealer_hedge_buy": 708754, "dealer_hedge_sell": 74591,
        "dealer_hedge_net": 634163,
        "dealer_net": 647311, "total_net": 23747702,
    }


def test_twse_2020_matches_the_legacy_row_for_2337() -> None:
    parsed = twse(TWSE_2020, FIRST)
    assert len(parsed.rows) == 1037
    assert shares(one(parsed, "2337")) == {
        "foreign_buy": 15770000, "foreign_sell": 6896000, "foreign_net": 8874000,
        "foreign_dealer_buy": 0, "foreign_dealer_sell": 0, "foreign_dealer_net": 0,
        "trust_buy": 732000, "trust_sell": 128000, "trust_net": 604000,
        "dealer_self_buy": 2668000, "dealer_self_sell": 1757000,
        "dealer_self_net": 911000,
        "dealer_hedge_buy": 2414000, "dealer_hedge_sell": 388000,
        "dealer_hedge_net": 2026000,
        "dealer_net": 2937000, "total_net": 12415000,
    }


def test_twse_negative_nets_stay_signed() -> None:
    observation = one(twse(), "00632R")
    assert observation.foreign_net.value == Decimal(-69992000)


def test_twse_a_closed_day_is_no_data() -> None:
    assert reason(lambda: twse(TWSE_CLOSED, SUNDAY)) == "no_data_for_date"


def test_twse_answering_for_another_date_fails() -> None:
    assert reason(lambda: twse(TWSE, date(2026, 9, 10))) == "date_mismatch"


def test_twse_an_unknown_header_fails_the_file() -> None:
    changed = mutate(TWSE, lambda p: p["fields"].__setitem__(4, "外陸資買賣超"))
    assert reason(lambda: twse(changed)) == "schema_mismatch"


def test_twse_a_declared_total_that_disagrees_fails_the_file() -> None:
    changed = mutate(TWSE, lambda p: p.__setitem__("total", 1329))
    assert reason(lambda: twse(changed)) == "schema_mismatch"


def test_twse_the_same_security_twice_fails_the_file() -> None:
    changed = mutate(TWSE, lambda p: p["data"].append(list(p["data"][0])))
    changed = mutate(changed, lambda p: p.__setitem__("total", len(p["data"])))
    assert reason(lambda: twse(changed)) == "duplicate_security"


def test_twse_an_unreadable_value_fails_the_file() -> None:
    changed = mutate(TWSE, lambda p: p["data"][0].__setitem__(2, "--"))
    assert reason(lambda: twse(changed)) == "unrecognised_value"


def test_twse_a_negative_gross_quantity_fails_the_file() -> None:
    changed = mutate(TWSE, lambda p: p["data"][0].__setitem__(2, "-1,000"))
    assert reason(lambda: twse(changed)) == "invalid_numeric"


# --- TPEx insti/dailyTrade -------------------------------------------------------


def test_tpex_reads_every_published_row() -> None:
    parsed = tpex()
    assert parsed.market == "TPEx"
    assert len(parsed.rows) == 894
    assert parsed.header_variant == "daily_trade_24"
    assert len(parsed.source_fields) == 24


def test_tpex_maps_each_group_by_position_in_shares() -> None:
    # Groups: 外資及陸資(不含外資自營商), 外資自營商, 外資及陸資 (total, not
    # stored), 投信, 自營商(自行買賣), 自營商(避險), 自營商 (total: only its
    # net is stored), then 三大法人買賣超股數合計.
    assert shares(one(tpex(), "00411A")) == {
        "foreign_buy": 171500, "foreign_sell": 214100, "foreign_net": -42600,
        "foreign_dealer_buy": 0, "foreign_dealer_sell": 0, "foreign_dealer_net": 0,
        "trust_buy": 0, "trust_sell": 0, "trust_net": 0,
        "dealer_self_buy": 0, "dealer_self_sell": 0, "dealer_self_net": 0,
        "dealer_hedge_buy": 6593000, "dealer_hedge_sell": 6773310,
        "dealer_hedge_net": -180310,
        "dealer_net": -180310, "total_net": -222910,
    }


def test_tpex_2020_reads_its_first_window_date() -> None:
    parsed = tpex(TPEX_2020, FIRST)
    assert len(parsed.rows) == 561
    observation = one(parsed, "00679B")
    assert observation.foreign_net.value == Decimal(-29000)
    assert observation.dealer_net.value == Decimal(-633000)
    assert observation.total_net.value == Decimal(-662000)


def test_tpex_a_closed_day_is_no_data() -> None:
    assert reason(lambda: tpex(TPEX_CLOSED, SUNDAY)) == "no_data_for_date"


def test_tpex_answering_for_another_date_fails() -> None:
    assert reason(lambda: tpex(TPEX, date(2026, 9, 10))) == "date_mismatch"


def test_tpex_a_table_dated_otherwise_fails() -> None:
    changed = mutate(TPEX, lambda p: p["tables"][0].__setitem__("date", "115/09/10"))
    assert reason(lambda: tpex(changed)) == "date_mismatch"


def test_tpex_the_status_must_be_ok() -> None:
    assert reason(lambda: tpex(b'{"stat": "\\u53c3\\u6578\\u8f38\\u5165\\u932f\\u8aa4"}')) == (
        "source_status"
    )


def test_tpex_the_sixteen_column_header_is_not_guessed() -> None:
    # The page also carries a 16-column layout (no foreign-dealer group). No
    # window date has used it, so it is a format change, not a variant.
    sixteen = [
        "代號", "名稱", "外資及陸資買股數", "外資及陸資賣股數", "外資及陸資淨買股數",
        "投信買股數", "投信賣股數", "投信淨買股數", "自營商淨買股數",
        "自營商(自行買賣)買股數", "自營商(自行買賣)賣股數", "自營商(自行買賣)淨買股數",
        "自營商(避險)買股數", "自營商(避險)賣股數", "自營商(避險)淨買股數",
        "三大法人買賣超股數",
    ]
    changed = mutate(TPEX, lambda p: p["tables"][0].__setitem__("fields", sixteen))
    assert reason(lambda: tpex(changed)) == "schema_mismatch"


def test_tpex_a_second_table_with_content_fails_the_file() -> None:
    # Every observed response carries the data table plus an empty `{}`.
    changed = mutate(TPEX, lambda p: p["tables"].__setitem__(1, {"data": [["x"]]}))
    assert reason(lambda: tpex(changed)) == "schema_mismatch"


def test_tpex_a_declared_total_that_disagrees_fails_the_file() -> None:
    changed = mutate(TPEX, lambda p: p["tables"][0].__setitem__("totalCount", 1))
    assert reason(lambda: tpex(changed)) == "schema_mismatch"


def test_tpex_a_row_of_the_wrong_width_fails_the_file() -> None:
    changed = mutate(TPEX, lambda p: p["tables"][0]["data"][0].pop())
    assert reason(lambda: tpex(changed)) == "schema_mismatch"
