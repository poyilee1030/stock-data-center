"""PR #16 — importing a month of trading days through the raw-first lifecycle."""

from __future__ import annotations

import json

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.ingestion.adapters import TWSETradingCalendarAdapter
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    ResourceQuarantinedError,
    TradingCalendarRequest,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.ingestion.trading_calendar import TradingCalendarImporter
from stock_data_center.market_calendar import (
    CalendarCoverageError,
    TradingCalendarService,
)


pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
JULY_2024 = (FIXTURES / "twse_fmtqik_202407.json").read_bytes()

SERVICE = TradingCalendarService()


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


class FetchMustNotRun:
    def fetch(self, resource):
        raise AssertionError("a completed import must not re-fetch the source")


def run_import(
    engine,
    tmp_path: Path,
    *,
    fetcher,
    month: date = date(2024, 7, 1),
    import_id=None,
    today: date = date(2026, 9, 15),
):
    importer = TradingCalendarImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=fetcher,
        today=today,
    )
    used_id = import_id or uuid4()
    result = importer.run(
        adapter=TWSETradingCalendarAdapter(),
        request=TradingCalendarRequest(month),
        import_id=used_id,
        git_commit="test-commit",
    )
    with engine.connect() as connection:
        manifest = importer.manifest(connection, used_id)
    return result, manifest


def test_a_real_month_is_imported_raw_first_with_its_closures(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest = run_import(
            engine, tmp_path, fetcher=StaticFetcher(JULY_2024)
        )

        assert result.normalized_rows == 21
        assert manifest.reconciliation["trading_day_count"] == 21
        assert manifest.reconciliation["month_complete"] is True
        assert manifest.reconciliation["coverage_validation"] == "complete_month"
        assert manifest.reconciliation["closure_semantics"] == (
            "absence_from_the_published_list"
        )
        assert manifest.reconciliation["publication_time"] == "unknown"

        with engine.connect() as connection:
            assert not SERVICE.is_trading_day(
                connection, market="TWSE", day=date(2024, 7, 24)
            )
            assert SERVICE.is_trading_day(
                connection, market="TWSE", day=date(2024, 7, 26)
            )
            stored = connection.execute(
                sa.text(
                    "SELECT count(*) FROM trading_calendar_versions "
                    "WHERE market = 'TWSE' AND calendar_month = DATE '2024-07-01'"
                )
            ).scalar_one()
            assert stored == 1
    finally:
        engine.dispose()


def test_the_import_records_unknown_publication_evidence(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The source publishes no release instant, so nothing may be invented."""
    engine = sa.create_engine(isolated_database_url)
    try:
        run_import(engine, tmp_path, fetcher=StaticFetcher(JULY_2024))

        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT evidence_kind, published_at, evidence_type, quality_rank "
                    "FROM publication_evidence "
                    "WHERE dataset_code = 'trading_calendar'"
                )
            ).mappings().one()

        assert row["evidence_kind"] == "unknown"
        assert row["published_at"] is None
        assert row["quality_rank"] == 0
    finally:
        engine.dispose()


def test_reimporting_the_same_month_keeps_one_version_and_does_not_refetch(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        import_id = uuid4()
        run_import(
            engine, tmp_path, fetcher=StaticFetcher(JULY_2024), import_id=import_id
        )
        again, _ = run_import(
            engine, tmp_path, fetcher=FetchMustNotRun(), import_id=import_id
        )

        assert again.resumed_from_checkpoint

        with engine.connect() as connection:
            versions = connection.execute(
                sa.text("SELECT count(*) FROM trading_calendar_versions")
            ).scalar_one()
        assert versions == 1
    finally:
        engine.dispose()


def test_an_unfinished_month_claims_only_the_days_it_published(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Fetched mid-month, the rest of the month is unknown rather than closed."""
    engine = sa.create_engine(isolated_database_url)
    partial = json.loads(JULY_2024)
    partial["data"] = [
        row for row in partial["data"] if row[0] <= "113/07/11"
    ]
    try:
        _, manifest = run_import(
            engine,
            tmp_path,
            fetcher=StaticFetcher(json.dumps(partial, ensure_ascii=False).encode()),
            today=date(2024, 7, 15),
        )

        assert manifest.reconciliation["month_complete"] is False
        assert manifest.reconciliation["coverage_validation"] == "partial_month"
        assert manifest.reconciliation["coverage_through"] == "2024-07-11"

        with engine.connect() as connection:
            assert SERVICE.is_trading_day(
                connection, market="TWSE", day=date(2024, 7, 11)
            )
            with pytest.raises(CalendarCoverageError):
                SERVICE.is_trading_day(
                    connection, market="TWSE", day=date(2024, 7, 12)
                )
    finally:
        engine.dispose()


def test_a_month_the_source_cannot_serve_is_quarantined_after_raw_capture(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    empty = b'{"stat":"OK","fields":["\\u65e5\\u671f"],"data":[]}'
    try:
        with pytest.raises(ResourceQuarantinedError, match="empty_coverage"):
            run_import(engine, tmp_path, fetcher=StaticFetcher(empty))

        with engine.connect() as connection:
            reason = connection.execute(
                sa.text("SELECT reason_code FROM import_quarantine")
            ).scalar_one()
            artifacts = connection.execute(
                sa.text("SELECT count(*) FROM raw_artifacts")
            ).scalar_one()
        assert reason == "empty_coverage"
        assert artifacts == 1
    finally:
        engine.dispose()
