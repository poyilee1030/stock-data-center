"""declare the corporate-action sources and event retraction table

Revision ID: 02f0a144b1fc
Revises: 8e4b2c7d9a13
Create Date: 2026-09-16

Step 19-c. Two additions the import path needs and nothing declared yet:

- The six exchange result-feed sources (ADR-0019) opt into `capture_bound`
  evidence, mirroring how 18-b declared the market-index sources. None
  declares a release rule: a result feed publishes whenever the exchange
  executes the event, not on a fixed schedule a rule could cite.
- `corporate_action_retractions` records that a range file's covered window no
  longer lists a locator it once did. Retraction is a business-level fact
  about an executed event withdrawn, not a correction of stored content, so it
  cannot be a `corporate_action_versions` revision — that table's rows are
  typed by `action_type` and enforce the terms each type requires, which a
  retraction has none of. It is its own append-only table instead, keyed by
  `(event_id, raw_artifact_id)`: one retraction fact per run whose covered
  range proved the row was gone, auditable per CLAUDE.md §26 rather than
  collapsed to one row per event. Nothing currently reads it — no consumer
  needs corporate actions yet — so this migration only records the fact;
  resolving whether a later reappearance un-retracts an event is deferred
  until a reader exists to need the answer.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "02f0a144b1fc"
down_revision: str | None = "8e4b2c7d9a13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCES = (
    "twse_twt49u", "twse_twtauu", "twse_twtb8u",
    "tpex_exdailyq", "tpex_revivt", "tpex_pvchgrslt",
)

RETRACTIONS_TABLE = "corporate_action_retractions"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('corporate_action',
                    'exchange result-feed corporate actions (Invariant G(2))',
                    'v1')
            ON CONFLICT (dataset_code) DO NOTHING
            """
        )
    )
    for source in SOURCES:
        op.execute(
            sa.text(
                """
                INSERT INTO dataset_sources
                    (dataset_code, source, supports_market_pit, supports_system_pit,
                     publication_time_quality, evidence_status,
                     accepted_evidence_types, is_canonical)
                VALUES ('corporate_action', :source, true, true, 0, 'verified',
                        ARRAY['official', 'capture_bound']::varchar[], false)
                ON CONFLICT (dataset_code, source) DO UPDATE
                   SET accepted_evidence_types = (
                           SELECT array_agg(DISTINCT t ORDER BY t)
                             FROM unnest(
                                 dataset_sources.accepted_evidence_types
                                 || EXCLUDED.accepted_evidence_types
                             ) AS t
                       )
                """
            ).bindparams(source=source)
        )

    op.create_table(
        RETRACTIONS_TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("ingested_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("raw_artifact_id", sa.UUID(), nullable=False),
        sa.Column("ingest_run_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "reason <> ''",
            name=op.f("ck_corporate_action_retractions_corporate_action_retraction_reason_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["corporate_action_events.id"],
            name=op.f("fk_corporate_action_retractions_event_id_corporate_action_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["raw_artifact_id", "ingest_run_id"],
            ["raw_artifact_observations.raw_artifact_id", "raw_artifact_observations.ingest_run_id"],
            name=op.f("fk_corporate_action_retractions_raw_artifact_id_raw_artifact_observations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_corporate_action_retractions")),
        sa.UniqueConstraint(
            "event_id", "raw_artifact_id",
            name="uq_corporate_action_retraction_artifact",
        ),
    )
    op.execute(
        f"CREATE TRIGGER immutable_{RETRACTIONS_TABLE} BEFORE UPDATE OR DELETE "
        f"ON {RETRACTIONS_TABLE} FOR EACH ROW "
        f"EXECUTE FUNCTION stockdc_reject_mutation()"
    )
    op.execute(
        f"CREATE TRIGGER no_truncate_{RETRACTIONS_TABLE} BEFORE TRUNCATE "
        f"ON {RETRACTIONS_TABLE} FOR EACH STATEMENT "
        f"EXECUTE FUNCTION stockdc_reject_mutation()"
    )


_SOURCE_LIST = "(" + ",".join(f"'{source}'" for source in SOURCES) + ")"

# Count what actually blocks the delete, matching 7a2c9e4d1b58's own guard:
# `ingest_runs` and `import_manifests` carry a real foreign key to
# `dataset_sources(dataset_code, source)`, so removing the row while either
# references it fails the DELETE itself (a bare 23001, mid-statement) unless
# this checks first and refuses with a readable P0001 before any mutation.
GUARD = f"""
DO $$
DECLARE blocking bigint;
BEGIN
    SELECT (SELECT count(*) FROM ingest_runs
             WHERE dataset_code = 'corporate_action' AND source IN {_SOURCE_LIST})
         + (SELECT count(*) FROM import_manifests
             WHERE dataset_code = 'corporate_action' AND source IN {_SOURCE_LIST})
         + (SELECT count(*) FROM corporate_action_retractions)
      INTO blocking;
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away declared corporate-action sources',
            DETAIL = format(
                '%s ingest runs, manifests or retractions reference them',
                blocking
            ),
            HINT = 'history is append-only; remove the history deliberately first';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    op.execute(f"DROP TRIGGER no_truncate_{RETRACTIONS_TABLE} ON {RETRACTIONS_TABLE}")
    op.execute(f"DROP TRIGGER immutable_{RETRACTIONS_TABLE} ON {RETRACTIONS_TABLE}")
    op.drop_table(RETRACTIONS_TABLE)
    sources = sa.bindparam("sources", value=list(SOURCES), expanding=True)
    op.execute(
        sa.text(
            "DELETE FROM dataset_sources "
            "WHERE dataset_code = 'corporate_action' AND source IN :sources"
        ).bindparams(sources)
    )
    op.execute(
        sa.text(
            """
            DELETE FROM dataset_catalog
             WHERE dataset_code = 'corporate_action'
               AND NOT EXISTS (
                     SELECT 1 FROM dataset_sources
                      WHERE dataset_code = 'corporate_action'
                   )
            """
        )
    )
