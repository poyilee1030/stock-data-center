#!/usr/bin/env python
"""Reconcile the stored `institutional_streaks:v1` against legacy `stock_db`.

Legacy computed `foreign/trust/dealer_streak_days` in `calculate_daily.py` and
stored them in its `technical_indicators`, one row per `daily_quotes` day. Ours
is `institutional_streaks`, over the days a stock traded on the institutional
source's market (`derived_store.PRICE_SOURCE`). Both sides are limited to the
stocks on today's list (ADR-0026).

Keys only one side has are counted (`ours_only`, `legacy_only`). A key only
ours has is explained only on 2026-03-27: legacy stored that day's price file
before odd-lot trading settled and never refetched it (ROADMAP Step 28), so a
stock that traded only odd lots had volume 0 there and legacy dropped the day.
Every other one-sided key is unexplained.

Every differing streak is attributed to one class; any it cannot attribute is
`unexplained`, and an unexplained key or streak fails the run (exit 1):

`A_day_sets_differ`
    Inside the run that reaches the date, a day is on one side's axis and not
    the other's, so one side counts a day the other does not.
`B_legacy_captured_another_date`
    Inside that run, a net differs in sign on one of the six TWSE dates whose
    legacy file belongs to another date (Step 20-a report,
    `legacy_captured_another_date`).
`B_net_sign_differs`
    Inside that run, a day both sides have carries a net of a different sign on
    another date: the institutional inputs disagree, not the counting; Step
    20-a classified every such input row (`legacy_row_incomplete`, legacy's
    same-day captures before the file settled).
`C_market_transfer`
    A stock that moved market has one series per source (CLAUDE.md §30);
    legacy's single series ran across the move.

The run that reaches a date is the `max(|ours|, |legacy|) + 1` union-axis days
up to it: both streaks are determined by those days alone.

    python scripts/reconcile_institutional_streaks.py \\
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
from datetime import date

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.v2.derived_store import PRICE_SOURCE
from stock_data_center.v2.streaks import PARTIES

FROZEN_VOLUME_DAY = date(2026, 3, 27)
# Step 20-a: legacy stored another date's T86 file under these dates.
ANOTHER_DATES_FILE = frozenset(date.fromisoformat(d) for d in (
    "2021-06-17", "2022-04-20", "2023-08-04", "2024-04-08", "2024-10-22", "2024-10-29"))


def _sign(value) -> int:
    return 0 if not value else (1 if value > 0 else -1)


def _ours(connection, code: str, start: date, end: date):
    """Stored streaks, our axis, and our nets, by date; the sources seen."""
    streaks: dict[date, tuple] = {}
    sources: set[str] = set()
    for row in connection.execute(sa.text(
            "SELECT * FROM institutional_streaks WHERE stock_id = :c "
            "AND trade_date BETWEEN :s AND :e"), {"c": code, "s": start, "e": end}).mappings():
        streaks[row["trade_date"]] = tuple(row[f"{p}_streak_days"] for p in PARTIES)
        sources.add(row["source"])
    nets = {
        day: tuple(values)
        for day, *values in connection.execute(sa.text(
            "SELECT DISTINCT ON (trade_date) trade_date, "
            + ", ".join(f"{p}_net" for p in PARTIES)
            + " FROM institutional_flows WHERE stock_id = :c AND trade_date <= :e "
            "ORDER BY trade_date, recorded_at DESC"), {"c": code, "e": end})
    }
    axis = set(connection.scalars(sa.text(
        "SELECT trade_date FROM (SELECT DISTINCT ON (source, trade_date) trade_date, volume "
        "FROM daily_prices WHERE stock_id = :c AND source IN :sources AND trade_date <= :e "
        "ORDER BY source, trade_date, recorded_at DESC) x WHERE volume > 0").bindparams(
            sa.bindparam("sources", expanding=True)),
        {"c": code, "e": end, "sources": list(PRICE_SOURCE.values())}))
    return streaks, axis, nets, sources


def _legacy(connection, code: str, start: date, end: date):
    streaks = {
        date.fromisoformat(day): tuple(int(v) for v in values)
        for day, *values in connection.execute(sa.text(
            "SELECT date, " + ", ".join(f"{p}_streak_days" for p in PARTIES)
            + " FROM technical_indicators WHERE symbol = :c AND date BETWEEN :s AND :e"),
            {"c": code, "s": start.isoformat(), "e": end.isoformat()})
    }
    axis = {date.fromisoformat(day) for day in connection.scalars(sa.text(
        "SELECT date FROM daily_quotes WHERE symbol = :c AND date <= :e"),
        {"c": code, "e": end.isoformat()})}
    nets = {
        date.fromisoformat(day): tuple(values)
        for day, *values in connection.execute(sa.text(
            "SELECT date, " + ", ".join(f"{p}_net" for p in PARTIES)
            + " FROM institutional_investors WHERE symbol = :c AND date <= :e"),
            {"c": code, "e": end.isoformat()})
    }
    return streaks, axis, nets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", required=True)
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-09-11")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    keys: Counter = Counter()
    compared: Counter = Counter()
    differs: Counter = Counter()
    classes: Counter = Counter()
    samples: dict[str, list[str]] = {}
    with sa.create_engine(args.database_url).connect() as ours_c, \
            sa.create_engine(args.legacy_database_url).connect() as legacy_c:
        universe = sorted(ours_c.scalars(sa.text("SELECT stock_id FROM stocks")))
        for code in universe:
            ours, our_axis, our_nets, sources = _ours(ours_c, code, start, end)
            legacy, legacy_axis, legacy_nets = _legacy(legacy_c, code, start, end)
            keys["ours"] += len(ours)
            keys["legacy"] += len(legacy)
            keys["ours_only"] += len(ours.keys() - legacy.keys())
            keys["legacy_only"] += len(legacy.keys() - ours.keys())
            for day in ours.keys() - legacy.keys():
                keys["ours_only_frozen_2026_03_27" if day == FROZEN_VOLUME_DAY
                     else "ours_only_unexplained"] += 1
            for day in sorted(ours.keys() - legacy.keys())[:3]:
                samples.setdefault("ours_only", []).append(f"{code} {day}")
            for day in sorted(legacy.keys() - ours.keys())[:3]:
                samples.setdefault("legacy_only", []).append(f"{code} {day}")
            union = sorted(our_axis | legacy_axis)
            position = {day: i for i, day in enumerate(union)}
            for day in sorted(ours.keys() & legacy.keys()):
                for index, party in enumerate(PARTIES):
                    compared[party] += 1
                    mine, theirs = ours[day][index], legacy[day][index]
                    if mine == theirs:
                        continue
                    differs[party] += 1
                    reach = max(abs(mine), abs(theirs)) + 1
                    run = union[max(0, position[day] - reach + 1): position[day] + 1]
                    if len(sources) > 1:
                        cls = "C_market_transfer"
                    elif any((d in our_axis) != (d in legacy_axis) for d in run):
                        cls = "A_day_sets_differ"
                    elif signs_differ := [
                        d for d in run
                        if _sign(our_nets.get(d, (None,) * 3)[index])
                        != _sign(legacy_nets.get(d, (None,) * 3)[index])
                    ]:
                        cls = ("B_legacy_captured_another_date"
                               if ANOTHER_DATES_FILE.intersection(signs_differ)
                               else "B_net_sign_differs")
                    else:
                        cls = "unexplained"
                    classes[cls] += 1
                    bucket = samples.setdefault(cls, [])
                    if len(bucket) < 10:
                        bucket.append(f"{code} {day} {party} ours={mine} legacy={theirs}")
    report = {
        "window": [start.isoformat(), end.isoformat()],
        "keys": dict(keys), "compared": dict(compared), "differs": dict(differs),
        "classification": dict(classes), "samples": samples,
    }
    print(json.dumps(report, indent=2, default=str))
    return 1 if (classes["unexplained"] or keys["legacy_only"]
                 or keys["ours_only_unexplained"]) else 0


if __name__ == "__main__":
    sys.exit(main())
