"""declare the official-valuation sources

Revision ID: b3d6f0a2c8e5
Revises: 9f3d7c2e5a41
Create Date: 2026-09-17

Step 18-c. Two sources, one per exchange. They also differ in field coverage:
only TPEx publishes a per-share dividend, and TPEx has no report period before
2025-01-02. A shared source code would make one logical key alternate between
field sets (CLAUDE.md §30).

Both follow `exchange_daily_settled@1`: they are exchange end-of-day tables for
a trade date, the same kind of dataset as the daily prices and indices that
already declare it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d6f0a2c8e5"
down_revision: str | None = "9f3d7c2e5a41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COVERAGE = {
    "twse_bwibbu_d": ("TWSE", "TWSE BWIBBU_d, whole market (audit 4.6)."),
    "tpex_pe_qry_date": (
        "TPEx",
        "TPEx afterTrading/peQryDate, whole market (audit 4.6).",
    ),
}
SOURCES = tuple(COVERAGE)


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('official_valuation',
                    'official exchange-published valuation ratios', 'v1')
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
                VALUES ('official_valuation', :source, true, true, 0, 'verified',
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
                VALUES ('official_valuation', :source, 'exchange_daily_settled', 1,
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
                VALUES ('official_valuation', :market, :source, 'TWSE',
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
             WHERE dataset_code = 'official_valuation'
               AND source IN ('twse_bwibbu_d', 'tpex_pe_qry_date'))
         + (SELECT count(*) FROM import_manifests
             WHERE dataset_code = 'official_valuation'
               AND source IN ('twse_bwibbu_d', 'tpex_pe_qry_date'))
         + (SELECT count(*) FROM official_valuation_versions
             WHERE source IN ('twse_bwibbu_d', 'tpex_pe_qry_date'))
      INTO blocking;
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away declared official-valuation sources',
            DETAIL = format('%s ingest runs, manifests or versions reference them',
                            blocking),
            HINT = 'history is append-only; remove the history deliberately first';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    # Only the rows this migration declared; a fixture may declare other
    # official_valuation sources with runs of their own.
    for table in (
        "dataset_expected_coverage",
        "dataset_release_rules",
        "dataset_sources",
    ):
        op.execute(
            sa.text(
                f"DELETE FROM {table} "
                "WHERE dataset_code = 'official_valuation' AND source IN :sources"
            ).bindparams(sa.bindparam("sources", value=SOURCES, expanding=True))
        )
    op.execute(
        sa.text(
            """
            DELETE FROM dataset_catalog
             WHERE dataset_code = 'official_valuation'
               AND NOT EXISTS (
                     SELECT 1 FROM dataset_sources
                      WHERE dataset_code = 'official_valuation'
                   )
            """
        )
    )
