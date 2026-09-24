#!/usr/bin/env python
"""Step 26-b acceptance: the stored derived tables against their references.

Run after a full run (`derived_store --full`, or a first run), so every stored
row was computed from its series' first row. Three checks, every series:

`pit`
    `technical_indicators` equals the on-demand `technical_indicators_pit:v1`
    rolling series bit for bit. Holds while no input row has a correction; the
    script counts the corrected keys and says so if there are any.
`incremental`
    What an incremental run would write when restarting each series at each
    `--restart` date, against the stored full series: windowed technical
    metrics and every streak exactly, the exponential technical metrics within
    `derived_store.within_tolerance`. The largest residue per metric is
    reported, since the tolerance was set from it.
`coverage`
    One stored row per input date: every latest `daily_prices` row for the
    technical indicators, every traded day for the streaks.

Exit 1 if any check fails.

    python scripts/verify_derived_store.py \
        --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.v2 import derived_store as ds
from stock_data_center.v2.derived import TechnicalIndicators
from stock_data_center.v2.indicators import METRIC_CODES

RESTARTS = ("2021-06-01", "2022-06-01", "2023-06-01", "2024-06-03", "2025-06-02", "2026-09-01")
STREAKS = tuple(f"{party}_streak_days" for party in ds.PARTIES)


def _rows(connection, table: str, stock_id: str, source: str) -> dict[date, dict]:
    return {
        row["trade_date"]: dict(row)
        for row in connection.execute(
            sa.text(f"SELECT * FROM {table} WHERE stock_id = :s AND source = :r"),
            {"s": stock_id, "r": source}).mappings()
    }


def _closes(connection, stock_id: str, source: str) -> dict[date, float]:
    """Each date's latest close on or before it, for the price-scaled tolerance."""
    out, last = {}, None
    for day, close in connection.execute(sa.text(
            "SELECT DISTINCT ON (trade_date) trade_date, close_price FROM daily_prices "
            "WHERE stock_id = :s AND source = :r ORDER BY trade_date, recorded_at DESC"),
            {"s": stock_id, "r": source}):
        last = float(close) if close is not None else last
        out[day] = last
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--restart", action="append", default=None,
                        help="a date an incremental run restarts at (repeatable)")
    parser.add_argument("--limit", type=int, help="only the first N series of each table")
    args = parser.parse_args()
    restarts = [date.fromisoformat(d) for d in (args.restart or RESTARTS)]
    engine = sa.create_engine(args.database_url)
    report: dict = {"restarts": [d.isoformat() for d in restarts]}
    failures: list[str] = []
    started = time.time()
    with engine.connect() as c:
        report["corrected_price_keys"] = c.scalar(sa.text(
            "SELECT count(*) FROM (SELECT 1 FROM daily_prices GROUP BY stock_id, source, "
            "trade_date HAVING count(*) > 1) x"))
        knowledge = datetime.now(UTC) + timedelta(minutes=1)
        pit = TechnicalIndicators(git_commit="verify")

        # ------------------------------------------------ technical indicators
        series = c.execute(sa.text(
            "SELECT DISTINCT stock_id, source FROM daily_prices ORDER BY 1, 2")).all()
        series = series[: args.limit] if args.limit else series
        pit_differs = coverage = 0
        worst: dict[str, list] = {}
        exact_differs: dict[str, int] = {}
        compared = 0
        for stock_id, source in series:
            stored = _rows(c, "technical_indicators", stock_id, source)
            closes = _closes(c, stock_id, source)
            if set(stored) != set(closes):
                coverage += 1
                failures.append(f"technical coverage {stock_id}/{source}")
            reference = pit.rolling(c, stock_id=stock_id, start_date=min(closes),
                                    end_date=max(closes), source=source,
                                    knowledge_as_of=knowledge)
            if len(reference) != len(stored) or any(
                    {k: stored[r.observation_date][k] for k in METRIC_CODES} != dict(r.metrics)
                    for r in reference):
                pit_differs += 1
                failures.append(f"pit {stock_id}/{source}")
            for restart in restarts:
                for row in ds._technical_rows(c, stock_id, source, restart):
                    full = stored[row["trade_date"]]
                    compared += 1
                    for code in METRIC_CODES:
                        a, b = row[code], full[code]
                        if a == b:
                            continue
                        if not ds.within_tolerance(code, a, b, closes[row["trade_date"]]):
                            exact_differs[code] = exact_differs.get(code, 0) + 1
                            failures.append(f"incremental {stock_id}/{source} "
                                            f"{row['trade_date']} {code} {a!r} {b!r}")
                            continue
                        residue = abs(a - b)
                        scale = closes[row["trade_date"]] if code in ds.PRICE_SCALED else 1.0
                        entry = worst.setdefault(code, [0, 0.0, None])
                        entry[0] += 1
                        if residue / scale > entry[1]:
                            entry[1] = residue / scale
                            entry[2] = [stock_id, source, row["trade_date"].isoformat(),
                                        restart.isoformat(), a, b]
        report["technical_indicators"] = {
            "series": len(series), "pit_series_differing": pit_differs,
            "coverage_series_differing": coverage, "incremental_rows_compared": compared,
            "outside_tolerance": exact_differs,
            "residue_within_tolerance": {
                code: {"values": n, "largest": largest, "where": where,
                       "unit": "of close" if code in ds.PRICE_SCALED else "absolute"}
                for code, (n, largest, where) in sorted(worst.items())
            },
        }

        # ------------------------------------------------ streaks
        series = c.execute(sa.text(
            "SELECT DISTINCT stock_id, source FROM institutional_streaks ORDER BY 1, 2")).all()
        series = series[: args.limit] if args.limit else series
        coverage = differs = compared = 0
        for stock_id, source in series:
            stored = _rows(c, "institutional_streaks", stock_id, source)
            traded = set(c.scalars(sa.text(
                "SELECT trade_date FROM (SELECT DISTINCT ON (trade_date) trade_date, volume "
                "FROM daily_prices WHERE stock_id = :s AND source = :r "
                "ORDER BY trade_date, recorded_at DESC) x WHERE volume > 0"),
                {"s": stock_id, "r": ds.PRICE_SOURCE[source]}))
            if set(stored) != traded:
                coverage += 1
                failures.append(f"streak coverage {stock_id}/{source}")
            for restart in restarts:
                for row in ds._streak_rows(c, stock_id, source, restart):
                    compared += 1
                    if any(row[k] != stored[row["trade_date"]][k] for k in STREAKS):
                        differs += 1
                        failures.append(f"streak incremental {stock_id}/{source} "
                                        f"{row['trade_date']}")
        report["institutional_streaks"] = {
            "series": len(series), "coverage_series_differing": coverage,
            "incremental_rows_compared": compared, "incremental_rows_differing": differs,
        }
    report["seconds"] = round(time.time() - started)
    report["failures"] = len(failures)
    print(json.dumps(report, indent=2, default=str))
    for failure in failures[:50]:
        print("FAIL", failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
