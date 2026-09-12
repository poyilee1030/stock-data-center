"""Append-only Phase 8 observed-data writers."""

from __future__ import annotations

from dataclasses import fields

import sqlalchemy as sa
from sqlalchemy import Connection, Table
from sqlalchemy.dialects.postgresql import insert

from stock_data_center.db import metadata
from stock_data_center.db.metadata import publication_evidence, publication_evidence_observations
from stock_data_center.market_reference.models import (
    CorporateActionObservation, MarketIndexMetadataObservation, MarketIndexObservation, OfficialValuationObservation,
    Phase8LineageRef, Phase8Publication, TwdAmount, WrittenPhase8Version,
)


class _Spec:
    def __init__(self, code: str, table: str, link: str, target: str, constraint: str):
        self.code = code
        self.table: Table = metadata.tables[table]
        self.link: Table = metadata.tables[link]
        self.target = target
        self.constraint = constraint


SPECS = {
    "market_index": _Spec("market_index", "market_index_versions", "market_index_version_observations", "market_index_version_id", "uq_market_index_business_revision"),
    "market_index_metadata": _Spec("market_index_metadata", "market_index_metadata_versions", "market_index_metadata_version_observations", "market_index_metadata_version_id", "uq_market_index_metadata_business_revision"),
    "corporate_action": _Spec("corporate_action", "corporate_action_versions", "corporate_action_version_observations", "corporate_action_version_id", "uq_corporate_action_business_revision"),
    "official_valuation": _Spec("official_valuation", "official_valuation_versions", "official_valuation_version_observations", "official_valuation_version_id", "uq_official_valuation_business_revision"),
}


class MarketReferenceWriter:
    def register_index(self, connection: Connection, *, index_code: str) -> int:
        if not index_code:
            raise ValueError("index_code must be nonempty")
        created = connection.execute(
            insert(metadata.tables["market_index"])
            .values(index_code=index_code)
            .on_conflict_do_nothing(index_elements=[metadata.tables["market_index"].c.index_code])
            .returning(metadata.tables["market_index"].c.id)
        ).scalar_one_or_none()
        if created is not None:
            return created
        return connection.scalar(sa.select(metadata.tables["market_index"].c.id).where(
            metadata.tables["market_index"].c.index_code == index_code
        ))

    def register_corporate_action_event(
        self, connection: Connection, *, security_id: int, source: str,
        source_event_key: str,
    ) -> int:
        if not source or not source_event_key:
            raise ValueError("source and source_event_key must be nonempty")
        table = metadata.tables["corporate_action_events"]
        event_id = connection.execute(
            insert(table).values(
                security_id=security_id, source=source,
                source_event_key=source_event_key,
            ).on_conflict_do_nothing(
                constraint="uq_corporate_action_event_source_key"
            ).returning(table.c.id)
        ).scalar_one_or_none()
        if event_id is not None:
            return event_id
        return connection.scalar(sa.select(table.c.id).where(
            table.c.security_id == security_id,
            table.c.source == source,
            table.c.source_event_key == source_event_key,
        ))

    def append_index(self, connection: Connection, *, market_index_id: int, source: str,
                     observation: MarketIndexObservation, lineage: Phase8LineageRef) -> WrittenPhase8Version:
        return self._append(connection, SPECS["market_index"], {"market_index_id": market_index_id}, source, observation, lineage)

    def append_index_metadata(
        self, connection: Connection, *, market_index_id: int, source: str,
        observation: MarketIndexMetadataObservation, lineage: Phase8LineageRef,
    ) -> WrittenPhase8Version:
        return self._append(
            connection, SPECS["market_index_metadata"],
            {"market_index_id": market_index_id}, source, observation, lineage,
        )

    def append_corporate_action(self, connection: Connection, *, event_id: int, source: str,
                                observation: CorporateActionObservation, lineage: Phase8LineageRef) -> WrittenPhase8Version:
        return self._append(connection, SPECS["corporate_action"], {"event_id": event_id}, source, observation, lineage)

    def append_official_valuation(self, connection: Connection, *, security_id: int, source: str,
                                  observation: OfficialValuationObservation, lineage: Phase8LineageRef) -> WrittenPhase8Version:
        return self._append(connection, SPECS["official_valuation"], {"security_id": security_id}, source, observation, lineage)

    def append_publication_evidence(self, connection: Connection, *, dataset_code: str,
                                    source: str, version_id: int, publication: Phase8Publication,
                                    lineage: Phase8LineageRef) -> int:
        if dataset_code not in SPECS:
            raise ValueError(f"unsupported Phase 8 dataset {dataset_code!r}")
        spec = SPECS[dataset_code]
        evidence_values = _values(publication)
        values = {**evidence_values, "dataset_code": dataset_code, "source": source,
                  spec.target: version_id, "publication_evidence_hash": "0" * 64,
                  "recorded_at": sa.func.statement_timestamp(),
                  "raw_artifact_id": lineage.raw_artifact_id, "ingest_run_id": lineage.ingest_run_id}
        evidence_id = connection.execute(insert(publication_evidence).values(**values)
            .on_conflict_do_nothing(index_elements=[publication_evidence.c.publication_evidence_hash])
            .returning(publication_evidence.c.id)).scalar_one_or_none()
        if evidence_id is None:
            predicates = [publication_evidence.c.dataset_code == dataset_code,
                          publication_evidence.c.source == source,
                          publication_evidence.c[spec.target] == version_id]
            predicates += [publication_evidence.c[name].is_not_distinct_from(value)
                           for name, value in evidence_values.items()]
            evidence_id = connection.scalar(sa.select(publication_evidence.c.id).where(*predicates))
        connection.execute(insert(publication_evidence_observations).values(
            publication_evidence_id=evidence_id, raw_artifact_id=lineage.raw_artifact_id,
            ingest_run_id=lineage.ingest_run_id).on_conflict_do_nothing())
        return evidence_id

    @staticmethod
    def _append(connection: Connection, spec: _Spec, identity: dict[str, object], source: str,
                observation: object, lineage: Phase8LineageRef) -> WrittenPhase8Version:
        observation_values = _values(observation)
        values = {**identity, **observation_values, "source": source,
                  "business_content_hash": "0" * 64, "ingested_at": sa.func.statement_timestamp(),
                  "raw_artifact_id": lineage.raw_artifact_id, "ingest_run_id": lineage.ingest_run_id}
        row = connection.execute(insert(spec.table).values(**values).on_conflict_do_nothing(
            constraint=spec.constraint).returning(spec.table.c.id, spec.table.c.business_content_hash,
                                                   spec.table.c.ingested_at)).mappings().one_or_none()
        created = row is not None
        if row is None:
            predicates = [spec.table.c.source == source]
            predicates += [spec.table.c[name].is_not_distinct_from(value)
                           for name, value in {**identity, **observation_values}.items()]
            row = connection.execute(sa.select(spec.table.c.id, spec.table.c.business_content_hash,
                                               spec.table.c.ingested_at).where(*predicates)).mappings().one()
        connection.execute(insert(spec.link).values(**{spec.target: row["id"],
            "raw_artifact_id": lineage.raw_artifact_id, "ingest_run_id": lineage.ingest_run_id}).on_conflict_do_nothing())
        return WrittenPhase8Version(spec.code, row["id"], row["business_content_hash"], row["ingested_at"], created)


def _values(instance: object) -> dict[str, object]:
    result = {}
    for field in fields(instance):
        value = getattr(instance, field.name)
        result[field.name] = value.value if isinstance(value, TwdAmount) else value
    return result
