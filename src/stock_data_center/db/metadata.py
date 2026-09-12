from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)

aware_timestamp = postgresql.TIMESTAMP(timezone=True)
uuid_type = postgresql.UUID(as_uuid=True)
jsonb_type = postgresql.JSONB()


dataset_catalog = sa.Table(
    "dataset_catalog",
    metadata,
    sa.Column("dataset_code", sa.String(64), primary_key=True),
    sa.Column("description", sa.Text(), nullable=False),
    sa.Column("schema_version", sa.String(32), nullable=False),
    sa.Column(
        "created_at",
        aware_timestamp,
        nullable=False,
        server_default=sa.text("statement_timestamp()"),
    ),
)

dataset_sources = sa.Table(
    "dataset_sources",
    metadata,
    sa.Column("dataset_code", sa.String(64), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("supports_market_pit", sa.Boolean(), nullable=False),
    sa.Column("supports_system_pit", sa.Boolean(), nullable=False),
    sa.Column("publication_time_quality", sa.SmallInteger(), nullable=False),
    sa.Column("evidence_status", sa.String(32), nullable=False),
    sa.Column(
        "accepted_evidence_types",
        postgresql.ARRAY(sa.String(64)),
        nullable=False,
        server_default=sa.text("ARRAY['official']::varchar[]"),
    ),
    sa.Column("is_canonical", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column(
        "created_at",
        aware_timestamp,
        nullable=False,
        server_default=sa.text("statement_timestamp()"),
    ),
    sa.PrimaryKeyConstraint("dataset_code", "source"),
    sa.ForeignKeyConstraint(
        ["dataset_code"], ["dataset_catalog.dataset_code"], ondelete="RESTRICT"
    ),
    sa.CheckConstraint(
        "publication_time_quality BETWEEN 0 AND 100",
        name="publication_time_quality_range",
    ),
    sa.CheckConstraint(
        "evidence_status IN ('unverified', 'verified', 'disabled')",
        name="evidence_status_value",
    ),
    sa.CheckConstraint(
        "cardinality(accepted_evidence_types) > 0 "
        "AND array_position(accepted_evidence_types, NULL) IS NULL "
        "AND array_position(accepted_evidence_types, '') IS NULL",
        name="accepted_evidence_types_valid",
    ),
)
sa.Index(
    "uq_dataset_sources_one_canonical",
    dataset_sources.c.dataset_code,
    unique=True,
    postgresql_where=dataset_sources.c.is_canonical.is_(True),
)

security = sa.Table(
    "security",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_code", sa.String(32), nullable=False, unique=True),
    sa.Column(
        "created_at",
        aware_timestamp,
        nullable=False,
        server_default=sa.text("statement_timestamp()"),
    ),
    sa.CheckConstraint("security_code <> ''", name="security_code_nonempty"),
)

ingest_runs = sa.Table(
    "ingest_runs",
    metadata,
    sa.Column(
        "id",
        uuid_type,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    ),
    sa.Column("dataset_code", sa.String(64), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("status", sa.String(24), nullable=False),
    sa.Column("started_at", aware_timestamp, nullable=False),
    sa.Column("completed_at", aware_timestamp),
    sa.Column("run_metadata", jsonb_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.ForeignKeyConstraint(
        ["dataset_code", "source"],
        ["dataset_sources.dataset_code", "dataset_sources.source"],
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "status IN ('running', 'succeeded', 'failed')", name="status_value"
    ),
    sa.CheckConstraint(
        "completed_at IS NULL OR completed_at >= started_at",
        name="completed_after_started",
    ),
)

raw_artifacts = sa.Table(
    "raw_artifacts",
    metadata,
    sa.Column(
        "id",
        uuid_type,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    ),
    sa.Column("raw_artifact_hash", sa.CHAR(64), nullable=False, unique=True),
    sa.Column("storage_uri", sa.Text(), nullable=False, unique=True),
    sa.Column("byte_size", sa.BigInteger(), nullable=False),
    sa.Column("media_type", sa.String(255), nullable=False),
    sa.Column(
        "stored_at",
        aware_timestamp,
        nullable=False,
        server_default=sa.text("statement_timestamp()"),
    ),
    sa.CheckConstraint(
        "raw_artifact_hash ~ '^[0-9a-f]{64}$'", name="hash_lower_hex"
    ),
    sa.CheckConstraint("byte_size >= 0", name="byte_size_nonnegative"),
    sa.CheckConstraint(
        "position(raw_artifact_hash in storage_uri) > 0",
        name="content_addressed_uri",
    ),
)

raw_artifact_observations = sa.Table(
    "raw_artifact_observations",
    metadata,
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.Column("source_uri", sa.Text(), nullable=False),
    sa.Column("fetched_at", aware_timestamp, nullable=False),
    sa.PrimaryKeyConstraint("raw_artifact_id", "ingest_run_id"),
    sa.ForeignKeyConstraint(
        ["raw_artifact_id"], ["raw_artifacts.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(["ingest_run_id"], ["ingest_runs.id"], ondelete="RESTRICT"),
)


def lineage_constraints() -> tuple[sa.ForeignKeyConstraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["raw_artifact_id", "ingest_run_id"],
            [
                "raw_artifact_observations.raw_artifact_id",
                "raw_artifact_observations.ingest_run_id",
            ],
            ondelete="RESTRICT",
        ),
    )


security_metadata_versions = sa.Table(
    "security_metadata_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("effective_from", sa.Date(), nullable=False),
    sa.Column("effective_to", sa.Date()),
    sa.Column("market", sa.String(32), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("industry", sa.Text()),
    sa.Column("listed_on", sa.Date()),
    sa.Column("delisted_on", sa.Date()),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "security_id",
        "source",
        "effective_from",
        "business_content_hash",
        name="uq_security_metadata_business_revision",
    ),
    sa.CheckConstraint(
        "effective_to IS NULL OR effective_to >= effective_from",
        name="effective_range",
    ),
    sa.CheckConstraint(
        "delisted_on IS NULL OR listed_on IS NULL OR delisted_on >= listed_on",
        name="listing_range",
    ),
    sa.CheckConstraint("market <> ''", name="market_nonempty"),
)

daily_price_versions = sa.Table(
    "daily_price_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("trade_date", sa.Date(), nullable=False),
    sa.Column("open_price", sa.Numeric(20, 6)),
    sa.Column("high_price", sa.Numeric(20, 6)),
    sa.Column("low_price", sa.Numeric(20, 6)),
    sa.Column("close_price", sa.Numeric(20, 6)),
    sa.Column("volume", sa.Numeric(30, 0)),
    sa.Column("trade_value", sa.Numeric(30, 4)),
    sa.Column("trade_count", sa.BigInteger()),
    sa.Column("price_change", sa.Numeric(20, 6)),
    sa.Column("price_direction", sa.String(16)),
    sa.Column("bid_snapshot", sa.Text()),
    sa.Column("ask_snapshot", sa.Text()),
    sa.Column("last_bid_price", sa.Numeric(20, 6)),
    sa.Column("last_ask_price", sa.Numeric(20, 6)),
    sa.Column("last_bid_volume", sa.Numeric(30, 0)),
    sa.Column("last_ask_volume", sa.Numeric(30, 0)),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "security_id",
        "source",
        "trade_date",
        "business_content_hash",
        name="uq_daily_price_business_revision",
    ),
    sa.CheckConstraint("volume IS NULL OR volume >= 0", name="volume_nonnegative"),
    sa.CheckConstraint(
        "trade_value IS NULL OR trade_value >= 0", name="trade_value_nonnegative"
    ),
    sa.CheckConstraint(
        "trade_count IS NULL OR trade_count >= 0", name="trade_count_nonnegative"
    ),
    sa.CheckConstraint(
        "high_price IS NULL OR low_price IS NULL OR high_price >= low_price",
        name="high_not_below_low",
    ),
)

monthly_revenue_versions = sa.Table(
    "monthly_revenue_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("revenue_year", sa.SmallInteger(), nullable=False),
    sa.Column("revenue_month", sa.SmallInteger(), nullable=False),
    sa.Column("revenue", sa.Numeric(24, 4), nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "security_id",
        "source",
        "revenue_year",
        "revenue_month",
        "business_content_hash",
        name="uq_monthly_revenue_business_revision",
    ),
    sa.CheckConstraint("revenue_year BETWEEN 1900 AND 9999", name="year_range"),
    sa.CheckConstraint("revenue_month BETWEEN 1 AND 12", name="month_range"),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
)

monthly_revenue_version_observations = sa.Table(
    "monthly_revenue_version_observations",
    metadata,
    sa.Column("monthly_revenue_version_id", sa.BigInteger(), nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.PrimaryKeyConstraint(
        "monthly_revenue_version_id", "raw_artifact_id", "ingest_run_id"
    ),
    sa.ForeignKeyConstraint(
        ["monthly_revenue_version_id"],
        ["monthly_revenue_versions.id"],
        ondelete="RESTRICT",
    ),
    *lineage_constraints(),
)

financial_filing_versions = sa.Table(
    "financial_filing_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("filing_key", sa.String(128), nullable=False),
    sa.Column("report_year", sa.SmallInteger(), nullable=False),
    sa.Column("report_quarter", sa.SmallInteger(), nullable=False),
    sa.Column("period_start", sa.Date(), nullable=False),
    sa.Column("period_end", sa.Date(), nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("business_content_hash", sa.CHAR(64)),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint("source", "filing_key", name="uq_financial_filing_source_key"),
    sa.CheckConstraint("report_quarter BETWEEN 1 AND 4", name="quarter_range"),
    sa.CheckConstraint("period_end >= period_start", name="period_range"),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
)

financial_filing_version_observations = sa.Table(
    "financial_filing_version_observations",
    metadata,
    sa.Column("filing_version_id", sa.BigInteger(), nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.PrimaryKeyConstraint(
        "filing_version_id", "raw_artifact_id", "ingest_run_id"
    ),
    sa.ForeignKeyConstraint(
        ["filing_version_id"],
        ["financial_filing_versions.id"],
        ondelete="RESTRICT",
    ),
    *lineage_constraints(),
)

financial_facts = sa.Table(
    "financial_facts",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("filing_version_id", sa.BigInteger(), nullable=False),
    sa.Column("concept_qname", sa.Text(), nullable=False),
    sa.Column("context_hash", sa.CHAR(64), nullable=False),
    sa.Column("entity_identifier", sa.Text(), nullable=False),
    sa.Column("period_type", sa.String(16), nullable=False),
    sa.Column("instant_date", sa.Date()),
    sa.Column("period_start", sa.Date()),
    sa.Column("period_end", sa.Date()),
    sa.Column("explicit_dimensions", jsonb_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("typed_dimensions", jsonb_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("scenario", jsonb_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("segment", jsonb_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("unit_identity", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.Column("numeric_value", sa.Numeric()),
    sa.Column("text_value", sa.Text()),
    sa.Column("is_nil", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("decimals", sa.String(32)),
    sa.ForeignKeyConstraint(
        ["filing_version_id"], ["financial_filing_versions.id"], ondelete="RESTRICT"
    ),
    sa.UniqueConstraint(
        "filing_version_id",
        "concept_qname",
        "context_hash",
        "unit_identity",
        name="uq_financial_fact_identity",
    ),
    sa.UniqueConstraint(
        "id", "filing_version_id", name="uq_financial_fact_filing_identity"
    ),
    sa.CheckConstraint(
        "period_type IN ('instant', 'duration', 'forever')", name="period_type_value"
    ),
    sa.CheckConstraint(
        "(period_type = 'instant' AND instant_date IS NOT NULL AND period_start IS NULL AND period_end IS NULL) OR "
        "(period_type = 'duration' AND instant_date IS NULL AND period_start IS NOT NULL AND period_end IS NOT NULL AND period_end >= period_start) OR "
        "(period_type = 'forever' AND instant_date IS NULL AND period_start IS NULL AND period_end IS NULL)",
        name="period_shape",
    ),
    sa.CheckConstraint(
        "(is_nil AND numeric_value IS NULL AND text_value IS NULL) OR "
        "(NOT is_nil AND num_nonnulls(numeric_value, text_value) = 1)",
        name="nil_value_shape",
    ),
    sa.CheckConstraint(
        "concept_qname ~ '^\\{[^{}]+\\}[^{}]+$'", name="canonical_qname"
    ),
)

quarterly_financial_summary = sa.Table(
    "quarterly_financial_summary",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("filing_version_id", sa.BigInteger(), nullable=False),
    sa.Column("source_fact_id", sa.BigInteger(), nullable=False),
    sa.Column("metric_code", sa.String(64), nullable=False),
    sa.Column("period_basis", sa.String(16), nullable=False),
    sa.Column("value", sa.Numeric(), nullable=False),
    sa.Column("unit_identity", sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(
        ["filing_version_id"], ["financial_filing_versions.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["source_fact_id", "filing_version_id"],
        ["financial_facts.id", "financial_facts.filing_version_id"],
        ondelete="RESTRICT",
    ),
    sa.UniqueConstraint(
        "filing_version_id",
        "metric_code",
        "period_basis",
        name="uq_quarterly_summary_metric_basis",
    ),
    sa.CheckConstraint(
        "period_basis IN ('quarter', 'ytd', 'annual', 'instant')",
        name="period_basis_value",
    ),
)

financial_filing_seals = sa.Table(
    "financial_filing_seals",
    metadata,
    sa.Column("filing_version_id", sa.BigInteger(), primary_key=True),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.ForeignKeyConstraint(
        ["filing_version_id"], ["financial_filing_versions.id"], ondelete="RESTRICT"
    ),
)
sa.Index(
    "uq_financial_filing_business_revision",
    financial_filing_versions.c.security_id,
    financial_filing_versions.c.source,
    financial_filing_versions.c.report_year,
    financial_filing_versions.c.report_quarter,
    financial_filing_versions.c.business_content_hash,
    unique=True,
    postgresql_where=financial_filing_versions.c.business_content_hash.is_not(None),
)

tdcc_snapshot_versions = sa.Table(
    "tdcc_snapshot_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("snapshot_date", sa.Date(), nullable=False),
    sa.Column("business_content_hash", sa.CHAR(64)),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
)

tdcc_snapshot_version_observations = sa.Table(
    "tdcc_snapshot_version_observations",
    metadata,
    sa.Column("snapshot_version_id", sa.BigInteger(), nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.PrimaryKeyConstraint(
        "snapshot_version_id", "raw_artifact_id", "ingest_run_id"
    ),
    sa.ForeignKeyConstraint(
        ["snapshot_version_id"],
        ["tdcc_snapshot_versions.id"],
        ondelete="RESTRICT",
    ),
    *lineage_constraints(),
)

tdcc_distribution = sa.Table(
    "tdcc_distribution",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("snapshot_version_id", sa.BigInteger(), nullable=False),
    sa.Column("bucket_code", sa.String(32), nullable=False),
    sa.Column("holder_count", sa.BigInteger(), nullable=False),
    sa.Column("shares", sa.Numeric(30, 0), nullable=False),
    sa.Column("ownership_percent", sa.Numeric(12, 8), nullable=False),
    sa.ForeignKeyConstraint(
        ["snapshot_version_id"], ["tdcc_snapshot_versions.id"], ondelete="RESTRICT"
    ),
    sa.UniqueConstraint(
        "snapshot_version_id", "bucket_code", name="uq_tdcc_distribution_bucket"
    ),
    sa.CheckConstraint("holder_count >= 0", name="holder_count_nonnegative"),
    sa.CheckConstraint("shares >= 0", name="shares_nonnegative"),
    sa.CheckConstraint(
        "ownership_percent BETWEEN 0 AND 100", name="ownership_percent_range"
    ),
)

tdcc_snapshot_seals = sa.Table(
    "tdcc_snapshot_seals",
    metadata,
    sa.Column("snapshot_version_id", sa.BigInteger(), primary_key=True),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.ForeignKeyConstraint(
        ["snapshot_version_id"], ["tdcc_snapshot_versions.id"], ondelete="RESTRICT"
    ),
)
sa.Index(
    "uq_tdcc_snapshot_business_revision",
    tdcc_snapshot_versions.c.security_id,
    tdcc_snapshot_versions.c.source,
    tdcc_snapshot_versions.c.snapshot_date,
    tdcc_snapshot_versions.c.business_content_hash,
    unique=True,
    postgresql_where=tdcc_snapshot_versions.c.business_content_hash.is_not(None),
)

institutional_investor_versions = sa.Table(
    "institutional_investor_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("trade_date", sa.Date(), nullable=False),
    sa.Column("foreign_buy", sa.Numeric(30, 0)),
    sa.Column("foreign_sell", sa.Numeric(30, 0)),
    sa.Column("foreign_net", sa.Numeric(30, 0)),
    sa.Column("foreign_dealer_buy", sa.Numeric(30, 0)),
    sa.Column("foreign_dealer_sell", sa.Numeric(30, 0)),
    sa.Column("foreign_dealer_net", sa.Numeric(30, 0)),
    sa.Column("trust_buy", sa.Numeric(30, 0)),
    sa.Column("trust_sell", sa.Numeric(30, 0)),
    sa.Column("trust_net", sa.Numeric(30, 0)),
    sa.Column("dealer_self_buy", sa.Numeric(30, 0)),
    sa.Column("dealer_self_sell", sa.Numeric(30, 0)),
    sa.Column("dealer_self_net", sa.Numeric(30, 0)),
    sa.Column("dealer_hedge_buy", sa.Numeric(30, 0)),
    sa.Column("dealer_hedge_sell", sa.Numeric(30, 0)),
    sa.Column("dealer_hedge_net", sa.Numeric(30, 0)),
    sa.Column("dealer_net", sa.Numeric(30, 0)),
    sa.Column("total_net", sa.Numeric(30, 0)),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "security_id", "source", "trade_date", "business_content_hash",
        name="uq_institutional_investor_business_revision",
    ),
)

foreign_holding_versions = sa.Table(
    "foreign_holding_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("trade_date", sa.Date(), nullable=False),
    sa.Column("issued_shares", sa.Numeric(30, 0)),
    sa.Column("investable_shares", sa.Numeric(30, 0)),
    sa.Column("held_shares", sa.Numeric(30, 0)),
    sa.Column("investable_ratio", sa.Numeric(12, 8)),
    sa.Column("held_ratio", sa.Numeric(12, 8)),
    sa.Column("foreign_legal_limit_ratio", sa.Numeric(12, 8)),
    sa.Column("mainland_legal_limit_ratio", sa.Numeric(12, 8)),
    sa.Column("change_reason", sa.Text()),
    sa.Column("source_last_update_date", sa.Date()),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "security_id", "source", "trade_date", "business_content_hash",
        name="uq_foreign_holding_business_revision",
    ),
)

institutional_market_summary_versions = sa.Table(
    "institutional_market_summary_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("market", sa.String(32), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("trade_date", sa.Date(), nullable=False),
    sa.Column("institution", sa.String(64), nullable=False),
    sa.Column("buy", sa.Numeric(30, 0)),
    sa.Column("sell", sa.Numeric(30, 0)),
    sa.Column("net", sa.Numeric(30, 0)),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "market", "source", "trade_date", "institution", "business_content_hash",
        name="uq_institutional_summary_business_revision",
    ),
)

margin_trading_versions = sa.Table(
    "margin_trading_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("trade_date", sa.Date(), nullable=False),
    sa.Column("margin_buy", sa.Numeric(30, 0)),
    sa.Column("margin_sell", sa.Numeric(30, 0)),
    sa.Column("margin_cash_repayment", sa.Numeric(30, 0)),
    sa.Column("margin_previous_balance", sa.Numeric(30, 0)),
    sa.Column("margin_balance", sa.Numeric(30, 0)),
    sa.Column("margin_next_limit", sa.Numeric(30, 0)),
    sa.Column("margin_utilization_ratio", sa.Numeric(12, 8)),
    sa.Column("short_buy", sa.Numeric(30, 0)),
    sa.Column("short_sell", sa.Numeric(30, 0)),
    sa.Column("short_stock_repayment", sa.Numeric(30, 0)),
    sa.Column("short_previous_balance", sa.Numeric(30, 0)),
    sa.Column("short_balance", sa.Numeric(30, 0)),
    sa.Column("short_next_limit", sa.Numeric(30, 0)),
    sa.Column("short_utilization_ratio", sa.Numeric(12, 8)),
    sa.Column("offset_balance", sa.Numeric(30, 0)),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "security_id", "source", "trade_date", "business_content_hash",
        name="uq_margin_trading_business_revision",
    ),
)

securities_lending_versions = sa.Table(
    "securities_lending_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("trade_date", sa.Date(), nullable=False),
    sa.Column("previous_balance", sa.Numeric(30, 0)),
    sa.Column("borrowed", sa.Numeric(30, 0)),
    sa.Column("returned", sa.Numeric(30, 0)),
    sa.Column("balance", sa.Numeric(30, 0)),
    sa.Column("next_limit", sa.Numeric(30, 0)),
    sa.Column("next_available_limit", sa.Numeric(30, 0)),
    sa.Column("adjustment", sa.Numeric(30, 0)),
    sa.Column("note", sa.Text()),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "security_id", "source", "trade_date", "business_content_hash",
        name="uq_securities_lending_business_revision",
    ),
)

market_index = sa.Table(
    "market_index",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("index_code", sa.String(64), nullable=False, unique=True),
    sa.Column("market", sa.String(32), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("created_at", aware_timestamp, nullable=False,
              server_default=sa.text("statement_timestamp()")),
)

market_index_versions = sa.Table(
    "market_index_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("market_index_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("trade_date", sa.Date(), nullable=False),
    sa.Column("open_value", sa.Numeric(24, 8)),
    sa.Column("high_value", sa.Numeric(24, 8)),
    sa.Column("low_value", sa.Numeric(24, 8)),
    sa.Column("close_value", sa.Numeric(24, 8), nullable=False),
    sa.Column("change_points", sa.Numeric(24, 8)),
    sa.Column("change_percent", sa.Numeric(16, 8)),
    sa.Column("trade_value", sa.Numeric(30, 4)),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["market_index_id"], ["market_index.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "market_index_id", "source", "trade_date", "business_content_hash",
        name="uq_market_index_business_revision",
    ),
)

corporate_action_versions = sa.Table(
    "corporate_action_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("action_type", sa.String(32), nullable=False),
    sa.Column("announcement_date", sa.Date()),
    sa.Column("ex_date", sa.Date(), nullable=False),
    sa.Column("record_date", sa.Date()),
    sa.Column("payment_date", sa.Date()),
    sa.Column("cash_dividend_per_share", sa.Numeric(24, 8)),
    sa.Column("stock_dividend_ratio", sa.Numeric(24, 8)),
    sa.Column("rights_ratio", sa.Numeric(24, 8)),
    sa.Column("subscription_price", sa.Numeric(20, 6)),
    sa.Column("close_before", sa.Numeric(20, 6)),
    sa.Column("reference_price", sa.Numeric(20, 6)),
    sa.Column("rights_dividend_value", sa.Numeric(20, 6)),
    sa.Column("terms", jsonb_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "security_id", "source", "action_type", "ex_date", "business_content_hash",
        name="uq_corporate_action_business_revision",
    ),
    sa.CheckConstraint(
        "action_type IN ('cash_dividend', 'stock_dividend', 'rights', "
        "'ex_dividend', 'ex_right', 'capital_reduction', 'other')",
        name="action_type_value",
    ),
)

official_valuation_versions = sa.Table(
    "official_valuation_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("trade_date", sa.Date(), nullable=False),
    sa.Column("pe_ratio", sa.Numeric(24, 8)),
    sa.Column("pb_ratio", sa.Numeric(24, 8)),
    sa.Column("dividend_yield", sa.Numeric(16, 8)),
    sa.Column("dividend_year", sa.SmallInteger()),
    sa.Column("dividend_per_share", sa.Numeric(24, 8)),
    sa.Column("report_period", sa.String(16)),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "security_id", "source", "trade_date", "business_content_hash",
        name="uq_official_valuation_business_revision",
    ),
)

security_tag_versions = sa.Table(
    "security_tag_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("tag", sa.Text(), nullable=False),
    sa.Column("effective_from", sa.Date(), nullable=False),
    sa.Column("effective_to", sa.Date()),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "security_id", "source", "tag", "effective_from", "business_content_hash",
        name="uq_security_tag_business_revision",
    ),
    sa.CheckConstraint(
        "effective_to IS NULL OR effective_to >= effective_from", name="effective_range"
    ),
)

xbrl_concept_catalog_versions = sa.Table(
    "xbrl_concept_catalog_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("concept_qname", sa.Text(), nullable=False),
    sa.Column("statement_type", sa.String(32), nullable=False),
    sa.Column("account_name_zh", sa.Text()),
    sa.Column("account_name_en", sa.Text()),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "source", "concept_qname", "business_content_hash",
        name="uq_xbrl_concept_business_revision",
    ),
)

derived_dataset_definitions = sa.Table(
    "derived_dataset_definitions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("dataset_code", sa.String(64), nullable=False),
    sa.Column("derivation_version", sa.String(64), nullable=False),
    sa.Column("storage_strategy", sa.String(16), nullable=False),
    sa.Column("formula_specification", sa.Text(), nullable=False),
    sa.Column("implementation_version", sa.String(128), nullable=False),
    sa.Column("input_dataset_codes", jsonb_type, nullable=False),
    sa.Column("calendar_timezone", sa.String(64)),
    sa.Column("calendar_convention", sa.Text()),
    sa.Column("price_adjustment_convention", sa.Text()),
    sa.Column("definition_hash", sa.CHAR(64), nullable=False),
    sa.Column("registered_at", aware_timestamp, nullable=False),
    sa.ForeignKeyConstraint(
        ["dataset_code"], ["dataset_catalog.dataset_code"], ondelete="RESTRICT"
    ),
    sa.UniqueConstraint(
        "dataset_code", "derivation_version", name="uq_derived_definition_version"
    ),
    sa.CheckConstraint(
        "storage_strategy IN ('materialized', 'virtual')", name="storage_strategy_value"
    ),
    sa.CheckConstraint(
        "jsonb_typeof(input_dataset_codes) = 'array'", name="input_datasets_array"
    ),
)

derived_computation_runs = sa.Table(
    "derived_computation_runs",
    metadata,
    sa.Column("id", uuid_type, primary_key=True,
              server_default=sa.text("gen_random_uuid()")),
    sa.Column("definition_id", sa.BigInteger(), nullable=False),
    sa.Column("status", sa.String(24), nullable=False),
    sa.Column("implementation_version", sa.String(128), nullable=False),
    sa.Column("started_at", aware_timestamp, nullable=False),
    sa.Column("completed_at", aware_timestamp),
    sa.Column("run_metadata", jsonb_type, nullable=False,
              server_default=sa.text("'{}'::jsonb")),
    sa.ForeignKeyConstraint(
        ["definition_id"], ["derived_dataset_definitions.id"], ondelete="RESTRICT"
    ),
    sa.CheckConstraint(
        "status IN ('running', 'succeeded', 'failed')", name="status_value"
    ),
    sa.CheckConstraint(
        "completed_at IS NULL OR completed_at >= started_at", name="completed_after_started"
    ),
)

derived_metric_versions = sa.Table(
    "derived_metric_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("definition_id", sa.BigInteger(), nullable=False),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("observation_date", sa.Date(), nullable=False),
    sa.Column("metric_code", sa.String(128), nullable=False),
    sa.Column("pit_mode", sa.String(16), nullable=False),
    sa.Column("information_as_of", aware_timestamp),
    sa.Column("knowledge_as_of", aware_timestamp),
    sa.Column("system_as_of", aware_timestamp),
    sa.Column("input_dataset_identity", jsonb_type, nullable=False),
    sa.Column("input_fingerprint", sa.CHAR(64), nullable=False),
    sa.Column("computation_run_id", uuid_type, nullable=False),
    sa.Column("numeric_value", sa.Numeric()),
    sa.Column("text_value", sa.Text()),
    sa.Column("json_value", jsonb_type),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("computed_at", aware_timestamp, nullable=False),
    sa.ForeignKeyConstraint(
        ["definition_id"], ["derived_dataset_definitions.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(
        ["computation_run_id"], ["derived_computation_runs.id"], ondelete="RESTRICT"
    ),
    sa.CheckConstraint(
        "(pit_mode = 'market' AND information_as_of IS NOT NULL AND "
        "knowledge_as_of IS NOT NULL AND system_as_of IS NULL) OR "
        "(pit_mode = 'system' AND information_as_of IS NULL AND "
        "knowledge_as_of IS NULL AND system_as_of IS NOT NULL)",
        name="pit_context_shape",
    ),
    sa.CheckConstraint(
        "num_nonnulls(numeric_value, text_value, json_value) = 1", name="one_value"
    ),
    sa.CheckConstraint(
        "input_fingerprint ~ '^[0-9a-f]{64}$'", name="input_fingerprint_lower_hex"
    ),
)
sa.Index(
    "uq_derived_metric_semantic_identity",
    derived_metric_versions.c.definition_id,
    derived_metric_versions.c.security_id,
    derived_metric_versions.c.observation_date,
    derived_metric_versions.c.metric_code,
    derived_metric_versions.c.pit_mode,
    derived_metric_versions.c.information_as_of,
    derived_metric_versions.c.knowledge_as_of,
    derived_metric_versions.c.system_as_of,
    derived_metric_versions.c.input_fingerprint,
    unique=True,
    postgresql_nulls_not_distinct=True,
)

publication_evidence = sa.Table(
    "publication_evidence",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("dataset_code", sa.String(64), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("evidence_kind", sa.String(24), nullable=False),
    sa.Column("published_at", aware_timestamp),
    sa.Column("recorded_at", aware_timestamp, nullable=False),
    sa.Column("evidence_source", sa.Text(), nullable=False),
    sa.Column("evidence_type", sa.String(64), nullable=False),
    sa.Column("quality_rank", sa.SmallInteger(), nullable=False),
    sa.Column("supersedes_evidence_id", sa.BigInteger()),
    sa.Column("security_metadata_version_id", sa.BigInteger()),
    sa.Column("daily_price_version_id", sa.BigInteger()),
    sa.Column("monthly_revenue_version_id", sa.BigInteger()),
    sa.Column("financial_filing_version_id", sa.BigInteger()),
    sa.Column("tdcc_snapshot_version_id", sa.BigInteger()),
    sa.Column("institutional_investor_version_id", sa.BigInteger()),
    sa.Column("foreign_holding_version_id", sa.BigInteger()),
    sa.Column("institutional_market_summary_version_id", sa.BigInteger()),
    sa.Column("margin_trading_version_id", sa.BigInteger()),
    sa.Column("securities_lending_version_id", sa.BigInteger()),
    sa.Column("market_index_version_id", sa.BigInteger()),
    sa.Column("corporate_action_version_id", sa.BigInteger()),
    sa.Column("official_valuation_version_id", sa.BigInteger()),
    sa.Column("security_tag_version_id", sa.BigInteger()),
    sa.Column("xbrl_concept_catalog_version_id", sa.BigInteger()),
    sa.Column("publication_evidence_hash", sa.CHAR(64), nullable=False, unique=True),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(
        ["dataset_code", "source"],
        ["dataset_sources.dataset_code", "dataset_sources.source"],
        ondelete="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["supersedes_evidence_id"], ["publication_evidence.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["security_metadata_version_id"], ["security_metadata_versions.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["daily_price_version_id"], ["daily_price_versions.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["monthly_revenue_version_id"], ["monthly_revenue_versions.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["financial_filing_version_id"], ["financial_filing_versions.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["tdcc_snapshot_version_id"], ["tdcc_snapshot_versions.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["institutional_investor_version_id"],
        ["institutional_investor_versions.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["foreign_holding_version_id"], ["foreign_holding_versions.id"],
        ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["institutional_market_summary_version_id"],
        ["institutional_market_summary_versions.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["margin_trading_version_id"], ["margin_trading_versions.id"],
        ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["securities_lending_version_id"], ["securities_lending_versions.id"],
        ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["market_index_version_id"], ["market_index_versions.id"],
        ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["corporate_action_version_id"], ["corporate_action_versions.id"],
        ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["official_valuation_version_id"], ["official_valuation_versions.id"],
        ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["security_tag_version_id"], ["security_tag_versions.id"],
        ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["xbrl_concept_catalog_version_id"],
        ["xbrl_concept_catalog_versions.id"], ondelete="RESTRICT"
    ),
    *lineage_constraints(),
    sa.CheckConstraint(
        "evidence_kind IN ('assertion', 'correction', 'retraction', 'unknown')",
        name="kind_value",
    ),
    sa.CheckConstraint("quality_rank BETWEEN 0 AND 100", name="quality_rank_range"),
    sa.CheckConstraint(
        "(evidence_kind IN ('assertion', 'correction') AND published_at IS NOT NULL) OR "
        "(evidence_kind IN ('retraction', 'unknown') AND published_at IS NULL)",
        name="publication_shape",
    ),
    sa.CheckConstraint(
        "num_nonnulls(security_metadata_version_id, daily_price_version_id, "
        "monthly_revenue_version_id, financial_filing_version_id, "
        "tdcc_snapshot_version_id, institutional_investor_version_id, "
        "foreign_holding_version_id, institutional_market_summary_version_id, "
        "margin_trading_version_id, securities_lending_version_id, "
        "market_index_version_id, corporate_action_version_id, "
        "official_valuation_version_id, security_tag_version_id, "
        "xbrl_concept_catalog_version_id) = 1",
        name="exactly_one_target",
    ),
)

publication_evidence_observations = sa.Table(
    "publication_evidence_observations",
    metadata,
    sa.Column("publication_evidence_id", sa.BigInteger(), nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.PrimaryKeyConstraint(
        "publication_evidence_id", "raw_artifact_id", "ingest_run_id"
    ),
    sa.ForeignKeyConstraint(
        ["publication_evidence_id"],
        ["publication_evidence.id"],
        ondelete="RESTRICT",
    ),
    *lineage_constraints(),
)

# Resolver-oriented prefixes keep source and PIT cutoffs adjacent to each
# logical identity. Unique revision constraints remain the business-identity
# guard; these indexes serve later market/system PIT selection.
for _name, _table, _columns in (
    ("ix_security_metadata_pit", security_metadata_versions,
     ("security_id", "source", "effective_from", "ingested_at")),
    ("ix_daily_price_pit", daily_price_versions,
     ("security_id", "source", "trade_date", "ingested_at")),
    ("ix_monthly_revenue_pit", monthly_revenue_versions,
     ("security_id", "source", "revenue_year", "revenue_month", "ingested_at")),
    ("ix_institutional_investor_pit", institutional_investor_versions,
     ("security_id", "source", "trade_date", "ingested_at")),
    ("ix_foreign_holding_pit", foreign_holding_versions,
     ("security_id", "source", "trade_date", "ingested_at")),
    ("ix_institutional_summary_pit", institutional_market_summary_versions,
     ("market", "source", "trade_date", "institution", "ingested_at")),
    ("ix_margin_trading_pit", margin_trading_versions,
     ("security_id", "source", "trade_date", "ingested_at")),
    ("ix_securities_lending_pit", securities_lending_versions,
     ("security_id", "source", "trade_date", "ingested_at")),
    ("ix_market_index_pit", market_index_versions,
     ("market_index_id", "source", "trade_date", "ingested_at")),
    ("ix_corporate_action_pit", corporate_action_versions,
     ("security_id", "source", "ex_date", "ingested_at")),
    ("ix_official_valuation_pit", official_valuation_versions,
     ("security_id", "source", "trade_date", "ingested_at")),
    ("ix_security_tag_pit", security_tag_versions,
     ("security_id", "source", "effective_from", "ingested_at")),
    ("ix_xbrl_concept_catalog_pit", xbrl_concept_catalog_versions,
     ("source", "concept_qname", "ingested_at")),
):
    sa.Index(_name, *(_table.c[column] for column in _columns))

sa.Index(
    "ix_publication_evidence_resolution",
    publication_evidence.c.dataset_code,
    publication_evidence.c.source,
    publication_evidence.c.published_at,
    publication_evidence.c.recorded_at,
)

__all__ = ["metadata"]
