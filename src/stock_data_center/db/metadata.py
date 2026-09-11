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
    sa.Column("market", sa.String(32), nullable=False),
    sa.Column(
        "created_at",
        aware_timestamp,
        nullable=False,
        server_default=sa.text("statement_timestamp()"),
    ),
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
        "numeric_value IS NOT NULL OR text_value IS NOT NULL", name="value_present"
    ),
)

quarterly_financial_summary = sa.Table(
    "quarterly_financial_summary",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("filing_version_id", sa.BigInteger(), nullable=False),
    sa.Column("metric_code", sa.String(64), nullable=False),
    sa.Column("value", sa.Numeric(), nullable=False),
    sa.Column("unit_identity", sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(
        ["filing_version_id"], ["financial_filing_versions.id"], ondelete="RESTRICT"
    ),
    sa.UniqueConstraint(
        "filing_version_id", "metric_code", name="uq_quarterly_summary_metric"
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
        "tdcc_snapshot_version_id) = 1",
        name="exactly_one_target",
    ),
)

__all__ = ["metadata"]
