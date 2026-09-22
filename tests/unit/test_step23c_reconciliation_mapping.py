"""Step 23-c — how a legacy `*_xbrl` row lines up with one of our facts.

These are regressions for two things the first real run got wrong, both found
by running the reconciliation against `stock_db` rather than by reading it:

* legacy files one duration under two labels in Q1, because the single quarter
  *is* the year to date. Matching a Q1 fact only as `accumulated` left 57,000
  of legacy's `quarter` rows reported as missing.
* the cash-flow statement is year-to-date only in legacy (`period_type` has
  exactly one value in that table), so emitting a `quarter` key for it invents
  84,000 rows legacy never had.

The unit multipliers are the third: legacy stored the printed number and left
the 仟元 multiplier to its consumers, so only a TWD amount is ×1,000.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "reconcile_financial_statements.py"
)
spec = importlib.util.spec_from_file_location("reconcile_financials", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def labels(statement: str, period_start: date, quarter: str) -> tuple[str, ...]:
    """What `our_quarter` would key this fact under, without a database."""
    ends = module.quarter_end(quarter)
    year_start = date(ends.year, 1, 1)
    starts = module.quarter_start(quarter)
    if statement == "balance_sheet":
        return ("as_of",)
    if statement == "cash_flow":
        return ("accumulated",) if period_start == year_start else ()
    return tuple(
        name
        for name, start in (("accumulated", year_start), ("quarter", starts))
        if period_start == start
    )


def test_a_q1_income_duration_answers_to_both_labels() -> None:
    assert labels("income_statement", date(2025, 1, 1), "2025Q1") == (
        "accumulated",
        "quarter",
    )


def test_a_q3_income_statement_keeps_its_two_durations_apart() -> None:
    assert labels("income_statement", date(2025, 1, 1), "2025Q3") == (
        "accumulated",
    )
    assert labels("income_statement", date(2025, 7, 1), "2025Q3") == ("quarter",)


def test_a_prior_year_comparative_matches_no_label() -> None:
    assert labels("income_statement", date(2024, 1, 1), "2025Q1") == ()


def test_the_cash_flow_statement_is_year_to_date_only() -> None:
    assert labels("cash_flow", date(2025, 1, 1), "2025Q1") == ("accumulated",)
    assert labels("cash_flow", date(2025, 7, 1), "2025Q3") == ()


@pytest.mark.parametrize(
    ("unit", "legacy", "ours"),
    [
        # The statements print 仟元 with scale="3"; we store TWD.
        ("iso4217:TWD", Decimal("89680417"), Decimal("89680417000")),
        # Share counts and EPS are printed as filed.
        ("xbrli:shares", Decimal("1234567"), Decimal("1234567")),
        ("iso4217:TWD/xbrli:shares", Decimal("0.07"), Decimal("0.07")),
    ],
)
def test_the_multiplier_is_the_unit_not_the_table(unit, legacy, ours) -> None:
    assert (
        module.compare_values(
            unit=unit, mine=ours, theirs=legacy, account_code="1100"
        )
        is None
    )


def test_a_real_difference_is_not_absorbed_by_the_tolerance() -> None:
    assert (
        module.compare_values(
            unit="iso4217:TWD",
            mine=Decimal("89680418000"),
            theirs=Decimal("89680417"),
            account_code="1100",
        )
        == "value_differs"
    )


def test_an_unknown_unit_is_named_rather_than_guessed() -> None:
    assert module.compare_values(
        unit="xbrli:pure", mine=Decimal(1), theirs=Decimal(1), account_code="X"
    ) == "unknown_unit:xbrli:pure"


def test_the_quarter_walk_covers_the_v1_window() -> None:
    window = module.quarters("2020Q1", "2026Q2")
    assert window[0] == "2020Q1"
    assert window[-1] == "2026Q2"
    assert len(window) == 26
