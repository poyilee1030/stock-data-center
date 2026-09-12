from __future__ import annotations

import json
from datetime import date

import pytest

from stock_data_center.ingestion.adapters import (
    TPExSecurityMetadataAdapter,
    TWSESecurityMetadataAdapter,
)
from stock_data_center.ingestion.models import (
    SecurityMetadataRequest,
    SourceDataError,
)


def _payload(row: dict[str, str]) -> bytes:
    return json.dumps([row], ensure_ascii=False).encode()


def _twse_row(**changes: str) -> dict[str, str]:
    row = {
        "出表日期": "1150911",
        "公司代號": "2330",
        "公司名稱": " 台灣積體電路製造股份有限公司 ",
        "產業別": "24",
        "上市日期": "19940905",
        "新增但不影響契約的欄位": "preserved in raw",
    }
    row.update(changes)
    return row


def _tpex_row(**changes: str) -> dict[str, str]:
    row = {
        "Date": "1150912",
        "SecuritiesCompanyCode": "6488",
        "CompanyName": "環球晶圓股份有限公司",
        "SecuritiesIndustryCode": "24",
        "DateOfListing": "20150925",
    }
    row.update(changes)
    return row


def test_twse_snapshot_uses_report_date_without_backdating_current_state() -> None:
    parsed = TWSESecurityMetadataAdapter().parse(
        _payload(_twse_row()), SecurityMetadataRequest(date(2026, 9, 11))
    )

    assert parsed.report_date == date(2026, 9, 11)
    assert parsed.market == "TWSE"
    assert len(parsed.rows) == 1
    record = parsed.rows[0]
    assert record.security_code == "2330"
    assert record.observation.effective_from == date(2026, 9, 11)
    assert record.observation.listed_on == date(1994, 9, 5)
    assert record.observation.name == "台灣積體電路製造股份有限公司"
    assert record.observation.industry == "24"
    assert record.observation.delisted_on is None


def test_tpex_snapshot_maps_official_english_fields() -> None:
    parsed = TPExSecurityMetadataAdapter().parse(
        _payload(_tpex_row()), SecurityMetadataRequest()
    )

    record = parsed.rows[0]
    assert parsed.report_date == date(2026, 9, 12)
    assert parsed.market == "TPEx"
    assert record.security_code == "6488"
    assert record.observation.market == "TPEx"
    assert record.observation.listed_on == date(2015, 9, 25)


def test_twse_official_six_digit_tdr_identity_is_preserved() -> None:
    parsed = TWSESecurityMetadataAdapter().parse(
        _payload(_twse_row(公司代號="910322")), SecurityMetadataRequest()
    )

    assert parsed.rows[0].security_code == "910322"


def test_snapshot_rows_are_canonicalized_by_security_code() -> None:
    payload = json.dumps(
        [_twse_row(公司代號="2330"), _twse_row(公司代號="1101")],
        ensure_ascii=False,
    ).encode()

    parsed = TWSESecurityMetadataAdapter().parse(payload, SecurityMetadataRequest())

    assert [row.security_code for row in parsed.rows] == ["1101", "2330"]


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([], "empty_coverage"),
        ([_twse_row(公司代號="23A0")], "invalid_identity"),
        (
            [_twse_row(), _twse_row(公司名稱="duplicate")],
            "ambiguous_identity",
        ),
        (
            [_twse_row(), _twse_row(公司代號="1101", 出表日期="1150912")],
            "date_mismatch",
        ),
        ([_twse_row(上市日期="20260912")], "invalid_metadata"),
    ],
)
def test_invalid_or_ambiguous_source_rows_fail_loudly(
    rows: list[dict[str, str]], reason: str
) -> None:
    with pytest.raises(SourceDataError) as raised:
        TWSESecurityMetadataAdapter().parse(
            json.dumps(rows, ensure_ascii=False).encode(), SecurityMetadataRequest()
        )
    assert raised.value.reason_code == reason


def test_expected_report_date_is_an_explicit_guard() -> None:
    with pytest.raises(SourceDataError) as raised:
        TPExSecurityMetadataAdapter().parse(
            _payload(_tpex_row()), SecurityMetadataRequest(date(2026, 9, 11))
        )
    assert raised.value.reason_code == "date_mismatch"


def test_resource_identity_is_stable_and_official() -> None:
    twse = TWSESecurityMetadataAdapter().resource(SecurityMetadataRequest())
    tpex = TPExSecurityMetadataAdapter().resource(SecurityMetadataRequest())

    assert twse.resource_key == "twse:security-metadata:current"
    assert twse.source_uri.startswith("https://openapi.twse.com.tw/")
    assert tpex.resource_key == "tpex:security-metadata:current"
    assert tpex.source_uri.startswith("https://www.tpex.org.tw/openapi/")
