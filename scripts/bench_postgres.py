#!/usr/bin/env python
"""Time read-only workloads against a database, to measure a PostgreSQL setting.

CLAUDE.md §63: no performance claim without a measurement. Each workload is one
the Data Center really runs, and none writes:

`adjusted_prices_all`  `adjusted_prices_pit:v1` for every (stock, source) series
`market_month`         the API's whole-market daily prices over 31 days
`market_year`          whole-market daily prices over a year (a large window sort)
`price_gaps_sql`       LAG over every daily price (the gap query of Step 36)
`indicators_pit_50`    `technical_indicators_pit:v1` for 50 stocks

Each run reports wall seconds and, from `pg_stat_database`, the blocks found in
shared buffers (`hit`), the blocks read from outside them (`read`, served by the
OS page cache or the disk) and the bytes spilled to temporary files (`temp`,
a sort or hash larger than `work_mem`). Run it once right after PostgreSQL
starts (its buffers are empty) and again warm.

    python scripts/bench_postgres.py --database-url ... --label before --runs 3
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime

import sqlalchemy as sa

from stock_data_center.db import schema_v2 as v2
from stock_data_center.v2 import visibility
from stock_data_center.v2.adjusted_prices import AdjustedPrices
from stock_data_center.v2.derived import TechnicalIndicators

START, END = date(2020, 1, 2), date(2026, 9, 11)
GAPS_SQL = """
WITH p AS (
    SELECT DISTINCT ON (stock_id, source, trade_date) stock_id, source, trade_date, close_price
    FROM daily_prices WHERE close_price IS NOT NULL
    ORDER BY stock_id, source, trade_date, recorded_at DESC)
SELECT count(*) FROM (
    SELECT close_price / lag(close_price) OVER (PARTITION BY stock_id, source
                                                ORDER BY trade_date) AS r FROM p) g
WHERE abs(r - 1) > 0.1
"""


def workloads(connection) -> dict[str, Callable[[], int]]:
    now = datetime.now(UTC)
    pit = visibility.MarketPIT(now, now)
    series = connection.execute(
        sa.select(v2.daily_prices.c.stock_id, v2.daily_prices.c.source).distinct()
        .order_by(v2.daily_prices.c.stock_id, v2.daily_prices.c.source)).all()
    adjusted = AdjustedPrices(git_commit="bench")
    indicators = TechnicalIndicators(git_commit="bench")
    fifty = [s for s in series if s.source == "twse_mi_index"][:50]

    def adjusted_prices_all() -> int:
        return sum(len(adjusted.compute(connection, stock_id=s, start_date=START, end_date=END,
                                        pit=pit, source=src).rows) for s, src in series)

    def market(start: date, end: date) -> Callable[[], int]:
        return lambda: len(visibility.rows(connection, "daily_prices", pit, start=start, end=end))

    def indicators_pit_50() -> int:
        return sum(len(indicators.compute(connection, stock_id=s, start_date=START, end_date=END,
                                          information_as_of=now, knowledge_as_of=now,
                                          source=src)) for s, src in fifty)

    return {
        "adjusted_prices_all": adjusted_prices_all,
        "market_month": market(date(2026, 8, 12), date(2026, 9, 11)),
        "market_year": market(date(2025, 9, 12), date(2026, 9, 11)),
        "price_gaps_sql": lambda: connection.scalar(sa.text(GAPS_SQL)),
        "indicators_pit_50": indicators_pit_50,
    }


def stats(connection) -> tuple[int, int, int]:
    return tuple(connection.execute(sa.text(
        "SELECT blks_hit, blks_read, temp_bytes FROM pg_stat_database "
        "WHERE datname = current_database()")).one())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--label", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", help="append the results here as JSON lines")
    args = parser.parse_args()
    engine = sa.create_engine(args.database_url)
    results = []
    with engine.connect() as connection:
        settings = {name: connection.execute(sa.text(f"SHOW {name}")).scalar() for name in
                    ("shared_buffers", "effective_cache_size", "work_mem",
                     "maintenance_work_mem", "random_page_cost")}
        print(json.dumps({"label": args.label, **settings}))
        for name, work in workloads(connection).items():
            runs = []
            for run in range(args.runs):
                connection.commit()  # a fresh snapshot, and pg_stat reads current counters
                before = stats(connection)
                started = time.perf_counter()
                rows = work()
                seconds = time.perf_counter() - started
                connection.commit()
                time.sleep(0.6)  # pg_stat_database is flushed about every 500 ms
                after = stats(connection)
                hit, read, temp = (a - b for a, b in zip(after, before, strict=True))
                runs.append({"run": run + 1, "seconds": round(seconds, 3), "rows": rows,
                             "hit": hit, "read": read, "temp_bytes": temp})
            result = {"label": args.label, "workload": name, **settings, "runs": runs,
                      "first": runs[0]["seconds"],
                      "warm_median": statistics.median(r["seconds"] for r in runs[1:])
                      if len(runs) > 1 else None}
            results.append(result)
            print(json.dumps(result))
    if args.output:
        with open(args.output, "a") as out:
            for result in results:
                out.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
