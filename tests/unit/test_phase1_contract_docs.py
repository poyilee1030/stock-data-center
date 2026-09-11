from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

LEGACY_TABLES = {
    "balance_sheet_xbrl",
    "cash_flow_xbrl",
    "daily_quotes",
    "dealer_holding",
    "dividend",
    "foreign_holding",
    "income_statement_xbrl",
    "institutional_investors",
    "institutional_summary",
    "margin_pressure_analysis",
    "margin_sbl",
    "margin_summary",
    "margin_trading",
    "market_indices",
    "monthly_revenue",
    "pe_ratio",
    "quarterly_reports_xbrl",
    "shareholding",
    "shareholding_concentration",
    "short_interest_analysis",
    "stock_info",
    "stock_tags",
    "technical_indicators",
    "trust_holding",
    "valuation_daily",
    "xbrl_codebook",
}

REQUIRED_DERIVED_CONTRACTS = {
    "technical_indicators:v1",
    "shareholding_concentration:v1",
    "valuation_metrics:v1",
    "margin_metrics:v1",
    "short_interest_metrics:v1",
}


def test_inventory_names_every_known_legacy_table_and_required_derived_contract() -> None:
    inventory = (ROOT / "docs/data_domain_inventory.md").read_text()
    missing_legacy = {name for name in LEGACY_TABLES if f"`{name}`" not in inventory}
    missing_derived = {
        name for name in REQUIRED_DERIVED_CONTRACTS if f"`{name}`" not in inventory
    }
    assert not missing_legacy
    assert not missing_derived


def test_inventory_declares_no_unmapped_domain_and_explicit_backfill_status() -> None:
    inventory = (ROOT / "docs/data_domain_inventory.md").read_text()
    assert "No table or declared legacy field is intentionally left\nunmapped" in inventory
    assert "Backfill status" in inventory
    assert "not started" in inventory
