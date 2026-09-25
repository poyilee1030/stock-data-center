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
A running sum forgets nothing either, and needs no tolerance: the cumulative
flow reads each series it rewrites from its first row. So does the
shareholding concentration, whose change needs the snapshot before the first
one it rewrites; a TDCC series is keyed by its snapshot date. The margin and
short-interest metrics come from each day's own row, so a run reads only the
days it rewrites.

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
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db import schema_v2 as v2
from stock_data_center.v2 import (
    concentration,
    cumulative_flow,
    financial_reports,
    margin_metrics,
    valuation,
)
from stock_data_center.v2.backfill import JOBS
from stock_data_center.v2.derived import TECHNICAL_INDICATORS_FORMULA, Definition
from stock_data_center.v2.exchange_daily import key_columns
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

INSTITUTIONAL_CUMULATIVE_FLOW_V1 = Definition(
    dataset_code="institutional_cumulative_flow",
    derivation_version="v1",
    formula_specification=(
        "Ported from legacy calculate_trust_holding.py and calculate_dealer_holding.py. "
        "For investment trusts and for dealers (proprietary plus hedging), the running "
        "sum of the daily net shares from the series' first day, a zero-origin proxy "
        "and not a holding; and that sum as a percentage of the same day's issued "
        "shares, ROUND((sum / issued * 100)::numeric, 4) in double precision as "
        "legacy computed it, NULL without a foreign-holding row that day."
    ),
    input_tables=("institutional_flows", "foreign_holdings"),
    calendar_timezone="Asia/Taipei",
    calendar_convention=(
        "The days of the institutional file, one series per institutional source: "
        "twse_t86 takes issued shares from twse_mi_qfiis, tpex_insti_daily_trade "
        "from mops_t13sa150_otc. A sum never forgets its first day, so every run "
        "sums each series it rewrites from that day."
    ),
    price_adjustment_convention="not applicable: no price enters the value",
)


SHAREHOLDING_CONCENTRATION_V1 = Definition(
    dataset_code="shareholding_concentration",
    derivation_version="v1",
    formula_specification=(
        "Ported from legacy calculate_shareholding_concentration.py. Of the fifteen "
        "TDCC holding levels, small holders are levels 1-8 (at most 50 lots), mid "
        "9-11 (up to 400) and large 12-15: each group's summed percentage of issued "
        "shares, the large minus the small, the small and large groups' holder "
        "counts, and each ratio's and the spread's change from the previous snapshot, "
        "NULL on the first; ratios rounded to four places half away from zero as "
        "legacy's ROUND(x::numeric, 4), exact because TDCC publishes two places. A "
        "level the source did not publish leaves what needs it NULL."
    ),
    input_tables=("shareholding_distributions",),
    calendar_timezone="Asia/Taipei",
    calendar_convention=(
        "The TDCC snapshot dates of one stock and source, in order, each read as its "
        "latest recorded row; a snapshot date need not be a trading day. A change is "
        "against the stock's previous snapshot however many weeks back, as legacy's "
        "LAG; every run reads each series it rewrites from its first snapshot."
    ),
    price_adjustment_convention="not applicable: no price enters the value",
)


MARGIN_METRICS_V1 = Definition(
    dataset_code="margin_metrics",
    derivation_version="v1",
    formula_specification=(
        "Ported from legacy calculate_margin_pressure_analysis.py, less its composite "
        "score (downstream). From one day's margin row: margin and short usage, the "
        "balance over the limit x 100; margin and short balance change, the balance "
        "minus the previous balance the source publishes on that row, in shares, and "
        "that change over the previous balance x 100; short-cover pressure, short "
        "buy plus stock repayment over the previous short balance x 100. A ratio is "
        "NULL unless its denominator is positive, and is ROUND(x::numeric, 4) of the "
        "double-precision value as legacy computed it. Legacy's _wow names are "
        "_change: the changes are daily."
    ),
    input_tables=("margin_trading",),
    calendar_timezone="Asia/Taipei",
    calendar_convention=(
        "The days of the stock's margin file from one source, each read as its latest "
        "recorded row; every value comes from that one row."
    ),
    price_adjustment_convention="not applicable: no price enters the value",
)

SHORT_INTEREST_METRICS_V1 = Definition(
    dataset_code="short_interest_metrics",
    derivation_version="v1",
    formula_specification=(
        "Ported from legacy calculate_short_interest_analysis.py, less its composite "
        "score (downstream) and its copy of the short-sale change (in margin_metrics). "
        "From one day's securities-lending row: the balance change, the balance minus "
        "the previous balance the source publishes on that row, in shares; that "
        "change over the previous balance x 100; and shares sold over shares returned. "
        "A ratio is NULL unless its denominator is positive, and is "
        "ROUND(x::numeric, 4) of the double-precision value as legacy computed it."
    ),
    input_tables=("securities_lending",),
    calendar_timezone="Asia/Taipei",
    calendar_convention=(
        "The days of the stock's securities-lending file from one source, each read as "
        "its latest recorded row; every value comes from that one row."
    ),
    price_adjustment_convention="not applicable: no price enters the value",
)


VALUATION_METRICS_V1 = Definition(
    dataset_code="valuation_metrics",
    derivation_version="v1",
    formula_specification=(
        "Ported from legacy calculate_valuation.py, with the owner's corrections of "
        "2026-09-25. TTM EPS: the sum of the single-quarter basic EPS (9750) of the four "
        "consecutive quarters ending at the latest quarter public on the day, NULL "
        "unless all four are public; the fourth quarter is the annual figure less the "
        "third quarter's year to date. PE: close over TTM EPS, NULL unless TTM EPS is "
        "positive, rounded as pandas round(2). PE percentile: the PE's average rank "
        "among the series' PEs so far, x 100, rounded as numpy round(4). ROE: the four "
        "quarters' net income attributable to the parent (8610; 8200 in an individual "
        "report) over the latest quarter-end equity attributable to the parent (31XX; "
        "3XXX in an individual report) x 100, rounded half away from zero to two "
        "places, NULL unless the equity is positive. Legacy's ROE assumed a par value "
        "of 10 and counted non-controlling interests, and its _official suffix is dropped."
    ),
    input_tables=("daily_prices", "financial_reports"),
    calendar_timezone="Asia/Taipei",
    calendar_convention=(
        "The days the stock traded (volume above zero) in one daily-price source, the "
        "days legacy's daily quotes kept, keyed by that source. A report counts from "
        "the day its first version's published_at falls on in Asia/Taipei, with its "
        "latest version's facts; a report without a published_at never counts. The "
        "percentile ranks against the whole series, so every run reads each series "
        "it rewrites from its first day."
    ),
    price_adjustment_convention=(
        "raw_official_close: no corporate-action adjustment, as legacy computed it."
    ),
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


@dataclass(frozen=True, slots=True)
class Input:
    """What a stored row reads of one input table, for when the row became public.

    `source` maps the stored series' source to the input's (None: the input has
    no source, and every row of the stock counts); `scope` is "history" when a
    row reads the input's series up to its own date, "day" when only its own
    date's row. A report counts from the day of its first publication."""

    table: sa.Table
    source: Callable[[str], str] | None
    scope: str


def _same(source: str) -> str:
    return source


@dataclass(frozen=True)
class StoredDataset:
    definition: Definition
    table: sa.Table
    inputs: tuple[sa.Table, ...]
    # Every input a row's value reads (Step 27-c: `visibility.derived_rows`).
    depends: tuple[Input, ...]
    # An input row's source -> the stored series' source.
    series_source: Callable[[str], str]
    # (connection, stock_id, source, first date to write or None for all) -> rows
    compute: Callable[[Connection, str, str, date | None], list[dict]]
    # Series to restart because of an input outside `inputs`, and from when.
    more_changes: Callable[[Connection, datetime | None], dict[Series, date]] | None = None
    # Job locks whose writers share them, so a run takes them exclusively.
    exclusive_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunResult:
    computed_at: datetime
    previous: datetime | None
    series: int
    rows: int


# ---------------------------------------------------------------- inputs


def period(table: sa.Table) -> sa.Column:
    """The date column of a table's key: a trade date, or a TDCC snapshot date."""
    [column] = (c for c in key_columns(table) if c not in ("stock_id", "source"))
    return table.c[column]


def _latest(table: sa.Table, stock_id: str, source: str, since: date | None, *columns):
    """Each date's latest recorded row of one stock and source."""
    day = period(table)
    query = (
        sa.select(day, *(table.c[c] for c in columns))
        .where(table.c.stock_id == stock_id, table.c.source == source)
        .order_by(day, table.c.recorded_at.desc())
        .distinct(day)
    )
    return query if since is None else query.where(day >= since)


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


# The foreign-holding source of each market's institutional source, for the
# issued shares a cumulative ratio divides by.
HOLDING_SOURCE = {"twse_t86": "twse_mi_qfiis", "tpex_insti_daily_trade": "mops_t13sa150_otc"}
_FLOW_OF_HOLDING = {holding: flow for flow, holding in HOLDING_SOURCE.items()}


def _cumulative_rows(connection: Connection, stock_id: str, source: str,
                     start: date | None) -> list[dict]:
    parties = cumulative_flow.PARTIES
    flows = connection.execute(_latest(
        v2.institutional_flows, stock_id, source, None,
        *(f"{party}_net" for party in parties))).all()
    issued = dict(connection.execute(_latest(
        v2.foreign_holdings, stock_id, HOLDING_SOURCE[source], None, "issued_shares")).all())
    sums = {
        party: cumulative_flow.cumulative_flows([row[index + 1] for row in flows])
        for index, party in enumerate(parties)
    }
    out = []
    for i, row in enumerate(flows):
        day = row[0]
        if start is not None and day < start:
            continue
        values = {}
        for party in parties:
            values[f"{party}_cumulative_net_shares"] = sums[party][i]
            values[f"{party}_cumulative_net_ratio"] = cumulative_flow.held_ratio(
                sums[party][i], issued.get(day))
        out.append({"stock_id": stock_id, "source": source, "trade_date": day, **values})
    return out


_LEVEL_COLUMNS = tuple(f"{kind}_{level}" for level in range(1, 16)
                       for kind in ("holders", "percent"))


def _concentration_rows(connection: Connection, stock_id: str, source: str,
                        start: date | None) -> list[dict]:
    # A change needs the snapshot before `start`; a series is a few hundred
    # weeks, so it is read whole.
    snapshots = connection.execute(_latest(
        v2.shareholding_distributions, stock_id, source, None, *_LEVEL_COLUMNS)).mappings().all()
    return [
        {"stock_id": stock_id, "source": source, "snapshot_date": snapshot["snapshot_date"],
         **metrics}
        for snapshot, metrics in zip(snapshots, concentration.concentration(snapshots))
        if start is None or snapshot["snapshot_date"] >= start
    ]


MARGIN_COLUMNS = margin_metrics.MARGIN_METRICS
SHORT_INTEREST_COLUMNS = margin_metrics.SHORT_INTEREST_METRICS


def _day_rows(table: sa.Table, inputs: tuple[str, ...], formula):
    """A dataset whose every value comes from one input row: no warm-up."""
    def compute(connection: Connection, stock_id: str, source: str,
                start: date | None) -> list[dict]:
        return [
            {"stock_id": stock_id, "source": source, "trade_date": row["trade_date"],
             **formula(row)}
            for row in connection.execute(_latest(
                table, stock_id, source, start, *inputs)).mappings()
        ]
    return compute


VALUATION_COLUMNS = valuation.METRICS
TAIPEI = ZoneInfo("Asia/Taipei")
_INCOME = (valuation.EPS, *valuation.NET_INCOME.values())
_EQUITY = tuple(valuation.EQUITY.values())


def _reports(connection: Connection, stock_id: str) -> list[valuation.Report]:
    """Each quarter's report: its first version's publication, its latest version's facts."""
    r, f = v2.financial_reports, v2.financial_report_facts
    first: dict[tuple[int, int], datetime | None] = {}
    latest: dict[tuple[int, int], tuple[int, str]] = {}
    for report_id, year, quarter, category, published_at in connection.execute(
            sa.select(r.c.id, r.c.report_year, r.c.report_quarter, r.c.report_category,
                      r.c.published_at)
            .where(r.c.stock_id == stock_id).order_by(r.c.recorded_at)):
        first.setdefault((year, quarter), published_at)
        latest[(year, quarter)] = (report_id, category)
    facts: dict[int, dict] = {report_id: {} for report_id, _ in latest.values()}
    if facts:
        for report_id, code, start, end, value in connection.execute(
                sa.select(f.c.report_id, f.c.account_code, f.c.period_start, f.c.period_end,
                          f.c.value)
                .where(f.c.report_id.in_(facts), sa.or_(
                    sa.and_(f.c.statement == "income_statement", f.c.account_code.in_(_INCOME)),
                    sa.and_(f.c.statement == "balance_sheet", f.c.account_code.in_(_EQUITY))))):
            facts[report_id][(code, start, end)] = value
    return [
        valuation.Report(year, quarter, category,
                         None if first[(year, quarter)] is None
                         else first[(year, quarter)].astimezone(TAIPEI).date(),
                         facts[report_id])
        for (year, quarter), (report_id, category) in latest.items()
    ]


def _valuation_rows(connection: Connection, stock_id: str, source: str,
                    start: date | None) -> list[dict]:
    days = [
        (day, _float(close))
        for day, close, volume in connection.execute(_latest(
            v2.daily_prices, stock_id, source, None, "close_price", "volume"))
        if volume
    ]
    by_quarter = valuation.quarters(_reports(connection, stock_id))
    return [
        {"stock_id": stock_id, "source": source, "trade_date": day, **metrics}
        for day, metrics in valuation.valuations(days, by_quarter)
        if start is None or day >= start
    ]


def _report_changes(connection: Connection, since: datetime | None) -> dict[Series, date]:
    """Each price series of a stock with a report version recorded after `since`, from
    the day that report was first public. None means a first or full run, which
    already recomputes every price series."""
    if since is None:
        return {}
    r = v2.financial_reports
    changed = (sa.select(r.c.stock_id, r.c.report_year, r.c.report_quarter)
               .where(r.c.recorded_at > since).distinct().subquery())
    first = (
        sa.select(r.c.stock_id, r.c.published_at)
        .join(changed, sa.and_(r.c.stock_id == changed.c.stock_id,
                               r.c.report_year == changed.c.report_year,
                               r.c.report_quarter == changed.c.report_quarter))
        .order_by(r.c.stock_id, r.c.report_year, r.c.report_quarter, r.c.recorded_at)
        .distinct(r.c.stock_id, r.c.report_year, r.c.report_quarter)
    )
    public: dict[str, date] = {}
    for stock_id, published_at in connection.execute(first):
        if published_at is not None:
            day = published_at.astimezone(TAIPEI).date()
            public[stock_id] = min(day, public.get(stock_id, day))
    if not public:
        return {}
    p = v2.daily_prices.c
    return {
        (stock_id, source): public[stock_id]
        for stock_id, source in connection.execute(
            sa.select(p.stock_id, p.source).distinct().where(p.stock_id.in_(public)))
    }


TECHNICAL_INDICATORS = StoredDataset(
    TECHNICAL_INDICATORS_V1, v2.technical_indicators, (v2.daily_prices,),
    (Input(v2.daily_prices, _same, "history"),),
    lambda source: source, _technical_rows,
)
INSTITUTIONAL_STREAKS = StoredDataset(
    INSTITUTIONAL_STREAKS_V1, v2.institutional_streaks,
    (v2.daily_prices, v2.institutional_flows),
    (Input(v2.daily_prices, PRICE_SOURCE.__getitem__, "history"),
     Input(v2.institutional_flows, _same, "history")),
    lambda source: FLOW_SOURCE.get(source, source), _streak_rows,
)
INSTITUTIONAL_CUMULATIVE_FLOW = StoredDataset(
    INSTITUTIONAL_CUMULATIVE_FLOW_V1, v2.institutional_cumulative_flow,
    (v2.institutional_flows, v2.foreign_holdings),
    (Input(v2.institutional_flows, _same, "history"),
     Input(v2.foreign_holdings, HOLDING_SOURCE.__getitem__, "day")),
    lambda source: _FLOW_OF_HOLDING.get(source, source), _cumulative_rows,
)
# A change reads only the previous snapshot, but "history" is the safe side of
# that: a row never shows before its inputs, at worst later.
SHAREHOLDING_CONCENTRATION = StoredDataset(
    SHAREHOLDING_CONCENTRATION_V1, v2.shareholding_concentration,
    (v2.shareholding_distributions,), (Input(v2.shareholding_distributions, _same, "history"),),
    lambda source: source, _concentration_rows,
)
MARGIN_METRICS = StoredDataset(
    MARGIN_METRICS_V1, v2.margin_metrics, (v2.margin_trading,),
    (Input(v2.margin_trading, _same, "day"),), lambda source: source,
    _day_rows(v2.margin_trading, margin_metrics.MARGIN_INPUTS, margin_metrics.margin_metrics),
)
SHORT_INTEREST_METRICS = StoredDataset(
    SHORT_INTEREST_METRICS_V1, v2.short_interest_metrics, (v2.securities_lending,),
    (Input(v2.securities_lending, _same, "day"),), lambda source: source,
    _day_rows(v2.securities_lending, margin_metrics.SHORT_INTEREST_INPUTS,
              margin_metrics.short_interest_metrics),
)
# The percentile ranks against the whole series, and each day reads every
# report public by then.
VALUATION_METRICS = StoredDataset(
    VALUATION_METRICS_V1, v2.valuation_metrics, (v2.daily_prices,),
    (Input(v2.daily_prices, _same, "history"), Input(v2.financial_reports, None, "history")),
    lambda source: source, _valuation_rows, more_changes=_report_changes,
    exclusive_keys=(financial_reports.KEY,),
)
DATASETS = {
    d.definition.dataset_code: d
    for d in (TECHNICAL_INDICATORS, INSTITUTIONAL_STREAKS, INSTITUTIONAL_CUMULATIVE_FLOW,
              SHAREHOLDING_CONCENTRATION, MARGIN_METRICS, SHORT_INTEREST_METRICS,
              VALUATION_METRICS)
}


# ---------------------------------------------------------------- run


def writer_keys(dataset: StoredDataset) -> list[str]:
    """The job keys whose writers lock the dataset's inputs, in lock order: every
    write-path job, not one module's, so no input's writer is left out."""
    return sorted(k for k, job in JOBS.items() if job.table in dataset.inputs)


def _fix_inputs(connection: Connection, dataset: StoredDataset) -> datetime:
    """Take the locks that make `computed_at` a clean cut, and return it.

    A writer stamps its rows with its INSERT's statement time and holds its
    job's advisory lock until it commits (`exchange_daily._write`, which every
    job in `backfill.JOBS` writes through). Taking the
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
    for key in writer_keys(dataset):
        connection.execute(sa.select(sa.func.pg_advisory_xact_lock_shared(sa.func.hashtext(key))))
    # A report writer holds its own stock's lock and the reports' job lock shared,
    # so writers of different stocks run side by side and a run waits for them all.
    for key in dataset.exclusive_keys:
        connection.execute(sa.select(lock(sa.func.hashtext(key))))
    return connection.scalar(sa.select(sa.func.clock_timestamp()))


def _changed(connection: Connection, dataset: StoredDataset,
             since: datetime | None) -> dict[Series, date]:
    """Each series' earliest input date recorded after `since`; all of them if None."""
    starts: dict[Series, date] = {}
    for table in dataset.inputs:
        query = sa.select(table.c.stock_id, table.c.source, sa.func.min(period(table))) \
            .group_by(table.c.stock_id, table.c.source)
        if since is not None:
            query = query.where(table.c.recorded_at > since)
        for stock_id, source, first in connection.execute(query):
            key = (stock_id, dataset.series_source(source))
            starts[key] = min(first, starts.get(key, first))
    if dataset.more_changes is not None:
        for key, first in dataset.more_changes(connection, since).items():
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
    written = sum(
        rewrite(connection, dataset, stock_id, source,
                None if full or previous is None else first, computed_at)
        for (stock_id, source), first in sorted(changed.items())
    )
    return RunResult(computed_at, previous, len(changed), written)


def rewrite(connection: Connection, dataset: StoredDataset, stock_id: str, source: str,
            start: date | None, computed_at: datetime) -> int:
    """Replace one series' rows from `start` on with freshly computed ones, and
    return how many were written. `start=None` computes the whole series and
    expects its rows gone already."""
    table = dataset.table
    rows = dataset.compute(connection, stock_id, source, start)
    if start is not None:
        connection.execute(sa.delete(table).where(
            table.c.stock_id == stock_id, table.c.source == source,
            period(table) >= start))
    if rows:
        connection.execute(sa.insert(table), [{**row, "computed_at": computed_at}
                                              for row in rows])
    return len(rows)


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
