from pathlib import Path

import pytest

from stock_data_center.monthly_revenue import RevenuePeriod


ROOT = Path(__file__).resolve().parents[2]


def test_revenue_period_validation() -> None:
    with pytest.raises(ValueError):
        RevenuePeriod(2025, 0)
    with pytest.raises(ValueError):
        RevenuePeriod(2025, 13)


def test_phase4_contract_keeps_derived_metrics_out_of_scope() -> None:
    contract = (ROOT / "docs" / "monthly_revenue.md").read_text()
    assert "monthly_revenue_growth:v1" in contract
    assert "Phase 9" in contract
    assert "computed_at" not in contract


def test_monthly_revenue_package_has_no_cache_or_http_dependency() -> None:
    package = ROOT / "src" / "stock_data_center" / "monthly_revenue"
    source = "\n".join(path.read_text() for path in package.glob("*.py"))
    assert "redis" not in source.lower()
    assert "fastapi" not in source.lower()
