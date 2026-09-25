#!/usr/bin/env python
"""Reconcile the stored `valuation_metrics:v1` against legacy `valuation_daily`.

Legacy keys a row by (date, symbol); ours by (stock, price source, trade date),
and a stock trades in one market on a given day, so the two meet on (stock,
date). Both sides are limited to the stocks on today's list (ADR-0026). Legacy
`ttm_eps_official` / `pe_percentile_official` are our `ttm_eps` /
`pe_percentile` (owner, 2026-09-25). Legacy summed EPS in double precision, so
its TTM is compared after rounding to the two places every EPS has.

Our value differs from legacy's for three reasons this script proves one value
at a time, and anything else is unexplained:

`publication_differs`
    We count a report from its first `published_at` (the statutory deadline
    moved to the next trading day, or an earlier proven first sighting);
    legacy from the unmoved deadline (Q1 05-15, Q2 08-14, Q3 11-14, Q4 03-31).
    Explained when recomputing ours with legacy's dates gives legacy's value,
    or legacy's key only: its row, or no row.
`legacy_report_outside_v1`
    Legacy's last four rows include a report its own `market` column files as
    an emerging, public or non-public company — a stock's filings before it
    listed, or a year it filed outside the two markets. Step 23 keeps only
    listed and OTC filers, so we have no such report. Explained when one of the
    rows legacy summed at that day is one, or is a fourth quarter derived by
    subtracting one (ours then has no single fourth quarter).
`legacy_quarter_missing` / `legacy_q4_fallback`
    Legacy summed its last four *rows*: where it has no row for one of the four
    quarters of our window (1519 2021Q2, 6243 2023Q3 and the like) its sum ran
    over five quarters, and where a year has no Q3 row it took the annual EPS
    as Q4. Explained when legacy lacks that quarter.
`percentile_pe_history_differs`
    A percentile ranks against the series' whole PE history, so one earlier
    difference moves every later rank. Explained when the two PE histories up
    to that day differ and ranking legacy's own PE against legacy's own history
    with our algorithm gives legacy's value. `percentile_market_move` is the
    same where the stock moved market: our series is per price source (CLAUDE.md
    §30) and starts over; legacy's ran on by symbol.

A key only one side has is explained by publication (the other side's window
is not complete yet), a quarter only one side has, or a day only one side
priced (legacy's daily quotes are its priced days). ROE is redefined (owner, 2026-09-25), so it is summarized, not judged.
Exit 1 on anything unexplained.

    python scripts/reconcile_valuation_metrics.py \\
        --database-url postgresql+psycopg://stockdc:stockdc@localhost:26519/stockdc_backfill \\
        --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db \\
        --start 2020-01-02 --end 2026-09-11
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from bisect import bisect_left, bisect_right, insort
from collections import Counter
from datetime import date
from decimal import Decimal

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.v2 import derived_store as ds
from stock_data_center.v2 import valuation

LEGACY_DEADLINE = {1: (0, 5, 15), 2: (0, 8, 14), 3: (0, 11, 14), 4: (1, 3, 31)}


def _legacy_published(year: int, quarter: int) -> date:
    plus, month, day = LEGACY_DEADLINE[quarter]
    return date(year + plus, month, day)


def _two_places(value: float | None) -> float | None:
    return None if value is None else float(Decimal(repr(value)).quantize(Decimal("0.01")))


def _legacy_pe(row) -> float | None:
    ttm, close = row["ttm_eps_official"], row["close"]
    if ttm is None or close is None or ttm <= 0:
        return None
    return valuation._numpy_round(close / ttm, 2)


def _rank(history: list[float], pe: float) -> float:
    less, through = bisect_left(history, pe), bisect_right(history, pe)
    return valuation._numpy_round((less + (through - less + 1) / 2) / len(history) * 100, 4)


def _window(latest: tuple[int, int]) -> list[tuple[int, int]]:
    keys, key = [], latest
    for _ in range(4):
        keys.append(key)
        key = valuation._previous(key)
    return keys


class Tally:
    def __init__(self) -> None:
        self.keys: Counter = Counter()
        self.compared: Counter = Counter()
        self.classes: Counter = Counter()
        self.samples: dict[str, list[str]] = {}
        self.roe_differences: list[float] = []
        self.roe_equal = 0

    def note(self, cls: str, text: str) -> None:
        self.classes[cls] += 1
        bucket = self.samples.setdefault(cls, [])
        if len(bucket) < 10:
            bucket.append(text)


def _stock(stock_id: str, ours_c, legacy_c, have: dict[tuple[int, int], str],
           start: date, end: date, tally: Tally) -> None:
    """Compare one stock's rows, every series of it, and classify each difference."""
    ours = {
        row["trade_date"]: row for row in ours_c.execute(sa.text(
            "SELECT * FROM valuation_metrics WHERE stock_id = :s ORDER BY trade_date"),
            {"s": stock_id}).mappings()
    }
    legacy = {
        date.fromisoformat(row["date"]): row for row in legacy_c.execute(sa.text(
            "SELECT * FROM valuation_daily WHERE symbol = :s ORDER BY date"),
            {"s": stock_id}).mappings()
    }
    if not ours and not legacy:
        return
    our_quarters = valuation.quarters(ds._reports(ours_c, stock_id))
    as_legacy = {
        key: valuation.Quarter(_legacy_published(*key), q.eps, q.net_income, q.equity)
        for key, q in our_quarters.items()
    }
    traded: dict[str, list[tuple[date, float | None]]] = {}
    for source in ours_c.scalars(sa.text(
            "SELECT DISTINCT source FROM daily_prices WHERE stock_id = :s"),
            {"s": stock_id}):
        traded[source] = [
            (day, None if close is None else float(close))
            for day, close, volume in ours_c.execute(ds._latest(
                ds.v2.daily_prices, stock_id, source, None, "close_price", "volume"))
            if volume
        ]
    our_days = {day for days in traded.values() for day, _ in days}
    alternative = {
        day: row for days in traded.values()
        for day, row in valuation.valuations(days, as_legacy)
    }
    legacy_rows = sorted(have, key=lambda k: _legacy_published(*k))
    priced_by_legacy = {
        date.fromisoformat(day) for day in legacy_c.scalars(sa.text(
            "SELECT date FROM daily_quotes WHERE symbol = :s"), {"s": stock_id})
    }

    def outside_v1(day: date) -> str | None:
        """A report outside v1 among the four rows legacy summed at `day`."""
        summed = [k for k in legacy_rows if _legacy_published(*k) <= day][-4:]
        for key in summed:
            if have[key] not in ("sii", "otc"):
                return f"legacy summed {key[0]}Q{key[1]}, filed as {have[key]!r}"
            third = (key[0], 3)
            if key[1] == 4 and have.get(third, "sii") not in ("sii", "otc"):
                return (f"legacy's {key[0]}Q4 is its annual EPS less {key[0]}Q3, "
                        f"filed as {have[third]!r}")
        return None

    def legacy_gap(day: date) -> str | None:
        """The quarter legacy's row-based sum skipped at `day`, if any."""
        published = [k for k, q in as_legacy.items() if q.published_on <= day]
        if not published:
            return None
        window = _window(max(published))
        for key in window:
            if key not in have:
                return f"legacy has no {key[0]}Q{key[1]}"
            if key[1] == 4 and (key[0], 3) not in have:
                return f"legacy has no {key[0]}Q3, so its Q4 is the annual EPS"
        return None

    def our_gap(day: date) -> str | None:
        published = [k for k, q in our_quarters.items() if q.published_on <= day]
        if not published:
            return "no quarter public yet"
        for key in _window(max(published)):
            q = our_quarters.get(key)
            if q is None or q.published_on > day:
                return f"{key[0]}Q{key[1]} not public"
            if q.eps is None:
                return f"{key[0]}Q{key[1]} has no single-quarter EPS"
        return None

    our_history: dict[str, list[float]] = {}
    legacy_history: list[float] = []
    sources_seen: set[str] = set()
    for day in sorted(ours.keys() | legacy.keys()):
        mine, theirs = ours.get(day), legacy.get(day)
        if mine is not None:
            history = our_history.setdefault(mine["source"], [])
            if mine["pe_ratio"] is not None:
                insort(history, mine["pe_ratio"])
            moved = bool(sources_seen - {mine["source"]})
            sources_seen.add(mine["source"])
        legacy_pe = None if theirs is None else _legacy_pe(theirs)
        if legacy_pe is not None:
            insort(legacy_history, legacy_pe)
        if not start <= day <= end:
            continue
        text = f"{stock_id} {day}"
        if theirs is None:
            tally.keys["ours_only"] += 1
            if day not in priced_by_legacy:
                tally.note("ours_only_day_legacy_did_not_price", text)
            elif day not in alternative:
                tally.note("ours_only_publication_differs", text)
            else:
                tally.note("ours_only_unexplained", text)
            continue
        if mine is None:
            tally.keys["legacy_only"] += 1
            if day not in our_days:
                tally.note("legacy_only_day_ours_did_not_price", text)
            elif day in alternative:
                tally.note("legacy_only_publication_differs", text + f" ({our_gap(day)})")
            elif (outside := outside_v1(day)) is not None:
                tally.note("legacy_only_legacy_report_outside_v1", text + f" ({outside})")
            elif (gap := legacy_gap(day)) is not None:
                tally.note("legacy_only_legacy_quarter_missing", text + f" ({gap})")
            else:
                tally.note("legacy_only_unexplained", text + f" ({our_gap(day)})")
            continue
        tally.keys["shared"] += 1
        tally.compared["ttm_eps"] += 1
        legacy_ttm = _two_places(theirs["ttm_eps_official"])
        if mine["ttm_eps"] != legacy_ttm:
            line = f"{text} ttm ours={mine['ttm_eps']} legacy={legacy_ttm}"
            if alternative.get(day, {}).get("ttm_eps") == legacy_ttm:
                tally.note("ttm_publication_differs", line)
            elif (outside := outside_v1(day)) is not None:
                tally.note("ttm_legacy_report_outside_v1", line + f" ({outside})")
            elif (gap := legacy_gap(day)) is not None:
                tally.note("ttm_legacy_quarter_missing", line + f" ({gap})")
            else:
                tally.note("ttm_unexplained", line)
        tally.compared["pe_percentile"] += 1
        legacy_percentile = theirs["pe_percentile_official"]
        if mine["pe_percentile"] != legacy_percentile:
            line = (f"{text} percentile ours={mine['pe_percentile']} "
                    f"legacy={legacy_percentile}")
            reproduces = (legacy_pe is None and legacy_percentile is None) or (
                legacy_pe is not None
                and _rank(legacy_history, legacy_pe) == legacy_percentile)
            if not reproduces or our_history[mine["source"]] == legacy_history:
                tally.note("percentile_unexplained", line)
            elif moved:
                tally.note("percentile_market_move", line)
            else:
                tally.note("percentile_pe_history_differs", line)
        if mine["roe"] is not None and theirs["roe_official"] is not None:
            if mine["roe"] == theirs["roe_official"]:
                tally.roe_equal += 1
            else:
                tally.roe_differences.append(abs(mine["roe"] - theirs["roe_official"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", required=True)
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-09-11")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    tally = Tally()
    ours_engine = sa.create_engine(args.database_url)
    legacy_engine = sa.create_engine(args.legacy_database_url)
    with ours_engine.connect() as ours_c, legacy_engine.connect() as legacy_c:
        universe = sorted(ours_c.scalars(sa.text("SELECT stock_id FROM stocks")))
        legacy_quarters: dict[str, dict[tuple[int, int], str]] = {}
        for symbol, quarter, market in legacy_c.execute(sa.text(
                "SELECT symbol, date, market FROM quarterly_reports_xbrl "
                "WHERE period_type = 'quarter'")):
            legacy_quarters.setdefault(symbol, {})[(int(quarter[:4]), int(quarter[5]))] = market

        for stock_id in universe:
            _stock(stock_id, ours_c, legacy_c, legacy_quarters.get(stock_id, {}), start, end,
                   tally)

    unexplained = {k: v for k, v in tally.classes.items() if k.endswith("unexplained")}
    differences = tally.roe_differences
    print(json.dumps({
        "window": [start.isoformat(), end.isoformat()], "keys": dict(tally.keys),
        "compared": dict(tally.compared), "classification": dict(sorted(tally.classes.items())),
        "roe_redefined": {
            "compared": tally.roe_equal + len(differences), "equal": tally.roe_equal,
            "median_abs_difference": statistics.median(differences) if differences else 0,
            "within_1_point": sum(1 for d in differences if d <= 1) + tally.roe_equal,
        },
        "samples": tally.samples,
    }, indent=2, default=str))
    return 1 if unexplained else 0


if __name__ == "__main__":
    sys.exit(main())
