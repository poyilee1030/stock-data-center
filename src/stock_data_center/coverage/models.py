"""Contracts for declared expected coverage and the gap report."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class ExpectedCoverage:
    """What one dataset is expected to hold, for one market.

    This is a declaration in the database rather than logic inside a report,
    because Step 28 turns it into fetch jobs.
    """

    dataset_code: str
    market: str
    source: str
    calendar_market: str
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
    # Periods the dataset holds but the declaration never expected — a price
    # row on a day the market never opened, say. Surfaced rather than filtered
    # away, because silently dropping it hides the anomaly it is.
    unexpected: tuple[date, ...]
    # Periods inside the window the calendar says are legitimately absent: the
    # closed days of a `trading_day` cadence, and for `trading_week` the
    # Monday of each week the market never opened (Step 24-b).
    non_trading_days: tuple[date, ...]

    @property
    def is_complete(self) -> bool:
        return not self.missing and not self.unexpected


@dataclass(frozen=True, slots=True)
class UnknownPricedSecurity:
    """A security the price feed carries that no metadata source describes.

    The whole-market feeds price every listed instrument; the company snapshots
    Step 10 imports describe only companies. ETFs, TDRs and preferred shares
    therefore have prices and no name, industry or venue. That is a real and
    expected gap, not a defect — but it has to be visible, because a consumer
    joining prices to metadata silently loses these rows.
    """

    security_id: int
    security_code: str
    first_priced_on: date
    last_priced_on: date
    priced_days: int
