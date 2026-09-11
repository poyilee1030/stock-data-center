"""enforce phase 1 invariants

Revision ID: b7e1c9a42f10
Revises: 94060901029e
Create Date: 2026-09-11 14:05:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7e1c9a42f10"
down_revision: str | None = "94060901029e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FUNCTIONS_AND_TRIGGERS = r"""
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE FUNCTION stockdc_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is forbidden', TG_TABLE_NAME, TG_OP
        USING ERRCODE = '55000';
END;
$$;

CREATE FUNCTION stockdc_assert_lineage(
    p_raw_artifact_id uuid,
    p_ingest_run_id uuid,
    p_dataset_code text,
    p_source text
) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    run_dataset text;
    run_source text;
BEGIN
    SELECT ir.dataset_code, ir.source
      INTO run_dataset, run_source
      FROM raw_artifact_observations observation
      JOIN ingest_runs ir ON ir.id = observation.ingest_run_id
     WHERE observation.raw_artifact_id = p_raw_artifact_id
       AND observation.ingest_run_id = p_ingest_run_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'artifact % does not belong to ingest run %',
            p_raw_artifact_id, p_ingest_run_id USING ERRCODE = '23503';
    END IF;
    IF run_dataset <> p_dataset_code OR run_source <> p_source THEN
        RAISE EXCEPTION 'lineage dataset/source mismatch: expected %/%, got %/%',
            p_dataset_code, p_source, run_dataset, run_source
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE FUNCTION stockdc_prepare_security_metadata() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, 'security_metadata', NEW.source
    );
    NEW.ingested_at := statement_timestamp();
    NEW.business_content_hash := encode(digest(convert_to(
        jsonb_build_object(
            'effective_from', NEW.effective_from,
            'effective_to', NEW.effective_to,
            'name', NEW.name,
            'industry', NEW.industry,
            'listed_on', NEW.listed_on,
            'delisted_on', NEW.delisted_on
        )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_prepare_daily_price() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, 'daily_price', NEW.source
    );
    NEW.ingested_at := statement_timestamp();
    NEW.business_content_hash := encode(digest(convert_to(
        jsonb_build_object(
            'open_price', NEW.open_price,
            'high_price', NEW.high_price,
            'low_price', NEW.low_price,
            'close_price', NEW.close_price,
            'volume', NEW.volume
        )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_prepare_monthly_revenue() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, 'monthly_revenue', NEW.source
    );
    NEW.ingested_at := statement_timestamp();
    NEW.business_content_hash := encode(digest(convert_to(
        jsonb_build_object(
            'revenue', NEW.revenue,
            'currency', NEW.currency
        )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_prepare_financial_filing() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, 'financial_filing', NEW.source
    );
    NEW.business_content_hash := NULL;
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_prepare_tdcc_snapshot() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, 'tdcc_snapshot', NEW.source
    );
    NEW.business_content_hash := NULL;
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_prepare_financial_fact() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.context_hash := encode(digest(convert_to(
        jsonb_build_object(
            'entity_identifier', NEW.entity_identifier,
            'period_type', NEW.period_type,
            'instant_date', NEW.instant_date,
            'period_start', NEW.period_start,
            'period_end', NEW.period_end,
            'explicit_dimensions', NEW.explicit_dimensions,
            'typed_dimensions', NEW.typed_dimensions,
            'scenario', NEW.scenario,
            'segment', NEW.segment
        )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_prepare_publication_evidence() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_dataset text;
    target_source text;
    prior publication_evidence%ROWTYPE;
BEGIN
    NEW.recorded_at := statement_timestamp();

    IF NEW.security_metadata_version_id IS NOT NULL THEN
        target_dataset := 'security_metadata';
        SELECT source INTO target_source FROM security_metadata_versions
         WHERE id = NEW.security_metadata_version_id;
    ELSIF NEW.daily_price_version_id IS NOT NULL THEN
        target_dataset := 'daily_price';
        SELECT source INTO target_source FROM daily_price_versions
         WHERE id = NEW.daily_price_version_id;
    ELSIF NEW.monthly_revenue_version_id IS NOT NULL THEN
        target_dataset := 'monthly_revenue';
        SELECT source INTO target_source FROM monthly_revenue_versions
         WHERE id = NEW.monthly_revenue_version_id;
    ELSIF NEW.financial_filing_version_id IS NOT NULL THEN
        target_dataset := 'financial_filing';
        SELECT source INTO target_source FROM financial_filing_versions
         WHERE id = NEW.financial_filing_version_id;
    ELSIF NEW.tdcc_snapshot_version_id IS NOT NULL THEN
        target_dataset := 'tdcc_snapshot';
        SELECT source INTO target_source FROM tdcc_snapshot_versions
         WHERE id = NEW.tdcc_snapshot_version_id;
    ELSE
        RAISE EXCEPTION 'publication evidence requires exactly one target'
            USING ERRCODE = '23514';
    END IF;

    IF target_source IS NULL THEN
        RAISE EXCEPTION 'publication evidence target does not exist'
            USING ERRCODE = '23503';
    END IF;
    IF NEW.dataset_code <> target_dataset OR NEW.source <> target_source THEN
        RAISE EXCEPTION 'evidence dataset/source does not match target'
            USING ERRCODE = '23514';
    END IF;

    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, NEW.dataset_code, NEW.source
    );

    IF NEW.supersedes_evidence_id IS NOT NULL THEN
        SELECT * INTO prior FROM publication_evidence
         WHERE id = NEW.supersedes_evidence_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'superseded evidence does not exist'
                USING ERRCODE = '23503';
        END IF;
        IF prior.dataset_code <> NEW.dataset_code
           OR prior.source <> NEW.source
           OR prior.security_metadata_version_id IS DISTINCT FROM NEW.security_metadata_version_id
           OR prior.daily_price_version_id IS DISTINCT FROM NEW.daily_price_version_id
           OR prior.monthly_revenue_version_id IS DISTINCT FROM NEW.monthly_revenue_version_id
           OR prior.financial_filing_version_id IS DISTINCT FROM NEW.financial_filing_version_id
           OR prior.tdcc_snapshot_version_id IS DISTINCT FROM NEW.tdcc_snapshot_version_id THEN
            RAISE EXCEPTION 'supersession must stay on the same target and source'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    NEW.publication_evidence_hash := encode(digest(convert_to(
        jsonb_build_object(
            'dataset_code', NEW.dataset_code,
            'source', NEW.source,
            'evidence_kind', NEW.evidence_kind,
            'published_at', NEW.published_at,
            'evidence_source', NEW.evidence_source,
            'evidence_type', NEW.evidence_type,
            'quality_rank', NEW.quality_rank,
            'supersedes_evidence_id', NEW.supersedes_evidence_id,
            'security_metadata_version_id', NEW.security_metadata_version_id,
            'daily_price_version_id', NEW.daily_price_version_id,
            'monthly_revenue_version_id', NEW.monthly_revenue_version_id,
            'financial_filing_version_id', NEW.financial_filing_version_id,
            'tdcc_snapshot_version_id', NEW.tdcc_snapshot_version_id
        )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_protect_financial_parent() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM financial_filing_seals
         WHERE filing_version_id = OLD.id
    ) THEN
        RAISE EXCEPTION 'sealed financial filing % is immutable', OLD.id
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.id <> OLD.id THEN
        RAISE EXCEPTION 'financial filing identity is immutable'
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_protect_financial_child() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent_id bigint;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.filing_version_id <> OLD.filing_version_id THEN
        RAISE EXCEPTION 'moving a financial child between filings is forbidden'
            USING ERRCODE = '55000';
    END IF;

    IF TG_OP = 'DELETE' THEN
        parent_id := OLD.filing_version_id;
    ELSE
        parent_id := NEW.filing_version_id;
    END IF;

    -- Child mutation and sealing must serialize on this exact parent row.
    -- If the child gets the lock first, a later seal includes the mutation.
    -- If the seal gets it first, this statement waits and then sees the seal.
    PERFORM 1 FROM financial_filing_versions
     WHERE id = parent_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'financial filing % does not exist', parent_id
            USING ERRCODE = '23503';
    END IF;

    IF EXISTS (
        SELECT 1 FROM financial_filing_seals WHERE filing_version_id = parent_id
    ) THEN
        RAISE EXCEPTION 'children of a sealed financial filing are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_seal_financial_filing() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    filing financial_filing_versions%ROWTYPE;
    canonical_payload jsonb;
    computed_hash text;
BEGIN
    SELECT * INTO filing FROM financial_filing_versions
     WHERE id = NEW.filing_version_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'financial filing % does not exist', NEW.filing_version_id
            USING ERRCODE = '23503';
    END IF;
    IF EXISTS (SELECT 1 FROM financial_filing_seals WHERE filing_version_id = filing.id) THEN
        RAISE EXCEPTION 'financial filing % is already sealed', filing.id
            USING ERRCODE = '23505';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM financial_facts WHERE filing_version_id = filing.id) THEN
        RAISE EXCEPTION 'financial filing % cannot be sealed without facts', filing.id
            USING ERRCODE = '23514';
    END IF;

    SELECT jsonb_build_object(
        'report_year', filing.report_year,
        'report_quarter', filing.report_quarter,
        'period_start', filing.period_start,
        'period_end', filing.period_end,
        'currency', filing.currency,
        'facts', (
            SELECT jsonb_agg(jsonb_build_object(
                'concept_qname', concept_qname,
                'context_hash', context_hash,
                'unit_identity', unit_identity,
                'numeric_value', numeric_value,
                'text_value', text_value,
                'decimals', decimals
            ) ORDER BY concept_qname, context_hash, unit_identity)
            FROM financial_facts WHERE filing_version_id = filing.id
        ),
        'summary', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'metric_code', metric_code,
                'value', value,
                'unit_identity', unit_identity
            ) ORDER BY metric_code)
            FROM quarterly_financial_summary WHERE filing_version_id = filing.id
        ), '[]'::jsonb)
    ) INTO canonical_payload;

    computed_hash := encode(digest(convert_to(canonical_payload::text, 'UTF8'), 'sha256'), 'hex');
    UPDATE financial_filing_versions
       SET business_content_hash = computed_hash
     WHERE id = filing.id;
    NEW.business_content_hash := computed_hash;
    NEW.ingested_at := statement_timestamp();
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_protect_tdcc_parent() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM tdcc_snapshot_seals WHERE snapshot_version_id = OLD.id
    ) THEN
        RAISE EXCEPTION 'sealed TDCC snapshot % is immutable', OLD.id
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.id <> OLD.id THEN
        RAISE EXCEPTION 'TDCC snapshot identity is immutable'
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_protect_tdcc_child() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent_id bigint;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.snapshot_version_id <> OLD.snapshot_version_id THEN
        RAISE EXCEPTION 'moving a TDCC child between snapshots is forbidden'
            USING ERRCODE = '55000';
    END IF;

    IF TG_OP = 'DELETE' THEN
        parent_id := OLD.snapshot_version_id;
    ELSE
        parent_id := NEW.snapshot_version_id;
    END IF;

    -- Use the same aggregate lock as sealing; see the financial equivalent.
    PERFORM 1 FROM tdcc_snapshot_versions
     WHERE id = parent_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TDCC snapshot % does not exist', parent_id
            USING ERRCODE = '23503';
    END IF;

    IF EXISTS (
        SELECT 1 FROM tdcc_snapshot_seals WHERE snapshot_version_id = parent_id
    ) THEN
        RAISE EXCEPTION 'children of a sealed TDCC snapshot are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION stockdc_seal_tdcc_snapshot() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    snapshot tdcc_snapshot_versions%ROWTYPE;
    canonical_payload jsonb;
    computed_hash text;
BEGIN
    SELECT * INTO snapshot FROM tdcc_snapshot_versions
     WHERE id = NEW.snapshot_version_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TDCC snapshot % does not exist', NEW.snapshot_version_id
            USING ERRCODE = '23503';
    END IF;
    IF EXISTS (SELECT 1 FROM tdcc_snapshot_seals WHERE snapshot_version_id = snapshot.id) THEN
        RAISE EXCEPTION 'TDCC snapshot % is already sealed', snapshot.id
            USING ERRCODE = '23505';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM tdcc_distribution WHERE snapshot_version_id = snapshot.id) THEN
        RAISE EXCEPTION 'TDCC snapshot % cannot be sealed without distribution rows', snapshot.id
            USING ERRCODE = '23514';
    END IF;

    SELECT jsonb_build_object(
        'snapshot_date', snapshot.snapshot_date,
        'distribution', (
            SELECT jsonb_agg(jsonb_build_object(
                'bucket_code', bucket_code,
                'holder_count', holder_count,
                'shares', shares,
                'ownership_percent', ownership_percent
            ) ORDER BY bucket_code)
            FROM tdcc_distribution WHERE snapshot_version_id = snapshot.id
        )
    ) INTO canonical_payload;

    computed_hash := encode(digest(convert_to(canonical_payload::text, 'UTF8'), 'sha256'), 'hex');
    UPDATE tdcc_snapshot_versions
       SET business_content_hash = computed_hash
     WHERE id = snapshot.id;
    NEW.business_content_hash := computed_hash;
    NEW.ingested_at := statement_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER prepare_security_metadata
BEFORE INSERT ON security_metadata_versions
FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_security_metadata();
CREATE TRIGGER immutable_security_metadata
BEFORE UPDATE OR DELETE ON security_metadata_versions
FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();

CREATE TRIGGER prepare_daily_price
BEFORE INSERT ON daily_price_versions
FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_daily_price();
CREATE TRIGGER immutable_daily_price
BEFORE UPDATE OR DELETE ON daily_price_versions
FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();

CREATE TRIGGER prepare_monthly_revenue
BEFORE INSERT ON monthly_revenue_versions
FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_monthly_revenue();
CREATE TRIGGER immutable_monthly_revenue
BEFORE UPDATE OR DELETE ON monthly_revenue_versions
FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();

CREATE TRIGGER prepare_financial_filing
BEFORE INSERT ON financial_filing_versions
FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_financial_filing();
CREATE TRIGGER protect_financial_parent
BEFORE UPDATE OR DELETE ON financial_filing_versions
FOR EACH ROW EXECUTE FUNCTION stockdc_protect_financial_parent();
CREATE TRIGGER prepare_financial_fact
BEFORE INSERT OR UPDATE ON financial_facts
FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_financial_fact();
CREATE TRIGGER protect_financial_fact
BEFORE INSERT OR UPDATE OR DELETE ON financial_facts
FOR EACH ROW EXECUTE FUNCTION stockdc_protect_financial_child();
CREATE TRIGGER protect_quarterly_summary
BEFORE INSERT OR UPDATE OR DELETE ON quarterly_financial_summary
FOR EACH ROW EXECUTE FUNCTION stockdc_protect_financial_child();
CREATE TRIGGER prepare_financial_seal
BEFORE INSERT ON financial_filing_seals
FOR EACH ROW EXECUTE FUNCTION stockdc_seal_financial_filing();
CREATE TRIGGER immutable_financial_seal
BEFORE UPDATE OR DELETE ON financial_filing_seals
FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();

CREATE TRIGGER prepare_tdcc_snapshot
BEFORE INSERT ON tdcc_snapshot_versions
FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_tdcc_snapshot();
CREATE TRIGGER protect_tdcc_parent
BEFORE UPDATE OR DELETE ON tdcc_snapshot_versions
FOR EACH ROW EXECUTE FUNCTION stockdc_protect_tdcc_parent();
CREATE TRIGGER protect_tdcc_distribution
BEFORE INSERT OR UPDATE OR DELETE ON tdcc_distribution
FOR EACH ROW EXECUTE FUNCTION stockdc_protect_tdcc_child();
CREATE TRIGGER prepare_tdcc_seal
BEFORE INSERT ON tdcc_snapshot_seals
FOR EACH ROW EXECUTE FUNCTION stockdc_seal_tdcc_snapshot();
CREATE TRIGGER immutable_tdcc_seal
BEFORE UPDATE OR DELETE ON tdcc_snapshot_seals
FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();

CREATE TRIGGER prepare_publication_evidence
BEFORE INSERT ON publication_evidence
FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_publication_evidence();
CREATE TRIGGER immutable_publication_evidence
BEFORE UPDATE OR DELETE ON publication_evidence
FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();

CREATE TRIGGER immutable_raw_artifact
BEFORE UPDATE OR DELETE ON raw_artifacts
FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER immutable_raw_observation
BEFORE UPDATE OR DELETE ON raw_artifact_observations
FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();

CREATE TRIGGER no_truncate_security_metadata
BEFORE TRUNCATE ON security_metadata_versions
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER no_truncate_daily_price
BEFORE TRUNCATE ON daily_price_versions
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER no_truncate_monthly_revenue
BEFORE TRUNCATE ON monthly_revenue_versions
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER no_truncate_financial_filing
BEFORE TRUNCATE ON financial_filing_versions
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER no_truncate_financial_facts
BEFORE TRUNCATE ON financial_facts
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER no_truncate_quarterly_summary
BEFORE TRUNCATE ON quarterly_financial_summary
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER no_truncate_financial_seals
BEFORE TRUNCATE ON financial_filing_seals
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER no_truncate_tdcc_snapshot
BEFORE TRUNCATE ON tdcc_snapshot_versions
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER no_truncate_tdcc_distribution
BEFORE TRUNCATE ON tdcc_distribution
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER no_truncate_tdcc_seals
BEFORE TRUNCATE ON tdcc_snapshot_seals
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER no_truncate_publication_evidence
BEFORE TRUNCATE ON publication_evidence
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER no_truncate_raw_artifacts
BEFORE TRUNCATE ON raw_artifacts
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
CREATE TRIGGER no_truncate_raw_observations
BEFORE TRUNCATE ON raw_artifact_observations
FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();

CREATE VIEW visible_financial_filings AS
SELECT filing.*, seal.ingested_at AS sealed_at
  FROM financial_filing_versions filing
  JOIN financial_filing_seals seal ON seal.filing_version_id = filing.id;

CREATE VIEW visible_tdcc_snapshots AS
SELECT snapshot.*, seal.ingested_at AS sealed_at
  FROM tdcc_snapshot_versions snapshot
  JOIN tdcc_snapshot_seals seal ON seal.snapshot_version_id = snapshot.id;
"""


DROP_OBJECTS = r"""
DROP VIEW IF EXISTS visible_tdcc_snapshots;
DROP VIEW IF EXISTS visible_financial_filings;

DROP TRIGGER IF EXISTS no_truncate_raw_observations ON raw_artifact_observations;
DROP TRIGGER IF EXISTS no_truncate_raw_artifacts ON raw_artifacts;
DROP TRIGGER IF EXISTS no_truncate_publication_evidence ON publication_evidence;
DROP TRIGGER IF EXISTS no_truncate_tdcc_seals ON tdcc_snapshot_seals;
DROP TRIGGER IF EXISTS no_truncate_tdcc_distribution ON tdcc_distribution;
DROP TRIGGER IF EXISTS no_truncate_tdcc_snapshot ON tdcc_snapshot_versions;
DROP TRIGGER IF EXISTS no_truncate_financial_seals ON financial_filing_seals;
DROP TRIGGER IF EXISTS no_truncate_quarterly_summary ON quarterly_financial_summary;
DROP TRIGGER IF EXISTS no_truncate_financial_facts ON financial_facts;
DROP TRIGGER IF EXISTS no_truncate_financial_filing ON financial_filing_versions;
DROP TRIGGER IF EXISTS no_truncate_monthly_revenue ON monthly_revenue_versions;
DROP TRIGGER IF EXISTS no_truncate_daily_price ON daily_price_versions;
DROP TRIGGER IF EXISTS no_truncate_security_metadata ON security_metadata_versions;
DROP TRIGGER IF EXISTS immutable_raw_observation ON raw_artifact_observations;
DROP TRIGGER IF EXISTS immutable_raw_artifact ON raw_artifacts;
DROP TRIGGER IF EXISTS immutable_publication_evidence ON publication_evidence;
DROP TRIGGER IF EXISTS prepare_publication_evidence ON publication_evidence;
DROP TRIGGER IF EXISTS immutable_tdcc_seal ON tdcc_snapshot_seals;
DROP TRIGGER IF EXISTS prepare_tdcc_seal ON tdcc_snapshot_seals;
DROP TRIGGER IF EXISTS protect_tdcc_distribution ON tdcc_distribution;
DROP TRIGGER IF EXISTS protect_tdcc_parent ON tdcc_snapshot_versions;
DROP TRIGGER IF EXISTS prepare_tdcc_snapshot ON tdcc_snapshot_versions;
DROP TRIGGER IF EXISTS immutable_financial_seal ON financial_filing_seals;
DROP TRIGGER IF EXISTS prepare_financial_seal ON financial_filing_seals;
DROP TRIGGER IF EXISTS protect_quarterly_summary ON quarterly_financial_summary;
DROP TRIGGER IF EXISTS protect_financial_fact ON financial_facts;
DROP TRIGGER IF EXISTS prepare_financial_fact ON financial_facts;
DROP TRIGGER IF EXISTS protect_financial_parent ON financial_filing_versions;
DROP TRIGGER IF EXISTS prepare_financial_filing ON financial_filing_versions;
DROP TRIGGER IF EXISTS immutable_monthly_revenue ON monthly_revenue_versions;
DROP TRIGGER IF EXISTS prepare_monthly_revenue ON monthly_revenue_versions;
DROP TRIGGER IF EXISTS immutable_daily_price ON daily_price_versions;
DROP TRIGGER IF EXISTS prepare_daily_price ON daily_price_versions;
DROP TRIGGER IF EXISTS immutable_security_metadata ON security_metadata_versions;
DROP TRIGGER IF EXISTS prepare_security_metadata ON security_metadata_versions;

DROP FUNCTION IF EXISTS stockdc_seal_tdcc_snapshot();
DROP FUNCTION IF EXISTS stockdc_protect_tdcc_child();
DROP FUNCTION IF EXISTS stockdc_protect_tdcc_parent();
DROP FUNCTION IF EXISTS stockdc_seal_financial_filing();
DROP FUNCTION IF EXISTS stockdc_protect_financial_child();
DROP FUNCTION IF EXISTS stockdc_protect_financial_parent();
DROP FUNCTION IF EXISTS stockdc_prepare_publication_evidence();
DROP FUNCTION IF EXISTS stockdc_prepare_financial_fact();
DROP FUNCTION IF EXISTS stockdc_prepare_tdcc_snapshot();
DROP FUNCTION IF EXISTS stockdc_prepare_financial_filing();
DROP FUNCTION IF EXISTS stockdc_prepare_monthly_revenue();
DROP FUNCTION IF EXISTS stockdc_prepare_daily_price();
DROP FUNCTION IF EXISTS stockdc_prepare_security_metadata();
DROP FUNCTION IF EXISTS stockdc_assert_lineage(uuid, uuid, text, text);
DROP FUNCTION IF EXISTS stockdc_reject_mutation();
"""


def upgrade() -> None:
    op.execute(FUNCTIONS_AND_TRIGGERS)


def downgrade() -> None:
    op.execute(DROP_OBJECTS)
