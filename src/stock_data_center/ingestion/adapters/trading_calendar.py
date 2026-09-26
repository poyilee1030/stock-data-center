"""Official trading-calendar adapter for the TWSE FMTQIK monthly report."""

from __future__ import annotations

import json
from datetime import date

from stock_data_center.ingestion.models import (
    ParsedTradingCalendar,
    SourceDataError,
    SourceResource,
    TradingCalendarRequest,
)

# The answer every TWSE rwd endpoint gives when it has no rows for the request.
_NO_DATA = "很抱歉，沒有符合條件的資料!"


class TradingCalendarAdapter:
    """Map one official monthly report into the market's actual trading days.

    The source lists only the days the market actually traded, so a closure is
    an absence and never a flag. Nothing here may infer a day the source did not
    publish.
    """

    dataset_code = "trading_calendar"
    source: str
    market: str
    version: str
    endpoint: str
    date_field: str

    def resource(self, request: TradingCalendarRequest) -> SourceResource:
        month = request.month
        return SourceResource(
            resource_key=(
                f"{self.source}:trading-calendar:{month.year:04d}-{month.month:02d}"
            ),
            source_uri=(
                f"{self.endpoint}?date={month.strftime('%Y%m%d')}&response=json"
            ),
        )

    def parse(
        self, content: bytes, request: TradingCalendarRequest
    ) -> ParsedTradingCalendar:
        payload = _json_object(content)

        stat = payload.get("stat")
        if stat == _NO_DATA:
            # A month whose first trading day has not closed yet (Step 28-a):
            # nothing published so far, asked again later. Never a closed month.
            raise SourceDataError(
                "no_data_for_period",
                f"{self.source} published no trading day for {request.month:%Y-%m} yet",
            )
        if stat != "OK":
            raise SourceDataError(
                "source_error",
                f"{self.source} trading calendar returned stat {stat!r}",
            )

        fields = payload.get("fields")
        if not isinstance(fields, list) or not fields or fields[0] != self.date_field:
            raise SourceDataError(
                "schema_mismatch",
                f"{self.source} trading calendar must publish {self.date_field!r} "
                f"as its first column, got {fields!r}",
            )

        rows = payload.get("data")
        if not isinstance(rows, list):
            raise SourceDataError(
                "schema_mismatch",
                f"{self.source} trading calendar data must be an array",
            )
        if not rows:
            raise SourceDataError(
                "empty_coverage",
                f"{self.source} published no trading day for "
                f"{request.month:%Y-%m}; a month with no open day is not a "
                "result this adapter may assume",
            )

        days: set[date] = set()
        for row_number, row in enumerate(rows, start=1):
            if not isinstance(row, list) or not row:
                raise SourceDataError(
                    "schema_mismatch",
                    f"{self.source} row {row_number} is not a non-empty array",
                )
            day = _roc_slashed_date(
                row[0], f"{self.source} row {row_number} trading date"
            )
            if (day.year, day.month) != (request.month.year, request.month.month):
                raise SourceDataError(
                    "date_mismatch",
                    f"{self.source} returned {day.isoformat()} for requested month "
                    f"{request.month:%Y-%m}",
                )
            days.add(day)

        return ParsedTradingCalendar(
            market=self.market,
            month=request.month,
            trading_days=tuple(sorted(days)),
            source_fields=(self.date_field,),
        )


class TWSETradingCalendarAdapter(TradingCalendarAdapter):
    source = "twse"
    market = "TWSE"
    version = "twse-fmtqik-trading-calendar:v2"
    endpoint = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
    date_field = "日期"


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


def _roc_slashed_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise SourceDataError("invalid_date", f"{field} is not a string: {value!r}")
    parts = value.strip().split("/")
    if len(parts) != 3 or not all(
        part.isascii() and part.isdigit() for part in parts
    ):
        raise SourceDataError(
            "invalid_date", f"{field} is not ROC YYY/MM/DD: {value!r}"
        )
    year, month, day = parts
    if len(year) != 3 or len(month) != 2 or len(day) != 2:
        raise SourceDataError(
            "invalid_date", f"{field} is not ROC YYY/MM/DD: {value!r}"
        )
    try:
        return date(int(year) + 1911, int(month), int(day))
    except ValueError as error:
        raise SourceDataError(
            "invalid_date", f"{field} is invalid: {value!r}"
        ) from error
