"""Fetch Fubon's adjusted daily candles into `third-party/fubon/raw/` (Step 36).

Run with the trade project's interpreter (it has `fubon_neo`):

    ~/GitHubLL/my_trade_project/venv/bin/python third-party/fubon/fetch_adjusted.py

`compare_adjusted.py` then compares them with `adjusted_prices_pit:v1` from this
repository's own interpreter. The cases are one per kind of exchange result
event; a window is at most a year, so the range is asked in yearly pieces.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import finish, login, save

CASES = ("2330", "6225", "2429", "1815", "5906", "2380", "1563", "6461", "3152", "7780",
         "4747")
START, END = date(2024, 6, 1), date(2026, 9, 11)


def windows() -> list[tuple[date, date]]:
    out, start = [], START
    while start <= END:
        end = min(date(start.year + 1, start.month, start.day) - timedelta(days=1), END)
        out.append((start, end))
        start = end + timedelta(days=1)
    return out


def main() -> int:
    stock = login()
    failures = 0
    for symbol in CASES:
        for start, end in windows():
            request = {"symbol": symbol, "from": start.isoformat(), "to": end.isoformat(),
                       "adjusted": "true"}
            try:
                body = stock.historical.candles(**request)
            except Exception as error:  # noqa: BLE001 - one request must not end the run
                failures += 1
                print(f"{symbol} {start}: {str(error)[:200]}")
                continue
            path = save(f"adjusted_{symbol}_{start}_{end}", request, body)
            print(f"{symbol} {start}..{end}: {len(body.get('data', []))} rows -> {path.name}")
    return 1 if failures else 0


if __name__ == "__main__":
    finish(main())
