"""Step 39-b report: what the by-category quotes say, against the announcements.

    DATABASE_URL=... python scripts/report_industry_observations.py [--legacy URL]

Prints one JSON document:

- `coverage`: per source and date, the stocks observed and the fetches behind them;
  for TPEx, whether every OTC stock that day's whole-market file lists was placed.
- `anchors`: every listing span that ended, its last quoted date, the category
  observed there, the category its market's announcements imply on that date
  (the old category of the next change, or the new one of the last), and
  legacy `stock_info.industry` (today's snapshot) where present.
- `otc_agreement`: on each TPEx reconciliation date, each observed OTC stock's
  category against the one the announcements imply: the old category of its
  first change after the date, or else its anchor (today's ISIN category for an
  open span, the anchor observation for one that ended). This is the raw
  agreement; Step 39-c owns the periods and the final report.

The expected categories use the stored rows and code constants only, so the
report is reproducible from the database.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict

import sqlalchemy as sa

from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import industry
from stock_data_center.v2 import industry_observations as obs

LEGACY = "postgresql+psycopg://user:password@127.0.0.1:5419/stock_db"
ANNOUNCEMENTS = {"otc": "tpex_announcement", "sii": "twse_announcement"}
QUOTES = {market: source for source, market in obs.SOURCES.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", default=LEGACY)
    args = parser.parse_args()
    engine = sa.create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as c:
        observed = defaultdict(dict)  # (source, day) -> stock -> code
        for source, stock, day, code in c.execute(sa.text(
                "SELECT DISTINCT ON (source, stock_id, trade_date) source, stock_id, trade_date, "
                "industry_code FROM industry_observations "
                "ORDER BY source, stock_id, trade_date, recorded_at DESC")):
            observed[source, day][stock] = code
        changes = defaultdict(list)  # (stock, market) -> [(effective, old code, new code)]
        for stock, source, effective, old, new in c.execute(sa.text(
                "SELECT DISTINCT ON (stock_id, source, effective_date) stock_id, source, "
                "effective_date, old_industry, new_industry FROM industry_changes "
                "ORDER BY stock_id, source, effective_date, recorded_at DESC")):
            market = "otc" if source == "tpex_announcement" else "sii"
            changes[stock, market].append(
                (effective, industry.code_of(old), industry.code_of(new)))
        today = {stock: industry.code_of(name) if name else None for stock, name in
                 c.execute(sa.text("SELECT stock_id, industry FROM stocks"))}
        spans = c.execute(sa.text(
            "SELECT stock_id, market, listed_on, delisted_on FROM listings")).all()
        found = obs.anchors(c, store=LocalRawArtifactStore())
        fetch_counts = Counter((source, status) for source, status in c.execute(sa.text(
            "SELECT source, status FROM fetches WHERE dataset = :d"), {"d": obs.DATASET}))
        whole_market = {}
        for key, sha, size in c.execute(sa.text(
                "SELECT DISTINCT ON (resource_key) resource_key, sha256, byte_size FROM fetches "
                "WHERE dataset = 'daily_price' AND source = 'tpex_otc_quotes' "
                "AND status = 'succeeded' ORDER BY resource_key, fetched_at DESC")):
            whole_market[key.rsplit(":", 1)[1]] = (sha, size)
        reconcile = obs.reconciliation_dates(c)
    engine.dispose()

    legacy = {}
    try:
        legacy_engine = sa.create_engine(args.legacy)
        with legacy_engine.connect() as c:
            legacy = {symbol: industry.code_of(name) if name else None for symbol, name in
                      c.execute(sa.text("SELECT symbol, industry FROM stock_info"))}
        legacy_engine.dispose()
    except sa.exc.OperationalError as error:
        legacy = {"unreachable": str(error)[:200]}

    def chain_at(stock: str, market: str, day) -> str | None:
        """The category the market's announcements imply on `day`, if they name one."""
        later = [old for effective, old, _ in changes[stock, market] if effective > day]
        earlier = [new for effective, _, new in changes[stock, market] if effective <= day]
        return later[0] if later else earlier[-1] if earlier else None

    anchors = []
    anchor_code = {}
    for a in found:
        seen = observed[QUOTES[a.market], a.last_quoted].get(a.stock_id) if a.last_quoted else None
        chain = chain_at(a.stock_id, a.market, a.last_quoted) if a.last_quoted else None
        anchor_code[a.stock_id, a.market] = seen
        anchors.append({
            "stock_id": a.stock_id, "market": a.market, "delisted_on": a.delisted_on,
            "last_quoted": a.last_quoted, "observed": seen, "announcements_imply": chain,
            "legacy": legacy.get(a.stock_id) if isinstance(legacy, dict) else None,
            "still_listed": today.get(a.stock_id) is not None and any(
                s.stock_id == a.stock_id and s.delisted_on is None for s in spans),
        })

    from stock_data_center.ingestion.adapters.whole_market_daily import (
        TPExWholeMarketDailyAdapter,
    )
    from stock_data_center.ingestion.models import WholeMarketDailyRequest

    store = LocalRawArtifactStore()
    coverage = []
    for (source, day), stocks in sorted(observed.items()):
        entry = {"source": source, "date": day, "observed": len(stocks)}
        if source == "tpex_otc_quotes" and str(day) in whole_market:
            sha, size = whole_market[str(day)]
            listed = {row.security_code for row in TPExWholeMarketDailyAdapter().parse(
                store.get(sha.hex(), size), WholeMarketDailyRequest(day)).rows}
            otc = {s.stock_id for s in spans if s.market == "otc"
                   and (s.listed_on is None or s.listed_on <= day)
                   and (s.delisted_on is None or s.delisted_on > day)}
            entry["otc_stocks_quoted"] = len(otc & listed)
            entry["quoted_but_not_placed"] = sorted((otc & listed) - set(stocks))
        coverage.append(entry)

    agreement = {}
    for day in reconcile:
        stocks = observed["tpex_otc_quotes", day]
        results, differences = Counter(), []
        for stock, code in sorted(stocks.items()):
            later = [old for effective, old, _ in changes[stock, "otc"] if effective > day]
            span = next((s for s in spans if s.stock_id == stock and s.market == "otc"
                         and (s.listed_on is None or s.listed_on <= day)
                         and (s.delisted_on is None or s.delisted_on > day)), None)
            if later:
                expected, basis = later[0], "next_change"
            elif span is not None and span.delisted_on is None:
                expected, basis = today.get(stock), "today_isin"
            else:
                expected, basis = anchor_code.get((stock, "otc")), "anchor"
            if code == expected:
                results[f"agree_{basis}"] += 1
            else:
                results["differ"] += 1
                differences.append({"stock_id": stock, "observed": code, "expected": expected,
                                    "basis": basis})
        agreement[str(day)] = {**results, "differences": differences}

    print(json.dumps({"fetches": {f"{s}/{st}": n for (s, st), n in sorted(fetch_counts.items())},
                      "coverage": coverage, "anchors": anchors, "otc_agreement": agreement},
                     ensure_ascii=False, default=str, indent=1))


if __name__ == "__main__":
    main()
