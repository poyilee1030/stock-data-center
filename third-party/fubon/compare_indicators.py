"""Compare Fubon's indicators with legacy `stock_db` and the Data Center.

Reads the responses `fetch_technical.py` saved, so it runs from this repo's
venv without logging in:

    .venv/bin/python third-party/fubon/compare_indicators.py --symbol 2330 \
        --from 2026-09-01 --to 2026-09-22

Three columns per date: legacy `technical_indicators`, the Data Center's
on-demand `technical_indicators:v1` (only where `stock_data_center.derived`
is importable — it is not on `main` until Step 26-a merges — and only through
the backfill's last trade date), and Fubon.

Then it tests what Fubon's KD and MACD actually are, against the official
closes in `stockdc_backfill`:

- KD as the Western slow stochastic: K = 3-day simple mean of RSV,
  D = 3-day simple mean of K (ours and legacy are the recursive
  K = 2/3 K' + 1/3 RSV);
- MACD as EMAs seeded at some start date: the start that reproduces Fubon
  exactly shows its warm-up follows the requested window.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import latest

DATA_CENTER_URL = "postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill"
LEGACY_URL = "postgresql+psycopg://user:password@127.0.0.1:5419/stock_db"
KNOWLEDGE_AS_OF = "2026-09-23T12:00:00+08:00"
COLUMNS = ("ma5", "ma10", "ma20", "ma60", "k", "d", "rsi6", "rsi12", "macd_dif", "macd_dea")


def _fubon(symbol: str, start: str, end: str) -> dict[str, dict]:
    suffix = f"{symbol}_{start}_{end}"
    out: dict[str, dict] = {}

    def rows(name: str) -> list[dict]:
        return latest(f"{name}_{suffix}")["body"].get("data", [])

    for period in (5, 10, 20, 60):
        for row in rows(f"sma{period}"):
            out.setdefault(row["date"], {})[f"ma{period}"] = row["sma"]
    for row in rows("kdj"):
        out.setdefault(row["date"], {}).update(k=row["k"], d=row["d"])
    for name in ("rsi6", "rsi12"):
        for row in rows(name):
            out.setdefault(row["date"], {})[name] = row["rsi"]
    for row in rows("macd"):
        out.setdefault(row["date"], {}).update(
            macd_dif=row["macdLine"], macd_dea=row["signalLine"]
        )
    return out


def _legacy(symbol: str, start: str, end: str) -> dict[str, dict]:
    engine = sa.create_engine(LEGACY_URL)
    with engine.connect() as connection:
        result = connection.execute(
            sa.text(
                f"SELECT date, {', '.join(COLUMNS)} FROM technical_indicators "
                "WHERE symbol = :symbol AND date BETWEEN :start AND :end"
            ),
            {"symbol": symbol, "start": start, "end": end},
        )
        return {row[0]: dict(zip(COLUMNS, row[1:], strict=True)) for row in result}


def _ours(symbol: str, start: str, end: str, source: str) -> dict[str, dict] | None:
    try:
        from stock_data_center.derived import TechnicalIndicatorService
    except ImportError:
        return None
    engine = sa.create_engine(DATA_CENTER_URL)
    with engine.connect() as connection:
        rows = TechnicalIndicatorService().rolling(
            connection,
            security_code=symbol,
            start_date=date.fromisoformat(start),
            end_date=date.fromisoformat(end),
            source=source,
            knowledge_as_of=datetime.fromisoformat(KNOWLEDGE_AS_OF),
        )
    return {row.observation_date.isoformat(): dict(row.metrics) for row in rows}


def _prices(symbol: str, source: str, end: str):
    """Official closes, highs and lows in trade-date order, traded days only."""
    engine = sa.create_engine(DATA_CENTER_URL)
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                """
                SELECT p.trade_date, p.close_price, p.high_price, p.low_price
                  FROM daily_price_versions p JOIN security s ON s.id = p.security_id
                 WHERE s.security_code = :symbol AND p.source = :source
                   AND p.trade_date <= :end AND p.close_price IS NOT NULL
                 ORDER BY p.trade_date
                """
            ),
            {"symbol": symbol, "source": source, "end": date.fromisoformat(end)},
        ).all()
    return (
        [row[0].isoformat() for row in rows],
        [float(row[1]) for row in rows],
        [float(row[2]) for row in rows],
        [float(row[3]) for row in rows],
    )


def _slow_stochastic(closes, highs, lows):
    n = len(closes)
    rsv: list[float | None] = [None] * n
    for i in range(8, n):
        low, high = min(lows[i - 8 : i + 1]), max(highs[i - 8 : i + 1])
        rsv[i] = 50.0 if high == low else (closes[i] - low) / (high - low) * 100

    def mean3(values, i):
        span = values[i - 2 : i + 1] if i >= 2 else []
        return None if len(span) < 3 or None in span else sum(span) / 3

    k = [mean3(rsv, i) for i in range(n)]
    d = [mean3(k, i) for i in range(n)]
    return k, d


def _ema(values, span: int, start: int):
    alpha = 2 / (span + 1)
    out: list[float | None] = [None] * len(values)
    weighted = None
    for i in range(start, len(values)):
        weighted = values[i] if weighted is None else alpha * values[i] + (1 - alpha) * weighted
        out[i] = weighted
    return out


def _macd_seed(days, closes, fubon) -> tuple[float, str] | None:
    """The EMA start date that best reproduces Fubon's MACD, and its error."""
    targets = [(i, fubon[day]) for i, day in enumerate(days) if "macd_dif" in fubon.get(day, {})]
    if not targets:
        return None
    best = None
    for start in range(max(0, len(closes) - 400), targets[0][0] + 1):
        fast, slow = _ema(closes, 12, start), _ema(closes, 26, start)
        dif = [None if f is None else f - s for f, s in zip(fast, slow, strict=True)]
        dea = _ema([0.0 if x is None else x for x in dif], 9, start)
        error = max(
            abs(dif[i] - row["macd_dif"]) + abs(dea[i] - row["macd_dea"]) for i, row in targets
        )
        if best is None or error < best[0]:
            best = (error, days[start])
    return best


def _fmt(value) -> str:
    return "      -" if value is None else f"{float(value):8.2f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--source", default="twse_mi_index")
    args = parser.parse_args()

    fubon = _fubon(args.symbol, args.start, args.end)
    legacy = _legacy(args.symbol, args.start, args.end)
    ours = _ours(args.symbol, args.start, args.end, args.source)
    sources = [("legacy", legacy), ("ours", ours), ("fubon", fubon)]
    if ours is None:
        print("(stock_data_center.derived not importable on this branch: 'ours' skipped)")
        sources = [item for item in sources if item[1] is not None]

    print("date       src   " + "".join(f"{c:>9}" for c in COLUMNS))
    for day in sorted(fubon.keys() | legacy.keys()):
        for name, rows in sources:
            if day in rows:
                print(f"{day} {name:6}" + "".join(f" {_fmt(rows[day].get(c))}" for c in COLUMNS))

    print("\nmax |difference| against legacy, on shared dates:")
    for name, rows in sources:
        if name == "legacy":
            continue
        shared = [day for day in rows if day in legacy]
        diffs = {
            c: max(
                (abs(float(rows[d][c]) - float(legacy[d][c]))
                 for d in shared
                 if rows[d].get(c) is not None and legacy[d].get(c) is not None),
                default=None,
            )
            for c in COLUMNS
        }
        print(f"  {name:6}", {c: None if v is None else round(v, 6) for c, v in diffs.items()})

    days, closes, highs, lows = _prices(args.symbol, args.source, args.end)
    k, d = _slow_stochastic(closes, highs, lows)
    print("\nFubon KD vs slow stochastic (3-day simple means) on official prices:")
    for i, day in enumerate(days):
        if day in fubon and "k" in fubon[day] and k[i] is not None:
            print(f"  {day}  K {fubon[day]['k']:7.2f} / {k[i]:7.2f}   D {fubon[day]['d']:7.2f} / {d[i]:7.2f}")
    seed = _macd_seed(days, closes, fubon)
    if seed is not None:
        print(f"\nFubon MACD is best reproduced by EMAs seeded at {seed[1]} (max error {seed[0]:.6f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
