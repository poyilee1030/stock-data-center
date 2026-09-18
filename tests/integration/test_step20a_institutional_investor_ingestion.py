"""Step 20-a — importing one institutional-flow date through the raw-first lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.coverage import ExpectedCoverageService
from stock_data_center.ingestion.adapters import (
    TPExInstitutionalInvestorAdapter,
    TWSEInstitutionalInvestorAdapter,
)
from stock_data_center.ingestion.institutional_investor import (
    InstitutionalInvestorImporter,
)
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    InstitutionalInvestorRequest,
    ResourceQuarantinedError,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.institutional_financing import InstitutionalFinancingService
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_t86_20260911.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_t86_20260913_closed.json").read_bytes()
TPEX = (FIXTURES / "tpex_insti_daily_trade_20260911.json").read_bytes()

DAY = date(2026, 9, 11)
PREVIOUS_HEAD = "b3d6f0a2c8e5"


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


def run(engine, tmp_path, *, adapter, content, day=DAY):
    importer = InstitutionalInvestorImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=StaticFetcher(content),
    )
    import_id = uuid4()
    result = importer.run(
        adapter=adapter,
        request=InstitutionalInvestorRequest(day),
        import_id=import_id,
        git_commit="test-commit",
        purpose=IngestPurpose.GAP_FILL,
    )
    with engine.connect() as connection:
        manifest = importer.manifest(connection, import_id)
    return result, manifest


def later() -> MarketPITContext:
    moment = datetime.now(UTC) + timedelta(minutes=1)
    return MarketPITContext(information_as_of=moment, knowledge_as_of=moment)


def test_one_twse_flow_date_imports_and_resolves(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest = run(engine, tmp_path,
                               adapter=TWSEInstitutionalInvestorAdapter(), content=TWSE)
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 1330
        assert manifest.reconciliation["header_variant"] == "t86_19"
        assert manifest.reconciliation["actual_row_count"] == 1330
        with engine.connect() as connection:
            record = InstitutionalFinancingService().institutional_investor(
                connection, security_code="2609", trade_date=DAY,
                context=later(), source="twse_t86",
            )
        assert record is not None
        assert int(record.data["foreign_net"]) == 22998285
        assert int(record.data["dealer_net"]) == 647311
        assert int(record.data["total_net"]) == 23747702
        assert record.authoritative_evidence.evidence_type == "release_rule"
        assert record.authoritative_evidence.evidence_source == (
            "exchange_daily_settled@1"
        )
    finally:
        engine.dispose()


def test_a_flow_date_is_market_invisible_until_the_release_rule(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEInstitutionalInvestorAdapter(), content=TWSE)
        # exchange_daily_settled@1: 03:00 Asia/Taipei on D+1 = 19:00 UTC on D.
        known = datetime.now(UTC) + timedelta(minutes=1)

        def at(moment: datetime):
            return InstitutionalFinancingService().institutional_investor(
                connection, security_code="2609", trade_date=DAY,
                context=MarketPITContext(
                    information_as_of=moment, knowledge_as_of=known
                ),
                source="twse_t86",
            )

        with engine.connect() as connection:
            early = at(datetime(2026, 9, 11, 18, 59, tzinfo=UTC))
            settled = at(datetime(2026, 9, 11, 19, 0, tzinfo=UTC))
            system = InstitutionalFinancingService().institutional_investor(
                connection, security_code="2609", trade_date=DAY,
                context=SystemPITContext(
                    system_as_of=datetime.now(UTC) + timedelta(minutes=1)
                ),
                source="twse_t86",
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
        run(engine, tmp_path, adapter=TPExInstitutionalInvestorAdapter(), content=TPEX)
        repeated, _ = run(engine, tmp_path,
                          adapter=TPExInstitutionalInvestorAdapter(), content=TPEX)
        assert repeated.business_versions_created == 0
        assert repeated.business_versions_deduplicated == 894
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM institutional_investor_versions")
            ) == 894
            assert connection.scalar(sa.text(
                "SELECT count(*) FROM institutional_investor_version_observations"
            )) == 2 * 894
    finally:
        engine.dispose()


def test_a_corrected_value_revises_that_security_only(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEInstitutionalInvestorAdapter(), content=TWSE)
        payload = json.loads(TWSE)
        payload["data"][1][8] = "106,000"
        corrected = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result, _ = run(engine, tmp_path,
                        adapter=TWSEInstitutionalInvestorAdapter(), content=corrected)
        assert result.business_versions_created == 1
        assert result.business_versions_deduplicated == 1329
    finally:
        engine.dispose()


def test_the_two_markets_keep_separate_histories(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEInstitutionalInvestorAdapter(), content=TWSE)
        run(engine, tmp_path, adapter=TPExInstitutionalInvestorAdapter(), content=TPEX)
        with engine.connect() as connection:
            counts = dict(connection.execute(sa.text(
                "SELECT source, count(*) FROM institutional_investor_versions "
                "GROUP BY source"
            )).all())
        assert counts == {"twse_t86": 1330, "tpex_insti_daily_trade": 894}
    finally:
        engine.dispose()


def test_a_closed_date_quarantines_and_keeps_its_raw_artifact(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError, match="no_data_for_date"):
            run(engine, tmp_path, adapter=TWSEInstitutionalInvestorAdapter(),
                content=TWSE_CLOSED, day=date(2026, 9, 13))
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM institutional_investor_versions")
            ) == 0
            assert connection.scalar(sa.text("SELECT count(*) FROM raw_artifacts")) == 1
    finally:
        engine.dispose()


def test_both_markets_declare_their_expected_flow_coverage(
    isolated_database_url: str,
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        service = ExpectedCoverageService()
        with engine.connect() as connection:
            twse = service.declaration(
                connection, dataset_code="institutional_investor", market="TWSE"
            )
            tpex = service.declaration(
                connection, dataset_code="institutional_investor", market="TPEx"
            )
        assert twse.source == "twse_t86"
        assert tpex.source == "tpex_insti_daily_trade"
        assert twse.cadence == tpex.cadence == "trading_day"
        assert twse.window_start == tpex.window_start == date(2020, 1, 2)
    finally:
        engine.dispose()


def test_the_cli_imports_one_flow_date(
    isolated_database_url: str, tmp_path: Path
) -> None:
    import stock_data_center.ingestion.institutional_investor as module
    from stock_data_center.ingestion.cli import main

    original = module.InstitutionalInvestorImporter.__init__

    def static_init(self, engine, **kwargs):
        kwargs["fetcher"] = StaticFetcher(TPEX)
        original(self, engine, **kwargs)

    module.InstitutionalInvestorImporter.__init__ = static_init
    try:
        code = main([
            "--database-url", isolated_database_url,
            "--purpose", "gap_fill",
            "institutional-investor", "--source", "tpex_insti_daily_trade",
            "--trade-date", "2026-09-11",
            "--raw-root", str(tmp_path / "raw"),
        ])
    finally:
        module.InstitutionalInvestorImporter.__init__ = original
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
            run(engine, tmp_path, adapter=TWSEInstitutionalInvestorAdapter(),
                content=TWSE_CLOSED, day=date(2026, 9, 13))
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
        "WHERE dataset_code = 'institutional_investor' "
        "AND source IN ('twse_t86', 'tpex_insti_daily_trade')"
    )
    try:
        command.downgrade(config, PREVIOUS_HEAD)
        with engine.connect() as connection:
            assert connection.scalar(query) == 0
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(query) == 2
    finally:
        engine.dispose()


def run_as(engine, tmp_path, *, adapter, content, purpose):
    importer = InstitutionalInvestorImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=StaticFetcher(content),
    )
    importer.run(
        adapter=adapter,
        request=InstitutionalInvestorRequest(DAY),
        import_id=uuid4(),
        git_commit="test-commit",
        purpose=purpose,
    )


def test_a_reimport_does_not_append_the_rule_a_late_first_capture_withheld(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Code review of #30: the policy could not see a stored capture for this
    dataset, so a re-import appended `release_rule` at D+1 03:00 for a version
    whose proven first sighting was days later — a market-PIT leak into
    append-only storage."""
    engine = sa.create_engine(isolated_database_url)
    try:
        # The fixture's fetch instant is now, well after 2026-09-11's rule
        # instant (2026-09-11 19:00 UTC), so this first sighting is late.
        run_as(engine, tmp_path, adapter=TWSEInstitutionalInvestorAdapter(),
               content=TWSE, purpose=IngestPurpose.FIRST_CAPTURE)
        run_as(engine, tmp_path, adapter=TWSEInstitutionalInvestorAdapter(),
               content=TWSE, purpose=IngestPurpose.GAP_FILL)
        with engine.connect() as connection:
            counts = dict(connection.execute(sa.text(
                "SELECT evidence_type, count(*) FROM publication_evidence "
                "WHERE dataset_code = 'institutional_investor' GROUP BY 1"
            )).all())
        assert counts == {"capture_bound": 1330}
    finally:
        engine.dispose()


def test_every_dataset_accepting_capture_bound_can_read_its_stored_captures(
    isolated_database_url: str,
) -> None:
    """The same gap for any later dataset: a source that accepts capture_bound
    must have a DATASET_TARGETS entry, or falsification cannot see it."""
    from stock_data_center.evidence.policy import DATASET_TARGETS

    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            datasets = set(connection.execute(sa.text(
                "SELECT DISTINCT dataset_code FROM dataset_sources "
                "WHERE 'capture_bound' = ANY(accepted_evidence_types)"
            )).scalars())
        assert datasets - set(DATASET_TARGETS) == set()
    finally:
        engine.dispose()
