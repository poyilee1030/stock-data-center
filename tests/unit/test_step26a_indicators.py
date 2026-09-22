"""Step 26-a: the ported technical-indicator formulas must reproduce legacy output.

The fixture is real legacy `stock_db` output, not a value this repository
produced: `daily_quotes` supplies the inputs and `technical_indicators` the
expected results, for the window that begins at the legacy series start so the
exponential warm-up is exact. Growing the test from the producer's own numbers
is the point — a fixture regenerated from our implementation would inherit
whatever the port got wrong.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from stock_data_center.derived.indicators import DailyBar, technical_indicators

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "step26a_technical_indicators_legacy.json"
)

# Legacy stored DOUBLE PRECISION computed by pandas; a faithful scalar port
# differs only by float association order.
TOLERANCE = 1e-9


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _bars(rows: list[dict]) -> tuple[DailyBar, ...]:
    return tuple(
        DailyBar(
            trade_date=date.fromisoformat(row["date"]),
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
        )
        for row in rows
    )


@pytest.mark.parametrize("security_code", ["2330", "1418"])
def test_indicators_match_legacy_values(security_code: str) -> None:
    fixture = _fixture()
    columns = fixture["columns"]
    payload = fixture["securities"][security_code]
    computed = technical_indicators(_bars(payload["input"]))

    assert len(computed) == len(payload["input"])

    compared = 0
    for row in computed:
        expected = payload["expected"].get(row.trade_date.isoformat())
        assert expected is not None
        for metric_code, legacy_value in zip(columns, expected, strict=True):
            actual = row.metrics[metric_code]
            if legacy_value is None:
                assert actual is None, f"{metric_code} @ {row.trade_date}"
                continue
            assert actual is not None, f"{metric_code} @ {row.trade_date}"
            assert abs(actual - legacy_value) <= TOLERANCE, (
                f"{metric_code} @ {row.trade_date}: {actual} != {legacy_value}"
            )
            compared += 1

    assert compared > 3000


def test_every_metric_code_is_covered_by_the_fixture() -> None:
    """The port must not quietly add or drop a metric the legacy table has."""
    fixture = _fixture()
    payload = fixture["securities"]["2330"]
    computed = technical_indicators(_bars(payload["input"]))
    assert set(computed[0].metrics) == set(fixture["columns"])


def test_a_null_price_propagates_instead_of_being_skipped() -> None:
    """1418 has trading days with volume but no published OHLC.

    pandas leaves every rolling window touching such a day empty; the port must
    do the same rather than treating the day as absent and closing the gap.
    """
    fixture = _fixture()
    payload = fixture["securities"]["1418"]
    rows = payload["input"]
    null_dates = {row["date"] for row in rows if row["close"] is None}
    assert null_dates, "fixture no longer covers the null-price case"

    computed = {
        row.trade_date.isoformat(): row for row in technical_indicators(_bars(rows))
    }
    for null_date in sorted(null_dates):
        metrics = computed[null_date].metrics
        assert metrics["ma5"] is None
        assert metrics["bb_upper"] is None
    # Volume is published on those days, so the volume series is unaffected
    # once it is past its own warm-up.
    assert any(
        computed[null_date].metrics["vma5"] is not None for null_date in null_dates
    )


def test_a_flat_nine_day_window_gives_a_neutral_rsv() -> None:
    """1418 2020-02-27 closes a window whose high equals its low.

    RSV is 0/0 there. Legacy filled it with 50, and K/D inherit that; a port
    that let the division produce NaN or raise would silently change the series.
    """
    fixture = _fixture()
    payload = fixture["securities"]["1418"]
    computed = {row.trade_date.isoformat(): row for row in technical_indicators(_bars(payload["input"]))}
    legacy = payload["expected"]["2020-02-27"]
    columns = fixture["columns"]
    assert computed["2020-02-27"].metrics["k"] == pytest.approx(
        legacy[columns.index("k")], abs=TOLERANCE
    )
