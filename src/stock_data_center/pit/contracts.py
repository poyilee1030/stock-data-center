"""Dataset-specific storage facts consumed by the generic PIT resolver."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Table

from stock_data_center.db import metadata
from stock_data_center.pit.errors import UnknownDatasetError


@dataclass(frozen=True, slots=True)
class DatasetContract:
    dataset_code: str
    version_table: Table
    evidence_target_column: str
    logical_key_columns: tuple[str, ...]
    ingestion_column: str | None = "ingested_at"
    seal_table: Table | None = None
    seal_version_column: str | None = None

    @property
    def is_aggregate(self) -> bool:
        return self.seal_table is not None


def _table(name: str) -> Table:
    return metadata.tables[name]


def _single(
    dataset: str, table: str, target: str, *logical_key: str
) -> DatasetContract:
    return DatasetContract(dataset, _table(table), target, tuple(logical_key))


DATASET_CONTRACTS: dict[str, DatasetContract] = {
    contract.dataset_code: contract
    for contract in (
        _single(
            "security_metadata",
            "security_metadata_versions",
            "security_metadata_version_id",
            "security_id",
            "effective_from",
        ),
        _single(
            "daily_price",
            "daily_price_versions",
            "daily_price_version_id",
            "security_id",
            "trade_date",
        ),
        _single(
            "monthly_revenue",
            "monthly_revenue_versions",
            "monthly_revenue_version_id",
            "security_id",
            "revenue_year",
            "revenue_month",
        ),
        DatasetContract(
            "financial_filing",
            _table("financial_filing_versions"),
            "financial_filing_version_id",
            ("security_id", "report_year", "report_quarter"),
            None,
            _table("financial_filing_seals"),
            "filing_version_id",
        ),
        DatasetContract(
            "tdcc_snapshot",
            _table("tdcc_snapshot_versions"),
            "tdcc_snapshot_version_id",
            ("security_id", "snapshot_date"),
            None,
            _table("tdcc_snapshot_seals"),
            "snapshot_version_id",
        ),
        _single(
            "institutional_investor",
            "institutional_investor_versions",
            "institutional_investor_version_id",
            "security_id",
            "trade_date",
        ),
        _single(
            "foreign_holding",
            "foreign_holding_versions",
            "foreign_holding_version_id",
            "security_id",
            "trade_date",
        ),
        _single(
            "institutional_market_summary",
            "institutional_market_summary_versions",
            "institutional_market_summary_version_id",
            "market",
            "trade_date",
            "institution",
        ),
        _single(
            "margin_trading",
            "margin_trading_versions",
            "margin_trading_version_id",
            "security_id",
            "trade_date",
        ),
        _single(
            "securities_lending",
            "securities_lending_versions",
            "securities_lending_version_id",
            "security_id",
            "trade_date",
        ),
        _single(
            "market_index",
            "market_index_versions",
            "market_index_version_id",
            "market_index_id",
            "trade_date",
        ),
        _single(
            "corporate_action",
            "corporate_action_versions",
            "corporate_action_version_id",
            "security_id",
            "action_type",
            "ex_date",
        ),
        _single(
            "official_valuation",
            "official_valuation_versions",
            "official_valuation_version_id",
            "security_id",
            "trade_date",
        ),
        _single(
            "security_tag",
            "security_tag_versions",
            "security_tag_version_id",
            "security_id",
            "tag",
            "effective_from",
        ),
        _single(
            "xbrl_concept_catalog",
            "xbrl_concept_catalog_versions",
            "xbrl_concept_catalog_version_id",
            "concept_qname",
        ),
    )
}


def get_contract(dataset_code: str) -> DatasetContract:
    try:
        return DATASET_CONTRACTS[dataset_code]
    except KeyError as error:
        raise UnknownDatasetError(
            f"no PIT resolver contract for dataset {dataset_code!r}"
        ) from error
