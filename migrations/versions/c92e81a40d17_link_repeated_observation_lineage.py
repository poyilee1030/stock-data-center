"""link repeated observation lineage

Revision ID: c92e81a40d17
Revises: f3a74c12e690
Create Date: 2026-09-12

Cache impact: none. Phase 4 has no cache; this migration adds provenance links
without changing PIT visibility or resolver semantics.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c92e81a40d17"
down_revision: str | None = "f3a74c12e690"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monthly_revenue_version_observations",
        sa.Column("monthly_revenue_version_id", sa.BigInteger(), nullable=False),
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["monthly_revenue_version_id"],
            ["monthly_revenue_versions.id"],
            name=op.f(
                "fk_monthly_revenue_version_observations_"
                "monthly_revenue_version_id_monthly_revenue_versions"
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
                "fk_monthly_revenue_version_observations_"
                "raw_artifact_id_raw_artifact_observations"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "monthly_revenue_version_id",
            "raw_artifact_id",
            "ingest_run_id",
            name=op.f("pk_monthly_revenue_version_observations"),
        ),
    )
    op.create_table(
        "publication_evidence_observations",
        sa.Column("publication_evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["publication_evidence_id"],
            ["publication_evidence.id"],
            name=op.f(
                "fk_publication_evidence_observations_"
                "publication_evidence_id_publication_evidence"
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
                "fk_publication_evidence_observations_"
                "raw_artifact_id_raw_artifact_observations"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "publication_evidence_id",
            "raw_artifact_id",
            "ingest_run_id",
            name=op.f("pk_publication_evidence_observations"),
        ),
    )
    op.execute(
        """
        CREATE FUNCTION stockdc_validate_monthly_revenue_observation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE version_source text; run_dataset text; run_source text;
        BEGIN
            SELECT source INTO version_source
            FROM monthly_revenue_versions
            WHERE id = NEW.monthly_revenue_version_id;

            SELECT dataset_code, source INTO run_dataset, run_source
            FROM ingest_runs
            WHERE id = NEW.ingest_run_id;

            IF version_source IS NULL OR run_dataset IS NULL THEN
                RAISE EXCEPTION 'revenue observation target/run does not exist'
                    USING ERRCODE = '23503';
            END IF;
            IF run_dataset <> 'monthly_revenue' OR run_source <> version_source THEN
                RAISE EXCEPTION 'revenue observation dataset/source mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION stockdc_validate_evidence_observation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE evidence_dataset text; evidence_source_value text;
                run_dataset text; run_source text;
        BEGIN
            SELECT dataset_code, source INTO evidence_dataset, evidence_source_value
            FROM publication_evidence
            WHERE id = NEW.publication_evidence_id;

            SELECT dataset_code, source INTO run_dataset, run_source
            FROM ingest_runs
            WHERE id = NEW.ingest_run_id;

            IF evidence_dataset IS NULL OR run_dataset IS NULL THEN
                RAISE EXCEPTION 'evidence observation target/run does not exist'
                    USING ERRCODE = '23503';
            END IF;
            IF run_dataset <> evidence_dataset
               OR run_source <> evidence_source_value THEN
                RAISE EXCEPTION 'evidence observation dataset/source mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER validate_monthly_revenue_observation
        BEFORE INSERT ON monthly_revenue_version_observations
        FOR EACH ROW EXECUTE FUNCTION
            stockdc_validate_monthly_revenue_observation();

        CREATE TRIGGER immutable_monthly_revenue_observations
        BEFORE UPDATE OR DELETE ON monthly_revenue_version_observations
        FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();

        CREATE TRIGGER no_truncate_monthly_revenue_observations
        BEFORE TRUNCATE ON monthly_revenue_version_observations
        FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();

        CREATE TRIGGER validate_publication_evidence_observation
        BEFORE INSERT ON publication_evidence_observations
        FOR EACH ROW EXECUTE FUNCTION stockdc_validate_evidence_observation();

        CREATE TRIGGER immutable_publication_evidence_observations
        BEFORE UPDATE OR DELETE ON publication_evidence_observations
        FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();

        CREATE TRIGGER no_truncate_publication_evidence_observations
        BEFORE TRUNCATE ON publication_evidence_observations
        FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
        """
    )
    op.execute(
        """
        INSERT INTO monthly_revenue_version_observations (
            monthly_revenue_version_id, raw_artifact_id, ingest_run_id
        )
        SELECT id, raw_artifact_id, ingest_run_id
        FROM monthly_revenue_versions;

        INSERT INTO publication_evidence_observations (
            publication_evidence_id, raw_artifact_id, ingest_run_id
        )
        SELECT id, raw_artifact_id, ingest_run_id
        FROM publication_evidence;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS no_truncate_publication_evidence_observations
            ON publication_evidence_observations;
        DROP TRIGGER IF EXISTS immutable_publication_evidence_observations
            ON publication_evidence_observations;
        DROP TRIGGER IF EXISTS validate_publication_evidence_observation
            ON publication_evidence_observations;
        DROP TRIGGER IF EXISTS no_truncate_monthly_revenue_observations
            ON monthly_revenue_version_observations;
        DROP TRIGGER IF EXISTS immutable_monthly_revenue_observations
            ON monthly_revenue_version_observations;
        DROP TRIGGER IF EXISTS validate_monthly_revenue_observation
            ON monthly_revenue_version_observations;
        DROP FUNCTION IF EXISTS stockdc_validate_evidence_observation();
        DROP FUNCTION IF EXISTS stockdc_validate_monthly_revenue_observation();
        """
    )
    op.drop_table("publication_evidence_observations")
    op.drop_table("monthly_revenue_version_observations")
