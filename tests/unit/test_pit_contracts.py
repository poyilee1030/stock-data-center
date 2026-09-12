from stock_data_center.db.metadata import publication_evidence
from stock_data_center.pit.contracts import DATASET_CONTRACTS


EXPECTED_OBSERVED_DATASETS = {
    "security_metadata",
    "daily_price",
    "monthly_revenue",
    "financial_filing",
    "tdcc_snapshot",
    "institutional_investor",
    "foreign_holding",
    "institutional_market_summary",
    "margin_trading",
    "securities_lending",
    "market_index",
    "market_index_metadata",
    "corporate_action",
    "official_valuation",
    "security_tag",
    "xbrl_concept_catalog",
}


def test_pit_registry_covers_every_observed_version_domain() -> None:
    assert set(DATASET_CONTRACTS) == EXPECTED_OBSERVED_DATASETS
    for contract in DATASET_CONTRACTS.values():
        columns = contract.version_table.c
        assert "id" in columns
        assert "source" in columns
        assert "business_content_hash" in columns
        assert "raw_artifact_id" in columns
        assert "ingest_run_id" in columns
        assert set(contract.logical_key_columns) <= set(columns.keys())
        assert contract.evidence_target_column in publication_evidence.c
        if contract.is_aggregate:
            assert contract.seal_table is not None
            assert contract.seal_version_column in contract.seal_table.c
        else:
            assert contract.ingestion_column in columns
