#!/usr/bin/env python
"""Reconcile imported per-security institutional flows against legacy `stock_db`.

Step 20-a, the same shape as `reconcile_official_valuation.py`: import counts
from the per-date manifests, the Step 16 coverage report, and the row-level
comparison with every difference classified (CLAUDE.md §78).

Legacy `institutional_investors` holds the same 17 share quantities as
`institutional_investor_versions`, in shares, as floats. Every one is compared
exactly: both sides are whole shares.

Two further checks need no legacy at all:

- **Published identities.** Every stored row is tested for buy − sell = net
  per group, self + hedge = dealer net, and foreign + trust + dealer = total.
  Nothing is recomputed; a row that fails is reported, not corrected.
- **TPEx totals the contract does not store.** TPEx also publishes the foreign
  total (外資及陸資) and the dealer total's buy and sell. The raw artifacts are
  re-read to prove each is the sum of stored groups, so dropping them loses
  nothing.

Usage:

    python scripts/reconcile_institutional_investors.py \
        --database-url postgresql+psycopg://... \
        --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.coverage import CoverageValidator
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore

DATASET = "institutional_investor"
MARKETS = {"twse_t86": ("TWSE", "sii"), "tpex_insti_daily_trade": ("TPEx", "otc")}
FIELDS = (
    "foreign_buy", "foreign_sell", "foreign_net",
    "foreign_dealer_buy", "foreign_dealer_sell", "foreign_dealer_net",
    "trust_buy", "trust_sell", "trust_net",
    "dealer_self_buy", "dealer_self_sell", "dealer_self_net",
    "dealer_hedge_buy", "dealer_hedge_sell", "dealer_hedge_net",
    "dealer_net", "total_net",
)
GROUPS = ("foreign", "foreign_dealer", "trust", "dealer_self", "dealer_hedge")


def import_counts(connection, source: str, start: date, end: date) -> dict:
    rows = connection.execute(
        sa.text(
            """
            SELECT status, result_counts, reconciliation
              FROM import_manifests
             WHERE dataset_code = :dataset AND source = :source
               AND (source_scope ->> 'trade_date')::date BETWEEN :start AND :end
            """
        ),
        {"dataset": DATASET, "source": source, "start": start, "end": end},
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


def stored_by_date(connection, source: str, start: date, end: date) -> dict:
    """Latest revision per (security, date), chosen deterministically."""
    columns = ", ".join(f"v.{name}" for name in FIELDS)
    rows = connection.execute(
        sa.text(
            f"""
            SELECT DISTINCT ON (v.security_id, v.trade_date)
                   s.security_code, v.trade_date, {columns}
              FROM institutional_investor_versions v
              JOIN security s ON s.id = v.security_id
             WHERE v.source = :source AND v.trade_date BETWEEN :start AND :end
             ORDER BY v.security_id, v.trade_date, v.ingested_at DESC, v.id DESC
            """
        ),
        {"source": source, "start": start, "end": end},
    ).mappings()
    grouped: dict[date, dict[str, dict]] = {}
    for row in rows:
        grouped.setdefault(row["trade_date"], {})[row["security_code"]] = {
            name: None if row[name] is None else int(row[name]) for name in FIELDS
        }
    return grouped


def identity_failures(row: dict) -> list[str]:
    failed = [
        group for group in GROUPS
        if row[f"{group}_buy"] - row[f"{group}_sell"] != row[f"{group}_net"]
    ]
    if row["dealer_self_net"] + row["dealer_hedge_net"] != row["dealer_net"]:
        failed.append("dealer_net")
    if row["foreign_net"] + row["trust_net"] + row["dealer_net"] != row["total_net"]:
        failed.append("total_net")
    return failed


def tpex_unstored_totals(connection, raw_root: Path, start: date, end: date) -> dict:
    """Re-read every TPEx artifact the window's versions came from."""
    artifacts = connection.execute(
        sa.text(
            """
            SELECT DISTINCT a.storage_uri, a.raw_artifact_hash, a.byte_size
              FROM institutional_investor_version_observations o
              JOIN institutional_investor_versions v
                ON v.id = o.institutional_investor_version_id
              JOIN raw_artifacts a ON a.id = o.raw_artifact_id
             WHERE v.source = 'tpex_insti_daily_trade'
               AND v.trade_date BETWEEN :start AND :end
            """
        ),
        {"start": start, "end": end},
    ).all()
    store = LocalRawArtifactStore(raw_root)
    checked = 0
    failures: list[dict] = []
    for uri, digest, size in artifacts:
        payload = json.loads(store.read(
            storage_uri=uri, expected_digest=digest, expected_byte_size=size
        ))
        for row in payload["tables"][0]["data"]:
            value = [int(cell.replace(",", "")) for cell in row[2:]]
            triple = [value[i:i + 3] for i in range(0, 21, 3)]
            checked += 1
            if [a + b for a, b in zip(triple[0], triple[1])] != triple[2]:
                failures.append({"date": payload["date"], "code": row[0], "total": "外資及陸資"})
            if [a + b for a, b in zip(triple[4], triple[5])] != triple[6]:
                failures.append({"date": payload["date"], "code": row[0], "total": "自營商"})
    return {
        "artifacts": len(artifacts),
        "rows_checked": checked,
        "failures": len(failures),
        "failure_examples": failures[:10],
    }


def compare(connection, legacy, source: str, start: date, end: date) -> dict:
    """Classify every legacy row, then every stored row legacy lacks.

    A legacy date whose values mostly disagree with ours is classified as a
    whole, as Step 18-c had to (legacy saved another date's file there), so
    an ordinary disagreement cannot hide inside it.
    """
    market, legacy_market = MARKETS[source]
    stored = stored_by_date(connection, source, start, end)
    columns = ", ".join(FIELDS)
    legacy_by_date: dict[date, dict[str, dict]] = {}
    for row in legacy.execute(
        sa.text(
            f"""
            SELECT date, symbol, {columns}
              FROM institutional_investors
             WHERE market = :market AND date BETWEEN :start AND :end
            """
        ),
        {"market": legacy_market, "start": start.isoformat(), "end": end.isoformat()},
    ).mappings():
        legacy_by_date.setdefault(date.fromisoformat(row["date"]), {})[row["symbol"]] = {
            name: None if row[name] is None else int(row[name]) for name in FIELDS
        }

    differences: Counter[str] = Counter()
    by_date: dict[str, Counter[str]] = {}
    examples: dict[str, list[dict]] = {}
    wrong_dates: dict[str, dict] = {}
    source_only: Counter[str] = Counter()
    source_only_dates: Counter[str] = Counter()
    identity: Counter[str] = Counter()
    identity_examples: list[dict] = []
    compared = 0
    legacy_total = 0

    def note(kind: str, day: date, sample: dict) -> None:
        differences[kind] += 1
        by_date.setdefault(kind, Counter())[day.isoformat()] += 1
        bucket = examples.setdefault(kind, [])
        if len(bucket) < 10:
            bucket.append(sample)

    for day in sorted(set(stored) | set(legacy_by_date)):
        ours_day = stored.get(day, {})
        theirs_day = legacy_by_date.get(day, {})
        legacy_total += len(theirs_day)
        for code, row in ours_day.items():
            for failed in identity_failures(row):
                identity[failed] += 1
                if len(identity_examples) < 10:
                    identity_examples.append(
                        {"date": day.isoformat(), "code": code, "identity": failed}
                    )
        present = [code for code in theirs_day if code in ours_day]
        agreeing = sum(ours_day[code] == theirs_day[code] for code in present)
        if present and agreeing / len(present) < 0.5:
            wrong_dates[day.isoformat()] = {
                "legacy_rows": len(theirs_day),
                "agreement": round(agreeing / len(present), 4),
            }
            differences["legacy_captured_another_date:legacy_rows"] += len(theirs_day)
            continue
        for code, theirs in theirs_day.items():
            sample = {"date": day.isoformat(), "symbol": code}
            if code not in ours_day:
                note("legacy_only", day, sample)
                continue
            compared += 1
            ours = ours_day[code]
            differing = [name for name in FIELDS if ours[name] != theirs[name]]
            if differing:
                sample.update(
                    {name: {"ours": ours[name], "legacy": theirs[name]} for name in differing}
                )
                note("value_differs:" + ",".join(differing), day, sample)
        for code in ours_day:
            if code not in theirs_day:
                # Legacy kept only 4-digit common-stock codes (audit §4.3).
                kind = "etf_or_other_instrument" if not (
                    len(code) == 4 and code.isdigit()
                ) else "common_stock"
                source_only[kind] += 1
                if kind == "common_stock":
                    source_only_dates[day.isoformat()] += 1

    return {
        "market": market,
        "stored_rows": sum(len(rows) for rows in stored.values()),
        "legacy_rows": legacy_total,
        "compared_rows": compared,
        "differences": dict(differences),
        "difference_dates": {
            kind: dict(sorted(counter.items())) if len(counter) <= 20
            else {"date_count": len(counter), "top": dict(counter.most_common(10))}
            for kind, counter in by_date.items()
        },
        "difference_examples": examples,
        "legacy_captured_another_date": wrong_dates,
        "source_only_rows": dict(source_only),
        "source_only_common_stock_dates": (
            dict(sorted(source_only_dates.items())) if len(source_only_dates) <= 40
            else {"date_count": len(source_only_dates),
                  "top": dict(source_only_dates.most_common(20))}
        ),
        "published_identity_failures": dict(identity),
        "published_identity_examples": identity_examples,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile-institutional-investors")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", default=os.getenv("LEGACY_DATABASE_URL"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 2))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 11))
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
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
                    connection, dataset_code=DATASET, market=market,
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
            report["sources"]["tpex_insti_daily_trade"]["unstored_totals"] = (
                tpex_unstored_totals(connection, args.raw_root, args.start, args.end)
            )
    finally:
        engine.dispose()
        legacy_engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    clean = all(
        entry["coverage"]["is_complete"]
        and "legacy_only" not in entry["legacy_reconciliation"]["differences"]
        and not entry["legacy_reconciliation"]["published_identity_failures"]
        for entry in report["sources"].values()
    ) and report["sources"]["tpex_insti_daily_trade"]["unstored_totals"]["failures"] == 0
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
