"""Splitting multi-row statements by the parameters they actually bind.

PostgreSQL sends the bind-parameter count as an int16, so one statement carries
at most 65,535 of them. Step 17-b hit this with 21 parameters per daily-price
row; the index writer binds a different number, which is exactly why the split
is computed from the row rather than fixed at a row count.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

MAX_BIND_PARAMETERS = 65535


def batched(rows: Sequence[dict[str, object]]) -> list[Sequence[dict[str, object]]]:
    """Split `rows` so no statement exceeds the bind-parameter ceiling.

    SQL expressions such as `statement_timestamp()` render inline and bind
    nothing, so they are not counted. Adding a column cannot quietly move the
    cliff, because the count comes from the row itself.
    """
    if not rows:
        return []
    per_row = sum(
        1 for value in rows[0].values() if not isinstance(value, sa.ClauseElement)
    )
    size = max(1, MAX_BIND_PARAMETERS // max(per_row, 1))
    return [rows[start : start + size] for start in range(0, len(rows), size)]
