"""Contracts for declared expected coverage and the gap report."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class ExpectedCoverage:
    """What one dataset is expected to hold, for one market.

    This is a declaration in the database rather than logic inside a report,
    because PR #27 turns it into fetch jobs.
    """

    dataset_code: str
    market: str
    cadence: str
    period_column: str
    window_start: date
    window_end: date | None
    note: str


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Which periods a dataset holds, and which it is genuinely missing.

    Periods are the unit, not securities: whether a date is covered must not
    depend on the security universe as it looks today.
    """

    dataset_code: str
    market: str
    start: date
    end: date
    window_start: date
    window_end: date | None
    expected: tuple[date, ...]
    observed: tuple[date, ...]
    missing: tuple[date, ...]
    non_trading_days: tuple[date, ...]

    @property
    def is_complete(self) -> bool:
        return not self.missing
