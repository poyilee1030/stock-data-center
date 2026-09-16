"""Whole-market daily-price adapters: one official file per (market, date).

The Step 9 pilots take one request per security per month, which would need
about 2,200 requests for a single trade date (audit §4.1). These take one.
They are separate sources rather than a faster path to the same rows: they
publish the disclosed bid/ask level the pilots do not, so sharing a source code
would make one logical key alternate revisions between two field sets, which
CLAUDE.md §30 forbids.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from stock_data_center.ingestion.models import (
    DailyMarketSourceSemantics,
    ParsedWholeMarketDaily,
    SourceDataError,
    SourceMoneyUnit,
    SourceQuantityUnit,
    SourceResource,
    WholeMarketDailyRequest,
    WholeMarketDailyRow,
)
from stock_data_center.market_data import DailyPriceObservation

_MISSING = frozenset({"", "--", "---", "----", "N/A"})
_TAGS = re.compile(r"<[^>]*>")
_LOT_SHARES = Decimal(1000)


class WholeMarketDailyAdapter(ABC):
    """One market's published closing quotes for one trade date."""

    dataset_code = "daily_price"
    source: str
    market: str
    version: str
    endpoint: str
    semantics: DailyMarketSourceSemantics

    def resource(self, request: WholeMarketDailyRequest) -> SourceResource:
        return SourceResource(
            resource_key=(
                f"{self.source}:daily-quotes:{request.trade_date.isoformat()}"
            ),
            source_uri=f"{self.endpoint}?{urlencode(self._query(request))}",
        )

    @abstractmethod
    def _query(self, request: WholeMarketDailyRequest) -> dict[str, str]: ...

    @abstractmethod
    def parse(
        self, content: bytes, request: WholeMarketDailyRequest
    ) -> ParsedWholeMarketDaily: ...

    def _disclosed_volume(self, value: str, field: str) -> Decimal | None:
        """The one published bid/ask level, in lots in both markets."""
        lots = _whole(value, field)
        if lots is None:
            return None
        _require_nonnegative(lots, field)
        if self.semantics.disclosed_volume_unit is SourceQuantityUnit.LOT_1000_SHARES:
            return lots * _LOT_SHARES
        if self.semantics.disclosed_volume_unit is SourceQuantityUnit.SHARE:
            return lots
        raise SourceDataError(  # pragma: no cover - the enum has two members
            "unknown_unit", f"{field} has no declared unit"
        )

    def _traded(self, volume: str, trade_value: str) -> tuple[Decimal | None, ...]:
        shares = _whole(volume, "source volume")
        if shares is not None:
            _require_nonnegative(shares, "source volume")
            if self.semantics.traded_quantity_unit is SourceQuantityUnit.SHARE:
                pass
            elif (
                self.semantics.traded_quantity_unit
                is SourceQuantityUnit.LOT_1000_SHARES
            ):
                shares *= _LOT_SHARES
            else:  # pragma: no cover - enum protects current implementations
                raise SourceDataError("unknown_unit", "unsupported quantity unit")
        value = _decimal(trade_value, "source trade value")
        if value is not None:
            _require_nonnegative(value, "source trade value")
            if self.semantics.trade_value_unit is SourceMoneyUnit.TWD:
                pass
            elif self.semantics.trade_value_unit is SourceMoneyUnit.THOUSAND_TWD:
                value *= _LOT_SHARES
            else:  # pragma: no cover - enum protects current implementations
                raise SourceDataError("unknown_unit", "unsupported money unit")
        return shares, value


class TWSEWholeMarketDailyAdapter(WholeMarketDailyAdapter):
    """TWSE `MI_INDEX?type=ALLBUT0999`, stock section only.

    The same artifact carries the index sections, which belong to Step 18. The
    stock section is found by its own header, never by a table position.
    """

    source = "twse_mi_index"
    market = "TWSE"
    version = "twse-mi-index-allbut0999:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    # The table states its own units and this is the only unit statement TWSE
    # makes about it, so every column here is TWD or shares — including the
    # disclosed bid/ask level, unlike TPEx, which labels that column in lots.
    # Corroborated by TWT53U, the odd-lot report: same column labels, same
    # hint, and 2330 shows 最後揭示買量 = 200,937 on the same date, which can
    # only be shares. `hints` is checked on every parse rather than trusted to
    # a comment: a restatement to 仟股 would silently mis-scale everything.
    unit_declaration = "單位：元、股"
    semantics = DailyMarketSourceSemantics(
        traded_quantity_unit=SourceQuantityUnit.SHARE,
        trade_value_unit=SourceMoneyUnit.TWD,
        disclosed_volume_unit=SourceQuantityUnit.SHARE,
    )
    fields = (
        "證券代號",
        "證券名稱",
        "成交股數",
        "成交筆數",
        "成交金額",
        "開盤價",
        "最高價",
        "最低價",
        "收盤價",
        "漲跌(+/-)",
        "漲跌價差",
        "最後揭示買價",
        "最後揭示買量",
        "最後揭示賣價",
        "最後揭示賣量",
        "本益比",
    )

    def _query(self, request: WholeMarketDailyRequest) -> dict[str, str]:
        return {
            "date": request.trade_date.strftime("%Y%m%d"),
            "type": "ALLBUT0999",
            "response": "json",
        }

    def parse(
        self, content: bytes, request: WholeMarketDailyRequest
    ) -> ParsedWholeMarketDaily:
        payload = _json_object(content)
        if payload.get("stat") != "OK":
            # A date TWSE has nothing for answers with an apology and no
            # tables at all. That is structurally distinguishable from a
            # status this adapter cannot interpret, and worth its own reason
            # code: a caller walking a date range can skip it without
            # re-deriving the calendar. It is not called `market_closed`,
            # because only the Step 16 calendar can say a date was a closure.
            if "tables" not in payload:
                raise SourceDataError(
                    "no_data_for_date",
                    f"TWSE has no data for {request.trade_date.isoformat()}: "
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
        table = _one_table_with_header(
            payload.get("tables"), self.fields, self.source
        )
        if table.get("hints") != self.unit_declaration:
            raise SourceDataError(
                "unit_declaration_changed",
                f"TWSE restated its units as {table.get('hints')!r}, not "
                f"{self.unit_declaration!r}; the scale of every quantity in "
                "this table follows from that statement",
            )

        rows = []
        for number, raw in enumerate(_data_rows(table.get("data"), self.source), 1):
            values = _row(raw, len(self.fields), self.source, number)
            shares, trade_value = self._traded(values[2], values[4])
            prices = _prices(values[5], values[6], values[7], values[8])
            change, direction = _twse_change(
                marker=values[9],
                magnitude=values[10],
                close_price=prices[3],
                row_number=number,
            )
            rows.append(
                WholeMarketDailyRow(
                    security_code=_security_code(values[0], self.source, number),
                    security_name=_security_name(values[1], self.source, number),
                    observation=DailyPriceObservation(
                        trade_date=request.trade_date,
                        open_price=prices[0],
                        high_price=prices[1],
                        low_price=prices[2],
                        close_price=prices[3],
                        volume=shares,
                        trade_value=trade_value,
                        trade_count=_integer(values[3], "trade count"),
                        price_change=change,
                        price_direction=direction,
                        last_bid_price=_decimal(values[11], "last bid price"),
                        last_bid_volume=self._disclosed_volume(
                            values[12], "last bid volume"
                        ),
                        last_ask_price=_decimal(values[13], "last ask price"),
                        last_ask_volume=self._disclosed_volume(
                            values[14], "last ask volume"
                        ),
                    ),
                )
            )
        _validate_rows(rows, self.source)
        return ParsedWholeMarketDaily(
            market=self.market,
            trade_date=request.trade_date,
            rows=tuple(rows),
            source_fields=self.fields,
            header_variant="allbut0999",
        )


# Each TPEx header variant, with the column index of every field this contract
# stores. The variants are the audit's three (§4.1), verified live at their own
# boundary dates: the disclosed bid/ask volume appears on 2020-04-30 and is
# relabelled 千股 to 張數 on 2025-01-10 without changing unit.
_TPEX_BASE = {
    "code": 0,
    "name": 1,
    "close": 2,
    "change": 3,
    "open": 4,
    "high": 5,
    "low": 6,
    "volume": 7,
    "trade_value": 8,
    "trade_count": 9,
    "bid_price": 10,
}
_TPEX_VARIANTS = {
    (
        "代號", "名稱", "收盤 ", "漲跌", "開盤 ", "最高 ", "最低", "成交股數  ",
        " 成交金額(元)", " 成交筆數 ", "最後買價", "最後賣價", "發行股數 ",
        "次日漲停價 ", "次日跌停價",
    ): (
        "prices_only",
        "千股",
        {**_TPEX_BASE, "ask_price": 11},
    ),
    (
        "代號", "名稱", "收盤 ", "漲跌", "開盤 ", "最高 ", "最低", "成交股數  ",
        " 成交金額(元)", " 成交筆數 ", "最後買價", "最後買量<br>(千股)",
        "最後賣價", "最後賣量<br>(千股)", "發行股數 ", "次日漲停價 ", "次日跌停價",
    ): (
        "volume_in_thousand_shares",
        "千股",
        {**_TPEX_BASE, "bid_volume": 11, "ask_price": 12, "ask_volume": 13},
    ),
    (
        "代號", "名稱", "收盤 ", "漲跌", "開盤 ", "最高 ", "最低", "成交股數  ",
        " 成交金額(元)", " 成交筆數 ", "最後買價", "最後買量<br>(張數)",
        "最後賣價", "最後賣量<br>(張數)", "發行股數 ", "次日漲停價 ", "次日跌停價",
    ): (
        "volume_in_lots",
        "張數",
        {**_TPEX_BASE, "bid_volume": 11, "ask_price": 12, "ask_volume": 13},
    ),
}


class TPExWholeMarketDailyAdapter(WholeMarketDailyAdapter):
    """TPEx `afterTrading/otc`, the feed the audit calls `stk_wn1430`."""

    source = "tpex_otc_quotes"
    market = "TPEx"
    version = "tpex-otc-daily-quotes:v1"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
    # 成交股數 and 成交金額(元) are already canonical; only the disclosed
    # bid/ask level is in lots, under either of its two labels.
    semantics = DailyMarketSourceSemantics(
        traded_quantity_unit=SourceQuantityUnit.SHARE,
        trade_value_unit=SourceMoneyUnit.TWD,
        disclosed_volume_unit=SourceQuantityUnit.LOT_1000_SHARES,
    )

    def _query(self, request: WholeMarketDailyRequest) -> dict[str, str]:
        return {
            "date": request.trade_date.strftime("%Y/%m/%d"),
            "type": "EW",
            "response": "json",
        }

    def parse(
        self, content: bytes, request: WholeMarketDailyRequest
    ) -> ParsedWholeMarketDaily:
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
        if not isinstance(tables, list) or len(tables) != 1:
            raise SourceDataError(
                "schema_mismatch", "TPEx expected exactly one quote table"
            )
        table = tables[0]
        if not isinstance(table, dict):
            raise SourceDataError("schema_mismatch", "TPEx table is not an object")
        fields = table.get("fields")
        header = tuple(fields) if isinstance(fields, list) else ()
        known = _TPEX_VARIANTS.get(header)
        if known is None:
            raise SourceDataError(
                "schema_mismatch",
                f"TPEx published an unknown header variant: {header!r}",
            )
        variant, expected_flag, columns = known
        # The response labels its own disclosed-volume unit. A label that
        # disagrees with the header is not ours to reconcile.
        if payload.get("flagField") != expected_flag:
            raise SourceDataError(
                "schema_mismatch",
                f"TPEx {variant} expects flagField {expected_flag!r}, got "
                f"{payload.get('flagField')!r}",
            )

        rows = []
        for number, raw in enumerate(_data_rows(table.get("data"), self.source), 1):
            values = _row(raw, len(header), self.source, number)
            # Absent in a variant means absent from the contract, not zero.
            cell = {name: values[index] for name, index in columns.items()}
            shares, trade_value = self._traded(
                cell["volume"], cell["trade_value"]
            )
            prices = _prices(
                cell["open"], cell["high"], cell["low"], cell["close"]
            )
            change, direction = _tpex_change(cell["change"], number)
            rows.append(
                WholeMarketDailyRow(
                    security_code=_security_code(
                        cell.get("code", ""), self.source, number
                    ),
                    security_name=_security_name(
                        cell.get("name", ""), self.source, number
                    ),
                    observation=DailyPriceObservation(
                        trade_date=request.trade_date,
                        open_price=prices[0],
                        high_price=prices[1],
                        low_price=prices[2],
                        close_price=prices[3],
                        volume=shares,
                        trade_value=trade_value,
                        trade_count=_integer(cell.get("trade_count", ""), "trade count"),
                        price_change=change,
                        price_direction=direction,
                        last_bid_price=_decimal(cell.get("bid_price", ""), "last bid price"),
                        last_bid_volume=self._disclosed_volume(
                            cell.get("bid_volume", ""), "last bid volume"
                        ),
                        last_ask_price=_decimal(cell.get("ask_price", ""), "last ask price"),
                        last_ask_volume=self._disclosed_volume(
                            cell.get("ask_volume", ""), "last ask volume"
                        ),
                    ),
                )
            )
        _validate_rows(rows, self.source)
        return ParsedWholeMarketDaily(
            market=self.market,
            trade_date=request.trade_date,
            rows=tuple(rows),
            source_fields=header,
            header_variant=variant,
        )


def _twse_change(
    *, marker: str, magnitude: str, close_price: Decimal | None, row_number: int
) -> tuple[Decimal | None, str | None]:
    """Combine 漲跌(+/-) with the unsigned 漲跌價差.

    TWSE's own note: `+/-/X` means 漲/跌/不比價. On an X row the cell under the
    magnitude is filler — every X row in every inspected file publishes 0.00 —
    so nothing is stored for it rather than a zero change the source never
    claimed. A row that did not trade has no close and therefore no change.
    """
    sign = _TAGS.sub("", marker).replace(" ", " ").strip().upper()
    if sign == "X":
        return None, "X"
    if sign == "":
        if close_price is None:
            return None, None
        value = _decimal(magnitude, f"TWSE row {row_number} price change")
        if value is None or value != 0:
            raise SourceDataError(
                "ambiguous_direction",
                f"TWSE row {row_number} publishes no sign for a change of "
                f"{magnitude!r}",
            )
        return value, "flat"
    if sign not in {"+", "-"}:
        raise SourceDataError(
            "ambiguous_direction",
            f"TWSE row {row_number} publishes an unknown sign {sign!r}",
        )
    value = _decimal(magnitude, f"TWSE row {row_number} price change")
    if value is None:
        raise SourceDataError(
            "ambiguous_direction",
            f"TWSE row {row_number} signs {sign!r} with no magnitude",
        )
    _require_nonnegative(value, f"TWSE row {row_number} price change")
    return (-value if sign == "-" else value), sign


def _tpex_change(value: str, row_number: int) -> tuple[Decimal | None, str | None]:
    """TPEx signs the number itself and publishes no direction column.

    Where TWSE writes `X`, TPEx writes the reason — 除息 / 除權 / 除權息 — in
    place of the number. Both mean 不比價, so the marker maps to the same `X`
    and no change is stored; the source's own wording survives in the raw
    artifact. Audit §4.1: the feed has no `漲跌(+/-)` column, so an ordinary
    row claims no direction.
    """
    text = value.strip()
    if text.startswith("除"):
        return None, "X"
    change = _decimal(text, f"TPEx row {row_number} price change")
    return change, None


def _prices(
    open_price: str, high_price: str, low_price: str, close_price: str
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    parsed = (
        _decimal(open_price, "open price"),
        _decimal(high_price, "high price"),
        _decimal(low_price, "low price"),
        _decimal(close_price, "close price"),
    )
    for field, value in zip(
        ("open price", "high price", "low price", "close price"), parsed, strict=True
    ):
        if value is not None:
            _require_nonnegative(value, field)
    opened, high, low, close = parsed
    if high is not None and low is not None and high < low:
        raise SourceDataError("impossible_value", "high price is below low price")
    for field, value in (("open price", opened), ("close price", close)):
        if value is not None and low is not None and value < low:
            raise SourceDataError("impossible_value", f"{field} is below low price")
        if value is not None and high is not None and value > high:
            raise SourceDataError("impossible_value", f"{field} is above high price")
    return parsed


def _one_table_with_header(
    tables: object, expected: tuple[str, ...], source: str
) -> dict[str, object]:
    if not isinstance(tables, list):
        raise SourceDataError("schema_mismatch", f"{source} tables is not a list")
    matches = [
        table
        for table in tables
        if isinstance(table, dict)
        and isinstance(table.get("fields"), list)
        and tuple(table["fields"]) == expected
    ]
    if len(matches) != 1:
        raise SourceDataError(
            "schema_mismatch",
            f"{source} published {len(matches)} tables with the stock header "
            f"{expected!r}",
        )
    return matches[0]


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


def _data_rows(value: object, source: str) -> list[object]:
    if not isinstance(value, list):
        raise SourceDataError("schema_mismatch", f"{source} data is not a list")
    if not value:
        raise SourceDataError(
            "no_data_for_date",
            f"{source} published no quote for the requested trade date",
        )
    return value


def _row(
    value: object, expected_length: int, source: str, row_number: int
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) != expected_length
        or not all(isinstance(item, str) for item in value)
    ):
        raise SourceDataError(
            "schema_mismatch", f"{source} row {row_number} has an invalid shape"
        )
    return tuple(value)


def _security_code(value: str, source: str, row_number: int) -> str:
    code = value.strip()
    if not code:
        raise SourceDataError(
            "invalid_identity", f"{source} row {row_number} has no security code"
        )
    return code


def _security_name(value: str, source: str, row_number: int) -> str:
    name = value.strip()
    if not name:
        raise SourceDataError(
            "invalid_identity", f"{source} row {row_number} has no security name"
        )
    return name


def _decimal(value: str, field: str) -> Decimal | None:
    normalized = _TAGS.sub("", value).replace(",", "").replace("　", "").strip()
    if normalized.upper() in _MISSING:
        return None
    try:
        result = Decimal(normalized)
    except InvalidOperation as error:
        raise SourceDataError(
            "invalid_numeric", f"{field} is not an unambiguous decimal: {value!r}"
        ) from error
    if not result.is_finite():
        raise SourceDataError("invalid_numeric", f"{field} must be finite")
    return result


def _whole(value: str, field: str) -> Decimal | None:
    result = _decimal(value, field)
    if result is not None and result != result.to_integral_value():
        raise SourceDataError("ambiguous_unit", f"{field} must be a whole source unit")
    return result


def _integer(value: str, field: str) -> int | None:
    result = _whole(value, field)
    if result is None:
        return None
    _require_nonnegative(result, field)
    return int(result)


def _require_nonnegative(value: Decimal, field: str) -> None:
    if value < 0:
        raise SourceDataError("impossible_value", f"{field} must be non-negative")


def _validate_rows(rows: list[WholeMarketDailyRow], source: str) -> None:
    if not rows:
        raise SourceDataError("empty_coverage", f"{source} returned no quotes")
    codes = [item.security_code for item in rows]
    if len(codes) != len(set(codes)):
        duplicates = sorted({code for code in codes if codes.count(code) > 1})
        raise SourceDataError(
            "ambiguous_identity",
            f"{source} published one trade date twice for {duplicates!r}",
        )
