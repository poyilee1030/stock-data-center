#!/usr/bin/env python
"""Reconcile the stored `institutional_cumulative_flow:v1` against legacy `stock_db`.

Legacy `trust_holding` and `dealer_holding` hold one row per (date, market,
symbol) of its `institutional_investors`, the running sum of `trust_net` /
`dealer_net` and its ratio to the same day's `foreign_holding.issued_shares`.
Ours is `institutional_cumulative_flow`, one series per institutional source;
`sii` is `twse_t86`, `otc` is `tpex_insti_daily_trade`. Both sides are limited
to the stocks on today's list (ADR-0026).

A sum carries every earlier difference forward, so a differing sum is not
judged by itself. It is `explained_by_inputs` when it equals, to the share, the
running sum of the two sides' net differences up to that day (a day only one
side has counts its whole net); Step 20-a classified every such input row.
Anything else is `unexplained`.

A ratio is compared only where the sums agree. A differing one is explained only
by the two input differences Step 20-d classified in `foreign_holdings`:

`ratio_legacy_file_of_no_date`
    TWSE 2022-06-28 and 2024-12-19, where legacy's `foreign_holding` file
    matches no date in the window, so it divided by other issued shares.
`ratio_mops_drops_the_stock`
    A TPEx day where we have no issued shares and legacy has: MOPS rebuilds
    every past date from today's list, so a stock that has since left TPEx is
    missing from every date (5236 moved to TWSE on 2026-07-15).

Any other ratio difference is `ratio_unexplained`. The report also counts them
by day.

A key only one side has is listed; every one must be a day only one side's
institutional file has, which is the same input difference. Exit 1 on any
unexplained sum, ratio or key.

    python scripts/reconcile_institutional_cumulative_flow.py \\
        --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \\
        --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db \\
        --start 2020-01-02 --end 2026-09-11
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.v2.cumulative_flow import PARTIES
from stock_data_center.v2.derived_store import HOLDING_SOURCE

MARKET_SOURCE = {"sii": "twse_t86", "otc": "tpex_insti_daily_trade"}
# Step 20-d: legacy foreign_holding files that match no date in the window.
LEGACY_FILE_OF_NO_DATE = frozenset({date(2022, 6, 28), date(2024, 12, 19)})


def _ours(connection, code: str, end: date):
    stored: dict[tuple, dict] = {}
    for row in connection.execute(sa.text(
            "SELECT * FROM institutional_cumulative_flow WHERE stock_id = :c "
            "AND trade_date <= :e"), {"c": code, "e": end}).mappings():
        stored[(row["source"], row["trade_date"])] = dict(row)
    nets = {
        (source, day): values
        for source, day, *values in connection.execute(sa.text(
            "SELECT DISTINCT ON (source, trade_date) source, trade_date, trust_net, dealer_net "
            "FROM institutional_flows WHERE stock_id = :c AND trade_date <= :e "
            "ORDER BY source, trade_date, recorded_at DESC"), {"c": code, "e": end})
    }
    holding_flow = {holding: flow for flow, holding in HOLDING_SOURCE.items()}
    issued = {
        (holding_flow[source], day): shares
        for source, day, shares in connection.execute(sa.text(
            "SELECT DISTINCT ON (source, trade_date) source, trade_date, issued_shares "
            "FROM foreign_holdings WHERE stock_id = :c AND trade_date <= :e "
            "ORDER BY source, trade_date, recorded_at DESC"), {"c": code, "e": end})
    }
    return stored, nets, issued


def _legacy(connection, code: str, end: date):
    stored: dict[tuple, dict] = {}
    for row in connection.execute(sa.text(
            "SELECT t.date, t.market, t.trust_held_shares, t.trust_held_ratio, "
            "d.dealer_held_shares, d.dealer_held_ratio, t.issued_shares "
            "FROM trust_holding t JOIN dealer_holding d "
            "ON d.date = t.date AND d.market = t.market AND d.symbol = t.symbol "
            "WHERE t.symbol = :c AND t.date <= :e"), {"c": code, "e": end.isoformat()}):
        key = (MARKET_SOURCE[row[1]], date.fromisoformat(row[0]))
        stored[key] = {
            "trust_cumulative_net_shares": int(row[2]),
            "trust_cumulative_net_ratio": None if row[3] is None else float(row[3]),
            "dealer_cumulative_net_shares": int(row[4]),
            "dealer_cumulative_net_ratio": None if row[5] is None else float(row[5]),
            "issued_shares": None if row[6] is None else int(row[6]),
        }
    nets = {
        (MARKET_SOURCE[market], date.fromisoformat(day)): (
            None if trust is None else int(trust), None if dealer is None else int(dealer))
        for day, market, trust, dealer in connection.execute(sa.text(
            "SELECT date, market, trust_net, dealer_net FROM institutional_investors "
            "WHERE symbol = :c AND date <= :e"), {"c": code, "e": end.isoformat()})
    }
    return stored, nets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", required=True)
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-09-11")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    keys: Counter = Counter()
    compared: Counter = Counter()
    classes: Counter = Counter()
    ratio_days: Counter = Counter()
    samples: dict[str, list[str]] = {}

    def note(cls: str, text: str) -> None:
        classes[cls] += 1
        bucket = samples.setdefault(cls, [])
        if len(bucket) < 10:
            bucket.append(text)

    with sa.create_engine(args.database_url).connect() as ours_c, \
            sa.create_engine(args.legacy_database_url).connect() as legacy_c:
        for code in sorted(ours_c.scalars(sa.text("SELECT stock_id FROM stocks"))):
            ours, our_nets, our_issued = _ours(ours_c, code, end)
            legacy, legacy_nets = _legacy(legacy_c, code, end)
            window = {k for k in ours.keys() | legacy.keys() if start <= k[1] <= end}
            for key in sorted(window):
                if key not in legacy:
                    keys["ours_only"] += 1
                    cls = ("ours_only_day_only_ours_has" if key in our_nets
                           and key not in legacy_nets else "ours_only_unexplained")
                    note(cls, f"{code} {key}")
                elif key not in ours:
                    keys["legacy_only"] += 1
                    cls = ("legacy_only_day_only_legacy_has" if key in legacy_nets
                           and key not in our_nets else "legacy_only_unexplained")
                    note(cls, f"{code} {key}")
                else:
                    keys["shared"] += 1
            # Running net difference per source and party, over every day
            # either side has, in date order.
            drift: dict[tuple, int] = {}
            running: dict[tuple, int] = Counter()
            for source, day in sorted(our_nets.keys() | legacy_nets.keys(),
                                      key=lambda k: (k[1], k[0])):
                mine = our_nets.get((source, day), (None, None))
                theirs = legacy_nets.get((source, day), (None, None))
                for index, party in enumerate(PARTIES):
                    running[(source, party)] += (mine[index] or 0) - (theirs[index] or 0)
                    drift[(source, day, party)] = running[(source, party)]
            for key in sorted(window & ours.keys() & legacy.keys()):
                mine, theirs = ours[key], legacy[key]
                for party in PARTIES:
                    compared[party] += 1
                    shares = f"{party}_cumulative_net_shares"
                    ratio = f"{party}_cumulative_net_ratio"
                    difference = mine[shares] - theirs[shares]
                    if difference:
                        explained = drift.get((key[0], key[1], party)) == difference
                        note("shares_explained_by_inputs" if explained
                             else "shares_unexplained",
                             f"{code} {key} {party} ours={mine[shares]} "
                             f"legacy={theirs[shares]}")
                        continue
                    if mine[ratio] == theirs[ratio]:
                        continue
                    ours_issued = our_issued.get(key)
                    ratio_days[f"{key[0]} {key[1].isoformat()}"] += 1
                    if key[0] == "twse_t86" and key[1] in LEGACY_FILE_OF_NO_DATE:
                        cls = "ratio_legacy_file_of_no_date"
                    elif (key[0] == "tpex_insti_daily_trade" and ours_issued is None
                          and theirs["issued_shares"] is not None):
                        cls = "ratio_mops_drops_the_stock"
                    else:
                        cls = "ratio_unexplained"
                    note(cls,
                         f"{code} {key} {party} ours={mine[ratio]} legacy={theirs[ratio]} "
                         f"issued ours={ours_issued} legacy={theirs['issued_shares']}")
    unexplained = {k: v for k, v in classes.items() if k.endswith("unexplained")}
    print(json.dumps({"window": [start.isoformat(), end.isoformat()], "keys": dict(keys),
                      "compared": dict(compared), "classification": dict(classes),
                      "ratio_differences_by_day": dict(ratio_days.most_common()),
                      "samples": samples}, indent=2, default=str))
    return 1 if unexplained else 0


if __name__ == "__main__":
    sys.exit(main())
