"""financial statements: the three statement rows and the MOPS source

Revision ID: a3f5d9c17e48
Revises: c8f1a63d5b02
Create Date: 2026-09-21

Step 23-b. `financial_facts` gains the two things a MOPS statement row is
printed with: the statement whose table it stood in, and the 會計科目代碼 in
that row's first cell. `financial_filing_versions` gains 合併 or 個體.

Why identity needed the statement. The Step 5 identity was filing + QName +
context + unit, and a real document breaks it: `ifrs-full:CashAndCashEquivalents`
is the balance sheet's 1100 and the cash-flow statement's E00210 — same
instant, same unit, same number. Over the whole archive that is 171,000
collisions, four in every one of the 42,750 documents in the v1 universe (scan
of 2026-09-21, audit §4.8). Adding the statement resolves every one of them:
with it, the 16,180,359 statement facts in those documents hold no duplicate
identity at all. The 會計科目代碼 is stored beside it as business content — it
is the row identity legacy `*_xbrl` keyed on, and Step 23-c reconciles
code <-> QName against it — but it is not part of identity, because the QName
already separates the two `ProfitLossBeforeTax` rows that share a statement, a
context and a unit (A00010 is `ifrs-full`, A10000 is `tifrs-scf`).

The unique constraint is `NULLS NOT DISTINCT`, so a fact written without a
statement — every fact Phase 5 wrote, and anything outside the three
statements — dedups exactly as it did before.

Both enter the sealed `business_content_hash`, and the payload's ORDER BY gains
the statement so the ordering is total and the hash deterministic (CLAUDE.md
§24). `report_category` enters it too: 合併 and 個體 are different filings.

One source, `mops_t164sb01`, for both the official fetch and the legacy
archive: they are the same endpoint's document, so they are one history
(CLAUDE.md §30) and the archive copy dedups against the official one instead of
becoming a second revision. It accepts only `official` evidence and follows no
release rule; the publication evidence is Step 23-c, so every version here is
System-PIT visible only, which is late and never early.

Downgrade refuses, before changing anything, once a statement fact, a report
category or anything belonging to this source exists: dropping the statement
would merge two distinct rows into one and discard history (CLAUDE.md §81).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f5d9c17e48"
down_revision: str | None = "c8f1a63d5b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IDENTITY = ["filing_version_id", "concept_qname", "context_hash", "unit_identity"]

GUARD = """
DO $$
DECLARE blocking bigint;
BEGIN
    SELECT (SELECT count(*) FROM financial_facts
             WHERE statement IS NOT NULL OR account_code IS NOT NULL)
         + (SELECT count(*) FROM financial_filing_versions
             WHERE report_category IS NOT NULL OR source = 'mops_t164sb01')
         + (SELECT count(*) FROM ingest_runs
             WHERE dataset_code = 'financial_filing' AND source = 'mops_t164sb01')
         + (SELECT count(*) FROM import_manifests
             WHERE dataset_code = 'financial_filing' AND source = 'mops_t164sb01')
      INTO blocking;
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away stored financial statement rows or the MOPS source',
            DETAIL = format('%s facts, filings, ingest runs or manifests depend on them', blocking),
            HINT = 'history is append-only; remove the history deliberately first';
    END IF;
END $$;
"""

_SEAL_WITH_STATEMENT = """
CREATE OR REPLACE FUNCTION stockdc_seal_financial_filing() RETURNS trigger
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
    IF EXISTS (
        SELECT 1
        FROM quarterly_financial_summary summary
        JOIN financial_facts fact
          ON fact.id = summary.source_fact_id
         AND fact.filing_version_id = summary.filing_version_id
        WHERE summary.filing_version_id = filing.id
          AND (
              fact.is_nil
              OR fact.numeric_value IS NULL
              OR fact.numeric_value IS DISTINCT FROM summary.value
              OR fact.unit_identity IS DISTINCT FROM summary.unit_identity
              OR (
                  summary.period_basis = 'instant'
                  AND (
                      fact.period_type <> 'instant'
                      OR fact.instant_date IS DISTINCT FROM filing.period_end
                  )
              )
              OR (
                  summary.period_basis <> 'instant'
                  AND (
                      fact.period_type <> 'duration'
                      OR fact.period_end IS DISTINCT FROM filing.period_end
                  )
              )
              OR (
                  summary.period_basis = 'quarter'
                  AND fact.period_end - fact.period_start > 100
              )
              OR (
                  summary.period_basis = 'ytd'
                  AND fact.period_start IS DISTINCT FROM filing.period_start
              )
              OR (
                  summary.period_basis = 'annual'
                  AND (
                      filing.report_quarter <> 4
                      OR fact.period_start IS DISTINCT FROM filing.period_start
                      OR fact.period_end - fact.period_start < 300
                  )
              )
          )
    ) THEN
        RAISE EXCEPTION 'financial summary/source fact contract is invalid at seal'
            USING ERRCODE = '23514';
    END IF;

    SELECT jsonb_build_object(
        'report_year', filing.report_year,
        'report_quarter', filing.report_quarter,
        'period_start', filing.period_start,
        'period_end', filing.period_end,
        'currency', filing.currency,
        'report_category', filing.report_category,
        'facts', (
            SELECT jsonb_agg(jsonb_build_object(
                'concept_qname', concept_qname,
                'context_hash', context_hash,
                'unit_identity', unit_identity,
                'statement', statement,
                'account_code', account_code,
                'numeric_value', numeric_value,
                'text_value', text_value,
                'is_nil', is_nil,
                'decimals', decimals
            ) ORDER BY concept_qname, context_hash, unit_identity, statement)
            FROM financial_facts WHERE filing_version_id = filing.id
        ),
        'summary', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'metric_code', summary.metric_code,
                'period_basis', summary.period_basis,
                'source_concept_qname', fact.concept_qname,
                'source_context_hash', fact.context_hash,
                'source_unit_identity', fact.unit_identity,
                'value', summary.value,
                'unit_identity', summary.unit_identity
            ) ORDER BY summary.metric_code, summary.period_basis)
            FROM quarterly_financial_summary summary
            JOIN financial_facts fact ON fact.id = summary.source_fact_id
            WHERE summary.filing_version_id = filing.id
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
"""

_SEAL_WITHOUT_STATEMENT = """
CREATE OR REPLACE FUNCTION stockdc_seal_financial_filing() RETURNS trigger
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
    IF EXISTS (
        SELECT 1
        FROM quarterly_financial_summary summary
        JOIN financial_facts fact
          ON fact.id = summary.source_fact_id
         AND fact.filing_version_id = summary.filing_version_id
        WHERE summary.filing_version_id = filing.id
          AND (
              fact.is_nil
              OR fact.numeric_value IS NULL
              OR fact.numeric_value IS DISTINCT FROM summary.value
              OR fact.unit_identity IS DISTINCT FROM summary.unit_identity
              OR (
                  summary.period_basis = 'instant'
                  AND (
                      fact.period_type <> 'instant'
                      OR fact.instant_date IS DISTINCT FROM filing.period_end
                  )
              )
              OR (
                  summary.period_basis <> 'instant'
                  AND (
                      fact.period_type <> 'duration'
                      OR fact.period_end IS DISTINCT FROM filing.period_end
                  )
              )
              OR (
                  summary.period_basis = 'quarter'
                  AND fact.period_end - fact.period_start > 100
              )
              OR (
                  summary.period_basis = 'ytd'
                  AND fact.period_start IS DISTINCT FROM filing.period_start
              )
              OR (
                  summary.period_basis = 'annual'
                  AND (
                      filing.report_quarter <> 4
                      OR fact.period_start IS DISTINCT FROM filing.period_start
                      OR fact.period_end - fact.period_start < 300
                  )
              )
          )
    ) THEN
        RAISE EXCEPTION 'financial summary/source fact contract is invalid at seal'
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
                'is_nil', is_nil,
                'decimals', decimals
            ) ORDER BY concept_qname, context_hash, unit_identity)
            FROM financial_facts WHERE filing_version_id = filing.id
        ),
        'summary', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'metric_code', summary.metric_code,
                'period_basis', summary.period_basis,
                'source_concept_qname', fact.concept_qname,
                'source_context_hash', fact.context_hash,
                'source_unit_identity', fact.unit_identity,
                'value', summary.value,
                'unit_identity', summary.unit_identity
            ) ORDER BY summary.metric_code, summary.period_basis)
            FROM quarterly_financial_summary summary
            JOIN financial_facts fact ON fact.id = summary.source_fact_id
            WHERE summary.filing_version_id = filing.id
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
"""


def upgrade() -> None:
    op.add_column(
        "financial_filing_versions", sa.Column("report_category", sa.String(16))
    )
    op.create_check_constraint(
        "report_category_value",
        "financial_filing_versions",
        "report_category IS NULL OR report_category IN "
        "('consolidated', 'individual')",
    )
    op.add_column("financial_facts", sa.Column("statement", sa.String(24)))
    op.add_column("financial_facts", sa.Column("account_code", sa.String(16)))
    op.create_check_constraint(
        "statement_value",
        "financial_facts",
        "statement IS NULL OR statement IN "
        "('balance_sheet', 'income_statement', 'cash_flow')",
    )
    op.drop_constraint("uq_financial_fact_identity", "financial_facts", type_="unique")
    op.create_unique_constraint(
        "uq_financial_fact_identity",
        "financial_facts",
        [*IDENTITY, "statement"],
        postgresql_nulls_not_distinct=True,
    )
    op.execute(_SEAL_WITH_STATEMENT)
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('financial_filing', 'iXBRL financial statement filings', 'v1')
            ON CONFLICT (dataset_code) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_sources
                (dataset_code, source, supports_market_pit, supports_system_pit,
                 publication_time_quality, evidence_status,
                 accepted_evidence_types, is_canonical)
            VALUES ('financial_filing', 'mops_t164sb01', true, true, 0, 'verified',
                    ARRAY['official']::varchar[], false)
            ON CONFLICT (dataset_code, source) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    op.execute(
        sa.text(
            "DELETE FROM dataset_sources WHERE dataset_code = 'financial_filing' "
            "AND source = 'mops_t164sb01'"
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM dataset_catalog
             WHERE dataset_code = 'financial_filing'
               AND NOT EXISTS (
                     SELECT 1 FROM dataset_sources
                      WHERE dataset_code = 'financial_filing'
                   )
            """
        )
    )
    op.execute(_SEAL_WITHOUT_STATEMENT)
    op.drop_constraint("uq_financial_fact_identity", "financial_facts", type_="unique")
    op.create_unique_constraint(
        "uq_financial_fact_identity", "financial_facts", IDENTITY
    )
    op.drop_constraint(
        "statement_value", "financial_facts", type_="check"
    )
    op.drop_column("financial_facts", "account_code")
    op.drop_column("financial_facts", "statement")
    op.drop_constraint(
        "report_category_value",
        "financial_filing_versions",
        type_="check",
    )
    op.drop_column("financial_filing_versions", "report_category")
