"""Append-only writer for observed trading-calendar months."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert

from stock_data_center.db.metadata import (
    publication_evidence,
    publication_evidence_observations,
    trading_calendar_version_observations,
    trading_calendar_versions,
)
from stock_data_center.market_calendar.models import (
    CalendarLineageRef,
    TradingCalendarObservation,
    WrittenCalendarVersion,
)


class TradingCalendarWriter:
    """Store one published month, reusing the version when nothing changed."""

    def append_month(
        self,
        connection: Connection,
        *,
        source: str,
        observation: TradingCalendarObservation,
        lineage: CalendarLineageRef,
    ) -> WrittenCalendarVersion:
        content = {
            "market": observation.market,
            "calendar_month": observation.calendar_month,
            "trading_days": list(observation.trading_days),
            "coverage_through": observation.coverage_through,
        }
        row = connection.execute(
            insert(trading_calendar_versions)
            .values(
                **content,
                source=source,
                business_content_hash="0" * 64,
                ingested_at=sa.func.statement_timestamp(),
                raw_artifact_id=lineage.raw_artifact_id,
                ingest_run_id=lineage.ingest_run_id,
            )
            .on_conflict_do_nothing(constraint="uq_trading_calendar_business_revision")
            .returning(
                trading_calendar_versions.c.id,
                trading_calendar_versions.c.business_content_hash,
                trading_calendar_versions.c.ingested_at,
            )
        ).mappings().one_or_none()
        created = row is not None
        if row is None:
            row = connection.execute(
                sa.select(
                    trading_calendar_versions.c.id,
                    trading_calendar_versions.c.business_content_hash,
                    trading_calendar_versions.c.ingested_at,
                ).where(
                    trading_calendar_versions.c.source == source,
                    trading_calendar_versions.c.market == observation.market,
                    trading_calendar_versions.c.calendar_month
                    == observation.calendar_month,
                    trading_calendar_versions.c.trading_days
                    == list(observation.trading_days),
                    trading_calendar_versions.c.coverage_through
                    == observation.coverage_through,
                )
            ).mappings().one()

        connection.execute(
            insert(trading_calendar_version_observations)
            .values(
                calendar_version_id=row["id"],
                raw_artifact_id=lineage.raw_artifact_id,
                ingest_run_id=lineage.ingest_run_id,
            )
            .on_conflict_do_nothing()
        )
        return WrittenCalendarVersion(
            version_id=row["id"],
            business_content_hash=row["business_content_hash"],
            ingested_at=row["ingested_at"],
            created=created,
        )

    def append_unknown_publication_evidence(
        self,
        connection: Connection,
        *,
        source: str,
        version_id: int,
        evidence_source: str,
        lineage: CalendarLineageRef,
    ) -> int:
        """Record that the source publishes no release instant for this month.

        Until ADR-0020 lands, this is the only evidence the calendar can carry,
        exactly as every other observed domain does.
        """
        evidence_id = connection.execute(
            insert(publication_evidence)
            .values(
                dataset_code="trading_calendar",
                source=source,
                evidence_kind="unknown",
                published_at=None,
                recorded_at=sa.func.statement_timestamp(),
                evidence_source=evidence_source,
                evidence_type="official",
                quality_rank=0,
                trading_calendar_version_id=version_id,
                publication_evidence_hash="0" * 64,
                raw_artifact_id=lineage.raw_artifact_id,
                ingest_run_id=lineage.ingest_run_id,
            )
            .on_conflict_do_nothing(
                index_elements=[publication_evidence.c.publication_evidence_hash]
            )
            .returning(publication_evidence.c.id)
        ).scalar_one_or_none()
        if evidence_id is None:
            evidence_id = connection.scalar(
                sa.select(publication_evidence.c.id).where(
                    publication_evidence.c.dataset_code == "trading_calendar",
                    publication_evidence.c.source == source,
                    publication_evidence.c.trading_calendar_version_id == version_id,
                    publication_evidence.c.evidence_kind == "unknown",
                    publication_evidence.c.evidence_source == evidence_source,
                )
            )
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
