"""allow a margin utilization ratio above 100

Revision ID: b9d1f3a5c7e2
Revises: a4c8e2f6b1d3
Create Date: 2026-09-19

Step 21-a, found by its backfill. Step 7 capped `margin_utilization_ratio` and
`short_utilization_ratio` at 0-100 with no source behind the cap. TPEx published
103.1% for 00989B on 2026-07-14: 15,568 lots bought in one day against a
15,113-lot limit, because the stop applies from the next business day (TWSE's
note: 備註欄係表明成交日次一營業日股票融資融券狀況). A shrinking share count can
push the ratio past 100 the same way. The ratios stay non-negative.

The downgrade restores the cap only while no stored ratio exceeds it; with one
stored it refuses before touching anything (CLAUDE.md §81).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9d1f3a5c7e2"
down_revision: str | None = "a4c8e2f6b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "margin_trading_versions"
OLD = "margin_trading_ratios_percent"
NEW = "margin_trading_ratios_nonnegative"


def upgrade() -> None:
    op.drop_constraint(op.f(f"ck_{TABLE}_{OLD}"), TABLE, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_{NEW}"),
        TABLE,
        "(margin_utilization_ratio IS NULL OR margin_utilization_ratio >= 0) AND "
        "(short_utilization_ratio IS NULL OR short_utilization_ratio >= 0)",
    )


GUARD = """
DO $$
DECLARE blocking bigint;
BEGIN
    SELECT count(*) INTO blocking FROM margin_trading_versions
     WHERE margin_utilization_ratio > 100 OR short_utilization_ratio > 100;
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot restore the 0-100 utilization cap',
            DETAIL = format('%s stored versions carry a ratio above 100', blocking),
            HINT = 'history is append-only; the cap cannot hold what the source published';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    op.drop_constraint(op.f(f"ck_{TABLE}_{NEW}"), TABLE, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_{OLD}"),
        TABLE,
        "(margin_utilization_ratio IS NULL OR margin_utilization_ratio BETWEEN 0 AND 100) AND "
        "(short_utilization_ratio IS NULL OR short_utilization_ratio BETWEEN 0 AND 100)",
    )
