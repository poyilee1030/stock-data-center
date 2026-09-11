import hashlib
import json
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

LEGACY_SCHEMA_COLS_TABLE_COUNT = 27
LEGACY_SCHEMA_COLS_FIELD_COUNT = 416
LEGACY_SCHEMA_COLS_PAIR_SHA256 = (
    "a22c4a17f8b9f4dc35199fce9d988c68d7aa1391c81870c4b6070b436152840f"
)
ALLOWED_DISPOSITIONS = {
    "observed",
    "canonical_derived",
    "downstream",
    "raw_artifact_only",
    "deprecated",
    "identity",
    "publication_evidence",
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
    assert "No retained table or declared field is left\nunmapped" in inventory
    assert "Backfill status" in inventory
    assert "not started" in inventory


def test_machine_readable_inventory_covers_every_legacy_schema_cols_field() -> None:
    contract = json.loads((ROOT / "docs/data_domain_inventory.json").read_text())
    fields = contract["fields"]
    pairs = [(item["legacy_table"], item["legacy_field"]) for item in fields]
    canonical_pairs = sorted(pairs)
    fingerprint = hashlib.sha256(
        json.dumps(
            canonical_pairs, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()

    assert contract["format_version"] == 1
    assert contract["source"]["path"] == "common/schemas.py::SCHEMA_COLS"
    assert contract["source"]["table_count"] == LEGACY_SCHEMA_COLS_TABLE_COUNT
    assert contract["source"]["field_count"] == LEGACY_SCHEMA_COLS_FIELD_COUNT
    assert len(fields) == LEGACY_SCHEMA_COLS_FIELD_COUNT
    assert len(set(pairs)) == len(pairs)
    assert len({table for table, _ in pairs}) == LEGACY_SCHEMA_COLS_TABLE_COUNT
    assert fingerprint == LEGACY_SCHEMA_COLS_PAIR_SHA256
    assert contract["source"]["canonical_pair_sha256"] == fingerprint


def test_every_legacy_field_has_a_complete_valid_disposition() -> None:
    contract = json.loads((ROOT / "docs/data_domain_inventory.json").read_text())
    for item in contract["fields"]:
        assert item["disposition"] in ALLOWED_DISPOSITIONS, item
        assert item["target"].strip(), item
        assert item["reason"].strip(), item
        assert ".logical_date" not in item["target"], item
