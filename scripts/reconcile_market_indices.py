#!/usr/bin/env python
"""Reconcile imported market indices against legacy `stock_db`.

Step 18-b, the same shape as `reconcile_daily_prices.py`: the import counts from
the per-date manifests, the Step 16 coverage report, and the row-level
comparison with every difference classified (CLAUDE.md §78).

Two things are specific to indices.

Identity is `(source, section, published name)`, and legacy has only the name —
it kept one TPEx section and lost the other entirely. Legacy rows are therefore
matched against our **price** section, and our return-section rows are counted
as a class legacy never collected rather than as a difference.

The TAIEX cross-check is here too: `MI_5MINS_HIST` and the `MI_INDEX` index
sections publish the same index, so their closes must agree on every trade date.
A disagreement is reported, never averaged.

Usage:

    python scripts/reconcile_market_indices.py \
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

MARKETS = {"twse_mi_index": ("TWSE", "sii"), "tpex_index_summary": ("TPEx", "otc")}
TAIEX_SOURCE = "twse_mi_5mins_hist"
TAIEX_NAME = "發行量加權股價指數"

# Legacy stores floats; one hundredth of an index point is far below any
# published increment and far above float noise at these magnitudes.
TOLERANCE = Decimal("0.0001")

# Rows of the `漲跌證券數合計` market-breadth table, which legacy's parser
# stored in `market_indices` alongside real indices. They are instrument
# classes and totals, and no index section publishes them.
_NOT_AN_INDEX = re.compile(r"^\d+\.")
_NOT_AN_INDEX_NAMES = frozenset(
    {"證券合計(1+6+14+15)", "總計(1~15)", "持平", "未成交", "無比價"}
)


def import_counts(connection, source: str, start: date, end: date) -> dict:
    rows = connection.execute(
        sa.text(
            """
            SELECT status, result_counts, reconciliation
              FROM import_manifests
             WHERE dataset_code = 'market_index' AND source = :source
               AND (
                     (source_scope ->> 'trade_date')::date BETWEEN :start AND :end
                  OR (source_scope ->> 'month')::date BETWEEN :start AND :end
                   )
            """
        ),
        {"source": source, "start": start, "end": end},
    ).mappings()
    totals: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    versions = set()
    for row in rows:
        statuses[row["status"]] += 1
        for key, value in (row["result_counts"] or {}).items():
            if isinstance(value, int):
                totals[key] += value
        version = (row["reconciliation"] or {}).get("adapter_version")
        if version:
            versions.add(version)
    return {
        "manifest_statuses": dict(statuses),
        "adapter_versions": sorted(versions),
        "raw_artifact_count": totals["raw_artifact_count"],
        "business_version_count": totals["business_version_count"],
        "dedup_count": totals["dedup_count"],
        "evidence_count": totals["evidence_count"],
        "unknown_publication_count": totals["unknown_publication_count"],
        "rejected_quarantined_count": totals["rejected_quarantined_count"],
    }


def stored_closes(connection, source: str, start: date, end: date) -> dict:
    """One row per (section, name, date), latest revision, chosen deterministically."""
    rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT ON (v.market_index_id, v.trade_date)
                   i.index_code, v.trade_date, v.close_value, v.change_points
              FROM market_index_versions v
              JOIN market_index i ON i.id = v.market_index_id
             WHERE v.source = :source AND v.trade_date BETWEEN :start AND :end
             ORDER BY v.market_index_id, v.trade_date, v.ingested_at DESC, v.id DESC
            """
        ),
        {"source": source, "start": start, "end": end},
    ).mappings()
    stored = {}
    for row in rows:
        _, section, name = row["index_code"].split(":", 2)
        stored[(section, name, row["trade_date"])] = row
    return stored


def compare(connection, legacy, source: str, start: date, end: date) -> dict:
    market, legacy_market = MARKETS[source]
    stored = stored_closes(connection, source, start, end)
    # Legacy carries the published name alone, so a name is matched wherever
    # we hold it. The two feeds differ in which section that is: TWSE names its
    # return indices distinctly and legacy collected both sections, while TPEx
    # repeats one name in both and legacy kept only the price one. Matching on
    # the name alone handles both, because within one feed a legacy name is
    # unambiguous. Indexed once: a scan per legacy row is O(n*m) over roughly
    # 400,000 by 370,000, which does not finish.
    # The two feeds need different rules, because legacy collected them
    # differently. TWSE names its return indices distinctly and legacy stored
    # both sections, so a legacy name maps to whichever section holds it. TPEx
    # repeats one name in both sections and legacy kept only the price one, so
    # matching must stay inside the price section or every legacy row would be
    # compared against a return series.
    price_only = source == "tpex_index_summary"
    by_name_date: dict[tuple, tuple] = {}
    for key in stored:
        if price_only and key[0].split("/")[0] != "指數":
            continue
        by_name_date.setdefault((key[1], key[2]), key)
    legacy_rows = legacy.execute(
        sa.text(
            """
            SELECT date, symbol, index_close, index_change_points
              FROM market_indices
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
    compared = 0
    not_an_index = 0
    legacy_only_dates: set[str] = set()
    matched_keys = set()
    for row in legacy_rows:
        day = date.fromisoformat(row["date"])
        key = by_name_date.get((row["symbol"], day))
        if key is None:
            if (
                _NOT_AN_INDEX.match(row["symbol"])
                or row["symbol"] in _NOT_AN_INDEX_NAMES
            ):
                # Legacy's parser also stored the rows of the `漲跌證券數合計`
                # table — `1.一般股票`, `12.公司債`, `13.ETN` and so on. Those
                # are instrument classes in a market-breadth summary, not
                # indices, and no index section publishes them.
                not_an_index += 1
                continue
            differences["legacy_only"] += 1
            legacy_only_dates.add(row["date"])
            if len(examples) < 20:
                examples.append(
                    {"kind": "legacy_only", "date": row["date"], "name": row["symbol"]}
                )
            continue
        matched_keys.add(key)
        compared += 1
        mine = stored[key]
        for ours_field, legacy_field in (
            ("close_value", "index_close"),
            ("change_points", "index_change_points"),
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
                        "name": row["symbol"], "ours": str(a), "legacy": str(b),
                    }
                )

    source_only = [key for key in stored if key not in matched_keys]
    return_section = sum(
        1 for key in source_only if key[0].split("/")[0] == "報酬指數"
    )
    return {
        "market": market,
        "stored_rows": len(stored),
        "legacy_rows": compared + differences["legacy_only"],
        "compared_rows": compared,
        "differences": dict(differences),
        "difference_examples": examples,
        "legacy_only_not_an_index": not_an_index,
        "legacy_only_dates": sorted(legacy_only_dates),
        "source_only_rows": len(source_only),
        "source_only_return_section": return_section,
        "source_only_price_section": len(source_only) - return_section,
    }


def taiex_cross_check(connection, start: date, end: date) -> dict:
    """The two TWSE sources publish the same index; their closes must agree."""
    rows = connection.execute(
        sa.text(
            """
            SELECT h.trade_date, h.close_value AS ohlc_close,
                   w.close_value AS list_close
              FROM (
                    SELECT DISTINCT ON (v.trade_date) v.trade_date, v.close_value
                      FROM market_index_versions v
                      JOIN market_index i ON i.id = v.market_index_id
                     WHERE v.source = :ohlc AND i.index_code LIKE '%' || :name
                       AND v.trade_date BETWEEN :start AND :end
                     ORDER BY v.trade_date, v.ingested_at DESC, v.id DESC
                   ) h
              JOIN (
                    SELECT DISTINCT ON (v.trade_date) v.trade_date, v.close_value
                      FROM market_index_versions v
                      JOIN market_index i ON i.id = v.market_index_id
                     WHERE v.source = 'twse_mi_index'
                       AND i.index_code = 'twse_mi_index:指數/臺灣證券交易所:' || :name
                       AND v.trade_date BETWEEN :start AND :end
                     ORDER BY v.trade_date, v.ingested_at DESC, v.id DESC
                   ) w ON w.trade_date = h.trade_date
            """
        ),
        {"ohlc": TAIEX_SOURCE, "name": TAIEX_NAME, "start": start, "end": end},
    ).mappings()
    compared = 0
    disagreements = []
    for row in rows:
        compared += 1
        if abs(row["ohlc_close"] - row["list_close"]) > TOLERANCE:
            disagreements.append(
                {
                    "trade_date": row["trade_date"].isoformat(),
                    "mi_5mins_hist": str(row["ohlc_close"]),
                    "mi_index": str(row["list_close"]),
                }
            )
    return {
        "compared_dates": compared,
        "disagreements": len(disagreements),
        "examples": disagreements[:10],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile-market-indices")
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
                    connection, dataset_code="market_index", market=market,
                    start=args.start, end=args.end,
                )
                report["sources"][source] = {
                    "import": import_counts(
                        connection, source, args.start, args.end
                    ),
                    "coverage": {
                        "expected_dates": len(coverage.expected),
                        "observed_dates": len(coverage.observed),
                        "missing_dates": [d.isoformat() for d in coverage.missing],
                        "unexpected_dates": [
                            d.isoformat() for d in coverage.unexpected
                        ],
                        "is_complete": coverage.is_complete,
                    },
                    "legacy_reconciliation": compare(
                        connection, legacy, source, args.start, args.end
                    ),
                }
            report["sources"][TAIEX_SOURCE] = {
                "import": import_counts(
                    connection, TAIEX_SOURCE, args.start, args.end
                ),
                "taiex_cross_check": taiex_cross_check(
                    connection, args.start, args.end
                ),
            }
    finally:
        engine.dispose()
        legacy_engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    def is_defect(kind: str) -> bool:
        return kind == "legacy_only" or kind.endswith(":null_disagreement")

    clean = all(
        entry["coverage"]["is_complete"]
        and not any(
            is_defect(kind)
            for kind in entry["legacy_reconciliation"]["differences"]
        )
        for entry in report["sources"].values()
        if "coverage" in entry
    )
    clean = clean and not report["sources"][TAIEX_SOURCE][
        "taiex_cross_check"
    ]["disagreements"]
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
