"""Stable input and output types for the cache-free PIT resolver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal, Mapping
from uuid import UUID

from stock_data_center.pit.errors import InvalidPITContextError


def _utc_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidPITContextError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class MarketPITContext:
    information_as_of: datetime
    knowledge_as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "information_as_of",
            _utc_aware(self.information_as_of, "information_as_of"),
        )
        object.__setattr__(
            self,
            "knowledge_as_of",
            _utc_aware(self.knowledge_as_of, "knowledge_as_of"),
        )

    @property
    def mode(self) -> Literal["market"]:
        return "market"


@dataclass(frozen=True, slots=True)
class SystemPITContext:
    system_as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "system_as_of",
            _utc_aware(self.system_as_of, "system_as_of"),
        )

    @property
    def mode(self) -> Literal["system"]:
        return "system"


PITContext = MarketPITContext | SystemPITContext


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    dataset_code: str
    source: str
    supports_market_pit: bool
    supports_system_pit: bool
    publication_time_quality: int
    evidence_status: str
    is_canonical: bool


@dataclass(frozen=True, slots=True)
class AuthoritativeEvidence:
    evidence_id: int
    evidence_kind: str
    published_at: datetime | None
    recorded_at: datetime
    evidence_source: str
    evidence_type: str
    quality_rank: int
    publication_evidence_hash: str
    supersedes_evidence_id: int | None
    raw_artifact_id: UUID
    raw_artifact_hash: str
    raw_artifact_uri: str
    ingest_run_id: UUID
    ingest_run_status: str
    source_uri: str
    fetched_at: datetime

    @property
    def affirms_publication(self) -> bool:
        return (
            self.evidence_kind in {"assertion", "correction"}
            and self.published_at is not None
        )


@dataclass(frozen=True, slots=True)
class Provenance:
    version_id: int
    business_content_hash: str
    ingested_at: datetime
    raw_artifact_id: UUID
    raw_artifact_hash: str
    raw_artifact_uri: str
    raw_artifact_stored_at: datetime
    ingest_run_id: UUID
    ingest_run_status: str
    ingest_run_started_at: datetime
    ingest_run_completed_at: datetime | None
    source_uri: str
    fetched_at: datetime
    aggregate_seal_id: int | None = None


@dataclass(frozen=True, slots=True)
class ResolvedRecord:
    dataset_code: str
    source: str
    pit_mode: Literal["market", "system"]
    information_as_of: datetime | None
    knowledge_as_of: datetime | None
    system_as_of: datetime | None
    data: Mapping[str, Any]
    provenance: Provenance
    authoritative_evidence: AuthoritativeEvidence | None

    @classmethod
    def create(
        cls,
        *,
        dataset_code: str,
        source: str,
        context: PITContext,
        data: Mapping[str, Any],
        provenance: Provenance,
        evidence: AuthoritativeEvidence | None,
    ) -> ResolvedRecord:
        market = context if isinstance(context, MarketPITContext) else None
        system = context if isinstance(context, SystemPITContext) else None
        return cls(
            dataset_code=dataset_code,
            source=source,
            pit_mode=context.mode,
            information_as_of=market.information_as_of if market else None,
            knowledge_as_of=market.knowledge_as_of if market else None,
            system_as_of=system.system_as_of if system else None,
            data=MappingProxyType(dict(data)),
            provenance=provenance,
            authoritative_evidence=evidence,
        )
