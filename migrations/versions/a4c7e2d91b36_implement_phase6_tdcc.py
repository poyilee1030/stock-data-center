"""implement phase 6 TDCC snapshot contract

Revision ID: a4c7e2d91b36
Revises: f62a8c9d315e
Create Date: 2026-09-12

Adds many-observation lineage for TDCC snapshot versions (backfilled from the
existing parent lineage) and versioned distribution-schema profiles. Every
snapshot declares a profile; each distribution row must be a bucket of that
profile and obey its role (holding, signed adjustment, total), and a seal is
accepted only when every bucket of the profile is present. A profile's bucket
definitions freeze once any snapshot references it. Adjustment rows
store holder_count as NULL (source blank and source 0 are canonicalized to
NULL). The canonical aggregate hash becomes a reusable DB function that covers
the profile and orders buckets byte-wise (COLLATE "C"). TDCC publication
evidence whose published_at precedes the snapshot date in Asia/Taipei is
rejected.

Existing rows are assigned the `tdcc-opendata-v1` profile. The migration aborts
instead of guessing when an existing row does not fit that profile or a sealed
snapshot is incomplete. Sealed snapshots are then rehashed under the new
payload by this trusted migration.

Cache impact: none. No cache exists and no TDCC writer existed before this
phase. Rehashing changes aggregate business identity only; no PIT timestamp
(published_at, recorded_at, ingested_at) is changed or backdated.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a4c7e2d91b36"
down_revision: str | None = "f62a8c9d315e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OFFICIAL_PROFILE = "tdcc-opendata-v1"

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
%(completeness)s
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

PHASE6_COMPLETENESS = """
    -- Row triggers already reject buckets outside the profile, so a missing
    -- profile bucket is the only remaining way to be incomplete.
    IF EXISTS (
        SELECT bucket_code FROM tdcc_distribution_schema_buckets
         WHERE distribution_schema = snapshot.distribution_schema
        EXCEPT
        SELECT bucket_code FROM tdcc_distribution
         WHERE snapshot_version_id = snapshot.id
    ) THEN
        RAISE EXCEPTION 'TDCC snapshot % is incomplete for distribution schema %',
            snapshot.id, snapshot.distribution_schema
            USING ERRCODE = '23514';
    END IF;
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

PHASE1_REHASH = """
    UPDATE tdcc_snapshot_versions snapshot
       SET business_content_hash = encode(digest(convert_to(jsonb_build_object(
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
        )::text, 'UTF8'), 'sha256'), 'hex')
     WHERE snapshot.business_content_hash IS NOT NULL;
"""

SEALED_TRIGGERS = (
    ("tdcc_snapshot_versions", "protect_tdcc_parent"),
    ("tdcc_distribution", "protect_tdcc_distribution"),
    ("tdcc_snapshot_seals", "immutable_tdcc_seal"),
)


def _set_sealed_triggers(enabled: bool) -> None:
    action = "ENABLE" if enabled else "DISABLE"
    for table, trigger in SEALED_TRIGGERS:
        op.execute(f"ALTER TABLE {table} {action} TRIGGER {trigger}")


def _copy_parent_hash_to_seals() -> None:
    op.execute(
        """
        UPDATE tdcc_snapshot_seals seal
           SET business_content_hash = snapshot.business_content_hash
          FROM tdcc_snapshot_versions snapshot
         WHERE snapshot.id = seal.snapshot_version_id
        """
    )


def upgrade() -> None:
    _create_observation_lineage()
    _create_distribution_schemas()

    op.add_column(
        "tdcc_snapshot_versions",
        sa.Column(
            "distribution_schema",
            sa.String(64),
            nullable=False,
            server_default=OFFICIAL_PROFILE,
        ),
    )
    op.alter_column("tdcc_snapshot_versions", "distribution_schema", server_default=None)
    op.create_foreign_key(
        op.f(
            "fk_tdcc_snapshot_versions_distribution_schema_"
            "tdcc_distribution_schemas"
        ),
        "tdcc_snapshot_versions",
        "tdcc_distribution_schemas",
        ["distribution_schema"],
        ["distribution_schema"],
        ondelete="RESTRICT",
    )
    _create_profile_freeze()
    op.alter_column("tdcc_distribution", "holder_count", nullable=True)
    op.drop_constraint(
        op.f("ck_tdcc_distribution_shares_nonnegative"), "tdcc_distribution", type_="check"
    )
    op.drop_constraint(
        op.f("ck_tdcc_distribution_ownership_percent_range"),
        "tdcc_distribution",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_tdcc_distribution_ownership_percent_range"),
        "tdcc_distribution",
        "ownership_percent BETWEEN -100 AND 100",
    )

    op.execute(
        """
        -- Volatile on purpose: each call takes a fresh snapshot, so the seal
        -- trigger hashes children committed while it waited for the lock.
        CREATE FUNCTION stockdc_tdcc_snapshot_business_hash(p_snapshot_version_id bigint)
        RETURNS text LANGUAGE sql VOLATILE AS $$
            SELECT encode(digest(convert_to(jsonb_build_object(
                'snapshot_date', snapshot.snapshot_date,
                'distribution_schema', snapshot.distribution_schema,
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

    _set_sealed_triggers(False)
    op.execute(
        f"""
        UPDATE tdcc_distribution dist
           SET holder_count = NULL
          FROM tdcc_snapshot_versions snapshot
          JOIN tdcc_distribution_schema_buckets bucket
            ON bucket.distribution_schema = snapshot.distribution_schema
         WHERE snapshot.id = dist.snapshot_version_id
           AND bucket.bucket_code = dist.bucket_code
           AND bucket.bucket_role = 'adjustment'
           AND dist.holder_count = 0;

        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM tdcc_distribution dist
                  JOIN tdcc_snapshot_versions snapshot
                    ON snapshot.id = dist.snapshot_version_id
                  LEFT JOIN tdcc_distribution_schema_buckets bucket
                    ON bucket.distribution_schema = snapshot.distribution_schema
                   AND bucket.bucket_code = dist.bucket_code
                 WHERE bucket.bucket_code IS NULL
                    OR (bucket.bucket_role = 'adjustment' AND dist.holder_count IS NOT NULL)
                    OR (bucket.bucket_role <> 'adjustment'
                        AND (dist.holder_count IS NULL OR dist.shares < 0
                             OR dist.ownership_percent < 0))
            ) THEN
                RAISE EXCEPTION
                    'existing TDCC distribution rows do not fit profile {OFFICIAL_PROFILE}';
            END IF;
            IF EXISTS (
                SELECT 1
                  FROM tdcc_snapshot_seals seal
                  JOIN tdcc_snapshot_versions snapshot
                    ON snapshot.id = seal.snapshot_version_id
                  JOIN tdcc_distribution_schema_buckets bucket
                    ON bucket.distribution_schema = snapshot.distribution_schema
                 WHERE NOT EXISTS (
                     SELECT 1 FROM tdcc_distribution dist
                      WHERE dist.snapshot_version_id = snapshot.id
                        AND dist.bucket_code = bucket.bucket_code
                 )
            ) THEN
                RAISE EXCEPTION
                    'existing sealed TDCC snapshots are incomplete for {OFFICIAL_PROFILE}';
            END IF;
        END;
        $$;

        UPDATE tdcc_snapshot_versions
           SET business_content_hash = stockdc_tdcc_snapshot_business_hash(id)
         WHERE business_content_hash IS NOT NULL;
        """
    )
    _copy_parent_hash_to_seals()
    _set_sealed_triggers(True)

    op.execute(
        """
        CREATE FUNCTION stockdc_validate_tdcc_distribution_row()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE role text; profile text;
        BEGIN
            SELECT snapshot.distribution_schema, bucket.bucket_role
              INTO profile, role
              FROM tdcc_snapshot_versions snapshot
              LEFT JOIN tdcc_distribution_schema_buckets bucket
                ON bucket.distribution_schema = snapshot.distribution_schema
               AND bucket.bucket_code = NEW.bucket_code
             WHERE snapshot.id = NEW.snapshot_version_id;
            IF role IS NULL THEN
                RAISE EXCEPTION 'bucket % is not defined by distribution schema %',
                    NEW.bucket_code, profile USING ERRCODE = '23514';
            END IF;
            IF role = 'adjustment' THEN
                -- Canonical contract: an adjustment row has no holder count.
                IF NEW.holder_count IS NOT NULL AND NEW.holder_count <> 0 THEN
                    RAISE EXCEPTION 'adjustment bucket % cannot carry holder_count %',
                        NEW.bucket_code, NEW.holder_count USING ERRCODE = '23514';
                END IF;
                NEW.holder_count := NULL;
            ELSIF NEW.holder_count IS NULL
               OR NEW.shares < 0
               OR NEW.ownership_percent < 0 THEN
                RAISE EXCEPTION
                    '% bucket % requires holder_count and non-negative shares/percent',
                    role, NEW.bucket_code USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        -- Fires after protect_tdcc_distribution (trigger names sort), so the
        -- aggregate lock and the sealed check still come first.
        CREATE TRIGGER validate_tdcc_distribution_row
        BEFORE INSERT OR UPDATE ON tdcc_distribution
        FOR EACH ROW EXECUTE FUNCTION stockdc_validate_tdcc_distribution_row();
        """
    )
    op.execute(
        SEAL_FUNCTION_TEMPLATE
        % {"completeness": PHASE6_COMPLETENESS, "hash_body": PHASE6_HASH_BODY}
    )
    _create_publication_time_rule()


def _create_observation_lineage() -> None:
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
        """
    )


def _create_distribution_schemas() -> None:
    op.create_table(
        "tdcc_distribution_schemas",
        sa.Column("distribution_schema", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "distribution_schema", name=op.f("pk_tdcc_distribution_schemas")
        ),
    )
    op.create_table(
        "tdcc_distribution_schema_buckets",
        sa.Column("distribution_schema", sa.String(64), nullable=False),
        sa.Column("bucket_code", sa.String(32), nullable=False),
        sa.Column("bucket_role", sa.String(16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["distribution_schema"],
            ["tdcc_distribution_schemas.distribution_schema"],
            name=op.f(
                "fk_tdcc_distribution_schema_buckets_distribution_schema_"
                "tdcc_distribution_schemas"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "distribution_schema",
            "bucket_code",
            name=op.f("pk_tdcc_distribution_schema_buckets"),
        ),
        sa.CheckConstraint(
            "bucket_role IN ('holding', 'adjustment', 'total')",
            name=op.f("ck_tdcc_distribution_schema_buckets_bucket_role_valid"),
        ),
    )
    ranges = [
        "1-999", "1,000-5,000", "5,001-10,000", "10,001-15,000",
        "15,001-20,000", "20,001-30,000", "30,001-40,000", "40,001-50,000",
        "50,001-100,000", "100,001-200,000", "200,001-400,000",
        "400,001-600,000", "600,001-800,000", "800,001-1,000,000",
        "1,000,001 and above",
    ]
    buckets = [
        (str(level), "holding", f"holding range {shares} shares")
        for level, shares in enumerate(ranges, start=1)
    ]
    buckets += [
        ("16", "adjustment", "difference adjustment; signed shares, no holder count"),
        ("17", "total", "source-published total"),
    ]
    op.execute(
        sa.text(
            "INSERT INTO tdcc_distribution_schemas (distribution_schema, description) "
            "VALUES (:profile, :description)"
        ).bindparams(
            profile=OFFICIAL_PROFILE,
            description="Official TDCC shareholding distribution, levels 1-17",
        )
    )
    for code, role, description in buckets:
        op.execute(
            sa.text(
                """
                INSERT INTO tdcc_distribution_schema_buckets (
                    distribution_schema, bucket_code, bucket_role, description
                ) VALUES (:profile, :code, :role, :description)
                """
            ).bindparams(
                profile=OFFICIAL_PROFILE, code=code, role=role, description=description
            )
        )
    op.execute(
        """
        CREATE TRIGGER immutable_tdcc_distribution_schemas
        BEFORE UPDATE OR DELETE ON tdcc_distribution_schemas
        FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER no_truncate_tdcc_distribution_schemas
        BEFORE TRUNCATE ON tdcc_distribution_schemas
        FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER immutable_tdcc_distribution_schema_buckets
        BEFORE UPDATE OR DELETE ON tdcc_distribution_schema_buckets
        FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER no_truncate_tdcc_distribution_schema_buckets
        BEFORE TRUNCATE ON tdcc_distribution_schema_buckets
        FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
        """
    )


def _create_profile_freeze() -> None:
    op.execute(
        """
        CREATE FUNCTION stockdc_freeze_used_tdcc_profile()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            -- A snapshot insert takes FOR KEY SHARE on this profile row through
            -- its foreign key, so locking it serializes the definition change
            -- with first use: whichever commits first wins.
            PERFORM 1 FROM tdcc_distribution_schemas
             WHERE distribution_schema = NEW.distribution_schema FOR UPDATE;
            IF EXISTS (
                SELECT 1 FROM tdcc_snapshot_versions
                 WHERE distribution_schema = NEW.distribution_schema
            ) THEN
                RAISE EXCEPTION
                    'distribution schema % is in use; define a new profile code',
                    NEW.distribution_schema USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER freeze_used_tdcc_profile
        BEFORE INSERT ON tdcc_distribution_schema_buckets
        FOR EACH ROW EXECUTE FUNCTION stockdc_freeze_used_tdcc_profile();
        """
    )


def _create_publication_time_rule() -> None:
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
        DROP TRIGGER IF EXISTS validate_tdcc_distribution_row ON tdcc_distribution;
        DROP FUNCTION IF EXISTS stockdc_validate_tdcc_distribution_row();
        """
    )
    op.execute(
        SEAL_FUNCTION_TEMPLATE
        % {"completeness": "", "hash_body": PHASE1_HASH_BODY}
    )
    op.execute("DROP FUNCTION IF EXISTS stockdc_tdcc_snapshot_business_hash(bigint)")

    # The Phase 1 contract has no adjustment NULL; restore canonical 0 and the
    # Phase 1 hash payload for sealed snapshots.
    _set_sealed_triggers(False)
    op.execute("UPDATE tdcc_distribution SET holder_count = 0 WHERE holder_count IS NULL")
    op.execute(PHASE1_REHASH)
    _copy_parent_hash_to_seals()
    _set_sealed_triggers(True)

    op.drop_constraint(
        op.f("ck_tdcc_distribution_ownership_percent_range"),
        "tdcc_distribution",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_tdcc_distribution_ownership_percent_range"),
        "tdcc_distribution",
        "ownership_percent BETWEEN 0 AND 100",
    )
    op.create_check_constraint(
        op.f("ck_tdcc_distribution_shares_nonnegative"),
        "tdcc_distribution",
        "shares >= 0",
    )
    op.alter_column("tdcc_distribution", "holder_count", nullable=False)
    op.drop_constraint(
        op.f(
            "fk_tdcc_snapshot_versions_distribution_schema_"
            "tdcc_distribution_schemas"
        ),
        "tdcc_snapshot_versions",
        type_="foreignkey",
    )
    op.drop_column("tdcc_snapshot_versions", "distribution_schema")
    op.drop_table("tdcc_distribution_schema_buckets")
    op.drop_table("tdcc_distribution_schemas")
    op.execute("DROP FUNCTION IF EXISTS stockdc_freeze_used_tdcc_profile()")
    op.execute(
        """
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
