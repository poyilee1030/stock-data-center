from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.market_reference import (
    AmountScale,
    CorporateActionObservation,
    MarketIndexMetadataObservation,
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


def test_taiwan_stock_distributions_preserve_legal_source_categories() -> None:
    earnings = CorporateActionObservation(
        action_type="earnings_stock_dividend",
        earnings_stock_ratio=Decimal("0.10"),
        free_share_ratio=Decimal("0.10"),
        source_event_type="盈餘配股",
    )
    capital = CorporateActionObservation(
        action_type="capital_surplus_stock_dividend",
        capital_surplus_stock_ratio=Decimal("0.05"),
        free_share_ratio=Decimal("0.05"),
        source_event_type="資本公積配股",
    )
    assert earnings.action_type != capital.action_type
    assert earnings.earnings_stock_ratio == Decimal("0.10")
    assert capital.capital_surplus_stock_ratio == Decimal("0.05")


def test_split_reverse_split_and_capital_reduction_are_distinct() -> None:
    split = CorporateActionObservation(
        action_type="stock_split", old_shares=Decimal("1"), new_shares=Decimal("2")
    )
    reverse = CorporateActionObservation(
        action_type="reverse_split", old_shares=Decimal("2"), new_shares=Decimal("1")
    )
    reduction = CorporateActionObservation(
        action_type="capital_reduction",
        capital_reduction_kind="loss_offset",
        old_shares=Decimal("1000"),
        new_shares=Decimal("800"),
    )
    assert {split.action_type, reverse.action_type, reduction.action_type} == {
        "stock_split",
        "reverse_split",
        "capital_reduction",
    }


@pytest.mark.parametrize(
    ("action_type", "old_shares", "new_shares"),
    (
        ("stock_split", "2", "1"),
        ("reverse_split", "1", "2"),
        ("capital_reduction", "1", "2"),
    ),
)
def test_share_change_direction_is_enforced(
    action_type: str, old_shares: str, new_shares: str
) -> None:
    with pytest.raises(ValueError, match="shares|increase"):
        CorporateActionObservation(
            action_type=action_type,  # type: ignore[arg-type]
            capital_reduction_kind=(
                "loss_offset" if action_type == "capital_reduction" else None
            ),
            old_shares=Decimal(old_shares),
            new_shares=Decimal(new_shares),
        )


def test_cash_refund_and_loss_offset_reductions_are_explicit() -> None:
    cash_refund = CorporateActionObservation(
        action_type="capital_reduction",
        capital_reduction_kind="cash_refund",
        old_shares=Decimal("1"),
        new_shares=Decimal("0.8"),
        capital_reduction_cash_return_per_share=twd("2"),
    )
    loss_offset = CorporateActionObservation(
        action_type="capital_reduction",
        capital_reduction_kind="loss_offset",
        old_shares=Decimal("1"),
        new_shares=Decimal("0.8"),
    )
    combined = CorporateActionObservation(
        action_type="capital_reduction",
        capital_reduction_kind="loss_offset_with_cash_increase",
        old_shares=Decimal("1"),
        new_shares=Decimal("0.8"),
        rights_ratio=Decimal("0.25"),
        subscription_price=twd("10"),
    )
    assert cash_refund.capital_reduction_cash_return_per_share == twd("2")
    assert loss_offset.capital_reduction_cash_return_per_share is None
    assert combined.rights_ratio == Decimal("0.25")


def test_cash_refund_reduction_requires_positive_return_per_share() -> None:
    with pytest.raises(ValueError, match="cash_refund requires"):
        CorporateActionObservation(
            action_type="capital_reduction",
            capital_reduction_kind="cash_refund",
            old_shares=Decimal("1"),
            new_shares=Decimal("0.8"),
        )
    with pytest.raises(ValueError, match="must be positive"):
        CorporateActionObservation(
            action_type="capital_reduction",
            capital_reduction_kind="cash_refund",
            old_shares=Decimal("1"),
            new_shares=Decimal("0.8"),
            capital_reduction_cash_return_per_share=twd("0"),
        )
    with pytest.raises(ValueError, match="must not be negative"):
        CorporateActionObservation(
            action_type="capital_reduction",
            capital_reduction_kind="cash_refund",
            old_shares=Decimal("1"),
            new_shares=Decimal("0.8"),
            capital_reduction_cash_return_per_share=twd("-1"),
        )


def test_rights_and_combined_ex_event_require_explicit_terms() -> None:
    rights = CorporateActionObservation(
        action_type="rights_issue",
        rights_ratio=Decimal("0.20"),
        subscription_price=twd("25"),
        official_reference_price=twd("42"),
        official_rights_dividend_value=twd("3"),
        source_terms={"source_label": "現金增資"},
    )
    combined = CorporateActionObservation(
        action_type="ex_right_dividend", ex_date=date(2026, 9, 10)
    )
    assert rights.rights_ratio == Decimal("0.20")
    assert combined.ex_date == date(2026, 9, 10)
    with pytest.raises(ValueError, match="explicit economic term"):
        CorporateActionObservation(action_type="rights_issue")


def test_ambiguous_legacy_action_types_are_rejected_for_new_observations() -> None:
    for action_type in ("stock_dividend", "rights"):
        with pytest.raises(ValueError, match="unsupported corporate action type"):
            CorporateActionObservation(
                action_type=action_type,  # type: ignore[arg-type]
                ex_date=date(2026, 9, 10),
                source_terms={"legacy": True},
            )


def test_index_name_is_versioned_metadata() -> None:
    old = MarketIndexMetadataObservation(
        effective_from=date(2020, 1, 1), market="TWSE", name="觀光事業類指數"
    )
    renamed = MarketIndexMetadataObservation(
        effective_from=date(2023, 7, 3), market="TWSE", name="觀光餐旅類指數"
    )
    assert old.name != renamed.name


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
    assert "source_event_key" in contract
    assert "action_type + ex_date" in contract
    assert "market_index_metadata" in contract


def test_phase8_package_has_no_cache_http_or_calculator_dependency() -> None:
    package = ROOT / "src" / "stock_data_center" / "market_reference"
    source = "\n".join(path.read_text() for path in package.glob("*.py"))
    for forbidden in ("redis", "fastapi", "calculator", "selection_score", "training"):
        assert forbidden not in source.lower()
