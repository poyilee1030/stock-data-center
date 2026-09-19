"""declare the margin-trading sources

Revision ID: a4c8e2f6b1d3
Revises: f2b6d8a4c1e9
Create Date: 2026-09-19

Step 21-a. Two sources, one per market, each its own history (CLAUDE.md §30):
TWSE `MI_MARGN` and TPEx's `margin/balance`, the JSON of the legacy
`margin_bal` page with the same 20 fields (audit §4.5).

Both follow `exchange_daily_settled@1`, like the other exchange end-of-day
tables. The evidence that the table exists by 03:00 on D+1: the legacy daily
job saved 137 TWSE and 136 TPEx files in the window on the trade date itself
(file mtimes, Asia/Taipei). The other ~1,490 files per market were written by
the January-February 2026 bulk re-fetch and say nothing about timing.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c8e2f6b1d3"
down_revision: str | None = "f2b6d8a4c1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COVERAGE = {
    "twse_mi_margn": ("TWSE", "TWSE marginTrading/MI_MARGN, selectType=ALL (audit 4.5)."),
    "tpex_margin_balance": ("TPEx", "TPEx www/zh-tw/margin/balance (audit 4.5)."),
}
SOURCES = tuple(COVERAGE)


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('margin_trading',
                    'per-security margin purchases and short sales', 'v1')
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
                VALUES ('margin_trading', :source, true, true, 0, 'verified',
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
                VALUES ('margin_trading', :source, 'exchange_daily_settled', 1,
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
                VALUES ('margin_trading', :market, :source, 'TWSE',
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
             WHERE dataset_code = 'margin_trading'
               AND source IN ('twse_mi_margn', 'tpex_margin_balance'))
         + (SELECT count(*) FROM import_manifests
             WHERE dataset_code = 'margin_trading'
               AND source IN ('twse_mi_margn', 'tpex_margin_balance'))
         + (SELECT count(*) FROM margin_trading_versions
             WHERE source IN ('twse_mi_margn', 'tpex_margin_balance'))
      INTO blocking;
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away declared margin-trading sources',
            DETAIL = format('%s ingest runs, manifests or versions reference them',
                            blocking),
            HINT = 'history is append-only; remove the history deliberately first';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    # Only the rows this migration declared; a fixture may declare other
    # margin_trading sources with runs of their own.
    for table in (
        "dataset_expected_coverage",
        "dataset_release_rules",
        "dataset_sources",
    ):
        op.execute(
            sa.text(
                f"DELETE FROM {table} "
                "WHERE dataset_code = 'margin_trading' AND source IN :sources"
            ).bindparams(sa.bindparam("sources", value=SOURCES, expanding=True))
        )
    op.execute(
        sa.text(
            """
            DELETE FROM dataset_catalog
             WHERE dataset_code = 'margin_trading'
               AND NOT EXISTS (
                     SELECT 1 FROM dataset_sources
                      WHERE dataset_code = 'margin_trading'
                   )
            """
        )
    )
