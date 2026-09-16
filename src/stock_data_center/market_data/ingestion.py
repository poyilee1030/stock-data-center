"""Normalized append-only writes for Phase 3 observed datasets."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Connection, RowMapping
from sqlalchemy.dialects.postgresql import insert

from stock_data_center.db.batch import batched as _batched
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
        latest = self._latest_security_metadata(
            connection,
            security_id=security_id,
            source=source,
            effective_on=observation.effective_from,
        )
        if (
            latest is not None
            and latest["effective_from"] == observation.effective_from
            and _security_metadata_state_matches(latest, observation)
        ):
            return _written(latest, created=False)
        return self._append_security_metadata_transition(
            connection,
            security_id=security_id,
            source=source,
            observation=observation,
            lineage=lineage,
            predecessor_version_id=latest["id"] if latest is not None else None,
        )

    def _append_security_metadata_transition(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        observation: SecurityMetadataObservation,
        lineage: LineageRef,
        predecessor_version_id: int | None,
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
                "predecessor_version_id": predecessor_version_id,
            },
            business=business,
            lineage=lineage,
        )

    def append_security_metadata_snapshot(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        observation: SecurityMetadataObservation,
        lineage: LineageRef,
    ) -> WrittenVersion:
        """Reuse the latest equal state instead of inventing daily revisions.

        Current-list endpoints identify when their snapshot was produced, not
        when every unchanged name/industry/venue field originally took effect.
        A later equal snapshot is therefore provenance for the existing state,
        not a new business revision.  A changed state starts no earlier than the
        first snapshot on which this importer observed it.
        """
        latest = self._latest_security_metadata(
            connection,
            security_id=security_id,
            source=source,
            effective_on=observation.effective_from,
        )
        if latest is not None and _security_metadata_state_matches(
            latest, observation
        ):
            return _written(latest, created=False)
        return self._append_security_metadata_transition(
            connection,
            security_id=security_id,
            source=source,
            observation=observation,
            lineage=lineage,
            predecessor_version_id=latest["id"] if latest is not None else None,
        )

    @staticmethod
    def _latest_security_metadata(
        connection: Connection,
        *,
        security_id: int,
        source: str,
        effective_on: date,
    ) -> RowMapping | None:
        return connection.execute(
            sa.select(
                security_metadata_versions.c.id,
                security_metadata_versions.c.business_content_hash,
                security_metadata_versions.c.ingested_at,
                security_metadata_versions.c.effective_from,
                *(
                    security_metadata_versions.c[name]
                    for name in _SECURITY_METADATA_STATE_FIELDS
                ),
            )
            .where(
                security_metadata_versions.c.security_id == security_id,
                security_metadata_versions.c.source == source,
                security_metadata_versions.c.effective_from <= effective_on,
            )
            .order_by(
                security_metadata_versions.c.effective_from.desc(),
                security_metadata_versions.c.ingested_at.desc(),
                security_metadata_versions.c.id.desc(),
            )
            .limit(1)
        ).mappings().one_or_none()

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

    def register_securities(
        self,
        connection: Connection,
        *,
        security_codes: Sequence[str],
    ) -> dict[str, int]:
        """Register a whole market-date's codes in one statement.

        Same contract as `register_security`, set-based: a whole-market file
        carries about 1,300 codes, and one round trip each is the difference
        between an import that finishes and one that does not.
        """
        codes = sorted(set(security_codes))
        if not codes:
            return {}
        for batch in _batched([{"security_code": code} for code in codes]):
            connection.execute(
                insert(security)
                .values(list(batch))
                .on_conflict_do_nothing(index_elements=[security.c.security_code])
            )
        registered = {
            row["security_code"]: row["id"]
            for row in connection.execute(
                sa.select(security.c.id, security.c.security_code).where(
                    security.c.security_code.in_(codes)
                )
            ).mappings()
        }
        missing = [code for code in codes if code not in registered]
        if missing:
            raise RuntimeError(f"conflicting security identity disappeared: {missing}")
        return registered

    def append_daily_prices(
        self,
        connection: Connection,
        *,
        source: str,
        trade_date: date,
        observations: Sequence[tuple[int, DailyPriceObservation]],
        lineage: LineageRef,
    ) -> tuple[WrittenVersion, ...]:
        """Append one trade date's observations, in the order given.

        Identity, hashing and `ingested_at` stay exactly where they are: the
        database still generates both, and an unchanged observation still
        reuses its existing version instead of creating a revision. Only the
        number of statements changes.
        """
        if not observations:
            return ()
        rows = [
            {
                "security_id": security_id,
                "source": source,
                **_dataclass_values(observation),
                "business_content_hash": "0" * 64,
                "ingested_at": sa.func.statement_timestamp(),
                "raw_artifact_id": lineage.raw_artifact_id,
                "ingest_run_id": lineage.ingest_run_id,
            }
            for security_id, observation in observations
        ]
        created = {}
        for batch in _batched(rows):
            created.update(
                {
                    row["security_id"]: row
                    for row in connection.execute(
                        insert(daily_price_versions)
                        .values(list(batch))
                        .on_conflict_do_nothing(
                            constraint="uq_daily_price_business_revision"
                        )
                        .returning(
                            daily_price_versions.c.id,
                            daily_price_versions.c.security_id,
                            daily_price_versions.c.business_content_hash,
                            daily_price_versions.c.ingested_at,
                        )
                    ).mappings()
                }
            )
        pending = [
            (security_id, observation)
            for security_id, observation in observations
            if security_id not in created
        ]
        existing = self._existing_daily_prices(
            connection,
            source=source,
            trade_date=trade_date,
            security_ids=[security_id for security_id, _ in pending],
        )
        written: list[WrittenVersion] = []
        for security_id, observation in observations:
            row = created.get(security_id)
            if row is not None:
                written.append(_written(row, created=True))
                continue
            match = _matching_version(existing.get(security_id, ()), observation)
            if match is None:
                raise RuntimeError(
                    "a conflicting daily-price version is not readable back for "
                    f"security {security_id} on {trade_date.isoformat()}"
                )
            written.append(_written(match, created=False))
        return tuple(written)

    @staticmethod
    def _existing_daily_prices(
        connection: Connection,
        *,
        source: str,
        trade_date: date,
        security_ids: Sequence[int],
    ) -> dict[int, tuple[RowMapping, ...]]:
        if not security_ids:
            return {}
        rows = connection.execute(
            sa.select(daily_price_versions).where(
                daily_price_versions.c.source == source,
                daily_price_versions.c.trade_date == trade_date,
                daily_price_versions.c.security_id.in_(sorted(set(security_ids))),
            )
        ).mappings()
        grouped: dict[int, list[RowMapping]] = {}
        for row in rows:
            grouped.setdefault(row["security_id"], []).append(row)
        return {key: tuple(value) for key, value in grouped.items()}

    def append_publication_evidence_batch(
        self,
        connection: Connection,
        *,
        dataset_code: str,
        source: str,
        planned: Sequence[tuple[int, PublicationObservation]],
        lineage: LineageRef,
    ) -> tuple[int, int]:
        """Append one trade date's evidence; returns (created, deduplicated).

        Identical evidence still collapses onto the stored row through the
        evidence hash, and the repeated observation is still recorded, so a
        rerun stays auditable without inventing an evidence revision.
        """
        if not planned:
            return 0, 0
        target_column = _EVIDENCE_TARGETS[dataset_code]
        rows = [
            {
                **_dataclass_values(observation),
                "dataset_code": dataset_code,
                "source": source,
                target_column: version_id,
                "publication_evidence_hash": "0" * 64,
                "recorded_at": sa.func.statement_timestamp(),
                "raw_artifact_id": lineage.raw_artifact_id,
                "ingest_run_id": lineage.ingest_run_id,
            }
            for version_id, observation in planned
        ]
        created = {}
        for batch in _batched(rows):
            created.update(
                {
                    (row[target_column], row["evidence_type"]): row["id"]
                    for row in connection.execute(
                        insert(publication_evidence)
                        .values(list(batch))
                        .on_conflict_do_nothing(
                            index_elements=[
                                publication_evidence.c.publication_evidence_hash
                            ]
                        )
                        .returning(
                            publication_evidence.c.id,
                            publication_evidence.c[target_column],
                            publication_evidence.c.evidence_type,
                        )
                    ).mappings()
                }
            )
        pending = [
            (version_id, observation)
            for version_id, observation in planned
            if (version_id, observation.evidence_type) not in created
        ]
        stored = self._existing_evidence(
            connection,
            dataset_code=dataset_code,
            source=source,
            target_column=target_column,
            version_ids=[version_id for version_id, _ in pending],
        )
        evidence_ids: list[int] = []
        deduplicated = 0
        for version_id, observation in planned:
            evidence_id = created.get((version_id, observation.evidence_type))
            if evidence_id is None:
                match = _matching_evidence(
                    stored.get(version_id, ()), observation
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
        for batch in _batched(observations):
            connection.execute(
                insert(publication_evidence_observations)
                .values(list(batch))
                .on_conflict_do_nothing()
            )
        return len(planned) - deduplicated, deduplicated

    @staticmethod
    def _existing_evidence(
        connection: Connection,
        *,
        dataset_code: str,
        source: str,
        target_column: str,
        version_ids: Sequence[int],
    ) -> dict[int, tuple[RowMapping, ...]]:
        if not version_ids:
            return {}
        rows = connection.execute(
            sa.select(publication_evidence).where(
                publication_evidence.c.dataset_code == dataset_code,
                publication_evidence.c.source == source,
                publication_evidence.c[target_column].in_(sorted(set(version_ids))),
            )
        ).mappings()
        grouped: dict[int, list[RowMapping]] = {}
        for row in rows:
            grouped.setdefault(row[target_column], []).append(row)
        return {key: tuple(value) for key, value in grouped.items()}

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
        target_column = _EVIDENCE_TARGETS[dataset_code]
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


# Which publication_evidence column carries each dataset this writer serves.
_EVIDENCE_TARGETS = {
    "security_metadata": "security_metadata_version_id",
    "daily_price": "daily_price_version_id",
}

_DAILY_PRICE_BUSINESS_FIELDS = tuple(
    field.name for field in fields(DailyPriceObservation)
)

_EVIDENCE_MATCH_FIELDS = (
    "evidence_kind",
    "published_at",
    "evidence_source",
    "evidence_type",
    "quality_rank",
    "supersedes_evidence_id",
)


def _matching_version(
    candidates: tuple[RowMapping, ...], observation: DailyPriceObservation
) -> RowMapping | None:
    """The stored version whose business content is the one we just wrote.

    The hash is the database's, so the match is made on the business values it
    hashes rather than on a hash recomputed here — two ways of spelling the
    same identity is one way too many.
    """
    for row in candidates:
        if all(
            row[name] == getattr(observation, name)
            if row[name] is not None and getattr(observation, name) is not None
            else row[name] is None and getattr(observation, name) is None
            for name in _DAILY_PRICE_BUSINESS_FIELDS
        ):
            return row
    return None


def _matching_evidence(
    candidates: tuple[RowMapping, ...], observation: PublicationObservation
) -> RowMapping | None:
    for row in candidates:
        if all(
            row[name] == getattr(observation, name)
            if row[name] is not None and getattr(observation, name) is not None
            else row[name] is None and getattr(observation, name) is None
            for name in _EVIDENCE_MATCH_FIELDS
        ):
            return row
    return None


def _dataclass_values(instance: object) -> dict[str, object]:
    return {field.name: getattr(instance, field.name) for field in fields(instance)}


_SECURITY_METADATA_STATE_FIELDS = (
    "effective_to",
    "market",
    "name",
    "industry",
    "listed_on",
    "delisted_on",
)


def _security_metadata_state_matches(
    row: RowMapping, observation: SecurityMetadataObservation
) -> bool:
    return all(
        row[name] == getattr(observation, name)
        for name in _SECURITY_METADATA_STATE_FIELDS
    )


def _written(row: RowMapping, *, created: bool) -> WrittenVersion:
    return WrittenVersion(
        version_id=row["id"],
        business_content_hash=row["business_content_hash"],
        ingested_at=row["ingested_at"],
        created=created,
    )
