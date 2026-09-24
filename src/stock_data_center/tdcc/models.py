"""TDCC shareholding-distribution domain value types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from stock_data_center.ingestion.observations import (  # noqa: F401 - moved, Step 35-d-1
    _PERCENT_SCALE,
    TDCC_OPENDATA_V1,
    TDCCBucketObservation,
    TDCCSnapshotObservation,
)
from stock_data_center.pit import ResolvedRecord

# Storage scale of tdcc_distribution.ownership_percent (NUMERIC(12, 8)).

# The official TDCC shareholding-distribution profile registered in
# tdcc_distribution_schemas: levels 1-15 holding, 16 adjustment, 17 total.


class TDCCDistributionError(ValueError):
    """Raised when a distribution does not satisfy its declared profile."""


@dataclass(frozen=True, slots=True)
class TDCCLineageRef:
    raw_artifact_id: UUID
    ingest_run_id: UUID


@dataclass(frozen=True, slots=True)
class TDCCPublication:
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
class TDCCBucket:
    bucket_code: str
    bucket_role: str
    holder_count: int | None
    shares: Decimal
    ownership_percent: Decimal


@dataclass(frozen=True, slots=True)
class ResolvedTDCCSnapshot:
    snapshot: ResolvedRecord
    distribution: tuple[TDCCBucket, ...]


@dataclass(frozen=True, slots=True)
class WrittenSnapshot:
    version_id: int
    business_content_hash: str
    ingested_at: datetime
    created: bool


@dataclass(frozen=True, slots=True)
class SealedSnapshot:
    version_id: int
    business_content_hash: str
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class TDCCLineageObservation:
    raw_artifact_id: UUID
    raw_artifact_hash: str
    raw_artifact_uri: str
    ingest_run_id: UUID
    ingest_run_status: str
    source_uri: str
    fetched_at: datetime
