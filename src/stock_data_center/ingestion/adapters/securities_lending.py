"""Securities-lending adapters: 信用額度總量管制餘額表 (借券賣出).

One whole-market table per trade date, in shares:

- TWSE `rwd/zh/marginTrading/TWT93U`. `hints` reads `單位：股`, checked on every
  file. 15 fields in the groups 股票 / 融券 / 借券賣出 / 備註; the labels repeat
  (前日餘額), so columns are read by position and the groups are checked too.
  The last row is the market total, `合計`, with no code; it is not stored.
- TPEx `www/zh-tw/margin/sbl`, the JSON of the legacy `margin_sbl` page (audit
  §4.5), the same 15 fields. The JSON states no unit. The page that renders it
  declares `單位：股`, and its 融券 columns equal `margin/balance`'s lots × 1,000
  row for row (2020-01-02); the reconciliation repeats that on every date.

Being in shares, nothing is converted, so the trading-unit exceptions of the
lot-denominated margin table (008201, 100 shares a lot) do not apply here.

Stored in `securities_lending`: the 借券賣出 group — 前日餘額,
當日賣出 (`borrowed` here, the table's `sold`), 當日還券 (`returned`), 當日調整
(`adjustment`, signed: positions moved between accounts and error corrections),
當日餘額, 次一營業日可限額 (`next_available_limit`) — and the 融券 group's
次一營業日限額 / 限額 as `next_limit`: the short-sale limit in exact shares,
which `margin_trading` holds only rounded down to whole lots. 備註 is parsed
without its padding, blank as NULL; schema v2 does not store it (ADR-0027).

Not stored: names, the 合計 row, and the 融券 group's balances and flows, which
repeat `margin_trading` (Step 21-a) — the reconciliation checks they agree.
No value is recomputed.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from urllib.parse import urlencode

from stock_data_center.ingestion.models import (
    ParsedSecuritiesLending,
    SecuritiesLendingRequest,
    SecuritiesLendingRow,
    SourceDataError,
    SourceResource,
)
from stock_data_center.ingestion.observations import (
    QuantityScale,
    SecuritiesLendingObservation,
    SourceShareQuantity,
)

_ROC_OFFSET = 1911
_SHARES = re.compile(r"-?(?:\d{1,3}(?:,\d{3})*|\d+)")
_NOTE = 14

# Observation field -> index into the published row; the same in both markets.
_COLUMNS = MappingProxyType({
    "next_limit": 7,
    "previous_balance": 8,
    "borrowed": 9,
    "returned": 10,
    "adjustment": 11,
    "balance": 12,
    "next_available_limit": 13,
})


class SecuritiesLendingAdapter(ABC):
    """One market's per-security securities lending for one trade date."""

    dataset_code = "securities_lending"
    source: str
    market: str
    version: str
    endpoint: str
    variants: Mapping[str, tuple[str, ...]]
    columns: Mapping[str, int] = _COLUMNS

    @abstractmethod
    def resource(self, request: SecuritiesLendingRequest) -> SourceResource: ...

    @abstractmethod
    def parse(
        self, content: bytes, request: SecuritiesLendingRequest
    ) -> ParsedSecuritiesLending: ...

    def _parse_rows(
        self, request: SecuritiesLendingRequest, fields: object, data: list[object]
    ) -> ParsedSecuritiesLending:
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
        rows: list[SecuritiesLendingRow] = []
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
            values = {
                name: self._shares(raw[index], number, name)
                for name, index in self.columns.items()
            }
            note = self._text(raw[_NOTE], number).strip() or None
            try:
                observation = SecuritiesLendingObservation(
                    trade_date=request.trade_date, note=note, **values
                )
            except ValueError as error:
                raise SourceDataError(
                    "invalid_numeric", f"{self.source} row {number} ({code}): {error}"
                ) from error
            rows.append(SecuritiesLendingRow(code, observation))
        return ParsedSecuritiesLending(
            market=self.market,
            trade_date=request.trade_date,
            rows=tuple(rows),
            header_variant=variant,
            source_fields=header,
        )

    def _text(self, value: object, number: int) -> str:
        if not isinstance(value, str):
            raise SourceDataError(
                "schema_mismatch", f"{self.source} row {number} value {value!r} is not text"
            )
        return value

    def _shares(self, value: object, number: int, field: str):
        text = self._text(value, number).strip()
        if not _SHARES.fullmatch(text):
            raise SourceDataError(
                "unrecognised_value", f"{self.source} row {number} {field} is {value!r}"
            )
        return SourceShareQuantity(
            Decimal(text.replace(",", "")), QuantityScale.SHARE
        ).to_canonical()


def _json_object(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceDataError("invalid_json", f"invalid source JSON: {error}") from error
    if not isinstance(payload, dict):
        raise SourceDataError("schema_mismatch", "source JSON must be an object")
    return payload


def _declared_rows(source: str, total: object, data: object) -> list[object]:
    if not isinstance(data, list):
        raise SourceDataError("schema_mismatch", f"{source} data is not a list")
    if not isinstance(total, int) or isinstance(total, bool) or total != len(data):
        raise SourceDataError(
            "schema_mismatch", f"{source} declares {total!r} rows but lists {len(data)}"
        )
    return data


_TWSE_UNIT = "單位：股"
_TWSE_GROUPS = (("股票", 2), ("融券", 6), ("借券賣出", 6), ("", 1))
_TWSE_TOTAL = "合計"


class TWSESecuritiesLendingAdapter(SecuritiesLendingAdapter):
    """`marginTrading/TWT93U` — 信用額度總量管制餘額表."""

    source = "twse_twt93u"
    market = "TWSE"
    version = "twse-twt93u:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U"
    variants = MappingProxyType({
        "twt93u_15": (
            "代號", "名稱",
            "前日餘額", "賣出", "買進", "現券", "今日餘額", "次一營業日限額",
            "前日餘額", "當日賣出", "當日還券", "當日調整", "當日餘額", "次一營業日可限額",
            "備註",
        ),
    })

    def resource(self, request: SecuritiesLendingRequest) -> SourceResource:
        query = urlencode({"date": request.trade_date.strftime("%Y%m%d"), "response": "json"})
        return SourceResource(
            resource_key=f"{self.source}:securities_lending:{request.trade_date.isoformat()}",
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: SecuritiesLendingRequest
    ) -> ParsedSecuritiesLending:
        payload = _json_object(content)
        if payload.get("stat") != "OK":
            raise SourceDataError("source_status", f"TWSE response status: {payload.get('stat')!r}")
        day = request.trade_date
        if payload.get("date") != day.strftime("%Y%m%d"):
            raise SourceDataError(
                "date_mismatch", f"TWSE answered for {payload.get('date')!r}, not {day.isoformat()}"
            )
        data = _declared_rows(self.source, payload.get("total"), payload.get("data"))
        if not data:
            # A closed date answers OK with no rows, no groups and no unit.
            raise SourceDataError(
                "no_data_for_date", f"TWSE has no securities lending for {day.isoformat()}"
            )
        if payload.get("hints") != _TWSE_UNIT:
            raise SourceDataError(
                "unit_declaration_changed",
                f"TWSE hints read {payload.get('hints')!r}, not {_TWSE_UNIT!r}",
            )
        groups = payload.get("groups")
        published = (
            tuple((g.get("title"), g.get("span")) for g in groups if isinstance(g, dict))
            if isinstance(groups, list)
            else None
        )
        if published != _TWSE_GROUPS:
            raise SourceDataError(
                "schema_mismatch", f"TWSE column groups are {groups!r}, not {_TWSE_GROUPS!r}"
            )
        last = data[-1]
        if isinstance(last, list) and len(last) > 1 and last[0] == "" and last[1] == _TWSE_TOTAL:
            data = data[:-1]
        return self._parse_rows(request, payload.get("fields"), data)


class TPExSecuritiesLendingAdapter(SecuritiesLendingAdapter):
    """TPEx `margin/sbl` — 信用額度總量管制餘額表."""

    source = "tpex_margin_sbl"
    market = "TPEx"
    version = "tpex-margin-sbl:v1"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/margin/sbl"
    variants = MappingProxyType({
        "margin_sbl_15": (
            "股票代號", "股票名稱",
            "前日餘額", "賣出", "買進", "現券", "當日餘額", "限額",
            "前日餘額", "當日賣出", "當日還券", "當日調整數額", "當日餘額",
            "次一營業日可借券賣出限額",
            "備註",
        ),
    })

    def resource(self, request: SecuritiesLendingRequest) -> SourceResource:
        query = urlencode({"date": request.trade_date.strftime("%Y/%m/%d"), "response": "json"})
        return SourceResource(
            resource_key=f"{self.source}:securities_lending:{request.trade_date.isoformat()}",
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: SecuritiesLendingRequest
    ) -> ParsedSecuritiesLending:
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
        data = _declared_rows(self.source, table.get("totalCount"), table.get("data"))
        if not data:
            raise SourceDataError(
                "no_data_for_date", f"TPEx has no securities lending for {day.isoformat()}"
            )
        return self._parse_rows(request, table.get("fields"), data)
