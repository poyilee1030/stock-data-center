"""Step 18-c — the official-valuation adapters, against captured bytes.

Every fixture is a live response from 2026-09-17, fetched with the URL the
adapter builds. `twse_bwibbu_d_20170103.json` is the real 5-field variant: TWSE
still serves it for older dates, and it is the header the legacy archive holds
for 2025-06-24 (audit §4.6). `tpex_peqrydate_20241204.json` carries 6720's
first-day `"0"` ratios.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.ingestion.adapters import (
    TPExOfficialValuationAdapter,
    TWSEOfficialValuationAdapter,
)
from stock_data_center.ingestion.models import (
    OfficialValuationRequest,
    SourceDataError,
)
from stock_data_center.market_reference.models import TwdAmount

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_bwibbu_d_20260911.json").read_bytes()
TWSE_FIVE = (FIXTURES / "twse_bwibbu_d_20170103.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_bwibbu_d_20260913_closed.json").read_bytes()
TPEX = (FIXTURES / "tpex_peqrydate_20260911.json").read_bytes()
TPEX_2020 = (FIXTURES / "tpex_peqrydate_20200102.json").read_bytes()
TPEX_ZERO = (FIXTURES / "tpex_peqrydate_20241204.json").read_bytes()
TPEX_CLOSED = (FIXTURES / "tpex_peqrydate_20260913_closed.json").read_bytes()

DAY = date(2026, 9, 11)


def twse(content: bytes = TWSE, day: date = DAY):
    return TWSEOfficialValuationAdapter().parse(content, OfficialValuationRequest(day))


def tpex(content: bytes = TPEX, day: date = DAY):
    return TPExOfficialValuationAdapter().parse(content, OfficialValuationRequest(day))


def mutate(raw: bytes, edit) -> bytes:
    payload = json.loads(raw)
    edit(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def one(parsed, code: str):
    matches = [row for row in parsed.rows if row.security_code == code]
    assert len(matches) == 1, f"{code} appears {len(matches)} times"
    return matches[0].observation


def reason(call) -> str:
    with pytest.raises(SourceDataError) as caught:
        call()
    return caught.value.reason_code


# --- identity and requests ---------------------------------------------------


def test_the_valuation_sources_are_their_own() -> None:
    assert TWSEOfficialValuationAdapter.source == "twse_bwibbu_d"
    assert TPExOfficialValuationAdapter.source == "tpex_pe_qry_date"
    for adapter in (TWSEOfficialValuationAdapter, TPExOfficialValuationAdapter):
        assert adapter.dataset_code == "official_valuation"


def test_each_adapter_requests_the_whole_market_as_json() -> None:
    request = OfficialValuationRequest(DAY)
    twse_resource = TWSEOfficialValuationAdapter().resource(request)
    assert twse_resource.source_uri == (
        "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
        "?date=20260911&selectType=ALL&response=json"
    )
    tpex_resource = TPExOfficialValuationAdapter().resource(request)
    assert tpex_resource.source_uri == (
        "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate"
        "?date=2026%2F09%2F11&response=json"
    )
    assert twse_resource.resource_key != tpex_resource.resource_key
    assert "2026-09-11" in twse_resource.resource_key


# --- TWSE --------------------------------------------------------------------


def test_twse_parses_every_row_of_the_current_header() -> None:
    parsed = twse()
    assert parsed.market == "TWSE"
    assert parsed.trade_date == DAY
    assert parsed.header_variant == "bwibbu_8"
    assert len(parsed.rows) == 1080
    assert parsed.rejected == ()


def test_twse_values_convert_explicitly() -> None:
    """`股利年度` is ROC, `財報年/季` is `115/2`, and `-` means not computed."""
    tsmc = one(twse(), "2330")
    assert tsmc.pe_ratio == Decimal("27.94")
    assert tsmc.pb_ratio == Decimal("9.72")
    assert tsmc.dividend_yield == Decimal("0.91")
    assert tsmc.dividend_year == 2025
    assert tsmc.report_period == "2026Q2"
    cement = one(twse(), "1101")
    assert cement.pe_ratio is None
    assert cement.dividend_yield == Decimal("3.27")


def test_twse_publishes_no_per_share_dividend() -> None:
    assert all(row.observation.dividend_per_share is None for row in twse().rows)


def test_twse_five_field_variant_is_parsed_explicitly() -> None:
    """The 5-field header TWSE serves for older dates, and the one legacy
    archived for 2025-06-24: no dividend year, no report period."""
    parsed = twse(TWSE_FIVE, date(2017, 1, 3))
    assert parsed.header_variant == "bwibbu_5"
    assert len(parsed.rows) == 891
    cement = one(parsed, "1101")
    assert cement.pe_ratio == Decimal("20.56")
    assert cement.dividend_yield == Decimal("3.78")
    assert cement.pb_ratio == Decimal("1.23")
    assert cement.dividend_year is None
    assert cement.report_period is None


def test_twse_unknown_header_fails_closed() -> None:
    changed = mutate(TWSE, lambda p: p["fields"].__setitem__(5, "本益比(倍)"))
    assert reason(lambda: twse(changed)) == "schema_mismatch"
    shorter = mutate(TWSE, lambda p: p["fields"].pop())
    assert reason(lambda: twse(shorter)) == "schema_mismatch"


def test_twse_closed_date_has_its_own_reason() -> None:
    assert reason(lambda: twse(TWSE_CLOSED, date(2026, 9, 13))) == "no_data_for_date"


def test_twse_answer_for_another_date_fails_closed() -> None:
    assert reason(lambda: twse(TWSE, date(2026, 9, 10))) == "date_mismatch"


def test_twse_row_count_must_match_its_total() -> None:
    wrong = mutate(TWSE, lambda p: p.__setitem__("total", 1079))
    assert reason(lambda: twse(wrong)) == "schema_mismatch"


def test_twse_rejects_the_tpex_report_period_format() -> None:
    other = mutate(TWSE, lambda p: p["data"][0].__setitem__(7, "115Q2"))
    assert reason(lambda: twse(other)) == "unrecognised_value"


def test_twse_unrecognised_placeholder_fails_closed() -> None:
    odd = mutate(TWSE, lambda p: p["data"][0].__setitem__(5, "n.a."))
    assert reason(lambda: twse(odd)) == "unrecognised_value"


def test_twse_non_positive_ratio_rejects_only_that_row() -> None:
    zero = mutate(TWSE, lambda p: p["data"][1].__setitem__(5, "0.00"))
    parsed = twse(zero)
    assert len(parsed.rows) == 1079
    assert [(r.security_code, r.reason_code) for r in parsed.rejected] == [
        ("1102", "nonpositive_ratio")
    ]


def test_twse_repeated_security_fails_closed() -> None:
    doubled = mutate(TWSE, lambda p: (
        p["data"].append(list(p["data"][0])), p.__setitem__("total", 1081)
    ))
    assert reason(lambda: twse(doubled)) == "duplicate_security"


def test_twse_thousands_separator_is_read() -> None:
    wide = mutate(TWSE, lambda p: p["data"][0].__setitem__(5, "1,234.56"))
    assert one(twse(wide), "1101").pe_ratio == Decimal("1234.56")


# --- TPEx --------------------------------------------------------------------


def test_tpex_parses_the_current_header() -> None:
    parsed = tpex()
    assert parsed.market == "TPEx"
    assert parsed.header_variant == "pe_qry_date_8"
    assert len(parsed.rows) == 885
    first = one(parsed, "1240")
    assert first.pe_ratio == Decimal("10.20")
    assert first.dividend_per_share == TwdAmount(Decimal("0.50000000"))
    assert first.dividend_year == 2025
    assert first.dividend_yield == Decimal("0.91")
    assert first.pb_ratio == Decimal("1.62")
    assert first.report_period == "2026Q2"


def test_tpex_before_2025_has_no_report_period_and_that_is_not_an_error() -> None:
    parsed = tpex(TPEX_2020, date(2020, 1, 2))
    assert parsed.header_variant == "pe_qry_date_7"
    assert len(parsed.rows) == 772
    assert all(row.observation.report_period is None for row in parsed.rows)


def test_tpex_na_means_not_computed() -> None:
    ky = one(tpex(TPEX_2020, date(2020, 1, 2)), "1258")
    assert ky.pe_ratio is None
    assert ky.dividend_per_share == TwdAmount(Decimal(0))
    assert ky.pb_ratio == Decimal("1.02")


def test_tpex_first_day_zero_ratios_reject_only_that_row() -> None:
    """6720 久昌, 2024-12-04: `"0"` is neither the documented `N/A` nor a ratio."""
    parsed = tpex(TPEX_ZERO, date(2024, 12, 4))
    assert len(parsed.rows) == 830
    assert [(r.security_code, r.reason_code) for r in parsed.rejected] == [
        ("6720", "nonpositive_ratio")
    ]
    assert "6720" not in {row.security_code for row in parsed.rows}


def test_tpex_rejects_the_twse_report_period_format() -> None:
    other = mutate(TPEX, lambda p: p["tables"][0]["data"][0].__setitem__(7, "115/2"))
    assert reason(lambda: tpex(other)) == "unrecognised_value"


def test_tpex_closed_date_has_its_own_reason() -> None:
    assert reason(lambda: tpex(TPEX_CLOSED, date(2026, 9, 13))) == "no_data_for_date"


def test_tpex_answer_for_another_date_fails_closed() -> None:
    assert reason(lambda: tpex(TPEX, date(2026, 9, 10))) == "date_mismatch"
    table_only = mutate(TPEX, lambda p: p["tables"][0].__setitem__("date", "115/09/10"))
    assert reason(lambda: tpex(table_only)) == "date_mismatch"


def test_tpex_status_other_than_ok_fails_closed() -> None:
    failed = mutate(TPEX, lambda p: p.__setitem__("stat", "error"))
    assert reason(lambda: tpex(failed)) == "source_status"


def test_tpex_row_count_must_match_its_total() -> None:
    wrong = mutate(TPEX, lambda p: p["tables"][0].__setitem__("totalCount", 884))
    assert reason(lambda: tpex(wrong)) == "schema_mismatch"


def test_tpex_unknown_header_fails_closed() -> None:
    changed = mutate(
        TPEX, lambda p: p["tables"][0]["fields"].__setitem__(3, "每股股利(元)")
    )
    assert reason(lambda: tpex(changed)) == "schema_mismatch"


def test_tpex_negative_dividend_rejects_only_that_row() -> None:
    negative = mutate(
        TPEX, lambda p: p["tables"][0]["data"][0].__setitem__(3, "-0.10000000")
    )
    parsed = tpex(negative)
    assert [(r.security_code, r.reason_code) for r in parsed.rejected] == [
        ("1240", "negative_value")
    ]
