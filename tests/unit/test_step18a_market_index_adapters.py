"""Step 18-a — the market-index adapters, against captured bytes.

The TWSE fixture is the same artifact Step 17-c stored for its prices: this step
reads a different section of it and fetches nothing for that market.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.ingestion.adapters import (
    TPExMarketIndexAdapter,
    TWSEMarketIndexAdapter,
    TWSETaiexHistoryAdapter,
)
from stock_data_center.ingestion.models import (
    MarketIndexRequest,
    SourceDataError,
    TaiexHistoryRequest,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_mi_index_allbut0999_20260911.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_mi_index_allbut0999_20240724_closed.json").read_bytes()
TPEX_2026 = (FIXTURES / "tpex_index_summary_20260911.json").read_bytes()
TPEX_2020 = (FIXTURES / "tpex_index_summary_20200102.json").read_bytes()
TAIEX = (FIXTURES / "twse_mi_5mins_hist_202601.json").read_bytes()


def twse(content: bytes = TWSE, day: date = date(2026, 9, 11)):
    return TWSEMarketIndexAdapter().parse(content, MarketIndexRequest(day))


def tpex(content: bytes = TPEX_2026, day: date = date(2026, 9, 11)):
    return TPExMarketIndexAdapter().parse(content, MarketIndexRequest(day))


def taiex(content: bytes = TAIEX, month: date = date(2026, 1, 1)):
    return TWSETaiexHistoryAdapter().parse(content, TaiexHistoryRequest(month))


def row(parsed, name: str, section: str = "指數"):
    matches = [
        item
        for item in parsed.rows
        if item.index_name == name and item.section == section
    ]
    assert len(matches) == 1, f"{section}/{name} appears {len(matches)} times"
    return matches[0]


def mutate(raw: bytes, edit):
    payload = json.loads(raw)
    edit(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


# --- identity and shape ------------------------------------------------------


def test_the_index_sources_are_their_own_and_identity_is_the_published_name() -> None:
    """No feed publishes an index code, so the name is the identity."""
    assert TWSEMarketIndexAdapter.source == "twse_mi_index"
    assert TPExMarketIndexAdapter.source == "tpex_index_summary"
    assert TWSETaiexHistoryAdapter.source == "twse_mi_5mins_hist"
    for adapter in (
        TWSEMarketIndexAdapter,
        TPExMarketIndexAdapter,
        TWSETaiexHistoryAdapter,
    ):
        assert adapter.dataset_code == "market_index"


def test_twse_indices_reuse_the_artifact_step_17c_already_stored() -> None:
    """The same source code as the prices, because it is the same artifact."""
    assert TWSEMarketIndexAdapter.source == "twse_mi_index"
    resource = TWSEMarketIndexAdapter().resource(
        MarketIndexRequest(date(2026, 9, 11))
    )
    assert "type=ALLBUT0999" in resource.source_uri
    # Same resource key as the price import, so the lifecycle finds the stored
    # artifact instead of fetching the file a second time.
    assert resource.resource_key == "twse_mi_index:daily-quotes:2026-09-11"


# --- TWSE --------------------------------------------------------------------


def test_twse_reads_every_index_section_not_just_the_first() -> None:
    """Six sections: price and return, each for TWSE, cross-market and TIP."""
    parsed = twse()
    assert parsed.market == "TWSE"
    assert parsed.trade_date == date(2026, 9, 11)
    assert len(parsed.rows) == 273
    assert parsed.section_count == 6


def test_twse_index_values_are_stored_as_published() -> None:
    item = row(twse(), "寶島股價指數").observation
    assert item.close_value == Decimal("51178.86")
    # 漲跌點數 is unsigned and the sign lives in its own column.
    assert item.change_points == Decimal("-865.63")
    assert item.change_percent == Decimal("-1.66")
    # A whole-list index source publishes no OHLC and no trade value.
    assert item.open_value is None
    assert item.high_value is None
    assert item.low_value is None
    assert item.trade_value is None


def test_twse_applies_its_sign_column_to_the_unsigned_points() -> None:
    rising = [
        item
        for item in twse().rows
        if item.observation.change_points is not None
        and item.observation.change_points > 0
    ]
    assert len(rising) == 40
    assert all(item.observation.change_percent > 0 for item in rising)


def test_twse_return_indices_are_the_same_dataset_as_price_indices() -> None:
    """`報酬指數` is a published index name like any other."""
    assert row(twse(), "發行量加權股價報酬指數", "報酬指數")


def test_a_closed_trade_date_fails_closed_on_twse() -> None:
    with pytest.raises(SourceDataError) as error:
        twse(TWSE_CLOSED, date(2024, 7, 24))
    assert error.value.reason_code == "no_data_for_date"


def test_twse_rejects_a_response_for_another_date() -> None:
    with pytest.raises(SourceDataError) as error:
        twse(TWSE, date(2026, 9, 10))
    assert error.value.reason_code == "date_mismatch"


def test_twse_rejects_a_changed_index_header() -> None:
    def rename(payload):
        for table in payload["tables"]:
            if table.get("fields") and table["fields"][0] == "指數":
                table["fields"][1] = "收盤"
                break

    with pytest.raises(SourceDataError) as error:
        twse(mutate(TWSE, rename))
    assert error.value.reason_code == "schema_mismatch"


def test_a_duplicated_index_name_fails_closed() -> None:
    def duplicate(payload):
        table = next(t for t in payload["tables"] if t.get("fields") and t["fields"][0] == "指數")
        table["data"].append(list(table["data"][0]))

    with pytest.raises(SourceDataError) as error:
        twse(mutate(TWSE, duplicate))
    assert error.value.reason_code == "ambiguous_identity"


# --- TPEx --------------------------------------------------------------------


def test_tpex_reads_both_its_price_and_return_sections() -> None:
    parsed = tpex()
    assert parsed.market == "TPEx"
    assert parsed.section_count == 2
    assert len(parsed.rows) == 74


def test_tpex_repeats_one_name_across_its_two_sections() -> None:
    """The published name alone is not an identity in this feed.

    `櫃買指數` appears in the price section at 395.52 and in the return section
    at 735.15 — 32 of 34 names are in both. TWSE avoids the collision by naming
    its return indices distinctly; TPEx does not, so the section is part of the
    identity. Legacy `market_indices` kept only one section and lost the other.
    """
    parsed = tpex()
    price = row(parsed, "櫃買指數", "指數").observation
    total_return = row(parsed, "櫃買指數", "報酬指數").observation
    assert price.close_value == Decimal("395.52")
    assert total_return.close_value == Decimal("735.15")
    assert row(parsed, "櫃買指數", "指數").index_code("tpex_index_summary") != row(
        parsed, "櫃買指數", "報酬指數"
    ).index_code("tpex_index_summary")


def test_tpex_publishes_a_signed_change_and_needs_no_sign_column() -> None:
    item = row(tpex(), "櫃買指數").observation
    assert item.close_value == Decimal("395.52")
    assert item.change_points == Decimal("-9.72")
    assert item.change_percent == Decimal("-2.40")
    assert item.open_value is None


def test_tpex_2020_parses_through_the_same_contract() -> None:
    parsed = tpex(TPEX_2020, date(2020, 1, 2))
    assert len(parsed.rows) == 60
    assert row(parsed, "櫃買指數").observation.close_value == Decimal("150.91")


def test_tpex_rejects_a_response_for_another_date() -> None:
    with pytest.raises(SourceDataError) as error:
        tpex(TPEX_2026, date(2026, 9, 10))
    assert error.value.reason_code == "date_mismatch"


def test_an_empty_tpex_index_file_fails_closed() -> None:
    def empty(payload):
        for table in payload["tables"]:
            table["data"] = []

    with pytest.raises(SourceDataError) as error:
        tpex(mutate(TPEX_2026, empty))
    assert error.value.reason_code == "no_data_for_date"


# --- TAIEX OHLC --------------------------------------------------------------


def test_the_taiex_history_adapter_parses_one_calendar_month() -> None:
    """The only index OHLC any inspected endpoint serves for past dates."""
    parsed = taiex()
    assert parsed.market == "TWSE"
    assert parsed.month == date(2026, 1, 1)
    assert len(parsed.rows) == 21
    first = parsed.rows[0]
    assert first.trade_date == date(2026, 1, 2)
    assert first.observation.open_value == Decimal("29016.68")
    assert first.observation.high_value == Decimal("29363.43")
    assert first.observation.low_value == Decimal("29007.75")
    assert first.observation.close_value == Decimal("29349.81")
    # This feed publishes no change columns, so nothing is claimed for them.
    assert first.observation.change_points is None
    assert first.observation.change_percent is None


def test_the_taiex_history_adapter_names_the_index_it_covers() -> None:
    assert taiex().index_name == "發行量加權股價指數"


def test_taiex_requests_one_month_and_rejects_another() -> None:
    resource = TWSETaiexHistoryAdapter().resource(
        TaiexHistoryRequest(date(2026, 1, 1))
    )
    assert "date=20260101" in resource.source_uri
    assert resource.resource_key == "twse_mi_5mins_hist:taiex:2026-01"
    with pytest.raises(SourceDataError) as error:
        taiex(TAIEX, date(2026, 2, 1))
    assert error.value.reason_code == "date_mismatch"


def test_taiex_rejects_a_changed_header() -> None:
    def rename(payload):
        payload["fields"][1] = "開盤"

    with pytest.raises(SourceDataError) as error:
        taiex(mutate(TAIEX, rename))
    assert error.value.reason_code == "schema_mismatch"
