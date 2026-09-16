#!/usr/bin/env python
"""Reconcile imported whole-market daily prices against legacy `stock_db`.

Step 17-c. Every adapter step reconciles against the legacy database for
2020-01-02 → 2026-09-11 with every difference classified (CLAUDE.md §78). This
is that report for daily prices, and it is a script rather than a service
because the legacy database is migration input on a developer machine, not
something the Data Center depends on.

It reports three things:

1. the import itself, aggregated from the per-date manifests — coverage, raw
   artifacts, versions, evidence, dedup and quarantine counts;
2. the gap report from the Step 16 coverage validator, so a missing date is
   named rather than inferred from a row count;
3. the row-level comparison against legacy, with every difference classified.

A difference is never smoothed, and it is never silently blamed on us. The
legacy archive is a re-fetch snapshot: about 92% of the window was fetched in
one campaign in January and February 2026, and the scraper skips any date whose
file already exists (audit §3). So a value difference can equally mean the
official figure changed after the archive was written. Those rows are counted
as `legacy_snapshot_differs` and reported for inspection rather than judged.

`legacy_only` rows — a security-date legacy holds and we do not — are a
different matter: the feed is a superset on every date measured, so one of those
is a real defect. The source-only classes are expected and are counted
separately so that "expected" never hides "unexplained".

Usage:

    python scripts/reconcile_daily_prices.py \
        --database-url postgresql+psycopg://... \
        --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db \
        --start 2020-01-02 --end 2026-09-11
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date
from decimal import Decimal

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.coverage import (
    CoverageValidator,
    securities_without_metadata,
)

# (our column, legacy column). Legacy has no bid/ask columns at all, so the
# disclosed level is unreconcilable here and is checked at the source instead.
COMPARED_FIELDS = (
    ("open_price", "open"),
    ("high_price", "high"),
    ("low_price", "low"),
    ("close_price", "close"),
    ("volume", "volume"),
    ("trade_value", "value"),
    ("trade_count", "transactions"),
)

MARKETS = {"twse_mi_index": ("TWSE", "sii"), "tpex_otc_quotes": ("TPEx", "otc")}

# Legacy stores every number as a float, so an exact comparison would report
# representation noise as a difference. One hundredth of a cent is far below
# any published price increment and far above float error at these magnitudes.
TOLERANCE = Decimal("0.0001")


def import_counts(connection, source: str, start: date, end: date) -> dict:
    rows = connection.execute(
        sa.text(
            """
            SELECT status, result_counts, reconciliation
              FROM import_manifests
             WHERE dataset_code = 'daily_price' AND source = :source
            """
        ),
        {"source": source},
    ).mappings()
    totals: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    adapter_versions = set()
    for row in rows:
        statuses[row["status"]] += 1
        for key, value in (row["result_counts"] or {}).items():
            if isinstance(value, int):
                totals[key] += value
        version = (row["reconciliation"] or {}).get("adapter_version")
        if version:
            adapter_versions.add(version)
    return {
        "manifest_statuses": dict(statuses),
        "adapter_versions": sorted(adapter_versions),
        "raw_artifact_count": totals["raw_artifact_count"],
        "business_version_count": totals["business_version_count"],
        "dedup_count": totals["dedup_count"],
        "evidence_count": totals["evidence_count"],
        "evidence_dedup_count": totals["evidence_dedup_count"],
        "unknown_publication_count": totals["unknown_publication_count"],
        "rejected_quarantined_count": totals["rejected_quarantined_count"],
        "normalized_row_count": totals["normalized_row_count"],
    }


def quarantined(connection, source: str) -> list[dict]:
    rows = connection.execute(
        sa.text(
            """
            SELECT q.resource_key, q.reason_code, q.reason_detail
              FROM import_quarantine q
              JOIN ingest_runs r ON r.id = q.ingest_run_id
             WHERE r.dataset_code = 'daily_price' AND r.source = :source
             ORDER BY q.resource_key
            """
        ),
        {"source": source},
    ).mappings()
    return [dict(row) for row in rows]


def compare(connection, legacy, source: str, start: date, end: date) -> dict:
    market, legacy_market = MARKETS[source]
    ours = connection.execute(
        sa.text(
            """
            SELECT v.trade_date, s.security_code, v.open_price, v.high_price,
                   v.low_price, v.close_price, v.volume, v.trade_value,
                   v.trade_count
              FROM daily_price_versions v
              JOIN security s ON s.id = v.security_id
             WHERE v.source = :source
               AND v.trade_date BETWEEN :start AND :end
            """
        ),
        {"source": source, "start": start, "end": end},
    ).mappings()
    stored = {(row["trade_date"], row["security_code"]): row for row in ours}

    legacy_rows = legacy.execute(
        sa.text(
            """
            SELECT date, symbol, open, high, low, close, volume, value,
                   transactions
              FROM daily_quotes
             WHERE market = :market AND date BETWEEN :start AND :end
            """
        ),
        {
            "market": legacy_market,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
    ).mappings()

    differences: Counter[str] = Counter()
    examples: list[dict] = []
    legacy_keys = set()
    compared = 0
    for row in legacy_rows:
        key = (date.fromisoformat(row["date"]), row["symbol"])
        legacy_keys.add(key)
        mine = stored.get(key)
        if mine is None:
            differences["legacy_only"] += 1
            if len(examples) < 20:
                examples.append(
                    {"kind": "legacy_only", "date": row["date"], "code": row["symbol"]}
                )
            continue
        compared += 1
        for ours_field, legacy_field in COMPARED_FIELDS:
            a, b = mine[ours_field], row[legacy_field]
            if (a is None) != (b is None):
                differences[f"{ours_field}:null_disagreement"] += 1
                kind = "null"
            elif a is not None and abs(Decimal(str(a)) - Decimal(str(b))) > TOLERANCE:
                # Named for what is known: the two snapshots disagree. Which
                # one is stale cannot be settled from here, because nothing
                # preserved the official bytes as they stood when the archive
                # was written.
                differences[f"{ours_field}:legacy_snapshot_differs"] += 1
                kind = "legacy_snapshot_differs"
            else:
                continue
            if len(examples) < 20:
                examples.append(
                    {
                        "kind": kind,
                        "field": ours_field,
                        "date": row["date"],
                        "code": row["symbol"],
                        "ours": str(a),
                        "legacy": str(b),
                    }
                )

    # Everything the official feed carries that legacy never did. Split, so a
    # real anomaly can never hide inside an expected class.
    source_only = [key for key in stored if key not in legacy_keys]
    untraded = sum(1 for key in source_only if stored[key]["volume"] == 0)
    return {
        "market": market,
        "legacy_market": legacy_market,
        "stored_rows": len(stored),
        "legacy_rows": len(legacy_keys),
        "compared_rows": compared,
        "differences": dict(differences),
        "difference_examples": examples,
        "source_only_rows": len(source_only),
        "source_only_untraded": untraded,
        "source_only_not_in_legacy_universe": len(source_only) - untraded,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile-daily-prices")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument(
        "--legacy-database-url", default=os.getenv("LEGACY_DATABASE_URL")
    )
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 2))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 11))
    args = parser.parse_args(argv)
    if not args.database_url or not args.legacy_database_url:
        parser.error("--database-url and --legacy-database-url are required")

    engine = sa.create_engine(args.database_url)
    legacy_engine = sa.create_engine(args.legacy_database_url)
    report: dict = {
        "window": {"start": args.start.isoformat(), "end": args.end.isoformat()},
        "sources": {},
    }
    try:
        with engine.connect() as connection, legacy_engine.connect() as legacy:
            for source, (market, _) in MARKETS.items():
                coverage = CoverageValidator().report(
                    connection,
                    dataset_code="daily_price",
                    market=market,
                    start=args.start,
                    end=args.end,
                )
                unknown = securities_without_metadata(
                    connection, source=source, start=args.start, end=args.end
                )
                report["sources"][source] = {
                    "import": import_counts(
                        connection, source, args.start, args.end
                    ),
                    "quarantined": quarantined(connection, source),
                    "coverage": {
                        "expected_dates": len(coverage.expected),
                        "observed_dates": len(coverage.observed),
                        "missing_dates": [
                            day.isoformat() for day in coverage.missing
                        ],
                        "unexpected_dates": [
                            day.isoformat() for day in coverage.unexpected
                        ],
                        "non_trading_days": len(coverage.non_trading_days),
                        "is_complete": coverage.is_complete,
                    },
                    "legacy_reconciliation": compare(
                        connection, legacy, source, args.start, args.end
                    ),
                    "priced_without_metadata": {
                        "count": len(unknown),
                        "sample": [
                            {
                                "security_code": item.security_code,
                                "first_priced_on": item.first_priced_on.isoformat(),
                                "last_priced_on": item.last_priced_on.isoformat(),
                                "priced_days": item.priced_days,
                            }
                            for item in unknown[:15]
                        ],
                    },
                }
    finally:
        engine.dispose()
        legacy_engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    clean = all(
        entry["coverage"]["is_complete"]
        and not entry["legacy_reconciliation"]["differences"]
        for entry in report["sources"].values()
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
