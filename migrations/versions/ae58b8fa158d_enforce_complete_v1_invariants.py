"""enforce complete v1 invariants

Revision ID: ae58b8fa158d
Revises: 58124040faa4
Create Date: 2026-09-11 15:40:11.698534
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ae58b8fa158d"
down_revision: str | None = "58124040faa4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_OBSERVED_TABLES = {
    "institutional_investor_versions": "institutional_investor",
    "foreign_holding_versions": "foreign_holding",
    "institutional_market_summary_versions": "institutional_market_summary",
    "margin_trading_versions": "margin_trading",
    "securities_lending_versions": "securities_lending",
    "market_index_versions": "market_index",
    "corporate_action_versions": "corporate_action",
    "official_valuation_versions": "official_valuation",
    "security_tag_versions": "security_tag",
    "xbrl_concept_catalog_versions": "xbrl_concept_catalog",
}

PIT_INDEXES = {
    "ix_security_metadata_pit": ("security_metadata_versions", "security_id, source, effective_from, ingested_at"),
    "ix_daily_price_pit": ("daily_price_versions", "security_id, source, trade_date, ingested_at"),
    "ix_monthly_revenue_pit": ("monthly_revenue_versions", "security_id, source, revenue_year, revenue_month, ingested_at"),
    "ix_institutional_investor_pit": ("institutional_investor_versions", "security_id, source, trade_date, ingested_at"),
    "ix_foreign_holding_pit": ("foreign_holding_versions", "security_id, source, trade_date, ingested_at"),
    "ix_institutional_summary_pit": ("institutional_market_summary_versions", "market, source, trade_date, institution, ingested_at"),
    "ix_margin_trading_pit": ("margin_trading_versions", "security_id, source, trade_date, ingested_at"),
    "ix_securities_lending_pit": ("securities_lending_versions", "security_id, source, trade_date, ingested_at"),
    "ix_market_index_pit": ("market_index_versions", "market_index_id, source, trade_date, ingested_at"),
    "ix_corporate_action_pit": ("corporate_action_versions", "security_id, source, ex_date, ingested_at"),
    "ix_official_valuation_pit": ("official_valuation_versions", "security_id, source, trade_date, ingested_at"),
    "ix_security_tag_pit": ("security_tag_versions", "security_id, source, effective_from, ingested_at"),
    "ix_xbrl_concept_catalog_pit": ("xbrl_concept_catalog_versions", "source, concept_qname, ingested_at"),
    "ix_publication_evidence_resolution": ("publication_evidence", "dataset_code, source, published_at, recorded_at"),
}


FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION stockdc_prepare_daily_price() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, 'daily_price', NEW.source
    );
    NEW.ingested_at := statement_timestamp();
    NEW.business_content_hash := encode(digest(convert_to(
        jsonb_build_object(
            'open_price', NEW.open_price, 'high_price', NEW.high_price,
            'low_price', NEW.low_price, 'close_price', NEW.close_price,
            'volume', NEW.volume, 'trade_value', NEW.trade_value,
            'trade_count', NEW.trade_count, 'price_change', NEW.price_change,
            'price_direction', NEW.price_direction,
            'bid_snapshot', NEW.bid_snapshot, 'ask_snapshot', NEW.ask_snapshot,
            'last_bid_price', NEW.last_bid_price,
            'last_ask_price', NEW.last_ask_price,
            'last_bid_volume', NEW.last_bid_volume,
            'last_ask_volume', NEW.last_ask_volume
        )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_prepare_observed_version() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    canonical_content jsonb;
BEGIN
    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, TG_ARGV[0], NEW.source
    );
    NEW.ingested_at := statement_timestamp();
    canonical_content := to_jsonb(NEW) - ARRAY[
        'id', 'business_content_hash', 'ingested_at',
        'raw_artifact_id', 'ingest_run_id', 'source',
        'security_id', 'market_index_id', 'trade_date'
    ];
    NEW.business_content_hash := encode(digest(
        convert_to(canonical_content::text, 'UTF8'), 'sha256'
    ), 'hex');
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_prepare_derived_definition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.registered_at := statement_timestamp();
    NEW.definition_hash := encode(digest(convert_to(
        jsonb_build_object(
            'dataset_code', NEW.dataset_code,
            'derivation_version', NEW.derivation_version,
            'storage_strategy', NEW.storage_strategy,
            'formula_specification', NEW.formula_specification,
            'implementation_version', NEW.implementation_version,
            'input_dataset_codes', NEW.input_dataset_codes,
            'calendar_timezone', NEW.calendar_timezone,
            'calendar_convention', NEW.calendar_convention,
            'price_adjustment_convention', NEW.price_adjustment_convention
        )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_prepare_derived_metric() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    run_definition_id bigint;
BEGIN
    SELECT definition_id INTO run_definition_id
      FROM derived_computation_runs WHERE id = NEW.computation_run_id;
    IF run_definition_id IS NULL THEN
        RAISE EXCEPTION 'derived computation run does not exist'
            USING ERRCODE = '23503';
    END IF;
    IF run_definition_id <> NEW.definition_id THEN
        RAISE EXCEPTION 'derived computation run definition mismatch'
            USING ERRCODE = '23514';
    END IF;
    NEW.computed_at := statement_timestamp();
    NEW.business_content_hash := encode(digest(convert_to(
        jsonb_build_object(
            'metric_code', NEW.metric_code,
            'numeric_value', NEW.numeric_value,
            'text_value', NEW.text_value,
            'json_value', NEW.json_value
        )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION stockdc_prepare_publication_evidence() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_dataset text;
    target_source text;
    prior publication_evidence%ROWTYPE;
BEGIN
    NEW.recorded_at := statement_timestamp();
    IF NEW.security_metadata_version_id IS NOT NULL THEN
        target_dataset := 'security_metadata';
        SELECT source INTO target_source FROM security_metadata_versions WHERE id = NEW.security_metadata_version_id;
    ELSIF NEW.daily_price_version_id IS NOT NULL THEN
        target_dataset := 'daily_price';
        SELECT source INTO target_source FROM daily_price_versions WHERE id = NEW.daily_price_version_id;
    ELSIF NEW.monthly_revenue_version_id IS NOT NULL THEN
        target_dataset := 'monthly_revenue';
        SELECT source INTO target_source FROM monthly_revenue_versions WHERE id = NEW.monthly_revenue_version_id;
    ELSIF NEW.financial_filing_version_id IS NOT NULL THEN
        target_dataset := 'financial_filing';
        SELECT source INTO target_source FROM financial_filing_versions WHERE id = NEW.financial_filing_version_id;
    ELSIF NEW.tdcc_snapshot_version_id IS NOT NULL THEN
        target_dataset := 'tdcc_snapshot';
        SELECT source INTO target_source FROM tdcc_snapshot_versions WHERE id = NEW.tdcc_snapshot_version_id;
    ELSIF NEW.institutional_investor_version_id IS NOT NULL THEN
        target_dataset := 'institutional_investor';
        SELECT source INTO target_source FROM institutional_investor_versions WHERE id = NEW.institutional_investor_version_id;
    ELSIF NEW.foreign_holding_version_id IS NOT NULL THEN
        target_dataset := 'foreign_holding';
        SELECT source INTO target_source FROM foreign_holding_versions WHERE id = NEW.foreign_holding_version_id;
    ELSIF NEW.institutional_market_summary_version_id IS NOT NULL THEN
        target_dataset := 'institutional_market_summary';
        SELECT source INTO target_source FROM institutional_market_summary_versions WHERE id = NEW.institutional_market_summary_version_id;
    ELSIF NEW.margin_trading_version_id IS NOT NULL THEN
        target_dataset := 'margin_trading';
        SELECT source INTO target_source FROM margin_trading_versions WHERE id = NEW.margin_trading_version_id;
    ELSIF NEW.securities_lending_version_id IS NOT NULL THEN
        target_dataset := 'securities_lending';
        SELECT source INTO target_source FROM securities_lending_versions WHERE id = NEW.securities_lending_version_id;
    ELSIF NEW.market_index_version_id IS NOT NULL THEN
        target_dataset := 'market_index';
        SELECT source INTO target_source FROM market_index_versions WHERE id = NEW.market_index_version_id;
    ELSIF NEW.corporate_action_version_id IS NOT NULL THEN
        target_dataset := 'corporate_action';
        SELECT source INTO target_source FROM corporate_action_versions WHERE id = NEW.corporate_action_version_id;
    ELSIF NEW.official_valuation_version_id IS NOT NULL THEN
        target_dataset := 'official_valuation';
        SELECT source INTO target_source FROM official_valuation_versions WHERE id = NEW.official_valuation_version_id;
    ELSIF NEW.security_tag_version_id IS NOT NULL THEN
        target_dataset := 'security_tag';
        SELECT source INTO target_source FROM security_tag_versions WHERE id = NEW.security_tag_version_id;
    ELSIF NEW.xbrl_concept_catalog_version_id IS NOT NULL THEN
        target_dataset := 'xbrl_concept_catalog';
        SELECT source INTO target_source FROM xbrl_concept_catalog_versions WHERE id = NEW.xbrl_concept_catalog_version_id;
    ELSE
        RAISE EXCEPTION 'publication evidence requires exactly one target' USING ERRCODE = '23514';
    END IF;
    IF target_source IS NULL THEN
        RAISE EXCEPTION 'publication evidence target does not exist' USING ERRCODE = '23503';
    END IF;
    IF NEW.dataset_code <> target_dataset OR NEW.source <> target_source THEN
        RAISE EXCEPTION 'evidence dataset/source does not match target' USING ERRCODE = '23514';
    END IF;
    PERFORM stockdc_assert_lineage(NEW.raw_artifact_id, NEW.ingest_run_id, NEW.dataset_code, NEW.source);

    IF NEW.supersedes_evidence_id IS NOT NULL THEN
        SELECT * INTO prior FROM publication_evidence WHERE id = NEW.supersedes_evidence_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'superseded evidence does not exist' USING ERRCODE = '23503';
        END IF;
        IF prior.dataset_code <> NEW.dataset_code OR prior.source <> NEW.source
           OR jsonb_build_array(
                prior.security_metadata_version_id, prior.daily_price_version_id,
                prior.monthly_revenue_version_id, prior.financial_filing_version_id,
                prior.tdcc_snapshot_version_id, prior.institutional_investor_version_id,
                prior.foreign_holding_version_id, prior.institutional_market_summary_version_id,
                prior.margin_trading_version_id, prior.securities_lending_version_id,
                prior.market_index_version_id, prior.corporate_action_version_id,
                prior.official_valuation_version_id, prior.security_tag_version_id,
                prior.xbrl_concept_catalog_version_id
              ) IS DISTINCT FROM jsonb_build_array(
                NEW.security_metadata_version_id, NEW.daily_price_version_id,
                NEW.monthly_revenue_version_id, NEW.financial_filing_version_id,
                NEW.tdcc_snapshot_version_id, NEW.institutional_investor_version_id,
                NEW.foreign_holding_version_id, NEW.institutional_market_summary_version_id,
                NEW.margin_trading_version_id, NEW.securities_lending_version_id,
                NEW.market_index_version_id, NEW.corporate_action_version_id,
                NEW.official_valuation_version_id, NEW.security_tag_version_id,
                NEW.xbrl_concept_catalog_version_id
              ) THEN
            RAISE EXCEPTION 'supersession must stay on the same target and source' USING ERRCODE = '23514';
        END IF;
    END IF;

    NEW.publication_evidence_hash := encode(digest(convert_to(
        (to_jsonb(NEW) - ARRAY['id', 'publication_evidence_hash', 'recorded_at',
            'raw_artifact_id', 'ingest_run_id'])::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;
"""


OLD_FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION stockdc_prepare_daily_price() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM stockdc_assert_lineage(NEW.raw_artifact_id, NEW.ingest_run_id, 'daily_price', NEW.source);
    NEW.ingested_at := statement_timestamp();
    NEW.business_content_hash := encode(digest(convert_to(jsonb_build_object(
        'open_price', NEW.open_price, 'high_price', NEW.high_price,
        'low_price', NEW.low_price, 'close_price', NEW.close_price,
        'volume', NEW.volume
    )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION stockdc_prepare_publication_evidence() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE target_dataset text; target_source text; prior publication_evidence%ROWTYPE;
BEGIN
    NEW.recorded_at := statement_timestamp();
    IF NEW.security_metadata_version_id IS NOT NULL THEN
        target_dataset := 'security_metadata'; SELECT source INTO target_source FROM security_metadata_versions WHERE id = NEW.security_metadata_version_id;
    ELSIF NEW.daily_price_version_id IS NOT NULL THEN
        target_dataset := 'daily_price'; SELECT source INTO target_source FROM daily_price_versions WHERE id = NEW.daily_price_version_id;
    ELSIF NEW.monthly_revenue_version_id IS NOT NULL THEN
        target_dataset := 'monthly_revenue'; SELECT source INTO target_source FROM monthly_revenue_versions WHERE id = NEW.monthly_revenue_version_id;
    ELSIF NEW.financial_filing_version_id IS NOT NULL THEN
        target_dataset := 'financial_filing'; SELECT source INTO target_source FROM financial_filing_versions WHERE id = NEW.financial_filing_version_id;
    ELSIF NEW.tdcc_snapshot_version_id IS NOT NULL THEN
        target_dataset := 'tdcc_snapshot'; SELECT source INTO target_source FROM tdcc_snapshot_versions WHERE id = NEW.tdcc_snapshot_version_id;
    ELSE RAISE EXCEPTION 'publication evidence requires exactly one target' USING ERRCODE = '23514';
    END IF;
    IF target_source IS NULL THEN RAISE EXCEPTION 'publication evidence target does not exist' USING ERRCODE = '23503'; END IF;
    IF NEW.dataset_code <> target_dataset OR NEW.source <> target_source THEN
        RAISE EXCEPTION 'evidence dataset/source does not match target' USING ERRCODE = '23514';
    END IF;
    PERFORM stockdc_assert_lineage(NEW.raw_artifact_id, NEW.ingest_run_id, NEW.dataset_code, NEW.source);
    IF NEW.supersedes_evidence_id IS NOT NULL THEN
        SELECT * INTO prior FROM publication_evidence WHERE id = NEW.supersedes_evidence_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'superseded evidence does not exist' USING ERRCODE = '23503'; END IF;
        IF prior.dataset_code <> NEW.dataset_code OR prior.source <> NEW.source
           OR prior.security_metadata_version_id IS DISTINCT FROM NEW.security_metadata_version_id
           OR prior.daily_price_version_id IS DISTINCT FROM NEW.daily_price_version_id
           OR prior.monthly_revenue_version_id IS DISTINCT FROM NEW.monthly_revenue_version_id
           OR prior.financial_filing_version_id IS DISTINCT FROM NEW.financial_filing_version_id
           OR prior.tdcc_snapshot_version_id IS DISTINCT FROM NEW.tdcc_snapshot_version_id THEN
            RAISE EXCEPTION 'supersession must stay on the same target and source' USING ERRCODE = '23514';
        END IF;
    END IF;
    NEW.publication_evidence_hash := encode(digest(convert_to(jsonb_build_object(
        'dataset_code', NEW.dataset_code, 'source', NEW.source,
        'evidence_kind', NEW.evidence_kind, 'published_at', NEW.published_at,
        'evidence_source', NEW.evidence_source, 'evidence_type', NEW.evidence_type,
        'quality_rank', NEW.quality_rank, 'supersedes_evidence_id', NEW.supersedes_evidence_id,
        'security_metadata_version_id', NEW.security_metadata_version_id,
        'daily_price_version_id', NEW.daily_price_version_id,
        'monthly_revenue_version_id', NEW.monthly_revenue_version_id,
        'financial_filing_version_id', NEW.financial_filing_version_id,
        'tdcc_snapshot_version_id', NEW.tdcc_snapshot_version_id
    )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    """Enforce trusted times, hashes, lineage, and append-only storage.

    Cache impact: none; Phase 1 deliberately has no cache namespace or data.
    """
    op.execute(FUNCTIONS)
    for table, dataset in NEW_OBSERVED_TABLES.items():
        op.execute(
            f"""CREATE TRIGGER prepare_{table}
            BEFORE INSERT ON {table} FOR EACH ROW
            EXECUTE FUNCTION stockdc_prepare_observed_version('{dataset}');
            CREATE TRIGGER immutable_{table}
            BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW
            EXECUTE FUNCTION stockdc_reject_mutation();
            CREATE TRIGGER no_truncate_{table}
            BEFORE TRUNCATE ON {table} FOR EACH STATEMENT
            EXECUTE FUNCTION stockdc_reject_mutation();"""
        )
    for name, (table, columns) in PIT_INDEXES.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})")
    op.execute("""
        CREATE TRIGGER prepare_derived_definition
        BEFORE INSERT ON derived_dataset_definitions FOR EACH ROW
        EXECUTE FUNCTION stockdc_prepare_derived_definition();
        CREATE TRIGGER immutable_derived_definition
        BEFORE UPDATE OR DELETE ON derived_dataset_definitions FOR EACH ROW
        EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER no_truncate_derived_definition
        BEFORE TRUNCATE ON derived_dataset_definitions FOR EACH STATEMENT
        EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER prepare_derived_metric
        BEFORE INSERT ON derived_metric_versions FOR EACH ROW
        EXECUTE FUNCTION stockdc_prepare_derived_metric();
        CREATE TRIGGER immutable_derived_metric
        BEFORE UPDATE OR DELETE ON derived_metric_versions FOR EACH ROW
        EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER no_truncate_derived_metric
        BEFORE TRUNCATE ON derived_metric_versions FOR EACH STATEMENT
        EXECUTE FUNCTION stockdc_reject_mutation();
    """)


def downgrade() -> None:
    for name in PIT_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    for table in NEW_OBSERVED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS no_truncate_{table} ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS immutable_{table} ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS prepare_{table} ON {table}")
    op.execute("""
        DROP TRIGGER IF EXISTS no_truncate_derived_metric ON derived_metric_versions;
        DROP TRIGGER IF EXISTS immutable_derived_metric ON derived_metric_versions;
        DROP TRIGGER IF EXISTS prepare_derived_metric ON derived_metric_versions;
        DROP TRIGGER IF EXISTS no_truncate_derived_definition ON derived_dataset_definitions;
        DROP TRIGGER IF EXISTS immutable_derived_definition ON derived_dataset_definitions;
        DROP TRIGGER IF EXISTS prepare_derived_definition ON derived_dataset_definitions;
        DROP FUNCTION IF EXISTS stockdc_prepare_derived_metric();
        DROP FUNCTION IF EXISTS stockdc_prepare_derived_definition();
        DROP FUNCTION IF EXISTS stockdc_prepare_observed_version();
    """)
    op.execute(OLD_FUNCTIONS)
