"""Official TWSE/TPEx historical listing-lifecycle adapters."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import date

from stock_data_center.ingestion.models import (
    ParsedSecurityLifecycle,
    SecurityLifecycleEvent,
    SecurityLifecycleRequest,
    SourceDataError,
    SourceResource,
)

_SECURITY_CODE = re.compile(r"^(?:[0-9]{4}|[0-9]{6})$")
_TWSE_LISTING_FIELDS = (
    "公司代號",
    "公司簡稱",
    "申請日期",
    "董事長",
    "申請時股本(仟元)",
    "上市審議委員會審議日期",
    "交易所董事會通過上市日期",
    "上市契約報請主管機關備查日期",
    "證期局核准上市契約日期",
    "股票上市買賣日期",
    "承銷商",
    "承銷價",
    "備註",
)
_TWSE_DELISTING_FIELDS = ("終止上市日期", "公司名稱", "上市編號")
_TPEX_LISTING_FIELDS = (
    "索引",
    "股票代號",
    "公司名稱",
    "上櫃日期",
    "每股面額",
    "公司資訊連結",
    "近期上櫃資訊連結",
)
_TPEX_DELISTING_FIELDS = (
    "股票代號",
    "公司名稱",
    "終止上櫃日期",
    "終止上櫃原因",
    "公司資料網址",
)


class SecurityLifecycleAdapter(ABC):
    dataset_code = "security_metadata"
    source: str
    market: str
    event_kind: str
    version: str
    endpoint: str

    @abstractmethod
    def resource(self, request: SecurityLifecycleRequest) -> SourceResource: ...

    @abstractmethod
    def parse(
        self, content: bytes, request: SecurityLifecycleRequest
    ) -> ParsedSecurityLifecycle: ...


class _TWSEHistoryAdapter(SecurityLifecycleAdapter):
    fields: tuple[str, ...]

    def resource(self, request: SecurityLifecycleRequest) -> SourceResource:
        _require_unbounded_request(request, self.source)
        return SourceResource(
            resource_key=f"twse:security-metadata-history:{self.event_kind}",
            source_uri=self.endpoint,
        )

    def _rows(self, content: bytes, request: SecurityLifecycleRequest) -> list[object]:
        _require_unbounded_request(request, self.source)
        payload = _json_object(content)
        if payload.get("fields") != list(self.fields):
            raise SourceDataError("schema_mismatch", "twse history fields changed")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise SourceDataError(
                "schema_mismatch", "twse history data is not an array"
            )
        if not rows:
            raise SourceDataError(
                "empty_coverage", f"twse returned no {self.event_kind} history"
            )
        return rows


class TWSEListingHistoryAdapter(_TWSEHistoryAdapter):
    source = "twse"
    market = "TWSE"
    event_kind = "listing"
    version = "twse-rwd-company-newlisting:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/company/newlisting?response=json"
    fields = _TWSE_LISTING_FIELDS

    def parse(
        self, content: bytes, request: SecurityLifecycleRequest
    ) -> ParsedSecurityLifecycle:
        rows = self._rows(content, request)
        events: list[SecurityLifecycleEvent] = []
        seen: set[tuple[str, date]] = set()
        for row_number, value in enumerate(rows, start=1):
            row = _table_row(value, len(self.fields), self.source, row_number)
            code, name = _validate_identity(row[0], row[1], self.source, row_number)
            effective_on = _roc_dot_date(
                _required_string(row[9], self.source, row_number, "listing date"),
                f"twse row {row_number} listing date",
            )
            note = _required_string(row[12], self.source, row_number, "note")
            _claim_event(seen, code, effective_on, self.source)
            events.append(
                SecurityLifecycleEvent(
                    security_code=code,
                    name=name,
                    effective_on=effective_on,
                    event_kind=self.event_kind,
                    market=self.market,
                    transfer_from_market="TPEx" if "櫃轉市" in note else None,
                )
            )
        events.sort(key=lambda item: (item.effective_on, item.security_code))
        return ParsedSecurityLifecycle(
            market=self.market,
            event_kind=self.event_kind,
            rows=tuple(events),
            source_fields=self.fields,
            source_row_count=len(rows),
        )


class TWSEDelistingHistoryAdapter(_TWSEHistoryAdapter):
    source = "twse"
    market = "TWSE"
    event_kind = "delisting"
    version = "twse-rwd-company-suspend-listing:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/company/suspendListing?response=json"
    fields = _TWSE_DELISTING_FIELDS

    def parse(
        self, content: bytes, request: SecurityLifecycleRequest
    ) -> ParsedSecurityLifecycle:
        return _parse_table_events(
            self._rows(content, request),
            fields=self.fields,
            source=self.source,
            market=self.market,
            event_kind=self.event_kind,
            code_index=2,
            name_index=1,
            date_index=0,
        )


class _TPExYearHistoryAdapter(SecurityLifecycleAdapter):
    fields: tuple[str, ...]

    def resource(self, request: SecurityLifecycleRequest) -> SourceResource:
        year = _require_year(request, self.source)
        return SourceResource(
            resource_key=f"tpex:security-metadata-history:{self.event_kind}:{year}",
            source_uri=f"{self.endpoint}?response=json&date={year}",
        )

    def _rows(self, content: bytes, request: SecurityLifecycleRequest) -> list[object]:
        year = _require_year(request, self.source)
        payload = _json_object(content)
        if payload.get("stat") != "ok":
            raise SourceDataError("source_status", "tpex history status is not ok")
        if payload.get("date") != str(year):
            raise SourceDataError(
                "date_mismatch",
                f"tpex response year {payload.get('date')!r} does not match {year}",
            )
        tables = payload.get("tables")
        if not isinstance(tables, list) or len(tables) != 1:
            raise SourceDataError(
                "schema_mismatch", "tpex history must contain exactly one table"
            )
        table = tables[0]
        if not isinstance(table, dict):
            raise SourceDataError(
                "schema_mismatch", "tpex history table is not an object"
            )
        if table.get("fields") != list(self.fields):
            raise SourceDataError("schema_mismatch", "tpex history fields changed")
        rows = table.get("data")
        if not isinstance(rows, list):
            raise SourceDataError(
                "schema_mismatch", "tpex history data is not an array"
            )
        return rows


class TPExListingHistoryAdapter(_TPExYearHistoryAdapter):
    source = "tpex"
    market = "TPEx"
    event_kind = "listing"
    version = "tpex-company-latest:v1"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/company/latest"
    fields = _TPEX_LISTING_FIELDS

    def parse(
        self, content: bytes, request: SecurityLifecycleRequest
    ) -> ParsedSecurityLifecycle:
        year = _require_year(request, self.source)
        return _parse_table_events(
            self._rows(content, request),
            fields=self.fields,
            source=self.source,
            market=self.market,
            event_kind=self.event_kind,
            code_index=1,
            name_index=2,
            date_index=3,
            expected_year=year,
        )


class TPExDelistingHistoryAdapter(_TPExYearHistoryAdapter):
    source = "tpex"
    market = "TPEx"
    event_kind = "delisting"
    version = "tpex-company-delisted:v1"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/company/deListed"
    fields = _TPEX_DELISTING_FIELDS

    def parse(
        self, content: bytes, request: SecurityLifecycleRequest
    ) -> ParsedSecurityLifecycle:
        year = _require_year(request, self.source)
        return _parse_table_events(
            self._rows(content, request),
            fields=self.fields,
            source=self.source,
            market=self.market,
            event_kind=self.event_kind,
            code_index=0,
            name_index=1,
            date_index=2,
            expected_year=year,
        )


def _parse_table_events(
    rows: list[object],
    *,
    fields: tuple[str, ...],
    source: str,
    market: str,
    event_kind: str,
    code_index: int,
    name_index: int,
    date_index: int,
    expected_year: int | None = None,
) -> ParsedSecurityLifecycle:
    events: list[SecurityLifecycleEvent] = []
    seen: set[tuple[str, date]] = set()
    for row_number, value in enumerate(rows, start=1):
        row = _table_row(value, len(fields), source, row_number)
        code, name = _validate_identity(
            row[code_index], row[name_index], source, row_number
        )
        raw_date = _required_string(
            row[date_index], source, row_number, f"{event_kind} date"
        )
        effective_on = _roc_separated_date(
            raw_date, f"{source} row {row_number} {event_kind} date"
        )
        if expected_year is not None and effective_on.year != expected_year:
            raise SourceDataError(
                "date_mismatch",
                f"{source} row {row_number} event year does not match {expected_year}",
            )
        _claim_event(seen, code, effective_on, source)
        events.append(
            SecurityLifecycleEvent(code, name, effective_on, event_kind, market)
        )
    events.sort(key=lambda item: (item.effective_on, item.security_code))
    return ParsedSecurityLifecycle(
        market=market,
        event_kind=event_kind,
        rows=tuple(events),
        source_fields=fields,
        source_row_count=len(rows),
    )


def _table_row(value: object, width: int, source: str, row_number: int) -> list[object]:
    if not isinstance(value, list) or len(value) != width:
        raise SourceDataError(
            "schema_mismatch", f"{source} row {row_number} has the wrong width"
        )
    return value


def _required_string(value: object, source: str, row_number: int, field: str) -> str:
    if not isinstance(value, str):
        raise SourceDataError(
            "schema_mismatch", f"{source} row {row_number} {field} must be a string"
        )
    return value


def _validate_identity(
    raw_code: object, raw_name: object, source: str, row_number: int
) -> tuple[str, str]:
    if not isinstance(raw_code, str) or not isinstance(raw_name, str):
        raise SourceDataError(
            "schema_mismatch",
            f"{source} row {row_number} identity fields must be strings",
        )
    code = raw_code.strip()
    name = raw_name.strip()
    if not _SECURITY_CODE.fullmatch(code):
        raise SourceDataError(
            "invalid_identity", f"{source} row {row_number} has invalid code {code!r}"
        )
    if not name:
        raise SourceDataError(
            "invalid_identity", f"{source} row {row_number} has an empty name"
        )
    return code, name


def _claim_event(
    seen: set[tuple[str, date]], code: str, effective_on: date, source: str
) -> None:
    key = (code, effective_on)
    if key in seen:
        raise SourceDataError(
            "ambiguous_identity",
            f"{source} repeats event identity {code!r} on {effective_on}",
        )
    seen.add(key)


def _json_object(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceDataError(
            "invalid_json", f"invalid source JSON: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise SourceDataError("schema_mismatch", "source JSON must be an object")
    return payload


def _roc_separated_date(value: str, field: str) -> date:
    normalized = value.strip()
    match = re.fullmatch(r"([0-9]{2,3})[-/]([0-9]{2})[-/]([0-9]{2})", normalized)
    if match is None:
        raise SourceDataError(
            "invalid_date", f"{field} is not separated ROC date: {value!r}"
        )
    return _roc_parts(*match.groups(), field, value)


def _roc_dot_date(value: str, field: str) -> date:
    normalized = value.strip()
    match = re.fullmatch(r"([0-9]{2,3})\.([0-9]{2})\.([0-9]{2})", normalized)
    if match is None:
        raise SourceDataError(
            "invalid_date", f"{field} is not dot-separated ROC date: {value!r}"
        )
    return _roc_parts(*match.groups(), field, value)


def _roc_parts(year: str, month: str, day: str, field: str, value: str) -> date:
    try:
        return date(int(year) + 1911, int(month), int(day))
    except ValueError as error:
        raise SourceDataError(
            "invalid_date", f"{field} is invalid: {value!r}"
        ) from error


def _require_year(request: SecurityLifecycleRequest, source: str) -> int:
    if request.year is None:
        raise ValueError(f"{source} lifecycle history requires a Gregorian year")
    return request.year


def _require_unbounded_request(request: SecurityLifecycleRequest, source: str) -> None:
    if request.year is not None:
        raise ValueError(f"{source} lifecycle endpoint does not accept a year")
