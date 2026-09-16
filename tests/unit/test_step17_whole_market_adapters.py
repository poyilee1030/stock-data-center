"""Step 17-a — the whole-market daily-price adapters, against captured bytes.

Every fixture here is the official response exactly as the endpoint served it
on 2026-09-16, including the three TPEx header variants at their real boundary
dates and both markets' answer for a closed date.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.ingestion.adapters import (
    TPExWholeMarketDailyAdapter,
    TWSEWholeMarketDailyAdapter,
)
from stock_data_center.ingestion.models import (
    SourceDataError,
    SourceMoneyUnit,
    SourceQuantityUnit,
    WholeMarketDailyRequest,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

TWSE_2026 = (FIXTURES / "twse_mi_index_allbut0999_20260911.json").read_bytes()
TWSE_2020 = (FIXTURES / "twse_mi_index_allbut0999_20200102.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_mi_index_allbut0999_20240724_closed.json").read_bytes()
TPEX_V1 = (FIXTURES / "tpex_otc_quotes_20200102.json").read_bytes()
TPEX_V2 = (FIXTURES / "tpex_otc_quotes_20200430.json").read_bytes()
TPEX_V3 = (FIXTURES / "tpex_otc_quotes_20260911.json").read_bytes()
TPEX_CLOSED = (FIXTURES / "tpex_otc_quotes_20240724_closed.json").read_bytes()


def twse(content: bytes = TWSE_2026, trade_date: date = date(2026, 9, 11)):
    return TWSEWholeMarketDailyAdapter().parse(
        content, WholeMarketDailyRequest(trade_date)
    )


def tpex(content: bytes = TPEX_V3, trade_date: date = date(2026, 9, 11)):
    return TPExWholeMarketDailyAdapter().parse(
        content, WholeMarketDailyRequest(trade_date)
    )


def row(parsed, security_code: str):
    matches = [item for item in parsed.rows if item.security_code == security_code]
    assert len(matches) == 1, f"{security_code} appears {len(matches)} times"
    return matches[0]


def mutate_twse(payload_edit):
    payload = json.loads(TWSE_2026)
    payload_edit(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def mutate_tpex(payload_edit):
    payload = json.loads(TPEX_V3)
    payload_edit(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def stock_table(payload):
    return next(
        table
        for table in payload["tables"]
        if table.get("fields") and table["fields"][0] == "證券代號"
    )


# --- identity and units, declared rather than inferred -----------------------


def test_the_whole_market_sources_are_not_the_step_9_per_security_pilots() -> None:
    """Different field coverage, so one logical key must not alternate.

    ROADMAP Step 17 and CLAUDE.md §30: the pilots publish no bid/ask level, so
    sharing their source code would make every import flap a revision.
    """
    assert TWSEWholeMarketDailyAdapter.source == "twse_mi_index"
    assert TPExWholeMarketDailyAdapter.source == "tpex_otc_quotes"
    assert TWSEWholeMarketDailyAdapter.dataset_code == "daily_price"
    assert TPExWholeMarketDailyAdapter.dataset_code == "daily_price"
    assert TWSEWholeMarketDailyAdapter.market == "TWSE"
    assert TPExWholeMarketDailyAdapter.market == "TPEx"


@pytest.mark.parametrize(
    "adapter",
    [TWSEWholeMarketDailyAdapter(), TPExWholeMarketDailyAdapter()],
    ids=["twse", "tpex"],
)
def test_both_whole_market_feeds_publish_shares_and_whole_twd(adapter) -> None:
    """Audit §4.1: TWSE hints 單位：元、股 and TPEx labels 成交金額(元)."""
    assert adapter.semantics.traded_quantity_unit is SourceQuantityUnit.SHARE
    assert adapter.semantics.trade_value_unit is SourceMoneyUnit.TWD
    # The one disclosed bid/ask level is counted in lots in both markets.
    assert adapter.semantics.disclosed_volume_unit is (
        SourceQuantityUnit.LOT_1000_SHARES
    )


def test_each_market_requests_one_trade_date() -> None:
    twse_resource = TWSEWholeMarketDailyAdapter().resource(
        WholeMarketDailyRequest(date(2026, 9, 11))
    )
    tpex_resource = TPExWholeMarketDailyAdapter().resource(
        WholeMarketDailyRequest(date(2026, 9, 11))
    )
    assert twse_resource.resource_key == "twse_mi_index:daily-quotes:2026-09-11"
    assert "type=ALLBUT0999" in twse_resource.source_uri
    assert "date=20260911" in twse_resource.source_uri
    assert "response=json" in twse_resource.source_uri
    assert tpex_resource.resource_key == "tpex_otc_quotes:daily-quotes:2026-09-11"
    assert "date=2026%2F09%2F11" in tpex_resource.source_uri
    assert "type=EW" in tpex_resource.source_uri


# --- TWSE --------------------------------------------------------------------


def test_twse_reads_the_stock_section_by_its_header_not_by_table_index() -> None:
    parsed = twse()
    assert parsed.market == "TWSE"
    assert parsed.trade_date == date(2026, 9, 11)
    assert len(parsed.rows) == 1379
    assert parsed.source_fields[0] == "證券代號"


def test_twse_stock_section_is_found_even_if_the_index_sections_move() -> None:
    def move_it(payload):
        payload["tables"] = list(reversed(payload["tables"]))

    assert len(twse(mutate_twse(move_it)).rows) == 1379


def test_twse_values_are_stored_exactly_as_published() -> None:
    item = row(twse(), "00400A")
    assert item.security_name == "主動國泰動能高息"
    observation = item.observation
    assert observation.trade_date == date(2026, 9, 11)
    assert observation.volume == Decimal("39736424")
    assert observation.trade_count == 13299
    assert observation.trade_value == Decimal("585390297")
    assert observation.open_price == Decimal("14.75")
    assert observation.high_price == Decimal("14.82")
    assert observation.low_price == Decimal("14.65")
    assert observation.close_price == Decimal("14.80")
    assert observation.price_change == Decimal("-0.21")
    assert observation.price_direction == "-"
    assert observation.last_bid_price == Decimal("14.79")
    assert observation.last_ask_price == Decimal("14.80")
    # 45 lots and 17 lots, normalized to shares.
    assert observation.last_bid_volume == Decimal("45000")
    assert observation.last_ask_volume == Decimal("17000")


def test_twse_signs_the_change_with_its_own_direction_column() -> None:
    """漲跌價差 is unsigned; the sign lives in 漲跌(+/-) as coloured markup."""
    rising = row(twse(TWSE_2020, date(2020, 1, 2)), "2330")
    assert rising.observation.price_change == Decimal("8.00")
    assert rising.observation.price_direction == "+"
    falling = row(twse(), "1101")
    assert falling.observation.price_change == Decimal("-0.30")
    assert falling.observation.price_direction == "-"


def test_twse_flat_and_not_compared_rows_keep_their_published_meaning() -> None:
    flat = row(twse(), "00709")
    assert flat.observation.price_direction == "flat"
    assert flat.observation.price_change == Decimal("0.00")
    # TWSE's own note: +/-/X means 漲/跌/不比價.
    not_compared = row(twse(), "00625K")
    assert not_compared.observation.price_direction == "X"


def test_twse_untraded_rows_keep_null_prices_rather_than_zero() -> None:
    untraded = row(twse(), "01010T")
    assert untraded.observation.open_price is None
    assert untraded.observation.close_price is None
    assert untraded.observation.volume == Decimal("0")
    assert untraded.observation.last_bid_price == Decimal("9.98")


def test_twse_2020_parses_through_the_same_contract() -> None:
    parsed = twse(TWSE_2020, date(2020, 1, 2))
    assert parsed.trade_date == date(2020, 1, 2)
    assert len(parsed.rows) == 1114
    assert row(parsed, "2330").observation.close_price == Decimal("339.00")


def test_a_closed_trade_date_fails_closed_on_twse() -> None:
    with pytest.raises(SourceDataError) as error:
        twse(TWSE_CLOSED, date(2024, 7, 24))
    assert error.value.reason_code == "source_status"


def test_twse_rejects_a_response_for_another_date() -> None:
    with pytest.raises(SourceDataError) as error:
        twse(TWSE_2026, date(2026, 9, 10))
    assert error.value.reason_code == "date_mismatch"


def test_twse_rejects_a_changed_header() -> None:
    def rename(payload):
        stock_table(payload)["fields"][2] = "成交量"

    with pytest.raises(SourceDataError) as error:
        twse(mutate_twse(rename))
    assert error.value.reason_code == "schema_mismatch"


def test_twse_rejects_a_missing_stock_section() -> None:
    def drop(payload):
        payload["tables"] = [
            table
            for table in payload["tables"]
            if not (table.get("fields") and table["fields"][0] == "證券代號")
        ]

    with pytest.raises(SourceDataError) as error:
        twse(mutate_twse(drop))
    assert error.value.reason_code == "schema_mismatch"


def test_an_unsigned_blank_direction_with_a_real_change_fails_closed() -> None:
    """A blank sign only ever accompanies a zero change; anything else is a
    change whose direction we would have to guess."""

    def break_it(payload):
        table = stock_table(payload)
        for data_row in table["data"]:
            if data_row[0] == "00709":
                data_row[10] = "0.35"

    with pytest.raises(SourceDataError) as error:
        twse(mutate_twse(break_it))
    assert error.value.reason_code == "ambiguous_direction"


def test_a_duplicated_security_code_fails_closed() -> None:
    def duplicate(payload):
        table = stock_table(payload)
        table["data"].append(list(table["data"][0]))

    with pytest.raises(SourceDataError) as error:
        twse(mutate_twse(duplicate))
    assert error.value.reason_code == "ambiguous_identity"


# --- TPEx --------------------------------------------------------------------


def test_tpex_header_variant_one_has_no_disclosed_bid_ask_volume() -> None:
    """2020-01-02 → 2020-04-29: 15 columns, prices only at the bid and ask."""
    parsed = tpex(TPEX_V1, date(2020, 1, 2))
    assert parsed.header_variant == "prices_only"
    assert len(parsed.rows) == 876
    item = row(parsed, "00679B").observation
    assert item.close_price == Decimal("42.01")
    assert item.price_change == Decimal("-0.28")
    assert item.volume == Decimal("1055000")
    assert item.trade_value == Decimal("44288880")
    assert item.trade_count == 565
    assert item.last_bid_price == Decimal("42.01")
    assert item.last_ask_price == Decimal("42.04")
    assert item.last_bid_volume is None
    assert item.last_ask_volume is None


def test_tpex_header_variant_two_labels_the_disclosed_volume_千股() -> None:
    """2020-04-30 → 2025-01-09: 17 columns, 最後買量<br>(千股)."""
    parsed = tpex(TPEX_V2, date(2020, 4, 30))
    assert parsed.header_variant == "volume_in_thousand_shares"
    item = row(parsed, "00679B").observation
    assert item.last_bid_price == Decimal("51.00")
    assert item.last_bid_volume == Decimal("3000")
    assert item.last_ask_volume == Decimal("1000")


def test_tpex_header_variant_three_relabels_the_same_unit_張數() -> None:
    """From 2025-01-10 the label changes and the unit does not."""
    parsed = tpex()
    assert parsed.header_variant == "volume_in_lots"
    item = row(parsed, "00411A").observation
    assert item.last_bid_volume == Decimal("147000")
    assert item.last_ask_volume == Decimal("1542000")


def test_tpex_publishes_a_signed_change_and_no_direction_column() -> None:
    """Audit §4.1: stk_wn1430 has no 漲跌(+/-) column, so nothing is claimed."""
    item = row(tpex(), "00411A").observation
    assert item.price_change == Decimal("-0.17")
    assert item.price_direction is None


def test_tpex_ex_rights_markers_are_the_sources_own_not_compared_rows() -> None:
    """除息 / 除權 / 除權息 stand where the number would be: 不比價, like TWSE's X."""
    parsed = tpex()
    marked = [
        item for item in parsed.rows if item.observation.price_direction == "X"
    ]
    assert len(marked) == 1
    assert marked[0].observation.price_change is None


def test_tpex_untraded_rows_keep_null_prices() -> None:
    item = row(tpex(), "00856B").observation
    assert item.close_price is None
    assert item.open_price is None
    assert item.price_change is None
    assert item.volume == Decimal("0")
    assert item.trade_value == Decimal("0")
    assert item.last_bid_price == Decimal("37.16")


def test_a_closed_trade_date_fails_closed_on_tpex() -> None:
    """TPEx answers ok with an empty table rather than an error status."""
    with pytest.raises(SourceDataError) as error:
        tpex(TPEX_CLOSED, date(2024, 7, 24))
    assert error.value.reason_code == "empty_coverage"


def test_tpex_rejects_a_response_for_another_date() -> None:
    with pytest.raises(SourceDataError) as error:
        tpex(TPEX_V3, date(2026, 9, 10))
    assert error.value.reason_code == "date_mismatch"


def test_an_unknown_tpex_header_quarantines_instead_of_guessing() -> None:
    def add_column(payload):
        table = payload["tables"][0]
        table["fields"].append("新欄位")
        for data_row in table["data"]:
            data_row.append("0")

    with pytest.raises(SourceDataError) as error:
        tpex(mutate_tpex(add_column))
    assert error.value.reason_code == "schema_mismatch"


def test_the_tpex_flag_field_must_agree_with_the_header_variant() -> None:
    """The response carries its own 千股/張數 flag; disagreement is not ours to
    resolve."""

    def contradict(payload):
        payload["flagField"] = "千股"

    with pytest.raises(SourceDataError) as error:
        tpex(mutate_tpex(contradict))
    assert error.value.reason_code == "schema_mismatch"
