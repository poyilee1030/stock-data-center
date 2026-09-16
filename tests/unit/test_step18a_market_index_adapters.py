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
    """Match on the section label; TWSE appends its provider to it."""
    matches = [
        item
        for item in parsed.rows
        if item.index_name == name and item.section.split("/")[0] == section
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


def test_twse_rejects_a_file_with_no_recognisable_index_section() -> None:
    """Renaming the value columns stops a table being an index section at all,
    and a file with none of them is a contract change, not an empty answer."""

    def rename(payload):
        for table in payload["tables"]:
            if table.get("fields") and table["fields"][0] in ("指數", "報酬指數"):
                table["fields"][1] = "收盤"

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


def test_the_twse_adapter_names_the_stored_resource_it_would_reuse() -> None:
    """Reuse is a lifecycle capability 18-b must build, not a key coincidence.

    An earlier version of this adapter borrowed the price import's resource key
    and claimed that meant the artifact was reused. It does not: checkpoints are
    scoped by `import_id`, so under a new one the file is fetched again, and
    under the price import's own id the completed checkpoint short-circuits and
    no index row is written at all. The adapter therefore has its own key and
    states separately which stored resource holds its bytes.
    """
    request = MarketIndexRequest(date(2026, 9, 11))
    adapter = TWSEMarketIndexAdapter()
    assert adapter.resource(request).resource_key == (
        "twse_mi_index:index-sections:2026-09-11"
    )
    assert adapter.stored_resource_key(request) == (
        "twse_mi_index:daily-quotes:2026-09-11"
    )


def test_an_index_section_with_an_unknown_label_fails_closed() -> None:
    """A section is recognised by its columns; its label must then be known.

    Selecting sections by the first column's label alone means a rename drops
    30-50 indices for that date with no error at all.
    """

    def rename(payload):
        for table in payload["tables"]:
            if table.get("fields") and table["fields"][0] == "報酬指數":
                table["fields"][0] = "總報酬指數"
                break

    with pytest.raises(SourceDataError) as error:
        twse(mutate(TWSE, rename))
    assert error.value.reason_code == "schema_mismatch"


def test_the_section_keeps_the_provider_the_title_names() -> None:
    """Six sections, three providers. `指數` alone conflates them, so the same
    name published by TWSE and by TIP would quarantine the whole date."""
    parsed = twse()
    sections = {item.section for item in parsed.rows}
    assert len(sections) == 6
    assert "指數/臺灣證券交易所" in sections
    assert "報酬指數/臺灣指數公司" in sections


def test_an_index_with_no_published_close_fails_closed() -> None:
    """`close_value` is NOT NULL in storage, so a missing close has to be
    rejected here rather than become an IntegrityError in 18-b's writer."""

    def blank(payload):
        table = next(
            t for t in payload["tables"] if t.get("fields") and t["fields"][0] == "指數"
        )
        table["data"][0][1] = "--"

    with pytest.raises(SourceDataError) as error:
        twse(mutate(TWSE, blank))
    assert error.value.reason_code == "missing_value"


def test_an_unknown_sign_fails_closed_even_with_no_magnitude() -> None:
    """The marker is validated before the magnitude, so a missing number cannot
    turn an unreadable sign into a silent None."""

    def break_it(payload):
        table = next(
            t for t in payload["tables"] if t.get("fields") and t["fields"][0] == "指數"
        )
        table["data"][0][2] = "<p>?</p>"
        table["data"][0][3] = "--"

    with pytest.raises(SourceDataError) as error:
        twse(mutate(TWSE, break_it))
    assert error.value.reason_code == "ambiguous_direction"


def test_a_taiex_status_that_is_not_an_empty_answer_stays_a_status_error() -> None:
    """Otherwise a maintenance page reads as an empty month, and 18-b walks
    about 80 of them producing silent coverage gaps."""

    def break_it(payload):
        payload["stat"] = "系統忙碌中"
        payload["data"] = []

    with pytest.raises(SourceDataError) as error:
        taiex(mutate(TAIEX, break_it))
    assert error.value.reason_code == "source_status"
