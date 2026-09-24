"""Stored derived datasets, computed incrementally from the latest inputs (Step 26).

Each dataset is one wide table keyed by (stock, source, date) (CLAUDE.md §46).
A run fixes its inputs at one instant, stored on every row it writes as
`computed_at`, and recomputes each series from the earliest input date recorded
after the previous run's `computed_at`; a new trading day and a corrected
earlier one are the same case. The rows it writes replace the ones there: the
tables follow the latest inputs and are not history (§43).

Like legacy `calculator/`, a series restarts `BUFFER_DAYS` calendar days before
the first date it rewrites, and further back when that holds fewer rows than
the longest window, so every windowed metric is exact. The exponential metrics
never forget where they started, so theirs is within `within_tolerance` of the
full series, not equal to it; the owner accepted that residue on 2026-09-24. A full
run (`--full`) reads each series from its first row and equals the on-demand
`technical_indicators_pit:v1` bit for bit while no input has a correction.

No value uses an input dated after it: every formula here is causal along the
trade date.

    python -m stock_data_center.v2.derived_store --dataset technical_indicators [--full]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db import schema_v2 as v2
from stock_data_center.v2.derived import TECHNICAL_INDICATORS_FORMULA, Definition
from stock_data_center.v2.exchange_daily import JOBS
from stock_data_center.v2.indicators import MA_WINDOWS, DailyBar, technical_indicators
from stock_data_center.v2.streaks import PARTIES, net_streaks

# Legacy calculate_daily.py: "MA240 needs ~480 trading days of history. 500
# calendar days covers it." It covers about 340 trading days.
BUFFER_DAYS = 500
# The longest window, so a suspension inside the buffer cannot empty MA240.
WARM_UP_ROWS = max(MA_WINDOWS)
# How far an incremental run's exponential metrics may stray from the full
# series; the windowed ones are exact. Measured on stockdc_backfill, 1,959
# series restarted at six dates from 2021 to 2026 (Step 26-b report): MACD, in
# price units, strayed at most 4.1e-6 of the day's close; K, D and RSI, on their
# 0-100 scale, at most 6.5e-8.
PRICE_SCALED = frozenset({"macd_dif", "macd_dea", "macd_hist"})
PERCENT_SCALED = frozenset({"k", "d", "rsi6", "rsi12"})
PRICE_TOLERANCE = 1e-5  # of the close
PERCENT_TOLERANCE = 1e-6


TECHNICAL_INDICATORS_V1 = Definition(
    dataset_code="technical_indicators",
    derivation_version="v1",
    formula_specification=TECHNICAL_INDICATORS_FORMULA,
    input_tables=("daily_prices",),
    calendar_timezone="Asia/Taipei",
    calendar_convention=(
        "Trading days of the stock's own daily-price history from one source, in "
        "order, each read as its latest recorded row. An incremental run restarts "
        "a series 500 calendar days, and at least 240 rows, before the first date "
        "it rewrites."
    ),
    price_adjustment_convention=(
        "raw_official_close: no corporate-action adjustment, as legacy computed "
        "them and its consumers were trained."
    ),
)

INSTITUTIONAL_STREAKS_V1 = Definition(
    dataset_code="institutional_streaks",
    derivation_version="v1",
    formula_specification=(
        "Ported from legacy calculate_daily.py. For foreign investors (excluding "
        "foreign dealers), investment trusts and dealers, the signed number of "
        "consecutive days the party was a net buyer (positive) or net seller "
        "(negative); a zero net is 0 and starts the count over, and a traded day "
        "without an institutional row is a zero net."
    ),
    input_tables=("daily_prices", "institutional_flows"),
    calendar_timezone="Asia/Taipei",
    calendar_convention=(
        "The days the stock traded (volume above zero) on the market of the "
        "institutional source, the days legacy's daily quotes kept; a listed day "
        "without a trade neither extends nor breaks a streak. Keyed by the "
        "institutional source: twse_t86 counts over twse_mi_index days, "
        "tpex_insti_daily_trade over tpex_otc_quotes days. An incremental run "
        "restarts a series 500 calendar days before the first date it rewrites, "
        "or at its first row when a streak on that date spans the whole buffer."
    ),
    price_adjustment_convention="not applicable: no price enters the value",
)


def within_tolerance(metric: str, incremental: float | None, full: float | None,
                     close: float | None) -> bool:
    """Whether an incremental value is an accepted stand-in for the full one.

    `close` is the stock's latest close on or before the date."""
    if incremental == full:
        return True
    if incremental is None or full is None:
        return False
    if metric in PRICE_SCALED:
        return close is not None and abs(incremental - full) <= PRICE_TOLERANCE * abs(close)
    return metric in PERCENT_SCALED and abs(incremental - full) <= PERCENT_TOLERANCE


Series = tuple[str, str]  # (stock_id, source of the stored row)


@dataclass(frozen=True)
class StoredDataset:
    definition: Definition
    table: sa.Table
    inputs: tuple[sa.Table, ...]
    # An input row's source -> the stored series' source.
    series_source: Callable[[str], str]
    # (connection, stock_id, source, first date to write or None for all) -> rows
    compute: Callable[[Connection, str, str, date | None], list[dict]]


@dataclass(frozen=True, slots=True)
class RunResult:
    computed_at: datetime
    previous: datetime | None
    series: int
    rows: int


# ---------------------------------------------------------------- inputs


def _latest(table: sa.Table, stock_id: str, source: str, since: date | None, *columns):
    """Each trade date's latest recorded row of one stock and source."""
    query = (
        sa.select(table.c.trade_date, *(table.c[c] for c in columns))
        .where(table.c.stock_id == stock_id, table.c.source == source)
        .order_by(table.c.trade_date, table.c.recorded_at.desc())
        .distinct(table.c.trade_date)
    )
    return query if since is None else query.where(table.c.trade_date >= since)


def _warm_up(connection: Connection, stock_id: str, source: str, start: date | None,
             rows: int = 0) -> date | None:
    if start is None:
        return None
    since = start - timedelta(days=BUFFER_DAYS)
    if rows:
        t = v2.daily_prices.c
        earlier = (
            sa.select(t.trade_date).distinct()
            .where(t.stock_id == stock_id, t.source == source, t.trade_date < start)
            .order_by(t.trade_date.desc()).limit(rows).subquery()
        )
        oldest = connection.scalar(sa.select(sa.func.min(earlier.c.trade_date)))
        if oldest is not None:
            since = min(since, oldest)
    return since


def _float(value) -> float | None:
    return None if value is None else float(value)


def _technical_rows(connection: Connection, stock_id: str, source: str,
                    start: date | None) -> list[dict]:
    since = _warm_up(connection, stock_id, source, start, WARM_UP_ROWS)
    bars = [
        DailyBar(day, _float(high), _float(low), _float(close), _float(volume))
        for day, high, low, close, volume in connection.execute(_latest(
            v2.daily_prices, stock_id, source, since,
            "high_price", "low_price", "close_price", "volume"))
    ]
    return [
        {"stock_id": stock_id, "source": source, "trade_date": row.trade_date, **row.metrics}
        for row in technical_indicators(bars)
        if start is None or row.trade_date >= start
    ]


# The institutional source of each market and the price source whose traded
# days it counts over.
PRICE_SOURCE = {"twse_t86": "twse_mi_index", "tpex_insti_daily_trade": "tpex_otc_quotes"}
FLOW_SOURCE = {price: flow for flow, price in PRICE_SOURCE.items()}


def _streak_rows(connection: Connection, stock_id: str, source: str,
                 start: date | None) -> list[dict]:
    rows = _streaks_since(connection, stock_id, source, start,
                          _warm_up(connection, stock_id, source, start))
    # A count must equal a full recomputation exactly: a streak still unbroken
    # on the first new date may have begun before the buffer, so count it from
    # the series' start.
    if start is not None and rows and rows[0]["_index"] + 1 in (
            abs(rows[0][f"{party}_streak_days"]) for party in PARTIES):
        rows = _streaks_since(connection, stock_id, source, start, None)
    for row in rows:
        del row["_index"]
    return rows


def _streaks_since(connection: Connection, stock_id: str, source: str,
                   start: date | None, since: date | None) -> list[dict]:
    days = [
        day for day, volume in connection.execute(_latest(
            v2.daily_prices, stock_id, PRICE_SOURCE[source], since, "volume"))
        if volume
    ]
    nets = {
        day: values
        for day, *values in connection.execute(_latest(
            v2.institutional_flows, stock_id, source, since,
            *(f"{party}_net" for party in PARTIES)))
    }
    streaks = {
        party: net_streaks([nets[day][index] if day in nets else None for day in days])
        for index, party in enumerate(PARTIES)
    }
    return [
        {"stock_id": stock_id, "source": source, "trade_date": day, "_index": i,
         **{f"{party}_streak_days": streaks[party][i] for party in PARTIES}}
        for i, day in enumerate(days)
        if start is None or day >= start
    ]


TECHNICAL_INDICATORS = StoredDataset(
    TECHNICAL_INDICATORS_V1, v2.technical_indicators, (v2.daily_prices,),
    lambda source: source, _technical_rows,
)
INSTITUTIONAL_STREAKS = StoredDataset(
    INSTITUTIONAL_STREAKS_V1, v2.institutional_streaks,
    (v2.daily_prices, v2.institutional_flows),
    lambda source: FLOW_SOURCE.get(source, source), _streak_rows,
)
DATASETS = {d.definition.dataset_code: d for d in (TECHNICAL_INDICATORS, INSTITUTIONAL_STREAKS)}


# ---------------------------------------------------------------- run


def _fix_inputs(connection: Connection, dataset: StoredDataset) -> datetime:
    """Take the locks that make `computed_at` a clean cut, and return it.

    A writer stamps its rows with its INSERT's statement time and holds its
    job's advisory lock until it commits (`exchange_daily._write`). Taking the
    same locks shared waits for every writer mid-transaction and blocks new ones
    until this run commits, so every input row stamped before the instant is
    committed and read, and every one stamped after it is left to the next run.
    Under READ COMMITTED each statement then reads that same set of input rows.
    """
    isolation = connection.scalar(sa.text("SELECT current_setting('transaction_isolation')"))
    if isolation != "read committed":
        raise RuntimeError(f"a derived run needs READ COMMITTED, not {isolation}")
    lock = sa.func.pg_advisory_xact_lock
    connection.execute(sa.select(lock(sa.func.hashtext(f"derived/{dataset.table.name}"))))
    for key in sorted(k for k, job in JOBS.items() if job.table in dataset.inputs):
        connection.execute(sa.select(sa.func.pg_advisory_xact_lock_shared(sa.func.hashtext(key))))
    return connection.scalar(sa.select(sa.func.clock_timestamp()))


def _changed(connection: Connection, dataset: StoredDataset,
             since: datetime | None) -> dict[Series, date]:
    """Each series' earliest input date recorded after `since`; all of them if None."""
    starts: dict[Series, date] = {}
    for table in dataset.inputs:
        query = sa.select(table.c.stock_id, table.c.source, sa.func.min(table.c.trade_date)) \
            .group_by(table.c.stock_id, table.c.source)
        if since is not None:
            query = query.where(table.c.recorded_at > since)
        for stock_id, source, first in connection.execute(query):
            key = (stock_id, dataset.series_source(source))
            starts[key] = min(first, starts.get(key, first))
    return starts


def run(connection: Connection, dataset: StoredDataset, *, full: bool = False) -> RunResult:
    """Bring one derived table up to the inputs recorded so far, in the caller's
    transaction; commit it whole, or the next run's starting point is wrong."""
    computed_at = _fix_inputs(connection, dataset)
    table = dataset.table
    previous = connection.scalar(sa.select(sa.func.max(table.c.computed_at)))
    changed = _changed(connection, dataset, None if full or previous is None else previous)
    if full:
        connection.execute(sa.delete(table))
    written = 0
    for (stock_id, source), first in sorted(changed.items()):
        start = None if full or previous is None else first
        rows = dataset.compute(connection, stock_id, source, start)
        if start is not None:
            connection.execute(sa.delete(table).where(
                table.c.stock_id == stock_id, table.c.source == source,
                table.c.trade_date >= start))
        if rows:
            connection.execute(sa.insert(table), [{**row, "computed_at": computed_at}
                                                  for row in rows])
            written += len(rows)
    return RunResult(computed_at, previous, len(changed), written)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--dataset", required=True, choices=sorted(DATASETS))
    parser.add_argument("--full", action="store_true",
                        help="recompute every series from its first row")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    engine = sa.create_engine(args.database_url)
    try:
        with engine.begin() as connection:
            result = run(connection, DATASETS[args.dataset], full=args.full)
    finally:
        engine.dispose()
    print(f"{args.dataset}: {result.series} series, {result.rows} rows, "
          f"computed_at {result.computed_at.isoformat()}, previous "
          f"{result.previous.isoformat() if result.previous else 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
