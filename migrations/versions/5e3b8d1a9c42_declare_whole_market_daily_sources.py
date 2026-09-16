"""declare the whole-market daily-price sources

Revision ID: 5e3b8d1a9c42
Revises: 4d9f2a6c8b17
Create Date: 2026-09-16

Step 17-a. The two whole-market endpoints are their own sources, distinct from
the Step 9 per-security pilots, because they publish the disclosed bid/ask level
the pilots do not: one logical key must not alternate revisions between two
field sets (CLAUDE.md §30).

Opting a source in is configuration, as Step 15-c established: the accepted
evidence types and the release rule are rows, not code. The expected-coverage
declaration lands here too, next to the adapter that fills it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e3b8d1a9c42"
down_revision: str | None = "4d9f2a6c8b17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SOURCES = {
    "twse_mi_index": (
        "TWSE",
        "TWSE MI_INDEX?type=ALLBUT0999, stock section (audit 4.1).",
    ),
    "tpex_otc_quotes": (
        "TPEx",
        "TPEx afterTrading/otc, the feed the audit calls stk_wn1430 (audit 4.1).",
    ),
}


def upgrade() -> None:
    for source, (market, note) in SOURCES.items():
        op.execute(
            sa.text(
                """
                INSERT INTO dataset_sources
                    (dataset_code, source, supports_market_pit, supports_system_pit,
                     publication_time_quality, evidence_status,
                     accepted_evidence_types, is_canonical)
                VALUES ('daily_price', :source, true, true, 0, 'verified',
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
                VALUES ('daily_price', :source, 'exchange_daily_settled', 1,
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
                VALUES ('daily_price', :market, :source, 'TWSE', 'trading_day',
                        'trade_date', DATE '2020-01-02', :note)
                ON CONFLICT (dataset_code, market) DO NOTHING
                """
            ).bindparams(source=source, market=market, note=note)
        )


GUARD = """
DO $$
DECLARE imported bigint;
BEGIN
    SELECT count(*) INTO imported
      FROM daily_price_versions
     WHERE source IN ('twse_mi_index', 'tpex_otc_quotes');
    IF imported > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away declared whole-market price sources',
            DETAIL = format('%s imported versions reference them', imported),
            HINT = 'history is append-only; remove the history deliberately first';
    END IF;
END $$;
"""


def downgrade() -> None:
    # Refuse before mutating rather than orphan imported history from the
    # source policy that makes it resolvable.
    op.execute(sa.text(GUARD))
    sources = tuple(SOURCES)
    op.execute(
        sa.text(
            "DELETE FROM dataset_expected_coverage "
            "WHERE dataset_code = 'daily_price' AND source IN :sources"
        ).bindparams(sa.bindparam("sources", value=sources, expanding=True))
    )
    op.execute(
        sa.text(
            "DELETE FROM dataset_release_rules "
            "WHERE dataset_code = 'daily_price' AND source IN :sources"
        ).bindparams(sa.bindparam("sources", value=sources, expanding=True))
    )
    op.execute(
        sa.text(
            "DELETE FROM dataset_sources "
            "WHERE dataset_code = 'daily_price' AND source IN :sources"
        ).bindparams(sa.bindparam("sources", value=sources, expanding=True))
    )
