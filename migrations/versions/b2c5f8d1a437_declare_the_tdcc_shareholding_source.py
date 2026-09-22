"""declare the TDCC shareholding-distribution source

Revision ID: b2c5f8d1a437
Revises: e1b7d4a92c63
Create Date: 2026-09-22

Step 24-a. One source, `tdcc_opendata`: the weekly whole-market file from
OpenData `getOD.ashx?id=1-5` (audit §4.9). The archived weeks before the first
forward capture are the same endpoint's bytes, saved whole by the legacy
scraper — the one archive that is an official response rather than a scraper's
rewrite (ROADMAP §14) — so they are the same source, read as `legacy_archive`
artifacts. Splitting them would give one publisher two histories for one
weekly series.

It follows `tdcc_weekly@1`, registered in Step 15-b: the data date's week
resolves at 12:00 on the following Sunday. That rule rests on the legacy
Sunday 10:20 job plus margin rather than a published TDCC schedule, which the
audit could not find (ADR-0020 §3, decision 2), and it is the only publication
instant this dataset has: neither the bulk file nor the portal states one.

`capture_bound` is accepted for the forward capture Step 27 will run, when a
`first_capture` run genuinely sees a week first. A bulk archive walk is a
`gap_fill` and claims the rule alone.

`is_canonical` stays false, as it is for every source declared so far. There is
only one source here, so nothing needs choosing between; claiming canonical
would be a reconciliation policy, and that takes an ADR (CLAUDE.md §30).

No `dataset_expected_coverage` row: its cadence check allows `trading_day` and
`calendar_month` only, and this series is weekly. The weekly cadence and the
coverage validator that reads it belong to Step 24-b, which is where the
Lunar New Year closures and the truncated 2023-10-20 week are classified.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c5f8d1a437"
down_revision: str | None = "e1b7d4a92c63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "tdcc_opendata"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('tdcc_snapshot',
                    'weekly per-security shareholding distribution', 'v1')
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
            VALUES ('tdcc_snapshot', :source, true, true, 0, 'verified',
                    ARRAY['official', 'capture_bound',
                          'release_rule']::varchar[], false)
            ON CONFLICT (dataset_code, source) DO UPDATE
               SET accepted_evidence_types = (
                       SELECT array_agg(DISTINCT t ORDER BY t)
                         FROM unnest(
                             dataset_sources.accepted_evidence_types
                             || EXCLUDED.accepted_evidence_types
                         ) AS t
                   )
            """
        ).bindparams(source=SOURCE)
    )
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_release_rules
                (dataset_code, source, rule_id, version, note)
            VALUES ('tdcc_snapshot', :source, 'tdcc_weekly', 1,
                    'A data date resolves at 12:00 on the following Sunday '
                    '(ADR-0020 §3, decision 2).')
            ON CONFLICT (dataset_code, source) DO NOTHING
            """
        ).bindparams(source=SOURCE)
    )


GUARD = """
DO $$
DECLARE blocking bigint;
BEGIN
    SELECT (SELECT count(*) FROM ingest_runs
             WHERE dataset_code = 'tdcc_snapshot' AND source = 'tdcc_opendata')
         + (SELECT count(*) FROM import_manifests
             WHERE dataset_code = 'tdcc_snapshot' AND source = 'tdcc_opendata')
         + (SELECT count(*) FROM tdcc_snapshot_versions
             WHERE source = 'tdcc_opendata')
      INTO blocking;
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away the declared TDCC source',
            DETAIL = format('%s ingest runs, manifests or versions reference it',
                            blocking),
            HINT = 'history is append-only; remove the history deliberately first';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    # Only the rows this migration declared; a fixture may declare other
    # tdcc_snapshot sources with runs of their own.
    for table in ("dataset_release_rules", "dataset_sources"):
        op.execute(
            sa.text(
                f"DELETE FROM {table} "
                "WHERE dataset_code = 'tdcc_snapshot' AND source = :source"
            ).bindparams(source=SOURCE)
        )
    op.execute(
        sa.text(
            """
            DELETE FROM dataset_catalog
             WHERE dataset_code = 'tdcc_snapshot'
               AND NOT EXISTS (
                     SELECT 1 FROM dataset_sources
                      WHERE dataset_code = 'tdcc_snapshot'
                   )
            """
        )
    )
