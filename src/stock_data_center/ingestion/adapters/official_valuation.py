"""Official-valuation adapters: the exchanges' own PE, PB and yield tables.

Both feeds are one whole-market file per trade date, served as JSON:

- TWSE `rwd/zh/afterTrading/BWIBBU_d`. Two headers are real: the current eight
  fields, and a five-field one (no dividend year, no report period) that TWSE
  serves for older dates and that the legacy archive holds for 2025-06-24.
- TPEx `www/zh-tw/afterTrading/peQryDate`, the data call of the page the legacy
  `pera.php` now redirects to. It is the legacy CSV row for row (audit §4.6),
  and it states its own date and row count. `財報年/季` appears from
  2025-01-02.

The two exchanges write the report period differently — TWSE `115/2`, TPEx
`115Q2` — so each is parsed by its own pattern and stored as `2026Q2`. A value
in the other exchange's format is a format change, not a synonym.

`-` (TWSE) and `N/A` (TPEx) are each source's own "not computed" marker and
store as NULL. TPEx also prints its first listed day's ratios as `"null"`
(2021-07-26 → 2022-11-02, 6840 and others) and later as `"0"` (6720,
2024-12-04). No official note explains either, and both store as NULL by owner
decision (ROADMAP 18-c). The same decision makes a ratio printed as zero mean
not computed on TWSE too, where `null` stays a format change. A negative ratio,
yield or dividend rejects its own row rather than being guessed at, and the
rest of the file imports.

Neither feed states a unit. The ratios are multiples, `殖利率(%)` is
percentage points, and TPEx `每股股利` is TWD per share by the formula its notes
publish: 殖利率 = 每股股利 / 收盤價 × 100%.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from urllib.parse import urlencode

from stock_data_center.ingestion.models import (
    OfficialValuationRequest,
    OfficialValuationRow,
    ParsedOfficialValuation,
    RejectedValuationRow,
    SourceDataError,
    SourceResource,
)
from stock_data_center.ingestion.observations import (
    OfficialValuationObservation,
    TwdAmount,
)

_ROC_OFFSET = 1911


class _RowRejected(Exception):
    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code


class OfficialValuationAdapter(ABC):
    """One market's published valuation ratios for one trade date."""

    dataset_code = "official_valuation"
    source: str
    market: str
    version: str
    endpoint: str
    # Header variant name -> exact published fields.
    variants: Mapping[str, tuple[str, ...]]
    # This source's documented "not computed" marker, valid in any value.
    not_computed: frozenset[str]
    # Markers accepted for 本益比 and 股價淨值比 only (owner decision).
    ratio_not_computed: frozenset[str] = frozenset()
    # A ratio printed as exactly zero means not computed (owner decision).
    zero_ratio_not_computed: bool = True
    report_period_pattern: re.Pattern[str]

    @abstractmethod
    def resource(self, request: OfficialValuationRequest) -> SourceResource: ...

    @abstractmethod
    def parse(
        self, content: bytes, request: OfficialValuationRequest
    ) -> ParsedOfficialValuation: ...

    def _parse_table(
        self,
        request: OfficialValuationRequest,
        fields: object,
        data: object,
        declared_total: object,
    ) -> ParsedOfficialValuation:
        variant = next(
            (
                name
                for name, expected in self.variants.items()
                if isinstance(fields, list) and tuple(fields) == expected
            ),
            None,
        )
        if variant is None:
            raise SourceDataError(
                "schema_mismatch",
                f"{self.source} published an unknown header {fields!r}",
            )
        header = self.variants[variant]
        if not isinstance(data, list):
            raise SourceDataError("schema_mismatch", f"{self.source} data is not a list")
        if not data:
            raise SourceDataError(
                "no_data_for_date",
                f"{self.source} published no row for {request.trade_date.isoformat()}",
            )
        if (
            not isinstance(declared_total, int)
            or isinstance(declared_total, bool)
            or declared_total != len(data)
        ):
            raise SourceDataError(
                "schema_mismatch",
                f"{self.source} declares {declared_total!r} rows but lists {len(data)}",
            )

        rows: list[OfficialValuationRow] = []
        rejected: list[RejectedValuationRow] = []
        seen: set[str] = set()
        for number, raw in enumerate(data, 1):
            if not isinstance(raw, list) or len(raw) != len(header):
                raise SourceDataError(
                    "schema_mismatch", f"{self.source} row {number} has an invalid shape"
                )
            cells = dict(zip(header, raw, strict=True))
            code = self._cell_text(cells[header[0]], number, header[0]).strip()
            if not code:
                raise SourceDataError(
                    "invalid_identity", f"{self.source} row {number} has no security code"
                )
            if code in seen:
                raise SourceDataError(
                    "duplicate_security",
                    f"{self.source} lists {code} twice for {request.trade_date.isoformat()}",
                )
            seen.add(code)
            try:
                observation = self._observation(request, cells, number)
            except _RowRejected as rejection:
                rejected.append(
                    RejectedValuationRow(
                        row_number=number,
                        security_code=code,
                        reason_code=rejection.reason_code,
                        detail=(
                            f"{self.source} {request.trade_date.isoformat()} "
                            f"row {number} ({code}): {rejection}"
                        ),
                    )
                )
                continue
            rows.append(OfficialValuationRow(code, observation))
        return ParsedOfficialValuation(
            market=self.market,
            trade_date=request.trade_date,
            rows=tuple(rows),
            rejected=tuple(rejected),
            header_variant=variant,
            source_fields=header,
        )

    @abstractmethod
    def _observation(
        self, request: OfficialValuationRequest, cells: dict[str, object], number: int
    ) -> OfficialValuationObservation: ...

    def _build(
        self,
        request: OfficialValuationRequest,
        *,
        pe: Decimal | None,
        pb: Decimal | None,
        dividend_yield: Decimal | None,
        dividend_year: int | None,
        dividend_per_share: Decimal | None,
        report_period: str | None,
    ) -> OfficialValuationObservation:
        for name, value in (("本益比", pe), ("股價淨值比", pb)):
            if value is not None and value <= 0:
                raise _RowRejected(
                    "nonpositive_ratio",
                    f"{name} is {value}, which is neither a ratio nor one of "
                    f"the source's not-computed markers "
                    f"{sorted(self.not_computed | self.ratio_not_computed)!r}",
                )
        for name, value in (("殖利率(%)", dividend_yield), ("每股股利", dividend_per_share)):
            if value is not None and value < 0:
                raise _RowRejected("negative_value", f"{name} is {value}")
        if all(v is None for v in (pe, pb, dividend_yield, dividend_per_share)):
            raise _RowRejected("no_value_published", "no valuation value is published")
        try:
            return OfficialValuationObservation(
                trade_date=request.trade_date,
                pe_ratio=pe,
                pb_ratio=pb,
                dividend_yield=dividend_yield,
                dividend_year=dividend_year,
                dividend_per_share=(
                    None if dividend_per_share is None else TwdAmount(dividend_per_share)
                ),
                report_period=report_period,
            )
        except ValueError as error:
            raise SourceDataError("invalid_numeric", str(error)) from error

    def _cell_text(self, value: object, number: int, field: str) -> str:
        if not isinstance(value, str):
            raise SourceDataError(
                "schema_mismatch",
                f"{self.source} row {number} {field} is not text: {value!r}",
            )
        return value

    def _number(
        self, value: object, number: int, field: str,
        markers: frozenset[str] = frozenset(),
    ) -> Decimal | None:
        text = self._cell_text(value, number, field).strip()
        if text in self.not_computed or text in markers:
            return None
        normalized = text.replace(",", "")
        if not re.fullmatch(r"-?\d+(\.\d+)?", normalized):
            raise SourceDataError(
                "unrecognised_value",
                f"{self.source} row {number} {field} is {text!r}",
            )
        try:
            return Decimal(normalized)
        except InvalidOperation as error:  # pragma: no cover - guarded above
            raise SourceDataError("unrecognised_value", str(error)) from error

    def _ratio(self, value: object, number: int, field: str) -> Decimal | None:
        result = self._number(value, number, field, self.ratio_not_computed)
        if self.zero_ratio_not_computed and result == 0:
            return None
        return result

    def _dividend_year(self, value: object, number: int) -> int:
        if isinstance(value, bool) or not (
            isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit())
        ):
            raise SourceDataError(
                "unrecognised_value",
                f"{self.source} row {number} 股利年度 is {value!r}",
            )
        roc = int(value)
        if not 1 <= roc <= 9999 - _ROC_OFFSET:
            raise SourceDataError(
                "unrecognised_value", f"{self.source} row {number} 股利年度 is {roc}"
            )
        return roc + _ROC_OFFSET

    def _report_period(self, value: object, number: int) -> str:
        text = self._cell_text(value, number, "財報年/季").strip()
        match = self.report_period_pattern.fullmatch(text)
        if match is None:
            raise SourceDataError(
                "unrecognised_value",
                f"{self.source} row {number} 財報年/季 is {text!r}",
            )
        return f"{int(match['year']) + _ROC_OFFSET}Q{match['quarter']}"


class TWSEOfficialValuationAdapter(OfficialValuationAdapter):
    """`BWIBBU_d` — 個股日本益比、殖利率及股價淨值比."""

    source = "twse_bwibbu_d"
    market = "TWSE"
    version = "twse-bwibbu-d:v2"
    endpoint = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
    variants = MappingProxyType({
        "bwibbu_8": (
            "證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比",
            "股價淨值比", "財報年/季",
        ),
        "bwibbu_5": ("證券代號", "證券名稱", "本益比", "殖利率(%)", "股價淨值比"),
    })
    not_computed = frozenset({"-"})
    report_period_pattern = re.compile(r"(?P<year>\d{2,3})/(?P<quarter>[1-4])")

    def resource(self, request: OfficialValuationRequest) -> SourceResource:
        query = urlencode(
            {
                "date": request.trade_date.strftime("%Y%m%d"),
                "selectType": "ALL",
                "response": "json",
            }
        )
        return SourceResource(
            resource_key=f"{self.source}:valuation:{request.trade_date.isoformat()}",
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: OfficialValuationRequest
    ) -> ParsedOfficialValuation:
        payload = _json_object(content)
        if payload.get("stat") != "OK":
            if "data" not in payload and "fields" not in payload:
                raise SourceDataError(
                    "no_data_for_date",
                    f"TWSE has no valuation for {request.trade_date.isoformat()}: "
                    f"{payload.get('stat')!r}",
                )
            raise SourceDataError(
                "source_status", f"TWSE response status: {payload.get('stat')!r}"
            )
        if payload.get("date") != request.trade_date.strftime("%Y%m%d"):
            raise SourceDataError(
                "date_mismatch",
                f"TWSE answered for {payload.get('date')!r}, not "
                f"{request.trade_date.isoformat()}",
            )
        return self._parse_table(
            request, payload.get("fields"), payload.get("data"), payload.get("total")
        )

    def _observation(self, request, cells, number):
        full = "財報年/季" in cells
        return self._build(
            request,
            pe=self._ratio(cells["本益比"], number, "本益比"),
            pb=self._ratio(cells["股價淨值比"], number, "股價淨值比"),
            dividend_yield=self._number(cells["殖利率(%)"], number, "殖利率(%)"),
            dividend_year=(
                self._dividend_year(cells["股利年度"], number) if full else None
            ),
            dividend_per_share=None,
            report_period=self._report_period(cells["財報年/季"], number) if full else None,
        )


class TPExOfficialValuationAdapter(OfficialValuationAdapter):
    """`afterTrading/peQryDate` — 上櫃股票個股本益比、殖利率、股價淨值比."""

    source = "tpex_pe_qry_date"
    market = "TPEx"
    version = "tpex-pe-qry-date:v3"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate"
    _fields = (
        "股票代號", "公司名稱", "本益比", "每股股利", "股利年度", "殖利率(%)",
        "股價淨值比",
    )
    variants = MappingProxyType({
        "pe_qry_date_8": (*_fields, "財報年/季"),
        # Before 2025-01-02 the file has no report period at all.
        "pe_qry_date_7": _fields,
    })
    not_computed = frozenset({"N/A"})
    # First listed day, 2021-07-26 → 2022-11-02 (audit §4.6).
    ratio_not_computed = frozenset({"null"})
    report_period_pattern = re.compile(r"(?P<year>\d{2,3})Q(?P<quarter>[1-4])")

    def resource(self, request: OfficialValuationRequest) -> SourceResource:
        query = urlencode(
            {"date": request.trade_date.strftime("%Y/%m/%d"), "response": "json"}
        )
        return SourceResource(
            resource_key=f"{self.source}:valuation:{request.trade_date.isoformat()}",
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: OfficialValuationRequest
    ) -> ParsedOfficialValuation:
        payload = _json_object(content)
        if payload.get("stat") != "ok":
            raise SourceDataError(
                "source_status", f"TPEx response status: {payload.get('stat')!r}"
            )
        if payload.get("date") != request.trade_date.strftime("%Y%m%d"):
            raise SourceDataError(
                "date_mismatch",
                f"TPEx answered for {payload.get('date')!r}, not "
                f"{request.trade_date.isoformat()}",
            )
        tables = payload.get("tables")
        if not isinstance(tables, list) or len(tables) != 1 or not isinstance(
            tables[0], dict
        ):
            raise SourceDataError("schema_mismatch", "TPEx must publish exactly one table")
        table = tables[0]
        # The table states its own date as well; both must agree.
        roc = request.trade_date.year - _ROC_OFFSET
        expected = f"{roc}/{request.trade_date:%m/%d}"
        if table.get("date") != expected:
            raise SourceDataError(
                "date_mismatch",
                f"TPEx table is dated {table.get('date')!r}, not {expected}",
            )
        return self._parse_table(
            request, table.get("fields"), table.get("data"), table.get("totalCount")
        )

    def _observation(self, request, cells, number):
        return self._build(
            request,
            pe=self._ratio(cells["本益比"], number, "本益比"),
            pb=self._ratio(cells["股價淨值比"], number, "股價淨值比"),
            dividend_yield=self._number(cells["殖利率(%)"], number, "殖利率(%)"),
            dividend_year=self._dividend_year(cells["股利年度"], number),
            dividend_per_share=self._number(cells["每股股利"], number, "每股股利"),
            report_period=(
                self._report_period(cells["財報年/季"], number)
                if "財報年/季" in cells
                else None
            ),
        )


def _json_object(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceDataError("invalid_json", f"invalid source JSON: {error}") from error
    if not isinstance(payload, dict):
        raise SourceDataError("schema_mismatch", "source JSON must be an object")
    return payload
