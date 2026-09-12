"""Monthly revenue domain value types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, order=True, slots=True)
class RevenuePeriod:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1900 <= self.year <= 9999:
            raise ValueError("year must be between 1900 and 9999")
        if not 1 <= self.month <= 12:
            raise ValueError("month must be between 1 and 12")


@dataclass(frozen=True, slots=True)
class RevenueLineageObservation:
    raw_artifact_id: UUID
    raw_artifact_hash: str
    raw_artifact_uri: str
    ingest_run_id: UUID
    ingest_run_status: str
    source_uri: str
    fetched_at: datetime
