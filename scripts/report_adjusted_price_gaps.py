#!/usr/bin/env python
"""Classify every daily price gap beyond the price limit, raw and adjusted (Step 36).

The threshold is the exchanges' daily price limit: a close may move at most 10%
from its reference, which is the previous close, so between two traded closes
k trading days apart the raw close can rise at most 1.1**k - 1 and fall at most
1 - 0.9**k. A gap beyond that bound needs a reason, and CLAUDE.md §78 fixes the
classes:

`explained_by_corporate_action`
    An exchange result event of the series' own exchange falls in (previous
    traded date, date]: the exchange reset the reference price.
`explained_by_other_documented_market_event`
    `new_listing`: the date is among the first five trading days of the stock's
    listing span on this market, which the exchanges trade without a price
    limit. The listing date is the exchange's own (`listings`, Step 38-a).
    `series_start`: no listing span says so, but the date is among the first
    five trading days of a series that starts inside the window — a listing the
    universe keeps no span for, such as a TWSE innovation-board debut. Listed
    for review with the others.
    `within_published_limits` (adjusted series only): the raw close of the
    ex-date is inside the limit prices the exchange published for it in the
    event's own list file (漲停價格/跌停價格, 漲停價/跌停價), read from the raw
    file the event row names. For a cash capital increase those limits are not
    centred on the ex-rights reference price: TWSE computes both from the
    dividend-only base (6225 on 2026-08-18: reference 30.04, 開盤競價基準 44.40,
    limit up 48.80), TPEx the limit up from it and the limit down from the
    reference (8097 on 2021-07-01: reference 30.80, 開始交易基準價 34.40, limits
    27.75 and 37.80). A market that ignores the dilution then moves the adjusted
    series by the subscription right's value, inside the exchange's own limits.
`unexplained_anomaly`
    Everything else, listed for review. Nothing is smoothed.

The same classification runs on the adjusted series of `adjusted_prices_pit:v1`
as the latest PIT context sees it: there an event day must be continuous, so
an adjusted gap explained only by an event is a failure of the adjustment and
is listed too. The script also counts how often an event's `close_before` is
the series' previous traded close, the premise of that continuity.

Exit 1 on an unexplained raw gap or a discontinuous event day. Run it from the
repository root, where `data/raw` holds the raw files.

    python scripts/report_adjusted_price_gaps.py \\
        --database-url postgresql+psycopg://stockdc:stockdc@localhost:26519/stockdc_backfill \\
        --start 2020-01-02 --end 2026-09-11 --output report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import cache
from itertools import pairwise
from pathlib import Path

import sqlalchemy as sa

from stock_data_center.db import schema_v2 as v2
from stock_data_center.v2 import visibility
from stock_data_center.v2.adjusted_prices import PRICE_SOURCE, AdjustedPrices

LIMIT = 0.10
NO_LIMIT_DAYS = 5
MARKET = {"twse_mi_index": "sii", "tpex_otc_quotes": "otc"}
EPSILON = 1e-9
RAW = Path("data/raw")


@cache
def published_limits(sha256: str) -> dict[tuple[str, date], tuple[Decimal, Decimal]]:
    """(limit down, limit up) per (code, date) in one result-feed list file."""
    body = json.loads((RAW / sha256[:2] / sha256).read_bytes())
    table = body["tables"][0] if "tables" in body else body
    fields = table["fields"]
    up = next((i for i, f in enumerate(fields) if f.startswith("漲停價")), None)
    down = next((i for i, f in enumerate(fields) if f.startswith("跌停價")), None)
    if up is None or down is None:
        return {}
    out = {}
    for row in table["data"]:
        try:
            year, month, day = (int(p) for p in row[0].replace("年", "/").replace("月", "/")
                                .replace("日", "").split("/"))
            out[(row[1].strip(), date(year + 1911, month, day))] = (
                Decimal(row[down].replace(",", "")), Decimal(row[up].replace(",", "")))
        except (ValueError, ArithmeticError):  # a "-" in place of a price
            continue
    return out


def beyond_limit(ratio: float, sessions: int) -> bool:
    return ratio > (1 + LIMIT) ** sessions + EPSILON or ratio < (1 - LIMIT) ** sessions - EPSILON


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 2))
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", help="write every classified gap here as JSON")
    args = parser.parse_args()
    engine = sa.create_engine(args.database_url)
    now = datetime.now(UTC)
    pit = visibility.MarketPIT(now, now)
    service = AdjustedPrices(git_commit="report")
    counts: Counter = Counter()
    gaps: list[dict] = []
    continuity: list[dict] = []
    differs: list[dict] = []
    with engine.connect() as connection:
        days = connection.scalars(sa.select(v2.trading_days.c.trade_date)
                                  .order_by(v2.trading_days.c.trade_date)).all()
        spans: dict[tuple[str, str], list[date]] = {}
        for stock_id, market, listed_on in connection.execute(
                sa.select(v2.listings.c.stock_id, v2.listings.c.market, v2.listings.c.listed_on)
                .where(v2.listings.c.listed_on.is_not(None))):
            spans.setdefault((stock_id, market), []).append(listed_on)
        series_keys = connection.execute(
            sa.select(v2.daily_prices.c.stock_id, v2.daily_prices.c.source).distinct()
            .where(v2.daily_prices.c.stock_id.in_(sa.select(v2.stocks.c.stock_id)))
            .order_by(v2.daily_prices.c.stock_id, v2.daily_prices.c.source)).all()

        def sessions(after: date, through: date) -> int:
            return bisect_right(days, through) - bisect_right(days, after)

        def nth_day(start: date, day: date) -> int:
            return bisect_right(days, day) - bisect_left(days, start)

        def listing(stock_id: str, source: str, first: date, day: date) -> str | None:
            listed = [d for d in spans.get((stock_id, MARKET[source]), ()) if d <= day]
            if any(nth_day(d, day) <= NO_LIMIT_DAYS for d in listed):
                return "new_listing"
            if first > args.start and nth_day(first, day) <= NO_LIMIT_DAYS and not any(
                    d >= first for d in listed):
                return "series_start"
            return None

        sha = dict(connection.execute(sa.select(v2.fetches.c.id, v2.fetches.c.sha256)
                                      .where(v2.fetches.c.sha256.is_not(None))).all())

        def within_limits(applied, stock_id: str, day: date, close) -> bool:
            on_day = [e for e in applied if e.row["ex_date"] == day]
            if not on_day:
                return False
            for e in on_day:
                limits = published_limits(sha[e.row["fetch_id"]].hex()).get((stock_id, day))
                if limits is None or not limits[0] <= close <= limits[1]:
                    return False
            return True

        for stock_id, source in series_keys:
            series = service.compute(connection, stock_id=stock_id, start_date=args.start,
                                     end_date=args.end, pit=pit, source=source)
            counts["series"] += 1
            events = sorted(e.row["ex_date"] for e in series.events)
            by_date = {e.row["ex_date"]: e for e in series.events}
            traded = [r for r in series.rows if r.bar.close is not None]
            first = series.rows[0].bar.trade_date if series.rows else None
            counts["closes"] += len(traded)
            for previous, row in pairwise(traded):
                before, day = previous.bar.trade_date, row.bar.trade_date
                k = sessions(before, day)
                event = bisect_right(events, day) > bisect_right(events, before)
                if event and day in by_date:
                    # The premise of continuity: the exchange's close before is our
                    # previous traded close.
                    e = by_date[day]
                    same = e.row["close_before"] == previous.bar.close
                    counts["event_close_before_is_previous_close" if same
                           else "event_close_before_differs"] += 1
                    if not same:
                        differs.append({"stock_id": stock_id, "source": e.row["source"],
                                        "ex_date": day.isoformat(),
                                        "close_before": float(e.row["close_before"]),
                                        "previous_traded_date": before.isoformat(),
                                        "previous_close": float(previous.bar.close)})
                for kind, a, b in (("raw", previous.bar.close, row.bar.close),
                                   ("adjusted", previous.adjusted_close, row.adjusted_close)):
                    if a is None or b is None:
                        counts[f"{kind}_unknown"] += 1
                        continue
                    ratio = float(b) / float(a)
                    if not beyond_limit(ratio, k):
                        continue
                    applied = [e for e in series.events if before < e.row["ex_date"] <= day]
                    detail = listing(stock_id, source, first, day)
                    if kind == "adjusted" and event and detail is None and within_limits(
                            applied, stock_id, day, row.bar.close):
                        detail = "within_published_limits"
                    if detail is not None:
                        reason = "explained_by_other_documented_market_event"
                    elif event:
                        reason, detail = "explained_by_corporate_action", "corporate_action"
                    else:
                        reason = "unexplained_anomaly"
                    counts[f"{kind}:{reason}"] += 1
                    counts[f"{kind}:{reason}:{detail}"] += 1
                    gap = {"kind": kind, "stock_id": stock_id, "source": source,
                           "previous_date": before.isoformat(), "date": day.isoformat(),
                           "sessions": k, "previous_close": float(a), "close": float(b),
                           "return": ratio - 1, "class": reason, "detail": detail,
                           "events": [f'{e.row["source"]} {e.row["event_type"]}'
                                      f'{" rights" if e.row["rights_ratio"] else ""}'
                                      for e in applied]}
                    gaps.append(gap)
                    if kind == "adjusted" and reason == "explained_by_corporate_action":
                        continuity.append(gap)
    unexplained = [g for g in gaps if g["kind"] == "raw" and g["class"] == "unexplained_anomaly"]
    summary = {"start": args.start.isoformat(), "end": args.end.isoformat(),
               "price_sources": sorted(set(PRICE_SOURCE.values())),
               "counts": dict(sorted(counts.items())),
               "unexplained_raw": unexplained,
               "series_start": [g for g in gaps if g["detail"] == "series_start"],
               "within_published_limits": [g for g in gaps
                                           if g["detail"] == "within_published_limits"],
               "discontinuous_event_days": continuity,
               "close_before_differs": differs}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.output:
        with open(args.output, "w") as out:
            json.dump({**summary, "gaps": gaps}, out, ensure_ascii=False, indent=1)
    return 1 if unexplained or continuity else 0


if __name__ == "__main__":
    sys.exit(main())
