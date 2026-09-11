"""Cache-independent market and system point-in-time resolution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection, RowMapping

from stock_data_center.db.metadata import (
    ingest_runs,
    raw_artifact_observations,
    raw_artifacts,
)
from stock_data_center.pit.contracts import DatasetContract, get_contract
from stock_data_center.pit.evidence import PublicationEvidenceResolver
from stock_data_center.pit.errors import InvalidLogicalKeyError
from stock_data_center.pit.models import (
    AuthoritativeEvidence,
    MarketPITContext,
    PITContext,
    Provenance,
    ResolvedRecord,
    SystemPITContext,
)
from stock_data_center.pit.source_policy import SourcePolicyResolver


_INTERNAL_COLUMNS = {
    "id",
    "source",
    "business_content_hash",
    "ingested_at",
    "raw_artifact_id",
    "ingest_run_id",
    "_seal_ingested_at",
    "_seal_business_content_hash",
    "_seal_version_id",
}


class PITResolver:
    """Resolve one logical-key record without a cache or current-state fallback."""

    def __init__(
        self,
        *,
        source_policy: SourcePolicyResolver | None = None,
        evidence: PublicationEvidenceResolver | None = None,
    ) -> None:
        self._source_policy = source_policy or SourcePolicyResolver()
        self._evidence = evidence or PublicationEvidenceResolver()

    def resolve(
        self,
        connection: Connection,
        *,
        dataset_code: str,
        logical_key: Mapping[str, Any],
        context: PITContext,
        source: str | None = None,
    ) -> ResolvedRecord | None:
        contract = get_contract(dataset_code)
        self._validate_logical_key(contract, logical_key)
        policy = self._source_policy.resolve(
            connection, dataset_code, context, source
        )
        candidates = self._candidate_rows(
            connection, contract, logical_key, policy.source, context
        )

        evidence: AuthoritativeEvidence | None = None
        selected: RowMapping | None = None
        if isinstance(context, MarketPITContext):
            eligible: list[tuple[object, int, RowMapping, AuthoritativeEvidence]] = []
            for row in candidates:
                authoritative = self._evidence.resolve(
                    connection, contract, row["id"], context, policy
                )
                if (
                    authoritative is not None
                    and authoritative.affirms_publication
                    and authoritative.published_at <= context.information_as_of
                ):
                    eligible.append(
                        (
                            authoritative.published_at,
                            row["id"],
                            row,
                            authoritative,
                        )
                    )
            if eligible:
                _, _, selected, evidence = max(
                    eligible, key=lambda item: (item[0], item[1])
                )
        else:
            selected = max(
                candidates,
                key=lambda row: (self._visibility_time(contract, row), row["id"]),
                default=None,
            )

        if selected is None:
            return None
        provenance = self._load_provenance(connection, contract, selected)
        data = {
            key: value
            for key, value in selected.items()
            if key not in _INTERNAL_COLUMNS
        }
        return ResolvedRecord.create(
            dataset_code=dataset_code,
            source=policy.source,
            context=context,
            data=data,
            provenance=provenance,
            evidence=evidence,
        )

    @staticmethod
    def _validate_logical_key(
        contract: DatasetContract, logical_key: Mapping[str, Any]
    ) -> None:
        expected = set(contract.logical_key_columns)
        supplied = set(logical_key)
        if supplied != expected:
            raise InvalidLogicalKeyError(
                f"{contract.dataset_code} requires logical key "
                f"{sorted(expected)!r}; received {sorted(supplied)!r}"
            )

    @staticmethod
    def _candidate_rows(
        connection: Connection,
        contract: DatasetContract,
        logical_key: Mapping[str, Any],
        source: str,
        context: PITContext,
    ) -> list[RowMapping]:
        table = contract.version_table
        statement = sa.select(table).where(table.c.source == source)
        for column, value in logical_key.items():
            statement = statement.where(table.c[column] == value)

        if contract.is_aggregate:
            assert contract.seal_table is not None
            assert contract.seal_version_column is not None
            seal = contract.seal_table
            statement = statement.join(
                seal, seal.c[contract.seal_version_column] == table.c.id
            ).add_columns(
                seal.c.ingested_at.label("_seal_ingested_at"),
                seal.c.business_content_hash.label("_seal_business_content_hash"),
                seal.c[contract.seal_version_column].label("_seal_version_id"),
            )
            if isinstance(context, SystemPITContext):
                statement = statement.where(
                    seal.c.ingested_at <= context.system_as_of
                )
            else:
                statement = statement.where(
                    seal.c.ingested_at <= context.knowledge_as_of
                )
        elif isinstance(context, SystemPITContext):
            assert contract.ingestion_column is not None
            statement = statement.where(
                table.c[contract.ingestion_column] <= context.system_as_of
            )
        return connection.execute(statement).mappings().all()

    @staticmethod
    def _visibility_time(contract: DatasetContract, row: RowMapping):
        if contract.is_aggregate:
            return row["_seal_ingested_at"]
        assert contract.ingestion_column is not None
        return row[contract.ingestion_column]

    @staticmethod
    def _load_provenance(
        connection: Connection,
        contract: DatasetContract,
        row: RowMapping,
    ) -> Provenance:
        lineage = connection.execute(
            sa.select(
                raw_artifacts.c.raw_artifact_hash,
                raw_artifacts.c.storage_uri,
                raw_artifacts.c.stored_at,
                ingest_runs.c.status,
                ingest_runs.c.started_at,
                ingest_runs.c.completed_at,
                raw_artifact_observations.c.source_uri,
                raw_artifact_observations.c.fetched_at,
            )
            .select_from(
                raw_artifact_observations.join(
                    raw_artifacts,
                    raw_artifacts.c.id
                    == raw_artifact_observations.c.raw_artifact_id,
                ).join(
                    ingest_runs,
                    ingest_runs.c.id
                    == raw_artifact_observations.c.ingest_run_id,
                )
            )
            .where(
                raw_artifact_observations.c.raw_artifact_id
                == row["raw_artifact_id"],
                raw_artifact_observations.c.ingest_run_id
                == row["ingest_run_id"],
            )
        ).mappings().one()
        return Provenance(
            version_id=row["id"],
            business_content_hash=(
                row["_seal_business_content_hash"]
                if contract.is_aggregate
                else row["business_content_hash"]
            ),
            ingested_at=PITResolver._visibility_time(contract, row),
            raw_artifact_id=row["raw_artifact_id"],
            raw_artifact_hash=lineage["raw_artifact_hash"],
            raw_artifact_uri=lineage["storage_uri"],
            raw_artifact_stored_at=lineage["stored_at"],
            ingest_run_id=row["ingest_run_id"],
            ingest_run_status=lineage["status"],
            ingest_run_started_at=lineage["started_at"],
            ingest_run_completed_at=lineage["completed_at"],
            source_uri=lineage["source_uri"],
            fetched_at=lineage["fetched_at"],
            aggregate_seal_id=(
                row["_seal_version_id"] if contract.is_aggregate else None
            ),
        )
