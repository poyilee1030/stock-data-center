"""Query declared expected coverage, and report what a dataset is missing."""

from __future__ import annotations

from datetime import date, timedelta

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.coverage.models import (
    CoverageReport,
    ExpectedCoverage,
    UnknownPricedSecurity,
)
from stock_data_center.db.metadata import (
    daily_price_versions,
    dataset_expected_coverage,
    security,
    security_metadata_versions,
)
from stock_data_center.market_calendar import TradingCalendarService
from stock_data_center.pit.contracts import get_contract


class ExpectedCoverageService:
    """Read the declarations, and expand one into the periods it implies."""

    def __init__(self, calendar: TradingCalendarService | None = None) -> None:
        self._calendar = calendar or TradingCalendarService()

    def declarations(self, connection: Connection) -> tuple[ExpectedCoverage, ...]:
        rows = connection.execute(
            sa.select(dataset_expected_coverage).order_by(
                dataset_expected_coverage.c.dataset_code,
                dataset_expected_coverage.c.market,
            )
        ).mappings()
        return tuple(ExpectedCoverage(**dict(row)) for row in rows)

    def declaration(
        self, connection: Connection, *, dataset_code: str, market: str
    ) -> ExpectedCoverage:
        row = connection.execute(
            sa.select(dataset_expected_coverage).where(
                dataset_expected_coverage.c.dataset_code == dataset_code,
                dataset_expected_coverage.c.market == market,
            )
        ).mappings().one_or_none()
        if row is None:
            raise LookupError(
                f"no expected coverage is declared for {dataset_code}/{market}; "
                "declare it with the adapter that fills it"
            )
        return ExpectedCoverage(**dict(row))

    def expected_periods(
        self,
        connection: Connection,
        *,
        dataset_code: str,
        market: str,
        start: date,
        end: date,
    ) -> tuple[date, ...]:
        declaration = self.declaration(
            connection, dataset_code=dataset_code, market=market
        )
        window_start = max(start, declaration.window_start)
        window_end = end if declaration.window_end is None else min(
            end, declaration.window_end
        )
        if window_start > window_end:
            return ()
        if declaration.cadence == "trading_day":
            return self._calendar.trading_days(
                connection,
                market=declaration.calendar_market,
                start=window_start,
                end=window_end,
            )
        if declaration.cadence == "calendar_month":
            return tuple(_months_between(window_start, window_end))
        raise ValueError(f"unsupported cadence {declaration.cadence!r}")


class CoverageValidator:
    """Report the difference between declared expectation and stored periods."""

    def __init__(
        self,
        expected: ExpectedCoverageService | None = None,
        calendar: TradingCalendarService | None = None,
    ) -> None:
        self._calendar = calendar or TradingCalendarService()
        self._expected = expected or ExpectedCoverageService(self._calendar)

    def report(
        self,
        connection: Connection,
        *,
        dataset_code: str,
        market: str,
        start: date,
        end: date,
    ) -> CoverageReport:
        if start > end:
            raise ValueError("start must not be after end")
        declaration = self._expected.declaration(
            connection, dataset_code=dataset_code, market=market
        )
        window_start = max(start, declaration.window_start)
        window_end = end if declaration.window_end is None else min(
            end, declaration.window_end
        )
        expected = self._expected.expected_periods(
            connection, dataset_code=dataset_code, market=market, start=start, end=end
        )
        observed = self._observed_periods(
            connection, declaration=declaration, start=start, end=end
        )
        non_trading_days: tuple[date, ...] = ()
        if declaration.cadence == "trading_day" and window_start <= window_end:
            # The closure list follows the declared window, exactly as the
            # expectation does; dates outside it are not this dataset's
            # closures any more than they are its gaps.
            open_days = set(
                self._calendar.trading_days(
                    connection,
                    market=declaration.calendar_market,
                    start=window_start,
                    end=window_end,
                )
            )
            non_trading_days = tuple(
                day
                for day in _days_between(window_start, window_end)
                if day not in open_days
            )
        expected_set = set(expected)
        observed_set = set(observed)
        return CoverageReport(
            dataset_code=dataset_code,
            market=market,
            start=start,
            end=end,
            window_start=declaration.window_start,
            window_end=declaration.window_end,
            expected=expected,
            observed=tuple(day for day in observed if day in expected_set),
            missing=tuple(day for day in expected if day not in observed_set),
            unexpected=tuple(day for day in observed if day not in expected_set),
            non_trading_days=non_trading_days,
        )

    @staticmethod
    def _observed_periods(
        connection: Connection,
        *,
        declaration: ExpectedCoverage,
        start: date,
        end: date,
    ) -> tuple[date, ...]:
        """Distinct periods this source holds at least one row for.

        Filtered by source: one market's rows are not another's coverage, and
        several markets share a version table.

        Deliberately period-grained: per-security completeness would need a
        PIT-resolved universe, and asking today's universe would make an old
        report change whenever a security is added.
        """
        table = get_contract(declaration.dataset_code).version_table
        column = table.c[declaration.period_column]
        predicates = [column >= start, column <= end]
        if "source" in table.c:
            predicates.append(table.c.source == declaration.source)
        if "market" in table.c:
            predicates.append(table.c.market == declaration.market)
        rows = connection.execute(
            sa.select(sa.distinct(column)).where(*predicates).order_by(column)
        ).scalars()
        return tuple(rows)


def _days_between(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _months_between(start: date, end: date):
    month = start.replace(day=1)
    last = end.replace(day=1)
    while month <= last:
        yield month
        month = (month.replace(day=28) + timedelta(days=7)).replace(day=1)


def securities_without_metadata(
    connection: Connection,
    *,
    source: str,
    start: date | None = None,
    end: date | None = None,
) -> tuple[UnknownPricedSecurity, ...]:
    """Securities this price source carries that no metadata version describes.

    Metadata is checked across every source: a security described by any of
    them is known, and the question here is whether anything describes it at
    all. Deliberately not PIT-resolved — this is an operational report about
    what the Data Center holds, not a claim about what was knowable on a date.
    """
    priced = (
        sa.select(
            daily_price_versions.c.security_id,
            sa.func.min(daily_price_versions.c.trade_date).label("first_priced_on"),
            sa.func.max(daily_price_versions.c.trade_date).label("last_priced_on"),
            sa.func.count(sa.distinct(daily_price_versions.c.trade_date)).label(
                "priced_days"
            ),
        )
        .where(daily_price_versions.c.source == source)
        .group_by(daily_price_versions.c.security_id)
    )
    if start is not None:
        priced = priced.where(daily_price_versions.c.trade_date >= start)
    if end is not None:
        priced = priced.where(daily_price_versions.c.trade_date <= end)
    priced = priced.subquery()

    described = sa.select(security_metadata_versions.c.security_id).distinct().subquery()
    rows = connection.execute(
        sa.select(
            priced.c.security_id,
            security.c.security_code,
            priced.c.first_priced_on,
            priced.c.last_priced_on,
            priced.c.priced_days,
        )
        .select_from(
            priced.join(security, security.c.id == priced.c.security_id)
            .outerjoin(described, described.c.security_id == priced.c.security_id)
        )
        .where(described.c.security_id.is_(None))
        .order_by(security.c.security_code)
    ).mappings()
    return tuple(UnknownPricedSecurity(**dict(row)) for row in rows)
