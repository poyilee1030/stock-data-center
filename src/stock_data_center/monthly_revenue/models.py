"""Monthly revenue domain value types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from stock_data_center.ingestion.observations import (  # noqa: F401 - moved, Step 35-d-1
    RevenuePeriod,
)


@dataclass(frozen=True, slots=True)
class RevenueLineageObservation:
    raw_artifact_id: UUID
    raw_artifact_hash: str
    raw_artifact_uri: str
    ingest_run_id: UUID
    ingest_run_status: str
    source_uri: str
    fetched_at: datetime
