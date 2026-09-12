from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.financials import (
    EPSPeriodBasis,
    FilingPeriod,
    FinancialFactObservation,
    FinancialFilingObservation,
    SourceContextClassification,
    SourcePeriodRole,
    XBRLContext,
    classify_eps_period_basis,
)


ROOT = Path(__file__).resolve().parents[2]


def test_financial_value_types_reject_ambiguous_identity_and_values() -> None:
    context = XBRLContext(
        entity_identifier="TW-2330",
        period_type="instant",
        instant_date=date(2024, 12, 31),
    )
    with pytest.raises(ValueError, match="canonical"):
        FinancialFactObservation(
            concept_qname="BasicEPS",
            context=context,
            unit_identity="TWD/shares",
            numeric_value=Decimal("1"),
        )
    with pytest.raises(ValueError, match="exactly one"):
        FinancialFactObservation(
            concept_qname="{urn:test}BasicEPS",
            context=context,
            unit_identity="TWD/shares",
            numeric_value=Decimal("1"),
            text_value="1",
        )
    nil_fact = FinancialFactObservation(
        concept_qname="{urn:test}BasicEPS",
        context=context,
        unit_identity="TWD/shares",
        is_nil=True,
    )
    assert nil_fact.is_nil is True
    with pytest.raises(ValueError, match="nil facts"):
        FinancialFactObservation(
            concept_qname="{urn:test}BasicEPS",
            context=context,
            unit_identity="TWD/shares",
            numeric_value=Decimal("0"),
            is_nil=True,
        )


def test_financial_period_and_context_shapes_are_explicit() -> None:
    with pytest.raises(ValueError):
        FilingPeriod(2024, 5)
    with pytest.raises(ValueError, match="period shape"):
        XBRLContext(
            entity_identifier="TW-2330",
            period_type="duration",
            instant_date=date(2024, 12, 31),
        )
    observation = FinancialFilingObservation(
        filing_key="2024-q4-v1",
        report_year=2024,
        report_quarter=4,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        currency="twd",
    )
    assert observation.currency == "TWD"


def test_phase5_contract_defers_canonical_derived_calculators() -> None:
    contract = (ROOT / "docs" / "financial_xbrl.md").read_text()
    assert "basic_eps" in contract
    assert "later canonical-derived phase" in contract
    assert "calendar-quarter availability shortcut" in contract
    assert set(EPSPeriodBasis) == {
        EPSPeriodBasis.QUARTER,
        EPSPeriodBasis.YTD,
        EPSPeriodBasis.ANNUAL,
    }


def test_financial_package_has_no_cache_or_http_dependency() -> None:
    package = ROOT / "src" / "stock_data_center" / "financials"
    source = "\n".join(path.read_text() for path in package.glob("*.py"))
    assert "redis" not in source.lower()
    assert "fastapi" not in source.lower()


def source_classification(
    context: XBRLContext, role: SourcePeriodRole
) -> SourceContextClassification:
    assert context.period_start is not None and context.period_end is not None
    return SourceContextClassification(
        source_context_ref="mops-context-id",
        classifier_rule="mops-xbrl-context-role:v1",
        period_role=role,
        expected_start=context.period_start,
        expected_end=context.period_end,
    )


def test_source_classifier_accepts_valid_single_quarter_context() -> None:
    context = XBRLContext(
        entity_identifier="TW-2330",
        period_type="duration",
        period_start=date(2024, 4, 1),
        period_end=date(2024, 6, 30),
    )
    result = classify_eps_period_basis(
        context=context,
        filing_period_start=date(2024, 1, 1),
        filing_period_end=date(2024, 6, 30),
        report_quarter=2,
        classification=source_classification(
            context, SourcePeriodRole.CURRENT_SINGLE_QUARTER
        ),
    )
    assert result is EPSPeriodBasis.QUARTER


def test_source_classifier_never_treats_full_year_as_quarter() -> None:
    context = XBRLContext(
        entity_identifier="TW-2330",
        period_type="duration",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
    )
    annual = classify_eps_period_basis(
        context=context,
        filing_period_start=date(2024, 1, 1),
        filing_period_end=date(2024, 12, 31),
        report_quarter=4,
        classification=source_classification(
            context, SourcePeriodRole.CURRENT_FULL_YEAR
        ),
    )
    assert annual is EPSPeriodBasis.ANNUAL
    with pytest.raises(ValueError, match="implausible duration"):
        classify_eps_period_basis(
            context=context,
            filing_period_start=date(2024, 1, 1),
            filing_period_end=date(2024, 12, 31),
            report_quarter=4,
            classification=source_classification(
                context, SourcePeriodRole.CURRENT_SINGLE_QUARTER
            ),
        )


def test_source_classifier_keeps_long_ytd_context_out_of_quarter() -> None:
    context = XBRLContext(
        entity_identifier="TW-2330",
        period_type="duration",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 9, 30),
    )
    ytd = classify_eps_period_basis(
        context=context,
        filing_period_start=date(2024, 1, 1),
        filing_period_end=date(2024, 9, 30),
        report_quarter=3,
        classification=source_classification(
            context, SourcePeriodRole.CURRENT_YEAR_TO_DATE
        ),
    )
    assert ytd is EPSPeriodBasis.YTD
    with pytest.raises(ValueError, match="implausible duration"):
        classify_eps_period_basis(
            context=context,
            filing_period_start=date(2024, 1, 1),
            filing_period_end=date(2024, 9, 30),
            report_quarter=3,
            classification=source_classification(
                context, SourcePeriodRole.CURRENT_SINGLE_QUARTER
            ),
        )


def test_source_classifier_rejects_suspicious_short_context() -> None:
    context = XBRLContext(
        entity_identifier="TW-2330",
        period_type="duration",
        period_start=date(2024, 6, 1),
        period_end=date(2024, 6, 30),
    )
    with pytest.raises(ValueError, match="implausible duration"):
        classify_eps_period_basis(
            context=context,
            filing_period_start=date(2024, 1, 1),
            filing_period_end=date(2024, 6, 30),
            report_quarter=2,
            classification=source_classification(
                context, SourcePeriodRole.CURRENT_SINGLE_QUARTER
            ),
        )
