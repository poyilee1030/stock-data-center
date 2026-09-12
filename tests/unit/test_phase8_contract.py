from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.market_reference import (
    AmountScale,
    CorporateActionObservation,
    MarketIndexObservation,
    OfficialValuationObservation,
    SourceTwdAmount,
    TwdAmount,
)


ROOT = Path(__file__).resolve().parents[2]


def twd(value: str) -> TwdAmount:
    return TwdAmount(Decimal(value))


def test_source_amounts_normalize_to_canonical_twd_major_units() -> None:
    thousands = SourceTwdAmount(Decimal("500"), AmountScale.THOUSAND).to_canonical()
    major = SourceTwdAmount(Decimal("500000"), AmountScale.MAJOR).to_canonical()
    assert thousands == major == twd("500000")


def test_monetary_fields_reject_ambiguous_raw_decimals() -> None:
    with pytest.raises(ValueError, match="canonical TwdAmount"):
        MarketIndexObservation(
            trade_date=date(2026, 9, 10),
            close_value=Decimal("25000"),
            trade_value=Decimal("500"),  # type: ignore[arg-type]
        )


def test_corporate_action_has_distinct_announcement_and_effective_dates() -> None:
    action = CorporateActionObservation(
        action_type="cash_dividend",
        announcement_date=date(2026, 8, 1),
        ex_date=date(2026, 9, 10),
        cash_dividend_per_share=twd("3.5"),
    )
    assert action.announcement_date < action.ex_date
    with pytest.raises(ValueError, match="must not follow"):
        CorporateActionObservation(
            action_type="cash_dividend",
            announcement_date=date(2026, 9, 11),
            ex_date=date(2026, 9, 10),
            cash_dividend_per_share=twd("3.5"),
        )


def test_official_valuation_requires_a_source_published_value() -> None:
    with pytest.raises(ValueError, match="source-published"):
        OfficialValuationObservation(trade_date=date(2026, 9, 10))


def test_phase8_contract_keeps_observed_and_computed_valuation_distinct() -> None:
    contract = (ROOT / "docs" / "market_reference.md").read_text()
    assert "official_valuation_versions" in contract
    assert "valuation_metrics:v1" in contract
    assert "source-published" in contract
    assert "computed" in contract
    assert "shares per share" in contract
    assert "effective" in contract
    assert "announcement" in contract


def test_phase8_package_has_no_cache_http_or_calculator_dependency() -> None:
    package = ROOT / "src" / "stock_data_center" / "market_reference"
    source = "\n".join(path.read_text() for path in package.glob("*.py"))
    for forbidden in ("redis", "fastapi", "calculator", "selection_score", "training"):
        assert forbidden not in source.lower()
