"""Read the observed trading calendar, or refuse when it does not reach."""

from __future__ import annotations

from datetime import date, timedelta

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import dataset_sources, trading_calendar_versions
from stock_data_center.market_calendar.models import CalendarCoverageError


class TradingCalendarService:
    """Answer calendar questions only inside imported, contiguous coverage.

    Every answer comes from the latest ingested version of each month of one
    source, so a corrected closure supersedes the month it corrected. Outside
    the covered range the service raises: an unimported month is
    indistinguishable from a month of closures, and returning ``False`` would
    hide that.
    """

    def trading_days(
        self,
        connection: Connection,
        *,
        market: str,
        start: date,
        end: date,
        source: str | None = None,
    ) -> tuple[date, ...]:
        if start > end:
            raise ValueError("start must not be after end")
        source = self._source(connection, source)
        self._require_coverage(
            connection, market=market, source=source, start=start, end=end
        )
        return tuple(
            day
            for day in self._days(connection, market=market, source=source)
            if start <= day <= end
        )

    def is_trading_day(
        self,
        connection: Connection,
        *,
        market: str,
        day: date,
        source: str | None = None,
    ) -> bool:
        source = self._source(connection, source)
        self._require_coverage(
            connection, market=market, source=source, start=day, end=day
        )
        return day in self._days(connection, market=market, source=source)

    def next_trading_day_on_or_after(
        self,
        connection: Connection,
        *,
        market: str,
        day: date,
        source: str | None = None,
    ) -> date:
        """The first open day at or after ``day``.

        ADR-0020 moves every release-rule deadline that falls on a closure to
        the next business day through this call.
        """
        source = self._source(connection, source)
        for candidate in self._days(connection, market=market, source=source):
            if candidate >= day:
                self._require_coverage(
                    connection,
                    market=market,
                    source=source,
                    start=day,
                    end=candidate,
                )
                return candidate
        raise CalendarCoverageError(
            f"{market} calendar has no trading day on or after {day.isoformat()}; "
            "import the months that follow before asking"
        )

    def coverage_through(
        self, connection: Connection, *, market: str, source: str | None = None
    ) -> date | None:
        """The last date the calendar can speak for, with no gap before it.

        The walk stops at the first month that did not reach its own end. A
        month published only up to the 11th bounds the calendar there even if
        later months are already imported: the days between were never
        published, and answering them would invent closures.
        """
        source = self._source(connection, source)
        months = self._months(connection, market=market, source=source)
        if not months:
            return None
        month = min(months)
        reach = None
        while month in months:
            reach = months[month]
            if reach < _month_end(month):
                break
            month = _next_month(month)
        return reach

    def _require_coverage(
        self,
        connection: Connection,
        *,
        market: str,
        source: str,
        start: date,
        end: date,
    ) -> None:
        months = self._months(connection, market=market, source=source)
        if not months:
            raise CalendarCoverageError(
                f"{market} calendar has no imported month for source {source!r}"
            )
        reach = self.coverage_through(connection, market=market, source=source)
        earliest = min(months)
        if start < earliest or end > reach:
            raise CalendarCoverageError(
                f"{market} calendar covers {earliest.isoformat()} through "
                f"{reach.isoformat()}; {start.isoformat()}..{end.isoformat()} "
                "is outside it"
            )

    @staticmethod
    def _source(connection: Connection, source: str | None) -> str:
        """The canonical calendar source, unless the caller named one.

        Two sources' calendars are never unioned: they are independent source
        histories, exactly as every other domain treats them.
        """
        if source is not None:
            return source
        canonical = connection.scalar(
            sa.select(dataset_sources.c.source).where(
                dataset_sources.c.dataset_code == "trading_calendar",
                dataset_sources.c.is_canonical.is_(True),
            )
        )
        if canonical is None:
            raise CalendarCoverageError(
                "no canonical trading_calendar source is configured"
            )
        return canonical

    def _months(
        self, connection: Connection, *, market: str, source: str
    ) -> dict[date, date]:
        """Latest version of each imported month: month -> coverage_through."""
        return {
            row["calendar_month"]: row["coverage_through"]
            for row in connection.execute(
                self._latest_versions(market, source).with_only_columns(
                    trading_calendar_versions.c.calendar_month,
                    trading_calendar_versions.c.coverage_through,
                )
            ).mappings()
        }

    def _days(
        self, connection: Connection, *, market: str, source: str
    ) -> tuple[date, ...]:
        days: list[date] = []
        for row in connection.execute(
            self._latest_versions(market, source).with_only_columns(
                trading_calendar_versions.c.trading_days
            )
        ).mappings():
            days.extend(row["trading_days"])
        return tuple(sorted(days))

    @staticmethod
    def _latest_versions(market: str, source: str) -> sa.Select:
        ranked = (
            sa.select(
                trading_calendar_versions.c.id,
                sa.func.row_number()
                .over(
                    partition_by=(trading_calendar_versions.c.calendar_month,),
                    order_by=(
                        trading_calendar_versions.c.ingested_at.desc(),
                        trading_calendar_versions.c.id.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(
                trading_calendar_versions.c.market == market,
                trading_calendar_versions.c.source == source,
            )
            .subquery()
        )
        return (
            sa.select(trading_calendar_versions)
            .join(ranked, ranked.c.id == trading_calendar_versions.c.id)
            .where(ranked.c.rank == 1)
            .order_by(trading_calendar_versions.c.calendar_month)
        )


def _next_month(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=7)).replace(day=1)


def _month_end(month: date) -> date:
    return _next_month(month) - timedelta(days=1)
