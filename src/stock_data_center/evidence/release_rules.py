"""Evaluate a registered release rule for one logical period.

Two shapes of rule, and the difference matters (ADR-0020 §3, corrected in
Step 15-b):

* a **statutory deadline** resolves at the end of its day and moves to the next
  business day, because a filing due on a closed day is filed on the next open
  one;
* a **scheduled instant** resolves at its stated time and never moves. The
  exchange file exists at 03:00 whether or not that day is a trading day, and
  the TDCC rule already names a Sunday, which is never a business day.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import release_rules
from stock_data_center.evidence.models import (
    ReleaseRule,
    ResolvedReleaseInstant,
    UnknownReleaseRuleError,
)
from stock_data_center.market_calendar import TradingCalendarService

END_OF_DAY = time(23, 59, 59)

# Both registered deadline rules cover 上市 and 上櫃 issuers alike, and only one
# calendar exists: no official TPEx trading-day source was found, and TPEx
# opened on exactly the same 1,627 dates as TWSE across the whole v1 window
# (ADR-0021 §4). Naming it here keeps that a recorded decision rather than an
# accidental default; pass `market=` explicitly when that stops being true.
CALENDAR_MARKET = "TWSE"


class ReleaseRuleService:
    """Turn a registered rule and a period into a no-later-than instant."""

    def __init__(self, calendar: TradingCalendarService | None = None) -> None:
        self._calendar = calendar or TradingCalendarService()

    def rule(
        self, connection: Connection, *, rule_id: str, version: int
    ) -> ReleaseRule:
        row = connection.execute(
            sa.select(release_rules).where(
                release_rules.c.rule_id == rule_id,
                release_rules.c.version == version,
            )
        ).mappings().one_or_none()
        if row is None:
            raise UnknownReleaseRuleError(
                f"no release rule {rule_id!r} at version {version}; rules are "
                "versioned and never edited, so add the version you mean"
            )
        return ReleaseRule(**dict(row))

    def instant_for(
        self,
        connection: Connection,
        *,
        rule_id: str,
        version: int,
        period: date,
        market: str = CALENDAR_MARKET,
    ) -> datetime:
        return self.resolve(
            connection,
            rule_id=rule_id,
            version=version,
            period=period,
            market=market,
        ).published_at

    def instants_for(
        self,
        connection: Connection,
        *,
        rule_id: str,
        version: int,
        periods: Iterable[date],
        market: str = CALENDAR_MARKET,
    ) -> dict[date, datetime]:
        """`instant_for` over many periods, reading the rule once."""
        rule = self.rule(connection, rule_id=rule_id, version=version)
        return {
            period: self._evaluate(connection, rule, period, market).published_at
            for period in periods
        }

    def resolve(
        self,
        connection: Connection,
        *,
        rule_id: str,
        version: int,
        period: date,
        market: str = CALENDAR_MARKET,
    ) -> ResolvedReleaseInstant:
        rule = self.rule(connection, rule_id=rule_id, version=version)
        return self._evaluate(connection, rule, period, market)

    def _evaluate(
        self,
        connection: Connection,
        rule: ReleaseRule,
        period: date,
        market: str,
    ) -> ResolvedReleaseInstant:
        zone = ZoneInfo(rule.timezone)

        if rule.rule_kind == "day_of_next_month":
            due = _day_of_next_month(period, int(rule.parameters["day"]))
            moment = _end_of_day(self._shift(connection, rule, due, market), zone)
        elif rule.rule_kind == "quarter_deadline":
            due = _quarter_deadline(period, rule.parameters)
            moment = _end_of_day(self._shift(connection, rule, due, market), zone)
        elif rule.rule_kind == "next_calendar_day_time":
            moment = datetime.combine(
                period + timedelta(days=1),
                _parse_time(rule.parameters["time"]),
                tzinfo=zone,
            )
        elif rule.rule_kind == "weekday_after_time":
            moment = datetime.combine(
                _next_weekday_after(period, int(rule.parameters["weekday"])),
                _parse_time(rule.parameters["time"]),
                tzinfo=zone,
            )
        else:  # pragma: no cover - the CHECK constraint forbids this
            raise ValueError(f"unsupported rule kind {rule.rule_kind!r}")

        return ResolvedReleaseInstant(rule=rule, period=period, published_at=moment)

    def _shift(
        self,
        connection: Connection,
        rule: ReleaseRule,
        due: date,
        market: str,
    ) -> date:
        if not rule.business_day_shift:
            return due
        # The calendar refuses outside its imported coverage rather than
        # guessing, and a rule that guesses its deadline is an invented instant.
        return self._calendar.next_trading_day_on_or_after(
            connection, market=market, day=due
        )


def _day_of_next_month(period: date, day: int) -> date:
    first_of_next = (period.replace(day=1) + timedelta(days=32)).replace(day=1)
    return first_of_next.replace(day=day)


def _quarter_deadline(period: date, parameters: dict) -> date:
    """`period` is the quarter's end date; Q4 is due in the following year."""
    quarter = (period.month - 1) // 3 + 1
    month, day = (int(part) for part in parameters[str(quarter)].split("-"))
    year = period.year + 1 if quarter == 4 else period.year
    return date(year, month, day)


def _next_weekday_after(period: date, weekday: int) -> date:
    """The first such weekday strictly after `period` (Monday is 0)."""
    ahead = (weekday - period.weekday()) % 7
    return period + timedelta(days=ahead or 7)


def _parse_time(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


def _end_of_day(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, END_OF_DAY, tzinfo=zone)
