"""Shared raw-first lifecycle for Phase 9 source-resource imports."""

from __future__ import annotations

import json
import logging
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from typing import Protocol, TypeVar
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
    raw_artifact_observations,
    raw_artifacts,
)
from stock_data_center.ingestion.http import HttpSourceFetcher, SourceFetcher
from stock_data_center.ingestion.models import (
    ArtifactOrigin,
    EvidenceContext,
    IngestPurpose,
    ImportManifestResult,
    ResourceImportResult,
    ResourceQuarantinedError,
    SourceDataError,
    UnusableSourceResponseError,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import (
    LocalRawArtifactStore,
    RawArtifactIntegrityError,
    StoredRawArtifact,
)
from stock_data_center.market_data import LineageRef

logger = logging.getLogger(__name__)

RequestT = TypeVar("RequestT")
ParsedT = TypeVar("ParsedT")


class RawFirstAdapter(Protocol[RequestT, ParsedT]):
    dataset_code: str
    source: str
    version: str

    def resource(self, request: RequestT) -> SourceResource: ...

    def parse(self, content: bytes, request: RequestT) -> ParsedT: ...


@dataclass(frozen=True, slots=True)
class BusinessWriteResult:
    """Domain writes and reconciliation returned to the shared lifecycle."""

    business_versions_created: int
    business_versions_deduplicated: int
    publication_evidence_created: int
    publication_evidence_deduplicated: int
    evidence_observations: int
    unknown_publication_observations: int
    normalized_rows: int
    coverage_start: date | None
    coverage_end: date | None
    reconciliation: Mapping[str, object]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _CapturedResource:
    run_id: UUID
    artifact_id: UUID
    artifact_hash: str
    storage_uri: str
    byte_size: int


class RawFirstImporter[RequestT, ParsedT](ABC):
    """Serialize fetch/capture/parse/write and retain resumable raw state."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
    ) -> None:
        self._engine = engine
        self._raw_store = raw_store or LocalRawArtifactStore()
        self._fetcher = fetcher or HttpSourceFetcher()

    @property
    def engine(self) -> Engine:
        """The engine this importer writes through.

        Exposed so a range runner can ask the calendar which dates to request
        without being handed a second engine that might point elsewhere.
        """
        return self._engine

    def run(
        self,
        *,
        adapter: RawFirstAdapter[RequestT, ParsedT],
        request: RequestT,
        import_id: UUID | None = None,
        git_commit: str | None = None,
        purpose: IngestPurpose = IngestPurpose.UNSPECIFIED,
        artifact_origin: ArtifactOrigin = ArtifactOrigin.OFFICIAL_FETCH,
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
                    resource=resource,
                    import_id=import_id,
                    git_commit=git_commit,
                    purpose=purpose,
                    artifact_origin=artifact_origin,
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
        adapter: RawFirstAdapter[RequestT, ParsedT],
        request: RequestT,
        resource: SourceResource,
        import_id: UUID,
        git_commit: str | None,
        purpose: IngestPurpose = IngestPurpose.UNSPECIFIED,
        artifact_origin: ArtifactOrigin = ArtifactOrigin.OFFICIAL_FETCH,
    ) -> ResourceImportResult:
        scope = self._source_scope(adapter, request, resource)
        fingerprint = _fingerprint(
            {
                "dataset_code": adapter.dataset_code,
                "source": adapter.source,
                "adapter_version": adapter.version,
                "source_semantics": self._source_semantics(adapter),
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
                    self._fail_manifest(connection, import_id, f"fetch failed: {error}")
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
                    purpose=purpose,
                    artifact_origin=artifact_origin,
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
                f"{resource.resource_key} quarantined as {error.reason_code}: {error}"
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
            dependencies = self._capture_dependencies(
                adapter=adapter,
                request=request,
                parsed=parsed,
                import_id=import_id,
                purpose=purpose,
                artifact_origin=artifact_origin,
            )
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
                f"{resource.resource_key} quarantined as {error.reason_code}: {error}"
            ) from error
        except Exception as error:
            with self._engine.begin() as connection:
                self._record_operational_failure(
                    connection,
                    import_id=import_id,
                    resource_key=resource.resource_key,
                    run_id=run_id,
                    artifact_id=artifact_id,
                    reason_code="dependency_operational_error",
                    detail=str(error),
                )
            raise

        try:
            with self._engine.begin() as connection:
                outcome = self._write_business(
                    connection,
                    adapter=adapter,
                    request=request,
                    parsed=parsed,
                    lineage=LineageRef(artifact_id, run_id),
                    context=self._evidence_context(
                        connection, artifact_id, run_id
                    ),
                    dependencies=dependencies,
                )
                return self._complete_resource(
                    connection,
                    import_id=import_id,
                    adapter=adapter,
                    resource_key=resource.resource_key,
                    run_id=run_id,
                    artifact_id=artifact_id,
                    artifact_hash=artifact_hash,
                    artifact_created=artifact_created,
                    outcome=outcome,
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
                logger.exception("could not record writer operational failure")
            raise

    @abstractmethod
    def _source_scope(
        self,
        adapter: RawFirstAdapter[RequestT, ParsedT],
        request: RequestT,
        resource: SourceResource,
    ) -> Mapping[str, object]: ...

    @abstractmethod
    def _source_semantics(
        self, adapter: RawFirstAdapter[RequestT, ParsedT]
    ) -> Mapping[str, object]: ...

    @abstractmethod
    def _dataset_description(
        self, adapter: RawFirstAdapter[RequestT, ParsedT]
    ) -> str: ...

    def _capture_dependencies(
        self,
        *,
        adapter: RawFirstAdapter[RequestT, ParsedT],
        request: RequestT,
        parsed: ParsedT,
        import_id: UUID,
        purpose: IngestPurpose,
        artifact_origin: ArtifactOrigin,
    ) -> object | None:
        """Fetch and parse whatever else `_write_business` needs beyond the
        primary resource — a TWSE range file's rows that publish their terms
        only on a per-event detail page, for instance — before the write
        transaction opens rather than during it.

        Default: none. A subclass that overrides this may call
        `self._capture_and_parse` per auxiliary resource, which shares the
        primary resource's raw-first capture, checkpoint/resume and
        quarantine handling under the same `import_id`.
        """
        return None

    def _capture_and_parse(
        self,
        adapter: RawFirstAdapter,
        request: object,
        *,
        import_id: UUID,
        purpose: IngestPurpose,
        artifact_origin: ArtifactOrigin,
    ) -> tuple[object, bool, UUID, UUID, str]:
        """Capture and parse one resource under `import_id`, resuming a prior
        checkpoint when one exists. For use from `_capture_dependencies` only:
        it opens its own connections and must not be called from inside an
        already-open write transaction.

        A dependency resource has no business write of its own to mark a
        checkpoint `succeeded` — only the primary resource's checkpoint
        reaches that status — so this only ever looks for `captured`.

        Returns `(parsed, fetched, run_id, artifact_id, resource_key)`:
        `fetched` is true only when this call made a real request, so a
        caller throttling many of these — a backfill's per-row detail pages
        — waits between requests the source actually felt, not between
        resumed checkpoint reads. `run_id`/`artifact_id`/`resource_key` let a
        caller that tolerates one failed dependency (ADR-0022 §8) record its
        own quarantine row referencing this resource's real provenance
        instead of the primary resource's. On failure the same three are
        attached to the raised `SourceDataError` as `run_id`/`artifact_id`/
        `dependency_resource_key`. Content that is not a source answer at all
        (`invalid_json`) is retried once live; if it still is not, this raises
        `UnusableSourceResponseError` instead, which fails the whole range
        resumably rather than quarantining one row.
        """
        resource = adapter.resource(request)
        retried_once = False
        while True:
            with self._engine.begin() as connection:
                captured = self._captured_checkpoint(
                    connection, import_id, resource.resource_key
                )
            if captured is not None:
                content = self._raw_store.read(
                    storage_uri=captured.storage_uri,
                    expected_digest=captured.artifact_hash,
                    expected_byte_size=captured.byte_size,
                )
                fetched_now = False
                run_id = captured.run_id
                artifact_id = captured.artifact_id
            else:
                fetched = self._fetcher.fetch(resource)
                stored = self._raw_store.put(fetched.content)
                with self._engine.begin() as connection:
                    run_id, artifact_id, _ = self._capture_raw(
                        connection,
                        import_id=import_id,
                        adapter=adapter,
                        resource_key=resource.resource_key,
                        stored=stored,
                        source_uri=fetched.source_uri,
                        fetched_at=fetched.fetched_at,
                        media_type=fetched.media_type,
                        purpose=purpose,
                        artifact_origin=artifact_origin,
                    )
                content = fetched.content
                fetched_now = True

            try:
                parsed = adapter.parse(content, request)
            except SourceDataError as error:
                error.run_id = run_id  # type: ignore[attr-defined]
                error.artifact_id = artifact_id  # type: ignore[attr-defined]
                error.dependency_resource_key = resource.resource_key  # type: ignore[attr-defined]
                if error.reason_code == "invalid_json":
                    # Content that is not even JSON is not a source answer
                    # to cache and replay — TWSE has served an HTML
                    # "網站維護中" maintenance page here live (Step 19-d,
                    # 2026-09-17). A domain fact like `no_data_for_date`
                    # stays captured forever on purpose; this is the one
                    # shape that means the capture itself was never a real
                    # response, so undo it.
                    with self._engine.begin() as connection:
                        self._discard_unusable_capture(
                            connection, import_id=import_id,
                            resource_key=resource.resource_key,
                        )
                    if not retried_once:
                        # Discarding the checkpoint alone only helps a
                        # later attempt under this same import_id — and a
                        # dependency failure this tolerant of (ADR-0022 §8)
                        # lets the primary resource finish `succeeded`,
                        # whose completed checkpoint then short-circuits
                        # every later run before it ever looks at this row
                        # again. One immediate live retry actually gets the
                        # self-healing the checkpoint discard was meant to
                        # provide, instead of requiring an operator to
                        # notice and start a fresh import_id by hand.
                        retried_once = True
                        continue
                    # Still not a real answer. Quarantining just this row
                    # would let the range finish `succeeded` and lose the
                    # row for good under this import_id (the completed
                    # checkpoint short-circuits every rerun). Fail the range
                    # operationally instead: its own checkpoint stays
                    # `captured`, the other rows' detail checkpoints replay,
                    # and a rerun under the same import_id fetches this one
                    # again (ADR-0022 §10).
                    raise UnusableSourceResponseError(
                        error.reason_code, resource.resource_key, str(error)
                    ) from error
                raise
            return parsed, fetched_now, run_id, artifact_id, resource.resource_key

    @abstractmethod
    def _write_business(
        self,
        connection: Connection,
        *,
        adapter: RawFirstAdapter[RequestT, ParsedT],
        request: RequestT,
        parsed: ParsedT,
        lineage: LineageRef,
        context: EvidenceContext,
        dependencies: object | None = None,
    ) -> BusinessWriteResult: ...

    @staticmethod
    def _evidence_context(
        connection: Connection, artifact_id: UUID, run_id: UUID
    ) -> EvidenceContext:
        """What the run that fetched these bytes declared, and when.

        Both are read back from that run rather than taken from this call. A
        resumed import reuses the original artifact and its fetch instant, so
        trusting the current call's purpose would let a run that declared
        `gap_fill`, captured, and died be rerun as `first_capture` and claim a
        capture bound at the earlier instant — laundering exactly the claim
        ADR-0020 §5 refuses to infer.
        """
        row = connection.execute(
            sa.select(
                raw_artifact_observations.c.fetched_at, ingest_runs.c.purpose
            )
            .select_from(
                raw_artifact_observations.join(
                    ingest_runs,
                    ingest_runs.c.id == raw_artifact_observations.c.ingest_run_id,
                )
            )
            .where(
                raw_artifact_observations.c.raw_artifact_id == artifact_id,
                raw_artifact_observations.c.ingest_run_id == run_id,
            )
        ).mappings().one()
        return EvidenceContext(
            purpose=IngestPurpose(row["purpose"]),
            captured_at=row["fetched_at"],
        )

    @staticmethod
    def manifest(connection: Connection, import_id: UUID) -> ImportManifestResult:
        row = (
            connection.execute(
                sa.select(import_manifests).where(
                    import_manifests.c.import_id == import_id
                )
            )
            .mappings()
            .one()
        )
        return ImportManifestResult(
            import_id=str(import_id),
            status=row["status"],
            result_counts=row["result_counts"],
            reconciliation=row["reconciliation"],
        )

    def _ensure_source(
        self,
        connection: Connection,
        adapter: RawFirstAdapter[RequestT, ParsedT],
    ) -> None:
        connection.execute(
            insert(dataset_catalog)
            .values(
                dataset_code=adapter.dataset_code,
                description=self._dataset_description(adapter),
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
        source = (
            connection.execute(
                sa.select(dataset_sources).where(
                    dataset_sources.c.dataset_code == adapter.dataset_code,
                    dataset_sources.c.source == adapter.source,
                )
            )
            .mappings()
            .one()
        )
        if (
            not source["supports_system_pit"]
            or source["evidence_status"] != "verified"
            or "official" not in source["accepted_evidence_types"]
        ):
            raise RuntimeError(
                f"{adapter.source} {adapter.dataset_code} source policy is incompatible"
            )

    @staticmethod
    def _start_or_resume_manifest(
        connection: Connection,
        *,
        import_id: UUID,
        adapter: RawFirstAdapter[RequestT, ParsedT],
        git_commit: str,
        scope: Mapping[str, object],
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
                source_scope=dict(scope),
                configuration_fingerprint=fingerprint,
                status="running",
            )
            .on_conflict_do_nothing(index_elements=[import_manifests.c.import_id])
            .returning(import_manifests.c.import_id)
        ).scalar_one_or_none()
        if inserted is not None:
            return
        existing = (
            connection.execute(
                sa.select(import_manifests).where(
                    import_manifests.c.import_id == import_id
                )
            )
            .mappings()
            .one()
        )
        expected = {
            "dataset_code": adapter.dataset_code,
            "source": adapter.source,
            "adapter_version": adapter.version,
            "git_commit": git_commit,
            "source_scope": dict(scope),
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
        row = (
            connection.execute(
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
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if row["run_status"] != "succeeded":
            raise RuntimeError(
                "succeeded checkpoint must reference a succeeded ingest run"
            )
        manifest = (
            connection.execute(
                sa.select(
                    import_manifests.c.result_counts,
                    import_manifests.c.reconciliation,
                ).where(import_manifests.c.import_id == import_id)
            )
            .mappings()
            .one()
        )
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
            publication_evidence_deduplicated=counts.get("evidence_dedup_count", 0),
            evidence_observations=counts.get("evidence_observation_count", 0),
            unknown_publication_observations=counts.get("unknown_publication_count", 0),
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
        row = (
            connection.execute(
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
            )
            .mappings()
            .one_or_none()
        )
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
        adapter: RawFirstAdapter[RequestT, ParsedT],
        resource_key: str,
        stored: StoredRawArtifact,
        source_uri: str,
        fetched_at: datetime,
        media_type: str,
        purpose: IngestPurpose,
        artifact_origin: ArtifactOrigin,
    ) -> tuple[UUID, UUID, bool]:
        run_id = connection.execute(
            insert(ingest_runs)
            .values(
                dataset_code=adapter.dataset_code,
                source=adapter.source,
                status="running",
                started_at=sa.func.statement_timestamp(),
                purpose=purpose.value,
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
            .on_conflict_do_nothing(index_elements=[raw_artifacts.c.raw_artifact_hash])
            .returning(raw_artifacts.c.id)
        ).scalar_one_or_none()
        if inserted_artifact is None:
            artifact = (
                connection.execute(
                    sa.select(raw_artifacts).where(
                        raw_artifacts.c.raw_artifact_hash == stored.digest
                    )
                )
                .mappings()
                .one()
            )
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
                artifact_origin=artifact_origin.value,
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

    @staticmethod
    def _discard_unusable_capture(
        connection: Connection, *, import_id: UUID, resource_key: str
    ) -> None:
        """Drop a dependency's checkpoint so the next attempt fetches again.

        The raw artifact itself is left alone — untouched, content-addressed
        evidence that this response was seen (CLAUDE.md §28) — only the
        pointer that says "this resource is done, do not re-fetch" is
        removed. Scoped to `_capture_and_parse`'s dependency resources only:
        a primary resource's own quarantine already lets a rerun fetch fresh
        (its checkpoint moves to `quarantined`, which neither
        `_captured_checkpoint` nor `_completed_checkpoint` matches).
        """
        connection.execute(
            import_checkpoints.delete().where(
                import_checkpoints.c.import_id == import_id,
                import_checkpoints.c.resource_key == resource_key,
            )
        )

    @staticmethod
    def _complete_resource(
        connection: Connection,
        *,
        import_id: UUID,
        adapter: RawFirstAdapter[RequestT, ParsedT],
        resource_key: str,
        run_id: UUID,
        artifact_id: UUID,
        artifact_hash: str,
        artifact_created: bool,
        outcome: BusinessWriteResult,
    ) -> ResourceImportResult:
        raw_stats = (
            connection.execute(
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
            )
            .mappings()
            .one()
        )
        prior_quarantine = list(
            connection.scalars(
                sa.select(import_quarantine.c.reason_detail)
                .where(import_quarantine.c.import_id == import_id)
                .order_by(import_quarantine.c.id)
            )
        )
        warnings = [*prior_quarantine, *outcome.warnings]
        counts = {
            "requested_resource_count": 1,
            "completed_resource_count": 1,
            "raw_artifact_count": raw_stats["artifact_count"],
            "raw_observation_count": raw_stats["observation_count"],
            "business_version_count": outcome.business_versions_created,
            "dedup_count": outcome.business_versions_deduplicated,
            "evidence_count": outcome.publication_evidence_created,
            "evidence_dedup_count": outcome.publication_evidence_deduplicated,
            "evidence_observation_count": outcome.evidence_observations,
            "unknown_publication_count": (outcome.unknown_publication_observations),
            "normalized_row_count": outcome.normalized_rows,
            "rejected_quarantined_count": len(prior_quarantine),
        }
        reconciliation = {
            "source": adapter.source,
            "adapter_version": adapter.version,
            "resource_key": resource_key,
            "raw_artifact_hash": artifact_hash,
            **dict(outcome.reconciliation),
            "warnings_anomalies": warnings,
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
        manifest_update = connection.execute(
            import_manifests.update()
            .where(
                import_manifests.c.import_id == import_id,
                import_manifests.c.status == "running",
            )
            .values(
                status="succeeded",
                completed_at=sa.func.statement_timestamp(),
                result_counts=counts,
                reconciliation=reconciliation,
                warnings=warnings,
            )
        )
        if manifest_update.rowcount != 1:
            raise RuntimeError("running import manifest changed before commit")
        return ResourceImportResult(
            resource_key=resource_key,
            source=adapter.source,
            raw_artifact_hash=artifact_hash,
            raw_artifact_created=artifact_created,
            business_versions_created=outcome.business_versions_created,
            business_versions_deduplicated=outcome.business_versions_deduplicated,
            publication_evidence_created=outcome.publication_evidence_created,
            publication_evidence_deduplicated=(
                outcome.publication_evidence_deduplicated
            ),
            evidence_observations=outcome.evidence_observations,
            unknown_publication_observations=(outcome.unknown_publication_observations),
            normalized_rows=outcome.normalized_rows,
            coverage_start=outcome.coverage_start,
            coverage_end=outcome.coverage_end,
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
            raise RuntimeError(
                "captured checkpoint ownership changed before quarantine"
            )
        connection.execute(
            import_manifests.update()
            .where(import_manifests.c.import_id == import_id)
            .values(
                status="failed",
                completed_at=sa.func.statement_timestamp(),
                result_counts=_failure_counts(
                    raw_artifact_count=1, quarantined_count=1
                ),
                reconciliation=_failure_reconciliation(safe_detail),
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
                result_counts=_failure_counts(
                    raw_artifact_count=1, quarantined_count=0
                ),
                reconciliation=_failure_reconciliation(safe_detail),
                warnings=[safe_detail],
            )
        )

    @staticmethod
    def _fail_manifest(connection: Connection, import_id: UUID, detail: str) -> None:
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
                reconciliation=_failure_reconciliation(detail),
                warnings=[detail],
            )
        )


def _failure_counts(
    *, raw_artifact_count: int, quarantined_count: int
) -> dict[str, int]:
    return {
        "requested_resource_count": 1,
        "completed_resource_count": 0,
        "raw_artifact_count": raw_artifact_count,
        "raw_observation_count": raw_artifact_count,
        "business_version_count": 0,
        "dedup_count": 0,
        "evidence_count": 0,
        "evidence_dedup_count": 0,
        "evidence_observation_count": 0,
        "unknown_publication_count": 0,
        "normalized_row_count": 0,
        "rejected_quarantined_count": quarantined_count,
    }


def _failure_reconciliation(detail: str) -> dict[str, object]:
    return {
        "coverage_start": None,
        "coverage_end": None,
        "coverage_validation": "not_evaluated",
        "coverage_gaps": None,
        "warnings_anomalies": [detail],
    }


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
