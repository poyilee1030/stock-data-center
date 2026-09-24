"""Value types for Phase 8 observed market-reference data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID

from stock_data_center.ingestion.observations import (  # noqa: F401 - moved, Step 35-d-1
    _MONEY_SCALES,
    _RATIO_SCALES,
    _SIGNED,
    _SIGNED_MONEY,
    ACTION_TYPES,
    CAPITAL_REDUCTION_KINDS,
    TWD_SCALE,
    ActionType,
    CapitalReductionKind,
    CorporateActionObservation,
    MarketIndexObservation,
    OfficialValuationObservation,
    SignedTwdAmount,
    TwdAmount,
    _decimal,
)


class AmountScale(str, Enum):
    MAJOR = "major"
    THOUSAND = "thousand"
    MILLION = "million"

    @property
    def multiplier(self) -> Decimal:
        return {
            AmountScale.MAJOR: Decimal(1),
            AmountScale.THOUSAND: Decimal(1000),
            AmountScale.MILLION: Decimal(1000000),
        }[self]


# The widest TWD column is NUMERIC(24, 8): TPEx publishes cash dividends such as
# 3.42936322 per share. PostgreSQL rounds a value to its column's scale without
# complaint, so each observation also checks the scale of the column it lands in.


@dataclass(frozen=True, slots=True)
class SourceTwdAmount:
    value: Decimal
    scale: AmountScale

    def __post_init__(self) -> None:
        _decimal("source TWD amount", self.value, 4, positive=True)
        if not isinstance(self.scale, AmountScale):
            raise ValueError("scale must be an AmountScale")

    def to_canonical(self) -> TwdAmount:
        return TwdAmount(self.value * self.scale.multiplier)


@dataclass(frozen=True, slots=True)
class Phase8LineageRef:
    raw_artifact_id: UUID
    ingest_run_id: UUID


@dataclass(frozen=True, slots=True)
class Phase8Publication:
    evidence_kind: Literal["assertion", "correction", "retraction", "unknown"]
    published_at: datetime | None
    evidence_source: str
    evidence_type: str
    quality_rank: int
    supersedes_evidence_id: int | None = None

    def __post_init__(self) -> None:
        if self.published_at is not None:
            if self.published_at.tzinfo is None or self.published_at.utcoffset() is None:
                raise ValueError("published_at must be timezone-aware")
            object.__setattr__(self, "published_at", self.published_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class MarketIndexMetadataObservation:
    effective_from: date
    market: str
    name: str
    effective_to: date | None = None

    def __post_init__(self) -> None:
        if not self.market or not self.name:
            raise ValueError("market and name must be nonempty")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")


# Each term's column scale. Share ratios carry twelve places because the
# exchanges publish them per thousand shares with eight: 202.11906001 per 1,000
# is 0.20211906001 (ROADMAP Step 19).
# The one term a source publishes as a signed difference.


@dataclass(frozen=True, slots=True)
class WrittenPhase8Version:
    dataset_code: str
    version_id: int
    business_content_hash: str
    ingested_at: datetime
    created: bool


@dataclass(frozen=True, slots=True)
class Phase8LineageObservation:
    raw_artifact_id: UUID
    raw_artifact_hash: str
    raw_artifact_uri: str
    ingest_run_id: UUID
    ingest_run_status: str
    source_uri: str
    fetched_at: datetime
