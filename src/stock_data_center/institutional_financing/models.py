"""Value types for institutional-flow and securities-financing source data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from stock_data_center.ingestion.observations import (  # noqa: F401 - moved, Step 35-d-1
    _RATIO_COLUMN_MAXIMUM,
    ForeignHoldingObservation,
    InstitutionalInvestorObservation,
    InstitutionalMarketSummaryObservation,
    MarginTradingObservation,
    QuantityScale,
    SecuritiesLendingObservation,
    ShareQuantity,
    SourceShareQuantity,
    _require_value,
    _validate_decimal,
    _validate_quantities,
)

# The largest value a NUMERIC(12, 8) column holds.


@dataclass(frozen=True, slots=True)
class SourceLineageRef:
    raw_artifact_id: UUID
    ingest_run_id: UUID


@dataclass(frozen=True, slots=True)
class SourceLineageObservation:
    raw_artifact_id: UUID
    raw_artifact_hash: str
    raw_artifact_uri: str
    ingest_run_id: UUID
    ingest_run_status: str
    source_uri: str
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class SourcePublication:
    evidence_kind: Literal["assertion", "correction", "retraction", "unknown"]
    published_at: datetime | None
    evidence_source: str
    evidence_type: str
    quality_rank: int
    supersedes_evidence_id: int | None = None

    def __post_init__(self) -> None:
        if self.published_at is not None:
            if (
                self.published_at.tzinfo is None
                or self.published_at.utcoffset() is None
            ):
                raise ValueError("published_at must be timezone-aware")
            object.__setattr__(
                self, "published_at", self.published_at.astimezone(UTC)
            )


@dataclass(frozen=True, slots=True)
class WrittenSourceVersion:
    dataset_code: str
    version_id: int
    business_content_hash: str
    ingested_at: datetime
    created: bool
