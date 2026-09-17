#!/usr/bin/env python
"""Reconcile imported official valuation against legacy `stock_db`.

Step 18-c, the same shape as `reconcile_market_indices.py`: import counts from
the per-date manifests, the Step 16 coverage report, and the row-level
comparison with every difference classified (CLAUDE.md §78).

Legacy `pe_ratio` holds one value per `(market, symbol, date)`: the PE ratio,
NULL where the exchange printed its not-computed marker. It never stored the
yield, PB, dividend year, report period or TPEx per-share dividend, so those
are counted as new data rather than compared.

Usage:

    python scripts/reconcile_official_valuation.py \
        --database-url postgresql+psycopg://... \
        --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date
from decimal import Decimal

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.coverage import CoverageValidator

MARKETS = {"twse_bwibbu_d": ("TWSE", "sii"), "tpex_pe_qry_date": ("TPEx", "otc")}

# Both feeds publish two decimals; legacy stores floats.
TOLERANCE = Decimal("0.005")

# A quarantined row's detail names its date and security (the adapter writes
# "<source> <date> row <n> (<code>): <reason>").
_ROW_DETAIL = re.compile(r"^\S+ (\d{4}-\d{2}-\d{2}) row \d+ \(([^)]+)\)")


def import_counts(connection, source: str, start: date, end: date) -> dict:
    rows = connection.execute(
        sa.text(
            """
            SELECT status, result_counts, reconciliation
              FROM import_manifests
             WHERE dataset_code = 'official_valuation' AND source = :source
               AND (source_scope ->> 'trade_date')::date BETWEEN :start AND :end
            """
        ),
        {"source": source, "start": start, "end": end},
    ).mappings()
    totals: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    versions = set()
    variants: Counter[str] = Counter()
    for row in rows:
        statuses[row["status"]] += 1
        for key, value in (row["result_counts"] or {}).items():
            if isinstance(value, int):
                totals[key] += value
        reconciliation = row["reconciliation"] or {}
        if reconciliation.get("adapter_version"):
            versions.add(reconciliation["adapter_version"])
        if reconciliation.get("header_variant"):
            variants[reconciliation["header_variant"]] += 1
    return {
        "manifest_statuses": dict(statuses),
        "adapter_versions": sorted(versions),
        "header_variants": dict(variants),
        "raw_artifact_count": totals["raw_artifact_count"],
        "business_version_count": totals["business_version_count"],
        "dedup_count": totals["dedup_count"],
        "evidence_count": totals["evidence_count"],
        "unknown_publication_count": totals["unknown_publication_count"],
        "rejected_quarantined_count": totals["rejected_quarantined_count"],
    }


def quarantined_rows(connection, source: str) -> dict[tuple[str, date], str]:
    rows = connection.execute(
        sa.text(
            """
            SELECT q.reason_code, q.reason_detail
              FROM import_quarantine q
              JOIN import_manifests m ON m.import_id = q.import_id
             WHERE m.dataset_code = 'official_valuation' AND m.source = :source
            """
        ),
        {"source": source},
    ).mappings()
    found = {}
    for row in rows:
        match = _ROW_DETAIL.match(row["reason_detail"])
        if match:
            found[(match[2], date.fromisoformat(match[1]))] = row["reason_code"]
    return found


def stored_rows(connection, source: str, start: date, end: date) -> dict:
    """Latest revision per (security, date), chosen deterministically."""
    rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT ON (v.security_id, v.trade_date)
                   s.security_code, v.trade_date, v.pe_ratio, v.pb_ratio,
                   v.dividend_yield, v.dividend_year, v.dividend_per_share,
                   v.report_period
              FROM official_valuation_versions v
              JOIN security s ON s.id = v.security_id
             WHERE v.source = :source AND v.trade_date BETWEEN :start AND :end
             ORDER BY v.security_id, v.trade_date, v.ingested_at DESC, v.id DESC
            """
        ),
        {"source": source, "start": start, "end": end},
    ).mappings()
    return {(row["security_code"], row["trade_date"]): row for row in rows}


def compare(connection, legacy, source: str, start: date, end: date) -> dict:
    market, legacy_market = MARKETS[source]
    stored = stored_rows(connection, source, start, end)
    quarantined = quarantined_rows(connection, source)
    legacy_rows = legacy.execute(
        sa.text(
            """
            SELECT date, symbol, pe_ratio
              FROM pe_ratio
             WHERE market = :market AND date BETWEEN :start AND :end
            """
        ),
        {"market": legacy_market, "start": start.isoformat(), "end": end.isoformat()},
    ).mappings()

    differences: Counter[str] = Counter()
    by_date: dict[str, Counter[str]] = {}
    examples: dict[str, list[dict]] = {}
    compared = 0
    matched = set()

    def note(kind: str, day: str, sample: dict) -> None:
        differences[kind] += 1
        by_date.setdefault(kind, Counter())[day] += 1
        bucket = examples.setdefault(kind, [])
        if len(bucket) < 10:
            bucket.append(sample)

    for row in legacy_rows:
        day = date.fromisoformat(row["date"])
        key = (row["symbol"], day)
        mine = stored.get(key)
        if mine is None:
            if key in quarantined:
                note(f"legacy_only:we_quarantined_{quarantined[key]}", row["date"],
                     {"date": row["date"], "symbol": row["symbol"],
                      "legacy": row["pe_ratio"]})
            else:
                note("legacy_only", row["date"],
                     {"date": row["date"], "symbol": row["symbol"]})
            continue
        matched.add(key)
        compared += 1
        ours, theirs = mine["pe_ratio"], row["pe_ratio"]
        if ours is None and theirs is None:
            continue
        sample = {"date": row["date"], "symbol": row["symbol"],
                  "ours": None if ours is None else str(ours), "legacy": theirs}
        if ours is None:
            note("pe_ratio:ours_not_computed_legacy_value", row["date"], sample)
        elif theirs is None:
            note("pe_ratio:ours_value_legacy_null", row["date"], sample)
        elif abs(ours - Decimal(str(theirs))) > TOLERANCE:
            note("pe_ratio:value_differs", row["date"], sample)

    source_only = [key for key in stored if key not in matched]
    source_only_dates = Counter(key[1].isoformat() for key in source_only)
    values = list(stored.values())
    return {
        "market": market,
        "stored_rows": len(stored),
        "legacy_rows": compared + sum(
            count for kind, count in differences.items()
            if kind.startswith("legacy_only")
        ),
        "compared_rows": compared,
        "differences": dict(differences),
        "difference_dates": {
            kind: dict(sorted(counter.items())) if len(counter) <= 20
            else {"date_count": len(counter), "top": dict(counter.most_common(10))}
            for kind, counter in by_date.items()
        },
        "difference_examples": examples,
        "source_only_rows": len(source_only),
        "source_only_top_dates": dict(source_only_dates.most_common(10)),
        "quarantined_rows": dict(Counter(quarantined.values())),
        "new_data_non_null": {
            name: sum(1 for row in values if row[name] is not None)
            for name in ("pb_ratio", "dividend_yield", "dividend_year",
                         "dividend_per_share", "report_period")
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile-official-valuation")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", default=os.getenv("LEGACY_DATABASE_URL"))
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
                    connection, dataset_code="official_valuation", market=market,
                    start=args.start, end=args.end,
                )
                report["sources"][source] = {
                    "import": import_counts(connection, source, args.start, args.end),
                    "coverage": {
                        "expected_dates": len(coverage.expected),
                        "observed_dates": len(coverage.observed),
                        "missing_dates": [d.isoformat() for d in coverage.missing],
                        "unexpected_dates": [d.isoformat() for d in coverage.unexpected],
                        "is_complete": coverage.is_complete,
                    },
                    "legacy_reconciliation": compare(
                        connection, legacy, source, args.start, args.end
                    ),
                }
    finally:
        engine.dispose()
        legacy_engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    clean = all(
        entry["coverage"]["is_complete"]
        and "legacy_only" not in entry["legacy_reconciliation"]["differences"]
        for entry in report["sources"].values()
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
