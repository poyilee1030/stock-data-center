#!/usr/bin/env python
"""Reconcile imported corporate actions against legacy `stock_db`.

Step 19-d, the same shape as `reconcile_daily_prices.py` and
`reconcile_market_indices.py`. Legacy `dividend` holds only TWT49U-shaped rows
(息/權/權息 — verified in Step 19-b: 6,182 rows, all three legacy `type` values),
so this reconciles `twse_twt49u` against it and reports every other declared
source's stored count as new data, never as a difference — legacy never
collected capital reductions, par-value changes, or anything TPEx.

It reports:

1. the import itself, aggregated from the per-source, per-year manifests —
   coverage, raw artifacts, versions, evidence, dedup, quarantine and
   retraction counts;
2. zero-duplicate `(feed, code, locator date)` over each feed's stored
   history (ROADMAP §19-d);
3. the row-level comparison of `twse_twt49u` against legacy `dividend`, with
   every difference classified (CLAUDE.md §78);
4. quarantined ranges, if any, named with their reason.

Usage:

    python scripts/reconcile_corporate_actions.py \
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

SOURCES = (
    "twse_twt49u", "twse_twtauu", "twse_twtb8u",
    "tpex_exdailyq", "tpex_revivt", "tpex_pvchgrslt",
)
FEED_BY_SOURCE = {
    "twse_twt49u": "TWT49U", "twse_twtauu": "TWTAUU", "twse_twtb8u": "TWTB8U",
    "tpex_exdailyq": "exDailyQ", "tpex_revivt": "revivt",
    "tpex_pvchgrslt": "pvChgRslt",
}
RECONCILED_SOURCE = "twse_twt49u"
# Legacy dividend.type -> our action_type (ADR-0019 mapping table).
LEGACY_TYPE = {"息": "ex_dividend", "權": "ex_right", "權息": "ex_right_dividend"}
# Legacy stores floats; a ten-thousandth of a TWD is below any published
# increment and above float noise at these magnitudes.
TOLERANCE = Decimal("0.0001")


def import_counts(connection, source: str, start: date, end: date) -> dict:
    rows = connection.execute(
        sa.text(
            """
            SELECT status, result_counts, reconciliation
              FROM import_manifests
             WHERE dataset_code = 'corporate_action' AND source = :source
               AND (source_scope ->> 'start')::date <= :end
               AND (source_scope ->> 'end')::date >= :start
            """
        ),
        {"source": source, "start": start, "end": end},
    ).mappings()
    totals: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    retracted = 0
    versions = set()
    for row in rows:
        statuses[row["status"]] += 1
        for key, value in (row["result_counts"] or {}).items():
            if isinstance(value, int):
                totals[key] += value
        reconciliation = row["reconciliation"] or {}
        version = reconciliation.get("adapter_version")
        if version:
            versions.add(version)
        retracted += reconciliation.get("retracted_count") or 0
    return {
        "manifest_statuses": dict(statuses),
        "adapter_versions": sorted(versions),
        "raw_artifact_count": totals["raw_artifact_count"],
        "business_version_count": totals["business_version_count"],
        "dedup_count": totals["dedup_count"],
        "evidence_count": totals["evidence_count"],
        "rejected_quarantined_count": totals["rejected_quarantined_count"],
        "retracted_count": retracted,
    }


def duplicate_check(connection, source: str) -> dict:
    """Zero duplicate (feed, code, locator date) over the feed's full stored
    history (ROADMAP §19-d). The locator is `event_id`'s own key, so a
    duplicate here would mean two events share one identity."""
    feed = FEED_BY_SOURCE[source]
    rows = connection.execute(
        sa.text(
            """
            SELECT source_event_key, count(*) AS n
              FROM corporate_action_events
             WHERE source = :source
             GROUP BY source_event_key
            HAVING count(*) > 1
            """
        ),
        {"source": source},
    ).all()
    return {
        "feed": feed,
        "event_count": connection.scalar(
            sa.text(
                "SELECT count(*) FROM corporate_action_events WHERE source = :source"
            ),
            {"source": source},
        ),
        "duplicate_locators": len(rows),
        "duplicate_examples": [row.source_event_key for row in rows[:10]],
    }


def quarantine_report(connection, source: str) -> list[dict]:
    rows = connection.execute(
        sa.text(
            """
            SELECT q.reason_code, q.reason_detail, q.resource_key
              FROM import_quarantine q
              JOIN import_manifests m ON m.import_id = q.import_id
             WHERE m.dataset_code = 'corporate_action' AND m.source = :source
             ORDER BY q.id
            """
        ),
        {"source": source},
    ).mappings()
    return [dict(row) for row in rows]


def stored_twt49u(connection, start: date, end: date) -> dict:
    """One row per (security, ex_date), latest revision, chosen
    deterministically — a corrected event keeps only its current terms."""
    rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT ON (v.event_id)
                   s.security_code, v.ex_date, v.action_type, v.close_before,
                   v.official_reference_price, v.official_rights_dividend_value
              FROM corporate_action_versions v
              JOIN corporate_action_events e ON e.id = v.event_id
              JOIN security s ON s.id = e.security_id
             WHERE v.source = :source AND v.ex_date BETWEEN :start AND :end
             ORDER BY v.event_id, v.ingested_at DESC, v.id DESC
            """
        ),
        {"source": RECONCILED_SOURCE, "start": start, "end": end},
    ).mappings()
    return {(row["security_code"], row["ex_date"]): row for row in rows}


def compare(connection, legacy, start: date, end: date) -> dict:
    stored = stored_twt49u(connection, start, end)
    legacy_rows = legacy.execute(
        sa.text(
            """
            SELECT date, symbol, close_before, ref_price,
                   rights_dividend_value, type
              FROM dividend
             WHERE date BETWEEN :start AND :end
            """
        ),
        {"start": start.isoformat(), "end": end.isoformat()},
    ).mappings()

    differences: Counter[str] = Counter()
    examples: list[dict] = []
    compared = 0
    matched_keys = set()
    for row in legacy_rows:
        day = date.fromisoformat(row["date"])
        key = (row["symbol"], day)
        mine = stored.get(key)
        if mine is None:
            differences["legacy_only"] += 1
            if len(examples) < 20:
                examples.append(
                    {"kind": "legacy_only", "date": row["date"], "symbol": row["symbol"]}
                )
            continue
        matched_keys.add(key)
        compared += 1
        expected_type = LEGACY_TYPE.get(row["type"])
        if expected_type != mine["action_type"]:
            differences["action_type"] += 1
            if len(examples) < 20:
                examples.append(
                    {
                        "kind": "action_type", "date": row["date"],
                        "symbol": row["symbol"], "ours": mine["action_type"],
                        "legacy": row["type"],
                    }
                )
        for ours_field, legacy_field in (
            ("close_before", "close_before"),
            ("official_reference_price", "ref_price"),
            ("official_rights_dividend_value", "rights_dividend_value"),
        ):
            a, b = mine[ours_field], row[legacy_field]
            if (a is None) != (b is None):
                differences[f"{ours_field}:null_disagreement"] += 1
                kind = "null"
            elif a is not None and abs(Decimal(str(a)) - Decimal(str(b))) > TOLERANCE:
                differences[f"{ours_field}:legacy_snapshot_differs"] += 1
                kind = "legacy_snapshot_differs"
            else:
                continue
            if len(examples) < 20:
                examples.append(
                    {
                        "kind": kind, "field": ours_field, "date": row["date"],
                        "symbol": row["symbol"], "ours": str(a), "legacy": str(b),
                    }
                )

    source_only = len(stored) - len(matched_keys)
    return {
        "stored_rows": len(stored),
        "legacy_rows": compared + differences["legacy_only"],
        "compared_rows": compared,
        "differences": dict(differences),
        "difference_examples": examples,
        "source_only_rows": source_only,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile-corporate-actions")
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
            for source in SOURCES:
                entry = {
                    "import": import_counts(connection, source, args.start, args.end),
                    "duplicate_check": duplicate_check(connection, source),
                    "quarantined": quarantine_report(connection, source),
                }
                if source == RECONCILED_SOURCE:
                    entry["legacy_reconciliation"] = compare(
                        connection, legacy, args.start, args.end
                    )
                else:
                    entry["legacy_reconciliation"] = (
                        "no legacy baseline for this source (Step 19-b: legacy "
                        "`dividend` holds only TWT49U-shaped rows)"
                    )
                report["sources"][source] = entry
    finally:
        engine.dispose()
        legacy_engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    def is_defect(kind: str) -> bool:
        return kind == "legacy_only" or kind.endswith(":null_disagreement")

    clean = True
    for source, entry in report["sources"].items():
        if entry["duplicate_check"]["duplicate_locators"] > 0:
            clean = False
        reconciliation = entry["legacy_reconciliation"]
        if isinstance(reconciliation, dict) and any(
            is_defect(kind) for kind in reconciliation["differences"]
        ):
            clean = False
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
