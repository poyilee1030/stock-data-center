"""implement phase 6 TDCC snapshot contract

Revision ID: a4c7e2d91b36
Revises: f62a8c9d315e
Create Date: 2026-09-12

Adds many-observation lineage for TDCC snapshot versions (backfilled from the
existing parent lineage), exposes the seal's canonical aggregate hash as a
reusable DB function ordered byte-wise (COLLATE "C") so it no longer depends on
the database default collation, and rejects TDCC publication evidence whose
published_at precedes the snapshot date in Asia/Taipei.

Cache impact: none. No cache exists and no TDCC writer existed before this
phase. The hash payload is unchanged; only its bucket ordering is pinned to
byte order, which is identical to the previous order for the numeric TDCC level
codes. No PIT timestamp is backdated.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a4c7e2d91b36"
down_revision: str | None = "f62a8c9d315e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEAL_FUNCTION_TEMPLATE = r"""
CREATE OR REPLACE FUNCTION stockdc_seal_tdcc_snapshot() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    snapshot tdcc_snapshot_versions%%ROWTYPE;
    canonical_payload jsonb;
    computed_hash text;
BEGIN
    SELECT * INTO snapshot FROM tdcc_snapshot_versions
     WHERE id = NEW.snapshot_version_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TDCC snapshot %% does not exist', NEW.snapshot_version_id
            USING ERRCODE = '23503';
    END IF;
    IF EXISTS (SELECT 1 FROM tdcc_snapshot_seals WHERE snapshot_version_id = snapshot.id) THEN
        RAISE EXCEPTION 'TDCC snapshot %% is already sealed', snapshot.id
            USING ERRCODE = '23505';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM tdcc_distribution WHERE snapshot_version_id = snapshot.id) THEN
        RAISE EXCEPTION 'TDCC snapshot %% cannot be sealed without distribution rows', snapshot.id
            USING ERRCODE = '23514';
    END IF;

%(hash_body)s

    UPDATE tdcc_snapshot_versions
       SET business_content_hash = computed_hash
     WHERE id = snapshot.id;
    NEW.business_content_hash := computed_hash;
    NEW.ingested_at := statement_timestamp();
    RETURN NEW;
END;
$$;
"""

PHASE6_HASH_BODY = """\
    -- Evaluated after the parent lock, so it sees every committed child.
    computed_hash := stockdc_tdcc_snapshot_business_hash(snapshot.id);"""

PHASE1_HASH_BODY = """\
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

    computed_hash := encode(digest(convert_to(canonical_payload::text, 'UTF8'), 'sha256'), 'hex');"""


def upgrade() -> None:
    op.create_table(
        "tdcc_snapshot_version_observations",
        sa.Column("snapshot_version_id", sa.BigInteger(), nullable=False),
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_version_id"],
            ["tdcc_snapshot_versions.id"],
            name=op.f(
                "fk_tdcc_snapshot_version_observations_"
                "snapshot_version_id_tdcc_snapshot_versions"
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
                "fk_tdcc_snapshot_version_observations_"
                "raw_artifact_id_raw_artifact_observations"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_version_id",
            "raw_artifact_id",
            "ingest_run_id",
            name=op.f("pk_tdcc_snapshot_version_observations"),
        ),
    )
    op.execute(
        """
        CREATE FUNCTION stockdc_validate_tdcc_snapshot_observation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE version_source text; run_dataset text; run_source text;
        BEGIN
            SELECT source INTO version_source
            FROM tdcc_snapshot_versions WHERE id = NEW.snapshot_version_id;
            SELECT dataset_code, source INTO run_dataset, run_source
            FROM ingest_runs WHERE id = NEW.ingest_run_id;
            IF version_source IS NULL OR run_dataset IS NULL THEN
                RAISE EXCEPTION 'TDCC observation target/run does not exist'
                    USING ERRCODE = '23503';
            END IF;
            IF run_dataset <> 'tdcc_snapshot' OR run_source <> version_source THEN
                RAISE EXCEPTION 'TDCC observation dataset/source mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER validate_tdcc_snapshot_observation
        BEFORE INSERT ON tdcc_snapshot_version_observations
        FOR EACH ROW EXECUTE FUNCTION stockdc_validate_tdcc_snapshot_observation();
        CREATE TRIGGER immutable_tdcc_snapshot_observations
        BEFORE UPDATE OR DELETE ON tdcc_snapshot_version_observations
        FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER no_truncate_tdcc_snapshot_observations
        BEFORE TRUNCATE ON tdcc_snapshot_version_observations
        FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();

        INSERT INTO tdcc_snapshot_version_observations (
            snapshot_version_id, raw_artifact_id, ingest_run_id
        )
        SELECT id, raw_artifact_id, ingest_run_id
        FROM tdcc_snapshot_versions;

        -- Volatile on purpose: each call takes a fresh snapshot, so the seal
        -- trigger hashes children committed while it waited for the lock.
        CREATE FUNCTION stockdc_tdcc_snapshot_business_hash(p_snapshot_version_id bigint)
        RETURNS text LANGUAGE sql VOLATILE AS $$
            SELECT encode(digest(convert_to(jsonb_build_object(
                'snapshot_date', snapshot.snapshot_date,
                'distribution', (
                    SELECT jsonb_agg(jsonb_build_object(
                        'bucket_code', bucket_code,
                        'holder_count', holder_count,
                        'shares', shares,
                        'ownership_percent', ownership_percent
                    ) ORDER BY bucket_code COLLATE "C")
                    FROM tdcc_distribution
                    WHERE snapshot_version_id = snapshot.id
                )
            )::text, 'UTF8'), 'sha256'), 'hex')
            FROM tdcc_snapshot_versions snapshot
            WHERE snapshot.id = p_snapshot_version_id
        $$;
        """
    )
    op.execute(SEAL_FUNCTION_TEMPLATE % {"hash_body": PHASE6_HASH_BODY})
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM publication_evidence evidence
                JOIN tdcc_snapshot_versions snapshot
                  ON snapshot.id = evidence.tdcc_snapshot_version_id
                WHERE evidence.published_at
                      < (snapshot.snapshot_date::timestamp AT TIME ZONE 'Asia/Taipei')
            ) THEN
                RAISE EXCEPTION 'existing TDCC evidence precedes its snapshot date';
            END IF;
        END;
        $$;

        CREATE FUNCTION stockdc_validate_tdcc_publication_time()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE snapshot_on date;
        BEGIN
            IF NEW.published_at IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT snapshot_date INTO snapshot_on
            FROM tdcc_snapshot_versions WHERE id = NEW.tdcc_snapshot_version_id;
            IF NEW.published_at
               < (snapshot_on::timestamp AT TIME ZONE 'Asia/Taipei') THEN
                RAISE EXCEPTION
                    'TDCC publication % precedes snapshot date % (Asia/Taipei)',
                    NEW.published_at, snapshot_on
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        -- Fires after prepare_publication_evidence (trigger names sort), which
        -- has already verified that the target snapshot exists.
        CREATE TRIGGER validate_tdcc_publication_time
        BEFORE INSERT ON publication_evidence
        FOR EACH ROW WHEN (NEW.tdcc_snapshot_version_id IS NOT NULL)
        EXECUTE FUNCTION stockdc_validate_tdcc_publication_time();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS validate_tdcc_publication_time ON publication_evidence;
        DROP FUNCTION IF EXISTS stockdc_validate_tdcc_publication_time();
        """
    )
    op.execute(SEAL_FUNCTION_TEMPLATE % {"hash_body": PHASE1_HASH_BODY})
    op.execute(
        """
        DROP FUNCTION IF EXISTS stockdc_tdcc_snapshot_business_hash(bigint);
        DROP TRIGGER IF EXISTS no_truncate_tdcc_snapshot_observations
            ON tdcc_snapshot_version_observations;
        DROP TRIGGER IF EXISTS immutable_tdcc_snapshot_observations
            ON tdcc_snapshot_version_observations;
        DROP TRIGGER IF EXISTS validate_tdcc_snapshot_observation
            ON tdcc_snapshot_version_observations;
        DROP FUNCTION IF EXISTS stockdc_validate_tdcc_snapshot_observation();
        """
    )
    op.drop_table("tdcc_snapshot_version_observations")
