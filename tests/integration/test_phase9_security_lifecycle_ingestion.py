from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.ingestion import (
    FetchedArtifact,
    LocalRawArtifactStore,
    ResourceQuarantinedError,
    SecurityLifecycleImporter,
    SecurityLifecycleRequest,
)
from stock_data_center.ingestion.adapters import (
    TPExDelistingHistoryAdapter,
    TPExListingHistoryAdapter,
    TWSEDelistingHistoryAdapter,
    TWSEListingHistoryAdapter,
)
from stock_data_center.ingestion.http import HttpSourceFetcher
from stock_data_center.market_data import MarketDataService
from stock_data_center.pit import MarketPITContext, SystemPITContext

pytestmark = pytest.mark.integration


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


class CrashAfterCaptureAdapter(TPExDelistingHistoryAdapter):
    def parse(self, content, request):
        raise SimulatedProcessCrash


class FetchMustNotRun:
    def fetch(self, resource):
        raise AssertionError("captured checkpoint must not re-fetch the source")


def _tpex(adapter, rows: list[list[object]], year: int) -> bytes:
    return json.dumps(
        {
            "stat": "ok",
            "date": str(year),
            "tables": [{"fields": list(adapter.fields), "data": rows}],
        },
        ensure_ascii=False,
    ).encode()


def _twse_transfer() -> bytes:
    adapter = TWSEListingHistoryAdapter()
    return json.dumps(
        {
            "fields": list(adapter.fields),
            "data": [
                [
                    "5236",
                    "凌陽創新",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "115.07.16",
                    "",
                    100,
                    "櫃轉市",
                ]
            ],
        },
        ensure_ascii=False,
    ).encode()


def test_authoritative_transfer_events_preserve_independent_source_histories(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    raw_store = LocalRawArtifactStore(tmp_path / "raw")
    service = MarketDataService()
    tpex_listing = TPExListingHistoryAdapter()
    tpex_delisting = TPExDelistingHistoryAdapter()
    try:
        SecurityLifecycleImporter(
            engine,
            raw_store=raw_store,
            fetcher=StaticFetcher(
                _tpex(
                    tpex_listing,
                    [[1, "5236", "凌陽創新", "109/01/06", "10", "a", "b"]],
                    2020,
                )
            ),
        ).run(
            adapter=tpex_listing,
            request=SecurityLifecycleRequest(2020),
            import_id=uuid4(),
            git_commit="test-commit",
        )
        SecurityLifecycleImporter(
            engine,
            raw_store=raw_store,
            fetcher=StaticFetcher(
                _tpex(
                    tpex_delisting,
                    [["5236", "凌陽創新科技股份有限公司", "115-07-16", "規則", "url"]],
                    2026,
                )
            ),
        ).run(
            adapter=tpex_delisting,
            request=SecurityLifecycleRequest(2026),
            import_id=uuid4(),
            git_commit="test-commit",
        )
        transfer_import_id = uuid4()
        transfer = SecurityLifecycleImporter(
            engine,
            raw_store=raw_store,
            fetcher=StaticFetcher(_twse_transfer()),
        ).run(
            adapter=TWSEListingHistoryAdapter(),
            request=SecurityLifecycleRequest(),
            import_id=transfer_import_id,
            git_commit="test-commit",
        )

        assert transfer.business_versions_created == 1
        assert transfer.unknown_publication_observations == 1
        with engine.connect() as connection:
            context = SystemPITContext(datetime.now(UTC) + timedelta(minutes=1))
            before = service.security_state(
                connection,
                security_code="5236",
                effective_on=date(2025, 6, 1),
                context=context,
                source="tpex",
            )
            tpex_after = service.security_state(
                connection,
                security_code="5236",
                effective_on=date(2026, 7, 16),
                context=context,
                source="tpex",
            )
            twse_before = service.security_state(
                connection,
                security_code="5236",
                effective_on=date(2026, 7, 15),
                context=context,
                source="twse",
            )
            after = service.security_state(
                connection,
                security_code="5236",
                effective_on=date(2026, 7, 16),
                context=context,
                source="twse",
            )
            assert before is not None and before.is_listed and before.market == "TPEx"
            assert tpex_after is not None and not tpex_after.is_listed
            assert twse_before is None
            assert after is not None and after.is_listed and after.market == "TWSE"
            assert before.security_id == after.security_id

            market = service.security_state(
                connection,
                security_code="5236",
                effective_on=date(2026, 7, 16),
                context=MarketPITContext(
                    information_as_of=datetime.now(UTC) + timedelta(minutes=1),
                    knowledge_as_of=datetime.now(UTC) + timedelta(minutes=1),
                ),
                source="twse",
            )
            assert market is None
            manifest = SecurityLifecycleImporter.manifest(
                connection, transfer_import_id
            )
            assert manifest.reconciliation["explicit_transfer_event_count"] == 1
            assert manifest.reconciliation["matched_cross_source_transfer_count"] == 1
            assert manifest.reconciliation["coverage_completeness"] == ("not_evaluated")
            assert manifest.result_counts["rejected_quarantined_count"] == 0
            assert manifest.reconciliation["warnings_anomalies"] == []
    finally:
        engine.dispose()


def test_repeated_lifecycle_import_deduplicates_business_and_preserves_observation(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    adapter = TPExDelistingHistoryAdapter()
    payload = _tpex(
        adapter,
        [["3426", "台興電子企業股份有限公司", "115-06-08", "規則", "url"]],
        2026,
    )
    try:
        outcomes = []
        for _ in range(2):
            outcomes.append(
                SecurityLifecycleImporter(
                    engine,
                    raw_store=LocalRawArtifactStore(tmp_path / "raw"),
                    fetcher=StaticFetcher(payload),
                ).run(
                    adapter=adapter,
                    request=SecurityLifecycleRequest(2026),
                    import_id=uuid4(),
                    git_commit="test-commit",
                )
            )
        assert [item.business_versions_created for item in outcomes] == [1, 0]
        assert outcomes[1].publication_evidence_deduplicated == 1
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM security_metadata_versions")
                )
                == 1
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
                == 2
            )
    finally:
        engine.dispose()


def test_invalid_lifecycle_source_is_quarantined_after_capture(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    import_id = uuid4()
    adapter = TPExDelistingHistoryAdapter()
    invalid = _tpex(adapter, [["bad"]], 2026)
    try:
        with pytest.raises(ResourceQuarantinedError, match="schema_mismatch"):
            SecurityLifecycleImporter(
                engine,
                raw_store=LocalRawArtifactStore(tmp_path / "raw"),
                fetcher=StaticFetcher(invalid),
            ).run(
                adapter=adapter,
                request=SecurityLifecycleRequest(2026),
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
            assert checkpoint == {
                "status": "quarantined",
                "error_code": "schema_mismatch",
            }
            assert connection.scalar(sa.text("SELECT count(*) FROM raw_artifacts")) == 1
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM security_metadata_versions")
                )
                == 0
            )
    finally:
        engine.dispose()


def test_lifecycle_capture_resumes_from_retained_bytes_without_refetch(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    import_id = uuid4()
    adapter = TPExDelistingHistoryAdapter()
    payload = _tpex(
        adapter,
        [["3426", "台興電子企業股份有限公司", "115-06-08", "規則", "url"]],
        2026,
    )
    raw_store = LocalRawArtifactStore(tmp_path / "raw")
    initial_fetcher = StaticFetcher(payload)
    try:
        with pytest.raises(SimulatedProcessCrash):
            SecurityLifecycleImporter(
                engine, raw_store=raw_store, fetcher=initial_fetcher
            ).run(
                adapter=CrashAfterCaptureAdapter(),
                request=SecurityLifecycleRequest(2026),
                import_id=import_id,
                git_commit="test-commit",
            )
        assert initial_fetcher.calls == 1

        resumed = SecurityLifecycleImporter(
            engine, raw_store=raw_store, fetcher=FetchMustNotRun()
        ).run(
            adapter=adapter,
            request=SecurityLifecycleRequest(2026),
            import_id=import_id,
            git_commit="test-commit",
        )
        assert resumed.business_versions_created == 1
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


@pytest.mark.live_source
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_SOURCE_TESTS") != "1",
    reason="set RUN_LIVE_SOURCE_TESTS=1 to call official endpoints",
)
def test_official_security_lifecycle_resources_reach_postgres(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    raw_store = LocalRawArtifactStore(tmp_path / "live-raw")
    try:
        plans = (
            (TPExListingHistoryAdapter(), SecurityLifecycleRequest(2025)),
            (TPExDelistingHistoryAdapter(), SecurityLifecycleRequest(2025)),
            (TWSEListingHistoryAdapter(), SecurityLifecycleRequest()),
            (TWSEDelistingHistoryAdapter(), SecurityLifecycleRequest()),
        )
        manifests = []
        for adapter, request in plans:
            import_id = uuid4()
            result = SecurityLifecycleImporter(
                engine,
                raw_store=raw_store,
                fetcher=HttpSourceFetcher(),
            ).run(
                adapter=adapter,
                request=request,
                import_id=import_id,
                git_commit="live-source-test",
            )
            assert result.normalized_rows > 0
            assert result.unknown_publication_observations == result.normalized_rows
            with engine.connect() as connection:
                manifests.append(
                    SecurityLifecycleImporter.manifest(connection, import_id)
                )
        assert manifests[2].reconciliation["explicit_transfer_event_count"] > 0
        assert manifests[2].reconciliation["matched_cross_source_transfer_count"] > 0
    finally:
        engine.dispose()
