"""Read the observed trading calendar, or refuse when it does not reach."""

from __future__ import annotations

from datetime import date, timedelta

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import trading_calendar_versions
from stock_data_center.market_calendar.models import CalendarCoverageError


class TradingCalendarService:
    """Answer calendar questions only inside imported, contiguous coverage.

    Every answer comes from the latest ingested version of each month, so a
    corrected closure supersedes the month it corrected. Outside the covered
    range the service raises: an unimported month is indistinguishable from a
    month of closures, and returning ``False`` would hide that.
    """

    def trading_days(
        self, connection: Connection, *, market: str, start: date, end: date
    ) -> tuple[date, ...]:
        if start > end:
            raise ValueError("start must not be after end")
        self._require_coverage(connection, market=market, start=start, end=end)
        return tuple(
            day
            for day in self._days(connection, market=market)
            if start <= day <= end
        )

    def is_trading_day(
        self, connection: Connection, *, market: str, day: date
    ) -> bool:
        self._require_coverage(connection, market=market, start=day, end=day)
        return day in self._days(connection, market=market)

    def next_trading_day_on_or_after(
        self, connection: Connection, *, market: str, day: date
    ) -> date:
        """The first open day at or after ``day``.

        ADR-0020 moves every release-rule deadline that falls on a closure to
        the next business day through this call.
        """
        for candidate in self._days(connection, market=market):
            if candidate >= day:
                self._require_coverage(
                    connection, market=market, start=day, end=candidate
                )
                return candidate
        raise CalendarCoverageError(
            f"{market} calendar has no trading day on or after {day.isoformat()}; "
            "import the months that follow before asking"
        )

    def coverage_through(
        self, connection: Connection, *, market: str
    ) -> date | None:
        """The last date the calendar can speak for, with no gap before it."""
        months = self._months(connection, market=market)
        if not months:
            return None
        first_month = min(months)
        reach = None
        month = first_month
        while month in months:
            reach = months[month]
            month = _next_month(month)
        return reach

    def _require_coverage(
        self, connection: Connection, *, market: str, start: date, end: date
    ) -> None:
        months = self._months(connection, market=market)
        if not months:
            raise CalendarCoverageError(f"{market} calendar has no imported month")
        reach = self.coverage_through(connection, market=market)
        earliest = min(months)
        if start < earliest or end > reach:
            raise CalendarCoverageError(
                f"{market} calendar covers {earliest.isoformat()} through "
                f"{reach.isoformat()}; {start.isoformat()}..{end.isoformat()} "
                "is outside it"
            )

    def _months(self, connection: Connection, *, market: str) -> dict[date, date]:
        """Latest version of each imported month: month -> coverage_through."""
        return {
            row["calendar_month"]: row["coverage_through"]
            for row in connection.execute(
                self._latest_versions(market).with_only_columns(
                    trading_calendar_versions.c.calendar_month,
                    trading_calendar_versions.c.coverage_through,
                )
            ).mappings()
        }

    def _days(self, connection: Connection, *, market: str) -> tuple[date, ...]:
        days: list[date] = []
        for row in connection.execute(
            self._latest_versions(market).with_only_columns(
                trading_calendar_versions.c.trading_days
            )
        ).mappings():
            days.extend(row["trading_days"])
        return tuple(sorted(days))

    @staticmethod
    def _latest_versions(market: str) -> sa.Select:
        ranked = sa.select(
            trading_calendar_versions.c.id,
            sa.func.row_number()
            .over(
                partition_by=(
                    trading_calendar_versions.c.market,
                    trading_calendar_versions.c.source,
                    trading_calendar_versions.c.calendar_month,
                ),
                order_by=(
                    trading_calendar_versions.c.ingested_at.desc(),
                    trading_calendar_versions.c.id.desc(),
                ),
            )
            .label("rank"),
        ).where(trading_calendar_versions.c.market == market).subquery()
        return (
            sa.select(trading_calendar_versions)
            .join(ranked, ranked.c.id == trading_calendar_versions.c.id)
            .where(ranked.c.rank == 1)
            .order_by(trading_calendar_versions.c.calendar_month)
        )


def _next_month(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=7)).replace(day=1)
