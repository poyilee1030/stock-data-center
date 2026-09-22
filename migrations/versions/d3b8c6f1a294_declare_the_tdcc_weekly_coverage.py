"""declare the TDCC weekly coverage cadence

Revision ID: d3b8c6f1a294
Revises: c9a4e7b21d58
Create Date: 2026-09-22

Step 24-b. `dataset_expected_coverage` knew `trading_day` and `calendar_month`.
TDCC publishes once a week, so it needs a cadence of its own, and the week is
what can honestly be expected: the *date* cannot be, because it is TDCC's own
business day rather than an exchange trading day. Measured over the 376
archived weeks (audit §4.9):

```text
Fri 330   Thu 22   Sat 14   Wed 9   Tue 1
```

The Saturdays are Taiwan's make-up workdays, and the exchange did not open on
any of them — 2020-06-20, 2021-02-20 and the rest are absent from the TWSE
calendar and hold no daily prices. TDCC's custody books were open; the market
was not. Two weeks even carry two data dates (2020-W39, 2021-W07: a Friday and
the make-up Saturday after it).

So the cadence is `trading_week`: every ISO week containing at least one TWSE
trading day is expected to hold at least one snapshot, and a week the market
never opened is not expected to hold one. Over the v1 window that leaves five
closed weeks, all Lunar New Year:

```text
2021-02-08  2022-01-31  2023-01-23  2025-01-27  2026-02-16   (week starts)
```

Four of them hold no snapshot, which is why they must not count as gaps. The
fifth, 2021-W06, does hold one — 2021-02-09, a Tuesday, when TDCC was open and
the exchange was closed for the whole week. It is reported as `unexpected`
coverage rather than filtered away, the same as any other period the
expectation did not predict.

`market` is `TW`: one file covers both markets, and declaring TWSE or TPEx
would invent a split the source does not have.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3b8c6f1a294"
down_revision: str | None = "c9a4e7b21d58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "dataset_expected_coverage"
CHECK = "ck_dataset_expected_coverage_cadence_value"
NOTE = (
    "TDCC OpenData id=1-5, one whole-market file per week (audit 4.9). The "
    "data date is TDCC's own business day, which includes make-up Saturdays "
    "the exchange never opened, so the week is the unit rather than the date."
)


def upgrade() -> None:
    op.drop_constraint(op.f(CHECK), TABLE, type_="check")
    op.create_check_constraint(
        op.f(CHECK),
        TABLE,
        "cadence IN ('trading_day', 'calendar_month', 'trading_week')",
    )
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_expected_coverage
                (dataset_code, market, source, calendar_market, cadence,
                 period_column, window_start, note)
            VALUES ('tdcc_snapshot', 'TW', 'tdcc_opendata', 'TWSE',
                    'trading_week', 'snapshot_date', DATE '2020-01-02', :note)
            ON CONFLICT (dataset_code, market) DO UPDATE
               SET source = EXCLUDED.source,
                   calendar_market = EXCLUDED.calendar_market,
                   cadence = EXCLUDED.cadence,
                   period_column = EXCLUDED.period_column,
                   window_start = EXCLUDED.window_start,
                   note = EXCLUDED.note
            """
        ).bindparams(note=NOTE)
    )


GUARD = """
DO $$
DECLARE blocking bigint;
BEGIN
    SELECT count(*) INTO blocking
      FROM dataset_expected_coverage
     WHERE cadence = 'trading_week'
       AND dataset_code <> 'tdcc_snapshot';
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = format('%s other declarations use the weekly cadence',
                             blocking),
            HINT = 'Narrowing the check would leave rows no insert could '
                   'reproduce. Remove those declarations deliberately first.';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    op.execute(
        sa.text(
            "DELETE FROM dataset_expected_coverage "
            "WHERE dataset_code = 'tdcc_snapshot' AND market = 'TW'"
        )
    )
    op.drop_constraint(op.f(CHECK), TABLE, type_="check")
    op.create_check_constraint(
        op.f(CHECK), TABLE, "cadence IN ('trading_day', 'calendar_month')"
    )
