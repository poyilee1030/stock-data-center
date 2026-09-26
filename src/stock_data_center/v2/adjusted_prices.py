"""`adjusted_prices_pit:v1`: adjusted prices computed on demand under full PIT (Step 36).

ROADMAP §18 and CLAUDE.md §80: every exchange result event on ex-date D — an
ex-right or ex-dividend date, or the resumption after a capital reduction or a
par-value change — has

    factor(D) = reference_price(D) / close_before(D)

and a price on date t is multiplied by the product of the factors of the
events after t, up to the series' last price. The reference price already has
the cash dividend taken out, so the series is total-return style: dividends
reinvested. Raw prices are never changed (§51.1); they are returned beside the
adjusted ones.

Adjusted prices keep PIT corporate-action visibility (§43, §51.3), so nothing
is stored: both inputs are read through `visibility.rows` under the caller's
context. An event adjusts the series only where that context sees it, and the
series is anchored at the last price the context sees, so a value never
depends on the window it was asked in, and an event whose ex-date price is not
yet public adjusts nothing. An event whose factor is unknown leaves every
earlier adjusted price unknown rather than silently unadjusted.

One series per daily-price source (§30): a result feed lists the executions of
its own exchange, so it adjusts that exchange's prices only. A stock that moved
from TPEx to TWSE has two series, neither adjusted by the other's events.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.schema_v2 import daily_prices, stocks
from stock_data_center.v2 import visibility
from stock_data_center.v2.derived import Definition, UnknownStockError

ADJUSTED_PRICES_PIT_V1 = Definition(
    dataset_code="adjusted_prices_pit",
    derivation_version="v1",
    formula_specification=(
        "factor(D) = reference_price / close_before of each exchange result event on "
        "ex-date D (ex-right, ex-dividend, resumption after a capital reduction or a "
        "par-value change). The cumulative factor of trade date t is the product of the "
        "factors of the visible events with t < D <= the last visible trade date of the "
        "series; adjusted open, high, low and close are the raw ones times it. An event "
        "with no reference price or close before leaves the cumulative factor of every "
        "earlier date unknown. Volume is not adjusted."
    ),
    input_tables=("daily_prices", "corporate_actions"),
    calendar_timezone="Asia/Taipei",
    calendar_convention=(
        "Trade dates of the stock's own daily-price history from one source. Events come "
        "from the result feeds of the same exchange. An event is visible under "
        "corporate_action_ex_date@1 (00:00 Asia/Taipei on the ex-date), a price under "
        "exchange_daily_settled@1, each under the caller's PIT context."
    ),
    price_adjustment_convention=(
        "backward, total-return: the exchange reference price already deducts cash "
        "dividends, so dividends are reinvested; the last visible price is unadjusted. "
        "A price-only series is not in v1."
    ),
)

# The daily-price source whose series each result feed adjusts.
PRICE_SOURCE: dict[str, str] = {
    "twse_twt49u": "twse_mi_index",
    "twse_twtauu": "twse_mi_index",
    "twse_twtb8u": "twse_mi_index",
    "tpex_exdailyq": "tpex_otc_quotes",
    "tpex_revivt": "tpex_otc_quotes",
    "tpex_pvchgrslt": "tpex_otc_quotes",
}


def feeds_of(price_source: str) -> list[str]:
    return sorted(feed for feed, prices in PRICE_SOURCE.items() if prices == price_source)


@dataclass(frozen=True, slots=True)
class Bar:
    trade_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None


@dataclass(frozen=True, slots=True)
class Event:
    ex_date: date
    close_before: Decimal | None
    reference_price: Decimal | None

    @property
    def factor(self) -> Decimal | None:
        if self.close_before is None or self.reference_price is None or not self.close_before:
            return None
        return self.reference_price / self.close_before


@dataclass(frozen=True, slots=True)
class Adjusted:
    bar: Bar
    factor: float | None  # cumulative: adjusted = raw * factor
    adjusted_open: float | None
    adjusted_high: float | None
    adjusted_low: float | None
    adjusted_close: float | None


def _times(value: Decimal | None, factor: float | None) -> float | None:
    return None if value is None or factor is None else float(value) * factor


def adjust(bars: Sequence[Bar], events: Sequence[Event]) -> list[Adjusted]:
    """`bars` in trade-date order, adjusted by `events` in any order."""
    if not bars:
        return []
    anchor = bars[-1].trade_date
    pending = sorted((e for e in events if e.ex_date <= anchor), key=lambda e: e.ex_date,
                     reverse=True)
    out: list[Adjusted] = []
    factor: float | None = 1.0
    for bar in reversed(bars):
        # Every event after this date joins the product, latest first.
        while pending and pending[0].ex_date > bar.trade_date:
            one = pending.pop(0).factor
            factor = None if one is None or factor is None else factor * float(one)
        out.append(Adjusted(bar, factor, _times(bar.open, factor), _times(bar.high, factor),
                            _times(bar.low, factor), _times(bar.close, factor)))
    out.reverse()
    return out


@dataclass(frozen=True, slots=True)
class AppliedEvent:
    row: sa.RowMapping  # the visible `corporate_actions` row, with its `available_at`
    factor: Decimal | None


@dataclass(frozen=True, slots=True)
class Series:
    stock_id: str
    source: str
    git_commit: str
    rows: tuple[Adjusted, ...]  # the requested window
    events: tuple[AppliedEvent, ...]  # every event adjusting a row of it, by ex-date


class AdjustedPrices:
    def __init__(self, *, git_commit: str | None = None) -> None:
        if git_commit is None:
            from stock_data_center.v2.fetch_log import current_git_commit

            git_commit = current_git_commit()
        self._git_commit = git_commit

    @property
    def git_commit(self) -> str:
        return self._git_commit

    def compute(self, connection: Connection, *, stock_id: str, start_date: date,
                end_date: date, pit: visibility.PIT, source: str | None = None) -> Series:
        """The series in [start_date, end_date] as `pit` sees it."""
        if connection.scalar(sa.select(stocks.c.stock_id).where(stocks.c.stock_id == stock_id)) \
                is None:
            raise UnknownStockError(f"{stock_id!r} is not on the list")
        if source is not None and source not in set(PRICE_SOURCE.values()):
            raise ValueError(f"{source!r} is not a daily-price source")
        # Every visible price from the window on: the last one is the anchor.
        visible = visibility.rows(connection, daily_prices.name, pit, start=start_date,
                                  end=date.max, stock_ids=[stock_id],
                                  sources=None if source is None else [source])
        if source is None:
            # Chosen from what this context sees in the window, never from rows it
            # cannot see (§19): a stock that later moved market has one series here.
            sources = sorted({p["source"] for p in visible if p["trade_date"] <= end_date})
            if len(sources) > 1:
                raise ValueError(f"{stock_id} has prices from {', '.join(sources)}; "
                                 "name the source")
            if not sources:
                return Series(stock_id, "", self._git_commit, (), ())
            (source,) = sources
        prices = [p for p in visible if p["source"] == source]
        if not prices or prices[0]["trade_date"] > end_date:
            return Series(stock_id, source, self._git_commit, (), ())
        first, anchor = prices[0]["trade_date"], prices[-1]["trade_date"]
        # An event on or before the window's first trade date adjusts nothing in it.
        found = [row for row in visibility.rows(
            connection, "corporate_actions", pit, start=first, end=anchor,
            stock_ids=[stock_id], sources=feeds_of(source)) if row["ex_date"] > first]
        found.sort(key=lambda row: (row["ex_date"], row["source"]))
        events = [Event(row["ex_date"], row["close_before"], row["reference_price"])
                  for row in found]
        bars = [Bar(p["trade_date"], p["open_price"], p["high_price"], p["low_price"],
                    p["close_price"]) for p in prices]
        rows = [row for row in adjust(bars, events) if row.bar.trade_date <= end_date]
        return Series(stock_id, source, self._git_commit, tuple(rows),
                      tuple(AppliedEvent(row, event.factor)
                            for row, event in zip(found, events, strict=True)))
