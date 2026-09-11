"""Domain result types for security and daily market-data access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stock_data_center.pit import ResolvedRecord


@dataclass(frozen=True, slots=True)
class SecurityState:
    """One PIT-resolved security metadata state on a business date."""

    security_id: int
    security_code: str
    market: str
    effective_on: date
    record: ResolvedRecord

    @property
    def is_listed(self) -> bool:
        """Whether the security belongs to the listed universe on the date."""
        listed_on = self.record.data["listed_on"]
        delisted_on = self.record.data["delisted_on"]
        return (listed_on is None or listed_on <= self.effective_on) and (
            delisted_on is None or self.effective_on < delisted_on
        )
