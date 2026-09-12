"""Dataset-specific PIT reads for sealed TDCC snapshot aggregates."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import (
    ingest_runs,
    raw_artifact_observations,
    raw_artifacts,
    security,
    tdcc_distribution,
    tdcc_distribution_schema_buckets,
    tdcc_snapshot_seals,
    tdcc_snapshot_version_observations,
    tdcc_snapshot_versions,
)
from stock_data_center.pit import PITResolver
from stock_data_center.pit.models import PITContext
from stock_data_center.pit.source_policy import SourcePolicyResolver
from stock_data_center.tdcc.models import (
    ResolvedTDCCSnapshot,
    TDCCBucket,
    TDCCLineageObservation,
)


class InvalidDateRangeError(ValueError):
    """Raised when the beginning of a requested history follows its end."""


class TDCCSnapshotService:
    def __init__(
        self,
        *,
        resolver: PITResolver | None = None,
        source_policy: SourcePolicyResolver | None = None,
    ) -> None:
        self._source_policy = source_policy or SourcePolicyResolver()
        self._resolver = resolver or PITResolver(source_policy=self._source_policy)

    def snapshot(
        self,
        connection: Connection,
        *,
        security_code: str,
        snapshot_date: date,
        context: PITContext,
        source: str | None = None,
    ) -> ResolvedTDCCSnapshot | None:
        security_id = self._security_id(connection, security_code)
        if security_id is None:
            return None
        return self._resolve(connection, security_id, snapshot_date, context, source)

    def history(
        self,
        connection: Connection,
        *,
        security_code: str,
        start_date: date,
        end_date: date,
        context: PITContext,
        source: str | None = None,
    ) -> tuple[ResolvedTDCCSnapshot, ...]:
        """Resolve every snapshot date in the range independently under ``context``.

        Candidate dates come from sealed versions only as an enumeration aid;
        each date is then resolved by the PIT resolver, and dates with no
        visible version are omitted.
        """
        if start_date > end_date:
            raise InvalidDateRangeError("start_date must not follow end_date")
        security_id = self._security_id(connection, security_code)
        if security_id is None:
            return ()
        policy = self._source_policy.resolve(
            connection, "tdcc_snapshot", context, source
        )
        snapshot_dates = connection.scalars(
            sa.select(tdcc_snapshot_versions.c.snapshot_date)
            .join(
                tdcc_snapshot_seals,
                tdcc_snapshot_seals.c.snapshot_version_id
                == tdcc_snapshot_versions.c.id,
            )
            .where(
                tdcc_snapshot_versions.c.security_id == security_id,
                tdcc_snapshot_versions.c.source == policy.source,
                tdcc_snapshot_versions.c.snapshot_date.between(start_date, end_date),
            )
            .distinct()
            .order_by(tdcc_snapshot_versions.c.snapshot_date)
        )
        resolved = (
            self._resolve(connection, security_id, snapshot_date, context, policy.source)
            for snapshot_date in snapshot_dates
        )
        return tuple(item for item in resolved if item is not None)

    def observations(
        self, connection: Connection, *, version_id: int
    ) -> tuple[TDCCLineageObservation, ...]:
        links = tdcc_snapshot_version_observations
        rows = connection.execute(
            sa.select(
                links.c.raw_artifact_id,
                raw_artifacts.c.raw_artifact_hash,
                raw_artifacts.c.storage_uri,
                links.c.ingest_run_id,
                ingest_runs.c.status,
                raw_artifact_observations.c.source_uri,
                raw_artifact_observations.c.fetched_at,
            )
            .select_from(
                links.join(
                    raw_artifact_observations,
                    sa.and_(
                        raw_artifact_observations.c.raw_artifact_id
                        == links.c.raw_artifact_id,
                        raw_artifact_observations.c.ingest_run_id
                        == links.c.ingest_run_id,
                    ),
                )
                .join(raw_artifacts, raw_artifacts.c.id == links.c.raw_artifact_id)
                .join(ingest_runs, ingest_runs.c.id == links.c.ingest_run_id)
            )
            .where(links.c.snapshot_version_id == version_id)
            .order_by(raw_artifact_observations.c.fetched_at, links.c.ingest_run_id)
        ).mappings()
        return tuple(
            TDCCLineageObservation(
                raw_artifact_id=row["raw_artifact_id"],
                raw_artifact_hash=row["raw_artifact_hash"],
                raw_artifact_uri=row["storage_uri"],
                ingest_run_id=row["ingest_run_id"],
                ingest_run_status=row["status"],
                source_uri=row["source_uri"],
                fetched_at=row["fetched_at"],
            )
            for row in rows
        )

    def _resolve(
        self,
        connection: Connection,
        security_id: int,
        snapshot_date: date,
        context: PITContext,
        source: str | None,
    ) -> ResolvedTDCCSnapshot | None:
        resolved = self._resolver.resolve(
            connection,
            dataset_code="tdcc_snapshot",
            logical_key={"security_id": security_id, "snapshot_date": snapshot_date},
            context=context,
            source=source,
        )
        if resolved is None:
            return None
        profile_buckets = tdcc_distribution_schema_buckets
        rows = connection.execute(
            sa.select(
                tdcc_distribution.c.bucket_code,
                profile_buckets.c.bucket_role,
                tdcc_distribution.c.holder_count,
                tdcc_distribution.c.shares,
                tdcc_distribution.c.ownership_percent,
            )
            .select_from(
                tdcc_distribution.join(
                    profile_buckets,
                    sa.and_(
                        profile_buckets.c.distribution_schema
                        == resolved.data["distribution_schema"],
                        profile_buckets.c.bucket_code == tdcc_distribution.c.bucket_code,
                    ),
                )
            )
            .where(
                tdcc_distribution.c.snapshot_version_id
                == resolved.provenance.version_id
            )
        ).mappings()
        distribution = tuple(TDCCBucket(**row) for row in rows)
        return ResolvedTDCCSnapshot(
            snapshot=resolved,
            distribution=tuple(sorted(distribution, key=_bucket_order)),
        )

    @staticmethod
    def _security_id(connection: Connection, security_code: str) -> int | None:
        return connection.scalar(
            sa.select(security.c.id).where(security.c.security_code == security_code)
        )


def _bucket_order(bucket: TDCCBucket) -> tuple[int, int, str]:
    """Order numeric source level codes numerically, then other codes byte-wise."""
    code = bucket.bucket_code
    if code.isascii() and code.isdigit():
        return (0, int(code), code)
    return (1, 0, code)
