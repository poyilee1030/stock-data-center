"""Step 26-d: `shareholding_concentration:v1` must reproduce legacy's table.

The fixture is legacy `stock_db` output from 2020-01-03 to 2022-06-30:
`shareholding` gives each week's holders and percentage for levels 1-15, and
`shareholding_concentration` the expected ratios, counts and week-over-week
changes. It spans legacy's 2021-11-26 -> 2021-12-24 gap, where a change is
measured against the snapshot before the gap, and 6781's first snapshot, whose
changes are NULL.
"""

from __future__ import annotations

import json
import random
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from stock_data_center.v2.concentration import (
    LARGE_LEVELS,
    METRICS,
    MID_LEVELS,
    SMALL_LEVELS,
    concentration,
)

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures"
    / "step26d_shareholding_concentration_legacy.json"
)


def _snapshot(row: dict) -> dict:
    return {
        **{f"holders_{level}": row[f"holders_{level}"] for level in range(1, 16)},
        **{f"percent_{level}": Decimal(row[f"percent_{level}"]) for level in range(1, 16)},
    }


def _expected(row: dict) -> dict:
    # Every fixture column that is not an input, so the expectation does not
    # come from the module under test.
    return {
        name: value if value is None or name.endswith("_count") else float(Decimal(value))
        for name, value in row.items()
        if name != "date" and not name.startswith(("holders_", "percent_"))
    }


def test_the_groups_are_legacys_levels() -> None:
    # Levels 1-8 hold at most 50 lots, 9-11 up to 400, 12-15 more.
    assert (tuple(SMALL_LEVELS), tuple(MID_LEVELS), tuple(LARGE_LEVELS)) == (
        tuple(range(1, 9)), (9, 10, 11), (12, 13, 14, 15))


def test_the_metrics_are_legacys_columns() -> None:
    assert METRICS == (
        "large_holder_ratio", "mid_holder_ratio", "small_holder_ratio",
        "concentration_spread", "large_holder_count", "small_holder_count",
        "large_holder_ratio_wow", "mid_holder_ratio_wow", "small_holder_ratio_wow",
        "concentration_spread_wow",
    )


@pytest.mark.parametrize("security_code", ["2330", "8069", "6781"])
def test_values_match_legacy(security_code: str) -> None:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["securities"][security_code]
    assert rows
    assert concentration([_snapshot(row) for row in rows]) == [_expected(row) for row in rows]


def _legacy(percents: list[float]) -> float:
    """Legacy's path: a double-precision SUM, cast to numeric (fifteen
    significant digits), ROUND to four places half away from zero."""
    total = sum(percents)
    return float(Decimal(format(total, ".15g")).quantize(Decimal("0.0001"),
                                                         rounding=ROUND_HALF_UP))


def test_exact_sums_equal_legacys_double_precision_ones() -> None:
    # TDCC percentages have two places, so the exact decimal sum is what
    # legacy's rounded double sum comes back to.
    generator = random.Random(26)
    for _ in range(5_000):
        snapshot = {f"holders_{level}": 1 for level in range(1, 16)} | {
            f"percent_{level}": Decimal(generator.randint(0, 13500)) / 100
            for level in range(1, 16)}
        [row] = concentration([snapshot])
        for group, levels in (("large", LARGE_LEVELS), ("mid", MID_LEVELS),
                              ("small", SMALL_LEVELS)):
            assert row[f"{group}_holder_ratio"] == _legacy(
                [float(snapshot[f"percent_{level}"]) for level in levels]), group


def _levels(percent: str = "1.00", holders: int = 10) -> dict:
    return {
        **{f"holders_{level}": holders for level in range(1, 16)},
        **{f"percent_{level}": Decimal(percent) for level in range(1, 16)},
    }


def test_the_first_snapshot_has_no_change_and_later_ones_measure_the_previous() -> None:
    first, second = _levels("1.00"), _levels("1.50")
    second["percent_15"] = Decimal("80.25")
    rows = concentration([first, second])
    assert rows[0]["large_holder_ratio"] == 4.0
    assert rows[0]["mid_holder_ratio"] == 3.0
    assert rows[0]["small_holder_ratio"] == 8.0
    assert rows[0]["concentration_spread"] == -4.0
    assert (rows[0]["large_holder_count"], rows[0]["small_holder_count"]) == (40, 80)
    assert all(rows[0][m] is None for m in METRICS if m.endswith("_wow"))
    assert rows[1]["large_holder_ratio"] == 84.75
    assert rows[1]["large_holder_ratio_wow"] == 80.75
    assert rows[1]["small_holder_ratio_wow"] == 4.0
    assert rows[1]["mid_holder_ratio_wow"] == 1.5
    assert rows[1]["concentration_spread_wow"] == 76.75


def test_a_missing_level_leaves_its_group_unknown_not_zero() -> None:
    # A value the source did not publish is not data (CLAUDE.md source-field
    # rule): legacy's COALESCE(…, 0) never fired on its own data, and here the
    # group, the spread and the changes that need it stay NULL.
    first, second = _levels(), _levels()
    second["percent_13"] = None
    second["holders_2"] = None
    rows = concentration([first, second, _levels()])
    assert rows[1]["large_holder_ratio"] is None
    assert rows[1]["concentration_spread"] is None
    assert rows[1]["large_holder_ratio_wow"] is None
    assert rows[1]["concentration_spread_wow"] is None
    assert rows[1]["small_holder_ratio"] == 8.0
    assert rows[1]["small_holder_count"] is None
    assert rows[1]["large_holder_count"] == 40
    assert rows[2]["large_holder_ratio_wow"] is None
    assert rows[2]["small_holder_ratio_wow"] == 0.0


def test_no_value_changes_with_a_later_snapshot() -> None:
    snapshots = [_levels(p) for p in ("1.00", "1.10", "0.90", "1.30")]
    full = concentration(snapshots)
    assert full[-1]["large_holder_ratio_wow"] == 1.6
    for end in range(1, len(snapshots)):
        assert concentration(snapshots[:end]) == full[:end]
