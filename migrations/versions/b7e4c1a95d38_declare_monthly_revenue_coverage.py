"""declare the monthly-revenue coverage window

Revision ID: b7e4c1a95d38
Revises: d4a7f2c9b8e1
Create Date: 2026-09-20

Step 22-b. The two sources of Step 22-a get the expected-coverage declaration
that the history backfill walks and the Step 16 validator reports against: one
row per market, `calendar_month` cadence, from 2020-01 (ROADMAP §14).

The version table keys a month as `(revenue_year, revenue_month)`, and the
declaration names one period column. `revenue_period` is that column,
generated from the two rather than written beside them: a stored copy could
disagree with the month it describes, and a coverage report built on a column
that can disagree reports the disagreement as coverage.

`calendar_market` is required by the declaration table and is meaningless for a
monthly filing — MOPS publishes revenue on a statutory day of the month, not on
a trading day. Both rows name `TWSE` because that is the only calendar the
Data Center holds; `cadence = 'calendar_month'` is what decides that no
calendar is consulted (`ExpectedCoverageService.expected_periods`).

The window has no end: the monthly pages continue, and Step 27 turns the same
declaration into forward capture jobs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4c1a95d38"
down_revision: str | None = "d4a7f2c9b8e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COVERAGE = {
    "TWSE": (
        "mops_t21sc03_sii",
        "MOPS t21sc03 sii, pages _0 and _1 (audit 4.7).",
    ),
    "TPEx": (
        "mops_t21sc03_otc",
        "MOPS t21sc03 otc, pages _0 and _1 (audit 4.7).",
    ),
}


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            ALTER TABLE monthly_revenue_versions
                ADD COLUMN revenue_period date
                GENERATED ALWAYS AS (make_date(revenue_year, revenue_month, 1))
                STORED
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_monthly_revenue_versions_period "
            "ON monthly_revenue_versions (revenue_period, source)"
        )
    )
    for market, (source, note) in COVERAGE.items():
        op.execute(
            sa.text(
                """
                INSERT INTO dataset_expected_coverage
                    (dataset_code, market, source, calendar_market, cadence,
                     period_column, window_start, note)
                VALUES ('monthly_revenue', :market, :source, 'TWSE',
                        'calendar_month', 'revenue_period', DATE '2020-01-01',
                        :note)
                ON CONFLICT (dataset_code, market) DO UPDATE
                   SET source = EXCLUDED.source,
                       calendar_market = EXCLUDED.calendar_market,
                       cadence = EXCLUDED.cadence,
                       period_column = EXCLUDED.period_column,
                       window_start = EXCLUDED.window_start,
                       note = EXCLUDED.note
                """
            ).bindparams(market=market, source=source, note=note)
        )


def downgrade() -> None:
    # Both objects are derived: the column is generated from columns that stay,
    # and the declaration is a statement of what is expected, not a record of
    # what was ingested. Dropping them loses no history (CLAUDE.md §81).
    op.execute(
        sa.text(
            "DELETE FROM dataset_expected_coverage "
            "WHERE dataset_code = 'monthly_revenue'"
        )
    )
    op.execute(sa.text("DROP INDEX IF EXISTS ix_monthly_revenue_versions_period"))
    op.execute(
        sa.text("ALTER TABLE monthly_revenue_versions DROP COLUMN revenue_period")
    )
