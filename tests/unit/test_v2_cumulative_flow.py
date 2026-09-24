"""Step 26-c: `institutional_cumulative_flow:v1` must reproduce legacy trust/dealer holding.

The fixture is legacy `stock_db` output for 2020: `institutional_investors`
gives the nets, `foreign_holding` the same-day issued shares, and legacy
`trust_holding` / `dealer_holding` the expected cumulative shares and ratios.
6446 has no legacy `foreign_holding` row that year, so its ratios are NULL.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.v2.cumulative_flow import PARTIES, cumulative_flows, held_ratio

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures"
    / "step26c_institutional_cumulative_flow_legacy.json"
)


@pytest.mark.parametrize(
    ("held", "issued", "expected"),
    [
        (-179000, 25930380458, -0.0007),
        (5, 1_000_000, 0.0005),
        # Legacy rounded with PostgreSQL's numeric ROUND: half away from zero.
        (1, 2_000_000, 0.0001),
        (-1, 2_000_000, -0.0001),
        (0, 1_000, 0.0),
        (7, 0, None),
        (7, None, None),
    ],
)
def test_the_ratio_is_legacy_percent_rounded_to_four_places(held, issued, expected) -> None:
    assert held_ratio(held, issued) == expected


def test_cumulative_flows_sum_from_zero_and_treat_a_missing_net_as_zero() -> None:
    assert cumulative_flows([5, None, -3, 0, 10]) == [5, 5, 2, 2, 12]
    assert cumulative_flows([]) == []


@pytest.mark.parametrize("security_code", ["2330", "8069", "6446"])
def test_values_match_legacy(security_code: str) -> None:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["securities"][security_code]
    assert rows
    for party in PARTIES:
        shares = cumulative_flows([row[f"{party}_net"] for row in rows])
        assert shares == [row[f"{party}_cumulative_net_shares"] for row in rows], party
        ratios = [held_ratio(held, row["issued_shares"]) for held, row in zip(shares, rows)]
        expected = [
            None if row[f"{party}_cumulative_net_ratio"] is None
            else float(Decimal(row[f"{party}_cumulative_net_ratio"]))
            for row in rows
        ]
        assert ratios == expected, party
