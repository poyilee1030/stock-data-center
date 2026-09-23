"""Deterministic authoritative publication-evidence selection."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Connection, RowMapping

from stock_data_center.db.metadata import (
    ingest_runs,
    publication_evidence,
    raw_artifact_observations,
    raw_artifacts,
)
from stock_data_center.pit.contracts import DatasetContract
from stock_data_center.pit.models import (
    AuthoritativeEvidence,
    MarketPITContext,
    SourcePolicy,
)


class PublicationEvidenceResolver:
    """Resolve an evidence chain head exactly as specified by ADR-0002."""

    def resolve(
        self,
        connection: Connection,
        contract: DatasetContract,
        version_id: int,
        context: MarketPITContext,
        policy: SourcePolicy,
    ) -> AuthoritativeEvidence | None:
        target = publication_evidence.c[contract.evidence_target_column]
        rows = connection.execute(
            _evidence_statement(contract, context, policy).where(target == version_id)
        ).mappings().all()
        return select_authoritative(rows)

    def resolve_many(
        self,
        connection: Connection,
        contract: DatasetContract,
        version_ids: sa.Select,
        context: MarketPITContext,
        policy: SourcePolicy,
    ) -> dict[int, AuthoritativeEvidence]:
        """The authoritative evidence of every version `version_ids` selects.

        One statement instead of one per version, and the same selection:
        grouping by target and applying `select_authoritative` to each group is
        exactly what `resolve` does to a group of one. A version with no
        authoritative evidence is absent from the result.
        """
        target = publication_evidence.c[contract.evidence_target_column]
        grouped: dict[int, list[RowMapping]] = defaultdict(list)
        for row in connection.execute(
            _evidence_statement(contract, context, policy).where(
                target.in_(version_ids.scalar_subquery())
            )
        ).mappings():
            grouped[int(row[contract.evidence_target_column])].append(row)
        resolved = {
            version_id: select_authoritative(rows)
            for version_id, rows in grouped.items()
        }
        return {
            version_id: evidence
            for version_id, evidence in resolved.items()
            if evidence is not None
        }


def _evidence_statement(
    contract: DatasetContract, context: MarketPITContext, policy: SourcePolicy
) -> sa.Select:
    """Every evidence row the knowledge cutoff and source policy admit."""
    return (
        sa.select(
            publication_evidence,
            raw_artifacts.c.raw_artifact_hash.label("evidence_artifact_hash"),
            raw_artifacts.c.storage_uri.label("evidence_artifact_uri"),
            ingest_runs.c.status.label("evidence_ingest_run_status"),
            raw_artifact_observations.c.source_uri.label("evidence_source_uri"),
            raw_artifact_observations.c.fetched_at.label("evidence_fetched_at"),
        )
        .select_from(
            publication_evidence.join(
                raw_artifact_observations,
                sa.and_(
                    raw_artifact_observations.c.raw_artifact_id
                    == publication_evidence.c.raw_artifact_id,
                    raw_artifact_observations.c.ingest_run_id
                    == publication_evidence.c.ingest_run_id,
                ),
            )
            .join(
                raw_artifacts,
                raw_artifacts.c.id == raw_artifact_observations.c.raw_artifact_id,
            )
            .join(
                ingest_runs,
                ingest_runs.c.id == raw_artifact_observations.c.ingest_run_id,
            )
        )
        .where(
            publication_evidence.c.dataset_code == policy.dataset_code,
            publication_evidence.c.source == policy.source,
            publication_evidence.c.recorded_at <= context.knowledge_as_of,
            publication_evidence.c.evidence_type.in_(policy.accepted_evidence_types),
        )
    )


def select_authoritative(rows: Sequence[RowMapping]) -> AuthoritativeEvidence | None:
    """The chain head of one version's admitted evidence rows."""
    if not rows:
        return None

    superseded_ids = {
        row["supersedes_evidence_id"]
        for row in rows
        if row["supersedes_evidence_id"] is not None
    }
    heads = [row for row in rows if row["id"] not in superseded_ids]
    if not heads:
        return None
    selected = max(
        heads,
        key=lambda row: (row["quality_rank"], row["recorded_at"], row["id"]),
    )
    return AuthoritativeEvidence(
        evidence_id=selected["id"],
        evidence_kind=selected["evidence_kind"],
        published_at=selected["published_at"],
        recorded_at=selected["recorded_at"],
        evidence_source=selected["evidence_source"],
        evidence_type=selected["evidence_type"],
        quality_rank=selected["quality_rank"],
        publication_evidence_hash=selected["publication_evidence_hash"],
        supersedes_evidence_id=selected["supersedes_evidence_id"],
        raw_artifact_id=selected["raw_artifact_id"],
        raw_artifact_hash=selected["evidence_artifact_hash"],
        raw_artifact_uri=selected["evidence_artifact_uri"],
        ingest_run_id=selected["ingest_run_id"],
        ingest_run_status=selected["evidence_ingest_run_status"],
        source_uri=selected["evidence_source_uri"],
        fetched_at=selected["evidence_fetched_at"],
    )
