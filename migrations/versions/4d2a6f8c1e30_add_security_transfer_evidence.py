"""add re-runnable security transfer evidence

Revision ID: 4d2a6f8c1e30
Revises: 9a7d3e5c1b20
Create Date: 2026-09-13

Cache impact: none. Phase 9 has no cache. This migration makes explicit
cross-source transfer evidence durable so reconciliation can be recomputed from
canonical histories instead of depending on source import order.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4d2a6f8c1e30"
down_revision: str | None = "9a7d3e5c1b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_transfer_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("entry_version_id", sa.BigInteger(), nullable=False),
        sa.Column("from_source", sa.String(64), nullable=False),
        sa.Column("from_market", sa.String(32), nullable=False),
        sa.Column("source_term", sa.Text(), nullable=False),
        sa.Column(
            "recorded_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "from_market <> ''",
            name=op.f("ck_security_transfer_events_from_market_nonempty"),
        ),
        sa.CheckConstraint(
            "from_source <> ''",
            name=op.f("ck_security_transfer_events_from_source_nonempty"),
        ),
        sa.CheckConstraint(
            "source_term <> ''",
            name=op.f("ck_security_transfer_events_source_term_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["entry_version_id"],
            ["security_metadata_versions.id"],
            name=op.f(
                "fk_security_transfer_events_entry_version_id_security_metadata_versions"
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
                "fk_security_transfer_events_raw_artifact_id_raw_artifact_observations"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security_transfer_events")),
        sa.UniqueConstraint(
            "entry_version_id",
            "from_source",
            "from_market",
            name="uq_security_transfer_event",
        ),
    )
    op.create_table(
        "security_transfer_event_observations",
        sa.Column("security_transfer_event_id", sa.BigInteger(), nullable=False),
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["security_transfer_event_id"],
            ["security_transfer_events.id"],
            name=op.f(
                "fk_security_transfer_event_observations_security_transfer_event_id_security_transfer_events"
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
                "fk_security_transfer_event_observations_raw_artifact_id_raw_artifact_observations"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "security_transfer_event_id",
            "raw_artifact_id",
            "ingest_run_id",
            name=op.f("pk_security_transfer_event_observations"),
        ),
    )
    op.execute(
        r"""
        CREATE FUNCTION stockdc_prepare_security_transfer_event() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            entry_source text;
            entry_market text;
            entry_effective_on date;
            entry_listed_on date;
            entry_delisted_on date;
        BEGIN
            SELECT source, market, effective_from, listed_on, delisted_on
              INTO entry_source, entry_market, entry_effective_on,
                   entry_listed_on, entry_delisted_on
              FROM security_metadata_versions
             WHERE id = NEW.entry_version_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'security transfer entry version % does not exist',
                    NEW.entry_version_id USING ERRCODE = '23503';
            END IF;
            IF entry_source = NEW.from_source THEN
                RAISE EXCEPTION 'security transfer must cross independent sources'
                    USING ERRCODE = '23514';
            END IF;
            IF entry_market = NEW.from_market THEN
                RAISE EXCEPTION 'security transfer must cross markets'
                    USING ERRCODE = '23514';
            END IF;
            IF entry_listed_on IS DISTINCT FROM entry_effective_on
               OR entry_delisted_on IS NOT NULL THEN
                RAISE EXCEPTION 'security transfer entry must reference a listing event'
                    USING ERRCODE = '23514';
            END IF;
            PERFORM stockdc_assert_lineage(
                NEW.raw_artifact_id, NEW.ingest_run_id,
                'security_metadata', entry_source
            );
            NEW.recorded_at := statement_timestamp();
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION stockdc_assert_security_transfer_observation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            entry_source text;
        BEGIN
            SELECT metadata.source
              INTO entry_source
              FROM security_transfer_events AS transfer
              JOIN security_metadata_versions AS metadata
                ON metadata.id = transfer.entry_version_id
             WHERE transfer.id = NEW.security_transfer_event_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'security transfer event % does not exist',
                    NEW.security_transfer_event_id USING ERRCODE = '23503';
            END IF;
            PERFORM stockdc_assert_lineage(
                NEW.raw_artifact_id, NEW.ingest_run_id,
                'security_metadata', entry_source
            );
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER prepare_security_transfer_event
        BEFORE INSERT ON security_transfer_events
        FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_security_transfer_event();
        CREATE TRIGGER immutable_security_transfer_events
        BEFORE UPDATE OR DELETE ON security_transfer_events
        FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER no_truncate_security_transfer_events
        BEFORE TRUNCATE ON security_transfer_events
        FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();

        CREATE TRIGGER assert_security_transfer_event_observation
        BEFORE INSERT ON security_transfer_event_observations
        FOR EACH ROW EXECUTE FUNCTION stockdc_assert_security_transfer_observation();
        CREATE TRIGGER immutable_security_transfer_event_observations
        BEFORE UPDATE OR DELETE ON security_transfer_event_observations
        FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER no_truncate_security_transfer_event_observations
        BEFORE TRUNCATE ON security_transfer_event_observations
        FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER no_truncate_security_transfer_event_observations "
        "ON security_transfer_event_observations; "
        "DROP TRIGGER immutable_security_transfer_event_observations "
        "ON security_transfer_event_observations; "
        "DROP TRIGGER assert_security_transfer_event_observation "
        "ON security_transfer_event_observations; "
        "DROP TRIGGER no_truncate_security_transfer_events "
        "ON security_transfer_events; "
        "DROP TRIGGER immutable_security_transfer_events "
        "ON security_transfer_events; "
        "DROP TRIGGER prepare_security_transfer_event "
        "ON security_transfer_events"
    )
    op.drop_table("security_transfer_event_observations")
    op.drop_table("security_transfer_events")
    op.execute("DROP FUNCTION stockdc_assert_security_transfer_observation()")
    op.execute("DROP FUNCTION stockdc_prepare_security_transfer_event()")
