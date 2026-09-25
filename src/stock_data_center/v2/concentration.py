"""`shareholding_concentration:v1` — legacy's TDCC holder groups, ported.

Legacy `calculate_shareholding_concentration.py` split each week's fifteen
holding levels into small holders (levels 1-8, at most 50 lots), mid (9-11, up
to 400) and large (12-15, more), summed each group's percentage of issued
shares and the small and large groups' holder counts, and measured each ratio
and the large-minus-small spread against the stock's previous snapshot.

Legacy summed in double precision and stored `ROUND(x::numeric, 4)`. TDCC
publishes two-place percentages, so the exact decimal sum is what that comes
back to (`test_exact_sums_equal_legacys_double_precision_ones`); the sums here
are exact and rounded the same way. Where legacy wrote `COALESCE(x, 0)`, a
level the source did not publish leaves what needs it NULL: it never fired on
legacy's own data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal

SMALL_LEVELS = range(1, 9)
MID_LEVELS = range(9, 12)
LARGE_LEVELS = range(12, 16)
GROUPS = {"large": LARGE_LEVELS, "mid": MID_LEVELS, "small": SMALL_LEVELS}
COUNTED = ("large", "small")

METRICS = (
    *(f"{group}_holder_ratio" for group in GROUPS),
    "concentration_spread",
    *(f"{group}_holder_count" for group in COUNTED),
    *(f"{group}_holder_ratio_wow" for group in GROUPS),
    "concentration_spread_wow",
)

_FOUR_PLACES = Decimal("0.0001")


def _sum(snapshot: Mapping, column: str, levels: range):
    values = [snapshot[f"{column}_{level}"] for level in levels]
    return None if any(v is None for v in values) else sum(values)


def _difference(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None or b is None else a - b


def _ratio(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)) + 0.0


def concentration(snapshots: Sequence[Mapping]) -> list[dict]:
    """One stock's metrics per snapshot, in date order.

    A snapshot maps `holders_<level>` and `percent_<level>` (a Decimal) for the
    fifteen holding levels. A change is against the snapshot before it in the
    sequence, however many weeks back that is, as legacy's LAG was; the first
    has none."""
    out: list[dict] = []
    previous: dict[str, Decimal | None] | None = None
    for snapshot in snapshots:
        exact: dict[str, Decimal | None] = {
            group: _sum(snapshot, "percent", levels) for group, levels in GROUPS.items()
        }
        exact["spread"] = _difference(exact["large"], exact["small"])
        row: dict = {f"{group}_holder_ratio": _ratio(exact[group]) for group in GROUPS}
        row["concentration_spread"] = _ratio(exact["spread"])
        for group in COUNTED:
            row[f"{group}_holder_count"] = _sum(snapshot, "holders", GROUPS[group])
        for name in (*GROUPS, "spread"):
            change = None if previous is None else _difference(exact[name], previous[name])
            column = "concentration_spread" if name == "spread" else f"{name}_holder_ratio"
            row[f"{column}_wow"] = _ratio(change)
        out.append({metric: row[metric] for metric in METRICS})
        previous = exact
    return out
