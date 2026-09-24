"""Step 14 — every stored column maps to audited source reality.

The storage contract, the source field audit, and the live schema must agree.
A column added to a schema v2 table without a source mapping fails here
(Step 35-d-3 moved the contract from the v1 tables to the v2 ones).
"""

import json
import re
from pathlib import Path

from stock_data_center.db.schema_v2 import metadata


ROOT = Path(__file__).resolve().parents[2]
INVENTORY_JSON = ROOT / "docs/data_domain_inventory.json"
INVENTORY_MD = ROOT / "docs/data_domain_inventory.md"
AUDIT_MD = ROOT / "docs/source_field_audit.md"

# A v2 row's `source` is the dataset's request key and `financial_reports.id`
# a surrogate; neither comes from a source field.
STRUCTURAL_COLUMNS = {"id", "source"}

COVERAGE_VALUES = {"sourced", "partially_sourced", "unsourced", "internal"}
AUDITED_COVERAGE = {"sourced", "partially_sourced", "unsourced"}

AUDIT_SECTION_5_STATUS = {
    "unsourced": "unsourced",
    "partially sourced": "partially_sourced",
}

# What an unsourced column actually holds. "Unsourced" alone does not imply NULL:
# some columns are NOT NULL and hold a documented constant or a value derived from
# our own observations.
EFFECT_VALUES = {
    "stays NULL",
    "stores a documented constant",
    "stores a derived value",
    "table stays empty",
}

OBSERVED_TARGET = re.compile(r"^([a-z0-9_]+)\.([a-z0-9_]+)$")


def storage_contract() -> dict:
    return json.loads(INVENTORY_JSON.read_text())["storage_contract"]


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


def audit_coverage_exceptions() -> dict[tuple[str, str], tuple[str, str]]:
    """`(table, column) -> (status, effect)` as declared by audit section 5."""
    rows = {}
    body = audit_section("5")
    for line in body.splitlines():
        match = re.match(
            r"\| `([a-z0-9_]+)\.([a-z0-9_]+)` \| (unsourced|partially sourced) \| ([^|]+?) \|",
            line.strip(),
        )
        if match:
            table, column, status, effect = match.groups()
            rows[(table, column)] = (AUDIT_SECTION_5_STATUS[status], effect)
    # A section parser that silently reads half the table would surface only as a
    # confusing set inequality, so check it against the raw row count first.
    table_rows = sum(
        1 for line in body.splitlines() if line.strip().startswith("| `")
    )
    assert table_rows > 0, "audit section 5 parsed no rows"
    assert len(rows) == table_rows, "audit section 5 has rows this parser cannot read"
    return rows


def test_every_table_in_the_schema_is_classified_or_explicitly_excluded() -> None:
    """The guard is total: a new table cannot slip past it unnoticed."""
    contract = storage_contract()
    covered = set(contract["tables"])
    excluded = set(contract["excluded_tables"])

    assert not covered & excluded
    assert covered | excluded == set(metadata.tables)
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
            if coverage == "unsourced":
                assert record["effect"] in EFFECT_VALUES, where
                if not entry["v1"]:
                    assert record["effect"] == "table stays empty", where


def test_a_column_that_stays_null_is_actually_nullable() -> None:
    """"Unsourced" does not imply NULL, and the registry must not pretend it does."""
    for table, entry in storage_contract()["tables"].items():
        for column, record in entry["columns"].items():
            if record.get("effect") == "stays NULL":
                assert metadata.tables[table].columns[column].nullable, (
                    f"{table}.{column} is NOT NULL and cannot stay NULL"
                )


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
        (table, column): (
            record["coverage"],
            record.get("effect", "holds the values that exist"),
        )
        for table, entry in contract["tables"].items()
        for column, record in entry["columns"].items()
        if record["coverage"] in {"unsourced", "partially_sourced"}
    }
    assert declared == audit_coverage_exceptions()


def test_scope_corrections_of_pr_14() -> None:
    """The specific claims ROADMAP Step 14 exists to correct, as schema v2 holds them."""
    tables = storage_contract()["tables"]

    daily = tables["daily_prices"]["columns"]
    assert daily["last_bid_price"]["coverage"] == "sourced"
    assert daily["last_ask_price"]["coverage"] == "sourced"
    assert daily["last_bid_volume"]["coverage"] == "partially_sourced"
    assert daily["last_ask_volume"]["coverage"] == "partially_sourced"
    assert daily["price_direction"]["coverage"] == "partially_sourced"

    index = tables["index_prices"]["columns"]
    for column in ("open_value", "high_value", "low_value"):
        assert index[column]["coverage"] == "partially_sourced"
        assert "MI_5MINS_HIST" in index[column]["note"]

    assert tables["monthly_revenues"]["columns"]["revenue"]["coverage"] == "sourced"

    # Columns no source fills are not stored at all in v2 (ADR-0027), rather
    # than kept NULL: the order-book depth blobs, the corporate-action dates and
    # the earnings / capital-surplus split, index trade value, revenue currency.
    dropped = {
        "daily_prices": ("bid_snapshot", "ask_snapshot"),
        "corporate_actions": ("announcement_date", "record_date", "payment_date",
                              "earnings_stock_ratio", "capital_surplus_stock_ratio"),
        "index_prices": ("trade_value",),
        "monthly_revenues": ("currency",),
    }
    for table, columns in dropped.items():
        for column in columns:
            assert column not in metadata.tables[table].columns, f"{table}.{column}"


def test_legacy_field_dispositions_follow_source_reality() -> None:
    fields = {
        (item["legacy_table"], item["legacy_field"]): item
        for item in json.loads(INVENTORY_JSON.read_text())["fields"]
    }

    # The one published order-book level, not a depth blob.
    assert fields[("daily_quotes", "bid")]["target"] == "daily_prices.last_bid_price"
    assert fields[("daily_quotes", "ask")]["target"] == "daily_prices.last_ask_price"

    # Published comparatives are source-published rows, not derived values.
    comparatives = {
        "revenue_last_month": "monthly_revenues.revenue_last_month",
        "revenue_last_year": "monthly_revenues.revenue_last_year_month",
        "mom_pct": "monthly_revenues.mom_pct",
        "yoy_pct": "monthly_revenues.yoy_pct",
        "revenue_cumulative": "monthly_revenues.cumulative_revenue",
        "revenue_cumulative_last_year": "monthly_revenues.cumulative_revenue_last_year",
        "cumulative_yoy_pct": "monthly_revenues.cumulative_yoy_pct",
        "comment": "monthly_revenues.note",
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


def test_observed_targets_exist_in_the_schema_or_name_the_pr_that_adds_them() -> None:
    """A target column that exists nowhere must say which PR creates it."""
    for item in json.loads(INVENTORY_JSON.read_text())["fields"]:
        if item["disposition"] not in {"observed", "identity", "publication_time"}:
            continue
        match = OBSERVED_TARGET.match(item["target"])
        if match is None:
            continue
        table, column = match.groups()
        where = f"{item['legacy_table']}.{item['legacy_field']} -> {item['target']}"
        assert table in metadata.tables, where
        if column in metadata.tables[table].columns:
            assert "planned_pr" not in item, f"{where} already exists"
        else:
            assert isinstance(item.get("planned_pr"), int), where


def test_markdown_inventory_states_the_v1_exclusions_and_new_domain() -> None:
    inventory = INVENTORY_MD.read_text()
    assert "`dividend_declaration`" in inventory
    assert "not in v1" in inventory
    for name in ("`security_tag`", "`xbrl_concept_catalog`", "`margin_market_summary:v1`"):
        assert name in inventory, name
