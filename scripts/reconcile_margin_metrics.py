#!/usr/bin/env python
"""Reconcile the stored `margin_metrics:v1` and `short_interest_metrics:v1` against legacy.

Legacy `margin_pressure_analysis` and `short_interest_analysis` hold one row per
(date, market, symbol) of its `margin_trading` and `margin_sbl`; ours are
`margin_metrics` and `short_interest_metrics`, one series per source. `sii` is
`twse_mi_margn` / `twse_twt93u`, `otc` is `tpex_margin_balance` /
`tpex_margin_sbl`. Both sides are limited to the stocks on today's list
(ADR-0026).

Units are normalized first (CLAUDE.md §78): legacy `margin_trading`, and so
`margin_pressure_analysis`'s share changes, are in lots of 1,000 shares, ours in
shares; `margin_sbl` is in shares like ours. Legacy's `_wow` columns are our
`_change` columns (owner, 2026-09-25).

The four tables are read as key-ordered streams and merged: legacy's input
tables have no index, so one sorted pass over each is the affordable read.

Every metric comes from one input row, so a differing value can only come from
a differing input row, and the input differences are the ones Steps 21-a and
21-b already classified. The only one that reaches these metrics is
`legacy_captured_another_date`: legacy's TWSE `margin_sbl` file for 2022-10-06
holds 2022-10-18's data (Step 21-b), so a difference there is explained when
legacy's value equals ours on 2022-10-18. Any other difference is
`unexplained`, whether or not the inputs differ. A key only one side has must be a day only that side's input
has (`ours_only_input_only_ours_has`, `legacy_only_input_only_legacy_has`).
Exit 1 on anything unexplained.

    python scripts/reconcile_margin_metrics.py \\
        --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \\
        --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db \\
        --start 2020-01-02 --end 2026-09-11
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import sqlalchemy as sa

LOT = 1000


@dataclass(frozen=True)
class Pair:
    ours: str
    legacy: str
    ours_input: str
    legacy_input: str
    sources: dict[str, str]  # legacy market -> our source
    # our metric -> (legacy column, legacy keeps it in lots)
    metrics: dict[str, tuple[str, bool]]
    # (market, date legacy files under) -> the date its data is from
    another_date: dict[tuple[str, date], date]


MARGIN = Pair(
    "margin_metrics", "margin_pressure_analysis", "margin_trading", "margin_trading",
    {"sii": "twse_mi_margn", "otc": "tpex_margin_balance"},
    {
        "margin_usage_ratio": ("margin_usage_ratio", False),
        "margin_balance_change": ("margin_long_balance_wow", True),
        "margin_balance_change_pct": ("margin_long_balance_wow_pct", False),
        "short_usage_ratio": ("short_usage_ratio", False),
        "short_balance_change": ("margin_short_balance_wow", True),
        "short_balance_change_pct": ("margin_short_balance_wow_pct", False),
        "short_cover_pressure": ("short_cover_pressure", False),
    },
    {},
)
SHORT_INTEREST = Pair(
    "short_interest_metrics", "short_interest_analysis", "securities_lending", "margin_sbl",
    {"sii": "twse_twt93u", "otc": "tpex_margin_sbl"},
    {
        "sbl_balance_change": ("sbl_balance_wow", False),
        "sbl_balance_change_pct": ("sbl_balance_wow_pct", False),
        "sbl_sell_repay_ratio": ("sbl_sell_repay_ratio", False),
    },
    # Step 21-b: legacy's 2022-10-06 sii.csv is 2022-10-18's file.
    {("sii", date(2022, 10, 6)): date(2022, 10, 18)},
)


def _normalized(value, in_lots: bool, integer: bool):
    if value is None:
        return None
    if integer:
        return int(Decimal(str(value)) * (LOT if in_lots else 1))
    return float(value)


def _stream(connection, query: str, params: dict, key_of):
    """Rows of a query already ordered by key, as (key, row), without holding them all."""
    result = connection.execution_options(stream_results=True, yield_per=20_000).execute(
        sa.text(query), params)
    for row in result.mappings():
        key = key_of(row)
        if key is not None:
            yield key, row


def _merged(*streams):
    """Walk key-ordered streams together: each key with its row from every stream or None."""
    heads = [next(stream, None) for stream in streams]
    while any(head is not None for head in heads):
        key = min(head[0] for head in heads if head is not None)
        rows = []
        for index, head in enumerate(heads):
            if head is not None and head[0] == key:
                rows.append(head[1])
                heads[index] = next(streams[index], None)
            else:
                rows.append(None)
        yield key, rows


def reconcile(pair: Pair, ours_c, ours_inputs_c, legacy_c, legacy_inputs_c,
              universe: set[str], start: date, end: date) -> dict:
    """Merge-join the four tables in (stock, market, date) order: legacy's inputs have
    no index, so one sorted pass over each is the affordable read."""
    market_sql = "CASE source " + " ".join(
        f"WHEN '{source}' THEN '{market}'" for market, source in pair.sources.items()) + " END"
    window = {"s": start, "e": end}
    text_window = {"s": start.isoformat(), "e": end.isoformat()}
    markets = ", ".join(f"'{m}'" for m in pair.sources)

    def ours_key(row):
        return (row["stock_id"], row["market"], row["trade_date"])

    def legacy_key(row):
        if row["symbol"] not in universe:
            return None
        return (row["symbol"], row["market"], date.fromisoformat(row["date"]))

    order_ours = f'ORDER BY stock_id COLLATE "C", {market_sql} COLLATE "C", trade_date'
    order_legacy = 'ORDER BY symbol COLLATE "C", market COLLATE "C", date'
    streams = (
        _stream(ours_c, f"SELECT *, {market_sql} AS market FROM {pair.ours} "
                f"WHERE trade_date BETWEEN :s AND :e {order_ours}", window, ours_key),
        _stream(ours_inputs_c,
                f"SELECT * FROM (SELECT DISTINCT ON (stock_id, source, trade_date) *, "
                f"{market_sql} AS market FROM {pair.ours_input} "
                "WHERE trade_date BETWEEN :s AND :e "
                "ORDER BY stock_id, source, trade_date, recorded_at DESC) latest "
                f"{order_ours}", window, ours_key),
        _stream(legacy_c, f"SELECT * FROM {pair.legacy} WHERE date BETWEEN :s AND :e "
                f"AND market IN ({markets}) {order_legacy}", text_window, legacy_key),
        _stream(legacy_inputs_c, f"SELECT * FROM {pair.legacy_input} "
                f"WHERE date BETWEEN :s AND :e AND market IN ({markets}) {order_legacy}",
                text_window, legacy_key),
    )

    keys: Counter = Counter()
    compared: Counter = Counter()
    classes: Counter = Counter()
    samples: dict[str, list[str]] = {}
    by_date: Counter = Counter()

    def note(cls: str, text: str) -> None:
        classes[cls] += 1
        bucket = samples.setdefault(cls, [])
        if len(bucket) < 10:
            bucket.append(text)

    # Our rows on each date a legacy file really holds, to check it against.
    elsewhere = {}
    for (market, _), actual in pair.another_date.items():
        for row in ours_c.execute(sa.text(
                f"SELECT * FROM {pair.ours} WHERE source = :r AND trade_date = :d"),
                {"r": pair.sources[market], "d": actual}).mappings():
            elsewhere[(row["stock_id"], market, actual)] = row

    for key, (mine, my_input, theirs, their_input) in _merged(*streams):
        if mine is None and theirs is None:
            continue  # an input row only; its own derived row is checked by coverage
        if theirs is None:
            keys["ours_only"] += 1
            note("ours_only_input_only_ours_has" if their_input is None
                 else "ours_only_unexplained", str(key))
            continue
        if mine is None:
            keys["legacy_only"] += 1
            note("legacy_only_input_only_legacy_has" if my_input is None
                 else "legacy_only_unexplained", str(key))
            continue
        keys["shared"] += 1
        actual = pair.another_date.get(key[1:])
        other = elsewhere.get((key[0], key[1], actual)) if actual else None
        for metric, (legacy_column, in_lots) in pair.metrics.items():
            compared[metric] += 1
            expected = _normalized(theirs[legacy_column], in_lots,
                                   integer=metric.endswith("_change"))
            if mine[metric] == expected:
                continue
            by_date[f"{key[1]} {key[2].isoformat()}"] += 1
            text = f"{key} {metric} ours={mine[metric]} legacy={expected}"
            if other is not None and other[metric] == expected:
                note("legacy_captured_another_date", text + f" = ours on {actual}")
            else:
                note("unexplained", text)
    return {"keys": dict(keys), "compared": dict(compared), "classification": dict(classes),
            "differences_by_date": dict(by_date.most_common(20)), "samples": samples}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", required=True)
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-09-11")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    report: dict = {"window": [start.isoformat(), end.isoformat()]}
    ours_engine = sa.create_engine(args.database_url)
    legacy_engine = sa.create_engine(args.legacy_database_url)
    with ours_engine.connect() as c:
        universe = set(c.scalars(sa.text("SELECT stock_id FROM stocks")))
    report["universe"] = len(universe)
    for pair in (MARGIN, SHORT_INTEREST):
        # Four open cursors at once, one connection each.
        with ours_engine.connect() as ours_c, ours_engine.connect() as ours_inputs_c, \
                legacy_engine.connect() as legacy_c, legacy_engine.connect() as legacy_inputs_c:
            report[pair.ours] = reconcile(pair, ours_c, ours_inputs_c, legacy_c,
                                          legacy_inputs_c, universe, start, end)
    print(json.dumps(report, indent=2, default=str))
    unexplained = [
        cls for pair in (MARGIN, SHORT_INTEREST)
        for cls in report[pair.ours]["classification"] if cls.endswith("unexplained")
    ]
    return 1 if unexplained else 0


if __name__ == "__main__":
    sys.exit(main())
