"""Step 20-d — the foreign-holding adapters, against captured bytes.

Every fixture is a live response from 2026-09-19, fetched with the request the
adapter builds. `*_20260913_closed.*` is a Sunday.

- TWSE `fund/MI_QFIIS` is JSON. Its two share ratios arrive as JSON numbers,
  not strings, so they must be read as exact decimals. Its change reason is
  sometimes wrapped in a link whose URL changes month to month; only the code
  is the source's statement.
- TPEx comes from MOPS `t13sa150_otc`, a POST of the legacy scraper's form that
  answers MS950 (cp950) HTML. The page is not strict big5: 11 characters in
  security names decode only as cp950. Names are not stored.

Expected values are legacy `stock_db.foreign_holding` rows for the same
security and date, which Step 20-d reconciles in full.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from stock_data_center.ingestion.adapters import (
    MOPSForeignHoldingAdapter,
    TPExInstiQfiiForeignHoldingAdapter,
    TWSEForeignHoldingAdapter,
)
from stock_data_center.ingestion.http import process_governor
from stock_data_center.ingestion.models import ForeignHoldingRequest, SourceDataError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_mi_qfiis_20260911.json").read_bytes()
TWSE_2020 = (FIXTURES / "twse_mi_qfiis_20200102.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_mi_qfiis_20260913_closed.json").read_bytes()
MOPS = (FIXTURES / "mops_t13sa150_otc_20260911.html").read_bytes()
MOPS_2020 = (FIXTURES / "mops_t13sa150_otc_20200102.html").read_bytes()
MOPS_CLOSED = (FIXTURES / "mops_t13sa150_otc_20260913_closed.html").read_bytes()
QFII = (FIXTURES / "tpex_insti_qfii_20260911.json").read_bytes()
QFII_2020 = (FIXTURES / "tpex_insti_qfii_20200102.json").read_bytes()
QFII_CLOSED = (FIXTURES / "tpex_insti_qfii_20260913_closed.json").read_bytes()

DAY = date(2026, 9, 11)
FIRST = date(2020, 1, 2)
SUNDAY = date(2026, 9, 13)


def twse(content: bytes = TWSE, day: date = DAY):
    return TWSEForeignHoldingAdapter().parse(content, ForeignHoldingRequest(day))


def mops(content: bytes = MOPS, day: date = DAY):
    return MOPSForeignHoldingAdapter().parse(content, ForeignHoldingRequest(day))


def mutate(raw: bytes, edit) -> bytes:
    payload = json.loads(raw, parse_float=Decimal)
    edit(payload)
    return json.dumps(payload, ensure_ascii=False, default=float).encode("utf-8")


def one(parsed, code: str):
    matches = [row for row in parsed.rows if row.security_code == code]
    assert len(matches) == 1, f"{code} appears {len(matches)} times"
    return matches[0].observation


def values(observation) -> dict[str, object]:
    return {
        "issued": int(observation.issued_shares.value),
        "investable": int(observation.investable_shares.value),
        "held": int(observation.held_shares.value),
        "investable_ratio": observation.investable_ratio,
        "held_ratio": observation.held_ratio,
        "foreign_limit": observation.foreign_legal_limit_ratio,
        "mainland_limit": observation.mainland_legal_limit_ratio,
        "reason": observation.change_reason,
        "last_update": observation.source_last_update_date,
    }


# ---- identity and requests -------------------------------------------------


def test_the_foreign_holding_sources_are_their_own() -> None:
    assert TWSEForeignHoldingAdapter.dataset_code == "foreign_holding"
    assert MOPSForeignHoldingAdapter.dataset_code == "foreign_holding"
    assert TWSEForeignHoldingAdapter.source == "twse_mi_qfiis"
    assert MOPSForeignHoldingAdapter.source == "mops_t13sa150_otc"
    assert TWSEForeignHoldingAdapter.market == "TWSE"
    assert MOPSForeignHoldingAdapter.market == "TPEx"


def test_twse_requests_every_security_but_warrants_as_json() -> None:
    resource = TWSEForeignHoldingAdapter().resource(ForeignHoldingRequest(DAY))
    url = urlsplit(resource.source_uri)
    assert resource.method == "GET"
    assert url.netloc == "www.twse.com.tw"
    assert url.path == "/rwd/zh/fund/MI_QFIIS"
    assert parse_qs(url.query) == {
        "date": ["20260911"], "selectType": ["ALLBUT0999"], "response": ["json"],
    }
    assert resource.resource_key == "twse_mi_qfiis:foreign_holding:2026-09-11"


def test_mops_posts_the_legacy_form_for_the_date() -> None:
    resource = MOPSForeignHoldingAdapter().resource(ForeignHoldingRequest(DAY))
    assert resource.method == "POST"
    assert resource.source_uri == "https://mopsov.twse.com.tw/server-java/t13sa150_otc"
    assert resource.body == b"step=2&years=2026&months=09&days=11&bcode="
    assert dict(resource.headers)["content-type"] == "application/x-www-form-urlencoded"
    assert dict(resource.headers)["accept"] == "text/html"
    assert resource.resource_key == "mops_t13sa150_otc:foreign_holding:2026-09-11"


def test_every_mops_request_is_paced_by_the_host_budget() -> None:
    resource = MOPSForeignHoldingAdapter().resource(ForeignHoldingRequest(DAY))
    assert process_governor().interval_for(resource.source_uri) == 3.0


# ---- TWSE ------------------------------------------------------------------


def test_twse_reads_every_published_row() -> None:
    parsed = twse()
    assert parsed.market == "TWSE"
    assert parsed.trade_date == DAY
    assert len(parsed.rows) == 1362
    assert len(twse(TWSE_2020, FIRST).rows) == 1099


def test_twse_2330_equals_the_legacy_row() -> None:
    assert values(one(twse(), "2330")) == {
        "issued": 25_932_370_067,
        "investable": 7_967_436_135,
        "held": 17_964_933_932,
        "investable_ratio": Decimal("30.72"),
        "held_ratio": Decimal("69.27"),
        "foreign_limit": Decimal("100.00"),
        "mainland_limit": Decimal("100.00"),
        "reason": None,
        "last_update": date(2026, 5, 26),
    }
    first = one(twse(TWSE_2020, FIRST), "2330")
    assert int(first.issued_shares.value) == 25_930_380_458
    assert int(first.held_shares.value) == 20_318_688_414
    assert first.held_ratio == Decimal("78.35")


def test_twse_a_json_number_ratio_is_read_exactly() -> None:
    # 0051 publishes 0.3, a JSON number; a float would store 0.29999…
    observation = one(twse(TWSE_2020, FIRST), "0051")
    assert observation.held_ratio == Decimal("0.3")
    assert observation.investable_ratio == Decimal("99.69")


def test_twse_a_linked_change_reason_stores_its_code_only() -> None:
    # 2317 carries `<a href='…t98sb03…&date=11509'>4</a>`; the link moves
    # every month, the code is the statement.
    assert one(twse(), "2317").change_reason == "4"


def test_twse_an_unknown_change_reason_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: p["data"][0].__setitem__(10, "9"))
    with pytest.raises(SourceDataError) as error:
        twse(raw)
    assert error.value.reason_code == "unrecognised_value"


def test_twse_a_closed_day_is_no_data() -> None:
    with pytest.raises(SourceDataError) as error:
        twse(TWSE_CLOSED, SUNDAY)
    assert error.value.reason_code == "no_data_for_date"


def test_twse_answering_for_another_date_fails() -> None:
    with pytest.raises(SourceDataError) as error:
        twse(TWSE, date(2026, 9, 10))
    assert error.value.reason_code == "date_mismatch"


def test_twse_an_unknown_header_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: p["fields"].append("新欄位"))
    with pytest.raises(SourceDataError) as error:
        twse(raw)
    assert error.value.reason_code == "schema_mismatch"


def test_twse_a_changed_unit_declaration_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: p.__setitem__("hints", "單位:千股"))
    with pytest.raises(SourceDataError) as error:
        twse(raw)
    assert error.value.reason_code == "unit_declaration_changed"


def test_twse_a_declared_total_that_disagrees_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: p.__setitem__("total", 5))
    with pytest.raises(SourceDataError) as error:
        twse(raw)
    assert error.value.reason_code == "schema_mismatch"


def test_twse_the_same_security_twice_fails_the_file() -> None:
    def duplicate(payload):
        payload["data"].append(list(payload["data"][0]))
        payload["total"] += 1

    with pytest.raises(SourceDataError) as error:
        twse(mutate(TWSE, duplicate))
    assert error.value.reason_code == "duplicate_security"


def test_twse_an_unreadable_share_count_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: p["data"][0].__setitem__(3, "--"))
    with pytest.raises(SourceDataError) as error:
        twse(raw)
    assert error.value.reason_code == "unrecognised_value"


def test_twse_a_ratio_above_one_hundred_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: p["data"][0].__setitem__(7, Decimal("100.01")))
    with pytest.raises(SourceDataError) as error:
        twse(raw)
    assert error.value.reason_code == "invalid_numeric"


# ---- MOPS (TPEx) -----------------------------------------------------------


def test_mops_reads_every_published_row() -> None:
    parsed = mops()
    assert parsed.market == "TPEx"
    assert parsed.trade_date == DAY
    assert len(parsed.rows) == 1010
    assert len(mops(MOPS_2020, FIRST).rows) == 821


def test_mops_5009_equals_the_legacy_row() -> None:
    assert values(one(mops(), "5009")) == {
        "issued": 602_471_197,
        "investable": 514_886_682,
        "held": 87_584_515,
        "investable_ratio": Decimal("85.46"),
        "held_ratio": Decimal("14.53"),
        "foreign_limit": Decimal("100.00"),
        "mainland_limit": Decimal("100.00"),
        "reason": None,
        "last_update": date(2026, 4, 21),
    }
    first = one(mops(MOPS_2020, FIRST), "5009")
    assert int(first.issued_shares.value) == 466_735_991
    assert int(first.investable_shares.value) == 420_447_419
    assert first.held_ratio == Decimal("9.91")


def test_mops_a_published_change_reason_is_stored() -> None:
    assert one(mops(), "4760").change_reason == "2"


def test_mops_a_blank_last_update_date_is_null() -> None:
    assert one(mops(), "7839").source_last_update_date is None


def test_mops_decodes_as_cp950_not_strict_big5() -> None:
    with pytest.raises(UnicodeDecodeError):
        MOPS.decode("big5")
    assert len(mops().rows) == 1010


def test_mops_a_closed_day_is_no_data() -> None:
    with pytest.raises(SourceDataError) as error:
        mops(MOPS_CLOSED, SUNDAY)
    assert error.value.reason_code == "no_data_for_date"


def test_mops_a_table_for_another_date_fails() -> None:
    with pytest.raises(SourceDataError) as error:
        mops(MOPS, date(2026, 9, 10))
    assert error.value.reason_code == "date_mismatch"


def test_mops_an_unknown_header_fails_the_file() -> None:
    raw = MOPS.replace("陸資法令投資<br>上限比率".encode("cp950"), "陸資上限".encode("cp950"))
    with pytest.raises(SourceDataError) as error:
        mops(raw)
    assert error.value.reason_code == "schema_mismatch"


def test_mops_a_row_of_the_wrong_width_fails_the_file() -> None:
    marker = "<td align=center><font size=2>115/04/21</td>".encode("cp950")
    assert marker in MOPS
    with pytest.raises(SourceDataError) as error:
        mops(MOPS.replace(marker, b"", 1))
    assert error.value.reason_code == "schema_mismatch"


def test_mops_an_unreadable_value_fails_the_file() -> None:
    raw = MOPS.replace(b"602,471,197", b"602,471,19x", 1)
    with pytest.raises(SourceDataError) as error:
        mops(raw)
    assert error.value.reason_code == "unrecognised_value"


def test_mops_the_same_security_twice_fails_the_file() -> None:
    raw = MOPS.replace(b">5009<", b">4760<", 1)
    with pytest.raises(SourceDataError) as error:
        mops(raw)
    assert error.value.reason_code == "duplicate_security"


def test_mops_a_page_that_is_not_the_table_fails_as_unusable() -> None:
    # A block or maintenance page is not a source answer about the date.
    page = "<html><body>FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED!</body></html>"
    with pytest.raises(SourceDataError) as error:
        mops(page.encode("cp950"))
    assert error.value.reason_code == "unusable_response"


def test_twse_a_zero_last_update_date_means_no_filing_yet() -> None:
    """Found by the backfill: a newly listed security that has not filed yet
    gets the JSON integer 0 in the last-update column (4581 on 2020-03-06,
    00875 on 2020-03-27 and 03-30). MOPS leaves the same cell blank."""
    raw = mutate(TWSE, lambda p: p["data"][0].__setitem__(11, 0))
    code = json.loads(TWSE)["data"][0][0]
    assert one(twse(raw), code).source_last_update_date is None


def test_twse_any_other_non_text_value_still_fails_the_file() -> None:
    for column, value in ((11, 1), (3, 0), (10, 0)):
        raw = mutate(TWSE, lambda p, c=column, v=value: p["data"][0].__setitem__(c, v))
        with pytest.raises(SourceDataError) as error:
            twse(raw)
        assert error.value.reason_code == "schema_mismatch"


def test_twse_the_adapter_version_records_the_zero_date_rule() -> None:
    assert TWSEForeignHoldingAdapter.version == "twse-mi-qfiis:v2"


def test_twse_several_linked_reasons_store_every_code() -> None:
    """Found by the backfill (2303 on 2020-05-15): two links separated by
    <br>. Both markets store the codes ascending, comma-separated."""
    two = (
        " <a href='https://mopsov.twse.com.tw/mops/web/t98sb02' target=_blank>2</a><br>"
        " <a href=' https://mopsov.twse.com.tw/server-java/t98sb03?step=1&TYPEK=sii"
        "&colorchg=1&kind=3&co_id=2303&date=10905' target=_blank>4</a>"
    )
    raw = mutate(TWSE, lambda p: p["data"][0].__setitem__(10, two))
    code = json.loads(TWSE)["data"][0][0]
    assert one(twse(raw), code).change_reason == "2,4"


def test_mops_concatenated_reasons_store_every_code() -> None:
    # 5483 on 2020-04-06 published `24`; every code is a single digit. MOPS
    # wraps the codes in a link, as TWSE does.
    cell = b"target='new_doc'>2</a>"
    assert cell in MOPS
    raw = MOPS.replace(cell, b"target='new_doc'>24</a>", 1)
    parsed = mops(raw)
    assert "2,4" in {row.observation.change_reason for row in parsed.rows}


@pytest.mark.parametrize("published", ["29", "22", "2 x"])
def test_mops_a_reason_that_is_not_distinct_known_codes_fails(published: str) -> None:
    cell = b"target='new_doc'>2</a>"
    assert cell in MOPS
    raw = MOPS.replace(cell, f"target='new_doc'>{published}</a>".encode("cp950"), 1)
    with pytest.raises(SourceDataError) as error:
        mops(raw)
    assert error.value.reason_code == "unrecognised_value"


def test_mops_the_adapter_version_records_the_multi_code_rule() -> None:
    assert MOPSForeignHoldingAdapter.version == "mops-t13sa150-otc:v2"



# ---- TPEx insti/qfii -------------------------------------------------------
#
# MOPS rebuilds a past date from today's security list: 5371, 4130, 3426 and
# 4987 (delisted 2026-05..08) and 5236 (moved to TWSE 2026-07-15) are gone from
# every MOPS date back to 2020, though legacy's February 2026 files hold them.
# TPEx's own `insti/qfii` still lists them, so it is the second TPEx source.


def qfii(content: bytes = QFII, day: date = DAY):
    return TPExInstiQfiiForeignHoldingAdapter().parse(content, ForeignHoldingRequest(day))


def edit_qfii(raw: bytes, edit) -> bytes:
    payload = json.loads(raw)
    edit(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def test_qfii_is_its_own_tpex_source() -> None:
    adapter = TPExInstiQfiiForeignHoldingAdapter
    assert adapter.dataset_code == "foreign_holding"
    assert adapter.source == "tpex_insti_qfii"
    assert adapter.market == "TPEx"


def test_qfii_requests_the_date_as_json() -> None:
    resource = TPExInstiQfiiForeignHoldingAdapter().resource(ForeignHoldingRequest(DAY))
    url = urlsplit(resource.source_uri)
    assert resource.method == "GET"
    assert (url.netloc, url.path) == ("www.tpex.org.tw", "/www/zh-tw/insti/qfii")
    assert parse_qs(url.query) == {"date": ["2026/09/11"], "response": ["json"]}
    assert resource.resource_key == "tpex_insti_qfii:foreign_holding:2026-09-11"


def test_qfii_still_lists_securities_mops_has_dropped() -> None:
    parsed = qfii(QFII_2020, FIRST)
    assert len(parsed.rows) == 778
    codes = {row.security_code for row in parsed.rows}
    assert {"5371", "4130", "3426", "4987"} <= codes
    assert not {"5371", "4130", "3426", "4987"} & {
        row.security_code for row in mops(MOPS_2020, FIRST).rows
    }


def test_qfii_5371_equals_the_legacy_row_except_the_rounded_ratio() -> None:
    # Legacy saved MOPS, which truncates D (56.80); insti/qfii rounds it.
    assert values(one(qfii(QFII_2020, FIRST), "5371")) == {
        "issued": 434_423_110,
        "investable": 246_785_122,
        "held": 187_637_988,
        "investable_ratio": Decimal("56.81"),
        "held_ratio": Decimal("43.19"),
        "foreign_limit": Decimal(100),
        "mainland_limit": None,
        "reason": None,
        "last_update": None,
    }


def test_qfii_reads_every_published_row() -> None:
    assert len(qfii().rows) == 892
    assert "8349A" in {row.security_code for row in qfii().rows}


def test_qfii_a_prohibition_note_is_not_a_change_reason() -> None:
    # 3086 carries 禁止投資 with B = 0; the note has no contract column.
    observation = one(qfii(QFII_2020, FIRST), "3086")
    assert observation.change_reason is None
    assert int(observation.investable_shares.value) == 0


def test_qfii_an_unknown_note_fails_the_file() -> None:
    raw = edit_qfii(QFII, lambda p: p["tables"][0]["data"][0].__setitem__(9, "新註記"))
    with pytest.raises(SourceDataError) as error:
        qfii(raw)
    assert error.value.reason_code == "unrecognised_value"


def test_qfii_a_ratio_without_its_percent_sign_fails_the_file() -> None:
    raw = edit_qfii(QFII, lambda p: p["tables"][0]["data"][0].__setitem__(6, "12.15"))
    with pytest.raises(SourceDataError) as error:
        qfii(raw)
    assert error.value.reason_code == "unrecognised_value"


def test_qfii_a_closed_day_is_no_data() -> None:
    with pytest.raises(SourceDataError) as error:
        qfii(QFII_CLOSED, SUNDAY)
    assert error.value.reason_code == "no_data_for_date"


def test_qfii_answering_for_another_date_fails() -> None:
    with pytest.raises(SourceDataError) as error:
        qfii(QFII, date(2026, 9, 10))
    assert error.value.reason_code == "date_mismatch"


def test_qfii_an_unknown_header_fails_the_file() -> None:
    raw = edit_qfii(QFII, lambda p: p["tables"][0]["fields"].append("新欄位"))
    with pytest.raises(SourceDataError) as error:
        qfii(raw)
    assert error.value.reason_code == "schema_mismatch"


def test_qfii_a_declared_total_that_disagrees_fails_the_file() -> None:
    raw = edit_qfii(QFII, lambda p: p["tables"][0].__setitem__("totalCount", 5))
    with pytest.raises(SourceDataError) as error:
        qfii(raw)
    assert error.value.reason_code == "schema_mismatch"


def test_qfii_a_limit_reached_note_is_accepted_and_not_stored() -> None:
    """Found by the backfill: 6497 carried 已達上限 from 2020-05-04 to
    2020-08-24. Like 禁止投資 it is a flag with no contract column."""
    raw = edit_qfii(QFII, lambda p: p["tables"][0]["data"][0].__setitem__(9, "已達上限"))
    code = json.loads(QFII)["tables"][0]["data"][0][1]
    assert one(qfii(raw), code).change_reason is None


def test_qfii_the_adapter_version_records_the_limit_reached_note() -> None:
    assert TPExInstiQfiiForeignHoldingAdapter.version == "tpex-insti-qfii:v2"
