"""declare the institutional market-summary sources

Revision ID: d5f8b2e4a0c7
Revises: c4e7a1d3f9b6
Create Date: 2026-09-18

Step 20-b. Two sources, one per exchange, each its own history (CLAUDE.md §30).
TWSE `BFI82U` and TPEx `insti/summary` publish every contract column, but they
are different endpoints, name their rows differently, and neither is canonical.

Both follow `exchange_daily_settled@1`, like the per-security flows of Step
20-a: they are exchange end-of-day tables for a trade date.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5f8b2e4a0c7"
down_revision: str | None = "c4e7a1d3f9b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COVERAGE = {
    "twse_bfi82u": ("TWSE", "TWSE fund/BFI82U, type=day (audit 4.3)."),
    "tpex_insti_summary": ("TPEx", "TPEx insti/summary (audit 4.3)."),
}
SOURCES = tuple(COVERAGE)


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('institutional_market_summary',
                    'institutional investor market trading-value summary', 'v1')
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
                VALUES ('institutional_market_summary', :source, true, true, 0, 'verified',
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
                VALUES ('institutional_market_summary', :source, 'exchange_daily_settled', 1,
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
                VALUES ('institutional_market_summary', :market, :source, 'TWSE',
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
             WHERE dataset_code = 'institutional_market_summary'
               AND source IN ('twse_bfi82u', 'tpex_insti_summary'))
         + (SELECT count(*) FROM import_manifests
             WHERE dataset_code = 'institutional_market_summary'
               AND source IN ('twse_bfi82u', 'tpex_insti_summary'))
         + (SELECT count(*) FROM institutional_market_summary_versions
             WHERE source IN ('twse_bfi82u', 'tpex_insti_summary'))
      INTO blocking;
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away declared institutional-summary sources',
            DETAIL = format('%s ingest runs, manifests or versions reference them',
                            blocking),
            HINT = 'history is append-only; remove the history deliberately first';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    # Only the rows this migration declared; a fixture may declare other
    # institutional_market_summary sources with runs of their own.
    for table in (
        "dataset_expected_coverage",
        "dataset_release_rules",
        "dataset_sources",
    ):
        op.execute(
            sa.text(
                f"DELETE FROM {table} "
                "WHERE dataset_code = 'institutional_market_summary' AND source IN :sources"
            ).bindparams(sa.bindparam("sources", value=SOURCES, expanding=True))
        )
    op.execute(
        sa.text(
            """
            DELETE FROM dataset_catalog
             WHERE dataset_code = 'institutional_market_summary'
               AND NOT EXISTS (
                     SELECT 1 FROM dataset_sources
                      WHERE dataset_code = 'institutional_market_summary'
                   )
            """
        )
    )
