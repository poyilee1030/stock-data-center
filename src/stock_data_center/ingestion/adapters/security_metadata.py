"""Official current-snapshot security metadata adapters for TWSE and TPEx."""

from __future__ import annotations

import json
import re
from abc import ABC
from datetime import date

from stock_data_center.ingestion.models import (
    ParsedSecurityMetadata,
    SecurityMetadataRecord,
    SecurityMetadataRequest,
    SourceDataError,
    SourceResource,
)
from stock_data_center.market_data import SecurityMetadataObservation

_SECURITY_CODE = re.compile(r"^(?:[0-9]{4}|[0-9]{6})$")


class SecurityMetadataAdapter(ABC):
    """Map one official current-company snapshot into effective-dated state."""

    dataset_code = "security_metadata"
    source: str
    market: str
    version: str
    endpoint: str
    report_date_field: str
    security_code_field: str
    name_field: str
    industry_field: str
    listed_on_field: str

    def resource(self, request: SecurityMetadataRequest) -> SourceResource:
        return SourceResource(
            resource_key=f"{self.source}:security-metadata:current",
            source_uri=self.endpoint,
        )

    def parse(
        self, content: bytes, request: SecurityMetadataRequest
    ) -> ParsedSecurityMetadata:
        payload = _json_array(content)
        if not payload:
            raise SourceDataError(
                "empty_coverage", f"{self.source} returned no security metadata"
            )

        required = self.required_fields
        records: list[SecurityMetadataRecord] = []
        report_dates: set[date] = set()
        seen_codes: set[str] = set()
        for row_number, value in enumerate(payload, start=1):
            if not isinstance(value, dict):
                raise SourceDataError(
                    "schema_mismatch",
                    f"{self.source} row {row_number} is not an object",
                )
            missing = required.difference(value)
            if missing:
                raise SourceDataError(
                    "schema_mismatch",
                    f"{self.source} row {row_number} lacks fields {sorted(missing)!r}",
                )
            if not all(isinstance(value[field], str) for field in required):
                raise SourceDataError(
                    "schema_mismatch",
                    f"{self.source} row {row_number} required fields must be strings",
                )

            report_date = _roc_compact_date(
                value[self.report_date_field],
                f"{self.source} row {row_number} report date",
            )
            report_dates.add(report_date)
            security_code = value[self.security_code_field].strip()
            if not _SECURITY_CODE.fullmatch(security_code):
                raise SourceDataError(
                    "invalid_identity",
                    f"{self.source} row {row_number} has invalid company code "
                    f"{security_code!r}",
                )
            if security_code in seen_codes:
                raise SourceDataError(
                    "ambiguous_identity",
                    f"{self.source} company code {security_code!r} is duplicated",
                )
            seen_codes.add(security_code)

            name = value[self.name_field].strip()
            if not name:
                raise SourceDataError(
                    "invalid_identity",
                    f"{self.source} row {row_number} has an empty company name",
                )
            industry = value[self.industry_field].strip()
            if not industry:
                raise SourceDataError(
                    "invalid_metadata",
                    f"{self.source} row {row_number} has an empty industry code",
                )
            listed_on = _gregorian_compact_date(
                value[self.listed_on_field],
                f"{self.source} row {row_number} listing date",
            )
            if listed_on > report_date:
                raise SourceDataError(
                    "invalid_metadata",
                    f"{self.source} row {row_number} listing date is after report date",
                )

            records.append(
                SecurityMetadataRecord(
                    security_code=security_code,
                    observation=SecurityMetadataObservation(
                        effective_from=report_date,
                        market=self.market,
                        name=name,
                        industry=industry,
                        listed_on=listed_on,
                    ),
                )
            )

        if len(report_dates) != 1:
            raise SourceDataError(
                "date_mismatch",
                f"{self.source} snapshot contains multiple report dates",
            )
        report_date = report_dates.pop()
        if (
            request.expected_report_date is not None
            and report_date != request.expected_report_date
        ):
            raise SourceDataError(
                "date_mismatch",
                f"{self.source} report date {report_date} does not match expected "
                f"{request.expected_report_date}",
            )
        records.sort(key=lambda row: row.security_code)
        return ParsedSecurityMetadata(
            report_date=report_date,
            market=self.market,
            rows=tuple(records),
            source_fields=tuple(sorted(required)),
        )

    @property
    def required_fields(self) -> frozenset[str]:
        return frozenset(
            {
                self.report_date_field,
                self.security_code_field,
                self.name_field,
                self.industry_field,
                self.listed_on_field,
            }
        )


class TWSESecurityMetadataAdapter(SecurityMetadataAdapter):
    source = "twse"
    market = "TWSE"
    version = "twse-listed-company-basic:v1"
    endpoint = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    report_date_field = "出表日期"
    security_code_field = "公司代號"
    name_field = "公司名稱"
    industry_field = "產業別"
    listed_on_field = "上市日期"


class TPExSecurityMetadataAdapter(SecurityMetadataAdapter):
    source = "tpex"
    market = "TPEx"
    version = "tpex-listed-company-basic:v1"
    endpoint = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
    report_date_field = "Date"
    security_code_field = "SecuritiesCompanyCode"
    name_field = "CompanyName"
    industry_field = "SecuritiesIndustryCode"
    listed_on_field = "DateOfListing"


def _json_array(content: bytes) -> list[object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceDataError(
            "invalid_json", f"invalid source JSON: {error}"
        ) from error
    if not isinstance(payload, list):
        raise SourceDataError("schema_mismatch", "source JSON must be an array")
    return payload


def _roc_compact_date(value: str, field: str) -> date:
    normalized = value.strip()
    if len(normalized) != 7 or not normalized.isascii() or not normalized.isdigit():
        raise SourceDataError(
            "invalid_date", f"{field} is not compact ROC YYYMMDD: {value!r}"
        )
    try:
        return date(
            int(normalized[:3]) + 1911,
            int(normalized[3:5]),
            int(normalized[5:7]),
        )
    except ValueError as error:
        raise SourceDataError(
            "invalid_date", f"{field} is invalid: {value!r}"
        ) from error


def _gregorian_compact_date(value: str, field: str) -> date:
    normalized = value.strip()
    if len(normalized) != 8 or not normalized.isascii() or not normalized.isdigit():
        raise SourceDataError(
            "invalid_date", f"{field} is not Gregorian YYYYMMDD: {value!r}"
        )
    try:
        return date(
            int(normalized[:4]),
            int(normalized[4:6]),
            int(normalized[6:8]),
        )
    except ValueError as error:
        raise SourceDataError(
            "invalid_date", f"{field} is invalid: {value!r}"
        ) from error
