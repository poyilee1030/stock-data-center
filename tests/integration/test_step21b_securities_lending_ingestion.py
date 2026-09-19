"""Step 21-b — importing one securities-lending date through the raw-first lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.coverage import ExpectedCoverageService
from stock_data_center.ingestion.adapters import (
    TPExSecuritiesLendingAdapter,
    TWSESecuritiesLendingAdapter,
)
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    ResourceQuarantinedError,
    SecuritiesLendingRequest,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.ingestion.securities_lending import SecuritiesLendingImporter
from stock_data_center.institutional_financing import InstitutionalFinancingService
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_twt93u_20260911.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_twt93u_20260913_closed.json").read_bytes()
TPEX = (FIXTURES / "tpex_margin_sbl_20260911.json").read_bytes()
TPEX_CLOSED = (FIXTURES / "tpex_margin_sbl_20260913_closed.json").read_bytes()

DAY = date(2026, 9, 11)
SUNDAY = date(2026, 9, 13)
PREVIOUS_HEAD = "b9d1f3a5c7e2"
SOURCES = ("twse_twt93u", "tpex_margin_sbl")
TWSE_ROWS, TPEX_ROWS = 1301, 932


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


def run(engine, tmp_path, *, adapter, content, day=DAY, purpose=IngestPurpose.GAP_FILL):
    importer = SecuritiesLendingImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=StaticFetcher(content),
    )
    import_id = uuid4()
    result = importer.run(
        adapter=adapter,
        request=SecuritiesLendingRequest(day),
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


def lending(connection, code: str, source: str, context=None):
    return InstitutionalFinancingService().securities_lending(
        connection, security_code=code, trade_date=DAY,
        context=context or later(), source=source,
    )


def test_one_twse_lending_date_imports_and_resolves_in_shares(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest = run(engine, tmp_path, adapter=TWSESecuritiesLendingAdapter(), content=TWSE)
        assert manifest.status == "succeeded"
        assert result.business_versions_created == TWSE_ROWS
        assert manifest.reconciliation["header_variant"] == "twt93u_15"
        with engine.connect() as connection:
            record = lending(connection, "2330", "twse_twt93u")
        assert int(record.data["previous_balance"]) == 16_191_514
        assert int(record.data["borrowed"]) == 61_000
        assert int(record.data["returned"]) == 8_000
        assert int(record.data["balance"]) == 16_244_514
        assert int(record.data["next_limit"]) == 6_483_092_516
        assert int(record.data["next_available_limit"]) == 6_756_118
        assert record.data["note"] == "X"
        assert record.authoritative_evidence.evidence_source == "exchange_daily_settled@1"
    finally:
        engine.dispose()


def test_one_tpex_lending_date_imports(isolated_database_url: str, tmp_path: Path) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest = run(engine, tmp_path, adapter=TPExSecuritiesLendingAdapter(), content=TPEX)
        assert result.business_versions_created == TPEX_ROWS
        assert manifest.reconciliation["header_variant"] == "margin_sbl_15"
        with engine.connect() as connection:
            record = lending(connection, "5009", "tpex_margin_sbl")
        assert int(record.data["balance"]) == 23_036_000
        assert int(record.data["next_limit"]) == 150_617_799
        assert record.data["note"] is None
    finally:
        engine.dispose()


def test_a_lending_date_is_market_invisible_until_the_release_rule(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSESecuritiesLendingAdapter(), content=TWSE)
        known = datetime.now(UTC) + timedelta(minutes=1)

        def at(moment: datetime):
            return lending(
                connection, "2330", "twse_twt93u",
                MarketPITContext(information_as_of=moment, knowledge_as_of=known),
            )

        with engine.connect() as connection:
            early = at(datetime(2026, 9, 11, 18, 59, tzinfo=UTC))
            settled = at(datetime(2026, 9, 11, 19, 0, tzinfo=UTC))
            system = lending(connection, "2330", "twse_twt93u", SystemPITContext(system_as_of=known))
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
        run(engine, tmp_path, adapter=TPExSecuritiesLendingAdapter(), content=TPEX)
        repeated, _ = run(engine, tmp_path, adapter=TPExSecuritiesLendingAdapter(), content=TPEX)
        assert repeated.business_versions_created == 0
        assert repeated.business_versions_deduplicated == TPEX_ROWS
    finally:
        engine.dispose()


def test_a_corrected_value_revises_that_security_only(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSESecuritiesLendingAdapter(), content=TWSE)
        payload = json.loads(TWSE)
        payload["data"][0][12] = "1"
        corrected = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result, _ = run(engine, tmp_path, adapter=TWSESecuritiesLendingAdapter(), content=corrected)
        assert result.business_versions_created == 1
        assert result.business_versions_deduplicated == TWSE_ROWS - 1
    finally:
        engine.dispose()


def test_a_changed_note_alone_is_a_revision(isolated_database_url: str, tmp_path: Path) -> None:
    """The status note is business content: 停券 starting is a change of state."""
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TPExSecuritiesLendingAdapter(), content=TPEX)
        payload = json.loads(TPEX)
        row = next(r for r in payload["tables"][0]["data"] if r[0] == "5009")
        row[14] = "X"
        result, _ = run(
            engine, tmp_path, adapter=TPExSecuritiesLendingAdapter(),
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        assert result.business_versions_created == 1
        with engine.connect() as connection:
            assert lending(connection, "5009", "tpex_margin_sbl").data["note"] == "X"
    finally:
        engine.dispose()


def test_the_two_markets_keep_separate_histories(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSESecuritiesLendingAdapter(), content=TWSE)
        run(engine, tmp_path, adapter=TPExSecuritiesLendingAdapter(), content=TPEX)
        with engine.connect() as connection:
            counts = dict(connection.execute(sa.text(
                "SELECT source, count(*) FROM securities_lending_versions GROUP BY source"
            )).all())
        assert counts == {"twse_twt93u": TWSE_ROWS, "tpex_margin_sbl": TPEX_ROWS}
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "adapter, content",
    [(TWSESecuritiesLendingAdapter(), TWSE_CLOSED), (TPExSecuritiesLendingAdapter(), TPEX_CLOSED)],
)
def test_a_closed_date_quarantines_and_keeps_its_raw_artifact(
    isolated_database_url: str, tmp_path: Path, adapter, content
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError, match="no_data_for_date"):
            run(engine, tmp_path, adapter=adapter, content=content, day=SUNDAY)
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT count(*) FROM securities_lending_versions")) == 0
            assert connection.scalar(sa.text("SELECT count(*) FROM raw_artifacts")) == 1
    finally:
        engine.dispose()


def test_both_markets_declare_their_expected_lending_coverage(
    isolated_database_url: str,
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        service = ExpectedCoverageService()
        with engine.connect() as connection:
            twse = service.declaration(connection, dataset_code="securities_lending", market="TWSE")
            tpex = service.declaration(connection, dataset_code="securities_lending", market="TPEx")
        assert (twse.source, tpex.source) == SOURCES
        assert twse.cadence == tpex.cadence == "trading_day"
        assert twse.calendar_market == tpex.calendar_market == "TWSE"
        assert twse.window_start == tpex.window_start == date(2020, 1, 2)
    finally:
        engine.dispose()


def test_the_cli_imports_one_lending_date(isolated_database_url: str, tmp_path: Path) -> None:
    import stock_data_center.ingestion.securities_lending as module
    from stock_data_center.ingestion.cli import main

    original = module.SecuritiesLendingImporter.__init__

    def static_init(self, engine, **kwargs):
        kwargs["fetcher"] = StaticFetcher(TPEX)
        original(self, engine, **kwargs)

    module.SecuritiesLendingImporter.__init__ = static_init
    try:
        code = main([
            "--database-url", isolated_database_url,
            "--purpose", "gap_fill",
            "securities-lending", "--source", "tpex_margin_sbl",
            "--trade-date", "2026-09-11",
            "--raw-root", str(tmp_path / "raw"),
        ])
    finally:
        module.SecuritiesLendingImporter.__init__ = original
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
            run(engine, tmp_path, adapter=TWSESecuritiesLendingAdapter(),
                content=TWSE_CLOSED, day=SUNDAY)
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
        "WHERE dataset_code = 'securities_lending' AND source IN :sources"
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
    """The #30 leak, for this dataset."""
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TPExSecuritiesLendingAdapter(), content=TPEX,
            purpose=IngestPurpose.FIRST_CAPTURE)
        run(engine, tmp_path, adapter=TPExSecuritiesLendingAdapter(), content=TPEX,
            purpose=IngestPurpose.GAP_FILL)
        with engine.connect() as connection:
            counts = dict(connection.execute(sa.text(
                "SELECT evidence_type, count(*) FROM publication_evidence "
                "WHERE dataset_code = 'securities_lending' GROUP BY 1"
            )).all())
        assert counts == {"capture_bound": TPEX_ROWS}
    finally:
        engine.dispose()


def test_the_manifest_records_that_nothing_is_converted(isolated_database_url: str) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        importer = SecuritiesLendingImporter(engine)
        for adapter in (TWSESecuritiesLendingAdapter(), TPExSecuritiesLendingAdapter()):
            described = importer._source_semantics(adapter)
            assert described["quantity_unit"] == "shares, as published"
            assert described["columns"] == dict(adapter.columns)
    finally:
        engine.dispose()
