#!/usr/bin/env python
"""Reconcile `technical_indicators:v1` against legacy `stock_db`.

Step 26-a. Nothing is materialised (ROADMAP §17), so "ours" is the rolling
as-of series computed on demand, one security at a time, exactly as a reader
would get it; the run also times each security, which is the latency that
decides whether on-demand computation is fast enough.

Every derived step reconciles against the legacy tables its
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
        --start 2020-01-02 --end 2026-09-11 \
        --knowledge-as-of 2026-09-23T12:00:00+08:00
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.derived import TechnicalIndicatorService
from stock_data_center.derived.indicators import METRIC_CODES

SOURCES = ("twse_mi_index", "tpex_otc_quotes")

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


def _codes(connection, *, source: str, start: date, end: date) -> list[str]:
    """Every security with a price from this source in the window.

    Taken from the stored prices rather than from today's universe: a security
    that left the market inside the window still has a series inside it.
    """
    return list(
        connection.scalars(
            sa.text(
                """
                SELECT s.security_code FROM security s
                 WHERE EXISTS (SELECT 1 FROM daily_price_versions p
                                WHERE p.security_id = s.id AND p.source = :source
                                  AND p.trade_date BETWEEN :start AND :end)
                 ORDER BY s.security_code
                """
            ),
            {"source": source, "start": start, "end": end},
        )
    )


def _ours(service, connection, *, code: str, sources, start, end, knowledge_as_of):
    """The security's rolling series, keyed like the legacy table.

    A security that transferred market has one series per source (CLAUDE.md
    §30); it trades on one market on any given day, so the two never share a
    key.
    """
    out: dict = {}
    for source in sources:
        for row in service.rolling(
            connection,
            security_code=code,
            start_date=start,
            end_date=end,
            source=source,
            knowledge_as_of=knowledge_as_of,
        ):
            out[(code, row.observation_date)] = dict(row.metrics)
    return out


def _legacy(connection, *, code: str, start: date, end: date, metrics) -> dict:
    columns = ", ".join(metrics)
    statement = sa.text(
        f"SELECT symbol, date, {columns} FROM technical_indicators "
        "WHERE symbol = :code AND date BETWEEN :start AND :end"
    )
    out: dict = {}
    for row in connection.execute(
        statement, {"code": code, "start": start.isoformat(), "end": end.isoformat()}
    ):
        out[(row[0], date.fromisoformat(row[1]))] = {
            metric: None if value is None else float(value)
            for metric, value in zip(metrics, row[2:], strict=True)
        }
    return out


def _legacy_codes(connection, *, start: date, end: date) -> set[str]:
    return set(
        connection.scalars(
            sa.text(
                "SELECT DISTINCT symbol FROM technical_indicators "
                "WHERE date BETWEEN :start AND :end"
            ),
            {"start": start.isoformat(), "end": end.isoformat()},
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", required=True)
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-09-11")
    parser.add_argument(
        "--knowledge-as-of",
        help="the evidence cutoff the series is computed at, ISO with an "
        "offset; defaults to now",
    )
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

    knowledge_as_of = (
        datetime.fromisoformat(args.knowledge_as_of) if args.knowledge_as_of else None
    )
    if knowledge_as_of is not None and knowledge_as_of.tzinfo is None:
        parser.error("--knowledge-as-of must carry an offset")

    our_engine = sa.create_engine(args.database_url)
    legacy_engine = sa.create_engine(args.legacy_database_url)
    service = TechnicalIndicatorService()

    keys = Counter()
    legacy_only_sample: list[str] = []
    compared: Counter = Counter()
    both_null: Counter = Counter()
    null_mismatch: Counter = Counter()
    buckets: dict[str, Counter] = {metric: Counter() for metric in metrics}
    worst: dict[str, tuple[float, str | None]] = {
        metric: (0.0, None) for metric in metrics
    }
    seconds: list[float] = []

    with our_engine.connect() as ours_connection, legacy_engine.connect() as legacy_connection:
        by_code: dict[str, list[str]] = {}
        for source in SOURCES:
            for code in _codes(ours_connection, source=source, start=start, end=end):
                by_code.setdefault(code, []).append(source)
        codes = sorted(by_code.keys() | _legacy_codes(legacy_connection, start=start, end=end))
        if selected is not None:
            codes = [code for code in codes if code in selected]

        for index, code in enumerate(codes, start=1):
            began = time.perf_counter()
            ours = _ours(
                service,
                ours_connection,
                code=code,
                sources=by_code.get(code, ()),
                start=start,
                end=end,
                knowledge_as_of=knowledge_as_of,
            )
            if code in by_code:
                seconds.append(time.perf_counter() - began)
            legacy = _legacy(
                legacy_connection, code=code, start=start, end=end, metrics=metrics
            )
            shared = ours.keys() & legacy.keys()
            keys["ours"] += len(ours)
            keys["legacy"] += len(legacy)
            keys["shared"] += len(shared)
            keys["legacy_only"] += len(legacy.keys() - ours.keys())
            keys["ours_only"] += len(ours.keys() - legacy.keys())
            if len(legacy_only_sample) < 20:
                legacy_only_sample.extend(
                    sorted(
                        f"{key_code} {day.isoformat()}"
                        for key_code, day in legacy.keys() - ours.keys()
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
            if index % 100 == 0:
                print(f"  {index}/{len(codes)}", file=sys.stderr, flush=True)

    report = {
        "window": [start.isoformat(), end.isoformat()],
        "securities_filter": (
            None if selected is None else {"file": args.securities_file,
                                           "count": len(selected)}
        ),
        "keys": dict(keys),
        "on_demand_seconds_per_security": (
            {
                "securities": len(seconds),
                "p50": round(statistics.median(seconds), 3),
                "p95": round(statistics.quantiles(seconds, n=20)[-1], 3),
                "max": round(max(seconds), 3),
                "total": round(sum(seconds), 1),
            }
            if len(seconds) >= 2
            else None
        ),
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
