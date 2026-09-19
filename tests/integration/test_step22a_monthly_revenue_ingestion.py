"""Step 22-a — importing one MOPS monthly-revenue page through the raw-first lifecycle.

22-a stores the official pages and their published comparatives. It claims no
publication time: the evidence that makes a month Market-PIT visible — the
recovered announcement dates, the legacy first-seen captures and the
statutory rule — is Step 22-c. Until then every version is System-PIT visible
only, which is late and never early.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.ingestion.adapters import (
    MOPSOtcMonthlyRevenueAdapter,
    MOPSSiiMonthlyRevenueAdapter,
)
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    MonthlyRevenueRequest,
    ResourceQuarantinedError,
    RevenuePage,
)
from stock_data_center.ingestion.monthly_revenue import MonthlyRevenueImporter
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.monthly_revenue import MonthlyRevenueService
from stock_data_center.monthly_revenue.models import RevenuePeriod
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SII_0 = (FIXTURES / "mops_t21sc03_sii_115_7_0.html").read_bytes()
SII_1 = (FIXTURES / "mops_t21sc03_sii_115_7_1.html").read_bytes()
OTC_M06 = (FIXTURES / "mops_t21sc03_otc_115_6_0.html").read_bytes()
OTC_M07 = (FIXTURES / "mops_t21sc03_otc_115_7_0.html").read_bytes()
NO_DATA = (FIXTURES / "mops_t21sc03_sii_115_9_0_no_data.html").read_bytes()
UNREACHABLE = (FIXTURES / "mops_t21sc03_unreachable.html").read_bytes()

JULY = RevenuePeriod(2026, 7)
JUNE = RevenuePeriod(2026, 6)
SEPT = RevenuePeriod(2026, 9)
PREVIOUS_HEAD = "c6e2a8d4f1b7"
SOURCES = ("mops_t21sc03_sii", "mops_t21sc03_otc")
SII_ROWS, SII_KY_ROWS, OTC_ROWS = 992, 94, 861


class StaticFetcher:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def fetch(self, resource):
        content = self.content
        return FetchedArtifact(
            content=content,
            source_uri=resource.source_uri,
            fetched_at=datetime.now(UTC),
            media_type="text/html",
        )


def run(engine, tmp_path, *, adapter, content, period=JULY, page=RevenuePage.DOMESTIC,
        purpose=IngestPurpose.GAP_FILL):
    importer = MonthlyRevenueImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=StaticFetcher(content),
    )
    import_id = uuid4()
    result = importer.run(
        adapter=adapter,
        request=MonthlyRevenueRequest(period, page),
        import_id=import_id,
        git_commit="test-commit",
        purpose=purpose,
    )
    with engine.connect() as connection:
        manifest = importer.manifest(connection, import_id)
    return result, manifest


def system() -> SystemPITContext:
    return SystemPITContext(system_as_of=datetime.now(UTC) + timedelta(minutes=1))


def revenue(connection, code: str, period, source: str, context=None):
    return MonthlyRevenueService().revenue(
        connection, security_code=code, period=period,
        context=context or system(), source=source,
    )


def with_value(raw: bytes, code: str, old: str, new: str) -> bytes:
    text = raw.decode("cp950")
    start = text.index(f"<tr align=right><td align=center>{code}</td>")
    end = text.index("</tr>", start)
    row = text[start:end]
    assert row.count(old) == 1
    return (text[:start] + row.replace(old, new) + text[end:]).encode("cp950")


def test_one_page_imports_with_its_comparatives(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest = run(engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(), content=SII_0)
        assert manifest.status == "succeeded"
        assert result.business_versions_created == SII_ROWS
        assert manifest.reconciliation["header_variant"] == "t21sc03_11"
        with engine.connect() as connection:
            record = revenue(connection, "2330", JULY, "mops_t21sc03_sii")
        assert Decimal(record.data["revenue"]) == Decimal(467580548000)
        assert Decimal(record.data["revenue_last_month"]) == Decimal(442679969000)
        assert Decimal(record.data["mom_pct"]) == Decimal("5.62")
        assert Decimal(record.data["cumulative_yoy_pct"]) == Decimal("37.01")
        assert record.data["note"] == "-"
        assert record.data["currency"] == "TWD"
    finally:
        engine.dispose()


def test_no_publication_time_is_claimed_yet(isolated_database_url: str, tmp_path: Path) -> None:
    """22-c attaches the evidence; until then Market PIT sees nothing."""
    engine = sa.create_engine(isolated_database_url)
    try:
        result, _ = run(engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(), content=SII_0)
        assert result.unknown_publication_observations == SII_ROWS
        later = datetime.now(UTC) + timedelta(minutes=1)
        with engine.connect() as connection:
            assert revenue(
                connection, "2330", JULY, "mops_t21sc03_sii",
                MarketPITContext(information_as_of=later, knowledge_as_of=later),
            ) is None
            assert revenue(connection, "2330", JULY, "mops_t21sc03_sii") is not None
    finally:
        engine.dispose()


def test_ky_issuers_import_from_the_foreign_page(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, _ = run(engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(), content=SII_1,
                        page=RevenuePage.FOREIGN)
        assert result.business_versions_created == SII_KY_ROWS
        with engine.connect() as connection:
            record = revenue(connection, "5871", JULY, "mops_t21sc03_sii")
        assert Decimal(record.data["revenue"]) == Decimal(8463270000)
    finally:
        engine.dispose()


def test_rerunning_the_same_page_creates_no_revision(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(), content=OTC_M07)
        repeated, _ = run(engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(), content=OTC_M07)
        assert repeated.business_versions_created == 0
        assert repeated.business_versions_deduplicated == OTC_ROWS
    finally:
        engine.dispose()


def test_a_correction_between_two_fetches_is_a_revision(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Legacy first captured 6441 廣錠 2026M06 as 13,094 千元; MOPS serves the
    corrected 10,948 today (audit §7.3)."""
    engine = sa.create_engine(isolated_database_url)
    try:
        first = with_value(OTC_M06, "6441", "10,948</td>", "13,094</td>")
        run(engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(), content=first, period=JUNE)
        result, _ = run(engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(), content=OTC_M06,
                        period=JUNE)
        assert result.business_versions_created == 1
        assert result.business_versions_deduplicated == OTC_ROWS - 1
        with engine.connect() as connection:
            values = connection.execute(sa.text(
                "SELECT v.revenue FROM monthly_revenue_versions v JOIN security s "
                "ON s.id = v.security_id WHERE s.security_code = '6441' ORDER BY v.id"
            )).scalars().all()
        assert values == [Decimal(13094000), Decimal(10948000)]
    finally:
        engine.dispose()


def test_a_comparative_that_disagrees_with_the_prior_month_is_stored_as_published(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Audit §7.3's regression fixture: the M07 page's 上月營收 for 6441 is
    10,948 while the stored M06 revenue is legacy's first-captured 13,094.
    Nothing reconciles the two; the disagreement is the data."""
    engine = sa.create_engine(isolated_database_url)
    try:
        first = with_value(OTC_M06, "6441", "10,948</td>", "13,094</td>")
        run(engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(), content=first, period=JUNE)
        result, manifest = run(engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(),
                               content=OTC_M07)
        assert manifest.status == "succeeded"
        assert result.business_versions_created == OTC_ROWS
        with engine.connect() as connection:
            june = revenue(connection, "6441", JUNE, "mops_t21sc03_otc")
            july = revenue(connection, "6441", JULY, "mops_t21sc03_otc")
        assert Decimal(june.data["revenue"]) == Decimal(13094000)
        assert Decimal(july.data["revenue_last_month"]) == Decimal(10948000)
    finally:
        engine.dispose()


def test_the_two_markets_keep_separate_histories(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(), content=SII_0)
        run(engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(), content=OTC_M07)
        with engine.connect() as connection:
            counts = dict(connection.execute(sa.text(
                "SELECT source, count(*) FROM monthly_revenue_versions GROUP BY source"
            )).all())
        assert counts == {"mops_t21sc03_sii": SII_ROWS, "mops_t21sc03_otc": OTC_ROWS}
    finally:
        engine.dispose()


def test_a_month_not_yet_published_quarantines_and_keeps_its_raw_artifact(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError, match="no_data_for_period"):
            run(engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(), content=NO_DATA,
                period=SEPT)
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT count(*) FROM monthly_revenue_versions")) == 0
            assert connection.scalar(sa.text("SELECT count(*) FROM raw_artifacts")) == 1
    finally:
        engine.dispose()


def test_an_unreachable_answer_quarantines_and_a_new_import_recovers(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The 18 bytes are kept as the raw artifact of a quarantined page, as a
    TWSE CDN error page is (Step 20-a); nothing is stored for the month, and a
    rerun under a new import id imports it."""
    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError, match="unusable_response"):
            run(engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(), content=UNREACHABLE)
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT count(*) FROM monthly_revenue_versions")) == 0
        result, manifest = run(engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(),
                               content=SII_0)
        assert manifest.status == "succeeded"
        assert result.business_versions_created == SII_ROWS
    finally:
        engine.dispose()


def test_a_version_without_comparatives_keeps_its_pre_22a_hash(
    isolated_database_url: str,
) -> None:
    """The comparatives enter the hash only when present, so every version
    written before them keeps its identity."""
    from stock_data_center.monthly_revenue import MonthlyRevenueObservation

    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            stored = connection.execute(sa.text(
                "SELECT count(*) FROM monthly_revenue_versions"
            )).scalar()
            assert stored == 0
        observation = MonthlyRevenueObservation(
            period=JULY, revenue=Decimal(1000), currency="TWD"
        )
        assert observation.mom_pct is None
        with engine.connect() as connection:
            expected = connection.scalar(sa.text(
                "SELECT encode(digest(convert_to(jsonb_build_object("
                "'revenue', CAST(1000 AS numeric(24,4)), 'currency', 'TWD')::text, "
                "'UTF8'), 'sha256'), 'hex')"
            ))
            computed = connection.scalar(sa.text(
                "SELECT stockdc_monthly_revenue_hash(CAST(1000 AS numeric(24,4)), 'TWD', "
                "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"
            ))
        assert computed == expected
    finally:
        engine.dispose()


def test_the_cli_imports_one_page(isolated_database_url: str, tmp_path: Path) -> None:
    import stock_data_center.ingestion.monthly_revenue as module
    from stock_data_center.ingestion.cli import main

    original = module.MonthlyRevenueImporter.__init__

    def static_init(self, engine, **kwargs):
        kwargs["fetcher"] = StaticFetcher(SII_1)
        original(self, engine, **kwargs)

    module.MonthlyRevenueImporter.__init__ = static_init
    try:
        code = main([
            "--database-url", isolated_database_url,
            "--purpose", "gap_fill",
            "monthly-revenue", "--source", "mops_t21sc03_sii",
            "--period", "2026-07", "--page", "foreign",
            "--raw-root", str(tmp_path / "raw"),
        ])
    finally:
        module.MonthlyRevenueImporter.__init__ = original
    assert code == 0


def test_the_downgrade_refuses_a_stored_comparative(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from alembic import command
    from conftest import alembic_config, alembic_head
    from sqlalchemy.exc import DBAPIError

    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(), content=SII_1,
            page=RevenuePage.FOREIGN)
        with pytest.raises(DBAPIError) as blocked:
            command.downgrade(alembic_config(isolated_database_url), PREVIOUS_HEAD)
        assert blocked.value.orig.sqlstate == "P0001"
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == alembic_head()
    finally:
        engine.dispose()


def test_the_downgrade_removes_the_comparatives_and_upgrade_restores_them(
    isolated_database_url: str,
) -> None:
    from alembic import command
    from conftest import alembic_config

    config = alembic_config(isolated_database_url)
    engine = sa.create_engine(isolated_database_url)
    columns = sa.text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'monthly_revenue_versions' AND column_name = 'mom_pct'"
    )
    sources = sa.text(
        "SELECT count(*) FROM dataset_sources WHERE dataset_code = 'monthly_revenue' "
        "AND source IN ('mops_t21sc03_sii', 'mops_t21sc03_otc')"
    )
    try:
        command.downgrade(config, PREVIOUS_HEAD)
        with engine.connect() as connection:
            assert connection.scalar(columns) == 0
            assert connection.scalar(sources) == 0
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(columns) == 1
            assert connection.scalar(sources) == 2
    finally:
        engine.dispose()


def test_the_manifest_records_the_unit_and_page(isolated_database_url: str) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        described = MonthlyRevenueImporter(engine)._source_semantics(MOPSSiiMonthlyRevenueAdapter())
        assert described["amount_unit"] == "TWD, from 千元 × 1,000"
        assert described["market"] == "sii"
    finally:
        engine.dispose()
