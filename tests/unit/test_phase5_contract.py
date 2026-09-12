from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.financials import (
    EPSPeriodBasis,
    FilingPeriod,
    FinancialFactObservation,
    FinancialFilingObservation,
    XBRLContext,
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
