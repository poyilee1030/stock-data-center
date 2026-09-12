"""Normalized append-only writes for Phase 3 observed datasets."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Connection, RowMapping
from sqlalchemy.dialects.postgresql import insert

from stock_data_center.db.metadata import (
    daily_price_versions,
    publication_evidence,
    publication_evidence_observations,
    security,
    security_metadata_versions,
)


@dataclass(frozen=True, slots=True)
class LineageRef:
    raw_artifact_id: UUID
    ingest_run_id: UUID


@dataclass(frozen=True, slots=True)
class SecurityMetadataObservation:
    effective_from: date
    market: str
    name: str
    effective_to: date | None = None
    industry: str | None = None
    listed_on: date | None = None
    delisted_on: date | None = None


@dataclass(frozen=True, slots=True)
class DailyPriceObservation:
    trade_date: date
    open_price: Decimal | None = None
    high_price: Decimal | None = None
    low_price: Decimal | None = None
    close_price: Decimal | None = None
    volume: Decimal | None = None
    trade_value: Decimal | None = None
    trade_count: int | None = None
    price_change: Decimal | None = None
    price_direction: str | None = None
    bid_snapshot: str | None = None
    ask_snapshot: str | None = None
    last_bid_price: Decimal | None = None
    last_ask_price: Decimal | None = None
    last_bid_volume: Decimal | None = None
    last_ask_volume: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PublicationObservation:
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
class WrittenVersion:
    version_id: int
    business_content_hash: str
    ingested_at: datetime
    created: bool


class MarketDataWriter:
    """Persist normalized rows while leaving hashes/times authoritative in DB."""

    def register_security(
        self,
        connection: Connection,
        *,
        security_code: str,
    ) -> int:
        inserted = connection.execute(
            insert(security)
            .values(security_code=security_code)
            .on_conflict_do_nothing(index_elements=[security.c.security_code])
            .returning(security.c.id)
        ).scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing = connection.scalar(
            sa.select(security.c.id).where(
                security.c.security_code == security_code
            )
        )
        if existing is None:
            raise RuntimeError("conflicting security identity disappeared")
        return existing

    def append_security_metadata(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        observation: SecurityMetadataObservation,
        lineage: LineageRef,
    ) -> WrittenVersion:
        business = _dataclass_values(observation)
        return self._append_version(
            connection,
            table=security_metadata_versions,
            constraint="uq_security_metadata_business_revision",
            identity={
                "security_id": security_id,
                "source": source,
                "effective_from": observation.effective_from,
            },
            business=business,
            lineage=lineage,
        )

    def append_daily_price(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        observation: DailyPriceObservation,
        lineage: LineageRef,
    ) -> WrittenVersion:
        business = _dataclass_values(observation)
        return self._append_version(
            connection,
            table=daily_price_versions,
            constraint="uq_daily_price_business_revision",
            identity={
                "security_id": security_id,
                "source": source,
                "trade_date": observation.trade_date,
            },
            business=business,
            lineage=lineage,
        )

    def append_publication_evidence(
        self,
        connection: Connection,
        *,
        dataset_code: Literal["security_metadata", "daily_price"],
        source: str,
        version_id: int,
        observation: PublicationObservation,
        lineage: LineageRef,
    ) -> int:
        target_column = {
            "security_metadata": "security_metadata_version_id",
            "daily_price": "daily_price_version_id",
        }[dataset_code]
        values = _dataclass_values(observation)
        values.update(
            {
                "dataset_code": dataset_code,
                "source": source,
                target_column: version_id,
                "publication_evidence_hash": "0" * 64,
                "recorded_at": sa.func.statement_timestamp(),
                "raw_artifact_id": lineage.raw_artifact_id,
                "ingest_run_id": lineage.ingest_run_id,
            }
        )
        inserted = connection.execute(
            insert(publication_evidence)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[publication_evidence.c.publication_evidence_hash]
            )
            .returning(publication_evidence.c.id)
        ).scalar_one_or_none()
        if inserted is not None:
            evidence_id = inserted
        else:
            predicates = [
                publication_evidence.c.dataset_code == dataset_code,
                publication_evidence.c.source == source,
                publication_evidence.c[target_column] == version_id,
            ]
            predicates.extend(
                publication_evidence.c[name].is_not_distinct_from(value)
                for name, value in _dataclass_values(observation).items()
            )
            evidence_id = connection.execute(
                sa.select(publication_evidence.c.id).where(*predicates)
            ).scalar_one()
        connection.execute(
            insert(publication_evidence_observations)
            .values(
                publication_evidence_id=evidence_id,
                raw_artifact_id=lineage.raw_artifact_id,
                ingest_run_id=lineage.ingest_run_id,
            )
            .on_conflict_do_nothing()
        )
        return evidence_id

    @staticmethod
    def _append_version(
        connection: Connection,
        *,
        table: sa.Table,
        constraint: str,
        identity: dict[str, object],
        business: dict[str, object],
        lineage: LineageRef,
    ) -> WrittenVersion:
        values = {
            **identity,
            **business,
            "business_content_hash": "0" * 64,
            "ingested_at": sa.func.statement_timestamp(),
            "raw_artifact_id": lineage.raw_artifact_id,
            "ingest_run_id": lineage.ingest_run_id,
        }
        inserted = connection.execute(
            insert(table)
            .values(**values)
            .on_conflict_do_nothing(constraint=constraint)
            .returning(
                table.c.id,
                table.c.business_content_hash,
                table.c.ingested_at,
            )
        ).mappings().one_or_none()
        if inserted is not None:
            return _written(inserted, created=True)

        predicates = [table.c[name] == value for name, value in identity.items()]
        predicates.extend(
            table.c[name].is_not_distinct_from(value)
            for name, value in business.items()
        )
        existing = connection.execute(
            sa.select(
                table.c.id,
                table.c.business_content_hash,
                table.c.ingested_at,
            ).where(*predicates)
        ).mappings().one()
        return _written(existing, created=False)


def _dataclass_values(instance: object) -> dict[str, object]:
    return {field.name: getattr(instance, field.name) for field in fields(instance)}


def _written(row: RowMapping, *, created: bool) -> WrittenVersion:
    return WrittenVersion(
        version_id=row["id"],
        business_content_hash=row["business_content_hash"],
        ingested_at=row["ingested_at"],
        created=created,
    )
