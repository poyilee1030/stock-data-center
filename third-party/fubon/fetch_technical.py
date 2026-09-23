"""Fetch Fubon's technical indicators and daily candles into `third-party/fubon/raw/`.

Run with the trade project's interpreter (it has `fubon_neo`):

    ~/GitHubLL/my_trade_project/venv/bin/python third-party/fubon/fetch_technical.py \
        --symbol 2330 --from 2026-09-01 --to 2026-09-22

    # daily candles only, e.g. to see whether no-trade days are kept
    ~/GitHubLL/my_trade_project/venv/bin/python third-party/fubon/fetch_technical.py \
        --candles-only --symbol 6615 --symbol 6496 --symbol 6597 \
        --from 2026-09-07 --to 2026-09-15

Parameters are Fubon's own names. KD is requested as rPeriod 9 / kPeriod 3 /
dPeriod 3 and MACD as 12/26/9, the same periods as `technical_indicators:v1`;
what Fubon does with them is what `compare_indicators.py` finds out.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import finish, login, save

SMA_PERIODS = (5, 10, 20, 60)
TECHNICAL = {
    "kdj": {"rPeriod": 9, "kPeriod": 3, "dPeriod": 3},
    "rsi6": {"period": 6},
    "rsi12": {"period": 12},
    "macd": {"fast": 12, "slow": 26, "signal": 9},
    "bb": {"period": 20},
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--candles-only", action="store_true")
    args = parser.parse_args()

    stock = login()
    window = {"from": args.start, "to": args.end}
    failures = 0
    for symbol in args.symbol:
        requests = [("candles", stock.historical.candles, {})]
        if not args.candles_only:
            requests += [
                (f"sma{period}", stock.technical.sma, {"period": period})
                for period in SMA_PERIODS
            ]
            requests += [
                (name, getattr(stock.technical, name.rstrip("0123456789")), params)
                for name, params in TECHNICAL.items()
            ]
        for name, call, params in requests:
            request = {"symbol": symbol, **params, **window}
            try:
                body = call(**request)
            except Exception as error:  # noqa: BLE001 - one endpoint must not end the run
                failures += 1
                print(f"{symbol} {name}: {str(error)[:200]}")
                continue
            path = save(f"{name}_{symbol}_{args.start}_{args.end}", request, body)
            rows = len(body.get("data", [])) if isinstance(body, dict) else "?"
            print(f"{symbol} {name}: {rows} rows -> {path.name}")
    return 1 if failures else 0


if __name__ == "__main__":
    finish(main())
