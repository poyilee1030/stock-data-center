"""declare the market-index sources

Revision ID: 7a2c9e4d1b58
Revises: 5e3b8d1a9c42
Create Date: 2026-09-16

Step 18-b. Three sources for one dataset, because they have different field
coverage: the two whole-list feeds publish closes and changes, and
`MI_5MINS_HIST` publishes OHLC for one index and no change columns at all.
Sharing a source code would make one logical key alternate revisions between
two field sets (CLAUDE.md §30).

Expected coverage is declared for the two whole-list feeds only. They are what
makes a trade date covered; the TAIEX feed supplements one index with OHLC and
would otherwise contend for the same `(dataset_code, market)` declaration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7a2c9e4d1b58"
down_revision: str | None = "5e3b8d1a9c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCES = ("twse_mi_index", "tpex_index_summary", "twse_mi_5mins_hist")
COVERAGE = {
    "twse_mi_index": ("TWSE", "TWSE MI_INDEX index sections (audit 4.2)."),
    "tpex_index_summary": (
        "TPEx",
        "TPEx afterTrading/indexSummary, price and return sections (audit 4.2).",
    ),
}


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('market_index',
                    'official market index closes and levels', 'v1')
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
                VALUES ('market_index', :source, true, true, 0, 'verified',
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
                VALUES ('market_index', :source, 'exchange_daily_settled', 1,
                        'Trade date D resolves at 03:00 on D+1 (ADR-0020 §3).')
                ON CONFLICT (dataset_code, source) DO NOTHING
                """
            ).bindparams(source=source)
        )
    for source, (market, note) in COVERAGE.items():
        op.execute(
            sa.text(
                """
                INSERT INTO dataset_expected_coverage
                    (dataset_code, market, source, calendar_market, cadence,
                     period_column, window_start, note)
                VALUES ('market_index', :market, :source, 'TWSE', 'trading_day',
                        'trade_date', DATE '2020-01-02', :note)
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
DECLARE imported bigint;
BEGIN
    SELECT count(*) INTO imported
      FROM market_index_versions
     WHERE source IN ('twse_mi_index', 'tpex_index_summary', 'twse_mi_5mins_hist');
    IF imported > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away declared market-index sources',
            DETAIL = format('%s imported versions reference them', imported),
            HINT = 'history is append-only; remove the history deliberately first';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    # Only the rows this migration declared. `market_index` may carry sources
    # another migration or a fixture created, and deleting those would break
    # the ingest runs referencing them.
    sources = sa.bindparam("sources", value=SOURCES, expanding=True)
    op.execute(
        sa.text(
            "DELETE FROM dataset_expected_coverage "
            "WHERE dataset_code = 'market_index' AND source IN :sources"
        ).bindparams(sources)
    )
    op.execute(
        sa.text(
            "DELETE FROM dataset_release_rules "
            "WHERE dataset_code = 'market_index' AND source IN :sources"
        ).bindparams(
            sa.bindparam("sources", value=SOURCES, expanding=True)
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM dataset_sources "
            "WHERE dataset_code = 'market_index' AND source IN :sources"
        ).bindparams(
            sa.bindparam("sources", value=SOURCES, expanding=True)
        )
    )
