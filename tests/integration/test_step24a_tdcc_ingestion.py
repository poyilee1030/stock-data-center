"""Step 24-a — importing one TDCC week through the raw-first lifecycle."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.ingestion.adapters.tdcc_shareholding import (
    LegacyTDCCArchiveAdapter,
    TDCCOpenDataAdapter,
    parse_shareholding,
)
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    TDCCShareholdingRequest,
)
from stock_data_center.ingestion.http import TDCCArchiveFetcher
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.ingestion.tdcc_shareholding import TDCCShareholdingImporter
from stock_data_center.market_data import MarketDataWriter
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.provenance import ArtifactOrigin, IngestPurpose
from stock_data_center.tdcc import (
    TDCCDistributionError,
    TDCCSnapshotService,
    TDCCSnapshotWriter,
)

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LATEST = (FIXTURES / "tdcc_od_1_5_20260918.csv").read_bytes()
TRUNCATED = (FIXTURES / "tdcc_od_1_5_20231020_truncated.csv").read_bytes()
OVER_HUNDRED = (FIXTURES / "tdcc_od_1_5_20200430_double_bom.csv").read_bytes()

WEEK = date(2026, 9, 18)
# `tdcc_weekly@1`: the data date resolves at 12:00 on the following Sunday,
# Asia/Taipei, which is 04:00 UTC (ADR-0020 §3, decision 2).
RELEASE = datetime(2026, 9, 20, 4, 0, tzinfo=UTC)
SOURCE = "tdcc_opendata"


class StaticFetcher:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.last_mtime: datetime | None = None
        self.last_candidates: tuple[str, ...] = ()

    def fetch(self, resource):
        return FetchedArtifact(
            content=self.content,
            source_uri=resource.source_uri,
            fetched_at=datetime.now(UTC),
            media_type="text/csv",
        )


def register(engine, *codes: str) -> None:
    """The exchange feeds own identity; TDCC registers nothing (§30, §75)."""
    with engine.begin() as connection:
        MarketDataWriter().register_securities(
            connection, security_codes=list(codes)
        )


def run(
    engine,
    tmp_path,
    *,
    content=LATEST,
    week=WEEK,
    adapter=None,
    purpose=IngestPurpose.GAP_FILL,
):
    importer = TDCCShareholdingImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=StaticFetcher(content),
    )
    import_id = uuid4()
    result = importer.run(
        adapter=adapter or TDCCOpenDataAdapter(),
        request=TDCCShareholdingRequest(week),
        import_id=import_id,
        git_commit="test-commit",
        purpose=purpose,
    )
    with engine.connect() as connection:
        manifest = importer.manifest(connection, import_id)
    return result, manifest


def later() -> MarketPITContext:
    moment = datetime.now(UTC) + timedelta(minutes=1)
    return MarketPITContext(information_as_of=moment, knowledge_as_of=moment)


def snapshot(connection, code: str, *, week=WEEK, context=None):
    return TDCCSnapshotService().snapshot(
        connection,
        security_code=code,
        snapshot_date=week,
        context=context or later(),
        source=SOURCE,
    )


def test_one_week_imports_sealed_snapshots_for_the_securities_we_hold(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "0056", "1101")
        result, manifest = run(engine, tmp_path)
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 3
        assert manifest.reconciliation["header_variant"] == "tdcc_opendata_1_5_6"
        assert manifest.reconciliation["actual_snapshot_date"] == "2026-09-18"
        assert manifest.reconciliation["securities_outside_the_v1_universe"] == 0
        with engine.connect() as connection:
            resolved = snapshot(connection, "2330")
        levels = {
            bucket.bucket_code: bucket for bucket in resolved.distribution
        }
        assert len(levels) == 17
        assert levels["1"].holder_count == 2_496_562
        assert levels["1"].shares == 291_447_275
        assert levels["17"].shares == 25_932_370_067
        # 說明4: a difference has no holders, and its publisher signs it.
        assert levels["16"].holder_count is None
        assert levels["16"].shares == -1_000
    finally:
        engine.dispose()


def test_a_security_outside_the_v1_universe_is_counted_not_registered(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330")
        result, manifest = run(engine, tmp_path)
        assert result.business_versions_created == 1
        assert manifest.reconciliation["securities_outside_the_v1_universe"] == 2
        assert manifest.reconciliation["outside_security_codes"] == ["0056", "1101"]
        with engine.connect() as connection:
            known = set(
                connection.scalars(sa.text("SELECT security_code FROM security"))
            )
        assert known == {"2330"}
    finally:
        engine.dispose()


def test_a_week_is_market_invisible_until_its_release_rule(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330")
        run(engine, tmp_path)
        known = datetime.now(UTC) + timedelta(minutes=1)

        def at(connection, moment: datetime):
            return snapshot(
                connection,
                "2330",
                context=MarketPITContext(
                    information_as_of=moment, knowledge_as_of=known
                ),
            )

        with engine.connect() as connection:
            before = at(connection, RELEASE - timedelta(minutes=1))
            after = at(connection, RELEASE)
            system = snapshot(
                connection, "2330", context=SystemPITContext(system_as_of=known)
            )
        assert before is None
        assert after is not None
        assert after.snapshot.authoritative_evidence.evidence_source == (
            "tdcc_weekly@1"
        )
        assert system is not None
    finally:
        engine.dispose()


def test_a_first_capture_run_claims_its_own_capture_bound(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """And the rule it falsified is not recorded.

    The 2026-09-18 week's rule instant is 2026-09-20 12:00 Taipei. A run
    capturing that week for the first time now is later than the rule, which
    falsifies it for these rows (ADR-0020 §2, CLAUDE.md §32), so the capture
    is the only evidence written.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330")
        run(engine, tmp_path, purpose=IngestPurpose.FIRST_CAPTURE)
        with engine.connect() as connection:
            types = set(
                connection.scalars(
                    sa.text(
                        "SELECT evidence_type FROM publication_evidence "
                        "WHERE dataset_code = 'tdcc_snapshot'"
                    )
                )
            )
        assert types == {"capture_bound"}
    finally:
        engine.dispose()


def test_rerunning_the_same_week_creates_no_revision_and_no_evidence(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "0056", "1101")
        run(engine, tmp_path)
        repeated, manifest = run(engine, tmp_path)
        assert repeated.business_versions_created == 0
        assert repeated.business_versions_deduplicated == 3
        assert repeated.publication_evidence_created == 0
        assert repeated.publication_evidence_deduplicated == 3
        with engine.connect() as connection:
            versions = connection.scalar(
                sa.text("SELECT count(*) FROM tdcc_snapshot_versions")
            )
            evidence = connection.scalar(
                sa.text("SELECT count(*) FROM publication_evidence")
            )
            observations = connection.scalar(
                sa.text(
                    "SELECT count(*) FROM tdcc_snapshot_version_observations"
                )
            )
        assert (versions, evidence) == (3, 3)
        # The repeated fetch stays auditable even though nothing was revised.
        assert observations == 6
        assert manifest.status == "succeeded"
    finally:
        engine.dispose()


def test_a_changed_distribution_is_a_new_sealed_version(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "0056", "1101")
        run(engine, tmp_path)
        corrected = LATEST.decode("utf-8-sig").replace(
            ",2496562,291447275,", ",2496563,291447276,", 1
        )
        result, _ = run(engine, tmp_path, content=corrected.encode("utf-8"))
        assert result.business_versions_created == 1
        assert result.business_versions_deduplicated == 2
        with engine.connect() as connection:
            resolved = snapshot(connection, "2330")
            versions = connection.scalar(
                sa.text(
                    "SELECT count(*) FROM tdcc_snapshot_versions "
                    "WHERE security_id = (SELECT id FROM security "
                    "WHERE security_code = '2330')"
                )
            )
        assert versions == 2
        level_one = next(
            bucket
            for bucket in resolved.distribution
            if bucket.bucket_code == "1"
        )
        assert level_one.holder_count == 2_496_563
    finally:
        engine.dispose()


def test_a_truncated_week_imports_its_complete_securities_and_quarantines_the_cut(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "8162")
        result, manifest = run(
            engine, tmp_path, content=TRUNCATED, week=date(2023, 10, 20)
        )
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 1
        assert manifest.reconciliation["truncated_payload"] is True
        assert manifest.reconciliation["row_quarantined_count"] == 1
        assert manifest.reconciliation["row_quarantines"][0]["security_code"] == (
            "8162"
        )
        assert any(
            "truncated" in warning
            for warning in manifest.reconciliation["warnings_anomalies"]
        )
        with engine.connect() as connection:
            quarantined = connection.execute(
                sa.text(
                    "SELECT reason_code, reason_detail FROM import_quarantine"
                )
            ).all()
            written = set(
                connection.scalars(
                    sa.text(
                        "SELECT s.security_code FROM tdcc_snapshot_versions v "
                        "JOIN security s ON s.id = v.security_id"
                    )
                )
            )
        assert [row[0] for row in quarantined] == ["incomplete_distribution"]
        assert "8162" in quarantined[0][1]
        assert written == {"2330"}
    finally:
        engine.dispose()


def test_a_published_total_above_one_hundred_percent_is_stored_as_published(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """158 archived rows publish 合計 above 100, up to 135 (audit §4.9)."""
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "00673R", "2330")
        result, _ = run(
            engine, tmp_path, content=OVER_HUNDRED, week=date(2020, 4, 30)
        )
        assert result.business_versions_created == 2
        with engine.connect() as connection:
            percent = connection.scalar(
                sa.text(
                    "SELECT ownership_percent FROM tdcc_distribution d "
                    "JOIN tdcc_snapshot_versions v ON v.id = d.snapshot_version_id "
                    "JOIN security s ON s.id = v.security_id "
                    "WHERE s.security_code = '00673R' AND d.bucket_code = '17'"
                )
            )
        assert percent == 101
    finally:
        engine.dispose()


def test_the_archive_adapter_records_legacy_archive_provenance(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "0056", "1101")
        _, manifest = run(
            engine,
            tmp_path,
            adapter=LegacyTDCCArchiveAdapter(archive_root=tmp_path / "archive"),
        )
        assert manifest.reconciliation["artifact_origin"] == (
            ArtifactOrigin.LEGACY_ARCHIVE.value
        )
        with engine.connect() as connection:
            origins = set(
                connection.scalars(
                    sa.text("SELECT artifact_origin FROM raw_artifact_observations")
                )
            )
        assert origins == {ArtifactOrigin.LEGACY_ARCHIVE.value}
    finally:
        engine.dispose()


def test_a_holding_level_above_one_hundred_percent_quarantines_that_security(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The ceiling moved to the role, it did not disappear — and it is a
    per-security defect, so the rest of the week still imports.

    No archived holding level exceeds 100 (audit §4.9), so one that did would
    be a parse defect in one security's rows. Failing the whole file over it
    would cost the other ~4,000 securities their published distribution.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "0056", "1101")
        content = LATEST.decode("utf-8-sig").replace(
            ",2496562,291447275,1.12", ",2496562,291447275,101.00", 1
        )
        result, manifest = run(engine, tmp_path, content=content.encode("utf-8"))
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 2
        assert manifest.reconciliation["row_quarantines"] == [
            {
                "security_code": "2330",
                "reason_code": "holding_level_above_one_hundred",
                "detail": "2330 holding level 1 owns 101.00% of custody",
            }
        ]
        with engine.connect() as connection:
            written = set(
                connection.scalars(
                    sa.text(
                        "SELECT s.security_code FROM tdcc_snapshot_versions v "
                        "JOIN security s ON s.id = v.security_id"
                    )
                )
            )
        assert written == {"0056", "1101"}
    finally:
        engine.dispose()


def test_storage_still_refuses_a_holding_level_above_one_hundred(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The adapter is not the only guard: the writer and the row trigger
    enforce the same rule for any other write path (Step 6, ADR-0012)."""
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330")
        importer = TDCCShareholdingImporter(engine)
        with engine.begin() as connection:
            security_id = connection.scalar(
                sa.text("SELECT id FROM security WHERE security_code = '2330'")
            )
            parsed = parse_shareholding(
                LATEST, source=SOURCE, expected_date=WEEK
            )
            row = next(
                item for item in parsed.rows if item.security_code == "2330"
            )
            buckets = tuple(
                replace(bucket, ownership_percent=Decimal("101"))
                if bucket.bucket_code == "1"
                else bucket
                for bucket in row.observation.distribution
            )
            with pytest.raises(TDCCDistributionError, match="cannot own"):
                importer._writer.validate_distribution(
                    connection,
                    replace(row.observation, distribution=buckets),
                )
        del security_id
    finally:
        engine.dispose()


def test_rerunning_a_truncated_week_is_safe_and_records_each_sighting(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """A rerun is idempotent for business content and additive for provenance.

    Each run genuinely saw the file cut, so each leaves its own quarantine row
    (CLAUDE.md §26, §76); nothing is revised and nothing collides.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "8162")
        first, _ = run(engine, tmp_path, content=TRUNCATED, week=date(2023, 10, 20))
        second, _ = run(engine, tmp_path, content=TRUNCATED, week=date(2023, 10, 20))
        assert (first.business_versions_created, second.business_versions_created) == (
            1,
            0,
        )
        assert second.business_versions_deduplicated == 1
        with engine.connect() as connection:
            quarantines = connection.scalar(
                sa.text("SELECT count(*) FROM import_quarantine")
            )
            versions = connection.scalar(
                sa.text("SELECT count(*) FROM tdcc_snapshot_versions")
            )
        assert (quarantines, versions) == (2, 1)
    finally:
        engine.dispose()


def archive(tmp_path: Path, weeks: dict[date, bytes]) -> Path:
    root = tmp_path / "shareholding"
    for week, payload in weeks.items():
        year = root / str(week.year)
        year.mkdir(parents=True, exist_ok=True)
        (year / f"{week:%Y%m%d}.csv").write_bytes(payload)
    return root


def test_an_archive_walk_never_claims_a_capture_however_it_was_declared(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Reading a file off disk today is a gap fill, not a first sighting.

    Honouring a declared `first_capture` would write today's instant as a
    capture bound and, being later than `tdcc_weekly@1`, suppress the rule as
    falsified — which in append-only storage would push that week's market
    visibility to the day of the backfill.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "0056", "1101")
        root = archive(tmp_path, {WEEK: LATEST})
        importer = TDCCShareholdingImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=TDCCArchiveFetcher(),
        )
        import_id = uuid4()
        importer.run(
            adapter=LegacyTDCCArchiveAdapter(archive_root=root),
            request=TDCCShareholdingRequest(WEEK),
            import_id=import_id,
            git_commit="test-commit",
            purpose=IngestPurpose.FIRST_CAPTURE,
        )
        with engine.connect() as connection:
            types = set(
                connection.scalars(
                    sa.text(
                        "SELECT evidence_type FROM publication_evidence "
                        "WHERE dataset_code = 'tdcc_snapshot'"
                    )
                )
            )
            purposes = set(
                connection.scalars(
                    sa.text(
                        "SELECT purpose FROM ingest_runs "
                        "WHERE dataset_code = 'tdcc_snapshot'"
                    )
                )
            )
            published = set(
                connection.scalars(
                    sa.text(
                        "SELECT published_at FROM publication_evidence "
                        "WHERE dataset_code = 'tdcc_snapshot'"
                    )
                )
            )
        assert types == {"release_rule"}
        # The declared purpose is not silently kept either: the run records
        # what it actually did.
        assert purposes == {IngestPurpose.GAP_FILL.value}
        assert published == {RELEASE}
    finally:
        engine.dispose()


def test_the_manifest_names_the_file_that_answered_even_across_weeks(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Read back from the stored artifact, not from the fetcher's last state.

    One fetcher serves a whole 376-week walk (Step 24-b), so live fetcher
    state would attribute the previous week's file to this week's manifest.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "0056", "1101", "00673R")
        root = archive(
            tmp_path,
            {WEEK: LATEST, date(2020, 4, 30): OVER_HUNDRED},
        )
        importer = TDCCShareholdingImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=TDCCArchiveFetcher(),
        )
        manifests = []
        for week in (WEEK, date(2020, 4, 30)):
            import_id = uuid4()
            importer.run(
                adapter=LegacyTDCCArchiveAdapter(archive_root=root),
                request=TDCCShareholdingRequest(week),
                import_id=import_id,
                git_commit="test-commit",
            )
            with engine.connect() as connection:
                manifests.append(importer.manifest(connection, import_id))
        assert [
            Path(str(manifest.reconciliation["archive_file"])).name
            for manifest in manifests
        ] == ["20260918.csv", "20200430.csv"]
        assert [
            manifest.reconciliation["archive_filename_date"]
            for manifest in manifests
        ] == ["2026-09-18", "2020-04-30"]
    finally:
        engine.dispose()


class WriterBug(TDCCSnapshotWriter):
    def write_snapshot(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("simulated writer bug")


class FetchMustNotRun:
    def fetch(self, resource):  # noqa: ANN001
        raise AssertionError("a captured checkpoint must not re-fetch")


def test_a_resumed_week_still_names_its_own_file(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """A resume reads the captured artifact and never calls `fetch()`, so the
    file has to come from the artifact observation (CLAUDE.md §75)."""
    engine = sa.create_engine(isolated_database_url)
    raw_store = LocalRawArtifactStore(tmp_path / "raw")
    try:
        register(engine, "2330", "0056", "1101")
        root = archive(tmp_path, {WEEK: LATEST})
        import_id = uuid4()
        with pytest.raises(RuntimeError, match="simulated writer bug"):
            TDCCShareholdingImporter(
                engine,
                raw_store=raw_store,
                fetcher=TDCCArchiveFetcher(),
                writer=WriterBug(),
            ).run(
                adapter=LegacyTDCCArchiveAdapter(archive_root=root),
                request=TDCCShareholdingRequest(WEEK),
                import_id=import_id,
                git_commit="test-commit",
            )
        resumed = TDCCShareholdingImporter(
            engine, raw_store=raw_store, fetcher=FetchMustNotRun()
        )
        result = resumed.run(
            adapter=LegacyTDCCArchiveAdapter(archive_root=root),
            request=TDCCShareholdingRequest(WEEK),
            import_id=import_id,
            git_commit="test-commit",
        )
        assert result.business_versions_created == 3
        with engine.connect() as connection:
            manifest = resumed.manifest(connection, import_id)
        assert Path(str(manifest.reconciliation["archive_file"])).name == (
            "20260918.csv"
        )
        # Nothing was fetched this run, so there is no mtime to report and no
        # candidate list — and neither is invented.
        assert manifest.reconciliation["archive_file_mtime"] is None
        assert manifest.reconciliation["archive_candidates"] == []
    finally:
        engine.dispose()
