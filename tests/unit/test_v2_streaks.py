"""Step 26-b: `institutional_streaks:v1` must reproduce the legacy streak days.

The fixture is legacy `stock_db` output: `daily_quotes` gives the days (legacy
kept only days the stock traded), `institutional_investors` the nets (a day
without a row is a day no institution traded the stock), and legacy
`technical_indicators` the expected `*_streak_days`. 1258 has 162 traded days
without an institutional row in 2020, so the missing-net rule is exercised on
real data, not only on the hand-made cases below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stock_data_center.v2.streaks import PARTIES, net_streaks

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "step26b_institutional_streaks_legacy.json"
)


@pytest.mark.parametrize(
    ("nets", "expected"),
    [
        ([], []),
        ([5, 3, 1], [1, 2, 3]),
        ([-5, -3, 2, 7, -1], [-1, -2, 1, 2, -1]),
        # A zero net is no side: the streak is 0 and the next day starts over.
        ([4, 0, 4, 4], [1, 0, 1, 2]),
        # A day no institution traded the stock is a zero net, not a gap.
        ([4, None, 4], [1, 0, 1]),
        ([None, None, -2], [0, 0, -1]),
    ],
)
def test_streak_days_follow_the_legacy_rule(nets, expected) -> None:
    assert net_streaks(nets) == expected


@pytest.mark.parametrize("security_code", ["2330", "1258"])
def test_streaks_match_legacy_values(security_code: str) -> None:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["securities"][security_code]
    assert rows
    for party in PARTIES:
        assert net_streaks([row[f"{party}_net"] for row in rows]) == [
            row[f"{party}_streak_days"] for row in rows
        ], party
