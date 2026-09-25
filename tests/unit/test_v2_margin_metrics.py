"""Step 26-e: `margin_metrics:v1` and `short_interest_metrics:v1` must reproduce legacy.

The fixture is legacy `stock_db` output from 2020-01-02 to 2020-06-30. Legacy
`margin_trading` is in lots of 1,000 shares and ours in shares, so its inputs
and share changes are multiplied by 1,000 here; `margin_sbl` is in shares like
ours. 1213 has a zero margin limit every day, so its usage ratios are NULL.

Legacy called every change `_wow`; each is today's balance minus the previous
balance the source publishes on the same row, a daily change, so ours is
`_change` (owner, 2026-09-25).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.v2.margin_metrics import (
    MARGIN_METRICS,
    SHORT_INTEREST_METRICS,
    margin_metrics,
    short_interest_metrics,
)

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "step26e_margin_short_interest_legacy.json"
)
LOT = 1000

# Legacy column -> ours, and whether it is a share count legacy kept in lots.
MARGIN_COLUMNS = {
    "margin_usage_ratio": ("margin_usage_ratio", False),
    "margin_long_balance_wow": ("margin_balance_change", True),
    "margin_long_balance_wow_pct": ("margin_balance_change_pct", False),
    "short_usage_ratio": ("short_usage_ratio", False),
    "margin_short_balance_wow": ("short_balance_change", True),
    "margin_short_balance_wow_pct": ("short_balance_change_pct", False),
    "short_cover_pressure": ("short_cover_pressure", False),
}
SHORT_INTEREST_COLUMNS = {
    "sbl_balance_wow": ("sbl_balance_change", False),
    "sbl_balance_wow_pct": ("sbl_balance_change_pct", False),
    "sbl_sell_repay_ratio": ("sbl_sell_repay_ratio", False),
}


def _legacy() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _expected(row: dict, columns: dict) -> dict:
    out = {}
    for legacy, (ours, in_lots) in columns.items():
        value = row[legacy]
        if value is None:
            out[ours] = None
        elif ours.endswith("_change"):
            out[ours] = int(Decimal(value) * (LOT if in_lots else 1))
        else:
            out[ours] = float(Decimal(value))
    return out


def _margin_row(row: dict) -> dict:
    return {
        "margin_previous_balance": row["margin_long_prev_balance"] * LOT,
        "margin_balance": row["margin_long_balance"] * LOT,
        "margin_limit": row["margin_long_limit"] * LOT,
        "short_buy": row["margin_short_buy"] * LOT,
        "short_stock_repayment": row["margin_short_cash_repay"] * LOT,
        "short_previous_balance": row["margin_short_prev_balance"] * LOT,
        "short_balance": row["margin_short_balance"] * LOT,
        "short_limit": row["margin_short_limit"] * LOT,
    }


def _lending_row(row: dict) -> dict:
    return {
        "previous_balance": row["sbl_prev_balance"],
        "sold": row["sbl_sell"],
        "returned": row["sbl_repay"],
        "balance": row["sbl_balance"],
    }


def test_the_metrics_are_legacys_less_the_scores_and_the_copied_inputs() -> None:
    # The composite pressure scores are downstream (ROADMAP Step 26); balances,
    # limits and SBL flows are observed and stay in their own tables; the SBL
    # dataset leaves the short-sale change to margin_metrics (owner, 2026-09-25).
    assert MARGIN_METRICS == tuple(ours for ours, _ in MARGIN_COLUMNS.values())
    assert SHORT_INTEREST_METRICS == tuple(ours for ours, _ in SHORT_INTEREST_COLUMNS.values())


@pytest.mark.parametrize("security_code", ["2330", "8069", "1213"])
def test_margin_metrics_match_legacy(security_code: str) -> None:
    rows = _legacy()["margin"][security_code]
    assert rows
    assert [margin_metrics(_margin_row(r)) for r in rows] == [
        _expected(r, MARGIN_COLUMNS) for r in rows]


@pytest.mark.parametrize("security_code", ["2330", "8069", "1213"])
def test_short_interest_metrics_match_legacy(security_code: str) -> None:
    rows = _legacy()["short_interest"][security_code]
    assert rows
    assert [short_interest_metrics(_lending_row(r)) for r in rows] == [
        _expected(r, SHORT_INTEREST_COLUMNS) for r in rows]


def _margin(**overrides) -> dict:
    row = {
        "margin_previous_balance": 1_000, "margin_balance": 1_500, "margin_limit": 10_000,
        "short_buy": 30, "short_stock_repayment": 10, "short_previous_balance": 200,
        "short_balance": 150, "short_limit": 10_000,
    }
    return {**row, **overrides}


def test_margin_formulas() -> None:
    assert margin_metrics(_margin()) == {
        "margin_usage_ratio": 15.0,
        "margin_balance_change": 500,
        "margin_balance_change_pct": 50.0,
        "short_usage_ratio": 1.5,
        "short_balance_change": -50,
        "short_balance_change_pct": -25.0,
        "short_cover_pressure": 20.0,
    }


def test_a_ratio_rounds_half_away_from_zero_as_legacys_numeric_round() -> None:
    # Legacy: ROUND((double)::numeric, 4). The cast keeps fifteen significant
    # digits, so 5 / 2,000,000 * 100 is exactly 0.00025, and numeric ROUND
    # takes it away from zero where banker's rounding would give 0.0002.
    assert margin_metrics(_margin(margin_balance=5, margin_limit=2_000_000))[
        "margin_usage_ratio"] == 0.0003
    assert margin_metrics(_margin(margin_balance=1, margin_limit=2_000_000))[
        "margin_usage_ratio"] == 0.0001
    assert margin_metrics(_margin(margin_balance=2_000_000 - 5,
                                  margin_previous_balance=2_000_000))[
        "margin_balance_change_pct"] == -0.0003


def test_a_zero_denominator_has_no_ratio() -> None:
    row = _margin(margin_limit=0, short_limit=0, margin_previous_balance=0,
                  short_previous_balance=0)
    metrics = margin_metrics(row)
    for name in ("margin_usage_ratio", "short_usage_ratio", "margin_balance_change_pct",
                 "short_balance_change_pct", "short_cover_pressure"):
        assert metrics[name] is None, name
    assert metrics["margin_balance_change"] == 1_500


def test_an_input_the_source_did_not_publish_leaves_what_needs_it_null() -> None:
    # Legacy's COALESCE(x, 0) never fired on its own data (no NULL input);
    # an unpublished value is not a zero (CLAUDE.md source-field rule).
    metrics = margin_metrics(_margin(short_buy=None, margin_limit=None))
    assert metrics["short_cover_pressure"] is None
    assert metrics["margin_usage_ratio"] is None
    assert metrics["margin_balance_change"] == 500
    lending = short_interest_metrics({"previous_balance": None, "sold": 5, "returned": 2,
                                      "balance": 7})
    assert lending == {"sbl_balance_change": None, "sbl_balance_change_pct": None,
                       "sbl_sell_repay_ratio": 2.5}


def test_short_interest_formulas() -> None:
    assert short_interest_metrics(
        {"previous_balance": 4_000, "sold": 900, "returned": 400, "balance": 4_500}) == {
        "sbl_balance_change": 500,
        "sbl_balance_change_pct": 12.5,
        "sbl_sell_repay_ratio": 2.25,
    }
    assert short_interest_metrics(
        {"previous_balance": 0, "sold": 900, "returned": 0, "balance": 900}) == {
        "sbl_balance_change": 900,
        "sbl_balance_change_pct": None,
        "sbl_sell_repay_ratio": None,
    }
