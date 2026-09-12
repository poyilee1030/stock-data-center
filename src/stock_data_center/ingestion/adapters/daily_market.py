"""Official TWSE and TPEx monthly daily-market adapters."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from stock_data_center.ingestion.models import (
    DailyMarketRequest,
    DailyMarketSourceSemantics,
    ParsedDailyMarket,
    SourceDataError,
    SourceMoneyUnit,
    SourceQuantityUnit,
    SourceResource,
)
from stock_data_center.market_data import DailyPriceObservation

_MISSING = frozenset({"", "--", "---", "----", "N/A"})


class DailyMarketAdapter(ABC):
    dataset_code = "daily_price"
    version: str
    source: str
    semantics: DailyMarketSourceSemantics

    @abstractmethod
    def resource(self, request: DailyMarketRequest) -> SourceResource: ...

    @abstractmethod
    def parse(
        self, content: bytes, request: DailyMarketRequest
    ) -> ParsedDailyMarket: ...

    def _observation(
        self,
        *,
        trade_date: date,
        source_volume: str,
        source_trade_value: str,
        open_price: str,
        high_price: str,
        low_price: str,
        close_price: str,
        price_change: str,
        trade_count: str,
    ) -> DailyPriceObservation:
        volume = _whole(source_volume, "source volume")
        if volume is not None:
            _require_nonnegative(volume, "source volume")
            if self.semantics.traded_quantity_unit is SourceQuantityUnit.SHARE:
                pass
            elif (
                self.semantics.traded_quantity_unit
                is SourceQuantityUnit.LOT_1000_SHARES
            ):
                volume *= Decimal(1000)
            else:  # pragma: no cover - enum protects current implementations
                raise SourceDataError("unknown_unit", "unsupported quantity unit")

        trade_value = _decimal(source_trade_value, "source trade value")
        if trade_value is not None:
            _require_nonnegative(trade_value, "source trade value")
            if self.semantics.trade_value_unit is SourceMoneyUnit.TWD:
                pass
            elif self.semantics.trade_value_unit is SourceMoneyUnit.THOUSAND_TWD:
                trade_value *= Decimal(1000)
            else:  # pragma: no cover - enum protects current implementations
                raise SourceDataError("unknown_unit", "unsupported money unit")

        parsed_open = _decimal(open_price, "open price")
        parsed_high = _decimal(high_price, "high price")
        parsed_low = _decimal(low_price, "low price")
        parsed_close = _decimal(close_price, "close price")
        parsed_trade_count = _integer(trade_count, "trade count")
        _validate_market_values(
            open_price=parsed_open,
            high_price=parsed_high,
            low_price=parsed_low,
            close_price=parsed_close,
            trade_count=parsed_trade_count,
        )
        change, direction = _signed_change(price_change)
        return DailyPriceObservation(
            trade_date=trade_date,
            open_price=parsed_open,
            high_price=parsed_high,
            low_price=parsed_low,
            close_price=parsed_close,
            volume=volume,
            trade_value=trade_value,
            trade_count=parsed_trade_count,
            price_change=change,
            price_direction=direction,
        )


class TWSEDailyMarketAdapter(DailyMarketAdapter):
    """Parse TWSE STOCK_DAY: shares and TWD are already canonical units."""

    source = "twse"
    version = "twse-stock-day:v1"
    semantics = DailyMarketSourceSemantics(
        traded_quantity_unit=SourceQuantityUnit.SHARE,
        trade_value_unit=SourceMoneyUnit.TWD,
    )
    fields = (
        "日期",
        "成交股數",
        "成交金額",
        "開盤價",
        "最高價",
        "最低價",
        "收盤價",
        "漲跌價差",
        "成交筆數",
        "註記",
    )
    endpoint = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"

    def resource(self, request: DailyMarketRequest) -> SourceResource:
        query = urlencode(
            {
                "response": "json",
                "date": request.month.strftime("%Y%m%d"),
                "stockNo": request.security_code,
            }
        )
        return SourceResource(
            resource_key=(
                f"twse:stock-day:{request.security_code}:"
                f"{request.month:%Y-%m}"
            ),
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: DailyMarketRequest
    ) -> ParsedDailyMarket:
        payload = _json_object(content)
        if payload.get("stat") != "OK":
            raise SourceDataError(
                "source_status", f"TWSE response status: {payload.get('stat')!r}"
            )
        _validate_fields(payload.get("fields"), self.fields, self.source)
        if payload.get("date") != request.month.strftime("%Y%m%d"):
            raise SourceDataError("date_mismatch", "TWSE response month mismatch")
        title = payload.get("title")
        if not isinstance(title, str) or request.security_code not in title:
            raise SourceDataError("identity_mismatch", "TWSE title/code mismatch")
        after_code = title.split(request.security_code, 1)[1]
        security_name = after_code.split("各日成交資訊", 1)[0].strip()
        if not security_name:
            raise SourceDataError("invalid_identity", "TWSE security name missing")

        rows = []
        for row_number, row in enumerate(
            _data_rows(payload.get("data"), self.source), start=1
        ):
            values = _row(row, len(self.fields), self.source, row_number)
            trade_date = _roc_date(values[0], f"TWSE row {row_number} date")
            _validate_month(trade_date, request.month, self.source, row_number)
            rows.append(
                self._observation(
                    trade_date=trade_date,
                    source_volume=values[1],
                    source_trade_value=values[2],
                    open_price=values[3],
                    high_price=values[4],
                    low_price=values[5],
                    close_price=values[6],
                    price_change=values[7],
                    trade_count=values[8],
                )
            )
        _validate_rows(rows, self.source)
        return ParsedDailyMarket(
            security_code=request.security_code,
            security_name=security_name,
            requested_month=request.month,
            rows=tuple(rows),
            source_fields=self.fields,
        )


class TPExDailyMarketAdapter(DailyMarketAdapter):
    """Parse TPEx tradingStock: lots/thousand-TWD normalize before writing."""

    source = "tpex"
    version = "tpex-trading-stock:v1"
    semantics = DailyMarketSourceSemantics(
        traded_quantity_unit=SourceQuantityUnit.LOT_1000_SHARES,
        trade_value_unit=SourceMoneyUnit.THOUSAND_TWD,
    )
    fields = (
        "日 期",
        "成交張數",
        "成交仟元",
        "開盤",
        "最高",
        "最低",
        "收盤",
        "漲跌",
        "筆數",
    )
    endpoint = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"

    def resource(self, request: DailyMarketRequest) -> SourceResource:
        query = urlencode(
            {
                "code": request.security_code,
                "date": request.month.strftime("%Y/%m/%d"),
                "response": "json",
            }
        )
        return SourceResource(
            resource_key=(
                f"tpex:trading-stock:{request.security_code}:"
                f"{request.month:%Y-%m}"
            ),
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: DailyMarketRequest
    ) -> ParsedDailyMarket:
        payload = _json_object(content)
        if payload.get("stat") != "ok":
            raise SourceDataError(
                "source_status", f"TPEx response status: {payload.get('stat')!r}"
            )
        if payload.get("code") != request.security_code:
            raise SourceDataError("identity_mismatch", "TPEx response code mismatch")
        if payload.get("date") != request.month.strftime("%Y%m%d"):
            raise SourceDataError("date_mismatch", "TPEx response month mismatch")
        security_name = payload.get("name")
        if not isinstance(security_name, str) or not security_name.strip():
            raise SourceDataError("invalid_identity", "TPEx security name missing")
        tables = payload.get("tables")
        if not isinstance(tables, list) or len(tables) != 1:
            raise SourceDataError("schema_mismatch", "TPEx expected exactly one table")
        table = tables[0]
        if not isinstance(table, dict):
            raise SourceDataError("schema_mismatch", "TPEx table is not an object")
        _validate_fields(table.get("fields"), self.fields, self.source)

        rows = []
        for row_number, row in enumerate(
            _data_rows(table.get("data"), self.source), start=1
        ):
            values = _row(row, len(self.fields), self.source, row_number)
            trade_date = _roc_date(values[0], f"TPEx row {row_number} date")
            _validate_month(trade_date, request.month, self.source, row_number)
            rows.append(
                self._observation(
                    trade_date=trade_date,
                    source_volume=values[1],
                    source_trade_value=values[2],
                    open_price=values[3],
                    high_price=values[4],
                    low_price=values[5],
                    close_price=values[6],
                    price_change=values[7],
                    trade_count=values[8],
                )
            )
        _validate_rows(rows, self.source)
        return ParsedDailyMarket(
            security_code=request.security_code,
            security_name=security_name.strip(),
            requested_month=request.month,
            rows=tuple(rows),
            source_fields=self.fields,
        )


def _json_object(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceDataError("invalid_json", f"invalid source JSON: {error}") from error
    if not isinstance(payload, dict):
        raise SourceDataError("schema_mismatch", "source JSON must be an object")
    return payload


def _validate_fields(
    actual: object, expected: tuple[str, ...], source: str
) -> None:
    if not isinstance(actual, list) or tuple(actual) != expected:
        raise SourceDataError(
            "schema_mismatch",
            f"{source} fields changed: expected {expected!r}, received {actual!r}",
        )


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


def _data_rows(value: object, source: str) -> list[object]:
    if not isinstance(value, list):
        raise SourceDataError("schema_mismatch", f"{source} data is not a list")
    return value


def _decimal(value: str, field: str) -> Decimal | None:
    normalized = value.strip().replace(",", "")
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
    return int(result) if result is not None else None


def _require_nonnegative(value: Decimal | int, field: str) -> None:
    if value < 0:
        raise SourceDataError("impossible_value", f"{field} must be non-negative")


def _validate_market_values(
    *,
    open_price: Decimal | None,
    high_price: Decimal | None,
    low_price: Decimal | None,
    close_price: Decimal | None,
    trade_count: int | None,
) -> None:
    for field, value in (
        ("open price", open_price),
        ("high price", high_price),
        ("low price", low_price),
        ("close price", close_price),
    ):
        if value is not None:
            _require_nonnegative(value, field)
    if trade_count is not None:
        _require_nonnegative(trade_count, "trade count")
    if high_price is not None and low_price is not None and high_price < low_price:
        raise SourceDataError("impossible_value", "high price is below low price")
    for field, value in (("open price", open_price), ("close price", close_price)):
        if value is not None and low_price is not None and value < low_price:
            raise SourceDataError("impossible_value", f"{field} is below low price")
        if value is not None and high_price is not None and value > high_price:
            raise SourceDataError("impossible_value", f"{field} is above high price")


def _signed_change(value: str) -> tuple[Decimal | None, str | None]:
    stripped = value.strip()
    if stripped[:1].upper() == "X":
        change = _decimal(stripped[1:], "X-marked price change")
        if change is None:
            raise SourceDataError(
                "invalid_numeric", "X-marked price change requires a number"
            )
        return change, "X"
    change = _decimal(stripped, "price change")
    if change is None:
        return None, None
    if change > 0:
        return change, "+"
    if change < 0:
        return change, "-"
    return change, "flat"


def _roc_date(value: str, field: str) -> date:
    parts = value.strip().split("/")
    if len(parts) != 3:
        raise SourceDataError("invalid_date", f"{field} is not ROC Y/M/D")
    try:
        year, month, day = (int(item) for item in parts)
        return date(year + 1911, month, day)
    except ValueError as error:
        raise SourceDataError("invalid_date", f"{field} is invalid: {value!r}") from error


def _validate_month(
    trade_date: date, requested_month: date, source: str, row_number: int
) -> None:
    if (trade_date.year, trade_date.month) != (
        requested_month.year,
        requested_month.month,
    ):
        raise SourceDataError(
            "date_mismatch", f"{source} row {row_number} falls outside requested month"
        )


def _validate_rows(rows: list[DailyPriceObservation], source: str) -> None:
    if not rows:
        raise SourceDataError("empty_coverage", f"{source} returned no daily rows")
    dates = [row.trade_date for row in rows]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise SourceDataError(
            "ambiguous_identity", f"{source} dates are duplicated or not ordered"
        )
