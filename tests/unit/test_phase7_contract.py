from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.institutional_financing import (
    ForeignHoldingObservation,
    InstitutionalInvestorObservation,
    InstitutionalMarketSummaryObservation,
    MarginTradingObservation,
    SecuritiesLendingObservation,
)
from stock_data_center.db.metadata import metadata


ROOT = Path(__file__).resolve().parents[2]


def test_signed_source_fields_are_preserved() -> None:
    institutional = InstitutionalInvestorObservation(
        trade_date=date(2025, 6, 2),
        foreign_buy=Decimal("100"),
        foreign_sell=Decimal("150"),
        foreign_net=Decimal("-50"),
    )
    lending = SecuritiesLendingObservation(
        trade_date=date(2025, 6, 2),
        previous_balance=Decimal("1000"),
        adjustment=Decimal("-25"),
    )
    assert institutional.foreign_net == Decimal("-50")
    assert lending.adjustment == Decimal("-25")


@pytest.mark.parametrize(
    "factory",
    [
        lambda: InstitutionalInvestorObservation(
            trade_date=date(2025, 6, 2), foreign_buy=Decimal("-1")
        ),
        lambda: InstitutionalMarketSummaryObservation(
            trade_date=date(2025, 6, 2),
            market="TWSE",
            institution="foreign",
            buy=Decimal("-1"),
        ),
        lambda: MarginTradingObservation(
            trade_date=date(2025, 6, 2), margin_balance=Decimal("-1")
        ),
        lambda: ForeignHoldingObservation(
            trade_date=date(2025, 6, 2), held_ratio=Decimal("100.00000001")
        ),
        lambda: SecuritiesLendingObservation(
            trade_date=date(2025, 6, 2), balance=Decimal("1.5")
        ),
    ],
)
def test_invalid_source_quantities_are_rejected(factory) -> None:
    with pytest.raises(ValueError):
        factory()


def test_empty_source_records_are_rejected() -> None:
    with pytest.raises(ValueError):
        MarginTradingObservation(trade_date=date(2025, 6, 2))


def test_phase7_owns_observed_facts_not_derived_signals() -> None:
    contract = (ROOT / "docs" / "institutional_financing.md").read_text()
    assert "margin_pressure_score" in contract
    assert "Phase 9" in contract
    assert "shareholding concentration" in contract
    assert "sealed TDCC" in contract


def test_phase7_package_has_no_cache_http_or_model_dependency() -> None:
    package = ROOT / "src" / "stock_data_center" / "institutional_financing"
    source = "\n".join(path.read_text() for path in package.glob("*.py"))
    for forbidden in ("redis", "fastapi", "selection_score", "training"):
        assert forbidden not in source.lower()


def test_legacy_observed_dependencies_have_explicit_storage_columns() -> None:
    expected = {
        "institutional_investor_versions": {
            "foreign_buy", "foreign_sell", "foreign_net",
            "foreign_dealer_buy", "foreign_dealer_sell", "foreign_dealer_net",
            "trust_buy", "trust_sell", "trust_net",
            "dealer_self_buy", "dealer_self_sell", "dealer_self_net",
            "dealer_hedge_buy", "dealer_hedge_sell", "dealer_hedge_net",
            "dealer_net", "total_net",
        },
        "foreign_holding_versions": {
            "issued_shares", "investable_shares", "held_shares",
            "investable_ratio", "held_ratio", "foreign_legal_limit_ratio",
            "mainland_legal_limit_ratio", "change_reason",
            "source_last_update_date",
        },
        "margin_trading_versions": {
            "margin_buy", "margin_sell", "margin_cash_repayment",
            "margin_previous_balance", "margin_balance", "margin_next_limit",
            "margin_utilization_ratio", "short_buy", "short_sell",
            "short_stock_repayment", "short_previous_balance", "short_balance",
            "short_next_limit", "short_utilization_ratio", "offset_balance",
        },
        "securities_lending_versions": {
            "previous_balance", "borrowed", "returned", "balance",
            "next_limit", "next_available_limit", "adjustment", "note",
        },
    }
    for table_name, columns in expected.items():
        assert columns <= set(metadata.tables[table_name].c.keys())
