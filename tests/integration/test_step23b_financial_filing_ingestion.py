"""Step 23-b — importing one `t164sb01` document through the raw-first lifecycle.

What this step stores is the scope the legacy database stored: the balance
sheet, the statement of comprehensive income and the statement of cash flows,
which the document marks with its own `id="BalanceSheet"`,
`id="StatementOfComprehensiveIncome"` and `id="StatementsOfCashFlows"` anchors.
權益變動表, the notes, the 附表 and the `escape="true"` narrative blocks are not
stored, and whether they ever are is a decision the ROADMAP takes at its end.

No publication time is claimed here. Every version is System-PIT visible and
Market-PIT invisible until Step 23-c attaches the evidence, which is the same
shape Step 22-a had.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.financials import FinancialFilingService
from stock_data_center.financials.models import EPSPeriodBasis, FilingPeriod
from stock_data_center.ingestion.adapters.financial_filing import (
    MOPSFinancialFilingAdapter,
)
from stock_data_center.ingestion.adapters.financial_filing_archive import (
    LegacyFinancialFilingArchiveAdapter,
)
from stock_data_center.ingestion.financial_filing import (
    FinancialFilingArchiveImporter,
    FinancialFilingImporter,
)
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    FinancialFilingArchiveRequest,
    FinancialFilingRequest,
    ResourceQuarantinedError,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.provenance import ArtifactOrigin, IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / f"mops_t164sb01_{name}.html").read_bytes()


CEMENT_Q1 = fixture("1101_2025Q1_statements")
CEMENT_Q3 = fixture("1101_2025Q3_statements")
SAME_EPS = fixture("6160_2024Q2_statements")
BROKER = fixture("5864_2020Q1_statements")
EMERGING = fixture("6785_2020Q2_statements")
CEMENT_FACTS = 20
SOURCE = "mops_t164sb01"


class StaticFetcher:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def fetch(self, resource):
        return FetchedArtifact(
            content=self.content,
            source_uri=resource.source_uri,
            fetched_at=datetime.now(UTC),
            media_type="text/html",
        )


def run(
    engine,
    tmp_path,
    *,
    content=CEMENT_Q1,
    code="1101",
    year=2025,
    quarter=1,
    report_id="C",
    import_id=None,
    purpose=IngestPurpose.GAP_FILL,
):
    importer = FinancialFilingImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=StaticFetcher(content),
    )
    import_id = import_id or uuid4()
    result = importer.run(
        adapter=MOPSFinancialFilingAdapter(),
        request=FinancialFilingRequest(code, year, quarter, report_id),
        import_id=import_id,
        git_commit="test-commit",
        purpose=purpose,
    )
    with engine.connect() as connection:
        manifest = importer.manifest(connection, import_id)
    return result, manifest


def system() -> SystemPITContext:
    return SystemPITContext(system_as_of=datetime.now(UTC) + timedelta(minutes=1))


def test_one_document_becomes_one_sealed_filing(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest = run(engine, tmp_path)
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 1
        assert result.normalized_rows == CEMENT_FACTS
        with engine.connect() as connection:
            filing = FinancialFilingService().filing(
                connection,
                security_code="1101",
                period=FilingPeriod(2025, 1),
                context=system(),
                source=SOURCE,
            )
        assert filing is not None
        assert len(filing.facts) == CEMENT_FACTS
        assert {fact.statement for fact in filing.facts} == {
            "balance_sheet",
            "income_statement",
            "cash_flow",
        }
        assert all(fact.account_code for fact in filing.facts)
    finally:
        engine.dispose()


def test_the_report_category_is_preserved(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path)
        with engine.connect() as connection:
            category = connection.scalar(
                sa.text(
                    "SELECT report_category FROM financial_filing_versions"
                    " WHERE report_year = 2025 AND report_quarter = 1"
                )
            )
        assert category == "consolidated"
    finally:
        engine.dispose()


def test_two_statements_may_print_the_same_concept(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The balance sheet's 1100 and the cash-flow statement's E00210 are the
    same `ifrs-full:CashAndCashEquivalents` at the same instant in the same
    unit. Both are rows the source printed, so both are stored."""
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path)
        with engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT statement, account_code FROM financial_facts"
                    " WHERE concept_qname LIKE '%%}ProfitLossBeforeTax'"
                    "   AND period_start = DATE '2025-01-01'"
                    " ORDER BY account_code"
                )
            ).all()
        assert [tuple(row) for row in rows] == [
            ("cash_flow", "A00010"),
            ("cash_flow", "A10000"),
        ]
    finally:
        engine.dispose()


def test_the_eps_contract_holds(isolated_database_url: str, tmp_path: Path) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path)
        run(engine, tmp_path, content=CEMENT_Q3, quarter=3)
        service = FinancialFilingService()
        with engine.connect() as connection:
            q1 = service.actual_eps(
                connection,
                security_code="1101",
                period=FilingPeriod(2025, 1),
                period_basis=EPSPeriodBasis.QUARTER,
                context=system(),
                source=SOURCE,
            )
            q3_quarter = service.actual_eps(
                connection,
                security_code="1101",
                period=FilingPeriod(2025, 3),
                period_basis=EPSPeriodBasis.QUARTER,
                context=system(),
                source=SOURCE,
            )
            q3_ytd = service.actual_eps(
                connection,
                security_code="1101",
                period=FilingPeriod(2025, 3),
                period_basis=EPSPeriodBasis.YTD,
                context=system(),
                source=SOURCE,
            )
            q3_annual = service.actual_eps(
                connection,
                security_code="1101",
                period=FilingPeriod(2025, 3),
                period_basis=EPSPeriodBasis.ANNUAL,
                context=system(),
                source=SOURCE,
            )
        assert q1.value == Decimal("0.07")
        assert q3_quarter.value == Decimal("-1.36")
        assert q3_ytd.value == Decimal("-1.28")
        assert q3_annual is None
        assert q1.unit_identity == "iso4217:TWD/xbrli:shares"
    finally:
        engine.dispose()


def test_a_quarter_eps_equal_to_the_year_to_date_one_is_still_two_facts(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """6160 2024Q2 prints -0.41 for the single quarter and -0.41 again for the
    year to date. The two facts differ only by context, so a summary that
    looked its source fact back up by value would tie both bases to one fact
    and the seal would refuse the filing. Nine documents in a 2,500-document
    sample of the archive do this."""
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest = run(
            engine, tmp_path, content=SAME_EPS, code="6160", year=2024, quarter=2
        )
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 1
        service = FinancialFilingService()
        with engine.connect() as connection:
            quarter = service.actual_eps(
                connection,
                security_code="6160",
                period=FilingPeriod(2024, 2),
                period_basis=EPSPeriodBasis.QUARTER,
                context=system(),
                source=SOURCE,
            )
            ytd = service.actual_eps(
                connection,
                security_code="6160",
                period=FilingPeriod(2024, 2),
                period_basis=EPSPeriodBasis.YTD,
                context=system(),
                source=SOURCE,
            )
        assert quarter.value == Decimal("-0.41")
        assert ytd.value == Decimal("-0.41")
        # Same number, different rows: each basis points at its own context.
        assert quarter.source_fact.fact_id != ytd.source_fact.fact_id
        assert quarter.source_fact.context.period_start != (
            ytd.source_fact.context.period_start
        )
    finally:
        engine.dispose()


def test_the_archive_importer_never_records_an_official_fetch(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The bytes come off disk, so the origin is `legacy_archive` whether or
    not the caller says so (CLAUDE.md §75)."""
    engine = sa.create_engine(isolated_database_url)
    try:
        folder = tmp_path / "archive" / "2025" / "2025Q1"
        folder.mkdir(parents=True)
        (folder / "2025Q1_1101_20250515.html").write_bytes(CEMENT_Q1)
        importer = FinancialFilingArchiveImporter(
            engine, raw_store=LocalRawArtifactStore(tmp_path / "raw")
        )
        importer.run(
            adapter=LegacyFinancialFilingArchiveAdapter(
                archive_root=tmp_path / "archive"
            ),
            request=FinancialFilingArchiveRequest("1101", 2025, 1),
            import_id=uuid4(),
            purpose=IngestPurpose.GAP_FILL,
        )
        with engine.connect() as connection:
            origins = connection.scalars(
                sa.text("SELECT DISTINCT artifact_origin FROM raw_artifact_observations")
            ).all()
        assert origins == ["legacy_archive"]
    finally:
        engine.dispose()


def test_a_missing_archive_document_leaves_a_failed_manifest(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Looking the file up is part of fetching it, so a miss is recorded.
    Raising before the manifest exists would end a 45,000-filing loop with no
    auditable trace at all (CLAUDE.md §78, §79)."""
    engine = sa.create_engine(isolated_database_url)
    try:
        importer = FinancialFilingArchiveImporter(
            engine, raw_store=LocalRawArtifactStore(tmp_path / "raw")
        )
        import_id = uuid4()
        with pytest.raises(Exception):
            importer.run(
                adapter=LegacyFinancialFilingArchiveAdapter(
                    archive_root=tmp_path / "empty"
                ),
                request=FinancialFilingArchiveRequest("1101", 2025, 1),
                import_id=import_id,
                purpose=IngestPurpose.GAP_FILL,
            )
        with engine.connect() as connection:
            status = connection.scalar(
                sa.text(
                    "SELECT status FROM import_manifests WHERE import_id = :import_id"
                ),
                {"import_id": import_id},
            )
        assert status == "failed"
    finally:
        engine.dispose()


def test_no_publication_time_is_claimed_yet(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, _ = run(engine, tmp_path)
        assert result.unknown_publication_observations == 1
        later = datetime.now(UTC) + timedelta(minutes=1)
        service = FinancialFilingService()
        with engine.connect() as connection:
            assert (
                service.filing(
                    connection,
                    security_code="1101",
                    period=FilingPeriod(2025, 1),
                    context=MarketPITContext(
                        information_as_of=later, knowledge_as_of=later
                    ),
                    source=SOURCE,
                )
                is None
            )
    finally:
        engine.dispose()


def test_reimporting_the_same_document_makes_no_second_version(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path)
        again, _ = run(engine, tmp_path)
        assert again.business_versions_created == 0
        assert again.business_versions_deduplicated == 1
        with engine.connect() as connection:
            versions = connection.scalar(
                sa.text("SELECT count(*) FROM financial_filing_versions")
            )
            facts = connection.scalar(
                sa.text("SELECT count(*) FROM financial_facts")
            )
            observations = connection.scalar(
                sa.text(
                    "SELECT count(*) FROM financial_filing_version_observations"
                )
            )
        assert versions == 1
        assert facts == CEMENT_FACTS
        # Provenance stays auditable: the second fetch is its own observation.
        assert observations == 2
    finally:
        engine.dispose()


def test_a_corrected_document_is_a_second_version(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path)
        corrected, _ = run(
            engine,
            tmp_path,
            content=CEMENT_Q1.replace(b">89,680,417<", b">89,680,418<"),
        )
        assert corrected.business_versions_created == 1
        with engine.connect() as connection:
            versions = connection.scalar(
                sa.text("SELECT count(*) FROM financial_filing_versions")
            )
        assert versions == 2
    finally:
        engine.dispose()


def test_a_financial_industry_issuer_stores_no_filing(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError):
            run(engine, tmp_path, content=BROKER, code="5864", year=2020, report_id="A")
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM financial_filing_versions")
                )
                == 0
            )
            reason = connection.scalar(
                sa.text("SELECT reason_code FROM import_quarantine")
            )
        assert reason == "financial_industry_issuer"
    finally:
        engine.dispose()


def test_a_filer_outside_the_universe_stores_no_filing(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with pytest.raises(ResourceQuarantinedError):
            run(
                engine,
                tmp_path,
                content=EMERGING,
                code="6785",
                year=2020,
                quarter=2,
                report_id="A",
            )
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM financial_filing_versions")
                )
                == 0
            )
            reason = connection.scalar(
                sa.text("SELECT reason_code FROM import_quarantine")
            )
        assert reason == "outside_v1_universe"
    finally:
        engine.dispose()


def test_the_archive_import_dedups_against_the_official_one(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The archived copy is the same document re-encoded, so it is the same
    source revision and must not become a second business version."""
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path)
        folder = tmp_path / "archive" / "2025" / "2025Q1"
        folder.mkdir(parents=True)
        (folder / "2025Q1_1101_20250515.html").write_bytes(CEMENT_Q1)
        importer = FinancialFilingArchiveImporter(
            engine, raw_store=LocalRawArtifactStore(tmp_path / "raw")
        )
        result = importer.run(
            adapter=LegacyFinancialFilingArchiveAdapter(
                archive_root=tmp_path / "archive"
            ),
            request=FinancialFilingArchiveRequest("1101", 2025, 1),
            import_id=uuid4(),
            purpose=IngestPurpose.GAP_FILL,
            artifact_origin=ArtifactOrigin.LEGACY_ARCHIVE,
        )
        assert result.business_versions_created == 0
        assert result.business_versions_deduplicated == 1
        with engine.connect() as connection:
            versions = connection.scalar(
                sa.text("SELECT count(*) FROM financial_filing_versions")
            )
            origins = connection.scalars(
                sa.text("SELECT DISTINCT artifact_origin FROM raw_artifact_observations")
            ).all()
        assert versions == 1
        assert sorted(origins) == ["legacy_archive", "official_fetch"]
    finally:
        engine.dispose()


def test_the_manifest_reports_what_was_stored_and_what_was_not(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        _, manifest = run(engine, tmp_path)
        reconciliation = manifest.reconciliation
        assert reconciliation["report_category"] == "consolidated"
        assert reconciliation["report_id"] == "C"
        assert reconciliation["stored_facts"] == CEMENT_FACTS
        assert reconciliation["statements"] == {
            "balance_sheet": 6,
            "income_statement": 8,
            "cash_flow": 6,
        }
        assert reconciliation["publication_time"] == "unknown"
    finally:
        engine.dispose()


PREVIOUS_HEAD = "c8f1a63d5b02"


def test_the_downgrade_refuses_a_stored_filing(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from alembic import command
    from conftest import alembic_config, alembic_head
    from sqlalchemy.exc import DBAPIError

    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path)
        with pytest.raises(DBAPIError) as blocked:
            command.downgrade(alembic_config(isolated_database_url), PREVIOUS_HEAD)
        assert blocked.value.orig.sqlstate == "P0001"
        with engine.connect() as connection:
            assert (
                connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
                == alembic_head()
            )
    finally:
        engine.dispose()


def test_the_downgrade_removes_the_statement_columns_and_upgrade_restores_them(
    isolated_database_url: str,
) -> None:
    from alembic import command
    from conftest import alembic_config

    config = alembic_config(isolated_database_url)
    engine = sa.create_engine(isolated_database_url)
    columns = sa.text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'financial_facts' "
        "AND column_name IN ('statement', 'account_code')"
    )
    sources = sa.text(
        "SELECT count(*) FROM dataset_sources "
        "WHERE dataset_code = 'financial_filing' AND source = 'mops_t164sb01'"
    )
    try:
        command.downgrade(config, PREVIOUS_HEAD)
        with engine.connect() as connection:
            assert connection.scalar(columns) == 0
            assert connection.scalar(sources) == 0
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(columns) == 2
            assert connection.scalar(sources) == 1
    finally:
        engine.dispose()


def test_one_number_printed_in_two_statements_keeps_both_rows(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The balance sheet's 1100 and the cash-flow statement's E00210 are the
    same `ifrs-full:CashAndCashEquivalents`, at the same instant, in the same
    unit. Under the Step 5 identity the second row was a duplicate; the
    statement it was printed in is what tells them apart, and the archive has
    171,000 such pairs — four in every one of its 42,750 in-scope documents."""
    engine = sa.create_engine(isolated_database_url)
    try:
        run(engine, tmp_path)
        with engine.connect() as connection:
            stored = connection.execute(
                sa.text(
                    "SELECT statement, account_code FROM financial_facts"
                    " WHERE concept_qname LIKE '%%}CashAndCashEquivalents'"
                    "   AND instant_date = DATE '2025-03-31'"
                    " ORDER BY statement"
                )
            ).all()
        assert [tuple(item) for item in stored] == [
            ("balance_sheet", "1100"),
            ("cash_flow", "E00210"),
        ]
    finally:
        engine.dispose()


def test_the_cli_falls_back_from_the_consolidated_report_to_the_individual_one(
    isolated_database_url: str, tmp_path: Path, monkeypatch
) -> None:
    """A filer that files individually answers `REPORT_ID=C` with
    `檔案不存在!`. That is the source saying which report exists, so the CLI
    asks for `A` instead of failing."""
    from stock_data_center.ingestion import cli as module

    answers = {"C": fixture("no_report"), "A": fixture("1342_2025Q1_statements")}

    class Switching:
        def fetch(self, resource):
            report_id = resource.source_uri.rsplit("=", 1)[1]
            return FetchedArtifact(
                content=answers[report_id],
                source_uri=resource.source_uri,
                fetched_at=datetime.now(UTC),
                media_type="text/html",
            )

    original = module.FinancialFilingImporter.__init__

    def patched(self, engine, **kwargs):
        kwargs["fetcher"] = Switching()
        original(self, engine, **kwargs)

    monkeypatch.setattr(module.FinancialFilingImporter, "__init__", patched)
    code = module.main(
        [
            "--database-url", isolated_database_url,
            "financial-filing",
            "--security-code", "1342",
            "--period", "2025Q1",
            "--raw-root", str(tmp_path / "raw"),
        ]
    )
    assert code == 0
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT filing_key, report_category FROM financial_filing_versions"
                )
            ).one()
        assert row.report_category == "individual"
        assert row.filing_key.startswith("1342:2025Q1:A:")
    finally:
        engine.dispose()
