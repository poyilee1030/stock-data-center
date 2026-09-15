"""PR #14 — every stored column maps to audited source reality.

The storage contract, the source field audit, and the live schema must agree.
A column added to an observed `*_versions` table without a source mapping fails
here.
"""

import json
import re
from pathlib import Path

from stock_data_center.db.metadata import metadata


ROOT = Path(__file__).resolve().parents[2]
INVENTORY_JSON = ROOT / "docs/data_domain_inventory.json"
INVENTORY_MD = ROOT / "docs/data_domain_inventory.md"
AUDIT_MD = ROOT / "docs/source_field_audit.md"

STRUCTURAL_COLUMNS = {
    "id",
    "source",
    "business_content_hash",
    "ingested_at",
    "raw_artifact_id",
    "ingest_run_id",
    "predecessor_version_id",
}

COVERAGE_VALUES = {"sourced", "partially_sourced", "unsourced", "internal"}
AUDITED_COVERAGE = {"sourced", "partially_sourced", "unsourced"}

AUDIT_SECTION_5_STATUS = {
    "unsourced": "unsourced",
    "partially sourced": "partially_sourced",
}


def storage_contract() -> dict:
    return json.loads(INVENTORY_JSON.read_text())["storage_contract"]


def schema_versions_tables() -> set[str]:
    return {name for name in metadata.tables if name.endswith("_versions")}


def audit_section(number: str) -> str:
    """Return the body of one numbered audit section."""
    text = AUDIT_MD.read_text()
    match = re.search(
        rf"^#+ {re.escape(number)}\.? .*?$(.*?)(?=^#{{1,4}} \d|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"audit section {number} is missing"
    return match.group(1)


def audit_coverage_exceptions() -> dict[tuple[str, str], str]:
    """`(table, column) -> status` as declared by audit section 5."""
    rows = {}
    for line in audit_section("5").splitlines():
        match = re.match(
            r"\| `([a-z0-9_]+)\.([a-z0-9_]+)` \| (unsourced|partially sourced) \|",
            line.strip(),
        )
        if match:
            table, column, status = match.groups()
            rows[(table, column)] = AUDIT_SECTION_5_STATUS[status]
    return rows


def test_every_versions_table_is_classified_or_explicitly_excluded() -> None:
    contract = storage_contract()
    covered = set(contract["tables"])
    excluded = set(contract["excluded_tables"])

    assert not covered & excluded
    assert covered | excluded == schema_versions_tables()
    for table, reason in contract["excluded_tables"].items():
        assert reason.strip(), table


def test_structural_columns_are_declared_once() -> None:
    assert set(storage_contract()["structural_columns"]) == STRUCTURAL_COLUMNS


def test_every_stored_column_has_a_source_coverage_entry() -> None:
    contract = storage_contract()
    for table, entry in contract["tables"].items():
        actual = {
            column.name
            for column in metadata.tables[table].columns
            if column.name not in STRUCTURAL_COLUMNS
        }
        assert set(entry["columns"]) == actual, table


def test_every_coverage_entry_is_complete_and_valid() -> None:
    contract = storage_contract()
    audit_text = AUDIT_MD.read_text()
    for table, entry in contract["tables"].items():
        assert isinstance(entry["v1"], bool), table
        assert entry["reason"].strip(), table
        for column, record in entry["columns"].items():
            where = f"{table}.{column}"
            coverage = record["coverage"]
            assert coverage in COVERAGE_VALUES, where
            assert record["note"].strip(), where
            if coverage in {"sourced", "partially_sourced"}:
                assert record["source_fields"], where
                assert all(field.strip() for field in record["source_fields"]), where
                assert record["audit_section"] in audit_text, where


def test_referenced_audit_sections_exist() -> None:
    contract = storage_contract()
    referenced = {
        record["audit_section"]
        for entry in contract["tables"].values()
        for record in entry["columns"].values()
        if record.get("audit_section")
    }
    for number in sorted(referenced):
        assert audit_section(number).strip(), number


def test_unsourced_and_partial_columns_match_the_audit() -> None:
    contract = storage_contract()
    declared = {
        (table, column): record["coverage"]
        for table, entry in contract["tables"].items()
        for column, record in entry["columns"].items()
        if record["coverage"] in {"unsourced", "partially_sourced"}
    }
    assert declared == audit_coverage_exceptions()


def test_scope_corrections_of_pr_14() -> None:
    """The specific claims ROADMAP PR #14 exists to correct."""
    tables = storage_contract()["tables"]

    daily = tables["daily_price_versions"]["columns"]
    assert daily["bid_snapshot"]["coverage"] == "unsourced"
    assert daily["ask_snapshot"]["coverage"] == "unsourced"
    assert daily["last_bid_price"]["coverage"] == "sourced"
    assert daily["last_ask_price"]["coverage"] == "sourced"
    assert daily["last_bid_volume"]["coverage"] == "partially_sourced"
    assert daily["last_ask_volume"]["coverage"] == "partially_sourced"
    assert daily["price_direction"]["coverage"] == "partially_sourced"

    index = tables["market_index_versions"]["columns"]
    for column in ("open_value", "high_value", "low_value"):
        assert index[column]["coverage"] == "partially_sourced"
        assert "MI_5MINS_HIST" in index[column]["note"]
    assert index["trade_value"]["coverage"] == "unsourced"

    index_metadata = tables["market_index_metadata_versions"]["columns"]
    assert index_metadata["effective_from"]["coverage"] == "unsourced"
    assert index_metadata["effective_to"]["coverage"] == "unsourced"

    revenue = tables["monthly_revenue_versions"]["columns"]
    assert revenue["currency"]["coverage"] == "unsourced"
    assert revenue["revenue"]["coverage"] == "sourced"

    action = tables["corporate_action_versions"]["columns"]
    for column in (
        "announcement_date",
        "record_date",
        "payment_date",
        "earnings_stock_ratio",
        "capital_surplus_stock_ratio",
    ):
        assert action[column]["coverage"] == "unsourced", column

    assert tables["security_tag_versions"]["v1"] is False
    assert tables["xbrl_concept_catalog_versions"]["v1"] is False


def test_legacy_field_dispositions_follow_source_reality() -> None:
    fields = {
        (item["legacy_table"], item["legacy_field"]): item
        for item in json.loads(INVENTORY_JSON.read_text())["fields"]
    }

    # The one published order-book level, not a depth blob.
    assert fields[("daily_quotes", "bid")]["target"] == (
        "daily_price_versions.last_bid_price"
    )
    assert fields[("daily_quotes", "ask")]["target"] == (
        "daily_price_versions.last_ask_price"
    )

    # Published comparatives are source-published rows, not derived values.
    comparatives = {
        "revenue_last_month": "monthly_revenue_versions.revenue_last_month",
        "revenue_last_year": "monthly_revenue_versions.revenue_last_year_month",
        "mom_pct": "monthly_revenue_versions.mom_pct",
        "yoy_pct": "monthly_revenue_versions.yoy_pct",
        "revenue_cumulative": "monthly_revenue_versions.cumulative_revenue",
        "revenue_cumulative_last_year": (
            "monthly_revenue_versions.cumulative_revenue_last_year"
        ),
        "cumulative_yoy_pct": "monthly_revenue_versions.cumulative_yoy_pct",
        "comment": "monthly_revenue_versions.note",
    }
    for legacy_field, target in comparatives.items():
        item = fields[("monthly_revenue", legacy_field)]
        assert item["disposition"] == "observed", legacy_field
        assert item["target"] == target, legacy_field

    # Domains ROADMAP §16 keeps out of v1.
    for legacy_table in ("stock_tags", "xbrl_codebook"):
        for (table, field), item in fields.items():
            if table == legacy_table and item["disposition"] == "observed":
                raise AssertionError(f"{table}.{field} is still observed")


def test_markdown_inventory_states_the_v1_exclusions_and_new_domain() -> None:
    inventory = INVENTORY_MD.read_text()
    assert "`dividend_declaration_versions`" in inventory
    assert "not in v1" in inventory
    for name in ("`security_tag`", "`xbrl_concept_catalog`", "`margin_market_summary:v1`"):
        assert name in inventory, name
