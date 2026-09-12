from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.ingestion import (
    DailyMarketImporter,
    DailyMarketRequest,
    FetchedArtifact,
    LocalRawArtifactStore,
    RawArtifactIntegrityError,
    ResourceQuarantinedError,
)
from stock_data_center.ingestion.adapters import (
    TPExDailyMarketAdapter,
    TWSEDailyMarketAdapter,
)
from stock_data_center.ingestion.http import HttpSourceFetcher
from stock_data_center.market_data import MarketDataService, MarketDataWriter
from stock_data_center.pit import MarketPITContext, SystemPITContext

pytestmark = pytest.mark.integration


def _twse_payload() -> bytes:
    return json.dumps(
        {
            "stat": "OK",
            "date": "20250901",
            "title": "114年09月 2330 台積電 各日成交資訊",
            "fields": list(TWSEDailyMarketAdapter.fields),
            "data": [
                [
                    "114/09/01",
                    "23,022,319",
                    "26,647,241,918",
                    "1,150.00",
                    "1,165.00",
                    "1,145.00",
                    "1,165.00",
                    "+5.00",
                    "41,416",
                    "",
                ]
            ],
        },
        ensure_ascii=False,
    ).encode()


def _tpex_payload(*, valid_fields: bool = True) -> bytes:
    fields = list(TPExDailyMarketAdapter.fields)
    if not valid_fields:
        fields[1] = "成交數量"
    return json.dumps(
        {
            "date": "20250901",
            "code": "6488",
            "name": "環球晶",
            "stat": "ok",
            "tables": [
                {
                    "fields": fields,
                    "data": [
                        [
                            "114/09/01",
                            "1,666",
                            "611,795",
                            "371.50",
                            "374.00",
                            "364.00",
                            "364.50",
                            "-7.50",
                            "3,198",
                        ]
                    ],
                }
            ],
        },
        ensure_ascii=False,
    ).encode()


class StaticFetcher:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = 0

    def fetch(self, resource):
        self.calls += 1
        return FetchedArtifact(
            content=self.content,
            source_uri=resource.source_uri,
            fetched_at=datetime.now(UTC),
            media_type="application/json",
        )


class SimulatedProcessCrash(BaseException):
    pass


class CrashAfterCaptureAdapter(TPExDailyMarketAdapter):
    def parse(self, content, request):
        raise SimulatedProcessCrash


class UnexpectedAdapterFailure(TPExDailyMarketAdapter):
    def parse(self, content, request):
        raise RuntimeError("simulated adapter bug")


class UnexpectedWriterFailure(MarketDataWriter):
    def append_daily_price(self, *args, **kwargs):
        raise RuntimeError("simulated writer bug")


class BlockingFetcher(StaticFetcher):
    def __init__(self, content: bytes, entered: Event, release: Event) -> None:
        super().__init__(content)
        self.entered = entered
        self.release = release

    def fetch(self, resource):
        self.entered.set()
        assert self.release.wait(timeout=10)
        return super().fetch(resource)


class FetchMustNotRun:
    def fetch(self, resource):
        raise AssertionError("captured checkpoint must not re-fetch the source")


def test_real_import_framework_is_raw_first_pit_safe_and_restartable(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        request = DailyMarketRequest("6488", date(2025, 9, 1))
        fetcher = StaticFetcher(_tpex_payload())
        importer = DailyMarketImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=fetcher,
        )
        first_import_id = uuid4()
        first = importer.run(
            adapter=TPExDailyMarketAdapter(),
            request=request,
            import_id=first_import_id,
            git_commit="test-commit",
        )

        assert first.business_versions_created == 1
        assert first.publication_evidence_created == 1
        assert first.unknown_publication_observations == 1
        assert fetcher.calls == 1
        with engine.connect() as connection:
            partial_manifest = DailyMarketImporter.manifest(
                connection, first_import_id
            )
            assert partial_manifest.reconciliation["coverage_validation"] == (
                "not_evaluated"
            )
            assert partial_manifest.reconciliation["coverage_gaps"] is None

        resumed = importer.run(
            adapter=TPExDailyMarketAdapter(),
            request=request,
            import_id=first_import_id,
            git_commit="test-commit",
        )
        assert resumed.resumed_from_checkpoint is True
        assert fetcher.calls == 1
        with pytest.raises(ValueError, match="changed configuration"):
            importer.run(
                adapter=TPExDailyMarketAdapter(),
                request=DailyMarketRequest("6488", date(2025, 10, 1)),
                import_id=first_import_id,
                git_commit="test-commit",
            )
        assert fetcher.calls == 1

        repeated = importer.run(
            adapter=TPExDailyMarketAdapter(),
            request=request,
            import_id=uuid4(),
            git_commit="test-commit",
        )
        assert repeated.business_versions_created == 0
        assert repeated.business_versions_deduplicated == 1
        assert repeated.publication_evidence_created == 0
        assert repeated.publication_evidence_deduplicated == 1
        assert fetcher.calls == 2

        with engine.connect() as connection:
            service = MarketDataService()
            system = service.daily_price(
                connection,
                security_code="6488",
                trade_date=date(2025, 9, 1),
                context=SystemPITContext(datetime.now(UTC) + timedelta(minutes=1)),
                source="tpex",
            )
            market = service.daily_price(
                connection,
                security_code="6488",
                trade_date=date(2025, 9, 1),
                context=MarketPITContext(
                    information_as_of=datetime.now(UTC) + timedelta(minutes=1),
                    knowledge_as_of=datetime.now(UTC) + timedelta(minutes=1),
                ),
                source="tpex",
            )
            assert system is not None
            assert system.data["volume"] == 1_666_000
            assert system.data["trade_value"] == 611_795_000
            assert system.provenance.ingested_at.date() == datetime.now(UTC).date()
            assert market is None
            assert connection.scalar(
                sa.text("SELECT count(*) FROM daily_price_versions")
            ) == 1
            assert connection.scalar(
                sa.text("SELECT count(*) FROM raw_artifact_observations")
            ) == 2

        invalid_fetcher = StaticFetcher(_tpex_payload(valid_fields=False))
        invalid_importer = DailyMarketImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "invalid-raw"),
            fetcher=invalid_fetcher,
        )
        invalid_import_id = uuid4()
        with pytest.raises(ResourceQuarantinedError, match="schema_mismatch"):
            invalid_importer.run(
                adapter=TPExDailyMarketAdapter(),
                request=request,
                import_id=invalid_import_id,
                git_commit="test-commit",
            )
        with engine.connect() as connection:
            quarantine = connection.execute(
                sa.text(
                    "SELECT reason_code, reason_detail FROM import_quarantine"
                )
            ).mappings().one()
            assert quarantine["reason_code"] == "schema_mismatch"
            assert "fields changed" in quarantine["reason_detail"]
            assert connection.scalar(
                sa.text("SELECT count(*) FROM raw_artifacts")
            ) == 2
            quarantine_id = connection.scalar(
                sa.text("SELECT id FROM import_quarantine")
            )
            with (
                pytest.raises(sa.exc.DBAPIError, match="append-only"),
                connection.begin_nested(),
            ):
                connection.execute(
                    sa.text(
                        "UPDATE import_quarantine SET reason_detail='changed' "
                        "WHERE id=:id"
                    ),
                    {"id": quarantine_id},
                )

        retry_fetcher = StaticFetcher(_tpex_payload())
        retry_importer = DailyMarketImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "invalid-raw"),
            fetcher=retry_fetcher,
        )
        retried = retry_importer.run(
            adapter=TPExDailyMarketAdapter(),
            request=request,
            import_id=invalid_import_id,
            git_commit="test-commit",
        )
        assert retried.business_versions_deduplicated == 1
        with engine.connect() as connection:
            manifest = DailyMarketImporter.manifest(connection, invalid_import_id)
            checkpoint = connection.execute(
                sa.text(
                    "SELECT status, attempt_count FROM import_checkpoints "
                    "WHERE import_id=:import_id"
                ),
                {"import_id": invalid_import_id},
            ).mappings().one()
            assert manifest.status == "succeeded"
            assert manifest.result_counts["raw_artifact_count"] == 2
            assert manifest.result_counts["raw_observation_count"] == 2
            assert manifest.result_counts["rejected_quarantined_count"] == 1
            assert checkpoint == {"status": "succeeded", "attempt_count": 2}
    finally:
        engine.dispose()


def test_captured_checkpoint_resumes_original_raw_and_run_without_refetch(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    request = DailyMarketRequest("6488", date(2025, 9, 1))
    import_id = uuid4()
    raw_store = LocalRawArtifactStore(tmp_path / "captured-raw")
    initial_fetcher = StaticFetcher(_tpex_payload())
    try:
        crashing_importer = DailyMarketImporter(
            engine,
            raw_store=raw_store,
            fetcher=initial_fetcher,
        )
        with pytest.raises(SimulatedProcessCrash):
            crashing_importer.run(
                adapter=CrashAfterCaptureAdapter(),
                request=request,
                import_id=import_id,
                git_commit="test-commit",
            )
        assert initial_fetcher.calls == 1

        with engine.connect() as connection:
            captured = connection.execute(
                sa.text(
                    """
                    SELECT c.status, c.last_ingest_run_id, c.last_raw_artifact_id,
                           a.raw_artifact_hash, r.status AS run_status
                    FROM import_checkpoints c
                    JOIN raw_artifacts a ON a.id = c.last_raw_artifact_id
                    JOIN ingest_runs r ON r.id = c.last_ingest_run_id
                    WHERE c.import_id = :import_id
                    """
                ),
                {"import_id": import_id},
            ).mappings().one()
            assert captured["status"] == "captured"
            assert captured["run_status"] == "running"

        resumed = DailyMarketImporter(
            engine,
            raw_store=raw_store,
            fetcher=FetchMustNotRun(),
        ).run(
            adapter=TPExDailyMarketAdapter(),
            request=request,
            import_id=import_id,
            git_commit="test-commit",
        )

        assert resumed.raw_artifact_hash == captured["raw_artifact_hash"]
        assert resumed.raw_artifact_created is False
        assert resumed.business_versions_created == 1
        with engine.connect() as connection:
            audit = connection.execute(
                sa.text(
                    """
                    SELECT c.status, c.attempt_count, c.last_ingest_run_id,
                           v.ingest_run_id AS version_run_id,
                           v.raw_artifact_id AS version_artifact_id,
                           r.status AS run_status
                    FROM import_checkpoints c
                    JOIN ingest_runs r ON r.id = c.last_ingest_run_id
                    JOIN daily_price_versions v
                      ON v.ingest_run_id = c.last_ingest_run_id
                    WHERE c.import_id = :import_id
                    """
                ),
                {"import_id": import_id},
            ).mappings().one()
            assert audit["status"] == "succeeded"
            assert audit["attempt_count"] == 1
            assert audit["last_ingest_run_id"] == captured["last_ingest_run_id"]
            assert audit["version_run_id"] == captured["last_ingest_run_id"]
            assert audit["version_artifact_id"] == captured["last_raw_artifact_id"]
            assert audit["run_status"] == "succeeded"
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM ingest_runs "
                    "WHERE run_metadata->>'import_id'=:import_id"
                ),
                {"import_id": str(import_id)},
            ) == 1
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM ingest_runs "
                    "WHERE run_metadata->>'import_id'=:import_id "
                    "AND status='running'"
                ),
                {"import_id": str(import_id)},
            ) == 0
            assert connection.scalar(
                sa.text("SELECT count(*) FROM daily_price_versions")
            ) == 1
    finally:
        engine.dispose()


def test_captured_checkpoint_retains_corrupt_raw_as_operational_failure(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    request = DailyMarketRequest("6488", date(2025, 9, 1))
    import_id = uuid4()
    raw_store = LocalRawArtifactStore(tmp_path / "corrupt-raw")
    try:
        with pytest.raises(SimulatedProcessCrash):
            DailyMarketImporter(
                engine,
                raw_store=raw_store,
                fetcher=StaticFetcher(_tpex_payload()),
            ).run(
                adapter=CrashAfterCaptureAdapter(),
                request=request,
                import_id=import_id,
                git_commit="test-commit",
            )
        with engine.connect() as connection:
            storage_uri = connection.scalar(
                sa.text(
                    """
                    SELECT a.storage_uri
                    FROM import_checkpoints c
                    JOIN raw_artifacts a ON a.id=c.last_raw_artifact_id
                    WHERE c.import_id=:import_id
                    """
                ),
                {"import_id": import_id},
            )
        retained_path = Path(storage_uri)
        original = retained_path.read_bytes()
        retained_path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))

        with pytest.raises(RawArtifactIntegrityError, match="SHA-256"):
            DailyMarketImporter(
                engine,
                raw_store=raw_store,
                fetcher=FetchMustNotRun(),
            ).run(
                adapter=TPExDailyMarketAdapter(),
                request=request,
                import_id=import_id,
                git_commit="test-commit",
            )

        with engine.connect() as connection:
            state = connection.execute(
                sa.text(
                    """
                    SELECT c.status, c.error_code, r.status AS run_status
                    FROM import_checkpoints c
                    JOIN ingest_runs r ON r.id=c.last_ingest_run_id
                    WHERE c.import_id=:import_id
                    """
                ),
                {"import_id": import_id},
            ).mappings().one()
            assert state == {
                "status": "captured",
                "error_code": "raw_artifact_integrity",
                "run_status": "running",
            }
            assert connection.scalar(
                sa.text("SELECT count(*) FROM import_quarantine")
            ) == 0
            assert connection.scalar(
                sa.text("SELECT count(*) FROM daily_price_versions")
            ) == 0
    finally:
        engine.dispose()


def test_unexpected_adapter_failure_keeps_original_raw_resumable(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    request = DailyMarketRequest("6488", date(2025, 9, 1))
    import_id = uuid4()
    raw_store = LocalRawArtifactStore(tmp_path / "operational-raw")
    try:
        with pytest.raises(RuntimeError, match="simulated adapter bug"):
            DailyMarketImporter(
                engine,
                raw_store=raw_store,
                fetcher=StaticFetcher(_tpex_payload()),
            ).run(
                adapter=UnexpectedAdapterFailure(),
                request=request,
                import_id=import_id,
                git_commit="test-commit",
            )

        with engine.connect() as connection:
            failed_state = connection.execute(
                sa.text(
                    """
                    SELECT c.status, c.error_code, c.last_ingest_run_id,
                           c.last_raw_artifact_id, r.status AS run_status
                    FROM import_checkpoints c
                    JOIN ingest_runs r ON r.id=c.last_ingest_run_id
                    WHERE c.import_id=:import_id
                    """
                ),
                {"import_id": import_id},
            ).mappings().one()
            assert failed_state["status"] == "captured"
            assert failed_state["error_code"] == "adapter_operational_error"
            assert failed_state["run_status"] == "running"
            assert connection.scalar(
                sa.text("SELECT count(*) FROM import_quarantine")
            ) == 0

        resumed = DailyMarketImporter(
            engine,
            raw_store=raw_store,
            fetcher=FetchMustNotRun(),
        ).run(
            adapter=TPExDailyMarketAdapter(),
            request=request,
            import_id=import_id,
            git_commit="test-commit",
        )
        assert resumed.business_versions_created == 1
        with engine.connect() as connection:
            resumed_state = connection.execute(
                sa.text(
                    """
                    SELECT c.status, c.last_ingest_run_id, c.last_raw_artifact_id,
                           r.status AS run_status
                    FROM import_checkpoints c
                    JOIN ingest_runs r ON r.id=c.last_ingest_run_id
                    WHERE c.import_id=:import_id
                    """
                ),
                {"import_id": import_id},
            ).mappings().one()
            assert resumed_state == {
                "status": "succeeded",
                "last_ingest_run_id": failed_state["last_ingest_run_id"],
                "last_raw_artifact_id": failed_state["last_raw_artifact_id"],
                "run_status": "succeeded",
            }
    finally:
        engine.dispose()


def test_raw_store_configuration_is_part_of_import_fingerprint(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    request = DailyMarketRequest("6488", date(2025, 9, 1))
    import_id = uuid4()
    try:
        with pytest.raises(SimulatedProcessCrash):
            DailyMarketImporter(
                engine,
                raw_store=LocalRawArtifactStore(tmp_path / "raw-a"),
                fetcher=StaticFetcher(_tpex_payload()),
            ).run(
                adapter=CrashAfterCaptureAdapter(),
                request=request,
                import_id=import_id,
                git_commit="test-commit",
            )

        with pytest.raises(ValueError, match="changed configuration"):
            DailyMarketImporter(
                engine,
                raw_store=LocalRawArtifactStore(tmp_path / "raw-b"),
                fetcher=FetchMustNotRun(),
            ).run(
                adapter=TPExDailyMarketAdapter(),
                request=request,
                import_id=import_id,
                git_commit="test-commit",
            )
    finally:
        engine.dispose()


def test_unexpected_writer_failure_keeps_original_raw_resumable(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    request = DailyMarketRequest("6488", date(2025, 9, 1))
    import_id = uuid4()
    raw_store = LocalRawArtifactStore(tmp_path / "writer-failure-raw")
    try:
        with pytest.raises(RuntimeError, match="simulated writer bug"):
            DailyMarketImporter(
                engine,
                raw_store=raw_store,
                fetcher=StaticFetcher(_tpex_payload()),
                writer=UnexpectedWriterFailure(),
            ).run(
                adapter=TPExDailyMarketAdapter(),
                request=request,
                import_id=import_id,
                git_commit="test-commit",
            )

        with engine.connect() as connection:
            failed_state = connection.execute(
                sa.text(
                    """
                    SELECT c.status, c.error_code, c.last_ingest_run_id,
                           c.last_raw_artifact_id, r.status AS run_status
                    FROM import_checkpoints c
                    JOIN ingest_runs r ON r.id=c.last_ingest_run_id
                    WHERE c.import_id=:import_id
                    """
                ),
                {"import_id": import_id},
            ).mappings().one()
            assert failed_state["status"] == "captured"
            assert failed_state["error_code"] == "writer_operational_error"
            assert failed_state["run_status"] == "running"
            assert connection.scalar(
                sa.text("SELECT count(*) FROM import_quarantine")
            ) == 0
            assert connection.scalar(
                sa.text("SELECT count(*) FROM daily_price_versions")
            ) == 0

        resumed = DailyMarketImporter(
            engine,
            raw_store=raw_store,
            fetcher=FetchMustNotRun(),
        ).run(
            adapter=TPExDailyMarketAdapter(),
            request=request,
            import_id=import_id,
            git_commit="test-commit",
        )
        assert resumed.business_versions_created == 1
        with engine.connect() as connection:
            succeeded = connection.execute(
                sa.text(
                    """
                    SELECT c.status, c.last_ingest_run_id, c.last_raw_artifact_id,
                           r.status AS run_status
                    FROM import_checkpoints c
                    JOIN ingest_runs r ON r.id=c.last_ingest_run_id
                    WHERE c.import_id=:import_id
                    """
                ),
                {"import_id": import_id},
            ).mappings().one()
            assert succeeded == {
                "status": "succeeded",
                "last_ingest_run_id": failed_state["last_ingest_run_id"],
                "last_raw_artifact_id": failed_state["last_raw_artifact_id"],
                "run_status": "succeeded",
            }
    finally:
        engine.dispose()


def test_same_import_resource_is_serialized_across_workers(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    request = DailyMarketRequest("6488", date(2025, 9, 1))
    import_id = uuid4()
    raw_store = LocalRawArtifactStore(tmp_path / "concurrent-raw")
    entered = Event()
    release = Event()
    first_fetcher = BlockingFetcher(_tpex_payload(), entered, release)
    second_fetcher = StaticFetcher(_tpex_payload())

    def run_first():
        return DailyMarketImporter(
            engine, raw_store=raw_store, fetcher=first_fetcher
        ).run(
            adapter=TPExDailyMarketAdapter(),
            request=request,
            import_id=import_id,
            git_commit="test-commit",
        )

    def run_second(started: Event):
        started.set()
        return DailyMarketImporter(
            engine, raw_store=raw_store, fetcher=second_fetcher
        ).run(
            adapter=TPExDailyMarketAdapter(),
            request=request,
            import_id=import_id,
            git_commit="test-commit",
        )

    try:
        second_started = Event()
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(run_first)
            assert entered.wait(timeout=10)
            second_future = executor.submit(run_second, second_started)
            assert second_started.wait(timeout=10)
            deadline = time.monotonic() + 10
            waiting_on_advisory_lock = False
            while time.monotonic() < deadline:
                with engine.connect() as connection:
                    waiting_on_advisory_lock = bool(
                        connection.scalar(
                            sa.text(
                                "SELECT count(*) FROM pg_locks "
                                "WHERE locktype='advisory' AND NOT granted"
                            )
                        )
                    )
                if waiting_on_advisory_lock:
                    break
                time.sleep(0.01)
            assert waiting_on_advisory_lock
            assert second_fetcher.calls == 0
            release.set()
            first = first_future.result(timeout=20)
            second = second_future.result(timeout=20)

        assert first.business_versions_created == 1
        assert second.resumed_from_checkpoint is True
        assert first_fetcher.calls == 1
        assert second_fetcher.calls == 0
        with engine.connect() as connection:
            audit = connection.execute(
                sa.text(
                    """
                    SELECT c.status, r.status AS run_status
                    FROM import_checkpoints c
                    JOIN ingest_runs r ON r.id=c.last_ingest_run_id
                    WHERE c.import_id=:import_id
                    """
                ),
                {"import_id": import_id},
            ).mappings().one()
            assert audit == {"status": "succeeded", "run_status": "succeeded"}
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM ingest_runs "
                    "WHERE run_metadata->>'import_id'=:import_id"
                ),
                {"import_id": str(import_id)},
            ) == 1
            assert connection.scalar(
                sa.text("SELECT count(*) FROM daily_price_versions")
            ) == 1

        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE ingest_runs SET status='running', completed_at=NULL "
                    "WHERE id=(SELECT last_ingest_run_id FROM import_checkpoints "
                    "WHERE import_id=:import_id)"
                ),
                {"import_id": import_id},
            )
        with pytest.raises(
            RuntimeError,
            match="succeeded checkpoint must reference a succeeded ingest run",
        ):
            DailyMarketImporter(
                engine,
                raw_store=raw_store,
                fetcher=FetchMustNotRun(),
            ).run(
                adapter=TPExDailyMarketAdapter(),
                request=request,
                import_id=import_id,
                git_commit="test-commit",
            )
    finally:
        release.set()
        engine.dispose()


def test_phase9_migration_adds_daily_price_sanity_constraints(
    isolated_database_url: str,
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        constraint_names = {
            item["name"]
            for item in sa.inspect(engine).get_check_constraints(
                "daily_price_versions"
            )
        }
        assert {
            "ck_daily_price_versions_prices_nonnegative",
            "ck_daily_price_versions_open_not_below_low",
            "ck_daily_price_versions_open_not_above_high",
            "ck_daily_price_versions_close_not_below_low",
            "ck_daily_price_versions_close_not_above_high",
        } <= constraint_names
    finally:
        engine.dispose()


@pytest.mark.live_source
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_SOURCE_TESTS") != "1",
    reason="set RUN_LIVE_SOURCE_TESTS=1 for official endpoint pilot",
)
def test_official_twse_and_tpex_resources_reach_postgres_and_pit(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        importer = DailyMarketImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "official-raw"),
            fetcher=HttpSourceFetcher(timeout_seconds=30),
        )
        cases = (
            (TWSEDailyMarketAdapter(), DailyMarketRequest("2330", date(2025, 9, 1))),
            (TPExDailyMarketAdapter(), DailyMarketRequest("6488", date(2025, 9, 1))),
        )
        for adapter, request in cases:
            result = importer.run(adapter=adapter, request=request)
            assert result.normalized_rows > 0
            assert result.coverage_start is not None
            with engine.connect() as connection:
                record = MarketDataService().daily_price(
                    connection,
                    security_code=request.security_code,
                    trade_date=result.coverage_start,
                    context=SystemPITContext(
                        datetime.now(UTC) + timedelta(minutes=1)
                    ),
                    source=adapter.source,
                )
                assert record is not None
                assert record.provenance.raw_artifact_hash == result.raw_artifact_hash
    finally:
        engine.dispose()
