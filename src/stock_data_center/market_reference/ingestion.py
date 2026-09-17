"""Append-only Phase 8 observed-data writers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields

import sqlalchemy as sa
from sqlalchemy import Connection, Table
from sqlalchemy.dialects.postgresql import insert

from stock_data_center.db import metadata
from stock_data_center.db.batch import batched
from stock_data_center.db.metadata import (
    corporate_action_retractions,
    publication_evidence,
    publication_evidence_observations,
)
from stock_data_center.market_reference.models import (
    CorporateActionObservation,
    MarketIndexMetadataObservation,
    MarketIndexObservation,
    OfficialValuationObservation,
    Phase8LineageRef,
    Phase8Publication,
    SignedTwdAmount,
    TwdAmount,
    WrittenPhase8Version,
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

    def register_indices(
        self, connection: Connection, *, index_codes: Sequence[str]
    ) -> dict[str, int]:
        """Register a whole index-date's codes in one statement."""
        codes = sorted(set(index_codes))
        if not codes:
            return {}
        table = metadata.tables["market_index"]
        for batch in batched([{"index_code": code} for code in codes]):
            connection.execute(
                insert(table).values(list(batch)).on_conflict_do_nothing(
                    index_elements=[table.c.index_code]
                )
            )
        registered = {
            row["index_code"]: row["id"]
            for row in connection.execute(
                sa.select(table.c.id, table.c.index_code).where(
                    table.c.index_code.in_(codes)
                )
            ).mappings()
        }
        missing = [code for code in codes if code not in registered]
        if missing:
            raise RuntimeError(f"conflicting index identity disappeared: {missing}")
        return registered

    def append_indices(
        self, connection: Connection, *, source: str,
        observations: Sequence[tuple[int, MarketIndexObservation]],
        lineage: Phase8LineageRef,
    ) -> tuple[WrittenPhase8Version, ...]:
        """Append a whole index-date, in the order given.

        Set-based for the same reason the price writer is: a TWSE index file
        carries 273 rows and the window is 1,627 dates. Identity, hashing and
        `ingested_at` stay in the database, and an unchanged observation still
        reuses its version instead of creating a revision.
        """
        return self._append_many(
            connection, SPECS["market_index"], "market_index_id",
            source, observations, lineage,
            # One index carries many trade dates, so the link column alone does
            # not identify a row. TAIEX history imports 21 dates for a single
            # index; keying on the index would collapse them and attach each
            # date's evidence to whichever version came back last.
            identity_fields=("trade_date",),
        )

    def register_corporate_action_events(
        self, connection: Connection, *,
        events: Sequence[tuple[int, str, str]],
    ) -> dict[tuple[int, str, str], int]:
        """Register a whole range file's events in one statement.

        `events` are `(security_id, source, source_event_key)` triples. A
        TWSE year file can list over a thousand, one insert each would be for
        the same reason a whole-market daily-price file is."""
        keys = sorted(set(events))
        if not keys:
            return {}
        table = metadata.tables["corporate_action_events"]
        rows_by_key = [
            {"security_id": security_id, "source": source, "source_event_key": key}
            for security_id, source, key in keys
        ]
        for batch in batched(rows_by_key):
            connection.execute(
                insert(table).values(list(batch)).on_conflict_do_nothing(
                    constraint="uq_corporate_action_event_source_key"
                )
            )
        registered: dict[tuple[int, str, str], int] = {}
        for batch in batched(rows_by_key):
            chunk = [
                (row["security_id"], row["source"], row["source_event_key"])
                for row in batch
            ]
            rows = connection.execute(
                sa.select(
                    table.c.security_id, table.c.source,
                    table.c.source_event_key, table.c.id,
                ).where(
                    sa.tuple_(
                        table.c.security_id, table.c.source, table.c.source_event_key
                    ).in_(chunk)
                )
            ).all()
            registered.update(
                {(security_id, source, key): event_id
                 for security_id, source, key, event_id in rows}
            )
        missing = [key for key in keys if key not in registered]
        if missing:
            raise RuntimeError(f"conflicting corporate-action event identity disappeared: {missing}")
        return registered

    def append_corporate_actions(
        self, connection: Connection, *, source: str,
        observations: Sequence[tuple[int, CorporateActionObservation]],
        lineage: Phase8LineageRef,
    ) -> tuple[WrittenPhase8Version, ...]:
        """Append a whole range file's events, in the order given.

        One event carries at most one observation per call — a range file
        lists each locator once (Invariant G(2)) — so no `identity_fields`
        are needed to tell rows sharing an `event_id` apart."""
        return self._append_many(
            connection, SPECS["corporate_action"], "event_id",
            source, observations, lineage,
        )

    def retract_corporate_actions(
        self, connection: Connection, *, events: Sequence[int], reason: str,
        lineage: Phase8LineageRef,
    ) -> tuple[int, ...]:
        """Record that this run's covered range no longer lists these events.

        One retraction fact per `(event_id, raw_artifact_id)`: a rerun of the
        same range over the same bytes must not pile up duplicate facts, but a
        later run that proves the same absence again is its own provenance
        (CLAUDE.md §26) rather than a state this call could collapse away."""
        ids = sorted(set(events))
        if not ids:
            return ()
        rows = [
            {
                "event_id": event_id, "reason": reason,
                "ingested_at": sa.func.statement_timestamp(),
                "raw_artifact_id": lineage.raw_artifact_id,
                "ingest_run_id": lineage.ingest_run_id,
            }
            for event_id in ids
        ]
        retraction_ids: list[int] = []
        for batch in batched(rows):
            retraction_ids.extend(
                connection.scalars(
                    insert(corporate_action_retractions).values(list(batch))
                    .on_conflict_do_nothing(
                        constraint="uq_corporate_action_retraction_artifact"
                    )
                    .returning(corporate_action_retractions.c.id)
                ).all()
            )
        return tuple(retraction_ids)

    def append_official_valuations(
        self, connection: Connection, *, source: str,
        observations: Sequence[tuple[int, OfficialValuationObservation]],
        lineage: Phase8LineageRef,
    ) -> tuple[WrittenPhase8Version, ...]:
        """Append a whole market-date of valuations, in the order given."""
        return self._append_many(
            connection, SPECS["official_valuation"], "security_id",
            source, observations, lineage, identity_fields=("trade_date",),
        )

    def append_publication_evidence_batch(
        self, connection: Connection, *, dataset_code: str, source: str,
        planned: Sequence[tuple[int, Phase8Publication]], lineage: Phase8LineageRef,
    ) -> tuple[int, int]:
        """Append one date's evidence; returns (created, deduplicated)."""
        if not planned:
            return 0, 0
        spec = SPECS[dataset_code]
        rows = [
            {
                **_values(publication), "dataset_code": dataset_code,
                "source": source, spec.target: version_id,
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
                (row[spec.target], row["evidence_type"]): row["id"]
                for row in connection.execute(
                    insert(publication_evidence).values(list(batch))
                    .on_conflict_do_nothing(
                        index_elements=[publication_evidence.c.publication_evidence_hash]
                    ).returning(
                        publication_evidence.c.id,
                        publication_evidence.c[spec.target],
                        publication_evidence.c.evidence_type,
                    )
                ).mappings()
            })
        pending_ids = [
            version_id for version_id, publication in planned
            if (version_id, publication.evidence_type) not in created
        ]
        stored = self._existing_evidence(
            connection, dataset_code, source, spec.target, pending_ids
        )
        evidence_ids: list[int] = []
        deduplicated = 0
        for version_id, publication in planned:
            evidence_id = created.get((version_id, publication.evidence_type))
            if evidence_id is None:
                wanted = _values(publication)
                match = next(
                    (
                        row for row in stored.get(version_id, ())
                        if all(row[k] == v for k, v in wanted.items())
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
            evidence_ids.append(evidence_id)
        observations = [
            {
                "publication_evidence_id": evidence_id,
                "raw_artifact_id": lineage.raw_artifact_id,
                "ingest_run_id": lineage.ingest_run_id,
            }
            for evidence_id in sorted(set(evidence_ids))
        ]
        for batch in batched(observations):
            connection.execute(
                insert(publication_evidence_observations)
                .values(list(batch)).on_conflict_do_nothing()
            )
        return len(planned) - deduplicated, deduplicated

    @staticmethod
    def _existing_rows(
        connection: Connection, spec: _Spec, link_column: str, source: str,
        identifiers: Sequence[int],
        *, period_column: str | None = None, periods: Sequence[object] = (),
    ) -> dict[int, tuple[dict, ...]]:
        """Read back the rows a conflicting insert could have hit.

        Scoped by period as well as by entity. Without it a re-run over the
        window would load every stored row for each index — 365,775 of them —
        and scan them linearly per observation.
        """
        if not identifiers:
            return {}
        predicates = [
            spec.table.c.source == source,
            spec.table.c[link_column].in_(sorted(set(identifiers))),
        ]
        if period_column and periods:
            predicates.append(
                spec.table.c[period_column].in_(sorted(set(periods)))
            )
        rows = connection.execute(sa.select(spec.table).where(*predicates)).mappings()
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row[link_column], []).append(dict(row))
        return {key: tuple(value) for key, value in grouped.items()}

    @staticmethod
    def _existing_evidence(
        connection: Connection, dataset_code: str, source: str, target: str,
        version_ids: Sequence[int],
    ) -> dict[int, tuple[dict, ...]]:
        if not version_ids:
            return {}
        rows = connection.execute(
            sa.select(publication_evidence).where(
                publication_evidence.c.dataset_code == dataset_code,
                publication_evidence.c.source == source,
                publication_evidence.c[target].in_(sorted(set(version_ids))),
            )
        ).mappings()
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row[target], []).append(dict(row))
        return {key: tuple(value) for key, value in grouped.items()}

    def _append_many(
        self, connection: Connection, spec: _Spec, link_column: str, source: str,
        observations: Sequence[tuple[int, object]], lineage: Phase8LineageRef,
        *, identity_fields: Sequence[str] = (),
    ) -> tuple[WrittenPhase8Version, ...]:
        """`identity_fields` are the observation fields that, with the link
        column, identify a row. Without them a batch holding several periods of
        one entity would collapse onto a single key."""
        if not observations:
            return ()

        def key(identifier: int, observation: object) -> tuple:
            values = _values(observation)
            return (identifier, *(values[name] for name in identity_fields))
        rows = [
            {
                link_column: identifier, **_values(observation), "source": source,
                "business_content_hash": "0" * 64,
                "ingested_at": sa.func.statement_timestamp(),
                "raw_artifact_id": lineage.raw_artifact_id,
                "ingest_run_id": lineage.ingest_run_id,
            }
            for identifier, observation in observations
        ]
        created: dict[tuple, dict] = {}
        for batch in batched(rows):
            created.update({
                (row[link_column], *(row[name] for name in identity_fields)): dict(row)
                for row in connection.execute(
                    insert(spec.table).values(list(batch))
                    .on_conflict_do_nothing(constraint=spec.constraint)
                    .returning(
                        spec.table.c.id, spec.table.c[link_column],
                        *(spec.table.c[name] for name in identity_fields),
                        spec.table.c.business_content_hash,
                        spec.table.c.ingested_at,
                    )
                ).mappings()
            })
        pending = [
            (identifier, observation)
            for identifier, observation in observations
            if key(identifier, observation) not in created
        ]
        period_column = identity_fields[0] if identity_fields else None
        existing = self._existing_rows(
            connection, spec, link_column, source,
            [identifier for identifier, _ in pending],
            period_column=period_column,
            periods=[
                _values(observation)[period_column]
                for _, observation in pending
            ] if period_column else (),
        )
        written: list[WrittenPhase8Version] = []
        links: list[dict] = []
        for identifier, observation in observations:
            row = created.get(key(identifier, observation))
            is_created = row is not None
            if row is None:
                wanted = _values(observation)
                row = next(
                    (
                        candidate for candidate in existing.get(identifier, ())
                        if all(
                            candidate[name] == value for name, value in wanted.items()
                        )
                    ),
                    None,
                )
                if row is None:
                    raise RuntimeError(
                        f"a conflicting {spec.code} version is not readable back "
                        f"for {link_column} {identifier}"
                    )
            written.append(WrittenPhase8Version(
                spec.code, row["id"], row["business_content_hash"],
                row["ingested_at"], is_created,
            ))
            links.append({
                spec.target: row["id"],
                "raw_artifact_id": lineage.raw_artifact_id,
                "ingest_run_id": lineage.ingest_run_id,
            })
        for batch in batched(links):
            connection.execute(
                insert(spec.link).values(list(batch)).on_conflict_do_nothing()
            )
        return tuple(written)

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
        amount = isinstance(value, (TwdAmount, SignedTwdAmount))
        result[field.name] = value.value if amount else value
    return result
