#!/usr/bin/env python
"""Step 35-c-3 acceptance: the v2 corporate actions equal the v1 backfill.

Compares every current (latest, not retracted) row of `corporate_actions` in
`stockdc_backfill` with the v1 versions in `stockdc_step19d` (Step 19-d's live
backfill of 2020-01-02 .. 2026-09-11, fetched 2026-09-16/17), for the stocks on
today's list, in both directions, value by value. Events after that range, which
the v1 run never requested, are reported apart; so are v2 retractions.

Both databases are only read.

    .venv/bin/python scripts/verify_v2_corporate_actions.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from decimal import Decimal

import sqlalchemy as sa

SERVER = "postgresql+psycopg://stockdc:stockdc@localhost:5432"
V1_SEEN_THROUGH = date(2026, 9, 11)  # Step 19-d requested --end 2026-09-11
COLUMNS = ("event_type", "close_before", "reference_price", "rights_dividend_value",
           "cash_dividend_per_share", "free_share_ratio", "rights_ratio",
           "subscription_price", "old_shares", "new_shares", "cash_return_per_share")

V2 = """
SELECT DISTINCT ON (stock_id, source, ex_date) stock_id, source, ex_date, retracted,
       """ + ", ".join(COLUMNS) + """
  FROM corporate_actions ORDER BY stock_id, source, ex_date, recorded_at DESC"""
V1 = """
SELECT DISTINCT ON (e.id)
       s.security_code, v.source, v.ex_date, v.source_event_type, v.close_before,
       v.official_reference_price, v.official_rights_dividend_value,
       v.cash_dividend_per_share, v.free_share_ratio, v.rights_ratio, v.subscription_price,
       v.old_shares, v.new_shares, v.capital_reduction_cash_return_per_share
  FROM corporate_action_versions v
  JOIN corporate_action_events e ON e.id = v.event_id
  JOIN security s ON s.id = e.security_id
 WHERE v.source = ANY(:sources)
   AND NOT EXISTS (SELECT 1 FROM corporate_action_retractions r WHERE r.event_id = e.id)
 ORDER BY e.id, v.ingested_at DESC, v.id DESC"""  # each event's latest version
SOURCES = ["twse_twt49u", "twse_twtauu", "twse_twtb8u", "tpex_exdailyq", "tpex_revivt",
           "tpex_pvchgrslt"]


def _norm(value):
    return value.normalize() if isinstance(value, Decimal) else value


def main() -> int:
    v2_db = sa.create_engine(f"{SERVER}/stockdc_backfill")
    v1_db = sa.create_engine(f"{SERVER}/stockdc_step19d")
    with v2_db.connect() as c:
        stocks = set(c.scalars(sa.text("SELECT stock_id FROM stocks")))
        current = {}
        retracted = []
        for row in c.execute(sa.text(V2)):
            key = (row.stock_id, row.source, row.ex_date)
            if row.retracted:
                retracted.append(key)
            else:
                current[key] = tuple(_norm(row[i]) for i in range(4, 4 + len(COLUMNS)))
    with v1_db.connect() as c:
        v1 = {
            (row[0], row[1], row[2]): tuple(_norm(value) for value in row[3:])
            for row in c.execute(sa.text(V1), {"sources": SOURCES}) if row[0] in stocks
        }
    only_v1 = sorted(k for k in v1 if k not in current)
    only_v2 = sorted(k for k in current if k not in v1)
    later = [k for k in only_v2 if k[2] > V1_SEEN_THROUGH]
    differing = [
        {"key": [str(x) for x in k],
         "columns": {COLUMNS[i]: [str(v1[k][i]), str(current[k][i])]
                     for i in range(len(COLUMNS)) if v1[k][i] != current[k][i]}}
        for k in sorted(v1.keys() & current.keys()) if v1[k] != current[k]
    ]
    by_source = {s: {"v1": sum(k[1] == s for k in v1), "v2": sum(k[1] == s for k in current)}
                 for s in SOURCES}
    report = {
        "by_source": by_source,
        "equal": len(v1.keys() & current.keys()) - len(differing),
        "differing": differing,
        "only_v1": [[str(x) for x in k] for k in only_v1],
        "only_v2_inside_v1_range": [[str(x) for x in k] for k in only_v2 if k not in later],
        "only_v2_after_v1_range": [[str(x) for x in k] for k in later],
        "retracted_in_v2": [[str(x) for x in k] for k in retracted],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed = differing or only_v1 or report["only_v2_inside_v1_range"] or retracted
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
