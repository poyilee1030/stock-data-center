from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from conftest import alembic_config
from sqlalchemy.exc import DBAPIError

from stock_data_center.ingestion import (
    FetchedArtifact,
    LocalRawArtifactStore,
    ResourceQuarantinedError,
    SecurityMetadataImporter,
    SecurityMetadataRequest,
)
from stock_data_center.ingestion.adapters import (
    TPExSecurityMetadataAdapter,
    TWSESecurityMetadataAdapter,
)
from stock_data_center.ingestion.http import HttpSourceFetcher
from stock_data_center.market_data import MarketDataService, MarketDataWriter
from stock_data_center.pit import MarketPITContext, SystemPITContext

pytestmark = pytest.mark.integration


def _twse_payload(
    *, report_date: str = "1150911", name: str = "台灣積體電路製造股份有限公司"
) -> bytes:
    return json.dumps(
        [
            {
                "出表日期": report_date,
                "公司代號": "2330",
                "公司名稱": name,
                "產業別": "24",
                "上市日期": "19940905",
            },
            {
                "出表日期": report_date,
                "公司代號": "1101",
                "公司名稱": "臺灣水泥股份有限公司",
                "產業別": "01",
                "上市日期": "19620209",
            },
        ],
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


class CrashAfterCaptureAdapter(TWSESecurityMetadataAdapter):
    def parse(self, content, request):
        raise SimulatedProcessCrash


class UnexpectedAdapterFailure(TWSESecurityMetadataAdapter):
    def parse(self, content, request):
        raise RuntimeError("simulated security adapter bug")


class UnexpectedWriterFailure(MarketDataWriter):
    def append_security_metadata_snapshot(self, *args, **kwargs):
        raise RuntimeError("simulated security writer bug")


class FetchMustNotRun:
    def fetch(self, resource):
        raise AssertionError("captured checkpoint must not re-fetch the source")


def test_security_snapshot_is_raw_first_pit_safe_and_deduplicates_unchanged_state(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    service = MarketDataService()
    try:
        first = SecurityMetadataImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=StaticFetcher(_twse_payload()),
        ).run(
            adapter=TWSESecurityMetadataAdapter(),
            request=SecurityMetadataRequest(date(2026, 9, 11)),
            import_id=uuid4(),
            git_commit="test-commit",
        )
        assert first.business_versions_created == 2
        assert first.publication_evidence_created == 2
        assert first.unknown_publication_observations == 2

        with engine.connect() as connection:
            system = service.security_state(
                connection,
                security_code="2330",
                effective_on=date(2026, 9, 11),
                context=SystemPITContext(datetime.now(UTC) + timedelta(minutes=1)),
                source="twse",
            )
            market = service.security_state(
                connection,
                security_code="2330",
                effective_on=date(2026, 9, 11),
                context=MarketPITContext(
                    information_as_of=datetime.now(UTC) + timedelta(minutes=1),
                    knowledge_as_of=datetime.now(UTC) + timedelta(minutes=1),
                ),
                source="twse",
            )
            assert system is not None
            assert system.market == "TWSE"
            assert system.record.data["effective_from"] == date(2026, 9, 11)
            assert system.record.data["listed_on"] == date(1994, 9, 5)
            assert market is None

        unchanged_import_id = uuid4()
        unchanged = SecurityMetadataImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=StaticFetcher(_twse_payload(report_date="1150912")),
        ).run(
            adapter=TWSESecurityMetadataAdapter(),
            request=SecurityMetadataRequest(date(2026, 9, 12)),
            import_id=unchanged_import_id,
            git_commit="test-commit",
        )
        assert unchanged.business_versions_created == 0
        assert unchanged.business_versions_deduplicated == 2
        assert unchanged.publication_evidence_created == 0
        assert unchanged.publication_evidence_deduplicated == 2

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM security_metadata_versions")
                )
                == 2
            )
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM raw_artifact_observations")
                )
                == 2
            )
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM publication_evidence_observations")
                )
                == 4
            )
            reconciliation = connection.execute(
                sa.text(
                    "SELECT reconciliation FROM import_manifests "
                    "WHERE import_id=:import_id"
                ),
                {"import_id": unchanged_import_id},
            ).scalar_one()
            assert reconciliation["coverage_validation"] == "snapshot_only"
            assert reconciliation["historical_coverage"] == "not_evaluated"
            assert reconciliation["market_transfer_history"] == "not_in_scope"
    finally:
        engine.dispose()


def test_changed_snapshot_starts_a_new_observed_state_without_closing_omissions(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    service = MarketDataService()
    try:
        for payload, expected in (
            (_twse_payload(report_date="1150911"), date(2026, 9, 11)),
            (
                _twse_payload(report_date="1150912", name="台積電股份有限公司"),
                date(2026, 9, 12),
            ),
        ):
            SecurityMetadataImporter(
                engine,
                raw_store=LocalRawArtifactStore(tmp_path / "raw"),
                fetcher=StaticFetcher(payload),
            ).run(
                adapter=TWSESecurityMetadataAdapter(),
                request=SecurityMetadataRequest(expected),
                import_id=uuid4(),
                git_commit="test-commit",
            )

        omitted_payload = json.dumps(
            [
                {
                    "出表日期": "1150913",
                    "公司代號": "2330",
                    "公司名稱": "台積電股份有限公司",
                    "產業別": "24",
                    "上市日期": "19940905",
                }
            ],
            ensure_ascii=False,
        ).encode()
        SecurityMetadataImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=StaticFetcher(omitted_payload),
        ).run(
            adapter=TWSESecurityMetadataAdapter(),
            request=SecurityMetadataRequest(date(2026, 9, 13)),
            import_id=uuid4(),
            git_commit="test-commit",
        )

        with engine.connect() as connection:
            context = SystemPITContext(datetime.now(UTC) + timedelta(minutes=1))
            old = service.security_state(
                connection,
                security_code="2330",
                effective_on=date(2026, 9, 11),
                context=context,
                source="twse",
            )
            changed = service.security_state(
                connection,
                security_code="2330",
                effective_on=date(2026, 9, 12),
                context=context,
                source="twse",
            )
            assert old is not None
            assert old.record.data["name"] == "台灣積體電路製造股份有限公司"
            assert changed is not None
            assert changed.record.data["name"] == "台積電股份有限公司"
            omitted = service.security_state(
                connection,
                security_code="1101",
                effective_on=date(2026, 9, 13),
                context=context,
                source="twse",
            )
            assert omitted is not None
            assert omitted.record.data["delisted_on"] is None
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM security_metadata_versions")
                )
                == 3
            )
    finally:
        engine.dispose()


def test_same_report_date_reassertion_remains_visible_in_system_pit(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    service = MarketDataService()
    original_name = "台灣積體電路製造股份有限公司"
    corrected_name = "台積電股份有限公司"
    outcomes = []
    try:
        for name in (original_name, corrected_name, original_name, original_name):
            outcomes.append(
                SecurityMetadataImporter(
                    engine,
                    raw_store=LocalRawArtifactStore(tmp_path / "raw"),
                    fetcher=StaticFetcher(_twse_payload(name=name)),
                ).run(
                    adapter=TWSESecurityMetadataAdapter(),
                    request=SecurityMetadataRequest(date(2026, 9, 11)),
                    import_id=uuid4(),
                    git_commit="test-commit",
                )
            )

        assert [outcome.business_versions_created for outcome in outcomes] == [
            2,
            1,
            1,
            0,
        ]

        with engine.connect() as connection:
            versions = connection.execute(
                sa.text(
                    """
                    SELECT metadata.id, metadata.name,
                           metadata.business_content_hash, metadata.ingested_at,
                           metadata.predecessor_version_id
                    FROM security_metadata_versions AS metadata
                    JOIN security AS identity ON identity.id=metadata.security_id
                    WHERE identity.security_code='2330'
                      AND metadata.source='twse'
                      AND metadata.effective_from='2026-09-11'
                    ORDER BY metadata.ingested_at, metadata.id
                    """
                )
            ).mappings().all()

            assert [row["name"] for row in versions] == [
                original_name,
                corrected_name,
                original_name,
            ]
            assert versions[0]["business_content_hash"] == versions[2][
                "business_content_hash"
            ]
            assert [row["predecessor_version_id"] for row in versions] == [
                None,
                versions[0]["id"],
                versions[1]["id"],
            ]

            for version, expected_name in zip(
                versions,
                (original_name, corrected_name, original_name),
                strict=True,
            ):
                resolved = service.security_state(
                    connection,
                    security_code="2330",
                    effective_on=date(2026, 9, 11),
                    context=SystemPITContext(version["ingested_at"]),
                    source="twse",
                )
                assert resolved is not None
                assert resolved.record.data["name"] == expected_name
                assert "predecessor_version_id" not in resolved.record.data
    finally:
        engine.dispose()


def test_security_metadata_predecessor_integrity_is_db_enforced(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        SecurityMetadataImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=StaticFetcher(_twse_payload()),
        ).run(
            adapter=TWSESecurityMetadataAdapter(),
            request=SecurityMetadataRequest(date(2026, 9, 11)),
            import_id=uuid4(),
            git_commit="test-commit",
        )

        with engine.begin() as connection:
            rows = {
                row["security_code"]: row
                for row in connection.execute(
                    sa.text(
                        """
                        SELECT identity.security_code, identity.id AS security_id,
                               metadata.id AS version_id,
                               metadata.raw_artifact_id, metadata.ingest_run_id
                        FROM security_metadata_versions AS metadata
                        JOIN security AS identity ON identity.id=metadata.security_id
                        """
                    )
                ).mappings()
            }
            invalid_transitions = (
                (rows["2330"]["security_id"], date(2026, 9, 11), rows["1101"]["version_id"]),
                (rows["2330"]["security_id"], date(2026, 9, 10), rows["2330"]["version_id"]),
            )
            for security_id, effective_from, predecessor_id in invalid_transitions:
                with pytest.raises(sa.exc.IntegrityError) as invalid, connection.begin_nested():
                    connection.execute(
                        sa.text(
                            """
                            INSERT INTO security_metadata_versions (
                                security_id, source, effective_from, market, name,
                                business_content_hash, ingested_at, raw_artifact_id,
                                ingest_run_id, predecessor_version_id
                            ) VALUES (
                                :security_id, 'twse', :effective_from, 'TWSE',
                                'invalid transition', repeat('0', 64),
                                statement_timestamp(), :artifact_id, :run_id,
                                :predecessor_id
                            )
                            """
                        ),
                        {
                            "security_id": security_id,
                            "effective_from": effective_from,
                            "artifact_id": rows["2330"]["raw_artifact_id"],
                            "run_id": rows["2330"]["ingest_run_id"],
                            "predecessor_id": predecessor_id,
                        },
                    )
                assert invalid.value.orig.sqlstate == "23514"
    finally:
        engine.dispose()


def test_reassertion_history_blocks_unrepresentable_downgrade(
    isolated_database_url: str, tmp_path: Path
) -> None:
    config = alembic_config(isolated_database_url)
    command.downgrade(config, "8c1f7a4e2d90")
    command.upgrade(config, "head")
    engine = sa.create_engine(isolated_database_url)
    original_name = "台灣積體電路製造股份有限公司"
    try:
        for name in (original_name, "台積電股份有限公司", original_name):
            SecurityMetadataImporter(
                engine,
                raw_store=LocalRawArtifactStore(tmp_path / "raw"),
                fetcher=StaticFetcher(_twse_payload(name=name)),
            ).run(
                adapter=TWSESecurityMetadataAdapter(),
                request=SecurityMetadataRequest(date(2026, 9, 11)),
                import_id=uuid4(),
                git_commit="test-commit",
            )
        engine.dispose()

        with pytest.raises(
            DBAPIError,
            match="cannot downgrade security metadata transition history",
        ) as blocked:
            command.downgrade(config, "8c1f7a4e2d90")
        assert blocked.value.orig.sqlstate == "P0001"
        assert "do not delete or collapse append-only PIT history" in str(
            blocked.value
        )

        engine = sa.create_engine(isolated_database_url)
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                "9a7d3e5c1b20"
            )
            assert connection.scalar(
                sa.text(
                    """
                    SELECT count(*)
                    FROM security_metadata_versions AS metadata
                    JOIN security AS identity ON identity.id=metadata.security_id
                    WHERE identity.security_code='2330'
                      AND metadata.effective_from='2026-09-11'
                    """
                )
            ) == 3
            assert "predecessor_version_id" in {
                column["name"]
                for column in sa.inspect(connection).get_columns(
                    "security_metadata_versions"
                )
            }
    finally:
        engine.dispose()


def test_security_metadata_captured_checkpoint_resumes_without_refetch(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    import_id = uuid4()
    raw_store = LocalRawArtifactStore(tmp_path / "raw")
    initial_fetcher = StaticFetcher(_twse_payload())
    try:
        with pytest.raises(SimulatedProcessCrash):
            SecurityMetadataImporter(
                engine, raw_store=raw_store, fetcher=initial_fetcher
            ).run(
                adapter=CrashAfterCaptureAdapter(),
                request=SecurityMetadataRequest(date(2026, 9, 11)),
                import_id=import_id,
                git_commit="test-commit",
            )
        assert initial_fetcher.calls == 1

        resumed = SecurityMetadataImporter(
            engine, raw_store=raw_store, fetcher=FetchMustNotRun()
        ).run(
            adapter=TWSESecurityMetadataAdapter(),
            request=SecurityMetadataRequest(date(2026, 9, 11)),
            import_id=import_id,
            git_commit="test-commit",
        )
        assert resumed.business_versions_created == 2
        with engine.connect() as connection:
            checkpoint = (
                connection.execute(
                    sa.text(
                        "SELECT status, attempt_count FROM import_checkpoints "
                        "WHERE import_id=:import_id"
                    ),
                    {"import_id": import_id},
                )
                .mappings()
                .one()
            )
            assert checkpoint == {"status": "succeeded", "attempt_count": 1}
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("adapter", "writer", "error_code"),
    [
        (UnexpectedAdapterFailure(), None, "adapter_operational_error"),
        (
            TWSESecurityMetadataAdapter(),
            UnexpectedWriterFailure(),
            "writer_operational_error",
        ),
    ],
)
def test_security_operational_failures_remain_captured_and_resumable(
    isolated_database_url: str,
    tmp_path: Path,
    adapter: TWSESecurityMetadataAdapter,
    writer: MarketDataWriter | None,
    error_code: str,
) -> None:
    engine = sa.create_engine(isolated_database_url)
    import_id = uuid4()
    raw_store = LocalRawArtifactStore(tmp_path / error_code)
    try:
        with pytest.raises(RuntimeError, match="simulated security"):
            SecurityMetadataImporter(
                engine,
                raw_store=raw_store,
                fetcher=StaticFetcher(_twse_payload()),
                writer=writer,
            ).run(
                adapter=adapter,
                request=SecurityMetadataRequest(date(2026, 9, 11)),
                import_id=import_id,
                git_commit="test-commit",
            )

        with engine.connect() as connection:
            checkpoint = (
                connection.execute(
                    sa.text(
                        "SELECT status, error_code FROM import_checkpoints "
                        "WHERE import_id=:import_id"
                    ),
                    {"import_id": import_id},
                )
                .mappings()
                .one()
            )
            assert checkpoint == {"status": "captured", "error_code": error_code}
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT count(*) FROM import_quarantine "
                        "WHERE import_id=:import_id"
                    ),
                    {"import_id": import_id},
                )
                == 0
            )

        resumed = SecurityMetadataImporter(
            engine, raw_store=raw_store, fetcher=FetchMustNotRun()
        ).run(
            adapter=TWSESecurityMetadataAdapter(),
            request=SecurityMetadataRequest(date(2026, 9, 11)),
            import_id=import_id,
            git_commit="test-commit",
        )
        assert resumed.business_versions_created == 2
    finally:
        engine.dispose()


def test_invalid_security_snapshot_is_quarantined_after_raw_capture(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    invalid = json.dumps(
        [{"出表日期": "1150911", "公司代號": "2330"}],
        ensure_ascii=False,
    ).encode()
    import_id = uuid4()
    try:
        with pytest.raises(ResourceQuarantinedError, match="schema_mismatch"):
            SecurityMetadataImporter(
                engine,
                raw_store=LocalRawArtifactStore(tmp_path / "raw"),
                fetcher=StaticFetcher(invalid),
            ).run(
                adapter=TWSESecurityMetadataAdapter(),
                request=SecurityMetadataRequest(),
                import_id=import_id,
                git_commit="test-commit",
            )
        with engine.connect() as connection:
            row = (
                connection.execute(
                    sa.text(
                        "SELECT c.status, q.reason_code FROM import_checkpoints c "
                        "JOIN import_quarantine q USING (import_id, resource_key) "
                        "WHERE c.import_id=:import_id"
                    ),
                    {"import_id": import_id},
                )
                .mappings()
                .one()
            )
            assert row == {"status": "quarantined", "reason_code": "schema_mismatch"}
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM security_metadata_versions")
                )
                == 0
            )
    finally:
        engine.dispose()


@pytest.mark.live_source
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_SOURCE_TESTS") != "1",
    reason="set RUN_LIVE_SOURCE_TESTS=1 to call official endpoints",
)
def test_official_security_metadata_snapshots_reach_postgres(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        for adapter in (
            TWSESecurityMetadataAdapter(),
            TPExSecurityMetadataAdapter(),
        ):
            result = SecurityMetadataImporter(
                engine,
                raw_store=LocalRawArtifactStore(tmp_path / "live-raw"),
                fetcher=HttpSourceFetcher(),
            ).run(
                adapter=adapter,
                request=SecurityMetadataRequest(),
                import_id=uuid4(),
                git_commit="live-source-test",
            )
            assert result.normalized_rows > 500
            assert result.business_versions_created > 500
            assert result.unknown_publication_observations == result.normalized_rows
    finally:
        engine.dispose()
