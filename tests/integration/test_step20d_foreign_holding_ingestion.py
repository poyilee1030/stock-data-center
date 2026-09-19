"""Step 20-d — importing one foreign-holding date through the raw-first lifecycle."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.coverage import ExpectedCoverageService
from stock_data_center.db.metadata import import_manifests, ingest_runs
from stock_data_center.ingestion.adapters import (
    MOPSForeignHoldingAdapter,
    TWSEForeignHoldingAdapter,
)
from stock_data_center.ingestion.foreign_holding import ForeignHoldingImporter
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    ForeignHoldingRequest,
    ResourceQuarantinedError,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.institutional_financing import InstitutionalFinancingService
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_mi_qfiis_20260911.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_mi_qfiis_20260913_closed.json").read_bytes()
MOPS = (FIXTURES / "mops_t13sa150_otc_20260911.html").read_bytes()
MOPS_CLOSED = (FIXTURES / "mops_t13sa150_otc_20260913_closed.html").read_bytes()

DAY = date(2026, 9, 11)
SUNDAY = date(2026, 9, 13)
PREVIOUS_HEAD = "d5f8b2e4a0c7"
SOURCES = ("twse_mi_qfiis", "mops_t13sa150_otc")


class StaticFetcher:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.resources: list[SourceResource] = []

    def fetch(self, resource):
        self.resources.append(resource)
        return FetchedArtifact(
            content=self.content,
            source_uri=resource.source_uri,
            fetched_at=datetime.now(UTC),
            media_type="text/html",
        )


def run(engine, tmp_path, *, adapter, content, day=DAY,
        purpose=IngestPurpose.GAP_FILL, fetcher=None):
    importer = ForeignHoldingImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=fetcher or StaticFetcher(content),
    )
    import_id = uuid4()
    result = importer.run(
        adapter=adapter,
        request=ForeignHoldingRequest(day),
        import_id=import_id,
        git_commit="test-commit",
        purpose=purpose,
    )
    with engine.connect() as connection:
        manifest = importer.manifest(connection, import_id)
    return result, manifest, import_id


def later() -> MarketPITContext:
    moment = datetime.now(UTC) + timedelta(minutes=1)
    return MarketPITContext(information_as_of=moment, knowledge_as_of=moment)


def test_one_twse_holding_date_imports_and_resolves(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest, _ = run(engine, tmp_path,
                                  adapter=TWSEForeignHoldingAdapter(), content=TWSE)
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 1362
        assert manifest.reconciliation["header_variant"] == "mi_qfiis_12"
        with engine.connect() as connection:
            record = InstitutionalFinancingService().foreign_holding(
                connection, security_code="2317", trade_date=DAY,
                context=later(), source="twse_mi_qfiis",
            )
        assert record is not None
        assert int(record.data["issued_shares"]) == 14_028_648_626
        assert int(record.data["held_shares"]) == 5_660_927_279
        assert str(record.data["held_ratio"]).rstrip("0") == "40.35"
        assert record.data["change_reason"] == "4"
        assert record.data["source_last_update_date"] == date(2026, 5, 26)
        assert record.authoritative_evidence.evidence_source == "exchange_daily_settled@1"
    finally:
        engine.dispose()


def test_one_mops_holding_date_imports_with_every_column(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest, _ = run(engine, tmp_path,
                                  adapter=MOPSForeignHoldingAdapter(), content=MOPS)
        assert result.business_versions_created == 1010
        assert manifest.reconciliation["header_variant"] == "t13sa150_11"
        with engine.connect() as connection:
            record = InstitutionalFinancingService().foreign_holding(
                connection, security_code="5009", trade_date=DAY,
                context=later(), source="mops_t13sa150_otc",
            )
            blank = InstitutionalFinancingService().foreign_holding(
                connection, security_code="7839", trade_date=DAY,
                context=later(), source="mops_t13sa150_otc",
            )
        assert int(record.data["issued_shares"]) == 602_471_197
        assert str(record.data["mainland_legal_limit_ratio"]).rstrip("0") == "100."
        assert record.data["source_last_update_date"] == date(2026, 4, 21)
        assert blank.data["source_last_update_date"] is None
    finally:
        engine.dispose()


def test_a_mops_import_records_its_post_request_in_provenance(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The 20-c contract end to end: every date posts to the same URL, so the
    form body must be on the run and in the manifest scope."""
    engine = sa.create_engine(isolated_database_url)
    try:
        fetcher = StaticFetcher(MOPS)
        _, _, import_id = run(engine, tmp_path, adapter=MOPSForeignHoldingAdapter(),
                              content=MOPS, fetcher=fetcher)
        (sent,) = fetcher.resources
        assert sent.method == "POST"
        with engine.connect() as connection:
            scope = connection.execute(
                sa.select(import_manifests.c.source_scope)
                .where(import_manifests.c.import_id == import_id)
            ).scalar_one()
            metadata = connection.execute(
                sa.select(ingest_runs.c.run_metadata)
                .where(ingest_runs.c.run_metadata["import_id"].astext == str(import_id))
            ).scalar_one()
        assert SourceResource.from_json_object(scope["request"]) == sent
        assert SourceResource.from_json_object(metadata["request"]) == sent
    finally:
        engine.dispose()


def test_a_holding_date_is_market_invisible_until_the_release_rule(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=MOPSForeignHoldingAdapter(), content=MOPS)
        known = datetime.now(UTC) + timedelta(minutes=1)

        def at(moment: datetime):
            return InstitutionalFinancingService().foreign_holding(
                connection, security_code="5009", trade_date=DAY,
                context=MarketPITContext(information_as_of=moment, knowledge_as_of=known),
                source="mops_t13sa150_otc",
            )

        with engine.connect() as connection:
            early = at(datetime(2026, 9, 11, 18, 59, tzinfo=UTC))
            settled = at(datetime(2026, 9, 11, 19, 0, tzinfo=UTC))
            system = InstitutionalFinancingService().foreign_holding(
                connection, security_code="5009", trade_date=DAY,
                context=SystemPITContext(system_as_of=known),
                source="mops_t13sa150_otc",
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
        run(engine, tmp_path, adapter=MOPSForeignHoldingAdapter(), content=MOPS)
        repeated, _, _ = run(engine, tmp_path,
                             adapter=MOPSForeignHoldingAdapter(), content=MOPS)
        assert repeated.business_versions_created == 0
        assert repeated.business_versions_deduplicated == 1010
    finally:
        engine.dispose()


def test_a_corrected_value_revises_that_security_only(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=MOPSForeignHoldingAdapter(), content=MOPS)
        corrected = MOPS.replace(b"87,584,515", b"87,584,516", 1)
        result, _, _ = run(engine, tmp_path,
                           adapter=MOPSForeignHoldingAdapter(), content=corrected)
        assert result.business_versions_created == 1
        assert result.business_versions_deduplicated == 1009
    finally:
        engine.dispose()


def test_a_moved_change_reason_link_is_not_a_revision(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """TWSE's link to the filing page carries the query month; next month the
    same code comes with another URL. Only the code is content."""
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEForeignHoldingAdapter(), content=TWSE)
        moved = TWSE.replace(b"&date=11509", b"&date=11510")
        assert moved != TWSE
        result, _, _ = run(engine, tmp_path,
                           adapter=TWSEForeignHoldingAdapter(), content=moved)
        assert result.business_versions_created == 0
    finally:
        engine.dispose()


def test_the_two_markets_keep_separate_histories(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEForeignHoldingAdapter(), content=TWSE)
        run(engine, tmp_path, adapter=MOPSForeignHoldingAdapter(), content=MOPS)
        with engine.connect() as connection:
            counts = dict(connection.execute(sa.text(
                "SELECT source, count(*) FROM foreign_holding_versions GROUP BY source"
            )).all())
        assert counts == {"twse_mi_qfiis": 1362, "mops_t13sa150_otc": 1010}
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "adapter, content",
    [(TWSEForeignHoldingAdapter(), TWSE_CLOSED), (MOPSForeignHoldingAdapter(), MOPS_CLOSED)],
)
def test_a_closed_date_quarantines_and_keeps_its_raw_artifact(
    isolated_database_url: str, tmp_path: Path, adapter, content
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError, match="no_data_for_date"):
            run(engine, tmp_path, adapter=adapter, content=content, day=SUNDAY)
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM foreign_holding_versions")
            ) == 0
            assert connection.scalar(sa.text("SELECT count(*) FROM raw_artifacts")) == 1
    finally:
        engine.dispose()


def test_both_markets_declare_their_expected_holding_coverage(
    isolated_database_url: str,
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        service = ExpectedCoverageService()
        with engine.connect() as connection:
            twse = service.declaration(connection, dataset_code="foreign_holding", market="TWSE")
            tpex = service.declaration(connection, dataset_code="foreign_holding", market="TPEx")
        assert twse.source == "twse_mi_qfiis"
        assert tpex.source == "mops_t13sa150_otc"
        assert twse.cadence == tpex.cadence == "trading_day"
        assert twse.calendar_market == tpex.calendar_market == "TWSE"
        assert twse.window_start == tpex.window_start == date(2020, 1, 2)
    finally:
        engine.dispose()


def test_the_cli_imports_one_holding_date(
    isolated_database_url: str, tmp_path: Path
) -> None:
    import stock_data_center.ingestion.foreign_holding as module
    from stock_data_center.ingestion.cli import main

    original = module.ForeignHoldingImporter.__init__

    def static_init(self, engine, **kwargs):
        kwargs["fetcher"] = StaticFetcher(MOPS)
        original(self, engine, **kwargs)

    module.ForeignHoldingImporter.__init__ = static_init
    try:
        code = main([
            "--database-url", isolated_database_url,
            "--purpose", "gap_fill",
            "foreign-holding", "--source", "mops_t13sa150_otc",
            "--trade-date", "2026-09-11",
            "--raw-root", str(tmp_path / "raw"),
        ])
    finally:
        module.ForeignHoldingImporter.__init__ = original
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
            run(engine, tmp_path, adapter=MOPSForeignHoldingAdapter(),
                content=MOPS_CLOSED, day=SUNDAY)
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
        "WHERE dataset_code = 'foreign_holding' AND source IN :sources"
    ).bindparams(sa.bindparam("sources", value=SOURCES, expanding=True))
    try:
        command.downgrade(config, PREVIOUS_HEAD)
        with engine.connect() as connection:
            assert connection.scalar(query) == 0
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(query) == 2
    finally:
        engine.dispose()


def test_a_reimport_does_not_append_the_rule_a_late_first_capture_withheld(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The #30 leak, for this dataset: a late first sighting withholds the rule,
    and a later gap_fill must not append it."""
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEForeignHoldingAdapter(), content=TWSE,
            purpose=IngestPurpose.FIRST_CAPTURE)
        run(engine, tmp_path, adapter=TWSEForeignHoldingAdapter(), content=TWSE,
            purpose=IngestPurpose.GAP_FILL)
        with engine.connect() as connection:
            counts = dict(connection.execute(sa.text(
                "SELECT evidence_type, count(*) FROM publication_evidence "
                "WHERE dataset_code = 'foreign_holding' GROUP BY 1"
            )).all())
        assert counts == {"capture_bound": 1362}
    finally:
        engine.dispose()
