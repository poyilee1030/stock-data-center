"""declare TPEx insti/qfii as a second foreign-holding source

Revision ID: f2b6d8a4c1e9
Revises: e7a9c3f1b2d4
Create Date: 2026-09-19

Step 20-d, found by its backfill. MOPS `t13sa150_otc` rebuilds a past date from
today's security list: 5371, 4130, 3426 and 4987 (delisted 2026-05..08) and 5236
(moved to TWSE 2026-07-15) vanish from every MOPS date back to 2020, although
legacy's February 2026 files hold them. TPEx's own `insti/qfii` still lists
them, while omitting most ETFs and three contract columns (audit §4.4).

So TPEx has two sources, each its own history (CLAUDE.md §30); nothing merges
them. MOPS stays the declared TPEx coverage: `dataset_expected_coverage` holds
one source per market, and MOPS carries every column. `insti/qfii` follows the
same `exchange_daily_settled@1` rule, and notes on its own page that the table
updates at 18:00 and 22:00.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2b6d8a4c1e9"
down_revision: str | None = "e7a9c3f1b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "tpex_insti_qfii"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_sources
                (dataset_code, source, supports_market_pit, supports_system_pit,
                 publication_time_quality, evidence_status,
                 accepted_evidence_types, is_canonical)
            VALUES ('foreign_holding', :source, true, true, 0, 'verified',
                    ARRAY['official', 'capture_bound', 'release_rule']::varchar[], false)
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
            VALUES ('foreign_holding', :source, 'exchange_daily_settled', 1,
                    'Trade date D resolves at 03:00 on D+1 (ADR-0020 §3).')
            ON CONFLICT (dataset_code, source) DO NOTHING
            """
        ).bindparams(source=SOURCE)
    )


GUARD = """
DO $$
DECLARE blocking bigint;
BEGIN
    SELECT (SELECT count(*) FROM ingest_runs
             WHERE dataset_code = 'foreign_holding' AND source = 'tpex_insti_qfii')
         + (SELECT count(*) FROM import_manifests
             WHERE dataset_code = 'foreign_holding' AND source = 'tpex_insti_qfii')
         + (SELECT count(*) FROM foreign_holding_versions
             WHERE source = 'tpex_insti_qfii')
      INTO blocking;
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away the declared tpex_insti_qfii source',
            DETAIL = format('%s ingest runs, manifests or versions reference it',
                            blocking),
            HINT = 'history is append-only; remove the history deliberately first';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    for table in ("dataset_release_rules", "dataset_sources"):
        op.execute(
            sa.text(
                f"DELETE FROM {table} "
                "WHERE dataset_code = 'foreign_holding' AND source = :source"
            ).bindparams(source=SOURCE)
        )
