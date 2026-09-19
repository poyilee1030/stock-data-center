"""Margin-trading adapters: 融資融券餘額.

One whole-market table per trade date, in lots:

- TWSE `rwd/zh/marginTrading/MI_MARGN`, `selectType=ALL`. Two tables: the
  market summary `信用交易統計`, whose rows read `融資(交易單位)` and
  `融券(交易單位)` — the only place the unit is stated — and the per-security
  `融資融券彙總`, 16 fields whose labels repeat for the margin and the short
  side, so columns are read by position. TWSE publishes no utilization ratio.
- TPEx `www/zh-tw/margin/balance`, the JSON of the legacy `margin_bal` page
  (audit §4.5), 20 fields labelled `(張)`. Its short side lists 券賣 before 券買.

A lot is 1,000 shares except where TWSE's own note says otherwise: 除境外指數
股票型基金及外國股票第二上市外，餘交易單位皆為千股 (MI_INDEX). The exceptions
this window holds are listed in `TWSE_LOT_SHARES`, each with its evidence;
nothing is inferred from magnitude at parse time (CLAUDE.md §72). Both exchanges
stop margin at 25% of listed shares, so the reconciliation compares every
next-day limit with 25% of the issued shares Step 20-d stored, which is how
008201 was found; a new exception fails that check until it is listed here.

A utilization ratio is stored as published, above 100 included: the stop takes
effect on the next business day, so one day's buying can overshoot the limit
(00989B, 2026-07-14: 103.1%).

Not stored: names, TPEx's 資屬證金 and 券屬證金, and both exchanges' status
notes (`O` 停止融資, `X` 停止融券, …), which have no contract column. The raw
artifact keeps them. No value is recomputed.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from urllib.parse import urlencode

from stock_data_center.ingestion.models import (
    MarginTradingRequest,
    MarginTradingRow,
    ParsedMarginTrading,
    SourceDataError,
    SourceResource,
)
from stock_data_center.institutional_financing.models import (
    MarginTradingObservation,
    QuantityScale,
    SourceShareQuantity,
)

_ROC_OFFSET = 1911
_LOTS = re.compile(r"\d{1,3}(?:,\d{3})*|\d+")
_RATIOS = frozenset({"margin_utilization_ratio", "short_utilization_ratio"})

# Securities whose TWSE trading unit is not 1,000 shares: code -> (shares per
# lot, first and last date the evidence covers). Outside those dates the code
# fails its file instead of being converted: a code reused by a new security
# would otherwise be stored ten times off (review of #34).
TWSE_LOT_SHARES = MappingProxyType({
    # BP上證50, an offshore ETF (ISIN HK0000052297), listed until 2022-07-08.
    # Its next-day limit × 100 equals 25% of its issued units on all 612 of its
    # dates; × 1,000 would be ten times that.
    "008201": (100, date(2020, 1, 2), date(2022, 7, 8)),
})


class MarginTradingAdapter(ABC):
    """One market's per-security margin trading for one trade date."""

    dataset_code = "margin_trading"
    source: str
    market: str
    version: str
    endpoint: str
    variants: Mapping[str, tuple[str, ...]]
    # Observation field -> index into the published row.
    columns: Mapping[str, int]
    # Shares per lot, with its dates, for securities not trading in lots of 1,000.
    lot_shares: Mapping[str, tuple[int, date, date]] = MappingProxyType({})

    @abstractmethod
    def resource(self, request: MarginTradingRequest) -> SourceResource: ...

    @abstractmethod
    def parse(
        self, content: bytes, request: MarginTradingRequest
    ) -> ParsedMarginTrading: ...

    def _parse_rows(
        self, request: MarginTradingRequest, fields: object, data: object
    ) -> ParsedMarginTrading:
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
                "schema_mismatch", f"{self.source} published an unknown header {fields!r}"
            )
        header = self.variants[variant]
        if not isinstance(data, list) or not data:
            raise SourceDataError("schema_mismatch", f"{self.source} data is not a non-empty list")
        rows: list[MarginTradingRow] = []
        seen: set[str] = set()
        for number, raw in enumerate(data, 1):
            if not isinstance(raw, list) or len(raw) != len(header):
                raise SourceDataError(
                    "schema_mismatch", f"{self.source} row {number} has an invalid shape"
                )
            code = self._text(raw[0], number).strip()
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
            lot = self._lot_size(code, request.trade_date)
            values = {
                name: (
                    self._ratio(raw[index], number, name)
                    if name in _RATIOS
                    else self._lots(raw[index], number, name, lot)
                )
                for name, index in self.columns.items()
            }
            try:
                observation = MarginTradingObservation(trade_date=request.trade_date, **values)
            except ValueError as error:
                raise SourceDataError(
                    "invalid_numeric", f"{self.source} row {number} ({code}): {error}"
                ) from error
            rows.append(MarginTradingRow(code, observation))
        return ParsedMarginTrading(
            market=self.market,
            trade_date=request.trade_date,
            rows=tuple(rows),
            header_variant=variant,
            source_fields=header,
        )

    def _lot_size(self, code: str, day: date) -> int | None:
        """Shares per lot where listed; None for the default lot of 1,000."""
        exception = self.lot_shares.get(code)
        if exception is None:
            return None
        shares, first, last = exception
        if not first <= day <= last:
            raise SourceDataError(
                "unverified_trading_unit",
                f"{self.source} lists {code} on {day.isoformat()}, outside the "
                f"{first.isoformat()}..{last.isoformat()} its {shares}-share lot is proven for",
            )
        return shares

    def _text(self, value: object, number: int) -> str:
        if not isinstance(value, str):
            raise SourceDataError(
                "schema_mismatch", f"{self.source} row {number} value {value!r} is not text"
            )
        return value

    def _lots(self, value: object, number: int, field: str, lot_shares: int | None):
        text = self._text(value, number).strip()
        if not _LOTS.fullmatch(text):
            raise SourceDataError(
                "unrecognised_value", f"{self.source} row {number} {field} is {value!r}"
            )
        lots = Decimal(text.replace(",", ""))
        if lot_shares is None:
            return SourceShareQuantity(lots, QuantityScale.LOT).to_canonical()
        return SourceShareQuantity(lots * lot_shares, QuantityScale.SHARE).to_canonical()

    def _ratio(self, value: object, number: int, field: str) -> Decimal:
        text = self._text(value, number).strip()
        try:
            ratio = Decimal(text)
        except InvalidOperation as error:
            raise SourceDataError(
                "unrecognised_value", f"{self.source} row {number} {field} is {value!r}"
            ) from error
        if not ratio.is_finite():
            raise SourceDataError(
                "unrecognised_value", f"{self.source} row {number} {field} is {value!r}"
            )
        return ratio


def _json_object(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceDataError("invalid_json", f"invalid source JSON: {error}") from error
    if not isinstance(payload, dict):
        raise SourceDataError("schema_mismatch", "source JSON must be an object")
    return payload


_TWSE_SUMMARY_UNITS = ("融資(交易單位)", "融券(交易單位)")


class TWSEMarginTradingAdapter(MarginTradingAdapter):
    """`marginTrading/MI_MARGN` — 融資融券彙總."""

    source = "twse_mi_margn"
    market = "TWSE"
    version = "twse-mi-margn:v3"
    lot_shares = TWSE_LOT_SHARES
    endpoint = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
    variants = MappingProxyType({
        "mi_margn_16": (
            "代號", "名稱",
            "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額",
            "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "次一營業日限額",
            "資券互抵", "註記",
        ),
    })
    columns = MappingProxyType({
        "margin_buy": 2, "margin_sell": 3, "margin_cash_repayment": 4,
        "margin_previous_balance": 5, "margin_balance": 6, "margin_next_limit": 7,
        "short_buy": 8, "short_sell": 9, "short_stock_repayment": 10,
        "short_previous_balance": 11, "short_balance": 12, "short_next_limit": 13,
        "offset_balance": 14,
    })

    def resource(self, request: MarginTradingRequest) -> SourceResource:
        query = urlencode(
            {
                "date": request.trade_date.strftime("%Y%m%d"),
                "selectType": "ALL",
                "response": "json",
            }
        )
        return SourceResource(
            resource_key=f"{self.source}:margin_trading:{request.trade_date.isoformat()}",
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(self, content: bytes, request: MarginTradingRequest) -> ParsedMarginTrading:
        payload = _json_object(content)
        if payload.get("stat") != "OK":
            # A closed date answers an apology and no tables at all.
            if "tables" not in payload:
                raise SourceDataError(
                    "no_data_for_date",
                    f"TWSE has no margin trading for {request.trade_date.isoformat()}: "
                    f"{payload.get('stat')!r}",
                )
            raise SourceDataError("source_status", f"TWSE response status: {payload.get('stat')!r}")
        if payload.get("date") != request.trade_date.strftime("%Y%m%d"):
            raise SourceDataError(
                "date_mismatch",
                f"TWSE answered for {payload.get('date')!r}, not {request.trade_date.isoformat()}",
            )
        tables = payload.get("tables")
        if not isinstance(tables, list):
            raise SourceDataError("schema_mismatch", "TWSE tables is not a list")
        summary = [t for t in tables if isinstance(t, dict) and "信用交易統計" in str(t.get("title"))]
        detail = [t for t in tables if isinstance(t, dict) and "融資融券彙總" in str(t.get("title"))]
        if len(summary) != 1 or len(detail) != 1:
            raise SourceDataError(
                "schema_mismatch", "TWSE must publish one summary and one per-security table"
            )
        labels = tuple(
            row[0] for row in summary[0].get("data") or [] if isinstance(row, list) and row
        )[:2]
        if labels != _TWSE_SUMMARY_UNITS:
            raise SourceDataError(
                "unit_declaration_changed",
                f"TWSE summary rows read {labels!r}, not {_TWSE_SUMMARY_UNITS!r}",
            )
        return self._parse_rows(request, detail[0].get("fields"), detail[0].get("data"))


class TPExMarginTradingAdapter(MarginTradingAdapter):
    """TPEx `margin/balance` — 上櫃股票融資融券餘額."""

    source = "tpex_margin_balance"
    market = "TPEx"
    version = "tpex-margin-balance:v1"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/margin/balance"
    variants = MappingProxyType({
        "margin_balance_20": (
            "代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金",
            "資使用率(%)", "資限額", "前券餘額(張)", "券賣", "券買", "券償", "券餘額",
            "券屬證金", "券使用率(%)", "券限額", "資券相抵(張)", "備註",
        ),
    })
    columns = MappingProxyType({
        "margin_previous_balance": 2, "margin_buy": 3, "margin_sell": 4,
        "margin_cash_repayment": 5, "margin_balance": 6,
        "margin_utilization_ratio": 8, "margin_next_limit": 9,
        "short_previous_balance": 10, "short_sell": 11, "short_buy": 12,
        "short_stock_repayment": 13, "short_balance": 14,
        "short_utilization_ratio": 16, "short_next_limit": 17,
        "offset_balance": 18,
    })

    def resource(self, request: MarginTradingRequest) -> SourceResource:
        query = urlencode({"date": request.trade_date.strftime("%Y/%m/%d"), "response": "json"})
        return SourceResource(
            resource_key=f"{self.source}:margin_trading:{request.trade_date.isoformat()}",
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(self, content: bytes, request: MarginTradingRequest) -> ParsedMarginTrading:
        payload = _json_object(content)
        if payload.get("stat") != "ok":
            raise SourceDataError("source_status", f"TPEx response status: {payload.get('stat')!r}")
        day: date = request.trade_date
        if payload.get("date") != day.strftime("%Y%m%d"):
            raise SourceDataError(
                "date_mismatch", f"TPEx answered for {payload.get('date')!r}, not {day.isoformat()}"
            )
        tables = payload.get("tables")
        if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
            raise SourceDataError("schema_mismatch", "TPEx must publish exactly one table")
        table = tables[0]
        expected = f"{day.year - _ROC_OFFSET}/{day:%m/%d}"
        if table.get("date") != expected:
            raise SourceDataError(
                "date_mismatch", f"TPEx table is dated {table.get('date')!r}, not {expected}"
            )
        data = table.get("data")
        total = table.get("totalCount")
        if total == 0 and not data:
            raise SourceDataError(
                "no_data_for_date", f"TPEx has no margin trading for {day.isoformat()}"
            )
        if not isinstance(total, int) or isinstance(total, bool) or total != len(data or []):
            raise SourceDataError(
                "schema_mismatch", f"TPEx declares {total!r} rows but lists {len(data or [])}"
            )
        return self._parse_rows(request, table.get("fields"), data)
