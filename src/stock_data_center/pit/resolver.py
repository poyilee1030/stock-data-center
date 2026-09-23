"""Cache-independent market and system point-in-time resolution."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection, RowMapping

from stock_data_center.db.metadata import (
    ingest_runs,
    raw_artifact_observations,
    raw_artifacts,
)
from stock_data_center.pit.contracts import DatasetContract, get_contract
from stock_data_center.pit.errors import (
    InvalidLogicalKeyError,
    UnsupportedPITModeError,
)
from stock_data_center.pit.evidence import PublicationEvidenceResolver
from stock_data_center.pit.history import (
    MarketPITHistory,
    build_history,
    market_order,
    visible_at,
)
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
    "predecessor_version_id",
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
            eligible: list[tuple[RowMapping, AuthoritativeEvidence]] = []
            for row in candidates:
                authoritative = self._evidence.resolve(
                    connection, contract, row["id"], context, policy
                )
                if visible_at(authoritative, context.information_as_of):
                    assert authoritative is not None
                    eligible.append((row, authoritative))
            if eligible:
                selected, evidence = max(
                    eligible, key=lambda item: market_order(item[1], item[0]["id"])
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

    def market_history(
        self,
        connection: Connection,
        *,
        dataset_code: str,
        key_filter: Mapping[str, Any],
        through: Mapping[str, Any] | None = None,
        knowledge_as_of: datetime,
        source: str | None = None,
    ) -> MarketPITHistory:
        """Every version of a key range, with its evidence at `knowledge_as_of`.

        Two statements for the whole range. `key_filter` fixes logical-key
        columns by equality and `through` bounds them from above; the history's
        `visible(information_as_of)` then gives, for every key in the range,
        the version `resolve` would give at that market context.
        """
        contract = get_contract(dataset_code)
        if contract.is_aggregate:
            raise UnsupportedPITModeError(
                f"{dataset_code} is a sealed aggregate; its history is resolved "
                "one sealed version at a time"
            )
        bounds = dict(through or {})
        unknown = (set(key_filter) | set(bounds)) - set(contract.logical_key_columns)
        if unknown:
            raise InvalidLogicalKeyError(
                f"{dataset_code} has no logical-key column {sorted(unknown)!r}"
            )
        # Evidence selection reads only the knowledge cutoff, so the market
        # context used for it needs no information cutoff of its own.
        context = MarketPITContext(
            information_as_of=knowledge_as_of, knowledge_as_of=knowledge_as_of
        )
        policy = self._source_policy.resolve(
            connection, dataset_code, context, source
        )
        table = contract.version_table
        conditions = [table.c.source == policy.source]
        conditions += [table.c[column] == value for column, value in key_filter.items()]
        conditions += [table.c[column] <= value for column, value in bounds.items()]

        rows = connection.execute(
            sa.select(table).where(*conditions)
        ).mappings().all()
        evidence = self._evidence.resolve_many(
            connection,
            contract,
            sa.select(table.c.id).where(*conditions),
            context,
            policy,
        )
        return build_history(
            dataset_code=dataset_code,
            source=policy.source,
            knowledge_as_of=context.knowledge_as_of,
            logical_key_columns=contract.logical_key_columns,
            rows=rows,
            evidence=evidence,
            internal_columns=frozenset(_INTERNAL_COLUMNS),
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
