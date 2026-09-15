"""Domain contracts for the observed trading calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID


class CalendarCoverageError(LookupError):
    """The calendar has not imported the range the caller asked about.

    Raised instead of answering, because a missing month is indistinguishable
    from a month of closures unless the caller is told.
    """


@dataclass(frozen=True, slots=True)
class CalendarLineageRef:
    raw_artifact_id: UUID
    ingest_run_id: UUID


@dataclass(frozen=True, slots=True)
class TradingCalendarObservation:
    """One month of actual trading days, exactly as one source published them."""

    market: str
    calendar_month: date
    trading_days: tuple[date, ...]
    coverage_through: date

    def __post_init__(self) -> None:
        if self.calendar_month.day != 1:
            raise ValueError("calendar_month must be the first day of the month")
        if not self.trading_days:
            raise ValueError("a published month has at least one trading day")
        if tuple(sorted(set(self.trading_days))) != self.trading_days:
            raise ValueError("trading_days must be sorted and distinct")
        first, last = self.trading_days[0], self.trading_days[-1]
        if (first.year, first.month) != (
            self.calendar_month.year,
            self.calendar_month.month,
        ) or (last.year, last.month) != (
            self.calendar_month.year,
            self.calendar_month.month,
        ):
            raise ValueError("every trading day must fall inside calendar_month")
        if self.coverage_through < last:
            raise ValueError("coverage_through must cover every published day")


@dataclass(frozen=True, slots=True)
class WrittenCalendarVersion:
    version_id: int
    business_content_hash: str
    ingested_at: datetime
    created: bool
