"""TDCC shareholding-distribution domain value types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from stock_data_center.pit import ResolvedRecord


# Storage scale of tdcc_distribution.ownership_percent (NUMERIC(12, 8)).
_PERCENT_SCALE = 8

# The official TDCC shareholding-distribution profile registered in
# tdcc_distribution_schemas: levels 1-15 holding, 16 adjustment, 17 total.
TDCC_OPENDATA_V1 = "tdcc-opendata-v1"


class TDCCDistributionError(ValueError):
    """Raised when a distribution does not satisfy its declared profile."""


@dataclass(frozen=True, slots=True)
class TDCCLineageRef:
    raw_artifact_id: UUID
    ingest_run_id: UUID


@dataclass(frozen=True, slots=True)
class TDCCBucketObservation:
    """One source-native holding-level row, retained verbatim.

    Sign and holder-count rules depend on the bucket's role in the declared
    distribution profile and are checked by the writer and PostgreSQL.
    """

    bucket_code: str
    holder_count: int | None
    shares: Decimal
    ownership_percent: Decimal

    def __post_init__(self) -> None:
        if not self.bucket_code or self.bucket_code != self.bucket_code.strip():
            raise ValueError(
                "bucket_code must be non-empty without surrounding whitespace"
            )
        if self.holder_count is not None and self.holder_count < 0:
            raise ValueError("holder_count must not be negative")
        shares = Decimal(self.shares)
        if shares != shares.to_integral_value():
            raise ValueError("shares must be a whole number")
        object.__setattr__(self, "shares", shares.quantize(Decimal(1)))
        percent = Decimal(self.ownership_percent)
        if not Decimal(-100) <= percent <= Decimal(100):
            raise ValueError("ownership_percent must be between -100 and 100")
        if percent != percent.quantize(Decimal(1).scaleb(-_PERCENT_SCALE)):
            raise ValueError(
                f"ownership_percent must have at most {_PERCENT_SCALE} decimal places"
            )
        object.__setattr__(self, "ownership_percent", percent)


@dataclass(frozen=True, slots=True)
class TDCCSnapshotObservation:
    """A complete distribution for one security and snapshot (data) date."""

    snapshot_date: date
    distribution_schema: str
    distribution: tuple[TDCCBucketObservation, ...]

    def __post_init__(self) -> None:
        distribution = tuple(self.distribution)
        if not distribution:
            raise ValueError("a TDCC snapshot requires at least one distribution row")
        codes = [item.bucket_code for item in distribution]
        if len(set(codes)) != len(codes):
            raise ValueError("a TDCC snapshot cannot contain duplicate bucket codes")
        object.__setattr__(self, "distribution", distribution)


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
