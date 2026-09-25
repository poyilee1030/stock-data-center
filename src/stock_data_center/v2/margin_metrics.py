"""`margin_metrics:v1` and `short_interest_metrics:v1` — legacy's margin and SBL ratios, ported.

Legacy `calculate_margin_pressure_analysis.py` and
`calculate_short_interest_analysis.py` derive every metric from one day's row:
a usage ratio is the balance over the limit, a change is the balance minus the
previous balance the source publishes on the same row, and the short-cover
pressure is the day's short covering (buy plus stock repayment) over the
previous short balance. Legacy called the changes `_wow`; they are daily, so
here they are `_change` (owner, 2026-09-25).

Not ported: the composite pressure scores, which are downstream (ROADMAP Step
26); the balances, limits and SBL flows legacy copied through, which are
observed values in `margin_trading` and `securities_lending`; and legacy's
second copy of the short-sale change in the SBL table, which `margin_metrics`
already holds.

A ratio is legacy's to the digit: computed in double precision in legacy's
order, cast to numeric (fifteen significant digits), and rounded to four
places half away from zero. Legacy kept margin in lots and we keep shares; a
ratio of the same two integers each times 1,000 is the same double. A change
is an exact share count. Where legacy wrote `COALESCE(x, 0)`, an input the
source did not publish leaves what needs it NULL: it never fired on legacy's
own data.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

MARGIN_METRICS = (
    "margin_usage_ratio",
    "margin_balance_change",
    "margin_balance_change_pct",
    "short_usage_ratio",
    "short_balance_change",
    "short_balance_change_pct",
    "short_cover_pressure",
)
SHORT_INTEREST_METRICS = (
    "sbl_balance_change",
    "sbl_balance_change_pct",
    "sbl_sell_repay_ratio",
)
# The input columns each dataset reads.
MARGIN_INPUTS = (
    "margin_previous_balance", "margin_balance", "margin_limit", "short_buy",
    "short_stock_repayment", "short_previous_balance", "short_balance", "short_limit",
)
SHORT_INTEREST_INPUTS = ("previous_balance", "sold", "returned", "balance")

_FOUR_PLACES = Decimal("0.0001")


def _rounded(value: float) -> float:
    exact = Decimal(format(value, ".15g"))
    return float(exact.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)) + 0.0


def _ratio(numerator: int | None, denominator: int | None, scale: int = 100) -> float | None:
    """numerator / denominator * scale, legacy-rounded; NULL unless the denominator is positive."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return _rounded(numerator / denominator * scale)


def _change(balance: int | None, previous: int | None) -> int | None:
    return None if balance is None or previous is None else balance - previous


def _sum(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else a + b


def margin_metrics(row: Mapping) -> dict:
    """One `margin_trading` row's metrics; every count is in shares."""
    out = {"margin_usage_ratio": _ratio(row["margin_balance"], row["margin_limit"])}
    for side in ("margin", "short"):
        change = _change(row[f"{side}_balance"], row[f"{side}_previous_balance"])
        out[f"{side}_balance_change"] = change
        out[f"{side}_balance_change_pct"] = _ratio(change, row[f"{side}_previous_balance"])
        if side == "margin":
            out["short_usage_ratio"] = _ratio(row["short_balance"], row["short_limit"])
    out["short_cover_pressure"] = _ratio(
        _sum(row["short_buy"], row["short_stock_repayment"]), row["short_previous_balance"])
    return {metric: out[metric] for metric in MARGIN_METRICS}


def short_interest_metrics(row: Mapping) -> dict:
    """One `securities_lending` row's metrics; every count is in shares."""
    change = _change(row["balance"], row["previous_balance"])
    return {
        "sbl_balance_change": change,
        "sbl_balance_change_pct": _ratio(change, row["previous_balance"]),
        "sbl_sell_repay_ratio": _ratio(row["sold"], row["returned"], scale=1),
    }
