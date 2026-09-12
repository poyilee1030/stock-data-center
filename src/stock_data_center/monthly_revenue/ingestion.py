"""Normalized append-only writes for observed monthly revenue."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Connection, RowMapping
from sqlalchemy.dialects.postgresql import insert

from stock_data_center.db.metadata import (
    monthly_revenue_versions,
    publication_evidence,
)
from stock_data_center.monthly_revenue.models import RevenuePeriod


@dataclass(frozen=True, slots=True)
class RevenueLineageRef:
    raw_artifact_id: UUID
    ingest_run_id: UUID


@dataclass(frozen=True, slots=True)
class MonthlyRevenueObservation:
    period: RevenuePeriod
    revenue: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class MonthlyRevenuePublication:
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
class WrittenRevenueVersion:
    version_id: int
    business_content_hash: str
    ingested_at: datetime
    created: bool


class MonthlyRevenueWriter:
    """Append normalized revenue and independent publication evidence."""

    def append_revenue(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        observation: MonthlyRevenueObservation,
        lineage: RevenueLineageRef,
    ) -> WrittenRevenueVersion:
        values = {
            "security_id": security_id,
            "source": source,
            "revenue_year": observation.period.year,
            "revenue_month": observation.period.month,
            "revenue": observation.revenue,
            "currency": observation.currency,
            "business_content_hash": "0" * 64,
            "ingested_at": sa.func.statement_timestamp(),
            "raw_artifact_id": lineage.raw_artifact_id,
            "ingest_run_id": lineage.ingest_run_id,
        }
        inserted = connection.execute(
            insert(monthly_revenue_versions)
            .values(**values)
            .on_conflict_do_nothing(
                constraint="uq_monthly_revenue_business_revision"
            )
            .returning(
                monthly_revenue_versions.c.id,
                monthly_revenue_versions.c.business_content_hash,
                monthly_revenue_versions.c.ingested_at,
            )
        ).mappings().one_or_none()
        if inserted is not None:
            return _written(inserted, created=True)

        existing = connection.execute(
            sa.select(
                monthly_revenue_versions.c.id,
                monthly_revenue_versions.c.business_content_hash,
                monthly_revenue_versions.c.ingested_at,
            ).where(
                monthly_revenue_versions.c.security_id == security_id,
                monthly_revenue_versions.c.source == source,
                monthly_revenue_versions.c.revenue_year
                == observation.period.year,
                monthly_revenue_versions.c.revenue_month
                == observation.period.month,
                monthly_revenue_versions.c.revenue == observation.revenue,
                monthly_revenue_versions.c.currency == observation.currency,
            )
        ).mappings().one()
        return _written(existing, created=False)

    def append_publication_evidence(
        self,
        connection: Connection,
        *,
        source: str,
        version_id: int,
        observation: MonthlyRevenuePublication,
        lineage: RevenueLineageRef,
    ) -> int:
        evidence_values = _dataclass_values(observation)
        values = {
            **evidence_values,
            "dataset_code": "monthly_revenue",
            "source": source,
            "monthly_revenue_version_id": version_id,
            "publication_evidence_hash": "0" * 64,
            "recorded_at": sa.func.statement_timestamp(),
            "raw_artifact_id": lineage.raw_artifact_id,
            "ingest_run_id": lineage.ingest_run_id,
        }
        inserted = connection.execute(
            insert(publication_evidence)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[publication_evidence.c.publication_evidence_hash]
            )
            .returning(publication_evidence.c.id)
        ).scalar_one_or_none()
        if inserted is not None:
            return inserted

        predicates = [
            publication_evidence.c.dataset_code == "monthly_revenue",
            publication_evidence.c.source == source,
            publication_evidence.c.monthly_revenue_version_id == version_id,
        ]
        predicates.extend(
            publication_evidence.c[name].is_not_distinct_from(value)
            for name, value in evidence_values.items()
        )
        return connection.execute(
            sa.select(publication_evidence.c.id).where(*predicates)
        ).scalar_one()


def _dataclass_values(instance: object) -> dict[str, object]:
    return {field.name: getattr(instance, field.name) for field in fields(instance)}


def _written(row: RowMapping, *, created: bool) -> WrittenRevenueVersion:
    return WrittenRevenueVersion(
        version_id=row["id"],
        business_content_hash=row["business_content_hash"],
        ingested_at=row["ingested_at"],
        created=created,
    )
