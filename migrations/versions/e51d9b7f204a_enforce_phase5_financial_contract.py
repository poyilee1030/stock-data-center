"""enforce phase 5 financial contract

Revision ID: e51d9b7f204a
Revises: c92e81a40d17
Create Date: 2026-09-12

Cache impact: none. Phase 5 has no cache and this migration does not change PIT
resolver semantics.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e51d9b7f204a"
down_revision: str | None = "c92e81a40d17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_financial_facts_value_present"),
        "financial_facts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_financial_facts_exactly_one_value"),
        "financial_facts",
        "num_nonnulls(numeric_value, text_value) = 1",
    )
    op.create_check_constraint(
        op.f("ck_financial_facts_canonical_qname"),
        "financial_facts",
        "concept_qname ~ '^\\{[^{}]+\\}[^{}]+$'",
    )
    op.create_table(
        "financial_filing_version_observations",
        sa.Column("filing_version_id", sa.BigInteger(), nullable=False),
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["filing_version_id"],
            ["financial_filing_versions.id"],
            name=op.f(
                "fk_financial_filing_version_observations_"
                "filing_version_id_financial_filing_versions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["raw_artifact_id", "ingest_run_id"],
            [
                "raw_artifact_observations.raw_artifact_id",
                "raw_artifact_observations.ingest_run_id",
            ],
            name=op.f(
                "fk_financial_filing_version_observations_"
                "raw_artifact_id_raw_artifact_observations"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "filing_version_id",
            "raw_artifact_id",
            "ingest_run_id",
            name=op.f("pk_financial_filing_version_observations"),
        ),
    )
    op.execute(
        """
        CREATE FUNCTION stockdc_validate_financial_filing_observation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE version_source text; run_dataset text; run_source text;
        BEGIN
            SELECT source INTO version_source
            FROM financial_filing_versions WHERE id = NEW.filing_version_id;
            SELECT dataset_code, source INTO run_dataset, run_source
            FROM ingest_runs WHERE id = NEW.ingest_run_id;
            IF version_source IS NULL OR run_dataset IS NULL THEN
                RAISE EXCEPTION 'filing observation target/run does not exist'
                    USING ERRCODE = '23503';
            END IF;
            IF run_dataset <> 'financial_filing' OR run_source <> version_source THEN
                RAISE EXCEPTION 'filing observation dataset/source mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER validate_financial_filing_observation
        BEFORE INSERT ON financial_filing_version_observations
        FOR EACH ROW EXECUTE FUNCTION
            stockdc_validate_financial_filing_observation();
        CREATE TRIGGER immutable_financial_filing_observations
        BEFORE UPDATE OR DELETE ON financial_filing_version_observations
        FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER no_truncate_financial_filing_observations
        BEFORE TRUNCATE ON financial_filing_version_observations
        FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();

        INSERT INTO financial_filing_version_observations (
            filing_version_id, raw_artifact_id, ingest_run_id
        )
        SELECT id, raw_artifact_id, ingest_run_id
        FROM financial_filing_versions;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS no_truncate_financial_filing_observations
            ON financial_filing_version_observations;
        DROP TRIGGER IF EXISTS immutable_financial_filing_observations
            ON financial_filing_version_observations;
        DROP TRIGGER IF EXISTS validate_financial_filing_observation
            ON financial_filing_version_observations;
        DROP FUNCTION IF EXISTS stockdc_validate_financial_filing_observation();
        """
    )
    op.drop_table("financial_filing_version_observations")
    op.drop_constraint(
        op.f("ck_financial_facts_canonical_qname"),
        "financial_facts",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_financial_facts_exactly_one_value"),
        "financial_facts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_financial_facts_value_present"),
        "financial_facts",
        "numeric_value IS NOT NULL OR text_value IS NOT NULL",
    )
