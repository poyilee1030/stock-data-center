"""Step 18-c — importing one valuation date through the raw-first lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.coverage import ExpectedCoverageService
from stock_data_center.ingestion.adapters import (
    TPExOfficialValuationAdapter,
    TWSEOfficialValuationAdapter,
)
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    OfficialValuationRequest,
    ResourceQuarantinedError,
)
from stock_data_center.ingestion.official_valuation import (
    OfficialValuationImporter,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_reference import MarketReferenceService
from stock_data_center.pit import MarketPITContext
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_bwibbu_d_20260911.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_bwibbu_d_20260913_closed.json").read_bytes()
TPEX = (FIXTURES / "tpex_peqrydate_20260911.json").read_bytes()
TPEX_ZERO = (FIXTURES / "tpex_peqrydate_20241204.json").read_bytes()

DAY = date(2026, 9, 11)


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


def run(engine, tmp_path, *, adapter, content, day=DAY, import_id=None):
    importer = OfficialValuationImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=StaticFetcher(content),
    )
    used_id = import_id or uuid4()
    result = importer.run(
        adapter=adapter,
        request=OfficialValuationRequest(day),
        import_id=used_id,
        git_commit="test-commit",
        purpose=IngestPurpose.GAP_FILL,
    )
    with engine.connect() as connection:
        manifest = importer.manifest(connection, used_id)
    return result, manifest


def later() -> MarketPITContext:
    moment = datetime.now(UTC) + timedelta(minutes=1)
    return MarketPITContext(information_as_of=moment, knowledge_as_of=moment)


def test_one_twse_valuation_date_imports_and_resolves(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest = run(engine, tmp_path, adapter=TWSEOfficialValuationAdapter(),
                               content=TWSE)
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 1080
        assert manifest.reconciliation["header_variant"] == "bwibbu_8"
        assert manifest.result_counts["rejected_quarantined_count"] == 0
        with engine.connect() as connection:
            record = MarketReferenceService().official_valuation(
                connection, security_code="2330", trade_date=DAY,
                context=later(), source="twse_bwibbu_d",
            )
        assert record is not None
        assert float(record.data["pe_ratio"]) == 27.94
        assert record.data["dividend_year"] == 2025
        assert record.data["report_period"] == "2026Q2"
        assert record.data["dividend_per_share"] is None
        assert record.authoritative_evidence.evidence_type == "release_rule"
        assert record.authoritative_evidence.evidence_source == (
            "exchange_daily_settled@1"
        )
    finally:
        engine.dispose()


def test_a_first_day_zero_ratio_is_stored_as_not_computed(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest = run(engine, tmp_path, adapter=TPExOfficialValuationAdapter(),
                               content=TPEX_ZERO, day=date(2024, 12, 4))
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 831
        assert manifest.result_counts["rejected_quarantined_count"] == 0
        with engine.connect() as connection:
            record = MarketReferenceService().official_valuation(
                connection, security_code="6720", trade_date=date(2024, 12, 4),
                context=later(), source="tpex_pe_qry_date",
            )
        assert record is not None
        assert record.data["pe_ratio"] is None
        assert record.data["pb_ratio"] is None
        assert float(record.data["dividend_yield"]) == 1.03
    finally:
        engine.dispose()


def test_a_negative_ratio_row_is_quarantined_and_the_date_still_imports(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        payload = json.loads(TPEX_ZERO)
        row = next(r for r in payload["tables"][0]["data"] if r[0] == "6720")
        row[2] = "-3.00"
        negative = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result, manifest = run(engine, tmp_path, adapter=TPExOfficialValuationAdapter(),
                               content=negative, day=date(2024, 12, 4))
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 830
        assert manifest.result_counts["rejected_quarantined_count"] == 1
        with engine.connect() as connection:
            quarantined = connection.execute(sa.text(
                "SELECT reason_code, reason_detail, raw_artifact_id IS NOT NULL "
                "FROM import_quarantine"
            )).one()
            stored = connection.scalar(sa.text(
                "SELECT count(*) FROM official_valuation_versions v "
                "JOIN security s ON s.id = v.security_id "
                "WHERE s.security_code = '6720'"
            ))
        assert quarantined[0] == "nonpositive_ratio"
        assert "(6720)" in quarantined[1]
        assert "2024-12-04" in quarantined[1]
        assert quarantined[2]
        assert stored == 0
    finally:
        engine.dispose()


def test_tpex_stores_its_per_share_dividend(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TPExOfficialValuationAdapter(), content=TPEX)
        with engine.connect() as connection:
            record = MarketReferenceService().official_valuation(
                connection, security_code="1240", trade_date=DAY,
                context=later(), source="tpex_pe_qry_date",
            )
        assert record is not None
        assert float(record.data["dividend_per_share"]) == 0.5
        assert record.data["report_period"] == "2026Q2"
    finally:
        engine.dispose()


def test_rerunning_the_same_date_creates_no_revision(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEOfficialValuationAdapter(), content=TWSE)
        repeated, _ = run(engine, tmp_path, adapter=TWSEOfficialValuationAdapter(),
                          content=TWSE)
        assert repeated.business_versions_created == 0
        assert repeated.business_versions_deduplicated == 1080
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM official_valuation_versions")
            ) == 1080
    finally:
        engine.dispose()


def test_a_corrected_value_revises_that_security_only(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEOfficialValuationAdapter(), content=TWSE)
        payload = json.loads(TWSE)
        payload["data"][1][6] = "0.69"
        corrected = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result, _ = run(engine, tmp_path, adapter=TWSEOfficialValuationAdapter(),
                        content=corrected)
        assert result.business_versions_created == 1
        assert result.business_versions_deduplicated == 1079
    finally:
        engine.dispose()


def test_the_two_markets_keep_separate_histories(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEOfficialValuationAdapter(), content=TWSE)
        run(engine, tmp_path, adapter=TPExOfficialValuationAdapter(), content=TPEX)
        with engine.connect() as connection:
            counts = dict(connection.execute(sa.text(
                "SELECT source, count(*) FROM official_valuation_versions "
                "GROUP BY source"
            )).all())
        assert counts == {"twse_bwibbu_d": 1080, "tpex_pe_qry_date": 885}
    finally:
        engine.dispose()


def test_a_closed_date_quarantines_and_keeps_its_raw_artifact(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError, match="no_data_for_date"):
            run(engine, tmp_path, adapter=TWSEOfficialValuationAdapter(),
                content=TWSE_CLOSED, day=date(2026, 9, 13))
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM official_valuation_versions")
            ) == 0
            assert connection.scalar(sa.text("SELECT count(*) FROM raw_artifacts")) == 1
    finally:
        engine.dispose()


def test_both_markets_declare_their_expected_valuation_coverage(
    isolated_database_url: str,
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        service = ExpectedCoverageService()
        with engine.connect() as connection:
            twse = service.declaration(
                connection, dataset_code="official_valuation", market="TWSE"
            )
            tpex = service.declaration(
                connection, dataset_code="official_valuation", market="TPEx"
            )
        assert twse.source == "twse_bwibbu_d"
        assert tpex.source == "tpex_pe_qry_date"
        assert twse.cadence == tpex.cadence == "trading_day"
        assert twse.window_start == date(2020, 1, 2)
    finally:
        engine.dispose()


def test_the_cli_imports_one_valuation_date(
    isolated_database_url: str, tmp_path: Path
) -> None:
    import stock_data_center.ingestion.official_valuation as module
    from stock_data_center.ingestion.cli import main

    original = module.OfficialValuationImporter.__init__

    def static_init(self, engine, **kwargs):
        kwargs["fetcher"] = StaticFetcher(TWSE)
        original(self, engine, **kwargs)

    module.OfficialValuationImporter.__init__ = static_init
    try:
        code = main([
            "--database-url", isolated_database_url,
            "--purpose", "gap_fill",
            "official-valuation", "--source", "twse_bwibbu_d",
            "--trade-date", "2026-09-11",
            "--raw-root", str(tmp_path / "raw"),
        ])
    finally:
        module.OfficialValuationImporter.__init__ = original
    assert code == 0


def test_the_downgrade_guard_sees_a_quarantined_run_with_no_versions(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from alembic import command
    from conftest import alembic_config, alembic_head
    from sqlalchemy.exc import DBAPIError

    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError):
            run(engine, tmp_path, adapter=TWSEOfficialValuationAdapter(),
                content=TWSE_CLOSED, day=date(2026, 9, 13))
        with pytest.raises(DBAPIError) as blocked:
            command.downgrade(alembic_config(isolated_database_url), "9f3d7c2e5a41")
        assert blocked.value.orig.sqlstate == "P0001"
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == alembic_head()
    finally:
        engine.dispose()
