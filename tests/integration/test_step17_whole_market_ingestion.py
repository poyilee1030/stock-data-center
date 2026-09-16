"""Step 17-a — importing one whole market-date through the raw-first lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.coverage import ExpectedCoverageService
from stock_data_center.ingestion.adapters import (
    TPExWholeMarketDailyAdapter,
    TWSEWholeMarketDailyAdapter,
)
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    ResourceQuarantinedError,
    WholeMarketDailyRequest,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.ingestion.whole_market_daily import WholeMarketDailyImporter
from stock_data_center.market_data import MarketDataService
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.provenance import IngestPurpose


pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE_DATE = date(2026, 9, 11)
TPEX_DATE = date(2026, 9, 11)
TWSE_BYTES = (FIXTURES / "twse_mi_index_allbut0999_20260911.json").read_bytes()
TPEX_BYTES = (FIXTURES / "tpex_otc_quotes_20260911.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_mi_index_allbut0999_20240724_closed.json").read_bytes()

TWSE_ROWS = 1379
TPEX_ROWS = 1012


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


def run_import(
    engine,
    tmp_path: Path,
    *,
    adapter,
    content: bytes,
    trade_date: date,
    import_id=None,
    purpose: IngestPurpose = IngestPurpose.GAP_FILL,
    fetcher=None,
):
    used_fetcher = fetcher or StaticFetcher(content)
    importer = WholeMarketDailyImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=used_fetcher,
    )
    used_id = import_id or uuid4()
    result = importer.run(
        adapter=adapter,
        request=WholeMarketDailyRequest(trade_date),
        import_id=used_id,
        git_commit="test-commit",
        purpose=purpose,
    )
    with engine.connect() as connection:
        manifest = importer.manifest(connection, used_id)
    return result, manifest, used_fetcher


def test_one_twse_trade_date_imports_end_to_end(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest, fetcher = run_import(
            engine,
            tmp_path,
            adapter=TWSEWholeMarketDailyAdapter(),
            content=TWSE_BYTES,
            trade_date=TWSE_DATE,
        )
        assert fetcher.calls == 1
        assert manifest.status == "succeeded"
        assert result.normalized_rows == TWSE_ROWS
        assert result.business_versions_created == TWSE_ROWS
        assert result.business_versions_deduplicated == 0
        assert result.coverage_start == TWSE_DATE
        assert result.coverage_end == TWSE_DATE
        assert manifest.result_counts["raw_artifact_count"] == 1
        assert manifest.reconciliation["security_count"] == TWSE_ROWS
        assert manifest.reconciliation["source_units"]["traded_quantity"] == "share"
        # TWSE declares 單位：元、股 for its whole table, so its disclosed
        # bid/ask level is already shares and nothing converts it. TPEx is the
        # market that labels that column in lots.
        assert manifest.reconciliation["source_units"]["disclosed_volume"] == "share"
        # The manifest says what availability time actually rests on. "unknown"
        # stopped being true for daily_price at Step 15-c.
        assert manifest.reconciliation["publication_time"] == (
            "exchange_daily_settled@1"
        )
        assert manifest.reconciliation["availability_time_evidence"] == [
            "release_rule"
        ]

        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM daily_price_versions "
                    "WHERE source = 'twse_mi_index'"
                )
            ) == TWSE_ROWS
            record = MarketDataService().daily_price(
                connection,
                security_code="2330",
                trade_date=TWSE_DATE,
                context=SystemPITContext(datetime.now(UTC) + timedelta(minutes=1)),
                source="twse_mi_index",
            )
            assert record is not None
            assert record.data["close_price"] == 2410
            assert record.data["price_change"] == -40
            assert record.data["last_bid_volume"] == 1078
    finally:
        engine.dispose()


def test_one_tpex_trade_date_imports_end_to_end(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest, _ = run_import(
            engine,
            tmp_path,
            adapter=TPExWholeMarketDailyAdapter(),
            content=TPEX_BYTES,
            trade_date=TPEX_DATE,
        )
        assert manifest.status == "succeeded"
        assert result.business_versions_created == TPEX_ROWS
        assert manifest.reconciliation["header_variant"] == "volume_in_lots"
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM daily_price_versions "
                    "WHERE source = 'tpex_otc_quotes'"
                )
            ) == TPEX_ROWS
    finally:
        engine.dispose()


def test_rerunning_the_same_date_creates_no_revision(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run_import(
            engine,
            tmp_path,
            adapter=TWSEWholeMarketDailyAdapter(),
            content=TWSE_BYTES,
            trade_date=TWSE_DATE,
        )
        repeated, manifest, _ = run_import(
            engine,
            tmp_path,
            adapter=TWSEWholeMarketDailyAdapter(),
            content=TWSE_BYTES,
            trade_date=TWSE_DATE,
        )
        assert repeated.business_versions_created == 0
        assert repeated.business_versions_deduplicated == TWSE_ROWS
        assert repeated.publication_evidence_created == 0
        assert repeated.publication_evidence_deduplicated == TWSE_ROWS
        assert manifest.status == "succeeded"
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM daily_price_versions")
            ) == TWSE_ROWS
            # The repeated fetch stays auditable even though nothing changed.
            assert connection.scalar(
                sa.text("SELECT count(*) FROM raw_artifact_observations")
            ) == 2
    finally:
        engine.dispose()


def test_a_corrected_value_creates_a_revision_only_for_the_changed_security(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run_import(
            engine,
            tmp_path,
            adapter=TWSEWholeMarketDailyAdapter(),
            content=TWSE_BYTES,
            trade_date=TWSE_DATE,
        )
        payload = json.loads(TWSE_BYTES)
        table = next(
            item
            for item in payload["tables"]
            if item.get("fields") and item["fields"][0] == "證券代號"
        )
        for row in table["data"]:
            if row[0] == "2330":
                row[8] = "2409.00"  # inside the published high/low, so only close moves
        corrected = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        result, _, _ = run_import(
            engine,
            tmp_path,
            adapter=TWSEWholeMarketDailyAdapter(),
            content=corrected,
            trade_date=TWSE_DATE,
        )
        assert result.business_versions_created == 1
        assert result.business_versions_deduplicated == TWSE_ROWS - 1
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM daily_price_versions v "
                    "JOIN security s ON s.id = v.security_id "
                    "WHERE s.security_code = '2330'"
                )
            ) == 2
    finally:
        engine.dispose()


def test_imported_rows_resolve_by_the_release_rule_their_source_declared(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Step 15-c opted `daily_price` in; 17-a opts in the two new source codes."""
    engine = sa.create_engine(isolated_database_url)
    try:
        run_import(
            engine,
            tmp_path,
            adapter=TWSEWholeMarketDailyAdapter(),
            content=TWSE_BYTES,
            trade_date=TWSE_DATE,
        )
        with engine.connect() as connection:
            market = MarketDataService().daily_price(
                connection,
                security_code="2330",
                trade_date=TWSE_DATE,
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
            # Trade date 2026-09-11 becomes knowable at 03:00 on 09-12 Taipei.
            assert market.authoritative_evidence.published_at == datetime(
                2026, 9, 11, 19, 0, tzinfo=UTC
            )
    finally:
        engine.dispose()


def test_a_gap_fill_import_claims_no_capture_bound(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, _, _ = run_import(
            engine,
            tmp_path,
            adapter=TWSEWholeMarketDailyAdapter(),
            content=TWSE_BYTES,
            trade_date=TWSE_DATE,
            purpose=IngestPurpose.GAP_FILL,
        )
        assert result.unknown_publication_observations == 0
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM publication_evidence "
                    "WHERE evidence_type = 'capture_bound'"
                )
            ) == 0
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM publication_evidence "
                    "WHERE evidence_type = 'release_rule'"
                )
            ) == TWSE_ROWS
    finally:
        engine.dispose()


def test_a_first_capture_import_claims_a_capture_bound_per_version(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run_import(
            engine,
            tmp_path,
            adapter=TWSEWholeMarketDailyAdapter(),
            content=TWSE_BYTES,
            trade_date=TWSE_DATE,
            purpose=IngestPurpose.FIRST_CAPTURE,
        )
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM publication_evidence "
                    "WHERE evidence_type = 'capture_bound'"
                )
            ) == TWSE_ROWS
    finally:
        engine.dispose()


def test_the_whole_market_source_does_not_flap_against_the_step_9_pilot(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Two endpoints, two source codes, two independent histories (CLAUDE.md §30)."""
    engine = sa.create_engine(isolated_database_url)
    try:
        run_import(
            engine,
            tmp_path,
            adapter=TWSEWholeMarketDailyAdapter(),
            content=TWSE_BYTES,
            trade_date=TWSE_DATE,
        )
        with engine.connect() as connection:
            sources = set(
                connection.scalars(
                    sa.text("SELECT DISTINCT source FROM daily_price_versions")
                )
            )
        assert sources == {"twse_mi_index"}
        assert "twse" not in sources
    finally:
        engine.dispose()


def test_a_closed_date_quarantines_and_keeps_its_raw_artifact(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The reason code survives into the quarantine row, which is the point.

    Step 17-c walks a date range; it has to tell "the source has nothing for
    this date" from a parse failure without re-deriving the calendar, and it
    reads that off `import_quarantine.reason_code` rather than the message.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError, match="no_data_for_date"):
            run_import(
                engine,
                tmp_path,
                adapter=TWSEWholeMarketDailyAdapter(),
                content=TWSE_CLOSED,
                trade_date=date(2024, 7, 24),
            )
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM daily_price_versions")
            ) == 0
            assert connection.scalar(
                sa.text("SELECT count(*) FROM raw_artifacts")
            ) == 1
            assert connection.scalar(
                sa.text("SELECT count(*) FROM import_quarantine")
            ) == 1
            assert connection.scalar(
                sa.text("SELECT reason_code FROM import_quarantine")
            ) == "no_data_for_date"
    finally:
        engine.dispose()


def test_both_markets_declare_their_expected_daily_price_coverage(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        service = ExpectedCoverageService()
        with engine.connect() as connection:
            twse = service.declaration(
                connection, dataset_code="daily_price", market="TWSE"
            )
            tpex = service.declaration(
                connection, dataset_code="daily_price", market="TPEx"
            )
        assert twse.source == "twse_mi_index"
        assert twse.cadence == "trading_day"
        assert twse.calendar_market == "TWSE"
        assert twse.window_start == date(2020, 1, 2)
        assert tpex.source == "tpex_otc_quotes"
        # No official TPEx calendar exists; Step 16 measured the equivalence.
        assert tpex.calendar_market == "TWSE"
    finally:
        engine.dispose()


def test_downgrade_refuses_to_orphan_imported_whole_market_history(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Removing the source policy under imported history would leave rows
    nothing can resolve. The downgrade refuses before it mutates anything."""
    from alembic import command
    from sqlalchemy.exc import DBAPIError

    from conftest import alembic_config, alembic_head

    engine = sa.create_engine(isolated_database_url)
    try:
        run_import(
            engine,
            tmp_path,
            adapter=TWSEWholeMarketDailyAdapter(),
            content=TWSE_BYTES,
            trade_date=TWSE_DATE,
        )
        with pytest.raises(DBAPIError) as blocked:
            command.downgrade(alembic_config(isolated_database_url), "4d9f2a6c8b17")
        assert blocked.value.orig.sqlstate == "P0001"
        assert "whole-market price sources" in str(blocked.value)

        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == alembic_head()
            assert connection.scalar(
                sa.text("SELECT count(*) FROM daily_price_versions")
            ) == TWSE_ROWS
    finally:
        engine.dispose()


def test_a_market_date_larger_than_the_bind_parameter_ceiling_still_writes(
    isolated_database_url: str,
) -> None:
    """PostgreSQL binds at most 65,535 parameters per statement.

    The observation insert binds 21 per row, so one statement tops out at 3,120
    securities — above today's 1,379 TWSE rows, but it is a hard failure at
    import time rather than a slow path, and the listed universe only grows.
    """
    from decimal import Decimal

    from stock_data_center.market_data import (
        DailyPriceObservation,
        LineageRef,
        MarketDataWriter,
    )

    engine = sa.create_engine(isolated_database_url)
    writer = MarketDataWriter()
    codes = [f"T{index:05d}" for index in range(4000)]
    try:
        with engine.begin() as connection:
            run_id = connection.scalar(
                sa.text(
                    "INSERT INTO ingest_runs "
                    "(dataset_code, source, status, started_at, purpose) "
                    "VALUES ('daily_price', 'twse_mi_index', 'running', now(), "
                    "        'gap_fill') "
                    "RETURNING id"
                )
            )
            artifact_id = connection.scalar(
                sa.text(
                    "INSERT INTO raw_artifacts "
                    "(raw_artifact_hash, storage_uri, byte_size, media_type) "
                    "VALUES (:h, :uri, 1, 'application/json') RETURNING id"
                ),
                {"h": "c" * 64, "uri": f"file://{'c' * 64}"},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO raw_artifact_observations "
                    "(raw_artifact_id, ingest_run_id, source_uri, fetched_at, "
                    " artifact_origin) "
                    "VALUES (:a, :r, 'https://x', now(), 'official_fetch')"
                ),
                {"a": artifact_id, "r": run_id},
            )
            security_ids = writer.register_securities(
                connection, security_codes=codes
            )
            assert len(security_ids) == 4000

            written = writer.append_daily_prices(
                connection,
                source="twse_mi_index",
                trade_date=TWSE_DATE,
                observations=[
                    (
                        security_ids[code],
                        DailyPriceObservation(
                            trade_date=TWSE_DATE, close_price=Decimal("10.00")
                        ),
                    )
                    for code in codes
                ],
                lineage=LineageRef(artifact_id, run_id),
            )

        assert len(written) == 4000
        assert all(item.created for item in written)
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM daily_price_versions "
                    "WHERE source = 'twse_mi_index'"
                )
            ) == 4000
    finally:
        engine.dispose()
