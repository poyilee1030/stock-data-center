"""`institutional_streaks:v1` — legacy's consecutive net-buy/net-sell days, ported.

Legacy `calculate_daily.py` counted, over the days a stock traded, how many
days in a row each party had been a net buyer (positive) or a net seller
(negative). A zero net is no side and resets the count to 0, and a traded day
without an institutional row is a zero net: the exchange lists a stock in its
institutional file only when some institution traded it.
"""

from __future__ import annotations

from collections.abc import Sequence

# The three nets legacy counted: foreign investors excluding foreign dealers,
# investment trusts, and dealers (proprietary plus hedging).
PARTIES = ("foreign", "trust", "dealer")


def net_streaks(nets: Sequence[int | None]) -> list[int]:
    """Signed run length of each day's side; None is a day nobody traded it."""
    out: list[int] = []
    streak = 0
    for net in nets:
        side = 0 if not net else (1 if net > 0 else -1)
        streak = side * (abs(streak) + 1) if side and streak * side > 0 else side
        out.append(streak)
    return out
