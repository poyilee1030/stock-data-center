#!/usr/bin/env python
"""Reconcile `technical_indicators:v1` against legacy `stock_db`.

Step 26-a. Every derived step reconciles against the legacy tables its
consumers read (CLAUDE.md §78), and for the price/volume indicators that table
is `technical_indicators`. The legacy streak columns in the same table belong to
`institutional_streaks:v1` and are Step 26-b's to reconcile; asking for them
here would compare a series this step does not produce.

Three classes of difference, and none of them is smoothed:

`value_differs`
    The same security-date-metric exists on both sides with different numbers.
    Legacy recomputed incrementally from a 500-calendar-day buffer, so its
    exponential metrics (K, D, RSI, MACD) carry whatever warm-up the last run
    happened to have; the rolling as-of series always warms up from the
    security's first visible day. Differences are therefore reported by
    magnitude rather than judged, and the report separates the exponential
    metrics from the windowed ones, where no such excuse exists.

`legacy_only`
    A security-date legacy has and we do not. Legacy dropped untraded rows and
    whole instrument classes, so our input is a superset; a `legacy_only` row
    means either a price we never imported or a date the rolling series refused
    to compute, and both are defects worth naming.

`ours_only`
    The opposite, and the expected one: the official feed carries securities
    the legacy scraper never collected.

Usage:

    python scripts/reconcile_technical_indicators.py \
        --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \
        --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db \
        --start 2020-01-02 --end 2026-09-11
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

import sqlalchemy as sa

_ONE_DAY = timedelta(days=1)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.derived.indicators import METRIC_CODES

# Infinite-memory metrics: legacy's buffered restarts leave a warm-up residue
# in these and in no others.
EXPONENTIAL = frozenset({"k", "d", "rsi6", "rsi12", "macd_dif", "macd_dea", "macd_hist"})

# Reported separately so a float-association difference never sits in the same
# bucket as a real one.
TOLERANCE = 1e-6

BUCKETS = ((1e-9, "at_float_noise"), (1e-6, "below_1e-6"), (1e-3, "below_1e-3"),
           (1.0, "below_1"), (float("inf"), "at_or_above_1"))


def _bucket(difference: float) -> str:
    for limit, name in BUCKETS:
        if difference < limit:
            return name
    raise AssertionError("unreachable")  # pragma: no cover


def _ours(engine, *, start: date, end: date) -> dict:
    statement = sa.text(
        """
        SELECT s.security_code, m.observation_date, m.metric_code, m.numeric_value
          FROM derived_metric_versions m
          JOIN security s ON s.id = m.security_id
          JOIN derived_dataset_definitions d ON d.id = m.definition_id
         WHERE d.dataset_code = 'technical_indicators'
           AND d.derivation_version = 'v1'
           AND m.observation_date BETWEEN :start AND :end
        """
    )
    out: dict = defaultdict(dict)
    with engine.connect().execution_options(stream_results=True) as connection:
        for code, day, metric, value in connection.execute(
            statement, {"start": start, "end": end}
        ):
            out[(code, day)][metric] = None if value is None else float(value)
    return out


def _legacy(engine, *, start: date, end: date, metrics: tuple[str, ...]) -> dict:
    columns = ", ".join(metrics)
    statement = sa.text(
        f"SELECT symbol, date, {columns} FROM technical_indicators "
        "WHERE date BETWEEN :start AND :end"
    )
    out: dict = {}
    with engine.connect().execution_options(stream_results=True) as connection:
        for row in connection.execute(
            statement, {"start": start.isoformat(), "end": end.isoformat()}
        ):
            out[(row[0], date.fromisoformat(row[1]))] = {
                metric: None if value is None else float(value)
                for metric, value in zip(metrics, row[2:], strict=True)
            }
    return out


def _months(start: date, end: date):
    """The window in calendar-month slices.

    Both sides at full width are 76 million and 2.9 million rows; holding them
    in memory at once is not a comparison, it is an out-of-memory error. A
    month is self-contained here because every metric is keyed by its own
    observation date.
    """
    cursor = start.replace(day=1)
    while cursor <= end:
        if cursor.month == 12:
            nxt = date(cursor.year + 1, 1, 1)
        else:
            nxt = date(cursor.year, cursor.month + 1, 1)
        yield max(cursor, start), min(nxt - _ONE_DAY, end)
        cursor = nxt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", required=True)
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-09-11")
    parser.add_argument(
        "--securities-file",
        help="one security code per line; compare only these. The whole-market "
        "run mixes two difference classes, because legacy dropped the days a "
        "security did not trade and we keep the official row. Restricting the "
        "comparison to the securities whose trade-date sets are identical "
        "separates the window difference from everything else instead of "
        "leaving one explanation to cover both",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    metrics = tuple(code for code in METRIC_CODES)

    selected: frozenset[str] | None = None
    if args.securities_file:
        with open(args.securities_file, encoding="utf-8") as handle:
            selected = frozenset(
                line.strip() for line in handle if line.strip()
            )

    our_engine = sa.create_engine(args.database_url)
    legacy_engine = sa.create_engine(args.legacy_database_url)

    keys = Counter()
    legacy_only_sample: list[str] = []
    compared: Counter = Counter()
    both_null: Counter = Counter()
    null_mismatch: Counter = Counter()
    buckets: dict[str, Counter] = {metric: Counter() for metric in metrics}
    worst: dict[str, tuple[float, str | None]] = {
        metric: (0.0, None) for metric in metrics
    }

    for month_start, month_end in _months(start, end):
        ours = _ours(our_engine, start=month_start, end=month_end)
        legacy = _legacy(
            legacy_engine, start=month_start, end=month_end, metrics=metrics
        )
        if selected is not None:
            ours = {key: value for key, value in ours.items() if key[0] in selected}
            legacy = {
                key: value for key, value in legacy.items() if key[0] in selected
            }
        shared = ours.keys() & legacy.keys()
        keys["ours"] += len(ours)
        keys["legacy"] += len(legacy)
        keys["shared"] += len(shared)
        keys["legacy_only"] += len(legacy.keys() - ours.keys())
        keys["ours_only"] += len(ours.keys() - legacy.keys())
        if len(legacy_only_sample) < 20:
            legacy_only_sample.extend(
                sorted(
                    f"{code} {day.isoformat()}"
                    for code, day in legacy.keys() - ours.keys()
                )[: 20 - len(legacy_only_sample)]
            )
        for key in shared:
            mine_row = ours[key]
            their_row = legacy[key]
            for metric in metrics:
                mine = mine_row.get(metric)
                theirs = their_row.get(metric)
                if mine is None and theirs is None:
                    both_null[metric] += 1
                    continue
                if (mine is None) != (theirs is None):
                    null_mismatch[metric] += 1
                    continue
                compared[metric] += 1
                difference = abs(mine - theirs)
                buckets[metric][_bucket(difference)] += 1
                if difference > worst[metric][0]:
                    worst[metric] = (difference, f"{key[0]} {key[1].isoformat()}")
        print(f"  {month_start} .. {month_end}", file=sys.stderr, flush=True)

    report = {
        "window": [start.isoformat(), end.isoformat()],
        "securities_filter": (
            None if selected is None else {"file": args.securities_file,
                                           "count": len(selected)}
        ),
        "keys": dict(keys),
        "legacy_only_sample": legacy_only_sample,
        "metrics": {},
    }
    for metric in metrics:
        report["metrics"][metric] = {
            "family": "exponential" if metric in EXPONENTIAL else "windowed",
            "compared": compared[metric],
            "both_null": both_null[metric],
            "null_mismatch": null_mismatch[metric],
            "buckets": dict(buckets[metric]),
            "above_tolerance": sum(
                count
                for bucket, count in buckets[metric].items()
                if bucket in {"below_1e-3", "below_1", "at_or_above_1"}
            ),
            "worst_difference": worst[metric][0],
            "worst_at": worst[metric][1],
        }

    windowed_off = sum(
        item["above_tolerance"]
        for item in report["metrics"].values()
        if item["family"] == "windowed"
    )
    report["verdict"] = {
        "tolerance": TOLERANCE,
        "windowed_metrics_above_tolerance": windowed_off,
        "null_mismatches": sum(
            item["null_mismatch"] for item in report["metrics"].values()
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
