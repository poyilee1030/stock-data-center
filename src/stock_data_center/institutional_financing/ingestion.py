"""Append-only writers for Phase 7 observed source datasets."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection, RowMapping, Table
from sqlalchemy.dialects.postgresql import insert

from stock_data_center.db import metadata as db_metadata
from stock_data_center.db.batch import batched
from stock_data_center.db.metadata import (
    publication_evidence,
    publication_evidence_observations,
)
from stock_data_center.institutional_financing.models import (
    ForeignHoldingObservation,
    InstitutionalInvestorObservation,
    InstitutionalMarketSummaryObservation,
    MarginTradingObservation,
    ShareQuantity,
    SecuritiesLendingObservation,
    SourceLineageRef,
    SourcePublication,
    WrittenSourceVersion,
)


class _DatasetSpec:
    def __init__(
        self,
        dataset_code: str,
        table: Table,
        observation_table: Table,
        version_column: str,
        constraint: str,
    ) -> None:
        self.dataset_code = dataset_code
        self.table = table
        self.observation_table = observation_table
        self.version_column = version_column
        self.constraint = constraint


_SPECS = {
    "institutional_investor": _DatasetSpec(
        "institutional_investor",
        db_metadata.tables["institutional_investor_versions"],
        db_metadata.tables["institutional_investor_version_observations"],
        "institutional_investor_version_id",
        "uq_institutional_investor_business_revision",
    ),
    "foreign_holding": _DatasetSpec(
        "foreign_holding",
        db_metadata.tables["foreign_holding_versions"],
        db_metadata.tables["foreign_holding_version_observations"],
        "foreign_holding_version_id",
        "uq_foreign_holding_business_revision",
    ),
    "institutional_market_summary": _DatasetSpec(
        "institutional_market_summary",
        db_metadata.tables["institutional_market_summary_versions"],
        db_metadata.tables["institutional_market_summary_version_observations"],
        "institutional_market_summary_version_id",
        "uq_institutional_summary_business_revision",
    ),
    "margin_trading": _DatasetSpec(
        "margin_trading",
        db_metadata.tables["margin_trading_versions"],
        db_metadata.tables["margin_trading_version_observations"],
        "margin_trading_version_id",
        "uq_margin_trading_business_revision",
    ),
    "securities_lending": _DatasetSpec(
        "securities_lending",
        db_metadata.tables["securities_lending_versions"],
        db_metadata.tables["securities_lending_version_observations"],
        "securities_lending_version_id",
        "uq_securities_lending_business_revision",
    ),
}


class InstitutionalFinancingWriter:
    """Write normalized source facts without creating fake repeat revisions."""

    def append_institutional_investor(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        observation: InstitutionalInvestorObservation,
        lineage: SourceLineageRef,
    ) -> WrittenSourceVersion:
        return self._append(
            connection,
            spec=_SPECS["institutional_investor"],
            identity={"security_id": security_id},
            source=source,
            observation=observation,
            lineage=lineage,
        )

    def append_institutional_investors(
        self,
        connection: Connection,
        *,
        source: str,
        observations: Sequence[tuple[int, InstitutionalInvestorObservation]],
        lineage: SourceLineageRef,
    ) -> tuple[WrittenSourceVersion, ...]:
        """Append a whole market-date of flows, in the order given.

        Set-based for the reason the price and valuation writers are: a TWSE
        file carries about 1,300 rows and the window is 1,627 dates. Identity,
        hashing and `ingested_at` stay in the database, and an unchanged
        observation still reuses its version instead of creating a revision.
        """
        return self._append_many(
            connection,
            spec=_SPECS["institutional_investor"],
            source=source,
            observations=observations,
            lineage=lineage,
        )

    def append_foreign_holding(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        observation: ForeignHoldingObservation,
        lineage: SourceLineageRef,
    ) -> WrittenSourceVersion:
        return self._append(
            connection,
            spec=_SPECS["foreign_holding"],
            identity={"security_id": security_id},
            source=source,
            observation=observation,
            lineage=lineage,
        )

    def append_market_summary(
        self,
        connection: Connection,
        *,
        source: str,
        observation: InstitutionalMarketSummaryObservation,
        lineage: SourceLineageRef,
    ) -> WrittenSourceVersion:
        return self._append(
            connection,
            spec=_SPECS["institutional_market_summary"],
            identity={},
            source=source,
            observation=observation,
            lineage=lineage,
        )

    def append_margin_trading(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        observation: MarginTradingObservation,
        lineage: SourceLineageRef,
    ) -> WrittenSourceVersion:
        return self._append(
            connection,
            spec=_SPECS["margin_trading"],
            identity={"security_id": security_id},
            source=source,
            observation=observation,
            lineage=lineage,
        )

    def append_securities_lending(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        observation: SecuritiesLendingObservation,
        lineage: SourceLineageRef,
    ) -> WrittenSourceVersion:
        return self._append(
            connection,
            spec=_SPECS["securities_lending"],
            identity={"security_id": security_id},
            source=source,
            observation=observation,
            lineage=lineage,
        )

    def append_publication_evidence(
        self,
        connection: Connection,
        *,
        dataset_code: str,
        source: str,
        version_id: int,
        publication: SourcePublication,
        lineage: SourceLineageRef,
    ) -> int:
        try:
            spec = _SPECS[dataset_code]
        except KeyError as error:
            raise ValueError(f"unsupported Phase 7 dataset {dataset_code!r}") from error
        evidence_values = _dataclass_values(publication)
        values = {
            **evidence_values,
            "dataset_code": dataset_code,
            "source": source,
            spec.version_column: version_id,
            "publication_evidence_hash": "0" * 64,
            "recorded_at": sa.func.statement_timestamp(),
            "raw_artifact_id": lineage.raw_artifact_id,
            "ingest_run_id": lineage.ingest_run_id,
        }
        evidence_id = connection.execute(
            insert(publication_evidence)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[publication_evidence.c.publication_evidence_hash]
            )
            .returning(publication_evidence.c.id)
        ).scalar_one_or_none()
        if evidence_id is None:
            predicates = [
                publication_evidence.c.dataset_code == dataset_code,
                publication_evidence.c.source == source,
                publication_evidence.c[spec.version_column] == version_id,
            ]
            predicates.extend(
                publication_evidence.c[name].is_not_distinct_from(value)
                for name, value in evidence_values.items()
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

    def append_publication_evidence_batch(
        self,
        connection: Connection,
        *,
        dataset_code: str,
        source: str,
        planned: Sequence[tuple[int, SourcePublication]],
        lineage: SourceLineageRef,
    ) -> tuple[int, int]:
        """Append one date's evidence; returns (created, deduplicated)."""
        if not planned:
            return 0, 0
        try:
            spec = _SPECS[dataset_code]
        except KeyError as error:
            raise ValueError(f"unsupported Phase 7 dataset {dataset_code!r}") from error
        target = spec.version_column
        rows = [
            {
                **_dataclass_values(publication),
                "dataset_code": dataset_code,
                "source": source,
                target: version_id,
                "publication_evidence_hash": "0" * 64,
                "recorded_at": sa.func.statement_timestamp(),
                "raw_artifact_id": lineage.raw_artifact_id,
                "ingest_run_id": lineage.ingest_run_id,
            }
            for version_id, publication in planned
        ]
        created: dict[tuple[int, str], int] = {}
        for batch in batched(rows):
            created.update({
                (row[target], row["evidence_type"]): row["id"]
                for row in connection.execute(
                    insert(publication_evidence)
                    .values(list(batch))
                    .on_conflict_do_nothing(
                        index_elements=[publication_evidence.c.publication_evidence_hash]
                    )
                    .returning(
                        publication_evidence.c.id,
                        publication_evidence.c[target],
                        publication_evidence.c.evidence_type,
                    )
                ).mappings()
            })
        pending = sorted({
            version_id for version_id, publication in planned
            if (version_id, publication.evidence_type) not in created
        })
        stored: dict[int, list[RowMapping]] = {}
        if pending:
            for row in connection.execute(
                sa.select(publication_evidence).where(
                    publication_evidence.c.dataset_code == dataset_code,
                    publication_evidence.c.source == source,
                    publication_evidence.c[target].in_(pending),
                )
            ).mappings():
                stored.setdefault(row[target], []).append(row)
        evidence_ids: set[int] = set()
        deduplicated = 0
        for version_id, publication in planned:
            evidence_id = created.get((version_id, publication.evidence_type))
            if evidence_id is None:
                wanted = _dataclass_values(publication)
                match = next(
                    (
                        row for row in stored.get(version_id, ())
                        if all(row[name] == value for name, value in wanted.items())
                    ),
                    None,
                )
                if match is None:
                    raise RuntimeError(
                        "a conflicting publication evidence row is not readable "
                        f"back for version {version_id}"
                    )
                evidence_id = match["id"]
                deduplicated += 1
            evidence_ids.add(evidence_id)
        links = [
            {
                "publication_evidence_id": evidence_id,
                "raw_artifact_id": lineage.raw_artifact_id,
                "ingest_run_id": lineage.ingest_run_id,
            }
            for evidence_id in sorted(evidence_ids)
        ]
        for batch in batched(links):
            connection.execute(
                insert(publication_evidence_observations)
                .values(list(batch))
                .on_conflict_do_nothing()
            )
        return len(planned) - deduplicated, deduplicated

    @staticmethod
    def _append_many(
        connection: Connection,
        *,
        spec: _DatasetSpec,
        source: str,
        observations: Sequence[tuple[int, object]],
        lineage: SourceLineageRef,
    ) -> tuple[WrittenSourceVersion, ...]:
        """Per-security datasets, keyed by `(security_id, trade_date)`."""
        if not observations:
            return ()
        table = spec.table
        rows = [
            {
                "security_id": security_id,
                **_dataclass_values(observation),
                "source": source,
                "business_content_hash": "0" * 64,
                "ingested_at": sa.func.statement_timestamp(),
                "raw_artifact_id": lineage.raw_artifact_id,
                "ingest_run_id": lineage.ingest_run_id,
            }
            for security_id, observation in observations
        ]
        created: dict[tuple, RowMapping] = {}
        for batch in batched(rows):
            created.update({
                (row["security_id"], row["trade_date"]): row
                for row in connection.execute(
                    insert(table)
                    .values(list(batch))
                    .on_conflict_do_nothing(constraint=spec.constraint)
                    .returning(
                        table.c.id,
                        table.c.security_id,
                        table.c.trade_date,
                        table.c.business_content_hash,
                        table.c.ingested_at,
                    )
                ).mappings()
            })
        pending = [
            (security_id, observation)
            for security_id, observation in observations
            if (security_id, observation.trade_date) not in created
        ]
        # Scoped by date as well as by security, so a re-run over the window
        # reads back one date's rows rather than every date of each security.
        existing: dict[int, list[RowMapping]] = {}
        if pending:
            for row in connection.execute(
                sa.select(table).where(
                    table.c.source == source,
                    table.c.security_id.in_(sorted({sid for sid, _ in pending})),
                    table.c.trade_date.in_(
                        sorted({observation.trade_date for _, observation in pending})
                    ),
                )
            ).mappings():
                existing.setdefault(row["security_id"], []).append(row)
        written: list[WrittenSourceVersion] = []
        for security_id, observation in observations:
            row = created.get((security_id, observation.trade_date))
            is_created = row is not None
            if row is None:
                wanted = _dataclass_values(observation)
                row = next(
                    (
                        candidate for candidate in existing.get(security_id, ())
                        if all(candidate[name] == value for name, value in wanted.items())
                    ),
                    None,
                )
                if row is None:
                    raise RuntimeError(
                        f"a conflicting {spec.dataset_code} version is not readable "
                        f"back for security {security_id}"
                    )
            written.append(_written(spec.dataset_code, row, created=is_created))
        links = [
            {
                spec.version_column: item.version_id,
                "raw_artifact_id": lineage.raw_artifact_id,
                "ingest_run_id": lineage.ingest_run_id,
            }
            for item in written
        ]
        for batch in batched(links):
            connection.execute(
                insert(spec.observation_table)
                .values(list(batch))
                .on_conflict_do_nothing()
            )
        return tuple(written)

    @staticmethod
    def _append(
        connection: Connection,
        *,
        spec: _DatasetSpec,
        identity: dict[str, object],
        source: str,
        observation: object,
        lineage: SourceLineageRef,
    ) -> WrittenSourceVersion:
        observation_values = _dataclass_values(observation)
        values = {
            **identity,
            **observation_values,
            "source": source,
            "business_content_hash": "0" * 64,
            "ingested_at": sa.func.statement_timestamp(),
            "raw_artifact_id": lineage.raw_artifact_id,
            "ingest_run_id": lineage.ingest_run_id,
        }
        inserted = connection.execute(
            insert(spec.table)
            .values(**values)
            .on_conflict_do_nothing(constraint=spec.constraint)
            .returning(
                spec.table.c.id,
                spec.table.c.business_content_hash,
                spec.table.c.ingested_at,
            )
        ).mappings().one_or_none()
        if inserted is None:
            predicates = [spec.table.c.source == source]
            predicates.extend(
                spec.table.c[name].is_not_distinct_from(value)
                for name, value in {**identity, **observation_values}.items()
            )
            inserted = connection.execute(
                sa.select(
                    spec.table.c.id,
                    spec.table.c.business_content_hash,
                    spec.table.c.ingested_at,
                ).where(*predicates)
            ).mappings().one()
            created = False
        else:
            created = True
        connection.execute(
            insert(spec.observation_table)
            .values(
                **{
                    spec.version_column: inserted["id"],
                    "raw_artifact_id": lineage.raw_artifact_id,
                    "ingest_run_id": lineage.ingest_run_id,
                }
            )
            .on_conflict_do_nothing()
        )
        return _written(spec.dataset_code, inserted, created=created)


def _dataclass_values(instance: object) -> dict[str, Any]:
    values = {}
    for field in fields(instance):
        value = getattr(instance, field.name)
        values[field.name] = value.value if isinstance(value, ShareQuantity) else value
    return values


def _written(
    dataset_code: str, row: RowMapping, *, created: bool
) -> WrittenSourceVersion:
    return WrittenSourceVersion(
        dataset_code=dataset_code,
        version_id=row["id"],
        business_content_hash=row["business_content_hash"],
        ingested_at=row["ingested_at"],
        created=created,
    )
