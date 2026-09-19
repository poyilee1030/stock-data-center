"""Step 21-a — importing one margin-trading date through the raw-first lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.coverage import ExpectedCoverageService
from stock_data_center.ingestion.adapters import (
    TPExMarginTradingAdapter,
    TWSEMarginTradingAdapter,
)
from stock_data_center.ingestion.margin_trading import MarginTradingImporter
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    MarginTradingRequest,
    ResourceQuarantinedError,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.institutional_financing import InstitutionalFinancingService
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_mi_margn_20260911.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_mi_margn_20260913_closed.json").read_bytes()
TPEX = (FIXTURES / "tpex_margin_balance_20260911.json").read_bytes()
TPEX_CLOSED = (FIXTURES / "tpex_margin_balance_20260913_closed.json").read_bytes()

DAY = date(2026, 9, 11)
SUNDAY = date(2026, 9, 13)
PREVIOUS_HEAD = "f2b6d8a4c1e9"
SOURCES = ("twse_mi_margn", "tpex_margin_balance")


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
    importer = MarginTradingImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=StaticFetcher(content),
    )
    import_id = uuid4()
    result = importer.run(
        adapter=adapter,
        request=MarginTradingRequest(day),
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


def test_one_twse_margin_date_imports_and_resolves_in_shares(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest = run(engine, tmp_path, adapter=TWSEMarginTradingAdapter(), content=TWSE)
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 1297
        assert manifest.reconciliation["header_variant"] == "mi_margn_16"
        with engine.connect() as connection:
            record = InstitutionalFinancingService().margin_trading(
                connection, security_code="2330", trade_date=DAY,
                context=later(), source="twse_mi_margn",
            )
        assert int(record.data["margin_buy"]) == 978_000
        assert int(record.data["margin_balance"]) == 28_901_000
        assert int(record.data["short_stock_repayment"]) == 2_000
        assert record.data["margin_utilization_ratio"] is None
        assert record.authoritative_evidence.evidence_source == "exchange_daily_settled@1"
    finally:
        engine.dispose()


def test_one_tpex_margin_date_imports_with_its_utilization(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest = run(engine, tmp_path, adapter=TPExMarginTradingAdapter(), content=TPEX)
        assert result.business_versions_created == 920
        assert manifest.reconciliation["header_variant"] == "margin_balance_20"
        with engine.connect() as connection:
            record = InstitutionalFinancingService().margin_trading(
                connection, security_code="5009", trade_date=DAY,
                context=later(), source="tpex_margin_balance",
            )
        assert int(record.data["margin_previous_balance"]) == 8_390_000
        assert int(record.data["short_balance"]) == 5_000
        assert str(record.data["margin_utilization_ratio"]).rstrip("0") == "5.56"
    finally:
        engine.dispose()


def test_a_margin_date_is_market_invisible_until_the_release_rule(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEMarginTradingAdapter(), content=TWSE)
        known = datetime.now(UTC) + timedelta(minutes=1)

        def at(moment: datetime):
            return InstitutionalFinancingService().margin_trading(
                connection, security_code="2330", trade_date=DAY,
                context=MarketPITContext(information_as_of=moment, knowledge_as_of=known),
                source="twse_mi_margn",
            )

        with engine.connect() as connection:
            early = at(datetime(2026, 9, 11, 18, 59, tzinfo=UTC))
            settled = at(datetime(2026, 9, 11, 19, 0, tzinfo=UTC))
            system = InstitutionalFinancingService().margin_trading(
                connection, security_code="2330", trade_date=DAY,
                context=SystemPITContext(system_as_of=known), source="twse_mi_margn",
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
        run(engine, tmp_path, adapter=TPExMarginTradingAdapter(), content=TPEX)
        repeated, _ = run(engine, tmp_path, adapter=TPExMarginTradingAdapter(), content=TPEX)
        assert repeated.business_versions_created == 0
        assert repeated.business_versions_deduplicated == 920
    finally:
        engine.dispose()


def test_a_corrected_value_revises_that_security_only(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEMarginTradingAdapter(), content=TWSE)
        payload = json.loads(TWSE)
        payload["tables"][1]["data"][0][2] = "205"
        corrected = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result, _ = run(engine, tmp_path, adapter=TWSEMarginTradingAdapter(), content=corrected)
        assert result.business_versions_created == 1
        assert result.business_versions_deduplicated == 1296
    finally:
        engine.dispose()


def test_the_two_markets_keep_separate_histories(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TWSEMarginTradingAdapter(), content=TWSE)
        run(engine, tmp_path, adapter=TPExMarginTradingAdapter(), content=TPEX)
        with engine.connect() as connection:
            counts = dict(connection.execute(sa.text(
                "SELECT source, count(*) FROM margin_trading_versions GROUP BY source"
            )).all())
        assert counts == {"twse_mi_margn": 1297, "tpex_margin_balance": 920}
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "adapter, content",
    [(TWSEMarginTradingAdapter(), TWSE_CLOSED), (TPExMarginTradingAdapter(), TPEX_CLOSED)],
)
def test_a_closed_date_quarantines_and_keeps_its_raw_artifact(
    isolated_database_url: str, tmp_path: Path, adapter, content
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError, match="no_data_for_date"):
            run(engine, tmp_path, adapter=adapter, content=content, day=SUNDAY)
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT count(*) FROM margin_trading_versions")) == 0
            assert connection.scalar(sa.text("SELECT count(*) FROM raw_artifacts")) == 1
    finally:
        engine.dispose()


def test_both_markets_declare_their_expected_margin_coverage(
    isolated_database_url: str,
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        service = ExpectedCoverageService()
        with engine.connect() as connection:
            twse = service.declaration(connection, dataset_code="margin_trading", market="TWSE")
            tpex = service.declaration(connection, dataset_code="margin_trading", market="TPEx")
        assert (twse.source, tpex.source) == SOURCES
        assert twse.cadence == tpex.cadence == "trading_day"
        assert twse.calendar_market == tpex.calendar_market == "TWSE"
        assert twse.window_start == tpex.window_start == date(2020, 1, 2)
    finally:
        engine.dispose()


def test_the_cli_imports_one_margin_date(isolated_database_url: str, tmp_path: Path) -> None:
    import stock_data_center.ingestion.margin_trading as module
    from stock_data_center.ingestion.cli import main

    original = module.MarginTradingImporter.__init__

    def static_init(self, engine, **kwargs):
        kwargs["fetcher"] = StaticFetcher(TPEX)
        original(self, engine, **kwargs)

    module.MarginTradingImporter.__init__ = static_init
    try:
        code = main([
            "--database-url", isolated_database_url,
            "--purpose", "gap_fill",
            "margin-trading", "--source", "tpex_margin_balance",
            "--trade-date", "2026-09-11",
            "--raw-root", str(tmp_path / "raw"),
        ])
    finally:
        module.MarginTradingImporter.__init__ = original
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
            run(engine, tmp_path, adapter=TWSEMarginTradingAdapter(),
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
        "WHERE dataset_code = 'margin_trading' AND source IN :sources"
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
        run(engine, tmp_path, adapter=TPExMarginTradingAdapter(), content=TPEX,
            purpose=IngestPurpose.FIRST_CAPTURE)
        run(engine, tmp_path, adapter=TPExMarginTradingAdapter(), content=TPEX,
            purpose=IngestPurpose.GAP_FILL)
        with engine.connect() as connection:
            counts = dict(connection.execute(sa.text(
                "SELECT evidence_type, count(*) FROM publication_evidence "
                "WHERE dataset_code = 'margin_trading' GROUP BY 1"
            )).all())
        assert counts == {"capture_bound": 920}
    finally:
        engine.dispose()


UTILIZATION_PREVIOUS = "a4c8e2f6b1d3"


def over_one_hundred() -> bytes:
    payload = json.loads(TPEX)
    payload["tables"][0]["data"][0][8] = "103.1"
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def test_a_utilization_above_one_hundred_is_stored(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """00989B, 2026-07-14: TPEx published 103.1%. Step 7's 0-100 cap had no
    source behind it; the stop applies from the next business day."""
    engine = sa.create_engine(isolated_database_url)
    try:
        result, _ = run(engine, tmp_path, adapter=TPExMarginTradingAdapter(),
                        content=over_one_hundred())
        assert result.business_versions_created == 920
        with engine.connect() as connection:
            assert connection.scalar(sa.text(
                "SELECT max(margin_utilization_ratio) FROM margin_trading_versions"
            )) == Decimal("103.1")
    finally:
        engine.dispose()


def test_the_cap_downgrade_refuses_a_stored_ratio_above_one_hundred(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from alembic import command
    from conftest import alembic_config, alembic_head
    from sqlalchemy.exc import DBAPIError

    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=TPExMarginTradingAdapter(), content=over_one_hundred())
        with pytest.raises(DBAPIError) as blocked:
            command.downgrade(alembic_config(isolated_database_url), UTILIZATION_PREVIOUS)
        assert blocked.value.orig.sqlstate == "P0001"
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == alembic_head()
    finally:
        engine.dispose()


def test_the_cap_downgrade_restores_the_old_check_and_upgrade_relaxes_it(
    isolated_database_url: str,
) -> None:
    from alembic import command
    from conftest import alembic_config

    config = alembic_config(isolated_database_url)
    engine = sa.create_engine(isolated_database_url)
    query = sa.text(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'margin_trading_versions'::regclass "
        "AND conname LIKE 'ck_margin_trading_versions_margin_trading_ratios%'"
    )
    try:
        command.downgrade(config, UTILIZATION_PREVIOUS)
        with engine.connect() as connection:
            assert connection.scalars(query).all() == [
                "ck_margin_trading_versions_margin_trading_ratios_percent"
            ]
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalars(query).all() == [
                "ck_margin_trading_versions_margin_trading_ratios_nonnegative"
            ]
    finally:
        engine.dispose()


def test_the_manifest_records_the_lot_exceptions(isolated_database_url: str) -> None:
    """Review of #34: the lot sizes are source semantics, so they belong in the
    configuration fingerprint; editing the exception list changes it."""
    engine = sa.create_engine(isolated_database_url)
    try:
        described = MarginTradingImporter(engine)._source_semantics(TWSEMarginTradingAdapter())
        assert described["lot_shares"] == {"008201": [100, "2020-01-02", "2022-07-08"]}
        assert described["default_lot_shares"] == 1000
        tpex = MarginTradingImporter(engine)._source_semantics(TPExMarginTradingAdapter())
        assert tpex["lot_shares"] == {}
    finally:
        engine.dispose()
