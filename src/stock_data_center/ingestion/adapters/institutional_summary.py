"""Institutional market-summary adapters: 三大法人買賣金額統計.

Both feeds are one small whole-market table per trade date, served as JSON, in
TWD:

- TWSE `rwd/zh/fund/BFI82U`, `type=day`. Six rows, `hints` states `單位：元`.
- TPEx `www/zh-tw/insti/summary`. It is the data call of the page the legacy
  `3itrdsum.php` now redirects to, with the same four fields (audit §4.3).
  Eight rows; every amount label reads `(元)`.

Each row is one institution, identified by the name the exchange publishes.
The two exchanges name and group their rows differently — TPEx publishes the
foreign and dealer subtotals that TWSE does not — and nothing maps one onto the
other: each market keeps its own names, as the indices do (Step 18).

TPEx indents a subgroup under its subtotal with U+3000. That is page layout,
kept in the raw artifact and stripped from the stored name. The row order is
what shows the hierarchy, so the adapter accepts exactly the published order
and nothing else: a new, missing or moved row is a format change to read, not a
variant to guess at. `三大法人合計*` keeps its asterisk; it refers to the note
that the foreign-dealer rows are already inside the dealer rows and so are not
added into the total, the same exclusion TWSE's `合計` makes.

No value is recomputed. A net is stored as published, signed; a gross amount
must not be negative, and one that is fails the file.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from urllib.parse import urlencode

from stock_data_center.ingestion.models import (
    InstitutionalMarketSummaryRequest,
    ParsedInstitutionalMarketSummary,
    SourceDataError,
    SourceResource,
)
from stock_data_center.institutional_financing.models import (
    InstitutionalMarketSummaryObservation,
)

_ROC_OFFSET = 1911
_INTEGER = re.compile(r"-?\d+")


class InstitutionalMarketSummaryAdapter(ABC):
    """One market's institutional trading-value summary for one trade date."""

    dataset_code = "institutional_market_summary"
    source: str
    market: str
    version: str
    endpoint: str
    # Header variant name -> exact published fields.
    variants: Mapping[str, tuple[str, ...]]
    # Every institution, in published order, after the layout indent is removed.
    institutions: tuple[str, ...]

    @abstractmethod
    def resource(self, request: InstitutionalMarketSummaryRequest) -> SourceResource: ...

    @abstractmethod
    def parse(
        self, content: bytes, request: InstitutionalMarketSummaryRequest
    ) -> ParsedInstitutionalMarketSummary: ...

    def _resource(
        self, request: InstitutionalMarketSummaryRequest, query: Mapping[str, str]
    ) -> SourceResource:
        return SourceResource(
            resource_key=(
                f"{self.source}:institutional_summary:{request.trade_date.isoformat()}"
            ),
            source_uri=f"{self.endpoint}?{urlencode(query)}",
        )

    def _parse_table(
        self,
        request: InstitutionalMarketSummaryRequest,
        fields: object,
        data: object,
    ) -> ParsedInstitutionalMarketSummary:
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
        for number, raw in enumerate(data, 1):
            if not isinstance(raw, list) or len(raw) != len(header):
                raise SourceDataError(
                    "schema_mismatch", f"{self.source} row {number} has an invalid shape"
                )
        names = tuple(self._text(raw[0], number, header[0]).strip()
                      for number, raw in enumerate(data, 1))
        if names != self.institutions:
            raise SourceDataError(
                "schema_mismatch",
                f"{self.source} published the institutions {names!r}, "
                f"not {self.institutions!r}",
            )

        rows: list[InstitutionalMarketSummaryObservation] = []
        for number, (name, raw) in enumerate(zip(names, data, strict=True), 1):
            buy, sell, net = (
                self._amount(raw[index], number, header[index]) for index in (1, 2, 3)
            )
            try:
                rows.append(
                    InstitutionalMarketSummaryObservation(
                        trade_date=request.trade_date,
                        market=self.market,
                        institution=name,
                        buy=buy,
                        sell=sell,
                        net=net,
                    )
                )
            except ValueError as error:
                raise SourceDataError(
                    "invalid_numeric", f"{self.source} row {number} ({name}): {error}"
                ) from error
        return ParsedInstitutionalMarketSummary(
            market=self.market,
            trade_date=request.trade_date,
            rows=tuple(rows),
            header_variant=variant,
            source_fields=header,
        )

    def _text(self, value: object, number: int, field: str) -> str:
        if not isinstance(value, str):
            raise SourceDataError(
                "schema_mismatch",
                f"{self.source} row {number} {field} is not text: {value!r}",
            )
        return value

    def _amount(self, value: object, number: int, field: str) -> Decimal:
        text = self._text(value, number, field).strip().replace(",", "")
        if not _INTEGER.fullmatch(text):
            raise SourceDataError(
                "unrecognised_value", f"{self.source} row {number} {field} is {value!r}"
            )
        return Decimal(text)


class TWSEInstitutionalMarketSummaryAdapter(InstitutionalMarketSummaryAdapter):
    """`fund/BFI82U` — 三大法人買賣金額統計表."""

    source = "twse_bfi82u"
    market = "TWSE"
    version = "twse-bfi82u:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
    variants = MappingProxyType({
        "bfi82u_4": ("單位名稱", "買進金額", "賣出金額", "買賣差額"),
    })
    institutions = (
        "自營商(自行買賣)", "自營商(避險)", "投信",
        "外資及陸資(不含外資自營商)", "外資自營商", "合計",
    )

    def resource(self, request: InstitutionalMarketSummaryRequest) -> SourceResource:
        return self._resource(
            request,
            {
                "type": "day",
                "dayDate": request.trade_date.strftime("%Y%m%d"),
                "response": "json",
            },
        )

    def parse(
        self, content: bytes, request: InstitutionalMarketSummaryRequest
    ) -> ParsedInstitutionalMarketSummary:
        payload = _json_object(content)
        if payload.get("stat") != "OK":
            if "data" not in payload and "fields" not in payload:
                raise SourceDataError(
                    "no_data_for_date",
                    f"TWSE has no institutional summary for "
                    f"{request.trade_date.isoformat()}: {payload.get('stat')!r}",
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
        # The amount labels carry no unit; the response states it once.
        if payload.get("hints") != "單位：元":
            raise SourceDataError(
                "schema_mismatch",
                f"TWSE states the unit as {payload.get('hints')!r}, not 單位：元",
            )
        return self._parse_table(request, payload.get("fields"), payload.get("data"))


class TPExInstitutionalMarketSummaryAdapter(InstitutionalMarketSummaryAdapter):
    """`insti/summary` — 三大法人買賣金額彙總表."""

    source = "tpex_insti_summary"
    market = "TPEx"
    version = "tpex-insti-summary:v1"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/insti/summary"
    variants = MappingProxyType({
        "summary_4": ("單位名稱", "買進金額(元)", "賣出金額(元)", "買賣超(元)"),
    })
    institutions = (
        "外資及陸資合計", "外資及陸資(不含自營商)", "外資自營商", "投信",
        "自營商合計", "自營商(自行買賣)", "自營商(避險)", "三大法人合計*",
    )

    def resource(self, request: InstitutionalMarketSummaryRequest) -> SourceResource:
        return self._resource(
            request,
            {"date": request.trade_date.strftime("%Y/%m/%d"), "response": "json"},
        )

    def parse(
        self, content: bytes, request: InstitutionalMarketSummaryRequest
    ) -> ParsedInstitutionalMarketSummary:
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
        roc = request.trade_date.year - _ROC_OFFSET
        expected = f"{roc}/{request.trade_date:%m/%d}"
        if table.get("date") != expected:
            raise SourceDataError(
                "date_mismatch",
                f"TPEx table is dated {table.get('date')!r}, not {expected}",
            )
        return self._parse_table(request, table.get("fields"), table.get("data"))


def _json_object(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceDataError("invalid_json", f"invalid source JSON: {error}") from error
    if not isinstance(payload, dict):
        raise SourceDataError("schema_mismatch", "source JSON must be an object")
    return payload
