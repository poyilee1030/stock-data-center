"""Step 18-b — importing one index-date through the raw-first lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.coverage import ExpectedCoverageService
from stock_data_center.ingestion.adapters import (
    TPExMarketIndexAdapter,
    TWSEMarketIndexAdapter,
    TWSETaiexHistoryAdapter,
)
from stock_data_center.ingestion.market_index import (
    MarketIndexImporter,
    TaiexHistoryImporter,
)
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    MarketIndexRequest,
    ResourceQuarantinedError,
    TaiexHistoryRequest,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_reference import MarketReferenceService
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_mi_index_allbut0999_20260911.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_mi_index_allbut0999_20240724_closed.json").read_bytes()
TPEX = (FIXTURES / "tpex_index_summary_20260911.json").read_bytes()
TAIEX = (FIXTURES / "twse_mi_5mins_hist_202601.json").read_bytes()

TRADE_DATE = date(2026, 9, 11)
TWSE_ROWS = 273
TPEX_ROWS = 74
TAIEX_ROWS = 21


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


def run_index(engine, tmp_path, *, adapter, content, day=TRADE_DATE, import_id=None,
              purpose=IngestPurpose.GAP_FILL, fetcher=None):
    used = fetcher or StaticFetcher(content)
    importer = MarketIndexImporter(
        engine, raw_store=LocalRawArtifactStore(tmp_path / "raw"), fetcher=used
    )
    used_id = import_id or uuid4()
    result = importer.run(
        adapter=adapter,
        request=MarketIndexRequest(day),
        import_id=used_id,
        git_commit="test-commit",
        purpose=purpose,
    )
    with engine.connect() as connection:
        manifest = importer.manifest(connection, used_id)
    return result, manifest, used


def test_one_twse_index_date_imports_end_to_end(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest, fetcher = run_index(
            engine, tmp_path, adapter=TWSEMarketIndexAdapter(), content=TWSE
        )
        assert fetcher.calls == 1
        assert manifest.status == "succeeded"
        assert result.normalized_rows == TWSE_ROWS
        assert result.business_versions_created == TWSE_ROWS
        assert manifest.reconciliation["index_count"] == TWSE_ROWS
        assert manifest.reconciliation["section_count"] == 6
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM market_index_versions "
                    "WHERE source = 'twse_mi_index'"
                )
            ) == TWSE_ROWS
            record = MarketReferenceService().index(
                connection,
                index_code="twse_mi_index:指數/臺灣證券交易所:寶島股價指數",
                trade_date=TRADE_DATE,
                context=SystemPITContext(datetime.now(UTC) + timedelta(minutes=1)),
                source="twse_mi_index",
            )
            assert record is not None
            assert float(record.data["close_value"]) == 51178.86
            assert float(record.data["change_points"]) == -865.63
            assert record.data["open_value"] is None
    finally:
        engine.dispose()


def test_one_tpex_index_date_keeps_both_sections_apart(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The price and return series for one name are two indices, not one."""
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest, _ = run_index(
            engine, tmp_path, adapter=TPExMarketIndexAdapter(), content=TPEX
        )
        assert manifest.status == "succeeded"
        assert result.business_versions_created == TPEX_ROWS
        with engine.connect() as connection:
            closes = dict(
                connection.execute(
                    sa.text(
                        "SELECT i.index_code, v.close_value "
                        "FROM market_index_versions v "
                        "JOIN market_index i ON i.id = v.market_index_id "
                        "WHERE i.index_code LIKE '%櫃買指數'"
                    )
                ).all()
            )
        assert float(closes["tpex_index_summary:指數:櫃買指數"]) == 395.52
        assert float(closes["tpex_index_summary:報酬指數:櫃買指數"]) == 735.15
    finally:
        engine.dispose()


def test_rerunning_the_same_index_date_creates_no_revision(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run_index(engine, tmp_path, adapter=TWSEMarketIndexAdapter(), content=TWSE)
        repeated, _, _ = run_index(
            engine, tmp_path, adapter=TWSEMarketIndexAdapter(), content=TWSE
        )
        assert repeated.business_versions_created == 0
        assert repeated.business_versions_deduplicated == TWSE_ROWS
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM market_index_versions")
            ) == TWSE_ROWS
    finally:
        engine.dispose()


def test_a_corrected_close_creates_a_revision_for_that_index_only(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run_index(engine, tmp_path, adapter=TWSEMarketIndexAdapter(), content=TWSE)
        payload = json.loads(TWSE)
        table = next(
            t for t in payload["tables"]
            if t.get("fields") and t["fields"][0] == "指數"
        )
        table["data"][0][1] = "51,178.87"
        corrected = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result, _, _ = run_index(
            engine, tmp_path, adapter=TWSEMarketIndexAdapter(), content=corrected
        )
        assert result.business_versions_created == 1
        assert result.business_versions_deduplicated == TWSE_ROWS - 1
    finally:
        engine.dispose()


def test_imported_indices_resolve_by_the_release_rule(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run_index(engine, tmp_path, adapter=TWSEMarketIndexAdapter(), content=TWSE)
        with engine.connect() as connection:
            market = MarketReferenceService().index(
                connection,
                index_code="twse_mi_index:指數/臺灣證券交易所:寶島股價指數",
                trade_date=TRADE_DATE,
                context=MarketPITContext(
                    information_as_of=datetime.now(UTC) + timedelta(minutes=1),
                    knowledge_as_of=datetime.now(UTC) + timedelta(minutes=1),
                ),
                source="twse_mi_index",
            )
            assert market is not None
            assert market.authoritative_evidence.evidence_type == "release_rule"
            assert market.authoritative_evidence.evidence_source == (
                "exchange_daily_settled@1"
            )
    finally:
        engine.dispose()


def test_a_closed_date_quarantines_and_keeps_its_raw_artifact(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError, match="no_data_for_date"):
            run_index(
                engine, tmp_path, adapter=TWSEMarketIndexAdapter(),
                content=TWSE_CLOSED, day=date(2024, 7, 24),
            )
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM market_index_versions")
            ) == 0
            assert connection.scalar(
                sa.text("SELECT count(*) FROM raw_artifacts")
            ) == 1
            assert connection.scalar(
                sa.text("SELECT reason_code FROM import_quarantine")
            ) == "no_data_for_date"
    finally:
        engine.dispose()


def test_the_taiex_month_imports_its_ohlc(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        importer = TaiexHistoryImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=StaticFetcher(TAIEX),
        )
        import_id = uuid4()
        result = importer.run(
            adapter=TWSETaiexHistoryAdapter(),
            request=TaiexHistoryRequest(date(2026, 1, 1)),
            import_id=import_id,
            git_commit="test-commit",
            purpose=IngestPurpose.GAP_FILL,
        )
        assert result.normalized_rows == TAIEX_ROWS
        assert result.business_versions_created == TAIEX_ROWS
        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT v.open_value, v.high_value, v.low_value, v.close_value "
                    "FROM market_index_versions v "
                    "JOIN market_index i ON i.id = v.market_index_id "
                    "WHERE v.source = 'twse_mi_5mins_hist' "
                    "AND v.trade_date = DATE '2026-01-02'"
                )
            ).one()
        assert [float(value) for value in row] == [
            29016.68, 29363.43, 29007.75, 29349.81
        ]
    finally:
        engine.dispose()


def test_the_taiex_source_is_separate_from_the_whole_list_one(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Different field coverage — one has OHLC, the other has the change
    columns — so they are separate histories (CLAUDE.md §30)."""
    engine = sa.create_engine(isolated_database_url)
    try:
        TaiexHistoryImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=StaticFetcher(TAIEX),
        ).run(
            adapter=TWSETaiexHistoryAdapter(),
            request=TaiexHistoryRequest(date(2026, 1, 1)),
            import_id=uuid4(),
            git_commit="test-commit",
            purpose=IngestPurpose.GAP_FILL,
        )
        with engine.connect() as connection:
            sources = set(
                connection.scalars(
                    sa.text("SELECT DISTINCT source FROM market_index_versions")
                )
            )
        assert sources == {"twse_mi_5mins_hist"}
    finally:
        engine.dispose()


def test_both_markets_declare_their_expected_index_coverage(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        service = ExpectedCoverageService()
        with engine.connect() as connection:
            twse = service.declaration(
                connection, dataset_code="market_index", market="TWSE"
            )
            tpex = service.declaration(
                connection, dataset_code="market_index", market="TPEx"
            )
        assert twse.source == "twse_mi_index"
        assert twse.cadence == "trading_day"
        assert twse.window_start == date(2020, 1, 2)
        assert tpex.source == "tpex_index_summary"
        assert tpex.calendar_market == "TWSE"
    finally:
        engine.dispose()


def test_a_month_run_derives_its_base_id_from_the_scope() -> None:
    """The same defect the date-range runner had, one loop over.

    Step 17-c fixed resume for the date range by deriving the run id from
    `(source, start, end)`. The month loops — the trading calendar's and the
    TAIEX one — kept minting a fresh `uuid4()`, so a run that died partway
    could not be resumed by running the same command again. A live TAIEX
    backfill hit a `ReadTimeout` at month 56 of 81 and proved it.
    """
    from stock_data_center.ingestion.backfill import (
        default_base_import_id,
        month_import_id,
    )

    scope = ("twse_mi_5mins_hist", date(2020, 1, 1), date(2026, 9, 1))
    assert default_base_import_id(*scope) == default_base_import_id(*scope)
    base = default_base_import_id(*scope)
    assert month_import_id(base, date(2024, 8, 1)) == month_import_id(
        default_base_import_id(*scope), date(2024, 8, 1)
    )
    assert month_import_id(base, date(2024, 8, 1)) != month_import_id(
        base, date(2024, 9, 1)
    )


def test_a_month_run_whose_every_month_fails_still_reports(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The failure path this step added must survive its own worst case.

    `result` and the trailing `import_id` are only assigned on a successful
    month, so an all-failed run reached the reporting block with an unrelated
    id: `manifest(...).one()` raised `NoResultFound` and `asdict(result)` would
    have raised `UnboundLocalError`. The run that fails completely is exactly
    the one whose report matters.
    """
    from stock_data_center.ingestion.cli import main

    class AlwaysFails:
        def fetch(self, resource):
            raise TimeoutError("the source did not answer")

    # Patch the fetcher the CLI builds, without changing its signature.
    import stock_data_center.ingestion.market_index as module

    original = module.TaiexHistoryImporter.__init__

    def failing_init(self, engine, **kwargs):
        kwargs["fetcher"] = AlwaysFails()
        original(self, engine, **kwargs)

    module.TaiexHistoryImporter.__init__ = failing_init
    try:
        code = main(
            [
                "--database-url", isolated_database_url,
                "--purpose", "gap_fill",
                "taiex-history", "--month", "2026-01", "--through", "2026-02",
                "--min-interval-seconds", "0",
                "--raw-root", str(tmp_path / "raw"),
            ]
        )
    finally:
        module.TaiexHistoryImporter.__init__ = original
    assert code == 1


def test_the_downgrade_guard_sees_a_quarantined_run_with_no_versions(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """A quarantined date leaves an ingest run and no version at all.

    The blocking foreign key is `ingest_runs(dataset_code, source)` with
    ON DELETE RESTRICT, so counting versions lets the guard pass and the DELETE
    then fails partway — §81 wants the refusal before any mutation.
    """
    from alembic import command
    from sqlalchemy.exc import DBAPIError

    from conftest import alembic_config, alembic_head

    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError):
            run_index(
                engine, tmp_path, adapter=TWSEMarketIndexAdapter(),
                content=TWSE_CLOSED, day=date(2024, 7, 24),
            )
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM market_index_versions")
            ) == 0
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM ingest_runs "
                    "WHERE dataset_code = 'market_index'"
                )
            ) == 1

        with pytest.raises(DBAPIError) as blocked:
            command.downgrade(alembic_config(isolated_database_url), "5e3b8d1a9c42")
        assert blocked.value.orig.sqlstate == "P0001"

        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == alembic_head()
    finally:
        engine.dispose()
