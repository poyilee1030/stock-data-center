"""Step 21-b — the securities-lending adapters, against captured bytes.

Every fixture is a live response from 2026-09-19, fetched with the URL the
adapter builds. `*_20260913_closed.json` is a Sunday.

- TWSE `rwd/zh/marginTrading/TWT93U` — 信用額度總量管制餘額表. `hints` reads
  `單位：股`. 15 fields in two groups whose labels repeat (前日餘額), so columns
  are read by position: 融券 (前日餘額, 賣出, 買進, 現券, 今日餘額,
  次一營業日限額), then 借券賣出 (前日餘額, 當日賣出, 當日還券, 當日調整,
  當日餘額, 次一營業日可限額), then 備註. The last row is `合計`, with no code.
- TPEx `www/zh-tw/margin/sbl`, the JSON of the legacy `margin_sbl` page, the
  same 15 fields. The JSON states no unit; the page that renders it does
  (`subtitle2:"單位：股"`), and its 融券 columns equal the margin table's
  lots × 1,000 on every row of 2020-01-02.

Both are in shares, so nothing is converted, 008201 included (its lot of 100
matters only to lot-denominated tables). Expected values are legacy
`stock_db.margin_sbl` rows, which are also in shares.

Stored: the 借券賣出 group, plus the 融券 group's 次一營業日限額 / 限額 as
`next_limit` — the share-precise short-sale limit that `margin_trading` holds
only rounded down to lots. The 融券 flows repeat `margin_trading` and are not
stored; the reconciliation checks they agree.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from stock_data_center.ingestion.adapters import (
    TPExSecuritiesLendingAdapter,
    TWSESecuritiesLendingAdapter,
)
from stock_data_center.ingestion.models import SecuritiesLendingRequest, SourceDataError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_twt93u_20260911.json").read_bytes()
TWSE_2020 = (FIXTURES / "twse_twt93u_20200102.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_twt93u_20260913_closed.json").read_bytes()
TPEX = (FIXTURES / "tpex_margin_sbl_20260911.json").read_bytes()
TPEX_2020 = (FIXTURES / "tpex_margin_sbl_20200102.json").read_bytes()
TPEX_CLOSED = (FIXTURES / "tpex_margin_sbl_20260913_closed.json").read_bytes()

DAY = date(2026, 9, 11)
FIRST = date(2020, 1, 2)
SUNDAY = date(2026, 9, 13)

QUANTITIES = (
    "previous_balance", "borrowed", "returned", "adjustment", "balance",
    "next_limit", "next_available_limit",
)


def twse(content: bytes = TWSE, day: date = DAY):
    return TWSESecuritiesLendingAdapter().parse(content, SecuritiesLendingRequest(day))


def tpex(content: bytes = TPEX, day: date = DAY):
    return TPExSecuritiesLendingAdapter().parse(content, SecuritiesLendingRequest(day))


def mutate(raw: bytes, edit) -> bytes:
    payload = json.loads(raw)
    edit(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def twse_row(payload, code: str) -> list[str]:
    return next(row for row in payload["data"] if row[0] == code)


def one(parsed, code: str):
    matches = [row for row in parsed.rows if row.security_code == code]
    assert len(matches) == 1, f"{code} appears {len(matches)} times"
    return matches[0].observation


def shares(observation) -> dict[str, int]:
    return {name: int(getattr(observation, name).value) for name in QUANTITIES}


def fails(reason: str, parse, content: bytes) -> None:
    with pytest.raises(SourceDataError) as error:
        parse(content)
    assert error.value.reason_code == reason


# ---- identity and requests -------------------------------------------------


def test_the_lending_sources_are_their_own() -> None:
    assert TWSESecuritiesLendingAdapter.dataset_code == "securities_lending"
    assert TPExSecuritiesLendingAdapter.dataset_code == "securities_lending"
    assert TWSESecuritiesLendingAdapter.source == "twse_twt93u"
    assert TPExSecuritiesLendingAdapter.source == "tpex_margin_sbl"
    assert (TWSESecuritiesLendingAdapter.market, TPExSecuritiesLendingAdapter.market) == (
        "TWSE", "TPEx",
    )


def test_twse_requests_the_quota_table_as_json() -> None:
    resource = TWSESecuritiesLendingAdapter().resource(SecuritiesLendingRequest(DAY))
    url = urlsplit(resource.source_uri)
    assert (url.netloc, url.path) == ("www.twse.com.tw", "/rwd/zh/marginTrading/TWT93U")
    assert parse_qs(url.query) == {"date": ["20260911"], "response": ["json"]}
    assert resource.resource_key == "twse_twt93u:securities_lending:2026-09-11"


def test_tpex_requests_the_sbl_table_as_json() -> None:
    resource = TPExSecuritiesLendingAdapter().resource(SecuritiesLendingRequest(DAY))
    url = urlsplit(resource.source_uri)
    assert (url.netloc, url.path) == ("www.tpex.org.tw", "/www/zh-tw/margin/sbl")
    assert parse_qs(url.query) == {"date": ["2026/09/11"], "response": ["json"]}
    assert resource.resource_key == "tpex_margin_sbl:securities_lending:2026-09-11"


# ---- TWSE ------------------------------------------------------------------


def test_twse_reads_every_security_and_not_the_total_row() -> None:
    assert len(twse().rows) == 1301
    assert len(twse(TWSE_2020, FIRST).rows) == 1062
    assert all(row.security_code for row in twse().rows)


def test_twse_0050_equals_the_legacy_row_without_conversion() -> None:
    observation = one(twse(TWSE_2020, FIRST), "0050")
    assert shares(observation) == {
        "previous_balance": 4_686_000, "borrowed": 327_000, "returned": 0,
        "adjustment": 0, "balance": 5_013_000,
        # 融券 次一營業日限額, share-precise; margin_trading holds 171,750 lots.
        "next_limit": 171_750_000,
        "next_available_limit": 1_006_197,
    }
    assert observation.note is None


def test_twse_quantities_are_not_whole_lots() -> None:
    assert shares(one(twse(TWSE_2020, FIRST), "2330"))["balance"] == 19_385_310


def test_twse_008201_is_not_converted() -> None:
    """Its lot is 100 shares, but this table is in shares: the limit is exactly
    25% of its 1,549,100 issued units, where MI_MARGN says 3,872 lots."""
    assert shares(one(twse(TWSE_2020, FIRST), "008201"))["next_limit"] == 387_275


def test_twse_a_status_note_is_stored_without_its_padding() -> None:
    assert one(twse(), "2330").note == "X"
    assert one(twse(TWSE_2020, FIRST), "2227").note == "Y"


def test_twse_a_negative_adjustment_is_stored() -> None:
    """當日調整 moves positions between accounts and is signed; the market
    total on 2024-01-02 was -565,000."""
    raw = mutate(TWSE, lambda p: twse_row(p, "2330").__setitem__(11, "-565,000"))
    assert int(one(twse(raw), "2330").adjustment.value) == -565_000


def test_twse_a_negative_balance_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: twse_row(p, "2330").__setitem__(12, "-1"))
    fails("invalid_numeric", twse, raw)


def test_twse_a_closed_day_is_no_data() -> None:
    fails("no_data_for_date", lambda c: twse(c, SUNDAY), TWSE_CLOSED)


def test_twse_answering_for_another_date_fails() -> None:
    fails("date_mismatch", lambda c: twse(c, date(2026, 9, 10)), TWSE)


def test_twse_a_restated_unit_fails_the_file() -> None:
    fails("unit_declaration_changed", twse, mutate(TWSE, lambda p: p.__setitem__("hints", "單位：張")))


def test_twse_a_changed_header_fails_the_file() -> None:
    fails("schema_mismatch", twse, mutate(TWSE, lambda p: p["fields"].append("新欄位")))


def test_twse_a_codeless_row_that_is_not_the_final_total_fails() -> None:
    def edit(payload):
        payload["data"].insert(0, list(payload["data"][-1]))
        payload["total"] += 1

    fails("invalid_identity", twse, mutate(TWSE, edit))


def test_twse_a_declared_total_that_disagrees_fails_the_file() -> None:
    fails("schema_mismatch", twse, mutate(TWSE, lambda p: p.__setitem__("total", 5)))


def test_twse_the_same_security_twice_fails_the_file() -> None:
    def edit(payload):
        payload["data"].insert(0, list(payload["data"][0]))
        payload["total"] += 1

    fails("duplicate_security", twse, mutate(TWSE, edit))


def test_twse_an_unreadable_quantity_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: p["data"][0].__setitem__(8, "--"))
    fails("unrecognised_value", twse, raw)


# ---- TPEx ------------------------------------------------------------------


def test_tpex_reads_every_published_row() -> None:
    assert len(tpex().rows) == 932
    assert len(tpex(TPEX_2020, FIRST).rows) == 756


def test_tpex_5009_equals_the_legacy_row() -> None:
    observation = one(tpex(TPEX_2020, FIRST), "5009")
    assert shares(observation) == {
        "previous_balance": 2_373_000, "borrowed": 0, "returned": 0,
        "adjustment": 0, "balance": 2_373_000,
        "next_limit": 116_683_997, "next_available_limit": 126_408,
    }
    assert observation.note is None


def test_tpex_a_status_note_is_stored() -> None:
    assert one(tpex(TPEX_2020, FIRST), "00694B").note == "X"


def test_tpex_a_closed_day_is_no_data() -> None:
    fails("no_data_for_date", lambda c: tpex(c, SUNDAY), TPEX_CLOSED)


def test_tpex_answering_for_another_date_fails() -> None:
    fails("date_mismatch", lambda c: tpex(c, date(2026, 9, 10)), TPEX)


def test_tpex_an_unknown_header_fails_the_file() -> None:
    raw = mutate(TPEX, lambda p: p["tables"][0]["fields"].__setitem__(7, "限額(張)"))
    fails("schema_mismatch", tpex, raw)


def test_tpex_a_declared_total_that_disagrees_fails_the_file() -> None:
    fails("schema_mismatch", tpex, mutate(TPEX, lambda p: p["tables"][0].__setitem__("totalCount", 5)))


def test_tpex_a_row_without_a_code_fails_the_file() -> None:
    fails("invalid_identity", tpex, mutate(TPEX, lambda p: p["tables"][0]["data"][0].__setitem__(0, "")))


def test_tpex_the_same_security_twice_fails_the_file() -> None:
    def duplicate(payload):
        table = payload["tables"][0]
        table["data"].append(list(table["data"][0]))
        table["totalCount"] += 1

    fails("duplicate_security", tpex, mutate(TPEX, duplicate))
