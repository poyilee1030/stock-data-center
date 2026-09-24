"""`institutional_cumulative_flow:v1` — legacy trust/dealer "holding", ported.

Legacy `calculate_trust_holding.py` and `calculate_dealer_holding.py` summed
each day's net from the first day of the institutional file and called the
total a holding. Without an observed starting holding it is not one: it is a
zero-origin cumulative net flow, and its ratio to issued shares is a proxy
(`docs/institutional_financing.md`).

The ratio is legacy's to the digit: `ROUND((held / issued * 100)::numeric, 4)`,
where the division is double precision, the cast keeps fifteen significant
digits, and PostgreSQL's numeric ROUND rounds half away from zero.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from itertools import accumulate

# Trust is the investment trusts; dealer is proprietary plus hedging.
PARTIES = ("trust", "dealer")

_FOUR_PLACES = Decimal("0.0001")


def cumulative_flows(nets: Sequence[int | None]) -> list[int]:
    """Running total from zero; a missing net adds nothing."""
    return list(accumulate(net or 0 for net in nets))


def held_ratio(held: int, issued: int | None) -> float | None:
    """Cumulative net shares as a percentage of issued shares, legacy-rounded."""
    if not issued:
        return None
    percent = Decimal(format(held / issued * 100, ".15g"))
    return float(percent.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)) + 0.0
