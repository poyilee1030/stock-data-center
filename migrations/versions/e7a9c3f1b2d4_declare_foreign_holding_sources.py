"""declare the foreign-holding sources

Revision ID: e7a9c3f1b2d4
Revises: d5f8b2e4a0c7
Create Date: 2026-09-19

Step 20-d. Two sources, one per market, each its own history (CLAUDE.md §30).
TWSE `MI_QFIIS` is the exchange's own table. TPEx's is MOPS `t13sa150_otc`:
TPEx's `insti/qfii` omits 119 ETFs and two contract columns (audit §4.4).

Both follow `exchange_daily_settled@1`, like the other exchange end-of-day
tables. The evidence that the table exists by 03:00 on D+1: the legacy daily
job saved 137 TWSE and 138 TPEx files in the window on the trade date itself
(file mtimes, Asia/Taipei). The other ~1,490 files per market were written by
the January-February 2026 bulk re-fetch and say nothing about timing.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a9c3f1b2d4"
down_revision: str | None = "d5f8b2e4a0c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COVERAGE = {
    "twse_mi_qfiis": ("TWSE", "TWSE fund/MI_QFIIS, ALLBUT0999 (audit 4.4)."),
    "mops_t13sa150_otc": (
        "TPEx",
        "MOPS t13sa150_otc, a POST answering cp950 HTML (audit 4.4).",
    ),
}
SOURCES = tuple(COVERAGE)


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('foreign_holding',
                    'per-security foreign and mainland investor holding', 'v1')
            ON CONFLICT (dataset_code) DO NOTHING
            """
        )
    )
    for source, (market, note) in COVERAGE.items():
        op.execute(
            sa.text(
                """
                INSERT INTO dataset_sources
                    (dataset_code, source, supports_market_pit, supports_system_pit,
                     publication_time_quality, evidence_status,
                     accepted_evidence_types, is_canonical)
                VALUES ('foreign_holding', :source, true, true, 0, 'verified',
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
            ).bindparams(source=source)
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
            ).bindparams(source=source)
        )
        op.execute(
            sa.text(
                """
                INSERT INTO dataset_expected_coverage
                    (dataset_code, market, source, calendar_market, cadence,
                     period_column, window_start, note)
                VALUES ('foreign_holding', :market, :source, 'TWSE',
                        'trading_day', 'trade_date', DATE '2020-01-02', :note)
                ON CONFLICT (dataset_code, market) DO UPDATE
                   SET source = EXCLUDED.source,
                       calendar_market = EXCLUDED.calendar_market,
                       cadence = EXCLUDED.cadence,
                       period_column = EXCLUDED.period_column,
                       window_start = EXCLUDED.window_start,
                       note = EXCLUDED.note
                """
            ).bindparams(source=source, market=market, note=note)
        )


GUARD = """
DO $$
DECLARE blocking bigint;
BEGIN
    -- Counts what actually blocks the delete: ingest runs reference the
    -- source, and a quarantined date leaves a run with no version (18-b).
    SELECT (SELECT count(*) FROM ingest_runs
             WHERE dataset_code = 'foreign_holding'
               AND source IN ('twse_mi_qfiis', 'mops_t13sa150_otc'))
         + (SELECT count(*) FROM import_manifests
             WHERE dataset_code = 'foreign_holding'
               AND source IN ('twse_mi_qfiis', 'mops_t13sa150_otc'))
         + (SELECT count(*) FROM foreign_holding_versions
             WHERE source IN ('twse_mi_qfiis', 'mops_t13sa150_otc'))
      INTO blocking;
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away declared foreign-holding sources',
            DETAIL = format('%s ingest runs, manifests or versions reference them',
                            blocking),
            HINT = 'history is append-only; remove the history deliberately first';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    # Only the rows this migration declared; a fixture may declare other
    # foreign_holding sources with runs of their own.
    for table in (
        "dataset_expected_coverage",
        "dataset_release_rules",
        "dataset_sources",
    ):
        op.execute(
            sa.text(
                f"DELETE FROM {table} "
                "WHERE dataset_code = 'foreign_holding' AND source IN :sources"
            ).bindparams(sa.bindparam("sources", value=SOURCES, expanding=True))
        )
    op.execute(
        sa.text(
            """
            DELETE FROM dataset_catalog
             WHERE dataset_code = 'foreign_holding'
               AND NOT EXISTS (
                     SELECT 1 FROM dataset_sources
                      WHERE dataset_code = 'foreign_holding'
                   )
            """
        )
    )
