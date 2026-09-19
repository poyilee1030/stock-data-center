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
