"""Step 21-a — the margin-trading adapters, against captured bytes.

Every fixture is a live response from 2026-09-19, fetched with the URL the
adapter builds. `*_20260913_closed.json` is a Sunday.

- TWSE `rwd/zh/marginTrading/MI_MARGN`, `selectType=ALL`. The per-security
  table is `融資融券彙總`; its sibling `信用交易統計` labels its rows
  `融資(交易單位)` / `融券(交易單位)`, which is where the unit is stated. The
  per-security fields repeat 買進/賣出/前日餘額/今日餘額/次一營業日限額 for the
  margin and the short side, so columns are read by position.
- TPEx `www/zh-tw/margin/balance`, the JSON of the legacy `margin_bal` page, 20
  fields whose labels say `(張)`. Its short side lists 券賣 before 券買.

Both quantities are lots of 1,000 shares. That the lot is 1,000 shares for every
security is checked, not assumed: the source's note says margin stops at 25% of
listed shares, and the next-day limit × 1,000 equals 25% of the issued shares
Step 20-d stored for 1,118 of 1,230 TWSE securities on 2026-09-11 (the rest are
reduced quotas, none near a factor of ten). The reconciliation repeats that
check on every date.

Expected values are legacy `stock_db.margin_trading` rows × 1,000.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from stock_data_center.ingestion.adapters import (
    TPExMarginTradingAdapter,
    TWSEMarginTradingAdapter,
)
from stock_data_center.ingestion.models import MarginTradingRequest, SourceDataError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_mi_margn_20260911.json").read_bytes()
TWSE_2020 = (FIXTURES / "twse_mi_margn_20200102.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_mi_margn_20260913_closed.json").read_bytes()
TPEX = (FIXTURES / "tpex_margin_balance_20260911.json").read_bytes()
TPEX_2020 = (FIXTURES / "tpex_margin_balance_20200102.json").read_bytes()
TPEX_CLOSED = (FIXTURES / "tpex_margin_balance_20260913_closed.json").read_bytes()

DAY = date(2026, 9, 11)
FIRST = date(2020, 1, 2)
SUNDAY = date(2026, 9, 13)

QUANTITIES = (
    "margin_buy", "margin_sell", "margin_cash_repayment", "margin_previous_balance",
    "margin_balance", "margin_next_limit",
    "short_buy", "short_sell", "short_stock_repayment", "short_previous_balance",
    "short_balance", "short_next_limit", "offset_balance",
)


def twse(content: bytes = TWSE, day: date = DAY):
    return TWSEMarginTradingAdapter().parse(content, MarginTradingRequest(day))


def tpex(content: bytes = TPEX, day: date = DAY):
    return TPExMarginTradingAdapter().parse(content, MarginTradingRequest(day))


def mutate(raw: bytes, edit) -> bytes:
    payload = json.loads(raw)
    edit(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def one(parsed, code: str):
    matches = [row for row in parsed.rows if row.security_code == code]
    assert len(matches) == 1, f"{code} appears {len(matches)} times"
    return matches[0].observation


def lots(observation) -> dict[str, int]:
    """Each quantity back in lots, to compare with legacy's own unit."""
    values = {}
    for name in QUANTITIES:
        shares = int(getattr(observation, name).value)
        assert shares % 1000 == 0
        values[name] = shares // 1000
    return values


# ---- identity and requests -------------------------------------------------


def test_the_margin_sources_are_their_own() -> None:
    assert TWSEMarginTradingAdapter.dataset_code == "margin_trading"
    assert TPExMarginTradingAdapter.dataset_code == "margin_trading"
    assert TWSEMarginTradingAdapter.source == "twse_mi_margn"
    assert TPExMarginTradingAdapter.source == "tpex_margin_balance"
    assert (TWSEMarginTradingAdapter.market, TPExMarginTradingAdapter.market) == ("TWSE", "TPEx")


def test_twse_requests_every_security_as_json() -> None:
    resource = TWSEMarginTradingAdapter().resource(MarginTradingRequest(DAY))
    url = urlsplit(resource.source_uri)
    assert (url.netloc, url.path) == ("www.twse.com.tw", "/rwd/zh/marginTrading/MI_MARGN")
    assert parse_qs(url.query) == {
        "date": ["20260911"], "selectType": ["ALL"], "response": ["json"],
    }
    assert resource.resource_key == "twse_mi_margn:margin_trading:2026-09-11"


def test_tpex_requests_the_balance_table_as_json() -> None:
    resource = TPExMarginTradingAdapter().resource(MarginTradingRequest(DAY))
    url = urlsplit(resource.source_uri)
    assert (url.netloc, url.path) == ("www.tpex.org.tw", "/www/zh-tw/margin/balance")
    assert parse_qs(url.query) == {"date": ["2026/09/11"], "response": ["json"]}
    assert resource.resource_key == "tpex_margin_balance:margin_trading:2026-09-11"


# ---- TWSE ------------------------------------------------------------------


def test_twse_reads_every_published_row() -> None:
    assert len(twse().rows) == 1297
    assert len(twse(TWSE_2020, FIRST).rows) == 1060


def test_twse_2330_equals_the_legacy_row_in_shares() -> None:
    observation = one(twse(TWSE_2020, FIRST), "2330")
    assert lots(observation) == {
        "margin_buy": 484, "margin_sell": 1223, "margin_cash_repayment": 38,
        "margin_previous_balance": 17975, "margin_balance": 17198,
        "margin_next_limit": 6482595,
        "short_buy": 13, "short_sell": 132, "short_stock_repayment": 0,
        "short_previous_balance": 326, "short_balance": 445,
        "short_next_limit": 6482595, "offset_balance": 4,
    }
    assert observation.margin_utilization_ratio is None
    assert observation.short_utilization_ratio is None


def test_twse_a_closed_day_is_no_data() -> None:
    with pytest.raises(SourceDataError) as error:
        twse(TWSE_CLOSED, SUNDAY)
    assert error.value.reason_code == "no_data_for_date"


def test_twse_answering_for_another_date_fails() -> None:
    with pytest.raises(SourceDataError) as error:
        twse(TWSE, date(2026, 9, 10))
    assert error.value.reason_code == "date_mismatch"


def test_twse_a_changed_per_security_header_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: p["tables"][1]["fields"].append("新欄位"))
    with pytest.raises(SourceDataError) as error:
        twse(raw)
    assert error.value.reason_code == "schema_mismatch"


def test_twse_a_missing_per_security_table_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: p.__setitem__("tables", p["tables"][:1]))
    with pytest.raises(SourceDataError) as error:
        twse(raw)
    assert error.value.reason_code == "schema_mismatch"


def test_twse_a_restated_unit_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: p["tables"][0]["data"][0].__setitem__(0, "融資(股)"))
    with pytest.raises(SourceDataError) as error:
        twse(raw)
    assert error.value.reason_code == "unit_declaration_changed"


def test_twse_the_same_security_twice_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: p["tables"][1]["data"].append(list(p["tables"][1]["data"][0])))
    with pytest.raises(SourceDataError) as error:
        twse(raw)
    assert error.value.reason_code == "duplicate_security"


def test_twse_an_unreadable_quantity_fails_the_file() -> None:
    raw = mutate(TWSE, lambda p: p["tables"][1]["data"][0].__setitem__(2, "--"))
    with pytest.raises(SourceDataError) as error:
        twse(raw)
    assert error.value.reason_code == "unrecognised_value"


# ---- TPEx ------------------------------------------------------------------


def test_tpex_reads_every_published_row() -> None:
    assert len(tpex().rows) == 920
    assert len(tpex(TPEX_2020, FIRST).rows) == 743


def test_tpex_5009_equals_the_legacy_row_in_shares() -> None:
    observation = one(tpex(TPEX_2020, FIRST), "5009")
    assert lots(observation) == {
        "margin_buy": 64, "margin_sell": 55, "margin_cash_repayment": 0,
        "margin_previous_balance": 7429, "margin_balance": 7438,
        "margin_next_limit": 116683,
        "short_buy": 0, "short_sell": 0, "short_stock_repayment": 0,
        "short_previous_balance": 10, "short_balance": 10,
        "short_next_limit": 116683, "offset_balance": 0,
    }
    assert observation.margin_utilization_ratio == Decimal("6.37")
    assert observation.short_utilization_ratio == Decimal("0.0")


def test_tpex_reads_short_sell_before_short_buy() -> None:
    # 券賣 comes before 券買 in TPEx's header; swapping them would still parse.
    def edit(payload):
        row = payload["tables"][0]["data"][0]
        row[11], row[12] = "7", "3"

    observation = tpex(mutate(TPEX, edit)).rows[0].observation
    assert int(observation.short_sell.value) == 7000
    assert int(observation.short_buy.value) == 3000


def test_tpex_a_closed_day_is_no_data() -> None:
    with pytest.raises(SourceDataError) as error:
        tpex(TPEX_CLOSED, SUNDAY)
    assert error.value.reason_code == "no_data_for_date"


def test_tpex_answering_for_another_date_fails() -> None:
    with pytest.raises(SourceDataError) as error:
        tpex(TPEX, date(2026, 9, 10))
    assert error.value.reason_code == "date_mismatch"


def test_tpex_an_unknown_header_fails_the_file() -> None:
    raw = mutate(TPEX, lambda p: p["tables"][0]["fields"].__setitem__(2, "前資餘額(股)"))
    with pytest.raises(SourceDataError) as error:
        tpex(raw)
    assert error.value.reason_code == "schema_mismatch"


def test_tpex_a_declared_total_that_disagrees_fails_the_file() -> None:
    raw = mutate(TPEX, lambda p: p["tables"][0].__setitem__("totalCount", 5))
    with pytest.raises(SourceDataError) as error:
        tpex(raw)
    assert error.value.reason_code == "schema_mismatch"


def test_tpex_a_utilization_above_one_hundred_fails_the_file() -> None:
    raw = mutate(TPEX, lambda p: p["tables"][0]["data"][0].__setitem__(8, "100.01"))
    with pytest.raises(SourceDataError) as error:
        tpex(raw)
    assert error.value.reason_code == "invalid_numeric"


def test_tpex_the_same_security_twice_fails_the_file() -> None:
    def duplicate(payload):
        table = payload["tables"][0]
        table["data"].append(list(table["data"][0]))
        table["totalCount"] += 1

    with pytest.raises(SourceDataError) as error:
        tpex(mutate(TPEX, duplicate))
    assert error.value.reason_code == "duplicate_security"
