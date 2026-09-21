"""Step 23-b — the two `t164sb01` adapters and what they refuse.

The fixtures are real documents cut to the three statement sections legacy
stored: the head through `</ix:header>` with only the contexts the kept rows
reference, then each statement's own anchor `<div>`, its `<table>` and a few of
its `<tr>` rows, unedited. The anchors are the cut's point: `id="BalanceSheet"`,
`id="StatementOfComprehensiveIncome"` and `id="StatementsOfCashFlows"` each
appear exactly once in every one of the 45,324 archive documents, and every
`ix:nonFraction` inside those three tables carries a 會計科目代碼 — 16,180,359
of them, 0 without (scan of 2026-09-21).

`mops_t164sb01_no_report.html` is what MOPS answered on 2026-09-21 for
`REPORT_ID=A` on 1101, a consolidated filer: 98 bytes of cp950 saying
`檔案不存在!`, under HTTP 200. The same page answers `REPORT_ID=C` for 1342,
which files individually. The two are mutually exclusive, which is why the
official adapter is asked for one report id and the caller falls back C→A.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.financials.ixbrl import StatementSection
from stock_data_center.financials.models import SummaryPeriodBasis
from stock_data_center.ingestion.adapters.financial_filing import (
    MOPSFinancialFilingAdapter,
)
from stock_data_center.ingestion.adapters.financial_filing_archive import (
    LegacyFinancialFilingArchiveAdapter,
)
from stock_data_center.ingestion.models import (
    FinancialFilingArchiveRequest,
    FinancialFilingRequest,
    SourceDataError,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / f"mops_t164sb01_{name}.html").read_bytes()


CEMENT_Q1 = fixture("1101_2025Q1_statements")
CEMENT_Q3 = fixture("1101_2025Q3_statements")
INDIVIDUAL = fixture("1342_2025Q1_statements")
BROKER = fixture("5864_2020Q1_statements")
EMERGING = fixture("6785_2020Q2_statements")
NO_REPORT = fixture("no_report")


def request_for(code: str, year: int, quarter: int, report_id: str = "C"):
    return FinancialFilingRequest(
        security_code=code,
        report_year=year,
        report_quarter=quarter,
        report_id=report_id,
    )


def test_resource_is_the_endpoint_legacy_fetched() -> None:
    adapter = MOPSFinancialFilingAdapter()
    resource = adapter.resource(request_for("1101", 2025, 1))
    assert resource.source_uri == (
        "https://mopsov.twse.com.tw/server-java/t164sb01"
        "?step=1&CO_ID=1101&SYEAR=2025&SSEASON=1&REPORT_ID=C"
    )
    assert resource.resource_key == "mops_t164sb01:financial_filing:1101:2025Q1:C"


def test_only_the_three_statement_sections_are_stored() -> None:
    parsed = MOPSFinancialFilingAdapter().parse(CEMENT_Q1, request_for("1101", 2025, 1))
    assert {fact.statement for fact in parsed.facts} == {
        StatementSection.BALANCE_SHEET,
        StatementSection.INCOME_STATEMENT,
        StatementSection.CASH_FLOW,
    }
    assert all(fact.account_code for fact in parsed.facts)
    # Every fact the document prints inside those tables, comparatives
    # included; nothing from 權益變動表, the notes or the 附表.
    assert len(parsed.facts) == 20


def test_a_repeated_concept_keeps_both_statement_rows() -> None:
    """1101 2025Q1 prints ProfitLossBeforeTax twice in the cash-flow table,
    as A00010 and A10000, same context and same unit. They differ only by
    namespace, so Clark-notation identity keeps both."""
    parsed = MOPSFinancialFilingAdapter().parse(CEMENT_Q1, request_for("1101", 2025, 1))
    before_tax = {
        (fact.account_code, fact.observation.concept_qname)
        for fact in parsed.facts
        if fact.account_code in {"A00010", "A10000"}
        and fact.observation.context.period_start is not None
        and fact.observation.context.period_start.year == 2025
    }
    assert before_tax == {
        (
            "A00010",
            "{http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full}ProfitLossBeforeTax",
        ),
        ("A10000", "{http://www.xbrl.org/tifrs/scf/2020-06-30}ProfitLossBeforeTax"),
    }


def test_scale_and_statement_come_from_the_source() -> None:
    parsed = MOPSFinancialFilingAdapter().parse(CEMENT_Q1, request_for("1101", 2025, 1))
    cash = next(
        fact
        for fact in parsed.facts
        if fact.account_code == "1100"
        and fact.observation.context.instant_date is not None
        and fact.observation.context.instant_date.isoformat() == "2025-03-31"
    )
    assert cash.statement is StatementSection.BALANCE_SHEET
    assert cash.observation.numeric_value == Decimal("89680417000")
    assert cash.observation.unit_identity == "iso4217:TWD"


def test_report_category_is_preserved() -> None:
    adapter = MOPSFinancialFilingAdapter()
    consolidated = adapter.parse(CEMENT_Q1, request_for("1101", 2025, 1))
    individual = adapter.parse(INDIVIDUAL, request_for("1342", 2025, 1, "A"))
    assert consolidated.report_category.value == "Consolidated report"
    assert individual.report_category.value == "Individual report"
    assert consolidated.report_id == "C"
    assert individual.report_id == "A"


def test_eps_carries_its_validated_source_role() -> None:
    parsed = MOPSFinancialFilingAdapter().parse(CEMENT_Q3, request_for("1101", 2025, 3))
    by_basis = {entry.period_basis: entry for entry in parsed.eps}
    assert set(by_basis) == {SummaryPeriodBasis.QUARTER, SummaryPeriodBasis.YTD}
    assert by_basis[SummaryPeriodBasis.QUARTER].value == Decimal("-1.36")
    assert by_basis[SummaryPeriodBasis.YTD].value == Decimal("-1.28")
    for entry in parsed.eps:
        assert entry.unit_identity == "iso4217:TWD/xbrli:shares"
        assert entry.classification.classifier_rule == "mops-xbrl-context-role:v1"


def test_q1_has_one_single_quarter_eps() -> None:
    parsed = MOPSFinancialFilingAdapter().parse(CEMENT_Q1, request_for("1101", 2025, 1))
    assert [entry.period_basis for entry in parsed.eps] == [SummaryPeriodBasis.QUARTER]
    assert parsed.eps[0].value == Decimal("0.07")


def test_a_financial_industry_issuer_is_refused_at_the_boundary() -> None:
    with pytest.raises(SourceDataError) as error:
        MOPSFinancialFilingAdapter().parse(BROKER, request_for("5864", 2020, 1, "A"))
    assert error.value.reason_code == "financial_industry_issuer"


def test_a_filer_outside_the_v1_universe_is_refused() -> None:
    with pytest.raises(SourceDataError) as error:
        MOPSFinancialFilingAdapter().parse(EMERGING, request_for("6785", 2020, 2, "A"))
    assert error.value.reason_code == "outside_v1_universe"


def test_the_document_must_be_the_one_that_was_requested() -> None:
    with pytest.raises(SourceDataError) as error:
        MOPSFinancialFilingAdapter().parse(CEMENT_Q1, request_for("1102", 2025, 1))
    assert error.value.reason_code == "identity_mismatch"


def test_no_such_report_is_a_source_answer_not_a_parse_failure() -> None:
    with pytest.raises(SourceDataError) as error:
        MOPSFinancialFilingAdapter().parse(NO_REPORT, request_for("1101", 2025, 1, "A"))
    assert error.value.reason_code == "no_such_report"


def test_the_filing_key_identifies_one_source_revision() -> None:
    adapter = MOPSFinancialFilingAdapter()
    parsed = adapter.parse(CEMENT_Q1, request_for("1101", 2025, 1))
    again = adapter.parse(CEMENT_Q1, request_for("1101", 2025, 1))
    assert parsed.filing_key == again.filing_key
    assert parsed.filing_key.startswith("1101:2025Q1:C:")
    # A different quarter of the same filer is a different filing.
    assert adapter.parse(CEMENT_Q3, request_for("1101", 2025, 3)).filing_key != (
        parsed.filing_key
    )


def test_a_corrected_document_is_a_new_source_revision() -> None:
    adapter = MOPSFinancialFilingAdapter()
    original = adapter.parse(CEMENT_Q1, request_for("1101", 2025, 1))
    corrected = adapter.parse(
        CEMENT_Q1.replace(b">89,680,417<", b">89,680,418<"),
        request_for("1101", 2025, 1),
    )
    assert corrected.filing_key != original.filing_key
    assert corrected.filing_key.startswith("1101:2025Q1:C:")


def test_the_archive_reads_the_same_document_through_the_same_contract() -> None:
    adapter = LegacyFinancialFilingArchiveAdapter()
    official = MOPSFinancialFilingAdapter().parse(
        CEMENT_Q1, request_for("1101", 2025, 1)
    )
    archived = adapter.parse(
        CEMENT_Q1, FinancialFilingArchiveRequest("1101", 2025, 1)
    )
    assert adapter.source == MOPSFinancialFilingAdapter().source
    assert archived.filing_key == official.filing_key
    assert len(archived.facts) == len(official.facts)


def test_the_archive_locates_one_document_per_filing(tmp_path: Path) -> None:
    folder = tmp_path / "2025" / "2025Q1"
    folder.mkdir(parents=True)
    (folder / "2025Q1_1101_20250515.html").write_bytes(CEMENT_Q1)
    adapter = LegacyFinancialFilingArchiveAdapter(archive_root=tmp_path)
    resource = adapter.resource(FinancialFilingArchiveRequest("1101", 2025, 1))
    assert resource.source_uri == str(folder / "2025Q1_1101_20250515.html")
    assert resource.resource_key == (
        "mops_t164sb01:financial_filing_archive:1101:2025Q1"
    )


def test_two_archive_copies_of_one_filing_fail_closed(tmp_path: Path) -> None:
    folder = tmp_path / "2025" / "2025Q1"
    folder.mkdir(parents=True)
    (folder / "2025Q1_1101_20250515.html").write_bytes(CEMENT_Q1)
    (folder / "2025Q1_1101_20250516.html").write_bytes(CEMENT_Q1)
    adapter = LegacyFinancialFilingArchiveAdapter(archive_root=tmp_path)
    with pytest.raises(SourceDataError) as error:
        adapter.resource(FinancialFilingArchiveRequest("1101", 2025, 1))
    assert error.value.reason_code == "ambiguous_archive_file"
