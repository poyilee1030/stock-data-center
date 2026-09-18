"""Step 20-b — the institutional market-summary adapters, against captured bytes.

Every fixture is a live response from 2026-09-18, fetched with the URL the
adapter builds. `twse_bfi82u_20260913_closed.json` is a Sunday.
`tpex_insti_summary_20260710_closed.json` is the date whose legacy archive file
is broken (audit §4.3): 2026-07-10 was a typhoon closure, and TPEx answers it
today exactly as it answers a Sunday, with an empty table.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from stock_data_center.ingestion.adapters import (
    TPExInstitutionalMarketSummaryAdapter,
    TWSEInstitutionalMarketSummaryAdapter,
)
from stock_data_center.ingestion.models import (
    InstitutionalMarketSummaryRequest,
    SourceDataError,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_bfi82u_20260911.json").read_bytes()
TWSE_2020 = (FIXTURES / "twse_bfi82u_20200102.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_bfi82u_20260913_closed.json").read_bytes()
TPEX = (FIXTURES / "tpex_insti_summary_20260911.json").read_bytes()
TPEX_2020 = (FIXTURES / "tpex_insti_summary_20200102.json").read_bytes()
TPEX_TYPHOON = (FIXTURES / "tpex_insti_summary_20260710_closed.json").read_bytes()

DAY = date(2026, 9, 11)
FIRST = date(2020, 1, 2)
SUNDAY = date(2026, 9, 13)
TYPHOON = date(2026, 7, 10)

TWSE_INSTITUTIONS = (
    "自營商(自行買賣)", "自營商(避險)", "投信",
    "外資及陸資(不含外資自營商)", "外資自營商", "合計",
)
TPEX_INSTITUTIONS = (
    "外資及陸資合計", "外資及陸資(不含自營商)", "外資自營商", "投信",
    "自營商合計", "自營商(自行買賣)", "自營商(避險)", "三大法人合計*",
)


def twse(content: bytes = TWSE, day: date = DAY):
    return TWSEInstitutionalMarketSummaryAdapter().parse(
        content, InstitutionalMarketSummaryRequest(day)
    )


def tpex(content: bytes = TPEX, day: date = DAY):
    return TPExInstitutionalMarketSummaryAdapter().parse(
        content, InstitutionalMarketSummaryRequest(day)
    )


def mutate(raw: bytes, edit) -> bytes:
    payload = json.loads(raw)
    edit(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def amounts(parsed, institution: str) -> tuple[int, int, int]:
    matches = [row for row in parsed.rows if row.institution == institution]
    assert len(matches) == 1, f"{institution} appears {len(matches)} times"
    row = matches[0]
    return int(row.buy), int(row.sell), int(row.net)


def reason(call) -> str:
    with pytest.raises(SourceDataError) as caught:
        call()
    return caught.value.reason_code


# --- identity and requests ---------------------------------------------------


def test_the_summary_sources_are_their_own() -> None:
    assert TWSEInstitutionalMarketSummaryAdapter.source == "twse_bfi82u"
    assert TPExInstitutionalMarketSummaryAdapter.source == "tpex_insti_summary"
    for adapter in (
        TWSEInstitutionalMarketSummaryAdapter, TPExInstitutionalMarketSummaryAdapter
    ):
        assert adapter.dataset_code == "institutional_market_summary"


def test_twse_requests_the_daily_report_as_json() -> None:
    resource = TWSEInstitutionalMarketSummaryAdapter().resource(
        InstitutionalMarketSummaryRequest(DAY)
    )
    url = urlsplit(resource.source_uri)
    assert f"{url.scheme}://{url.netloc}{url.path}" == (
        "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
    )
    assert parse_qs(url.query) == {
        "type": ["day"], "dayDate": ["20260911"], "response": ["json"],
    }
    assert resource.resource_key == "twse_bfi82u:institutional_summary:2026-09-11"


def test_tpex_requests_the_daily_summary_as_json() -> None:
    resource = TPExInstitutionalMarketSummaryAdapter().resource(
        InstitutionalMarketSummaryRequest(DAY)
    )
    url = urlsplit(resource.source_uri)
    assert f"{url.scheme}://{url.netloc}{url.path}" == (
        "https://www.tpex.org.tw/www/zh-tw/insti/summary"
    )
    assert parse_qs(url.query) == {"date": ["2026/09/11"], "response": ["json"]}
    assert resource.resource_key == (
        "tpex_insti_summary:institutional_summary:2026-09-11"
    )


# --- TWSE BFI82U ---------------------------------------------------------------


def test_twse_reads_every_published_row_in_order() -> None:
    parsed = twse()
    assert parsed.market == "TWSE"
    assert parsed.trade_date == DAY
    assert parsed.header_variant == "bfi82u_4"
    assert parsed.source_fields == ("單位名稱", "買進金額", "賣出金額", "買賣差額")
    assert tuple(row.institution for row in parsed.rows) == TWSE_INSTITUTIONS
    assert {row.market for row in parsed.rows} == {"TWSE"}
    assert {row.trade_date for row in parsed.rows} == {DAY}


def test_twse_amounts_are_twd_as_published() -> None:
    parsed = twse()
    assert amounts(parsed, "外資及陸資(不含外資自營商)") == (
        247009091715, 336279536641, -89270444926,
    )
    assert amounts(parsed, "合計") == (294671579248, 405930099318, -111258520070)
    assert amounts(parsed, "外資自營商") == (0, 0, 0)
    assert all(isinstance(row.buy, Decimal) for row in parsed.rows)


def test_twse_2020_matches_the_legacy_rows() -> None:
    parsed = twse(TWSE_2020, FIRST)
    assert amounts(parsed, "自營商(自行買賣)") == (2618174510, 1222750424, 1395424086)
    assert amounts(parsed, "外資自營商") == (9852550, 12604610, -2752060)
    assert amounts(parsed, "合計") == (36416925152, 36152522745, 264402407)


def test_twse_a_unit_other_than_yuan_fails_the_file() -> None:
    changed = mutate(TWSE, lambda p: p.update(hints="單位：千元"))
    assert reason(lambda: twse(changed)) == "schema_mismatch"


def test_twse_a_closed_day_is_no_data() -> None:
    assert reason(lambda: twse(TWSE_CLOSED, SUNDAY)) == "no_data_for_date"


def test_twse_answering_for_another_date_fails() -> None:
    assert reason(lambda: twse(TWSE, date(2026, 9, 10))) == "date_mismatch"


def test_twse_an_unknown_header_fails_the_file() -> None:
    changed = mutate(TWSE, lambda p: p["fields"].__setitem__(3, "買賣超"))
    assert reason(lambda: twse(changed)) == "schema_mismatch"


def test_twse_an_unknown_institution_fails_the_file() -> None:
    changed = mutate(TWSE, lambda p: p["data"][4].__setitem__(0, "外資自營商(新)"))
    assert reason(lambda: twse(changed)) == "schema_mismatch"


def test_twse_a_missing_institution_fails_the_file() -> None:
    changed = mutate(TWSE, lambda p: p["data"].pop(4))
    assert reason(lambda: twse(changed)) == "schema_mismatch"


def test_twse_an_unreadable_amount_fails_the_file() -> None:
    changed = mutate(TWSE, lambda p: p["data"][0].__setitem__(1, "--"))
    assert reason(lambda: twse(changed)) == "unrecognised_value"


def test_twse_a_negative_gross_amount_fails_the_file() -> None:
    changed = mutate(TWSE, lambda p: p["data"][0].__setitem__(1, "-1"))
    assert reason(lambda: twse(changed)) == "invalid_numeric"


def test_twse_a_row_of_the_wrong_width_fails_the_file() -> None:
    changed = mutate(TWSE, lambda p: p["data"][0].pop())
    assert reason(lambda: twse(changed)) == "schema_mismatch"


# --- TPEx insti/summary --------------------------------------------------------


def test_tpex_reads_every_published_row_in_order() -> None:
    parsed = tpex()
    assert parsed.market == "TPEx"
    assert parsed.header_variant == "summary_4"
    assert parsed.source_fields == (
        "單位名稱", "買進金額(元)", "賣出金額(元)", "買賣超(元)",
    )
    assert tuple(row.institution for row in parsed.rows) == TPEX_INSTITUTIONS


def test_tpex_the_indentation_is_layout_not_part_of_the_name() -> None:
    # The page indents a subgroup under its total with U+3000; the raw
    # artifact keeps it, the stored name does not.
    raw = [row[0] for row in json.loads(TPEX)["tables"][0]["data"]]
    assert raw[1] == "　外資及陸資(不含自營商)"
    assert all(not row.institution[0].isspace() for row in tpex().rows)


def test_tpex_amounts_are_twd_as_published() -> None:
    parsed = tpex()
    assert amounts(parsed, "外資及陸資(不含自營商)") == (
        52297020380, 58180658502, -5883638122,
    )
    assert amounts(parsed, "自營商合計") == (5806773785, 7144288304, -1337514519)
    assert amounts(parsed, "三大法人合計*") == (61761292148, 69774016189, -8012724041)


def test_tpex_2020_reads_its_first_window_date() -> None:
    parsed = tpex(TPEX_2020, FIRST)
    assert amounts(parsed, "投信") == (764766500, 615048500, 149718000)
    assert amounts(parsed, "三大法人合計*") == (5720449024, 4776403585, 944045439)


def test_tpex_a_closed_day_is_no_data() -> None:
    assert reason(lambda: tpex(TPEX_TYPHOON, TYPHOON)) == "no_data_for_date"


def test_tpex_answering_for_another_date_fails() -> None:
    assert reason(lambda: tpex(TPEX, date(2026, 9, 10))) == "date_mismatch"


def test_tpex_a_table_dated_otherwise_fails() -> None:
    changed = mutate(TPEX, lambda p: p["tables"][0].update(date="115/09/10"))
    assert reason(lambda: tpex(changed)) == "date_mismatch"


def test_tpex_the_status_must_be_ok() -> None:
    changed = mutate(TPEX, lambda p: p.update(stat="參數輸入錯誤"))
    assert reason(lambda: tpex(changed)) == "source_status"


def test_tpex_a_second_table_fails_the_file() -> None:
    changed = mutate(TPEX, lambda p: p["tables"].append(p["tables"][0]))
    assert reason(lambda: tpex(changed)) == "schema_mismatch"


def test_tpex_an_unknown_header_fails_the_file() -> None:
    changed = mutate(TPEX, lambda p: p["tables"][0]["fields"].__setitem__(1, "買進金額"))
    assert reason(lambda: tpex(changed)) == "schema_mismatch"


def test_tpex_an_unknown_institution_fails_the_file() -> None:
    changed = mutate(
        TPEX, lambda p: p["tables"][0]["data"][7].__setitem__(0, "三大法人合計")
    )
    assert reason(lambda: tpex(changed)) == "schema_mismatch"


def test_tpex_reordered_rows_fail_the_file() -> None:
    def swap(payload) -> None:
        data = payload["tables"][0]["data"]
        data[0], data[3] = data[3], data[0]

    assert reason(lambda: tpex(mutate(TPEX, swap))) == "schema_mismatch"


def test_tpex_a_negative_gross_amount_fails_the_file() -> None:
    changed = mutate(TPEX, lambda p: p["tables"][0]["data"][3].__setitem__(2, "-5"))
    assert reason(lambda: tpex(changed)) == "invalid_numeric"


def test_tpex_a_decimal_amount_fails_the_file() -> None:
    changed = mutate(TPEX, lambda p: p["tables"][0]["data"][3].__setitem__(1, "1.5"))
    assert reason(lambda: tpex(changed)) == "unrecognised_value"
