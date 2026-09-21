"""Step 23-c — the archive's publication evidence and the full backfill.

23-b stored every filing with `unknown` evidence: System-PIT visible, Market-PIT
invisible. This step attaches what the archive can prove. The file's mtime is
the whole of the evidence, and it says one of two things (audit §4.8):

* a daily-job file from 2025Q4 onward was written when the legacy scraper first
  saw the filing, so it bounds publication from above —
  `legacy_capture_bound` at that instant;
* a file from one of the bulk runs — February 2026 for 2020Q1–2025Q3, and the
  2026-08-01/16/17 catch-ups — was written long after publication and proves
  nothing, so the filing resolves by `financial_statements_general@1`.

The backfill itself walks the archive quarter by quarter, one import id per
document, so a run that dies at document 30,000 resumes there.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from stock_data_center.financials import FinancialFilingService
from stock_data_center.financials.models import FilingPeriod
from stock_data_center.ingestion.adapters.financial_filing_archive import (
    LegacyFinancialFilingArchiveAdapter,
)
from stock_data_center.ingestion.backfill import (
    FinancialFilingArchiveBackfill,
    default_base_import_id,
)
from stock_data_center.ingestion.financial_filing import (
    FinancialFilingArchiveImporter,
)
from stock_data_center.ingestion.models import FinancialFilingArchiveRequest
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_calendar import TradingCalendarWriter
from stock_data_center.market_calendar.models import (
    CalendarLineageRef,
    TradingCalendarObservation,
)
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.provenance import ArtifactOrigin, IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TAIPEI = ZoneInfo("Asia/Taipei")
SOURCE = "mops_t164sb01"

CEMENT_2026Q1 = (FIXTURES / "mops_t164sb01_1101_2026Q1_statements.html").read_bytes()
CEMENT_2020Q2 = (FIXTURES / "mops_t164sb01_1101_2020Q2_statements.html").read_bytes()
BROKER_2020Q1 = (FIXTURES / "mops_t164sb01_5864_2020Q1_statements.html").read_bytes()

# The daily job wrote 2026Q1's 1101 at this instant; the 08-17 catch-up run
# wrote the stragglers.
DAILY_CAPTURE = datetime(2026, 5, 13, 21, 44, 5, tzinfo=TAIPEI)
CATCH_UP_RUN = datetime(2026, 8, 17, 2, 15, 0, tzinfo=TAIPEI)

MAY_2026_TRADING_DAYS = tuple(
    date(2026, 5, day)
    for day in range(1, 32)
    if date(2026, 5, day).weekday() < 5
)
AUGUST_2020_TRADING_DAYS = tuple(
    date(2020, 8, day)
    for day in range(1, 32)
    if date(2020, 8, day).weekday() < 5
)


def archive_file(
    root: Path, *, period: str, code: str, content: bytes, mtime: datetime
) -> Path:
    """One archived document, named and dated the way the archive names them.

    The trailing date in the name is the legacy scraper's synthetic deadline,
    which carries no evidence; the mtime is what this step reads.
    """
    year = period[:4]
    folder = root / year / period
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{period}_{code}_{year}1231.html"
    path.write_bytes(content)
    stamp = mtime.timestamp()
    os.utime(path, (stamp, stamp))
    return path


def import_archive(
    engine,
    tmp_path: Path,
    *,
    root: Path,
    code: str,
    year: int,
    quarter: int,
    import_id=None,
    purpose: IngestPurpose = IngestPurpose.GAP_FILL,
):
    importer = FinancialFilingArchiveImporter(
        engine, raw_store=LocalRawArtifactStore(tmp_path / "raw")
    )
    import_id = import_id or uuid4()
    result = importer.run(
        adapter=LegacyFinancialFilingArchiveAdapter(archive_root=root),
        request=FinancialFilingArchiveRequest(code, year, quarter),
        import_id=import_id,
        purpose=purpose,
    )
    with engine.connect() as connection:
        manifest = importer.manifest(connection, import_id)
    return result, manifest


def store_calendar(connection, *, month: date, days, through: date) -> None:
    digest = hashlib.sha256(month.isoformat().encode()).hexdigest()
    run_id = connection.scalar(
        sa.text(
            "INSERT INTO ingest_runs (dataset_code, source, status, started_at, "
            " purpose) VALUES ('trading_calendar', 'twse', 'succeeded', now(), "
            "'gap_fill') RETURNING id"
        )
    )
    artifact_id = connection.scalar(
        sa.text(
            "INSERT INTO raw_artifacts (raw_artifact_hash, storage_uri, byte_size, "
            " media_type) VALUES (:h, :uri, 1, 'application/json') RETURNING id"
        ),
        {"h": digest, "uri": f"file://{digest[:2]}/{digest}"},
    )
    connection.execute(
        sa.text(
            "INSERT INTO raw_artifact_observations (raw_artifact_id, ingest_run_id, "
            " source_uri, fetched_at, artifact_origin) "
            "VALUES (:a, :r, 'https://x', now(), 'official_fetch')"
        ),
        {"a": artifact_id, "r": run_id},
    )
    TradingCalendarWriter().append_month(
        connection,
        source="twse",
        observation=TradingCalendarObservation(
            market="TWSE",
            calendar_month=month,
            trading_days=tuple(days),
            coverage_through=through,
        ),
        lineage=CalendarLineageRef(artifact_id, run_id),
    )
    connection.commit()


def evidence_rows(connection, *, code: str, year: int, quarter: int):
    return connection.execute(
        sa.text(
            """
            SELECT e.evidence_type, e.published_at, e.quality_rank,
                   e.evidence_source
              FROM publication_evidence e
              JOIN financial_filing_versions v
                ON v.id = e.financial_filing_version_id
              JOIN security s ON s.id = v.security_id
             WHERE s.security_code = :code
               AND v.report_year = :year
               AND v.report_quarter = :quarter
             ORDER BY e.quality_rank DESC, e.id
            """
        ),
        {"code": code, "year": year, "quarter": quarter},
    ).mappings().all()


def claimed_types(rows) -> list[str]:
    """What this step claimed, leaving out the `unknown` row 23-b wrote."""
    return [row["evidence_type"] for row in rows if row["evidence_type"] != "official"]


def market_visible(connection, *, code: str, period: FilingPeriod, at: datetime):
    """What the market knew at `at`, using everything we have recorded.

    `knowledge_as_of` is now, not `at`: this evidence was recorded today, and
    asking what we had recorded in 2026M05 would answer nothing for any row.
    """
    return FinancialFilingService().filing(
        connection,
        security_code=code,
        period=period,
        context=MarketPITContext(
            information_as_of=at,
            knowledge_as_of=datetime.now(UTC) + timedelta(minutes=1),
        ),
        source=SOURCE,
    )


def test_a_daily_job_file_carries_a_legacy_capture_bound(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        root = tmp_path / "xbrl"
        archive_file(
            root, period="2026Q1", code="1101",
            content=CEMENT_2026Q1, mtime=DAILY_CAPTURE,
        )
        import_archive(engine, tmp_path, root=root, code="1101", year=2026, quarter=1)
        with engine.connect() as connection:
            rows = evidence_rows(connection, code="1101", year=2026, quarter=1)
            assert claimed_types(rows) == ["legacy_capture_bound"]
            assert rows[0]["published_at"] == DAILY_CAPTURE
            assert rows[0]["evidence_source"] == (
                f"legacy xbrl archive {DAILY_CAPTURE.date().isoformat()}"
            )
    finally:
        engine.dispose()


def test_market_pit_sees_the_filing_at_the_capture_and_not_before(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        root = tmp_path / "xbrl"
        archive_file(
            root, period="2026Q1", code="1101",
            content=CEMENT_2026Q1, mtime=DAILY_CAPTURE,
        )
        import_archive(engine, tmp_path, root=root, code="1101", year=2026, quarter=1)
        period = FilingPeriod(2026, 1)
        with engine.connect() as connection:
            assert market_visible(
                connection, code="1101", period=period, at=DAILY_CAPTURE
            ) is not None
            assert market_visible(
                connection,
                code="1101",
                period=period,
                at=DAILY_CAPTURE - timedelta(seconds=1),
            ) is None
    finally:
        engine.dispose()


def test_a_catch_up_run_file_resolves_by_the_statutory_rule(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """2026-05-15 is a Friday, so Q1's deadline does not move."""
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            store_calendar(
                connection, month=date(2026, 5, 1),
                days=MAY_2026_TRADING_DAYS, through=date(2026, 5, 31),
            )
        root = tmp_path / "xbrl"
        archive_file(
            root, period="2026Q1", code="1101",
            content=CEMENT_2026Q1, mtime=CATCH_UP_RUN,
        )
        import_archive(engine, tmp_path, root=root, code="1101", year=2026, quarter=1)
        with engine.connect() as connection:
            rows = evidence_rows(connection, code="1101", year=2026, quarter=1)
            assert claimed_types(rows) == ["release_rule"]
            assert rows[0]["evidence_source"] == "financial_statements_general@1"
            assert rows[0]["published_at"] == datetime.combine(
                date(2026, 5, 15), time(23, 59, 59), tzinfo=TAIPEI
            )
    finally:
        engine.dispose()


def test_a_february_refetch_file_resolves_by_the_rule_whatever_its_mtime(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """2020-08-15 is a Saturday: the deadline moves to Monday the 17th."""
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            store_calendar(
                connection, month=date(2020, 8, 1),
                days=AUGUST_2020_TRADING_DAYS, through=date(2020, 8, 31),
            )
        root = tmp_path / "xbrl"
        archive_file(
            root, period="2020Q2", code="1101", content=CEMENT_2020Q2,
            mtime=datetime(2026, 2, 22, 9, 30, tzinfo=TAIPEI),
        )
        import_archive(engine, tmp_path, root=root, code="1101", year=2020, quarter=2)
        with engine.connect() as connection:
            rows = evidence_rows(connection, code="1101", year=2020, quarter=2)
            assert claimed_types(rows) == ["release_rule"]
            assert rows[0]["published_at"] == datetime.combine(
                date(2020, 8, 17), time(23, 59, 59), tzinfo=TAIPEI
            )
    finally:
        engine.dispose()


def test_rerunning_the_archive_import_adds_no_version_and_no_evidence(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        root = tmp_path / "xbrl"
        archive_file(
            root, period="2026Q1", code="1101",
            content=CEMENT_2026Q1, mtime=DAILY_CAPTURE,
        )
        import_archive(engine, tmp_path, root=root, code="1101", year=2026, quarter=1)
        result, _ = import_archive(
            engine, tmp_path, root=root, code="1101", year=2026, quarter=1
        )
        assert result.business_versions_created == 0
        assert result.business_versions_deduplicated == 1
        with engine.connect() as connection:
            rows = evidence_rows(connection, code="1101", year=2026, quarter=1)
            assert claimed_types(rows) == ["legacy_capture_bound"]
            assert connection.scalar(
                sa.text("SELECT count(*) FROM financial_filing_versions")
            ) == 1
    finally:
        engine.dispose()


def test_the_artifact_origin_stays_legacy_archive(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        root = tmp_path / "xbrl"
        archive_file(
            root, period="2026Q1", code="1101",
            content=CEMENT_2026Q1, mtime=DAILY_CAPTURE,
        )
        import_archive(engine, tmp_path, root=root, code="1101", year=2026, quarter=1)
        with engine.connect() as connection:
            origins = connection.scalars(
                sa.text(
                    "SELECT DISTINCT o.artifact_origin"
                    " FROM raw_artifact_observations o"
                    " JOIN ingest_runs r ON r.id = o.ingest_run_id"
                    " WHERE r.dataset_code = 'financial_filing'"
                )
            ).all()
        assert origins == [ArtifactOrigin.LEGACY_ARCHIVE.value]
    finally:
        engine.dispose()


def test_the_backfill_walks_a_quarter_and_reports_what_it_excluded(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        root = tmp_path / "xbrl"
        archive_file(
            root, period="2020Q1", code="5864",
            content=BROKER_2020Q1, mtime=datetime(2026, 2, 21, 8, 0, tzinfo=TAIPEI),
        )
        archive_file(
            root, period="2020Q2", code="1101", content=CEMENT_2020Q2,
            mtime=datetime(2026, 2, 22, 9, 30, tzinfo=TAIPEI),
        )
        with engine.connect() as connection:
            store_calendar(
                connection, month=date(2020, 8, 1),
                days=AUGUST_2020_TRADING_DAYS, through=date(2020, 8, 31),
            )
        backfill = FinancialFilingArchiveBackfill(
            FinancialFilingArchiveImporter(
                engine, raw_store=LocalRawArtifactStore(tmp_path / "raw")
            ),
            archive_root=root,
        )
        report = backfill.run(
            start=FilingPeriod(2020, 1),
            end=FilingPeriod(2020, 2),
            purpose=IngestPurpose.GAP_FILL,
        )
        assert report.documents == 2
        assert report.imported == 1
        assert report.quarantined == 1
        assert report.failed == 0
        quarantined = [item for item in report.results if item.status == "quarantined"]
        assert quarantined[0].security_code == "5864"
        assert quarantined[0].reason_code == "financial_industry_issuer"
        assert report.as_dict()["periods"] == ["2020Q1", "2020Q2"]
    finally:
        engine.dispose()


def test_the_backfill_resumes_rather_than_reimporting(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        root = tmp_path / "xbrl"
        archive_file(
            root, period="2026Q1", code="1101",
            content=CEMENT_2026Q1, mtime=DAILY_CAPTURE,
        )
        backfill = FinancialFilingArchiveBackfill(
            FinancialFilingArchiveImporter(
                engine, raw_store=LocalRawArtifactStore(tmp_path / "raw")
            ),
            archive_root=root,
        )
        first = backfill.run(
            start=FilingPeriod(2026, 1),
            end=FilingPeriod(2026, 1),
            purpose=IngestPurpose.GAP_FILL,
        )
        second = backfill.run(
            start=FilingPeriod(2026, 1),
            end=FilingPeriod(2026, 1),
            purpose=IngestPurpose.GAP_FILL,
        )
        assert first.imported == 1
        assert second.imported == 0
        assert second.resumed == 1
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM financial_filing_versions")
            ) == 1
    finally:
        engine.dispose()


def test_a_missing_quarter_folder_is_reported_not_silently_empty(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        root = tmp_path / "xbrl"
        root.mkdir()
        backfill = FinancialFilingArchiveBackfill(
            FinancialFilingArchiveImporter(
                engine, raw_store=LocalRawArtifactStore(tmp_path / "raw")
            ),
            archive_root=root,
        )
        with pytest.raises(FileNotFoundError, match="2026Q1"):
            backfill.run(
                start=FilingPeriod(2026, 1),
                end=FilingPeriod(2026, 1),
                purpose=IngestPurpose.GAP_FILL,
            )
    finally:
        engine.dispose()


def test_the_source_now_accepts_the_archive_evidence_types(
    isolated_database_url: str,
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            accepted = connection.scalar(
                sa.text(
                    "SELECT accepted_evidence_types FROM dataset_sources"
                    " WHERE dataset_code = 'financial_filing'"
                    "   AND source = 'mops_t164sb01'"
                )
            )
        assert set(accepted) == {
            "capture_bound",
            "legacy_capture_bound",
            "official",
            "release_rule",
        }
    finally:
        engine.dispose()


def test_system_pit_still_sees_a_filing_the_market_cannot(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The rule resolves in 2026; a query before it still gets the filing under
    System PIT, because ingestion happened whatever the market knew."""
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            store_calendar(
                connection, month=date(2020, 8, 1),
                days=AUGUST_2020_TRADING_DAYS, through=date(2020, 8, 31),
            )
        root = tmp_path / "xbrl"
        archive_file(
            root, period="2020Q2", code="1101", content=CEMENT_2020Q2,
            mtime=datetime(2026, 2, 22, 9, 30, tzinfo=TAIPEI),
        )
        import_archive(engine, tmp_path, root=root, code="1101", year=2020, quarter=2)
        before = datetime(2020, 8, 1, tzinfo=UTC)
        with engine.connect() as connection:
            assert market_visible(
                connection, code="1101", period=FilingPeriod(2020, 2), at=before
            ) is None
            assert FinancialFilingService().filing(
                connection,
                security_code="1101",
                period=FilingPeriod(2020, 2),
                context=SystemPITContext(
                    system_as_of=datetime.now(UTC) + timedelta(minutes=1)
                ),
                source=SOURCE,
            ) is not None
    finally:
        engine.dispose()


def test_the_cli_walks_the_archive_and_reports_its_manifest(
    isolated_database_url: str, tmp_path: Path, capsys
) -> None:
    from stock_data_center.ingestion.cli import main

    root = tmp_path / "xbrl"
    archive_file(
        root, period="2026Q1", code="1101",
        content=CEMENT_2026Q1, mtime=DAILY_CAPTURE,
    )
    exit_code = main(
        [
            "--database-url", isolated_database_url,
            "--purpose", "gap_fill",
            "financial-filing-backfill",
            "--period", "2026Q1",
            "--archive-root", str(root),
            "--raw-root", str(tmp_path / "raw"),
        ]
    )
    assert exit_code == 0
    manifest = json.loads(capsys.readouterr().out)["backfill"]
    assert manifest["documents"] == 1
    assert manifest["imported"] == 1
    assert manifest["periods"] == ["2026Q1"]
    assert manifest["failed"] == 0
    # Derived from the range, so a rerun of the same command resumes it.
    assert manifest["base_import_id"] == str(
        default_base_import_id(
            "mops_t164sb01", date(2026, 1, 1), date(2026, 1, 1)
        )
    )
