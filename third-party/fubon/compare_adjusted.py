"""Compare Fubon's adjusted candles with `adjusted_prices_pit:v1` (Step 36).

A verification cross-check only (CLAUDE.md §30.1): a disagreement is
classified, never fixed by moving our values to Fubon's. Fubon has no PIT and
does not publish its adjustment method, and it drops no-trade days, so the
comparison is of close-to-close returns between the days both sides price,
which do not depend on where either series is anchored. Reads the responses
`fetch_adjusted.py` saved; run from the repo root with this repo's interpreter:

    .venv/bin/python third-party/fubon/compare_adjusted.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import RAW_DIR
from fetch_adjusted import CASES, END, START, windows

from stock_data_center.v2 import visibility
from stock_data_center.v2.adjusted_prices import AdjustedPrices

DATABASE_URL = "postgresql+psycopg://stockdc:stockdc@localhost:26519/stockdc_backfill"
TOLERANCE = 2e-3  # Fubon rounds adjusted prices to the tick; a return moves by up to a tick


def _fubon(symbol: str) -> dict[date, float]:
    closes: dict[date, float] = {}
    for start, end in windows():
        [path] = sorted(RAW_DIR.glob(f"adjusted_{symbol}_{start}_{end}_*.json"))[-1:]
        for row in json.loads(path.read_text(encoding="utf-8"))["body"]["data"]:
            closes[date.fromisoformat(row["date"])] = float(row["close"])
    return closes


def main() -> int:
    engine = sa.create_engine(DATABASE_URL)
    now = datetime.now(UTC)
    service = AdjustedPrices(git_commit="check")
    with engine.connect() as connection:
        for code in CASES:
            series = service.compute(connection, stock_id=code, start_date=START, end_date=END,
                                     pit=visibility.MarketPIT(now, now))
            events = {e.row["ex_date"]: e.row["event_type"] for e in series.events}
            ours = {r.bar.trade_date: r.adjusted_close for r in series.rows
                    if r.adjusted_close is not None}
            theirs = _fubon(code)
            days = sorted(set(ours) & set(theirs))
            differ = []
            for previous, day in pairwise(days):
                a, b = ours[day] / ours[previous], theirs[day] / theirs[previous]
                if abs(a / b - 1) > TOLERANCE:
                    differ.append((previous, day, a, b))
            print(f"{code} {series.source}: {len(days)} shared days, {len(events)} events "
                  f"({', '.join(f'{d} {t}' for d, t in sorted(events.items()))}), "
                  f"{len(differ)} returns beyond {TOLERANCE}")
            for previous, day, a, b in differ:
                inside = [f"{d} {t}" for d, t in events.items() if previous < d <= day]
                print(f"    {previous}..{day}  ours {a - 1:+.4%}  Fubon {b - 1:+.4%}  "
                      f"({'; '.join(inside) or 'no event'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
