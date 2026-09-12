"""Audited orchestration for real daily-market source resources."""

from __future__ import annotations

import calendar
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.postgresql import insert

from stock_data_center.db.metadata import (
    dataset_catalog,
    dataset_sources,
    import_checkpoints,
    import_manifests,
    import_quarantine,
    ingest_runs,
    publication_evidence,
    raw_artifact_observations,
    raw_artifacts,
)
from stock_data_center.ingestion.adapters import DailyMarketAdapter
from stock_data_center.ingestion.http import HttpSourceFetcher, SourceFetcher
from stock_data_center.ingestion.models import (
    DailyMarketRequest,
    ImportManifestResult,
    ParsedDailyMarket,
    ResourceImportResult,
    ResourceQuarantinedError,
    SourceDataError,
)
from stock_data_center.ingestion.raw_storage import (
    LocalRawArtifactStore,
    RawArtifactIntegrityError,
    StoredRawArtifact,
)
from stock_data_center.market_data import (
    LineageRef,
    MarketDataWriter,
    PublicationObservation,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _CapturedResource:
    run_id: UUID
    artifact_id: UUID
    artifact_hash: str
    storage_uri: str
    byte_size: int


class DailyMarketImporter:
    """Fetch one source resource raw-first, then normalize and commit it."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        writer: MarketDataWriter | None = None,
    ) -> None:
        self._engine = engine
        self._raw_store = raw_store or LocalRawArtifactStore()
        self._fetcher = fetcher or HttpSourceFetcher()
        self._writer = writer or MarketDataWriter()

    def run(
        self,
        *,
        adapter: DailyMarketAdapter,
        request: DailyMarketRequest,
        import_id: UUID | None = None,
        git_commit: str | None = None,
    ) -> ResourceImportResult:
        import_id = import_id or uuid4()
        resource = adapter.resource(request)
        lock_key = _advisory_lock_key(import_id, resource.resource_key)
        with self._engine.connect() as lock_connection:
            lock_connection.execute(
                sa.text("SELECT pg_advisory_lock(:lock_key)"),
                {"lock_key": lock_key},
            )
            lock_connection.commit()
            try:
                return self._run_locked(
                    adapter=adapter,
                    request=request,
                    import_id=import_id,
                    git_commit=git_commit,
                )
            finally:
                if lock_connection.in_transaction():
                    lock_connection.rollback()
                lock_connection.execute(
                    sa.text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": lock_key},
                )
                lock_connection.commit()

    def _run_locked(
        self,
        *,
        adapter: DailyMarketAdapter,
        request: DailyMarketRequest,
        import_id: UUID,
        git_commit: str | None,
    ) -> ResourceImportResult:
        resource = adapter.resource(request)
        scope = {
            "security_code": request.security_code,
            "month": request.month.isoformat(),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }
        fingerprint = _fingerprint(
            {
                "dataset_code": adapter.dataset_code,
                "source": adapter.source,
                "adapter_version": adapter.version,
                "source_semantics": {
                    "traded_quantity_unit": (
                        adapter.semantics.traded_quantity_unit.value
                    ),
                    "trade_value_unit": adapter.semantics.trade_value_unit.value,
                },
                "raw_store": self._raw_store.configuration_identity,
                "scope": scope,
            }
        )
        with self._engine.begin() as connection:
            self._ensure_source(connection, adapter)
            self._start_or_resume_manifest(
                connection,
                import_id=import_id,
                adapter=adapter,
                git_commit=git_commit or _git_commit(),
                scope=scope,
                fingerprint=fingerprint,
            )
            resumed = self._completed_checkpoint(
                connection, import_id, resource.resource_key, adapter.source
            )
            if resumed is not None:
                return resumed
            captured = self._captured_checkpoint(
                connection, import_id, resource.resource_key
            )

        if captured is not None:
            try:
                content = self._raw_store.read(
                    storage_uri=captured.storage_uri,
                    expected_digest=captured.artifact_hash,
                    expected_byte_size=captured.byte_size,
                )
            except RawArtifactIntegrityError as error:
                with self._engine.begin() as connection:
                    self._record_operational_failure(
                        connection,
                        import_id=import_id,
                        resource_key=resource.resource_key,
                        run_id=captured.run_id,
                        artifact_id=captured.artifact_id,
                        reason_code="raw_artifact_integrity",
                        detail=str(error),
                    )
                raise
            run_id = captured.run_id
            artifact_id = captured.artifact_id
            artifact_hash = captured.artifact_hash
            artifact_created = False
        else:
            try:
                fetched = self._fetcher.fetch(resource)
                stored = self._raw_store.put(fetched.content)
            except Exception as error:
                with self._engine.begin() as connection:
                    self._fail_manifest(
                        connection, import_id, f"fetch failed: {error}"
                    )
                raise

            with self._engine.begin() as connection:
                run_id, artifact_id, artifact_created = self._capture_raw(
                    connection,
                    import_id=import_id,
                    adapter=adapter,
                    resource_key=resource.resource_key,
                    stored=stored,
                    source_uri=fetched.source_uri,
                    fetched_at=fetched.fetched_at,
                    media_type=fetched.media_type,
                )
            content = fetched.content
            artifact_hash = stored.digest

        try:
            parsed = adapter.parse(content, request)
        except SourceDataError as error:
            with self._engine.begin() as connection:
                self._quarantine(
                    connection,
                    import_id=import_id,
                    resource_key=resource.resource_key,
                    run_id=run_id,
                    artifact_id=artifact_id,
                    reason_code=error.reason_code,
                    detail=str(error),
                )
            raise ResourceQuarantinedError(
                f"{resource.resource_key} quarantined as "
                f"{error.reason_code}: {error}"
            ) from error
        except Exception as error:
            with self._engine.begin() as connection:
                self._record_operational_failure(
                    connection,
                    import_id=import_id,
                    resource_key=resource.resource_key,
                    run_id=run_id,
                    artifact_id=artifact_id,
                    reason_code="adapter_operational_error",
                    detail=str(error),
                )
            raise

        try:
            with self._engine.begin() as connection:
                return self._write_parsed(
                    connection,
                    import_id=import_id,
                    adapter=adapter,
                    request=request,
                    resource_key=resource.resource_key,
                    run_id=run_id,
                    artifact_id=artifact_id,
                    artifact_hash=artifact_hash,
                    artifact_created=artifact_created,
                    parsed=parsed,
                )
        except Exception as error:
            try:
                with self._engine.begin() as connection:
                    self._record_operational_failure(
                        connection,
                        import_id=import_id,
                        resource_key=resource.resource_key,
                        run_id=run_id,
                        artifact_id=artifact_id,
                        reason_code="writer_operational_error",
                        detail=str(error),
                    )
            except sa.exc.SQLAlchemyError:
                # Preserve the original writer/DB failure even when audit storage
                # is affected by the same operational outage.
                logger.exception("could not record writer operational failure")
            raise

    @staticmethod
    def manifest(connection: Connection, import_id: UUID) -> ImportManifestResult:
        row = connection.execute(
            sa.select(import_manifests).where(
                import_manifests.c.import_id == import_id
            )
        ).mappings().one()
        return ImportManifestResult(
            import_id=str(import_id),
            status=row["status"],
            result_counts=row["result_counts"],
            reconciliation=row["reconciliation"],
        )

    @staticmethod
    def _ensure_source(
        connection: Connection, adapter: DailyMarketAdapter
    ) -> None:
        connection.execute(
            insert(dataset_catalog)
            .values(
                dataset_code=adapter.dataset_code,
                description="official per-security daily market observations",
                schema_version="v1",
            )
            .on_conflict_do_nothing(index_elements=[dataset_catalog.c.dataset_code])
        )
        connection.execute(
            insert(dataset_sources)
            .values(
                dataset_code=adapter.dataset_code,
                source=adapter.source,
                supports_market_pit=True,
                supports_system_pit=True,
                publication_time_quality=0,
                evidence_status="verified",
                accepted_evidence_types=["official"],
                is_canonical=False,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    dataset_sources.c.dataset_code,
                    dataset_sources.c.source,
                ]
            )
        )
        source = connection.execute(
            sa.select(dataset_sources).where(
                dataset_sources.c.dataset_code == adapter.dataset_code,
                dataset_sources.c.source == adapter.source,
            )
        ).mappings().one()
        if (
            not source["supports_system_pit"]
            or source["evidence_status"] != "verified"
            or "official" not in source["accepted_evidence_types"]
        ):
            raise RuntimeError(
                f"{adapter.source} daily_price source policy is incompatible"
            )

    @staticmethod
    def _start_or_resume_manifest(
        connection: Connection,
        *,
        import_id: UUID,
        adapter: DailyMarketAdapter,
        git_commit: str,
        scope: dict[str, str],
        fingerprint: str,
    ) -> None:
        inserted = connection.execute(
            insert(import_manifests)
            .values(
                import_id=import_id,
                dataset_code=adapter.dataset_code,
                source=adapter.source,
                adapter_version=adapter.version,
                git_commit=git_commit,
                source_scope=scope,
                configuration_fingerprint=fingerprint,
                status="running",
            )
            .on_conflict_do_nothing(
                index_elements=[import_manifests.c.import_id]
            )
            .returning(import_manifests.c.import_id)
        ).scalar_one_or_none()
        if inserted is not None:
            return
        existing = connection.execute(
            sa.select(import_manifests).where(
                import_manifests.c.import_id == import_id
            )
        ).mappings().one()
        expected = {
            "dataset_code": adapter.dataset_code,
            "source": adapter.source,
            "adapter_version": adapter.version,
            "git_commit": git_commit,
            "source_scope": scope,
            "configuration_fingerprint": fingerprint,
        }
        if any(existing[key] != value for key, value in expected.items()):
            raise ValueError("import_id cannot be reused with changed configuration")
        if existing["status"] != "succeeded":
            connection.execute(
                import_manifests.update()
                .where(import_manifests.c.import_id == import_id)
                .values(status="running", completed_at=None)
            )

    @staticmethod
    def _completed_checkpoint(
        connection: Connection,
        import_id: UUID,
        resource_key: str,
        source: str,
    ) -> ResourceImportResult | None:
        row = connection.execute(
            sa.select(
                import_checkpoints,
                raw_artifacts.c.raw_artifact_hash,
                ingest_runs.c.status.label("run_status"),
            )
            .outerjoin(
                raw_artifacts,
                raw_artifacts.c.id == import_checkpoints.c.last_raw_artifact_id,
            )
            .join(
                ingest_runs,
                ingest_runs.c.id == import_checkpoints.c.last_ingest_run_id,
            )
            .where(
                import_checkpoints.c.import_id == import_id,
                import_checkpoints.c.resource_key == resource_key,
                import_checkpoints.c.status == "succeeded",
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        if row["run_status"] != "succeeded":
            raise RuntimeError(
                "succeeded checkpoint must reference a succeeded ingest run"
            )
        manifest = connection.execute(
            sa.select(
                import_manifests.c.result_counts,
                import_manifests.c.reconciliation,
            ).where(import_manifests.c.import_id == import_id)
        ).mappings().one()
        counts = manifest["result_counts"]
        reconciliation = manifest["reconciliation"]
        return ResourceImportResult(
            resource_key=resource_key,
            source=source,
            raw_artifact_hash=row["raw_artifact_hash"],
            raw_artifact_created=False,
            business_versions_created=counts.get("business_version_count", 0),
            business_versions_deduplicated=counts.get("dedup_count", 0),
            publication_evidence_created=counts.get("evidence_count", 0),
            publication_evidence_deduplicated=counts.get(
                "evidence_dedup_count", 0
            ),
            evidence_observations=counts.get("evidence_observation_count", 0),
            unknown_publication_observations=counts.get(
                "unknown_publication_count", 0
            ),
            normalized_rows=counts.get("normalized_row_count", 0),
            coverage_start=_optional_date(reconciliation.get("coverage_start")),
            coverage_end=_optional_date(reconciliation.get("coverage_end")),
            resumed_from_checkpoint=True,
        )

    @staticmethod
    def _captured_checkpoint(
        connection: Connection,
        import_id: UUID,
        resource_key: str,
    ) -> _CapturedResource | None:
        row = connection.execute(
            sa.select(
                import_checkpoints.c.last_ingest_run_id,
                import_checkpoints.c.last_raw_artifact_id,
                raw_artifacts.c.raw_artifact_hash,
                raw_artifacts.c.storage_uri,
                raw_artifacts.c.byte_size,
                ingest_runs.c.status.label("run_status"),
            )
            .join(
                raw_artifacts,
                raw_artifacts.c.id == import_checkpoints.c.last_raw_artifact_id,
            )
            .join(
                ingest_runs,
                ingest_runs.c.id == import_checkpoints.c.last_ingest_run_id,
            )
            .where(
                import_checkpoints.c.import_id == import_id,
                import_checkpoints.c.resource_key == resource_key,
                import_checkpoints.c.status == "captured",
            )
            .with_for_update(of=import_checkpoints)
        ).mappings().one_or_none()
        if row is None:
            return None
        if row["run_status"] != "running":
            raise RuntimeError(
                "captured checkpoint must reference its original running ingest run"
            )
        return _CapturedResource(
            run_id=row["last_ingest_run_id"],
            artifact_id=row["last_raw_artifact_id"],
            artifact_hash=row["raw_artifact_hash"],
            storage_uri=row["storage_uri"],
            byte_size=row["byte_size"],
        )

    @staticmethod
    def _capture_raw(
        connection: Connection,
        *,
        import_id: UUID,
        adapter: DailyMarketAdapter,
        resource_key: str,
        stored: StoredRawArtifact,
        source_uri: str,
        fetched_at: datetime,
        media_type: str,
    ) -> tuple[UUID, UUID, bool]:
        run_id = connection.execute(
            insert(ingest_runs)
            .values(
                dataset_code=adapter.dataset_code,
                source=adapter.source,
                status="running",
                started_at=sa.func.statement_timestamp(),
                run_metadata={
                    "import_id": str(import_id),
                    "resource_key": resource_key,
                    "adapter_version": adapter.version,
                },
            )
            .returning(ingest_runs.c.id)
        ).scalar_one()
        inserted_artifact = connection.execute(
            insert(raw_artifacts)
            .values(
                raw_artifact_hash=stored.digest,
                storage_uri=stored.storage_uri,
                byte_size=stored.byte_size,
                media_type=media_type,
            )
            .on_conflict_do_nothing(
                index_elements=[raw_artifacts.c.raw_artifact_hash]
            )
            .returning(raw_artifacts.c.id)
        ).scalar_one_or_none()
        if inserted_artifact is None:
            artifact = connection.execute(
                sa.select(raw_artifacts).where(
                    raw_artifacts.c.raw_artifact_hash == stored.digest
                )
            ).mappings().one()
            if artifact["byte_size"] != stored.byte_size:
                raise RuntimeError("raw artifact digest has conflicting size")
            artifact_id = artifact["id"]
            artifact_created = False
        else:
            artifact_id = inserted_artifact
            artifact_created = True
        connection.execute(
            insert(raw_artifact_observations).values(
                raw_artifact_id=artifact_id,
                ingest_run_id=run_id,
                source_uri=source_uri,
                fetched_at=fetched_at,
            )
        )
        connection.execute(
            insert(import_checkpoints)
            .values(
                import_id=import_id,
                resource_key=resource_key,
                status="captured",
                attempt_count=1,
                last_ingest_run_id=run_id,
                last_raw_artifact_id=artifact_id,
                updated_at=sa.func.statement_timestamp(),
            )
            .on_conflict_do_update(
                index_elements=[
                    import_checkpoints.c.import_id,
                    import_checkpoints.c.resource_key,
                ],
                set_={
                    "status": "captured",
                    "attempt_count": import_checkpoints.c.attempt_count + 1,
                    "last_ingest_run_id": run_id,
                    "last_raw_artifact_id": artifact_id,
                    "updated_at": sa.func.statement_timestamp(),
                    "error_code": None,
                    "error_detail": None,
                },
            )
        )
        return run_id, artifact_id, artifact_created

    def _write_parsed(
        self,
        connection: Connection,
        *,
        import_id: UUID,
        adapter: DailyMarketAdapter,
        request: DailyMarketRequest,
        resource_key: str,
        run_id: UUID,
        artifact_id: UUID,
        artifact_hash: str,
        artifact_created: bool,
        parsed: ParsedDailyMarket,
    ) -> ResourceImportResult:
        lineage = LineageRef(artifact_id, run_id)
        security_id = self._writer.register_security(
            connection, security_code=parsed.security_code
        )
        created = 0
        deduplicated = 0
        evidence_created = 0
        evidence_deduplicated = 0
        evidence_observations = 0
        for observation in parsed.rows:
            written = self._writer.append_daily_price(
                connection,
                security_id=security_id,
                source=adapter.source,
                observation=observation,
                lineage=lineage,
            )
            created += int(written.created)
            deduplicated += int(not written.created)
            evidence_existed = connection.scalar(
                sa.select(sa.exists().where(
                    publication_evidence.c.dataset_code == adapter.dataset_code,
                    publication_evidence.c.source == adapter.source,
                    publication_evidence.c.daily_price_version_id
                    == written.version_id,
                    publication_evidence.c.evidence_kind == "unknown",
                    publication_evidence.c.evidence_source
                    == f"{adapter.source} official historical endpoint",
                    publication_evidence.c.evidence_type == "official",
                    publication_evidence.c.quality_rank == 0,
                ))
            )
            self._writer.append_publication_evidence(
                connection,
                dataset_code="daily_price",
                source=adapter.source,
                version_id=written.version_id,
                observation=PublicationObservation(
                    evidence_kind="unknown",
                    published_at=None,
                    evidence_source=(
                        f"{adapter.source} official historical endpoint"
                    ),
                    evidence_type="official",
                    quality_rank=0,
                ),
                lineage=lineage,
            )
            evidence_created += int(not evidence_existed)
            evidence_deduplicated += int(evidence_existed)
            evidence_observations += 1

        raw_stats = connection.execute(
            sa.select(
                sa.func.count(
                    sa.distinct(raw_artifact_observations.c.raw_artifact_id)
                ).label("artifact_count"),
                sa.func.count().label("observation_count"),
            )
            .select_from(
                raw_artifact_observations.join(
                    ingest_runs,
                    ingest_runs.c.id == raw_artifact_observations.c.ingest_run_id,
                )
            )
            .where(ingest_runs.c.run_metadata["import_id"].astext == str(import_id))
        ).mappings().one()
        quarantine_details = list(
            connection.scalars(
                sa.select(import_quarantine.c.reason_detail)
                .where(import_quarantine.c.import_id == import_id)
                .order_by(import_quarantine.c.id)
            )
        )

        counts = {
            "requested_resource_count": 1,
            "completed_resource_count": 1,
            "raw_artifact_count": raw_stats["artifact_count"],
            "raw_observation_count": raw_stats["observation_count"],
            "business_version_count": created,
            "dedup_count": deduplicated,
            "evidence_count": evidence_created,
            "evidence_dedup_count": evidence_deduplicated,
            "evidence_observation_count": evidence_observations,
            "unknown_publication_count": len(parsed.rows),
            "normalized_row_count": len(parsed.rows),
            "rejected_quarantined_count": len(quarantine_details),
        }
        reconciliation = {
            "source": adapter.source,
            "adapter_version": adapter.version,
            "resource_key": resource_key,
            "raw_artifact_hash": artifact_hash,
            "requested_date_start": request.month.isoformat(),
            "requested_date_end": date(
                request.month.year,
                request.month.month,
                calendar.monthrange(request.month.year, request.month.month)[1],
            ).isoformat(),
            "requested_security": request.security_code,
            "actual_security": parsed.security_code,
            "actual_security_name": parsed.security_name,
            "actual_row_count": len(parsed.rows),
            "coverage_start": (
                parsed.coverage_start.isoformat() if parsed.coverage_start else None
            ),
            "coverage_end": (
                parsed.coverage_end.isoformat() if parsed.coverage_end else None
            ),
            "coverage_validation": "not_evaluated",
            "coverage_gaps": None,
            "warnings_anomalies": quarantine_details,
            "source_units": {
                "traded_quantity": adapter.semantics.traded_quantity_unit.value,
                "trade_value": adapter.semantics.trade_value_unit.value,
            },
            "canonical_units": {
                "volume": "share",
                "trade_value": "twd",
            },
            "publication_time": "unknown",
        }
        checkpoint_update = connection.execute(
            import_checkpoints.update()
            .where(
                import_checkpoints.c.import_id == import_id,
                import_checkpoints.c.resource_key == resource_key,
                import_checkpoints.c.status == "captured",
                import_checkpoints.c.last_ingest_run_id == run_id,
                import_checkpoints.c.last_raw_artifact_id == artifact_id,
            )
            .values(
                status="succeeded",
                updated_at=sa.func.statement_timestamp(),
                error_code=None,
                error_detail=None,
            )
        )
        if checkpoint_update.rowcount != 1:
            raise RuntimeError("captured checkpoint ownership changed before commit")
        run_update = connection.execute(
            ingest_runs.update()
            .where(ingest_runs.c.id == run_id, ingest_runs.c.status == "running")
            .values(status="succeeded", completed_at=sa.func.statement_timestamp())
        )
        if run_update.rowcount != 1:
            raise RuntimeError("ingest run was not running at commit")
        connection.execute(
            import_manifests.update()
            .where(import_manifests.c.import_id == import_id)
            .values(
                status="succeeded",
                completed_at=sa.func.statement_timestamp(),
                result_counts=counts,
                reconciliation=reconciliation,
                warnings=quarantine_details,
            )
        )
        return ResourceImportResult(
            resource_key=resource_key,
            source=adapter.source,
            raw_artifact_hash=artifact_hash,
            raw_artifact_created=artifact_created,
            business_versions_created=created,
            business_versions_deduplicated=deduplicated,
            publication_evidence_created=evidence_created,
            publication_evidence_deduplicated=evidence_deduplicated,
            evidence_observations=evidence_observations,
            unknown_publication_observations=len(parsed.rows),
            normalized_rows=len(parsed.rows),
            coverage_start=parsed.coverage_start,
            coverage_end=parsed.coverage_end,
        )

    @staticmethod
    def _quarantine(
        connection: Connection,
        *,
        import_id: UUID,
        resource_key: str,
        run_id: UUID,
        artifact_id: UUID,
        reason_code: str,
        detail: str,
    ) -> None:
        safe_detail = detail or "source record rejected without detail"
        connection.execute(
            ingest_runs.update()
            .where(ingest_runs.c.id == run_id)
            .values(status="failed", completed_at=sa.func.statement_timestamp())
        )
        connection.execute(
            insert(import_quarantine).values(
                import_id=import_id,
                resource_key=resource_key,
                ingest_run_id=run_id,
                raw_artifact_id=artifact_id,
                reason_code=reason_code,
                reason_detail=safe_detail,
            )
        )
        checkpoint_update = connection.execute(
            import_checkpoints.update()
            .where(
                import_checkpoints.c.import_id == import_id,
                import_checkpoints.c.resource_key == resource_key,
                import_checkpoints.c.status == "captured",
                import_checkpoints.c.last_ingest_run_id == run_id,
                import_checkpoints.c.last_raw_artifact_id == artifact_id,
            )
            .values(
                status="quarantined",
                updated_at=sa.func.statement_timestamp(),
                error_code=reason_code,
                error_detail=safe_detail,
            )
        )
        if checkpoint_update.rowcount != 1:
            raise RuntimeError("captured checkpoint ownership changed before quarantine")
        connection.execute(
            import_manifests.update()
            .where(import_manifests.c.import_id == import_id)
            .values(
                status="failed",
                completed_at=sa.func.statement_timestamp(),
                result_counts={
                    "requested_resource_count": 1,
                    "completed_resource_count": 0,
                    "raw_artifact_count": 1,
                    "raw_observation_count": 1,
                    "business_version_count": 0,
                    "dedup_count": 0,
                    "evidence_count": 0,
                    "evidence_dedup_count": 0,
                    "evidence_observation_count": 0,
                    "unknown_publication_count": 0,
                    "normalized_row_count": 0,
                    "rejected_quarantined_count": 1,
                },
                reconciliation={
                    "coverage_start": None,
                    "coverage_end": None,
                    "coverage_validation": "not_evaluated",
                    "coverage_gaps": None,
                    "warnings_anomalies": [safe_detail],
                },
                warnings=[safe_detail],
            )
        )

    @staticmethod
    def _record_operational_failure(
        connection: Connection,
        *,
        import_id: UUID,
        resource_key: str,
        run_id: UUID,
        artifact_id: UUID,
        reason_code: str,
        detail: str,
    ) -> None:
        safe_detail = detail or "operational failure without detail"
        checkpoint_update = connection.execute(
            import_checkpoints.update()
            .where(
                import_checkpoints.c.import_id == import_id,
                import_checkpoints.c.resource_key == resource_key,
                import_checkpoints.c.status == "captured",
                import_checkpoints.c.last_ingest_run_id == run_id,
                import_checkpoints.c.last_raw_artifact_id == artifact_id,
            )
            .values(
                updated_at=sa.func.statement_timestamp(),
                error_code=reason_code,
                error_detail=safe_detail,
            )
        )
        if checkpoint_update.rowcount != 1:
            raise RuntimeError(
                "captured checkpoint ownership changed during operational failure"
            )
        connection.execute(
            import_manifests.update()
            .where(import_manifests.c.import_id == import_id)
            .values(
                status="failed",
                completed_at=sa.func.statement_timestamp(),
                result_counts={
                    "requested_resource_count": 1,
                    "completed_resource_count": 0,
                    "raw_artifact_count": 1,
                    "raw_observation_count": 1,
                    "business_version_count": 0,
                    "dedup_count": 0,
                    "evidence_count": 0,
                    "evidence_dedup_count": 0,
                    "evidence_observation_count": 0,
                    "unknown_publication_count": 0,
                    "normalized_row_count": 0,
                    "rejected_quarantined_count": 0,
                },
                reconciliation={
                    "coverage_start": None,
                    "coverage_end": None,
                    "coverage_validation": "not_evaluated",
                    "coverage_gaps": None,
                    "warnings_anomalies": [safe_detail],
                },
                warnings=[safe_detail],
            )
        )

    @staticmethod
    def _fail_manifest(
        connection: Connection, import_id: UUID, detail: str
    ) -> None:
        connection.execute(
            import_manifests.update()
            .where(import_manifests.c.import_id == import_id)
            .values(
                status="failed",
                completed_at=sa.func.statement_timestamp(),
                result_counts={
                    "requested_resource_count": 1,
                    "completed_resource_count": 0,
                    "raw_artifact_count": 0,
                    "rejected_quarantined_count": 0,
                },
                reconciliation={
                    "coverage_start": None,
                    "coverage_end": None,
                    "coverage_validation": "not_evaluated",
                    "coverage_gaps": None,
                    "warnings_anomalies": [detail],
                },
                warnings=[detail],
            )
        )


def _fingerprint(value: object) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _advisory_lock_key(import_id: UUID, resource_key: str) -> int:
    identity = f"{import_id}:{resource_key}".encode()
    return int.from_bytes(sha256(identity).digest()[:8], "big", signed=True)


def _git_commit() -> str:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return f"{commit}-dirty" if dirty else commit
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _optional_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    return date.fromisoformat(value)
