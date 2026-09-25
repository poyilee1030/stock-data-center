#!/usr/bin/env python
"""Reconcile the stored `shareholding_concentration:v1` against legacy `stock_db`.

Legacy `shareholding_concentration` holds one row per (date, symbol) of its
`shareholding`; ours is `shareholding_concentration`, one series per stock of
`tdcc_opendata`. Both sides are limited to the stocks on today's list
(ADR-0026). Step 24-b found every shared (week, security, level 1-15) of the two
inputs identical, so every ratio and count of a shared key must be identical;
any difference is `unexplained`.

A week-over-week change is against the stock's previous snapshot, and the two
sides do not always hold the same snapshots (Step 24-b: eight weeks only ours
has, and our history starts in 2019). A differing change is explained when the
two previous snapshots differ and our own ratios, taken against legacy's
previous date, give legacy's value to the digit:

`wow_previous_snapshot_differs`
    Legacy's previous snapshot is not ours, and ours(D) - ours(legacy's
    previous) rounds to legacy's change.
`wow_legacy_series_starts_later`
    Legacy has no earlier snapshot, so its change is NULL, and ours has one.
`wow_previous_only_legacy_has`
    Legacy's previous snapshot is one only its input has (a `legacy_only`
    key), and legacy's change is its own ratios' difference to the digit;
    the ratios of D itself are compared as every shared key's are.
`wow_legacy_incremental_lost_previous`
    Legacy's change is NULL although it has an earlier snapshot, and the stock
    was missing from the snapshot legacy processed last before D. Legacy's
    incremental run reads only from that date on (`WHERE date >= :last`), so
    its LAG found nothing; a full run would have found the earlier snapshot.

Anything else is `wow_unexplained`.

A key only one side has must be a snapshot only that side's input has:
`ours_only_input_only_ours_has` when legacy `shareholding` has no row for it,
`legacy_only_input_only_legacy_has` when our `shareholding_distributions` has
none. Exit 1 on any unexplained difference or key.

    python scripts/reconcile_shareholding_concentration.py \\
        --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \\
        --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db \\
        --start 2020-01-02 --end 2026-09-11
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from bisect import bisect_left
from collections import Counter
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.v2.concentration import GROUPS, METRICS

WOW = tuple(m for m in METRICS if m.endswith("_wow"))
LOCAL = tuple(m for m in METRICS if m not in WOW)
BASE = {f"{m}_wow": m for m in (*(f"{g}_holder_ratio" for g in GROUPS), "concentration_spread")}


def _change(later: float | None, earlier: float | None) -> float | None:
    """Two stored ratios' difference, rounded as a stored change; NULL if either is.

    A stored ratio is the double nearest a four-place decimal, so its repr is
    that decimal. A NULL ratio (a level the source did not publish) has no
    change, and one that has to match a value then does not."""
    if later is None or earlier is None:
        return None
    difference = Decimal(repr(later)) - Decimal(repr(earlier))
    return float(difference.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)) + 0.0


def _float(value):
    return None if value is None else float(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", required=True)
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-09-11")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    with sa.create_engine(args.database_url).connect() as c:
        universe = set(c.scalars(sa.text("SELECT stock_id FROM stocks")))
        ours: dict[tuple, dict] = {
            (row["stock_id"], row["snapshot_date"]): dict(row)
            for row in c.execute(sa.text(
                "SELECT * FROM shareholding_concentration WHERE source = 'tdcc_opendata'"
            )).mappings()
        }
        our_inputs = {
            tuple(row) for row in c.execute(sa.text(
                "SELECT DISTINCT stock_id, snapshot_date FROM shareholding_distributions"))
        }
    with sa.create_engine(args.legacy_database_url).connect() as c:
        legacy: dict[tuple, dict] = {}
        for row in c.execute(sa.text(
                "SELECT date, symbol, " + ", ".join(METRICS)
                + " FROM shareholding_concentration")).mappings():
            if row["symbol"] in universe:
                legacy[(row["symbol"], date.fromisoformat(row["date"]))] = {
                    m: row[m] if m.endswith("_count") else _float(row[m]) for m in METRICS}
        legacy_inputs = {
            (symbol, date.fromisoformat(day))
            for day, symbol in c.execute(sa.text(
                "SELECT DISTINCT date, symbol FROM shareholding WHERE level = 1"))
            if symbol in universe
        }

    def dates_of(keys) -> dict[str, list[date]]:
        out: dict[str, list[date]] = {}
        for stock_id, day in sorted(keys):
            out.setdefault(stock_id, []).append(day)
        return out

    def previous(dates: dict[str, list[date]], stock_id: str, day: date) -> date | None:
        series = dates.get(stock_id, [])
        index = bisect_left(series, day)
        return series[index - 1] if index else None

    our_dates, legacy_dates = dates_of(ours), dates_of(legacy)
    legacy_weeks = sorted({day for _, day in legacy})
    keys: Counter = Counter()
    compared: Counter = Counter()
    classes: Counter = Counter()
    only_by_date: dict[str, Counter] = {"ours_only": Counter(), "legacy_only": Counter()}
    samples: dict[str, list[str]] = {}

    def note(cls: str, text: str) -> None:
        classes[cls] += 1
        bucket = samples.setdefault(cls, [])
        if len(bucket) < 10:
            bucket.append(text)

    window = {k for k in ours.keys() | legacy.keys() if start <= k[1] <= end}
    for key in sorted(window):
        stock_id, day = key
        if key not in legacy:
            keys["ours_only"] += 1
            only_by_date["ours_only"][day.isoformat()] += 1
            note("ours_only_input_only_ours_has" if key not in legacy_inputs
                 else "ours_only_unexplained", f"{stock_id} {day}")
            continue
        if key not in ours:
            keys["legacy_only"] += 1
            only_by_date["legacy_only"][day.isoformat()] += 1
            note("legacy_only_input_only_legacy_has" if key not in our_inputs
                 else "legacy_only_unexplained", f"{stock_id} {day}")
            continue
        keys["shared"] += 1
        mine, theirs = ours[key], legacy[key]
        for metric in LOCAL:
            compared[metric] += 1
            if mine[metric] != theirs[metric]:
                note("local_unexplained",
                     f"{stock_id} {day} {metric} ours={mine[metric]} legacy={theirs[metric]}")
        ours_before = previous(our_dates, stock_id, day)
        legacy_before = previous(legacy_dates, stock_id, day)
        for metric in WOW:
            compared[metric] += 1
            if mine[metric] == theirs[metric]:
                continue
            text = (f"{stock_id} {day} {metric} ours={mine[metric]} legacy={theirs[metric]} "
                    f"previous ours={ours_before} legacy={legacy_before}")
            if theirs[metric] is None and legacy_before is not None:
                index = bisect_left(legacy_weeks, day)
                lost = index and legacy_weeks[index - 1] > legacy_before
                note("wow_legacy_incremental_lost_previous" if lost else "wow_unexplained",
                     text)
            elif ours_before == legacy_before:
                note("wow_unexplained", text)
            elif legacy_before is None:
                note("wow_legacy_series_starts_later" if theirs[metric] is None
                     else "wow_unexplained", text)
            # Past the branch above, legacy's change is not NULL here, so a
            # NULL recomputed change never matches it.
            elif (stock_id, legacy_before) in ours and _change(
                    mine[BASE[metric]],
                    ours[(stock_id, legacy_before)][BASE[metric]]) == theirs[metric]:
                note("wow_previous_snapshot_differs", text)
            elif (stock_id, legacy_before) not in ours and theirs[metric] == _change(
                    theirs[BASE[metric]], legacy[(stock_id, legacy_before)][BASE[metric]]):
                note("wow_previous_only_legacy_has", text)
            else:
                note("wow_unexplained", text)

    unexplained = {k: v for k, v in classes.items() if k.endswith("unexplained")}
    print(json.dumps({
        "window": [start.isoformat(), end.isoformat()], "universe": len(universe),
        "keys": dict(keys), "compared": dict(compared), "classification": dict(classes),
        "only_by_date": {side: dict(sorted(counts.items(), key=lambda kv: -kv[1])[:20])
                         for side, counts in only_by_date.items()},
        "samples": samples,
    }, indent=2, default=str))
    return 1 if unexplained else 0


if __name__ == "__main__":
    sys.exit(main())
