from __future__ import annotations

import json
from datetime import date

import pytest

from stock_data_center.ingestion.adapters import (
    TPExDelistingHistoryAdapter,
    TPExListingHistoryAdapter,
    TWSEDelistingHistoryAdapter,
    TWSEListingHistoryAdapter,
)
from stock_data_center.ingestion.models import (
    SecurityLifecycleRequest,
    SourceDataError,
)


def _tpex_payload(
    fields: tuple[str, ...], rows: list[list[object]], year=2026
) -> bytes:
    return json.dumps(
        {
            "stat": "ok",
            "date": str(year),
            "tables": [{"fields": list(fields), "data": rows}],
        },
        ensure_ascii=False,
    ).encode()


def test_twse_listing_history_uses_complete_rwd_dates_and_explicit_transfer() -> None:
    adapter = TWSEListingHistoryAdapter()
    payload = json.dumps(
        {
            "fields": list(adapter.fields),
            "data": [
                [
                    "5236",
                    "凌陽創新",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "115.07.16",
                    "",
                    100,
                    "櫃轉市",
                ],
                ["2443", "利碟", "", "", "", "", "", "", "", "90.01.03", "", 10.5, ""],
            ],
        },
        ensure_ascii=False,
    ).encode()

    parsed = adapter.parse(payload, SecurityLifecycleRequest())

    assert parsed.source_row_count == 2
    assert parsed.coverage_start == date(2001, 1, 3)
    assert parsed.coverage_end == date(2026, 7, 16)
    assert parsed.rows[-1].transfer_from_market == "TPEx"
    assert parsed.rows[-1].event_kind == "listing"


def test_twse_delisting_history_maps_terminal_event() -> None:
    adapter = TWSEDelistingHistoryAdapter()
    payload = json.dumps(
        {"fields": list(adapter.fields), "data": [["090/01/20", "國豐企業", "2334"]]},
        ensure_ascii=False,
    ).encode()

    parsed = adapter.parse(payload, SecurityLifecycleRequest())

    assert parsed.rows[0].effective_on == date(2001, 1, 20)
    assert parsed.rows[0].event_kind == "delisting"
    assert parsed.rows[0].market == "TWSE"


def test_tpex_year_scoped_listing_and_delisting_contracts() -> None:
    listing = TPExListingHistoryAdapter()
    listed = listing.parse(
        _tpex_payload(
            listing.fields,
            [[1, "5236", "凌陽創新", "109/01/06", "新台幣 10.0000元", "a", "b"]],
            2020,
        ),
        SecurityLifecycleRequest(2020),
    )
    delisting = TPExDelistingHistoryAdapter()
    delisted = delisting.parse(
        _tpex_payload(
            delisting.fields,
            [["5236", "凌陽創新科技股份有限公司", "115-07-16", "規則", "url"]],
        ),
        SecurityLifecycleRequest(2026),
    )

    assert listed.rows[0].effective_on == date(2020, 1, 6)
    assert delisted.rows[0].effective_on == date(2026, 7, 16)
    assert delisted.rows[0].event_kind == "delisting"


def test_tpex_empty_year_is_valid_zero_coverage() -> None:
    adapter = TPExDelistingHistoryAdapter()
    parsed = adapter.parse(
        _tpex_payload(adapter.fields, [], 2001), SecurityLifecycleRequest(2001)
    )

    assert parsed.rows == ()
    assert parsed.coverage_start is None
    assert parsed.source_row_count == 0


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            lambda adapter: _tpex_payload(adapter.fields, [], 2025),
            "date_mismatch",
        ),
        (
            lambda adapter: _tpex_payload(("changed",), []),
            "schema_mismatch",
        ),
        (
            lambda adapter: _tpex_payload(
                adapter.fields,
                [
                    ["5236", "公司", "115-07-16", "規則", "url"],
                    ["5236", "公司", "115-07-16", "規則", "url"],
                ],
            ),
            "ambiguous_identity",
        ),
    ],
)
def test_tpex_invalid_or_ambiguous_history_fails_loudly(payload, reason) -> None:
    adapter = TPExDelistingHistoryAdapter()
    with pytest.raises(SourceDataError) as raised:
        adapter.parse(payload(adapter), SecurityLifecycleRequest(2026))
    assert raised.value.reason_code == reason


def test_lifecycle_resource_identity_is_stable_and_official() -> None:
    twse = TWSEListingHistoryAdapter().resource(SecurityLifecycleRequest())
    tpex = TPExDelistingHistoryAdapter().resource(SecurityLifecycleRequest(2026))

    assert twse.resource_key == "twse:security-metadata-history:listing"
    assert twse.source_uri.startswith("https://www.twse.com.tw/rwd/")
    assert tpex.resource_key.endswith(":delisting:2026")
    assert tpex.source_uri.endswith("?response=json&date=2026")


def test_year_scope_is_required_only_for_tpex() -> None:
    with pytest.raises(ValueError, match="requires a Gregorian year"):
        TPExListingHistoryAdapter().resource(SecurityLifecycleRequest())
    with pytest.raises(ValueError, match="does not accept a year"):
        TWSEListingHistoryAdapter().resource(SecurityLifecycleRequest(2026))
