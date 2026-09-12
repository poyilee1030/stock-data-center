"""Normalized append-only writes for TDCC snapshot aggregates."""

from __future__ import annotations

from dataclasses import fields
from datetime import date

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from stock_data_center.db.metadata import (
    publication_evidence,
    publication_evidence_observations,
    tdcc_distribution,
    tdcc_distribution_schema_buckets,
    tdcc_snapshot_seals,
    tdcc_snapshot_version_observations,
    tdcc_snapshot_versions,
)
from stock_data_center.tdcc.models import (
    SealedSnapshot,
    TDCCBucketObservation,
    TDCCDistributionError,
    TDCCLineageRef,
    TDCCPublication,
    TDCCSnapshotObservation,
    WrittenSnapshot,
)


_BUSINESS_REVISION_INDEX = "uq_tdcc_snapshot_business_revision"


class TDCCSnapshotWriter:
    """Build a snapshot draft, seal it atomically, and append evidence separately."""

    def write_snapshot(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        observation: TDCCSnapshotObservation,
        lineage: TDCCLineageRef,
    ) -> WrittenSnapshot:
        """Write and seal one complete snapshot without creating fake revisions.

        The distribution must be complete and valid for its declared profile.
        An identical sealed snapshot is reused: the draft is discarded and only
        the new artifact/run observation is linked to the existing version.
        """
        self.validate_distribution(connection, observation)
        draft_hash: str | None = None
        existing: SealedSnapshot | None = None
        savepoint = connection.begin_nested()
        try:
            version_id = self.begin_snapshot(
                connection,
                security_id=security_id,
                source=source,
                snapshot_date=observation.snapshot_date,
                distribution_schema=observation.distribution_schema,
                lineage=lineage,
            )
            for bucket in observation.distribution:
                self.append_bucket(connection, version_id=version_id, observation=bucket)
            # The same DB function the seal trigger uses; never hashed in Python.
            draft_hash = connection.scalar(
                sa.select(sa.func.stockdc_tdcc_snapshot_business_hash(version_id))
            )
            existing = self._sealed_revision(
                connection, security_id, source, observation.snapshot_date, draft_hash
            )
            if existing is None:
                sealed = self.seal(connection, version_id=version_id)
        except IntegrityError as error:
            savepoint.rollback()
            # A concurrent writer may have sealed the identical revision first.
            constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
            if draft_hash is None or constraint != _BUSINESS_REVISION_INDEX:
                raise
            existing = self._sealed_revision(
                connection, security_id, source, observation.snapshot_date, draft_hash
            )
            if existing is None:
                raise
        except BaseException:
            savepoint.rollback()
            raise
        else:
            if existing is None:
                savepoint.commit()
                return WrittenSnapshot(
                    version_id=sealed.version_id,
                    business_content_hash=sealed.business_content_hash,
                    ingested_at=sealed.ingested_at,
                    created=True,
                )
            savepoint.rollback()

        self._link_observation(
            connection, version_id=existing.version_id, lineage=lineage
        )
        return WrittenSnapshot(
            version_id=existing.version_id,
            business_content_hash=existing.business_content_hash,
            ingested_at=existing.ingested_at,
            created=False,
        )

    @staticmethod
    def validate_distribution(
        connection: Connection, observation: TDCCSnapshotObservation
    ) -> None:
        """Check the distribution against its registered profile before writing.

        PostgreSQL enforces the same rules per row and at seal; this check makes
        the canonical ingestion path fail before any draft is created.
        """
        profile = tdcc_distribution_schema_buckets
        roles = dict(
            connection.execute(
                sa.select(profile.c.bucket_code, profile.c.bucket_role).where(
                    profile.c.distribution_schema == observation.distribution_schema
                )
            ).tuples().all()
        )
        if not roles:
            raise TDCCDistributionError(
                f"unknown distribution schema {observation.distribution_schema!r}"
            )
        supplied = {bucket.bucket_code for bucket in observation.distribution}
        if unknown := sorted(supplied - roles.keys()):
            raise TDCCDistributionError(
                f"buckets not in {observation.distribution_schema}: {', '.join(unknown)}"
            )
        if missing := sorted(roles.keys() - supplied, key=_code_order):
            raise TDCCDistributionError(
                f"incomplete {observation.distribution_schema} distribution; "
                f"missing buckets: {', '.join(missing)}"
            )
        for bucket in observation.distribution:
            role = roles[bucket.bucket_code]
            if role == "adjustment":
                if bucket.holder_count not in (None, 0):
                    raise TDCCDistributionError(
                        f"adjustment bucket {bucket.bucket_code} cannot carry "
                        f"holder_count {bucket.holder_count}"
                    )
            elif (
                bucket.holder_count is None
                or bucket.shares < 0
                or bucket.ownership_percent < 0
            ):
                raise TDCCDistributionError(
                    f"{role} bucket {bucket.bucket_code} requires holder_count and "
                    "non-negative shares/percent"
                )

    def begin_snapshot(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        snapshot_date: date,
        distribution_schema: str,
        lineage: TDCCLineageRef,
    ) -> int:
        version_id = connection.execute(
            tdcc_snapshot_versions.insert()
            .values(
                security_id=security_id,
                source=source,
                snapshot_date=snapshot_date,
                distribution_schema=distribution_schema,
                business_content_hash=None,
                raw_artifact_id=lineage.raw_artifact_id,
                ingest_run_id=lineage.ingest_run_id,
            )
            .returning(tdcc_snapshot_versions.c.id)
        ).scalar_one()
        self._link_observation(connection, version_id=version_id, lineage=lineage)
        return version_id

    def append_bucket(
        self,
        connection: Connection,
        *,
        version_id: int,
        observation: TDCCBucketObservation,
    ) -> int:
        return connection.execute(
            tdcc_distribution.insert()
            .values(snapshot_version_id=version_id, **_dataclass_values(observation))
            .returning(tdcc_distribution.c.id)
        ).scalar_one()

    def seal(self, connection: Connection, *, version_id: int) -> SealedSnapshot:
        row = connection.execute(
            tdcc_snapshot_seals.insert()
            .values(
                snapshot_version_id=version_id,
                business_content_hash="0" * 64,
                ingested_at=sa.func.statement_timestamp(),
            )
            .returning(
                tdcc_snapshot_seals.c.snapshot_version_id,
                tdcc_snapshot_seals.c.business_content_hash,
                tdcc_snapshot_seals.c.ingested_at,
            )
        ).mappings().one()
        return SealedSnapshot(
            version_id=row["snapshot_version_id"],
            business_content_hash=row["business_content_hash"],
            ingested_at=row["ingested_at"],
        )

    def append_publication_evidence(
        self,
        connection: Connection,
        *,
        source: str,
        version_id: int,
        observation: TDCCPublication,
        lineage: TDCCLineageRef,
    ) -> int:
        evidence_values = _dataclass_values(observation)
        values = {
            **evidence_values,
            "dataset_code": "tdcc_snapshot",
            "source": source,
            "tdcc_snapshot_version_id": version_id,
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
                publication_evidence.c.dataset_code == "tdcc_snapshot",
                publication_evidence.c.source == source,
                publication_evidence.c.tdcc_snapshot_version_id == version_id,
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

    @staticmethod
    def _sealed_revision(
        connection: Connection,
        security_id: int,
        source: str,
        snapshot_date: date,
        business_content_hash: str,
    ) -> SealedSnapshot | None:
        row = connection.execute(
            sa.select(
                tdcc_snapshot_seals.c.snapshot_version_id,
                tdcc_snapshot_seals.c.business_content_hash,
                tdcc_snapshot_seals.c.ingested_at,
            )
            .select_from(
                tdcc_snapshot_versions.join(
                    tdcc_snapshot_seals,
                    tdcc_snapshot_seals.c.snapshot_version_id
                    == tdcc_snapshot_versions.c.id,
                )
            )
            .where(
                tdcc_snapshot_versions.c.security_id == security_id,
                tdcc_snapshot_versions.c.source == source,
                tdcc_snapshot_versions.c.snapshot_date == snapshot_date,
                tdcc_snapshot_versions.c.business_content_hash
                == business_content_hash,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return SealedSnapshot(
            version_id=row["snapshot_version_id"],
            business_content_hash=row["business_content_hash"],
            ingested_at=row["ingested_at"],
        )

    @staticmethod
    def _link_observation(
        connection: Connection,
        *,
        version_id: int,
        lineage: TDCCLineageRef,
    ) -> None:
        connection.execute(
            insert(tdcc_snapshot_version_observations)
            .values(
                snapshot_version_id=version_id,
                raw_artifact_id=lineage.raw_artifact_id,
                ingest_run_id=lineage.ingest_run_id,
            )
            .on_conflict_do_nothing()
        )


def _code_order(code: str) -> tuple[int, int, str]:
    return (0, int(code), code) if code.isascii() and code.isdigit() else (1, 0, code)


def _dataclass_values(instance: object) -> dict[str, object]:
    return {field.name: getattr(instance, field.name) for field in fields(instance)}
