"""Compare FinMind `TaiwanStockPrice` with the Data Center's official daily prices.

Two questions:

1. For an ordinary liquid security (2330), are OHLC and volume the same?
2. For thinly traded securities with official no-trade rows (volume 0, prices
   NULL), does FinMind keep the day, drop it like legacy and Fubon, or fill it?

Run from the repo root:

    .venv/bin/python third-party/finmind/check_daily_price.py
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import fetch

DATABASE_URL = "postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill"

CASES = (
    ("2330", "2026-09-01", "2026-09-11"),
    ("6615", "2026-09-07", "2026-09-11"),
    ("6496", "2026-09-07", "2026-09-11"),
    ("6597", "2026-09-07", "2026-09-11"),
)

# FinMind field -> Data Center column. Volume is in shares on both sides.
FIELDS = {
    "open": "open_price",
    "max": "high_price",
    "min": "low_price",
    "close": "close_price",
    "Trading_Volume": "volume",
}


def _ours(connection, code: str, start: str, end: str) -> dict[str, dict]:
    rows = connection.execute(
        sa.text(
            """
            SELECT p.trade_date, p.source, p.open_price, p.high_price,
                   p.low_price, p.close_price, p.volume
              FROM daily_price_versions p
              JOIN security s ON s.id = p.security_id
             WHERE s.security_code = :code
               AND p.source IN ('twse_mi_index', 'tpex_otc_quotes')
               AND p.trade_date BETWEEN :start AND :end
            """
        ),
        {"code": code, "start": date.fromisoformat(start), "end": date.fromisoformat(end)},
    ).mappings()
    return {row["trade_date"].isoformat(): dict(row) for row in rows}


def _same(theirs, ours) -> bool:
    if ours is None:
        return theirs in (None, 0, 0.0)
    return Decimal(str(theirs)) == Decimal(ours)


def main() -> int:
    engine = sa.create_engine(DATABASE_URL)
    with engine.connect() as connection:
        for code, start, end in CASES:
            theirs = {row["date"]: row for row in fetch(
                "TaiwanStockPrice", data_id=code, start_date=start, end_date=end
            )}
            ours = _ours(connection, code, start, end)
            print(f"\n{code}  {start} → {end}")
            print(f"  FinMind days: {len(theirs)}   Data Center days: {len(ours)}")
            for day in sorted(ours.keys() | theirs.keys()):
                mine, their = ours.get(day), theirs.get(day)
                if their is None:
                    print(f"  {day}  missing in FinMind   ours: close={mine['close_price']} volume={mine['volume']}")
                    continue
                if mine is None:
                    print(f"  {day}  missing in Data Center   FinMind: {their}")
                    continue
                diffs = [
                    f"{field}={their[field]} vs {mine[column]}"
                    for field, column in FIELDS.items()
                    if not _same(their[field], mine[column])
                ]
                flag = "same" if not diffs else "DIFF " + ", ".join(diffs)
                print(f"  {day}  {flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
