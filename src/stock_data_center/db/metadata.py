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

release_rules = sa.Table(
    "release_rules",
    metadata,
    sa.Column("rule_id", sa.String(64), primary_key=True),
    sa.Column("version", sa.SmallInteger(), primary_key=True),
    sa.Column("rule_kind", sa.String(32), nullable=False),
    sa.Column("parameters", jsonb_type, nullable=False),
    sa.Column("timezone", sa.String(64), nullable=False),
    # A statutory deadline moves off a closure; a scheduled instant does not.
    # ADR-0020 §3, as corrected in Step 15-b.
    sa.Column("business_day_shift", sa.Boolean(), nullable=False),
    # The published schedule or statute the rule derives from. A rule without a
    # cited authority is an invented instant, which ROADMAP forbids.
    sa.Column("authority", sa.Text(), nullable=False),
    sa.CheckConstraint(
        "rule_kind IN ('day_of_next_month', 'quarter_deadline', "
        "'next_calendar_day_time', 'weekday_after_time')",
        name="rule_kind_value",
    ),
    sa.CheckConstraint("version > 0", name="version_positive"),
    sa.CheckConstraint(
        "rule_kind <> 'day_of_next_month' OR "
        "((parameters->>'day')::int BETWEEN 1 AND 28)",
        name="day_of_month_representable",
    ),
    sa.CheckConstraint("btrim(authority) <> ''", name="authority_nonempty"),
)

dataset_release_rules = sa.Table(
    "dataset_release_rules",
    metadata,
    sa.Column("dataset_code", sa.String(64), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("rule_id", sa.String(64), nullable=False),
    sa.Column("version", sa.SmallInteger(), nullable=False),
    sa.Column("note", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.PrimaryKeyConstraint("dataset_code", "source"),
    sa.ForeignKeyConstraint(
        ["dataset_code", "source"],
        ["dataset_sources.dataset_code", "dataset_sources.source"],
        ondelete="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["rule_id", "version"],
        ["release_rules.rule_id", "release_rules.version"],
        ondelete="RESTRICT",
    ),
)

evidence_types = sa.Table(
    "evidence_types",
    metadata,
    sa.Column("evidence_type", sa.String(64), primary_key=True),
    # ADR-0020 §1. The ordering is the decision; the numbers express it.
    # Storage enforces that affirmative evidence of a registered type carries
    # exactly this rank, so precedence cannot be forged by a caller.
    sa.Column("quality_rank", sa.SmallInteger(), nullable=False),
    # False only for `official`, which predates ADR-0020 and whose rank varies
    # across existing rows. The four types ADR-0020 introduces are pinned.
    sa.Column("rank_is_enforced", sa.Boolean(), nullable=False),
    sa.Column("description", sa.Text(), nullable=False),
    sa.CheckConstraint("quality_rank BETWEEN 0 AND 100", name="quality_rank_range"),
    sa.CheckConstraint("btrim(description) <> ''", name="description_nonempty"),
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
    # ADR-0020 §5. Declared when the fetch is requested, never inferred
    # afterwards: only a first capture may later claim capture_bound evidence.
    sa.Column(
        "purpose",
        sa.String(32),
        nullable=False,
        server_default=sa.text("'unspecified'"),
    ),
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
    sa.CheckConstraint(
        "purpose IN ('first_capture', 'gap_fill', 'correction_check', "
        "'unspecified')",
        name="purpose_value",
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
    # ROADMAP §14 names two origins. `legacy_archive` is allowed only where an
    # archive holds what no official re-fetch can provide.
    sa.Column(
        "artifact_origin",
        sa.String(32),
        nullable=False,
        server_default=sa.text("'official_fetch'"),
    ),
    sa.PrimaryKeyConstraint("raw_artifact_id", "ingest_run_id"),
    sa.ForeignKeyConstraint(
        ["raw_artifact_id"], ["raw_artifacts.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(["ingest_run_id"], ["ingest_runs.id"], ondelete="RESTRICT"),
    sa.CheckConstraint(
        "artifact_origin IN ('official_fetch', 'legacy_archive')",
        name="artifact_origin_value",
    ),
)


import_manifests = sa.Table(
    "import_manifests",
    metadata,
    sa.Column("import_id", uuid_type, primary_key=True),
    sa.Column("dataset_code", sa.String(64), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("adapter_version", sa.String(64), nullable=False),
    sa.Column("git_commit", sa.String(64), nullable=False),
    sa.Column("source_scope", jsonb_type, nullable=False),
    sa.Column("configuration_fingerprint", sa.CHAR(64), nullable=False),
    sa.Column("status", sa.String(24), nullable=False),
    sa.Column(
        "started_at",
        aware_timestamp,
        nullable=False,
        server_default=sa.text("statement_timestamp()"),
    ),
    sa.Column("completed_at", aware_timestamp),
    sa.Column(
        "result_counts",
        jsonb_type,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    ),
    sa.Column(
        "reconciliation",
        jsonb_type,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    ),
    sa.Column(
        "warnings",
        jsonb_type,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ),
    sa.ForeignKeyConstraint(
        ["dataset_code", "source"],
        ["dataset_sources.dataset_code", "dataset_sources.source"],
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "configuration_fingerprint ~ '^[0-9a-f]{64}$'",
        name="configuration_fingerprint_lower_hex",
    ),
    sa.CheckConstraint(
        "status IN ('running', 'succeeded', 'failed')",
        name="status_value",
    ),
    sa.CheckConstraint(
        "completed_at IS NULL OR completed_at >= started_at",
        name="completed_after_started",
    ),
)


import_checkpoints = sa.Table(
    "import_checkpoints",
    metadata,
    sa.Column("import_id", uuid_type, nullable=False),
    sa.Column("resource_key", sa.Text(), nullable=False),
    sa.Column("status", sa.String(24), nullable=False),
    sa.Column("attempt_count", sa.Integer(), nullable=False),
    sa.Column("last_ingest_run_id", uuid_type, nullable=False),
    sa.Column("last_raw_artifact_id", uuid_type, nullable=False),
    sa.Column(
        "updated_at",
        aware_timestamp,
        nullable=False,
        server_default=sa.text("statement_timestamp()"),
    ),
    sa.Column("error_code", sa.String(64)),
    sa.Column("error_detail", sa.Text()),
    sa.PrimaryKeyConstraint("import_id", "resource_key"),
    sa.ForeignKeyConstraint(
        ["import_id"], ["import_manifests.import_id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["last_raw_artifact_id", "last_ingest_run_id"],
        [
            "raw_artifact_observations.raw_artifact_id",
            "raw_artifact_observations.ingest_run_id",
        ],
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "status IN ('captured', 'succeeded', 'quarantined')",
        name="status_value",
    ),
    sa.CheckConstraint("attempt_count > 0", name="attempt_count_positive"),
    sa.CheckConstraint("resource_key <> ''", name="resource_key_nonempty"),
)


import_quarantine = sa.Table(
    "import_quarantine",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("import_id", uuid_type, nullable=False),
    sa.Column("resource_key", sa.Text(), nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("reason_code", sa.String(64), nullable=False),
    sa.Column("reason_detail", sa.Text(), nullable=False),
    sa.Column(
        "quarantined_at",
        aware_timestamp,
        nullable=False,
        server_default=sa.text("statement_timestamp()"),
    ),
    sa.ForeignKeyConstraint(
        ["import_id"], ["import_manifests.import_id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["raw_artifact_id", "ingest_run_id"],
        [
            "raw_artifact_observations.raw_artifact_id",
            "raw_artifact_observations.ingest_run_id",
        ],
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint("resource_key <> ''", name="resource_key_nonempty"),
    sa.CheckConstraint("reason_code <> ''", name="reason_code_nonempty"),
    sa.CheckConstraint("reason_detail <> ''", name="reason_detail_nonempty"),
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
    sa.Column("predecessor_version_id", sa.BigInteger()),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(
        ["predecessor_version_id"],
        ["security_metadata_versions.id"],
        name="fk_security_metadata_predecessor",
        ondelete="RESTRICT",
    ),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "security_id",
        "source",
        "effective_from",
        "business_content_hash",
        "predecessor_version_id",
        name="uq_security_metadata_business_revision",
        postgresql_nulls_not_distinct=True,
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

security_transfer_events = sa.Table(
    "security_transfer_events",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("entry_version_id", sa.BigInteger(), nullable=False),
    sa.Column("from_source", sa.String(64), nullable=False),
    sa.Column("from_market", sa.String(32), nullable=False),
    sa.Column("source_term", sa.Text(), nullable=False),
    sa.Column("recorded_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(
        ["entry_version_id"],
        ["security_metadata_versions.id"],
        ondelete="RESTRICT",
    ),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "entry_version_id",
        "from_source",
        "from_market",
        name="uq_security_transfer_event",
    ),
    sa.CheckConstraint("from_source <> ''", name="from_source_nonempty"),
    sa.CheckConstraint("from_market <> ''", name="from_market_nonempty"),
    sa.CheckConstraint("source_term <> ''", name="source_term_nonempty"),
)

security_transfer_event_observations = sa.Table(
    "security_transfer_event_observations",
    metadata,
    sa.Column("security_transfer_event_id", sa.BigInteger(), nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.PrimaryKeyConstraint(
        "security_transfer_event_id", "raw_artifact_id", "ingest_run_id"
    ),
    sa.ForeignKeyConstraint(
        ["security_transfer_event_id"],
        ["security_transfer_events.id"],
        ondelete="RESTRICT",
    ),
    *lineage_constraints(),
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
    sa.CheckConstraint(
        "(open_price IS NULL OR open_price >= 0) AND "
        "(high_price IS NULL OR high_price >= 0) AND "
        "(low_price IS NULL OR low_price >= 0) AND "
        "(close_price IS NULL OR close_price >= 0)",
        name="prices_nonnegative",
    ),
    sa.CheckConstraint(
        "open_price IS NULL OR low_price IS NULL OR open_price >= low_price",
        name="open_not_below_low",
    ),
    sa.CheckConstraint(
        "open_price IS NULL OR high_price IS NULL OR open_price <= high_price",
        name="open_not_above_high",
    ),
    sa.CheckConstraint(
        "close_price IS NULL OR low_price IS NULL OR close_price >= low_price",
        name="close_not_below_low",
    ),
    sa.CheckConstraint(
        "close_price IS NULL OR high_price IS NULL OR close_price <= high_price",
        name="close_not_above_high",
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
    # The month as a date, generated rather than written, so the coverage
    # report (Step 22-b) has one period column and nothing can disagree with
    # the year and month it came from.
    sa.Column(
        "revenue_period",
        sa.Date(),
        sa.Computed("make_date(revenue_year, revenue_month, 1)", persisted=True),
    ),
    sa.Column("revenue", sa.Numeric(24, 4), nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    # Published comparatives, as published (Step 22-a, audit §7.3).
    sa.Column("revenue_last_month", sa.Numeric(24, 4)),
    sa.Column("revenue_last_year_month", sa.Numeric(24, 4)),
    sa.Column("cumulative_revenue", sa.Numeric(24, 4)),
    sa.Column("cumulative_revenue_last_year", sa.Numeric(24, 4)),
    sa.Column("mom_pct", sa.Numeric(24, 4)),
    sa.Column("yoy_pct", sa.Numeric(24, 4)),
    sa.Column("cumulative_yoy_pct", sa.Numeric(24, 4)),
    sa.Column("note", sa.Text()),
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
    sa.CheckConstraint("note IS NULL OR note <> ''", name="note_nonempty"),
)
sa.Index(
    "ix_monthly_revenue_versions_period",
    monthly_revenue_versions.c.revenue_period,
    monthly_revenue_versions.c.source,
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
    # 合併 or 個體. MOPS asks for one of them by `REPORT_ID` and answers
    # `檔案不存在!` for the other, so a filer files one per quarter, never both.
    sa.Column("report_category", sa.String(16)),
    sa.Column("business_content_hash", sa.CHAR(64)),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint("source", "filing_key", name="uq_financial_filing_source_key"),
    sa.CheckConstraint("report_quarter BETWEEN 1 AND 4", name="quarter_range"),
    sa.CheckConstraint("period_end >= period_start", name="period_range"),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
    sa.CheckConstraint(
        "report_category IS NULL OR report_category IN ('consolidated', 'individual')",
        name="report_category_value",
    ),
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
    # Which statement printed this row, and the 會計科目代碼 it printed in the
    # first cell. One number can be a row of two statements — the balance
    # sheet's 1100 and the cash-flow statement's E00210 are the same
    # `ifrs-full:CashAndCashEquivalents` at the same instant in the same unit,
    # four such pairs in every one of the archive's 42,750 in-scope documents —
    # so the statement is part of what tells two rows apart.
    sa.Column("statement", sa.String(24)),
    sa.Column("account_code", sa.String(16)),
    sa.ForeignKeyConstraint(
        ["filing_version_id"], ["financial_filing_versions.id"], ondelete="RESTRICT"
    ),
    sa.UniqueConstraint(
        "filing_version_id",
        "concept_qname",
        "context_hash",
        "unit_identity",
        "statement",
        name="uq_financial_fact_identity",
        postgresql_nulls_not_distinct=True,
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
    sa.CheckConstraint(
        "statement IS NULL OR statement IN "
        "('balance_sheet', 'income_statement', 'cash_flow')",
        name="statement_value",
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

tdcc_distribution_schemas = sa.Table(
    "tdcc_distribution_schemas",
    metadata,
    sa.Column("distribution_schema", sa.String(64), primary_key=True),
    sa.Column("description", sa.Text(), nullable=False),
)

tdcc_distribution_schema_buckets = sa.Table(
    "tdcc_distribution_schema_buckets",
    metadata,
    sa.Column("distribution_schema", sa.String(64), primary_key=True),
    sa.Column("bucket_code", sa.String(32), primary_key=True),
    sa.Column("bucket_role", sa.String(16), nullable=False),
    sa.Column("description", sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(
        ["distribution_schema"],
        ["tdcc_distribution_schemas.distribution_schema"],
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "bucket_role IN ('holding', 'adjustment', 'total')", name="bucket_role_valid"
    ),
)

dataset_expected_coverage = sa.Table(
    "dataset_expected_coverage",
    metadata,
    sa.Column("dataset_code", sa.String(64), nullable=False),
    sa.Column("market", sa.String(32), nullable=False),
    # Which source history the expectation is about. Observed rows of another
    # source never count as this one's coverage.
    sa.Column("source", sa.String(64), nullable=False),
    # Whose trading calendar decides the expected periods. TPEx datasets name
    # TWSE here because no official TPEx calendar exists; `note` records the
    # measurement behind that (ADR-0021).
    sa.Column("calendar_market", sa.String(32), nullable=False),
    # How often the source publishes a period the dataset must hold.
    sa.Column("cadence", sa.String(32), nullable=False),
    # The version-table column holding that period's logical date.
    sa.Column("period_column", sa.String(64), nullable=False),
    sa.Column("window_start", sa.Date(), nullable=False),
    # NULL means the window is still open.
    sa.Column("window_end", sa.Date()),
    sa.Column("note", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.PrimaryKeyConstraint("dataset_code", "market"),
    sa.ForeignKeyConstraint(
        ["dataset_code", "source"],
        ["dataset_sources.dataset_code", "dataset_sources.source"],
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        # `trading_week` is TDCC's: the data date is the publisher's own
        # business day, so only the week can be expected (Step 24-b).
        "cadence IN ('trading_day', 'calendar_month', 'trading_week')",
        name="cadence_value",
    ),
    sa.CheckConstraint(
        "window_end IS NULL OR window_end >= window_start", name="window_order"
    ),
)

trading_calendar_versions = sa.Table(
    "trading_calendar_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("market", sa.String(32), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("calendar_month", sa.Date(), nullable=False),
    # The business content of one month: the days the market actually opened.
    # A closure is an absence from this list, so a corrected closure is a new
    # version rather than an update.
    sa.Column("trading_days", postgresql.ARRAY(sa.Date()), nullable=False),
    # The last date this version can speak for. An in-month fetch publishes a
    # partial list, and days after this bound are unknown, never closed.
    sa.Column("coverage_through", sa.Date(), nullable=False),
    sa.Column("business_content_hash", sa.CHAR(64)),
    sa.Column("ingested_at", aware_timestamp),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "market",
        "source",
        "calendar_month",
        "business_content_hash",
        name="uq_trading_calendar_business_revision",
    ),
    sa.CheckConstraint(
        "calendar_month = date_trunc('month', calendar_month)::date",
        name="calendar_month_is_first_day",
    ),
    sa.CheckConstraint("cardinality(trading_days) > 0", name="month_has_open_day"),
    # A CHECK cannot hold a subquery, so the predicate lives in an immutable
    # function the migration creates.
    sa.CheckConstraint(
        "stockdc_dates_sorted_distinct(trading_days)",
        name="trading_days_sorted_distinct",
    ),
    sa.CheckConstraint(
        "trading_days[1] >= calendar_month "
        "AND trading_days[cardinality(trading_days)] "
        "    < (calendar_month + INTERVAL '1 month')::date",
        name="trading_days_inside_month",
    ),
    sa.CheckConstraint(
        "coverage_through >= trading_days[cardinality(trading_days)] "
        "AND coverage_through >= calendar_month "
        "AND coverage_through < (calendar_month + INTERVAL '1 month')::date",
        name="coverage_through_inside_month",
    ),
)

trading_calendar_version_observations = sa.Table(
    "trading_calendar_version_observations",
    metadata,
    sa.Column("calendar_version_id", sa.BigInteger(), nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.PrimaryKeyConstraint(
        "calendar_version_id", "raw_artifact_id", "ingest_run_id"
    ),
    sa.ForeignKeyConstraint(
        ["calendar_version_id"],
        ["trading_calendar_versions.id"],
        ondelete="RESTRICT",
    ),
    *lineage_constraints(),
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
    sa.Column("distribution_schema", sa.String(64), nullable=False),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(
        ["distribution_schema"],
        ["tdcc_distribution_schemas.distribution_schema"],
        ondelete="RESTRICT",
    ),
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
    # NULL only for adjustment buckets; see docs/tdcc.md.
    sa.Column("holder_count", sa.BigInteger()),
    sa.Column("shares", sa.Numeric(30, 0), nullable=False),
    sa.Column("ownership_percent", sa.Numeric(12, 8), nullable=False),
    sa.ForeignKeyConstraint(
        ["snapshot_version_id"], ["tdcc_snapshot_versions.id"], ondelete="RESTRICT"
    ),
    sa.UniqueConstraint(
        "snapshot_version_id", "bucket_code", name="uq_tdcc_distribution_bucket"
    ),
    sa.CheckConstraint("holder_count >= 0", name="holder_count_nonnegative"),
    # Signed values are allowed for adjustment buckets, and the published
    # 合計 exceeds 100 for some securities (Step 24-a, audit §4.9), so only the
    # lower bound is a table rule. Per-role rules, the holding levels' ceiling
    # among them, are enforced by the validate_tdcc_distribution_row trigger.
    sa.CheckConstraint(
        "ownership_percent >= -100", name="ownership_percent_range"
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
    sa.CheckConstraint(
        "num_nonnulls(foreign_buy, foreign_sell, foreign_net, foreign_dealer_buy, "
        "foreign_dealer_sell, foreign_dealer_net, trust_buy, trust_sell, trust_net, "
        "dealer_self_buy, dealer_self_sell, dealer_self_net, dealer_hedge_buy, "
        "dealer_hedge_sell, dealer_hedge_net, dealer_net, total_net) > 0",
        name="institutional_value_present",
    ),
    sa.CheckConstraint(
        "(foreign_buy IS NULL OR foreign_buy >= 0) AND "
        "(foreign_sell IS NULL OR foreign_sell >= 0) AND "
        "(foreign_dealer_buy IS NULL OR foreign_dealer_buy >= 0) AND "
        "(foreign_dealer_sell IS NULL OR foreign_dealer_sell >= 0) AND "
        "(trust_buy IS NULL OR trust_buy >= 0) AND "
        "(trust_sell IS NULL OR trust_sell >= 0) AND "
        "(dealer_self_buy IS NULL OR dealer_self_buy >= 0) AND "
        "(dealer_self_sell IS NULL OR dealer_self_sell >= 0) AND "
        "(dealer_hedge_buy IS NULL OR dealer_hedge_buy >= 0) AND "
        "(dealer_hedge_sell IS NULL OR dealer_hedge_sell >= 0)",
        name="institutional_gross_nonnegative",
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
    sa.CheckConstraint(
        "num_nonnulls(issued_shares, investable_shares, held_shares, "
        "investable_ratio, held_ratio, foreign_legal_limit_ratio, "
        "mainland_legal_limit_ratio) > 0",
        name="foreign_holding_value_present",
    ),
    sa.CheckConstraint(
        "(issued_shares IS NULL OR issued_shares >= 0) AND "
        "(investable_shares IS NULL OR investable_shares >= 0) AND "
        "(held_shares IS NULL OR held_shares >= 0)",
        name="foreign_holding_shares_nonnegative",
    ),
    sa.CheckConstraint(
        "(investable_ratio IS NULL OR investable_ratio BETWEEN 0 AND 100) AND "
        "(held_ratio IS NULL OR held_ratio BETWEEN 0 AND 100) AND "
        "(foreign_legal_limit_ratio IS NULL OR foreign_legal_limit_ratio BETWEEN 0 AND 100) AND "
        "(mainland_legal_limit_ratio IS NULL OR mainland_legal_limit_ratio BETWEEN 0 AND 100)",
        name="foreign_holding_ratios_percent",
    ),
    sa.CheckConstraint(
        "change_reason IS NULL OR change_reason <> ''",
        name="foreign_holding_change_reason_nonempty",
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
    sa.CheckConstraint("market <> ''", name="institutional_summary_market_nonempty"),
    sa.CheckConstraint(
        "institution <> ''", name="institutional_summary_institution_nonempty"
    ),
    sa.CheckConstraint(
        "num_nonnulls(buy, sell, net) > 0",
        name="institutional_summary_value_present",
    ),
    sa.CheckConstraint(
        "(buy IS NULL OR buy >= 0) AND (sell IS NULL OR sell >= 0)",
        name="institutional_summary_gross_nonnegative",
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
    sa.CheckConstraint(
        "num_nonnulls(margin_buy, margin_sell, margin_cash_repayment, "
        "margin_previous_balance, margin_balance, margin_next_limit, "
        "margin_utilization_ratio, short_buy, short_sell, "
        "short_stock_repayment, short_previous_balance, short_balance, "
        "short_next_limit, short_utilization_ratio, offset_balance) > 0",
        name="margin_trading_value_present",
    ),
    sa.CheckConstraint(
        "(margin_buy IS NULL OR margin_buy >= 0) AND "
        "(margin_sell IS NULL OR margin_sell >= 0) AND "
        "(margin_cash_repayment IS NULL OR margin_cash_repayment >= 0) AND "
        "(margin_previous_balance IS NULL OR margin_previous_balance >= 0) AND "
        "(margin_balance IS NULL OR margin_balance >= 0) AND "
        "(margin_next_limit IS NULL OR margin_next_limit >= 0) AND "
        "(short_buy IS NULL OR short_buy >= 0) AND "
        "(short_sell IS NULL OR short_sell >= 0) AND "
        "(short_stock_repayment IS NULL OR short_stock_repayment >= 0) AND "
        "(short_previous_balance IS NULL OR short_previous_balance >= 0) AND "
        "(short_balance IS NULL OR short_balance >= 0) AND "
        "(short_next_limit IS NULL OR short_next_limit >= 0) AND "
        "(offset_balance IS NULL OR offset_balance >= 0)",
        name="margin_trading_quantities_nonnegative",
    ),
    sa.CheckConstraint(
        "(margin_utilization_ratio IS NULL OR margin_utilization_ratio >= 0) AND "
        "(short_utilization_ratio IS NULL OR short_utilization_ratio >= 0)",
        name="margin_trading_ratios_nonnegative",
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
    sa.CheckConstraint(
        "num_nonnulls(previous_balance, borrowed, returned, balance, next_limit, "
        "next_available_limit, adjustment) > 0",
        name="securities_lending_value_present",
    ),
    sa.CheckConstraint(
        "(previous_balance IS NULL OR previous_balance >= 0) AND "
        "(borrowed IS NULL OR borrowed >= 0) AND "
        "(returned IS NULL OR returned >= 0) AND "
        "(balance IS NULL OR balance >= 0) AND "
        "(next_limit IS NULL OR next_limit >= 0) AND "
        "(next_available_limit IS NULL OR next_available_limit >= 0)",
        name="securities_lending_quantities_nonnegative",
    ),
    sa.CheckConstraint(
        "note IS NULL OR note <> ''", name="securities_lending_note_nonempty"
    ),
)


def _observed_version_observations(
    table_name: str,
    version_column: str,
    version_table: str,
) -> sa.Table:
    return sa.Table(
        table_name,
        metadata,
        sa.Column(version_column, sa.BigInteger(), nullable=False),
        sa.Column("raw_artifact_id", uuid_type, nullable=False),
        sa.Column("ingest_run_id", uuid_type, nullable=False),
        sa.PrimaryKeyConstraint(version_column, "raw_artifact_id", "ingest_run_id"),
        sa.ForeignKeyConstraint(
            [version_column], [f"{version_table}.id"], ondelete="RESTRICT"
        ),
        *lineage_constraints(),
    )


institutional_investor_version_observations = _observed_version_observations(
    "institutional_investor_version_observations",
    "institutional_investor_version_id",
    "institutional_investor_versions",
)
foreign_holding_version_observations = _observed_version_observations(
    "foreign_holding_version_observations",
    "foreign_holding_version_id",
    "foreign_holding_versions",
)
institutional_market_summary_version_observations = _observed_version_observations(
    "institutional_market_summary_version_observations",
    "institutional_market_summary_version_id",
    "institutional_market_summary_versions",
)
margin_trading_version_observations = _observed_version_observations(
    "margin_trading_version_observations",
    "margin_trading_version_id",
    "margin_trading_versions",
)
securities_lending_version_observations = _observed_version_observations(
    "securities_lending_version_observations",
    "securities_lending_version_id",
    "securities_lending_versions",
)

market_index = sa.Table(
    "market_index",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("index_code", sa.String(64), nullable=False, unique=True),
    sa.Column("created_at", aware_timestamp, nullable=False,
              server_default=sa.text("statement_timestamp()")),
    sa.CheckConstraint("index_code <> ''", name="market_index_code_nonempty"),
)

market_index_metadata_versions = sa.Table(
    "market_index_metadata_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("market_index_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("effective_from", sa.Date(), nullable=False),
    sa.Column("effective_to", sa.Date()),
    sa.Column("market", sa.String(32), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["market_index_id"], ["market_index.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "market_index_id", "source", "effective_from", "business_content_hash",
        name="uq_market_index_metadata_business_revision",
    ),
    sa.CheckConstraint("market <> ''", name="market_index_metadata_market_nonempty"),
    sa.CheckConstraint("name <> ''", name="market_index_metadata_name_nonempty"),
    sa.CheckConstraint(
        "effective_to IS NULL OR effective_to >= effective_from",
        name="market_index_metadata_effective_range",
    ),
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
    sa.CheckConstraint(
        "open_value IS NULL OR open_value >= 0", name="market_index_open_nonnegative"
    ),
    sa.CheckConstraint(
        "high_value IS NULL OR high_value >= 0", name="market_index_high_nonnegative"
    ),
    sa.CheckConstraint(
        "low_value IS NULL OR low_value >= 0", name="market_index_low_nonnegative"
    ),
    sa.CheckConstraint("close_value >= 0", name="market_index_close_nonnegative"),
    sa.CheckConstraint(
        "trade_value IS NULL OR trade_value >= 0",
        name="market_index_trade_value_nonnegative",
    ),
    sa.CheckConstraint(
        "high_value IS NULL OR (high_value >= close_value AND "
        "(open_value IS NULL OR high_value >= open_value) AND "
        "(low_value IS NULL OR high_value >= low_value))",
        name="market_index_high_consistent",
    ),
    sa.CheckConstraint(
        "low_value IS NULL OR (low_value <= close_value AND "
        "(open_value IS NULL OR low_value <= open_value))",
        name="market_index_low_consistent",
    ),
)

corporate_action_events = sa.Table(
    "corporate_action_events",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("security_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("source_event_key", sa.Text(), nullable=False),
    sa.Column("created_at", aware_timestamp, nullable=False,
              server_default=sa.text("statement_timestamp()")),
    sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
    sa.UniqueConstraint(
        "security_id", "source", "source_event_key",
        name="uq_corporate_action_event_source_key",
    ),
    sa.CheckConstraint("source <> ''", name="corporate_action_event_source_nonempty"),
    sa.CheckConstraint(
        "source_event_key <> ''", name="corporate_action_event_key_nonempty"
    ),
)

corporate_action_versions = sa.Table(
    "corporate_action_versions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("event_id", sa.BigInteger(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("action_type", sa.String(32), nullable=False),
    sa.Column("capital_reduction_kind", sa.String(48)),
    sa.Column("announcement_date", sa.Date()),
    sa.Column("ex_date", sa.Date()),
    sa.Column("record_date", sa.Date()),
    sa.Column("payment_date", sa.Date()),
    sa.Column("cash_dividend_per_share", sa.Numeric(24, 8)),
    sa.Column("capital_reduction_cash_return_per_share", sa.Numeric(24, 8)),
    sa.Column("earnings_stock_ratio", sa.Numeric(28, 12)),
    sa.Column("capital_surplus_stock_ratio", sa.Numeric(28, 12)),
    sa.Column("free_share_ratio", sa.Numeric(28, 12)),
    sa.Column("old_shares", sa.Numeric(24, 8)),
    sa.Column("new_shares", sa.Numeric(24, 8)),
    sa.Column("rights_ratio", sa.Numeric(28, 12)),
    sa.Column("subscription_price", sa.Numeric(20, 6)),
    sa.Column("close_before", sa.Numeric(20, 6)),
    sa.Column("official_reference_price", sa.Numeric(20, 6)),
    sa.Column("official_rights_dividend_value", sa.Numeric(20, 6)),
    sa.Column("source_event_type", sa.Text()),
    sa.Column("source_terms", jsonb_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(["event_id"], ["corporate_action_events.id"], ondelete="RESTRICT"),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "event_id", "source", "business_content_hash",
        name="uq_corporate_action_business_revision",
    ),
    sa.CheckConstraint(
        "action_type IN ('cash_dividend', 'earnings_stock_dividend', "
        "'capital_surplus_stock_dividend', 'stock_split', 'reverse_split', "
        "'rights_issue', 'capital_reduction', 'ex_dividend', 'ex_right', "
        "'ex_right_dividend', 'other', 'stock_dividend', 'rights')",
        name="action_type_value",
    ),
    sa.CheckConstraint(
        "announcement_date IS NULL OR ex_date IS NULL OR announcement_date <= ex_date",
        name="corporate_action_announcement_by_ex_date",
    ),
    sa.CheckConstraint(
        "num_nonnulls(announcement_date, record_date, payment_date, "
        "cash_dividend_per_share, capital_reduction_cash_return_per_share, "
        "capital_reduction_kind, earnings_stock_ratio, "
        "capital_surplus_stock_ratio, free_share_ratio, old_shares, new_shares, "
        "rights_ratio, subscription_price, close_before, official_reference_price, "
        "official_rights_dividend_value, source_event_type) > 0 "
        "OR ex_date IS NOT NULL OR source_terms <> '{}'::jsonb",
        name="corporate_action_value_present",
    ),
    sa.CheckConstraint(
        "(cash_dividend_per_share IS NULL OR cash_dividend_per_share >= 0) AND "
        "(capital_reduction_cash_return_per_share IS NULL OR "
        "capital_reduction_cash_return_per_share > 0) AND "
        "(earnings_stock_ratio IS NULL OR earnings_stock_ratio > 0) AND "
        "(capital_surplus_stock_ratio IS NULL OR capital_surplus_stock_ratio > 0) AND "
        "(free_share_ratio IS NULL OR free_share_ratio > 0) AND "
        "(old_shares IS NULL OR old_shares > 0) AND "
        "(new_shares IS NULL OR new_shares > 0) AND "
        "(rights_ratio IS NULL OR rights_ratio > 0) AND "
        "(subscription_price IS NULL OR subscription_price >= 0) AND "
        "(close_before IS NULL OR close_before >= 0) AND "
        "(official_reference_price IS NULL OR official_reference_price >= 0)",
        # official_rights_dividend_value is a signed difference (Step 19-a).
        name="corporate_action_values_nonnegative",
    ),
    sa.CheckConstraint(
        "(old_shares IS NULL) = (new_shares IS NULL)",
        name="corporate_action_share_pair",
    ),
    sa.CheckConstraint(
        "free_share_ratio IS NULL OR free_share_ratio >= "
        "coalesce(earnings_stock_ratio, 0) + coalesce(capital_surplus_stock_ratio, 0)",
        name="corporate_action_free_share_components",
    ),
    sa.CheckConstraint(
        "record_date IS NULL OR ex_date IS NULL OR ex_date <= record_date",
        name="corporate_action_ex_by_record_date",
    ),
    sa.CheckConstraint(
        "payment_date IS NULL OR record_date IS NULL OR record_date <= payment_date",
        name="corporate_action_record_by_payment_date",
    ),
    sa.CheckConstraint(
        "source_event_type IS NULL OR source_event_type <> ''",
        name="corporate_action_source_event_type_nonempty",
    ),
    sa.CheckConstraint(
        "(action_type <> 'cash_dividend' OR cash_dividend_per_share > 0) AND "
        "(action_type <> 'earnings_stock_dividend' OR earnings_stock_ratio IS NOT NULL) AND "
        "(action_type <> 'capital_surplus_stock_dividend' OR capital_surplus_stock_ratio IS NOT NULL) AND "
        "(action_type <> 'rights_issue' OR rights_ratio IS NOT NULL) AND "
        "(action_type NOT IN ('ex_dividend','ex_right','ex_right_dividend') OR ex_date IS NOT NULL)",
        name="corporate_action_required_terms",
    ),
    sa.CheckConstraint(
        "action_type NOT IN ('stock_split','reverse_split','capital_reduction') OR "
        "(old_shares IS NOT NULL AND new_shares IS NOT NULL AND old_shares <> new_shares)",
        name="corporate_action_share_change_required",
    ),
    sa.CheckConstraint(
        "(action_type <> 'stock_split' OR new_shares > old_shares) AND "
        "(action_type NOT IN ('reverse_split','capital_reduction') OR new_shares < old_shares)",
        name="corporate_action_share_change_direction",
    ),
    sa.CheckConstraint(
        "action_type NOT IN ('stock_dividend', 'rights')",
        name="corporate_action_unambiguous_new_type",
    ),
    sa.CheckConstraint(
        "capital_reduction_kind IS NULL OR capital_reduction_kind IN "
        "('cash_refund','loss_offset','loss_offset_with_cash_increase','other')",
        name="corporate_action_reduction_kind_value",
    ),
    sa.CheckConstraint(
        "(action_type = 'capital_reduction') = (capital_reduction_kind IS NOT NULL) AND "
        "(capital_reduction_cash_return_per_share IS NULL OR "
        "action_type = 'capital_reduction')",
        name="corporate_action_reduction_terms_scope",
    ),
    sa.CheckConstraint(
        "(capital_reduction_kind <> 'cash_refund' OR "
        "capital_reduction_cash_return_per_share IS NOT NULL) AND "
        "(capital_reduction_kind NOT IN "
        "('loss_offset','loss_offset_with_cash_increase') OR "
        "capital_reduction_cash_return_per_share IS NULL)",
        name="corporate_action_reduction_cash_semantics",
    ),
)

corporate_action_retractions = sa.Table(
    "corporate_action_retractions",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("event_id", sa.BigInteger(), nullable=False),
    sa.Column("reason", sa.Text(), nullable=False),
    sa.Column("ingested_at", aware_timestamp, nullable=False),
    sa.Column("raw_artifact_id", uuid_type, nullable=False),
    sa.Column("ingest_run_id", uuid_type, nullable=False),
    sa.ForeignKeyConstraint(
        ["event_id"], ["corporate_action_events.id"], ondelete="RESTRICT"
    ),
    *lineage_constraints(),
    sa.UniqueConstraint(
        "event_id", "raw_artifact_id",
        name="uq_corporate_action_retraction_artifact",
    ),
    sa.CheckConstraint(
        "reason <> ''", name="corporate_action_retraction_reason_nonempty"
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
    sa.CheckConstraint(
        "num_nonnulls(pe_ratio, pb_ratio, dividend_yield, dividend_per_share) > 0",
        name="official_valuation_value_present",
    ),
    sa.CheckConstraint(
        "(pe_ratio IS NULL OR pe_ratio > 0) AND "
        "(pb_ratio IS NULL OR pb_ratio > 0) AND "
        "(dividend_yield IS NULL OR dividend_yield >= 0) AND "
        "(dividend_per_share IS NULL OR dividend_per_share >= 0)",
        name="official_valuation_values_valid",
    ),
    sa.CheckConstraint(
        "dividend_year IS NULL OR dividend_year BETWEEN 1900 AND 9999",
        name="official_valuation_dividend_year_valid",
    ),
    sa.CheckConstraint(
        "report_period IS NULL OR report_period <> ''",
        name="official_valuation_report_period_nonempty",
    ),
)

market_index_version_observations = _observed_version_observations(
    "market_index_version_observations",
    "market_index_version_id",
    "market_index_versions",
)
market_index_metadata_version_observations = _observed_version_observations(
    "market_index_metadata_version_observations",
    "market_index_metadata_version_id",
    "market_index_metadata_versions",
)
corporate_action_version_observations = _observed_version_observations(
    "corporate_action_version_observations",
    "corporate_action_version_id",
    "corporate_action_versions",
)
official_valuation_version_observations = _observed_version_observations(
    "official_valuation_version_observations",
    "official_valuation_version_id",
    "official_valuation_versions",
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
    sa.Column("market_index_metadata_version_id", sa.BigInteger()),
    sa.Column("corporate_action_version_id", sa.BigInteger()),
    sa.Column("official_valuation_version_id", sa.BigInteger()),
    sa.Column("security_tag_version_id", sa.BigInteger()),
    sa.Column("xbrl_concept_catalog_version_id", sa.BigInteger()),
    sa.Column("trading_calendar_version_id", sa.BigInteger()),
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
        ["market_index_metadata_version_id"], ["market_index_metadata_versions.id"],
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
    sa.ForeignKeyConstraint(
        ["trading_calendar_version_id"],
        ["trading_calendar_versions.id"], ondelete="RESTRICT"
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
        "market_index_version_id, market_index_metadata_version_id, "
        "corporate_action_version_id, "
        "official_valuation_version_id, security_tag_version_id, "
        "xbrl_concept_catalog_version_id, trading_calendar_version_id) = 1",
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
    ("ix_market_index_metadata_pit", market_index_metadata_versions,
     ("market_index_id", "source", "effective_from", "ingested_at")),
    ("ix_corporate_action_pit", corporate_action_versions,
     ("event_id", "source", "ingested_at")),
    ("ix_official_valuation_pit", official_valuation_versions,
     ("security_id", "source", "trade_date", "ingested_at")),
    ("ix_security_tag_pit", security_tag_versions,
     ("security_id", "source", "effective_from", "ingested_at")),
    ("ix_xbrl_concept_catalog_pit", xbrl_concept_catalog_versions,
     ("source", "concept_qname", "ingested_at")),
    ("ix_trading_calendar_pit", trading_calendar_versions,
     ("market", "source", "calendar_month", "ingested_at")),
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
