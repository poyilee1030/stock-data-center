"""Per-security institutional-flow adapters: 三大法人買賣超日報.

Both feeds are one whole-market file per trade date, served as JSON, in shares:

- TWSE `rwd/zh/fund/T86`, `selectType=ALLBUT0999` (every security except
  warrants). One 19-field header for the whole window; `hints` states
  `單位：股`.
- TPEx `www/zh-tw/insti/dailyTrade`, `type=Daily&sect=EW` (所有證券, 不含權證、
  牛熊證). It is the data call of the page the legacy `3itrade_hedge.php` now
  redirects to, and it carries the same 24 fields (audit §4.3). Every value
  label reads `股數`.

TPEx labels its 21 value columns with only three repeated names — 買進股數,
賣出股數, 買賣超股數. The group each triple belongs to is not in the JSON. It
is in the `<template id="theads">` of the official page that loads this table,
and that order is what `_TPEX_GROUPS` records. Because the JSON cannot prove it
on its own, the adapter accepts exactly that 24-field list and nothing else:
the page's other layout, 16 columns with no foreign-dealer group, is a format
change here rather than a variant to guess at.

TPEx also publishes the foreign-investor total (外資及陸資) and the dealer
total's buy and sell. The contract has no column for them; they are the sums of
groups that are stored, and the backfill checks that on every row.

No value is recomputed. A net is stored as published, signed; a gross
quantity must not be negative, and one that is fails the file as a format
change.
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
    InstitutionalInvestorRequest,
    InstitutionalInvestorRow,
    ParsedInstitutionalInvestor,
    SourceDataError,
    SourceResource,
)
from stock_data_center.institutional_financing.models import (
    InstitutionalInvestorObservation,
    QuantityScale,
    SourceShareQuantity,
)

_ROC_OFFSET = 1911
_INTEGER = re.compile(r"-?\d+")


class InstitutionalInvestorAdapter(ABC):
    """One market's per-security institutional flows for one trade date."""

    dataset_code = "institutional_investor"
    source: str
    market: str
    version: str
    endpoint: str
    # Header variant name -> exact published fields.
    variants: Mapping[str, tuple[str, ...]]
    # Observation field -> index into the published row.
    columns: Mapping[str, int]

    @abstractmethod
    def resource(self, request: InstitutionalInvestorRequest) -> SourceResource: ...

    @abstractmethod
    def parse(
        self, content: bytes, request: InstitutionalInvestorRequest
    ) -> ParsedInstitutionalInvestor: ...

    def _parse_table(
        self,
        request: InstitutionalInvestorRequest,
        fields: object,
        data: object,
        declared_total: object,
    ) -> ParsedInstitutionalInvestor:
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

        rows: list[InstitutionalInvestorRow] = []
        seen: set[str] = set()
        for number, raw in enumerate(data, 1):
            if not isinstance(raw, list) or len(raw) != len(header):
                raise SourceDataError(
                    "schema_mismatch", f"{self.source} row {number} has an invalid shape"
                )
            code = self._text(raw[0], number, header[0]).strip()
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
            values = {
                name: self._shares(raw[index], number, header[index])
                for name, index in self.columns.items()
            }
            try:
                observation = InstitutionalInvestorObservation(
                    trade_date=request.trade_date, **values
                )
            except ValueError as error:
                raise SourceDataError(
                    "invalid_numeric", f"{self.source} row {number} ({code}): {error}"
                ) from error
            rows.append(InstitutionalInvestorRow(code, observation))
        return ParsedInstitutionalInvestor(
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

    def _shares(self, value: object, number: int, field: str):
        text = self._text(value, number, field).strip().replace(",", "")
        if not _INTEGER.fullmatch(text):
            raise SourceDataError(
                "unrecognised_value", f"{self.source} row {number} {field} is {value!r}"
            )
        return SourceShareQuantity(Decimal(text), QuantityScale.SHARE).to_canonical()


class TWSEInstitutionalInvestorAdapter(InstitutionalInvestorAdapter):
    """`fund/T86` — 三大法人買賣超日報."""

    source = "twse_t86"
    market = "TWSE"
    version = "twse-t86:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/fund/T86"
    variants = MappingProxyType({
        "t86_19": (
            "證券代號", "證券名稱",
            "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
            "外陸資買賣超股數(不含外資自營商)",
            "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
            "投信買進股數", "投信賣出股數", "投信買賣超股數",
            "自營商買賣超股數",
            "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)",
            "自營商買賣超股數(自行買賣)",
            "自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)",
            "三大法人買賣超股數",
        ),
    })
    columns = MappingProxyType({
        "foreign_buy": 2, "foreign_sell": 3, "foreign_net": 4,
        "foreign_dealer_buy": 5, "foreign_dealer_sell": 6, "foreign_dealer_net": 7,
        "trust_buy": 8, "trust_sell": 9, "trust_net": 10,
        "dealer_net": 11,
        "dealer_self_buy": 12, "dealer_self_sell": 13, "dealer_self_net": 14,
        "dealer_hedge_buy": 15, "dealer_hedge_sell": 16, "dealer_hedge_net": 17,
        "total_net": 18,
    })

    def resource(self, request: InstitutionalInvestorRequest) -> SourceResource:
        query = urlencode(
            {
                "date": request.trade_date.strftime("%Y%m%d"),
                "selectType": "ALLBUT0999",
                "response": "json",
            }
        )
        return SourceResource(
            resource_key=f"{self.source}:institutional:{request.trade_date.isoformat()}",
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: InstitutionalInvestorRequest
    ) -> ParsedInstitutionalInvestor:
        payload = _json_object(content)
        if payload.get("stat") != "OK":
            if "data" not in payload and "fields" not in payload:
                raise SourceDataError(
                    "no_data_for_date",
                    f"TWSE has no institutional flows for "
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
        return self._parse_table(
            request, payload.get("fields"), payload.get("data"), payload.get("total")
        )


_TPEX_TRIPLE = ("買進股數", "賣出股數", "買賣超股數")
# Group order from the official page's <template id="theads">.
_TPEX_GROUPS = (
    "外資及陸資(不含外資自營商)", "外資自營商", "外資及陸資", "投信",
    "自營商(自行買賣)", "自營商(避險)", "自營商",
)


class TPExInstitutionalInvestorAdapter(InstitutionalInvestorAdapter):
    """`insti/dailyTrade` — 三大法人買賣明細資訊."""

    source = "tpex_insti_daily_trade"
    market = "TPEx"
    version = "tpex-insti-daily-trade:v1"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
    variants = MappingProxyType({
        "daily_trade_24": (
            "代號", "名稱", *(_TPEX_TRIPLE * len(_TPEX_GROUPS)), "三大法人買賣超股數合計",
        ),
    })
    # Index = 2 + 3 × group + position; groups 2 (外資及陸資) and the buy and
    # sell of 6 (自營商) have no column in the contract.
    columns = MappingProxyType({
        "foreign_buy": 2, "foreign_sell": 3, "foreign_net": 4,
        "foreign_dealer_buy": 5, "foreign_dealer_sell": 6, "foreign_dealer_net": 7,
        "trust_buy": 11, "trust_sell": 12, "trust_net": 13,
        "dealer_self_buy": 14, "dealer_self_sell": 15, "dealer_self_net": 16,
        "dealer_hedge_buy": 17, "dealer_hedge_sell": 18, "dealer_hedge_net": 19,
        "dealer_net": 22,
        "total_net": 23,
    })

    def resource(self, request: InstitutionalInvestorRequest) -> SourceResource:
        query = urlencode(
            {
                "date": request.trade_date.strftime("%Y/%m/%d"),
                "type": "Daily",
                "sect": "EW",
                "response": "json",
            }
        )
        return SourceResource(
            resource_key=f"{self.source}:institutional:{request.trade_date.isoformat()}",
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: InstitutionalInvestorRequest
    ) -> ParsedInstitutionalInvestor:
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
        # The data table, then the page's second (16-column) table, always
        # empty in the window. Content there would be a layout this adapter
        # does not read.
        if (
            not isinstance(tables, list)
            or not tables
            or not isinstance(tables[0], dict)
            or any(table != {} for table in tables[1:])
        ):
            raise SourceDataError(
                "schema_mismatch",
                "TPEx must publish one data table and nothing in any other",
            )
        table = tables[0]
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


def _json_object(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceDataError("invalid_json", f"invalid source JSON: {error}") from error
    if not isinstance(payload, dict):
        raise SourceDataError("schema_mismatch", "source JSON must be an object")
    return payload
