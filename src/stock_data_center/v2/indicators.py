"""`technical_indicators:v1` — the legacy price/volume formulas, ported exactly.

Legacy computed these with pandas on raw official close and volume, and the
consumers that read them were trained on those values, so the port reproduces
pandas semantics rather than improving on them:

- a rolling window containing an unpublished price yields nothing, it does not
  close the gap by skipping the day;
- the exponential averages decay across unpublished days even though those days
  contribute no observation;
- RSV over a flat nine-day window is 0/0 and resolves to the neutral 50.

Every function here is pure. Storage, PIT context and lineage belong to the
service; a formula that reached for a connection would be impossible to pin
against the legacy fixture.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

MA_WINDOWS = (5, 10, 20, 60, 120, 240)
RSI_WINDOWS = (6, 12)
RSV_WINDOW = 9
KD_ALPHA = 1.0 / 3.0
MACD_FAST_SPAN = 12
MACD_SLOW_SPAN = 26
MACD_SIGNAL_SPAN = 9
BOLLINGER_WINDOW = 20
BOLLINGER_MULTIPLE = 2.0
NEUTRAL_RSV = 50.0

METRIC_CODES: tuple[str, ...] = (
    *(f"ma{window}" for window in MA_WINDOWS),
    *(f"vma{window}" for window in MA_WINDOWS),
    "k",
    "d",
    *(f"rsi{window}" for window in RSI_WINDOWS),
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "bb_upper",
    "bb_middle",
    "bb_lower",
)


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One trading day of raw official price and volume.

    A day the exchange listed without a published price is a real row with
    `close is None`; it is not the same as a day that never traded.
    """

    trade_date: date
    high: float | None
    low: float | None
    close: float | None
    volume: float | None


@dataclass(frozen=True, slots=True)
class IndicatorRow:
    trade_date: date
    metrics: Mapping[str, float | None]


def _rolling_mean(values: Sequence[float | None], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for index in range(window - 1, len(values)):
        span = values[index - window + 1 : index + 1]
        if any(value is None for value in span):
            continue
        out[index] = math.fsum(span) / window  # type: ignore[arg-type]
    return out


def _rolling_sample_std(
    values: Sequence[float | None], window: int
) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for index in range(window - 1, len(values)):
        span = values[index - window + 1 : index + 1]
        if any(value is None for value in span):
            continue
        mean = math.fsum(span) / window  # type: ignore[arg-type]
        variance = math.fsum((value - mean) ** 2 for value in span) / (window - 1)  # type: ignore[operator]
        out[index] = math.sqrt(variance)
    return out


def _ewm(
    values: Sequence[float | None],
    *,
    alpha: float,
    adjust: bool,
    min_periods: int,
) -> list[float | None]:
    """Exponentially weighted mean with pandas' own missing-value behaviour.

    An unpublished day decays the accumulated weight without contributing an
    observation, which is what `ignore_na=False` means: the next real day
    therefore weighs more than it would if the gap had never existed.
    """
    out: list[float | None] = [None] * len(values)
    new_weight = 1.0 if adjust else alpha
    decay = 1.0 - alpha
    weighted: float | None = None
    old_weight = 1.0
    observations = 0

    for index, current in enumerate(values):
        observed = current is not None
        if observed:
            observations += 1
        if weighted is not None:
            old_weight *= decay
            if observed:
                if weighted != current:
                    weighted = (old_weight * weighted + new_weight * current) / (
                        old_weight + new_weight
                    )
                old_weight = old_weight + new_weight if adjust else 1.0
        elif observed:
            weighted = current
        if observations >= min_periods:
            out[index] = weighted
    return out


def _rsv(bars: Sequence[DailyBar]) -> list[float]:
    out: list[float] = []
    for index, bar in enumerate(bars):
        if index < RSV_WINDOW - 1:
            out.append(NEUTRAL_RSV)
            continue
        span = bars[index - RSV_WINDOW + 1 : index + 1]
        lows = [item.low for item in span]
        highs = [item.high for item in span]
        if bar.close is None or any(value is None for value in lows + highs):
            out.append(NEUTRAL_RSV)
            continue
        lowest = min(lows)  # type: ignore[type-var]
        highest = max(highs)  # type: ignore[type-var]
        if highest == lowest:
            out.append(NEUTRAL_RSV)
            continue
        out.append((bar.close - lowest) / (highest - lowest) * 100.0)
    return out


def _rsi(closes: Sequence[float | None], window: int) -> list[float | None]:
    gains: list[float | None] = [None] * len(closes)
    losses: list[float | None] = [None] * len(closes)
    for index in range(1, len(closes)):
        current = closes[index]
        previous = closes[index - 1]
        if current is None or previous is None:
            continue
        change = current - previous
        gains[index] = max(change, 0.0)
        losses[index] = max(-change, 0.0)

    alpha = 1.0 / window
    average_gain = _ewm(gains, alpha=alpha, adjust=True, min_periods=window)
    average_loss = _ewm(losses, alpha=alpha, adjust=True, min_periods=window)

    out: list[float | None] = [None] * len(closes)
    for index in range(len(closes)):
        gain = average_gain[index]
        loss = average_loss[index]
        if gain is None or loss is None:
            continue
        if loss == 0.0:
            # 0/0 is undefined, as it was in pandas; a run with no down day at
            # all is a saturated 100.
            out[index] = None if gain == 0.0 else 100.0
            continue
        out[index] = 100.0 - 100.0 / (1.0 + gain / loss)
    return out


def technical_indicators(bars: Sequence[DailyBar]) -> tuple[IndicatorRow, ...]:
    """Compute every `technical_indicators:v1` metric for one security.

    `bars` is one security's history in trade-date order, starting at the first
    day the series is allowed to know about. The warm-up is part of the value:
    the exponential metrics never forget, so a caller that trims the beginning
    gets different numbers for the same dates.
    """
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]

    metrics: dict[str, list[float | None]] = {}
    for window in MA_WINDOWS:
        metrics[f"ma{window}"] = _rolling_mean(closes, window)
        metrics[f"vma{window}"] = _rolling_mean(volumes, window)

    k_line = _ewm(_rsv(bars), alpha=KD_ALPHA, adjust=False, min_periods=1)
    metrics["k"] = k_line
    metrics["d"] = _ewm(k_line, alpha=KD_ALPHA, adjust=False, min_periods=1)

    for window in RSI_WINDOWS:
        metrics[f"rsi{window}"] = _rsi(closes, window)

    fast = _ewm(closes, alpha=2.0 / (MACD_FAST_SPAN + 1), adjust=False, min_periods=1)
    slow = _ewm(closes, alpha=2.0 / (MACD_SLOW_SPAN + 1), adjust=False, min_periods=1)
    dif: list[float | None] = [
        None if fast[i] is None or slow[i] is None else fast[i] - slow[i]
        for i in range(len(bars))
    ]
    dea = _ewm(dif, alpha=2.0 / (MACD_SIGNAL_SPAN + 1), adjust=False, min_periods=1)
    metrics["macd_dif"] = dif
    metrics["macd_dea"] = dea
    metrics["macd_hist"] = [
        None if dif[i] is None or dea[i] is None else dif[i] - dea[i]
        for i in range(len(bars))
    ]

    middle = metrics[f"ma{BOLLINGER_WINDOW}"]
    deviation = _rolling_sample_std(closes, BOLLINGER_WINDOW)
    metrics["bb_middle"] = list(middle)
    metrics["bb_upper"] = [
        None if middle[i] is None or deviation[i] is None
        else middle[i] + BOLLINGER_MULTIPLE * deviation[i]
        for i in range(len(bars))
    ]
    metrics["bb_lower"] = [
        None if middle[i] is None or deviation[i] is None
        else middle[i] - BOLLINGER_MULTIPLE * deviation[i]
        for i in range(len(bars))
    ]

    return tuple(
        IndicatorRow(
            trade_date=bar.trade_date,
            metrics={code: metrics[code][index] for code in METRIC_CODES},
        )
        for index, bar in enumerate(bars)
    )
