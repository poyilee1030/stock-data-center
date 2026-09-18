"""Step 20-b — importing one institutional market-summary date through the raw-first lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.coverage import ExpectedCoverageService
from stock_data_center.ingestion.adapters import (
    TPExInstitutionalMarketSummaryAdapter,
    TWSEInstitutionalMarketSummaryAdapter,
)
from stock_data_center.ingestion.institutional_summary import (
    InstitutionalMarketSummaryImporter,
)
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    InstitutionalMarketSummaryRequest,
    ResourceQuarantinedError,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.institutional_financing import InstitutionalFinancingService
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_bfi82u_20260911.json").read_bytes()
TPEX = (FIXTURES / "tpex_insti_summary_20260911.json").read_bytes()
TPEX_CLOSURE = (FIXTURES / "tpex_insti_summary_20260710_closed.json").read_bytes()

DAY = date(2026, 9, 11)
CLOSURE = date(2026, 7, 10)
PREVIOUS_HEAD = "c4e7a1d3f9b6"


class StaticFetcher:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def fetch(self, resource):
        return FetchedArtifact(
            content=self.content,
            source_uri=resource.source_uri,
            fetched_at=datetime.now(UTC),
            media_type="application/json",
        )


def run(engine, tmp_path, *, adapter, content, day=DAY,
        purpose=IngestPurpose.GAP_FILL):
    importer = InstitutionalMarketSummaryImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=StaticFetcher(content),
    )
    import_id = uuid4()
    result = importer.run(
        adapter=adapter,
        request=InstitutionalMarketSummaryRequest(day),
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


def summary(connection, *, market, institution, context, source):
    return InstitutionalFinancingService().market_summary(
        connection, market=market, trade_date=DAY, institution=institution,
        context=context, source=source,
    )


def test_one_twse_summary_date_imports_and_resolves(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest = run(engine, tmp_path,
                               adapter=TWSEInstitutionalMarketSummaryAdapter(),
                               content=TWSE)
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 6
        assert manifest.reconciliation["header_variant"] == "bfi82u_4"
        assert manifest.reconciliation["actual_row_count"] == 6
        with engine.connect() as connection:
            record = summary(connection, market="TWSE", institution="合計",
                             context=later(), source="twse_bfi82u")
        assert record is not None
        assert int(record.data["buy"]) == 294671579248
        assert int(record.data["sell"]) == 405930099318
        assert int(record.data["net"]) == -111258520070
        assert record.authoritative_evidence.evidence_type == "release_rule"
        assert record.authoritative_evidence.evidence_source == (
            "exchange_daily_settled@1"
        )
    finally:
        engine.dispose()


def test_one_tpex_summary_date_stores_every_row_including_subtotals(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TPExInstitutionalMarketSummaryAdapter(),
            content=TPEX)
        with engine.connect() as connection:
            names = set(connection.execute(sa.text(
                "SELECT institution FROM institutional_market_summary_versions "
                "WHERE market = 'TPEx'"
            )).scalars())
            record = summary(connection, market="TPEx", institution="自營商合計",
                             context=later(), source="tpex_insti_summary")
        assert names == {
            "外資及陸資合計", "外資及陸資(不含自營商)", "外資自營商", "投信",
            "自營商合計", "自營商(自行買賣)", "自營商(避險)", "三大法人合計*",
        }
        assert int(record.data["net"]) == -1337514519
    finally:
        engine.dispose()


def test_a_summary_date_is_market_invisible_until_the_release_rule(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEInstitutionalMarketSummaryAdapter(),
            content=TWSE)
        # exchange_daily_settled@1: 03:00 Asia/Taipei on D+1 = 19:00 UTC on D.
        known = datetime.now(UTC) + timedelta(minutes=1)

        def at(moment: datetime):
            return summary(
                connection, market="TWSE", institution="投信",
                context=MarketPITContext(
                    information_as_of=moment, knowledge_as_of=known
                ),
                source="twse_bfi82u",
            )

        with engine.connect() as connection:
            early = at(datetime(2026, 9, 11, 18, 59, tzinfo=UTC))
            settled = at(datetime(2026, 9, 11, 19, 0, tzinfo=UTC))
            system = summary(
                connection, market="TWSE", institution="投信",
                context=SystemPITContext(
                    system_as_of=datetime.now(UTC) + timedelta(minutes=1)
                ),
                source="twse_bfi82u",
            )
        assert early is None
        assert settled is not None
        assert system is not None
    finally:
        engine.dispose()


def test_rerunning_the_same_date_creates_no_revision(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TPExInstitutionalMarketSummaryAdapter(),
            content=TPEX)
        repeated, _ = run(engine, tmp_path,
                          adapter=TPExInstitutionalMarketSummaryAdapter(),
                          content=TPEX)
        assert repeated.business_versions_created == 0
        assert repeated.business_versions_deduplicated == 8
        assert repeated.publication_evidence_created == 0
        with engine.connect() as connection:
            assert connection.scalar(sa.text(
                "SELECT count(*) FROM institutional_market_summary_versions"
            )) == 8
            assert connection.scalar(sa.text(
                "SELECT count(*) FROM institutional_market_summary_version_observations"
            )) == 2 * 8
    finally:
        engine.dispose()


def test_a_corrected_amount_revises_that_institution_only(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEInstitutionalMarketSummaryAdapter(),
            content=TWSE)
        payload = json.loads(TWSE)
        payload["data"][2][1] = "18,281,400,622"
        corrected = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result, _ = run(engine, tmp_path,
                        adapter=TWSEInstitutionalMarketSummaryAdapter(),
                        content=corrected)
        assert result.business_versions_created == 1
        assert result.business_versions_deduplicated == 5
        with engine.connect() as connection:
            record = summary(connection, market="TWSE", institution="投信",
                             context=later(), source="twse_bfi82u")
        assert int(record.data["buy"]) == 18281400622
    finally:
        engine.dispose()


def test_the_two_markets_keep_separate_histories(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEInstitutionalMarketSummaryAdapter(),
            content=TWSE)
        run(engine, tmp_path, adapter=TPExInstitutionalMarketSummaryAdapter(),
            content=TPEX)
        with engine.connect() as connection:
            counts = dict(connection.execute(sa.text(
                "SELECT source || '/' || market, count(*) "
                "FROM institutional_market_summary_versions GROUP BY 1"
            )).all())
        assert counts == {"twse_bfi82u/TWSE": 6, "tpex_insti_summary/TPEx": 8}
    finally:
        engine.dispose()


def test_the_closure_date_quarantines_and_keeps_its_raw_artifact(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The legacy archive file for 2026-07-10 is broken (audit §4.3). Re-fetched,
    the official answer is an empty table: the market was closed. The date
    quarantines as no data and keeps the official bytes."""
    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError, match="no_data_for_date"):
            run(engine, tmp_path, adapter=TPExInstitutionalMarketSummaryAdapter(),
                content=TPEX_CLOSURE, day=CLOSURE)
        with engine.connect() as connection:
            assert connection.scalar(sa.text(
                "SELECT count(*) FROM institutional_market_summary_versions"
            )) == 0
            assert connection.scalar(sa.text("SELECT count(*) FROM raw_artifacts")) == 1
    finally:
        engine.dispose()


def test_both_markets_declare_their_expected_summary_coverage(
    isolated_database_url: str,
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        service = ExpectedCoverageService()
        with engine.connect() as connection:
            twse = service.declaration(
                connection, dataset_code="institutional_market_summary", market="TWSE"
            )
            tpex = service.declaration(
                connection, dataset_code="institutional_market_summary", market="TPEx"
            )
        assert twse.source == "twse_bfi82u"
        assert tpex.source == "tpex_insti_summary"
        assert twse.cadence == tpex.cadence == "trading_day"
        assert twse.window_start == tpex.window_start == date(2020, 1, 2)
    finally:
        engine.dispose()


def test_the_cli_imports_one_summary_date(
    isolated_database_url: str, tmp_path: Path
) -> None:
    import stock_data_center.ingestion.institutional_summary as module
    from stock_data_center.ingestion.cli import main

    original = module.InstitutionalMarketSummaryImporter.__init__

    def static_init(self, engine, **kwargs):
        kwargs["fetcher"] = StaticFetcher(TPEX)
        original(self, engine, **kwargs)

    module.InstitutionalMarketSummaryImporter.__init__ = static_init
    try:
        code = main([
            "--database-url", isolated_database_url,
            "--purpose", "gap_fill",
            "institutional-summary", "--source", "tpex_insti_summary",
            "--trade-date", "2026-09-11",
            "--raw-root", str(tmp_path / "raw"),
        ])
    finally:
        module.InstitutionalMarketSummaryImporter.__init__ = original
    assert code == 0
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(sa.text(
                "SELECT count(*) FROM institutional_market_summary_versions"
            )) == 8
    finally:
        engine.dispose()


def test_the_downgrade_guard_sees_a_quarantined_run_with_no_versions(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from alembic import command
    from conftest import alembic_config, alembic_head
    from sqlalchemy.exc import DBAPIError

    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError):
            run(engine, tmp_path, adapter=TPExInstitutionalMarketSummaryAdapter(),
                content=TPEX_CLOSURE, day=CLOSURE)
        with pytest.raises(DBAPIError) as blocked:
            command.downgrade(alembic_config(isolated_database_url), PREVIOUS_HEAD)
        assert blocked.value.orig.sqlstate == "P0001"
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == alembic_head()
    finally:
        engine.dispose()


def test_the_downgrade_removes_only_its_declarations_and_upgrade_restores_them(
    isolated_database_url: str,
) -> None:
    from alembic import command
    from conftest import alembic_config

    config = alembic_config(isolated_database_url)
    engine = sa.create_engine(isolated_database_url)
    query = sa.text(
        "SELECT count(*) FROM dataset_sources "
        "WHERE dataset_code = 'institutional_market_summary' "
        "AND source IN ('twse_bfi82u', 'tpex_insti_summary')"
    )
    flows = sa.text(
        "SELECT count(*) FROM dataset_sources "
        "WHERE dataset_code = 'institutional_investor'"
    )
    try:
        with engine.connect() as connection:
            before = connection.scalar(flows)
        command.downgrade(config, PREVIOUS_HEAD)
        with engine.connect() as connection:
            assert connection.scalar(query) == 0
            assert connection.scalar(flows) == before
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(query) == 2
    finally:
        engine.dispose()


def test_a_reimport_does_not_append_the_rule_a_late_first_capture_withheld(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The #30 review finding, for this dataset: a late first capture
    falsifies the release rule, and a later re-import must still see that."""
    engine = sa.create_engine(isolated_database_url)
    try:
        # The fixture's fetch instant is now, well after 2026-09-11's rule
        # instant (2026-09-11 19:00 UTC), so this first sighting is late.
        run(engine, tmp_path, adapter=TWSEInstitutionalMarketSummaryAdapter(),
            content=TWSE, purpose=IngestPurpose.FIRST_CAPTURE)
        run(engine, tmp_path, adapter=TWSEInstitutionalMarketSummaryAdapter(),
            content=TWSE, purpose=IngestPurpose.GAP_FILL)
        with engine.connect() as connection:
            counts = dict(connection.execute(sa.text(
                "SELECT evidence_type, count(*) FROM publication_evidence "
                "WHERE dataset_code = 'institutional_market_summary' GROUP BY 1"
            )).all())
        assert counts == {"capture_bound": 6}
    finally:
        engine.dispose()
