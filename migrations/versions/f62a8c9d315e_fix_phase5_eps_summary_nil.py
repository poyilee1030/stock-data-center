"""fix phase 5 EPS basis, summary lineage, and XBRL nil semantics

Revision ID: f62a8c9d315e
Revises: e51d9b7f204a
Create Date: 2026-09-12

Cache impact: none. Phase 5 has no cache implementation. This migration makes
previously ambiguous summary semantics explicit and does not backdate PIT data.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f62a8c9d315e"
down_revision: str | None = "e51d9b7f204a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "financial_facts",
        sa.Column(
            "is_nil", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.drop_constraint(
        op.f("ck_financial_facts_exactly_one_value"),
        "financial_facts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_financial_facts_nil_value_shape"),
        "financial_facts",
        "(is_nil AND numeric_value IS NULL AND text_value IS NULL) OR "
        "(NOT is_nil AND num_nonnulls(numeric_value, text_value) = 1)",
    )
    op.create_unique_constraint(
        "uq_financial_fact_filing_identity",
        "financial_facts",
        ["id", "filing_version_id"],
    )

    op.add_column(
        "quarterly_financial_summary",
        sa.Column("source_fact_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "quarterly_financial_summary",
        sa.Column("period_basis", sa.String(length=16), nullable=True),
    )
    op.execute(
        """
        UPDATE quarterly_financial_summary summary
        SET source_fact_id = (
            SELECT min(candidate.id)
            FROM financial_facts candidate
            WHERE candidate.filing_version_id = summary.filing_version_id
              AND candidate.is_nil = false
              AND candidate.numeric_value = summary.value
              AND candidate.unit_identity = summary.unit_identity
            HAVING count(*) = 1
        );

        UPDATE quarterly_financial_summary summary
        SET period_basis = CASE
                WHEN fact.period_type = 'instant' THEN 'instant'
                WHEN summary.metric_code LIKE '%\\_q' ESCAPE '\\'
                    THEN 'quarter'
                WHEN summary.metric_code LIKE '%\\_annual' ESCAPE '\\'
                    THEN 'annual'
                WHEN summary.metric_code LIKE '%\\_ytd' ESCAPE '\\'
                    THEN 'ytd'
                WHEN summary.metric_code LIKE '%\\_acc%' ESCAPE '\\'
                 AND filing.report_quarter = 4 THEN 'annual'
                WHEN summary.metric_code LIKE '%\\_acc%' ESCAPE '\\'
                    THEN 'ytd'
                ELSE NULL
            END
        FROM financial_filing_versions filing, financial_facts fact
        WHERE filing.id = summary.filing_version_id
          AND fact.id = summary.source_fact_id;

        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM quarterly_financial_summary
                WHERE source_fact_id IS NULL OR period_basis IS NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot migrate summary without an exact same-filing source fact'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$;
        """
    )
    op.alter_column(
        "quarterly_financial_summary", "source_fact_id", nullable=False
    )
    op.alter_column(
        "quarterly_financial_summary", "period_basis", nullable=False
    )
    op.drop_constraint(
        "uq_quarterly_summary_metric",
        "quarterly_financial_summary",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_quarterly_summary_metric_basis",
        "quarterly_financial_summary",
        ["filing_version_id", "metric_code", "period_basis"],
    )
    op.create_check_constraint(
        op.f("ck_quarterly_financial_summary_period_basis_value"),
        "quarterly_financial_summary",
        "period_basis IN ('quarter', 'ytd', 'annual', 'instant')",
    )
    op.create_foreign_key(
        op.f(
            "fk_quarterly_financial_summary_"
            "source_fact_id_financial_facts"
        ),
        "quarterly_financial_summary",
        "financial_facts",
        ["source_fact_id", "filing_version_id"],
        ["id", "filing_version_id"],
        ondelete="RESTRICT",
    )
    op.execute(_SUMMARY_VALIDATION_SQL)
    op.execute(_SEAL_FUNCTION_SQL)


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS validate_quarterly_financial_summary
            ON quarterly_financial_summary;
        DROP FUNCTION IF EXISTS stockdc_validate_quarterly_financial_summary();
        """
    )
    op.drop_constraint(
        op.f(
            "fk_quarterly_financial_summary_"
            "source_fact_id_financial_facts"
        ),
        "quarterly_financial_summary",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_quarterly_financial_summary_period_basis_value"),
        "quarterly_financial_summary",
        type_="check",
    )
    op.drop_constraint(
        "uq_quarterly_summary_metric_basis",
        "quarterly_financial_summary",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_quarterly_summary_metric",
        "quarterly_financial_summary",
        ["filing_version_id", "metric_code"],
    )
    op.drop_column("quarterly_financial_summary", "period_basis")
    op.drop_column("quarterly_financial_summary", "source_fact_id")
    op.drop_constraint(
        "uq_financial_fact_filing_identity",
        "financial_facts",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_financial_facts_nil_value_shape"),
        "financial_facts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_financial_facts_exactly_one_value"),
        "financial_facts",
        "num_nonnulls(numeric_value, text_value) = 1",
    )
    op.drop_column("financial_facts", "is_nil")
    op.execute(_LEGACY_SEAL_FUNCTION_SQL)


_SUMMARY_VALIDATION_SQL = """
CREATE FUNCTION stockdc_validate_quarterly_financial_summary()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    fact financial_facts%ROWTYPE;
    filing financial_filing_versions%ROWTYPE;
BEGIN
    SELECT * INTO fact FROM financial_facts WHERE id = NEW.source_fact_id;
    IF NOT FOUND OR fact.filing_version_id <> NEW.filing_version_id THEN
        RAISE EXCEPTION 'summary source fact must belong to the same filing'
            USING ERRCODE = '23514';
    END IF;
    IF fact.is_nil OR fact.numeric_value IS NULL
       OR fact.numeric_value IS DISTINCT FROM NEW.value
       OR fact.unit_identity IS DISTINCT FROM NEW.unit_identity THEN
        RAISE EXCEPTION 'summary value/unit must equal its non-nil numeric source fact'
            USING ERRCODE = '23514';
    END IF;
    SELECT * INTO filing FROM financial_filing_versions
     WHERE id = NEW.filing_version_id;
    IF NEW.period_basis = 'instant' THEN
        IF fact.period_type <> 'instant'
           OR fact.instant_date <> filing.period_end THEN
            RAISE EXCEPTION 'instant metric must use the filing-end instant fact'
                USING ERRCODE = '23514';
        END IF;
    ELSIF fact.period_type <> 'duration'
       OR fact.period_end <> filing.period_end THEN
        RAISE EXCEPTION 'duration metric must end at the filing period end'
            USING ERRCODE = '23514';
    ELSIF NEW.period_basis = 'quarter'
       AND fact.period_end - fact.period_start > 100 THEN
        RAISE EXCEPTION 'quarter metric cannot use a YTD/annual duration fact'
            USING ERRCODE = '23514';
    ELSIF NEW.period_basis = 'ytd'
       AND fact.period_start <> filing.period_start THEN
        RAISE EXCEPTION 'YTD metric must start at the filing period start'
            USING ERRCODE = '23514';
    ELSIF NEW.period_basis = 'annual'
       AND (filing.report_quarter <> 4
            OR fact.period_start <> filing.period_start
            OR fact.period_end - fact.period_start < 300) THEN
        RAISE EXCEPTION 'annual metric requires a full-year Q4 duration fact'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER validate_quarterly_financial_summary
BEFORE INSERT OR UPDATE ON quarterly_financial_summary
FOR EACH ROW EXECUTE FUNCTION stockdc_validate_quarterly_financial_summary();
"""


_SEAL_FUNCTION_SQL = """
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


_LEGACY_SEAL_FUNCTION_SQL = _SEAL_FUNCTION_SQL.replace(
    "                'is_nil', is_nil,\n", ""
).replace(
    "                'period_basis', summary.period_basis,\n"
    "                'source_concept_qname', fact.concept_qname,\n"
    "                'source_context_hash', fact.context_hash,\n"
    "                'source_unit_identity', fact.unit_identity,\n",
    "",
).replace(
    "            ) ORDER BY summary.metric_code, summary.period_basis)\n"
    "            FROM quarterly_financial_summary summary\n"
    "            JOIN financial_facts fact ON fact.id = summary.source_fact_id\n"
    "            WHERE summary.filing_version_id = filing.id",
    "            ) ORDER BY summary.metric_code)\n"
    "            FROM quarterly_financial_summary summary\n"
    "            WHERE summary.filing_version_id = filing.id",
)
_legacy_validation_start = _LEGACY_SEAL_FUNCTION_SQL.index(
    "    IF EXISTS (\n"
    "        SELECT 1\n"
    "        FROM quarterly_financial_summary summary"
)
_legacy_validation_end = _LEGACY_SEAL_FUNCTION_SQL.index(
    "\n\n    SELECT jsonb_build_object(", _legacy_validation_start
)
_LEGACY_SEAL_FUNCTION_SQL = (
    _LEGACY_SEAL_FUNCTION_SQL[:_legacy_validation_start]
    + _LEGACY_SEAL_FUNCTION_SQL[_legacy_validation_end:]
)
