#!/usr/bin/env python
"""Reconcile the imported institutional market summary against legacy `stock_db`.

Step 20-b, the same shape as `reconcile_institutional_investors.py`: import
counts from the per-date manifests, the Step 16 coverage report, and the
row-level comparison with every difference classified (CLAUDE.md §78).

Legacy `institutional_summary` holds one row per (date, market, institution),
in TWD, under its own English codes. `LEGACY_NAMES` maps each code to the name
the exchange publishes. The mapping exists for this comparison only; nothing
stored uses it. Legacy never kept TPEx's two subtotal rows, 外資及陸資合計 and
自營商合計, so those are reported as source-only.

Each difference is classified from the legacy archive file it came from
(`--legacy-archive`, one `<yyyy>/<yyyymmdd>/<sii|otc>.csv` per date). A value
difference gets a class only if two things hold. The legacy database row must
equal its own archive file, so the difference is in what the source served
and not in legacy's parsing. And on that date at least one nonzero row must
agree exactly with ours, so the two sides share a scale and a row mapping. A
systematic unit or mapping error in ours fails the second test on every date
and stays unexplained (code review of #31).

- **Captured before settlement.** The file was saved before 03:00 on D+1, the
  `exchange_daily_settled@1` instant; ours is the settled value (ADR-0020 §10).
- **Source changed after the legacy capture.** The file was saved after
  settlement, both rows satisfy buy − sell = net, and the source now serves
  another value. This is the correction look-ahead ADR-0020 accepts for
  exchange daily data before forward capture; it is counted, not corrected.
- **Five-row layout.** On some dates legacy saved a file with one combined
  `外資` row and no foreign-dealer row, which its parser dropped. The row
  equals our 外資及陸資(不含外資自營商) and the total is unchanged.

Two further checks need no legacy at all:

- **Published identities.** Every stored row is tested for buy − sell = net.
- **Published totals.** Every stored date is tested for its market's totals:
  TWSE 合計 is the sum of the self-dealer, hedge, trust and foreign rows;
  TPEx's two subtotals are the sums of their indented rows, and its
  三大法人合計* is foreign + trust + dealer total. Both exchanges state that
  the foreign-dealer row is already inside the dealer rows and is not added
  again. Nothing is recomputed; a date that fails is reported, not corrected.

Usage:

    python scripts/reconcile_institutional_summary.py \
        --database-url postgresql+psycopg://... \
        --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.coverage import CoverageValidator

DATASET = "institutional_market_summary"
MARKETS = {"twse_bfi82u": ("TWSE", "sii"), "tpex_insti_summary": ("TPEx", "otc")}
AMOUNTS = ("buy", "sell", "net")
TAIPEI = ZoneInfo("Asia/Taipei")
FOREIGN = "外資及陸資(不含外資自營商)"
LEGACY_NAMES = {
    "sii": {
        "dealer_self": "自營商(自行買賣)",
        "dealer_hedge": "自營商(避險)",
        "investment_trust": "投信",
        "foreign_investors": "外資及陸資(不含外資自營商)",
        "foreign_dealer": "外資自營商",
        "total": "合計",
    },
    "otc": {
        "foreign_investors": "外資及陸資(不含自營商)",
        "foreign_dealer": "外資自營商",
        "investment_trust": "投信",
        "dealer_self": "自營商(自行買賣)",
        "dealer_hedge": "自營商(避險)",
        "total": "三大法人合計*",
    },
}
# Total name -> the rows it is published as the sum of.
TOTALS = {
    "TWSE": {
        "合計": ("自營商(自行買賣)", "自營商(避險)", "投信", "外資及陸資(不含外資自營商)"),
    },
    "TPEx": {
        "外資及陸資合計": ("外資及陸資(不含自營商)", "外資自營商"),
        "自營商合計": ("自營商(自行買賣)", "自營商(避險)"),
        "三大法人合計*": ("外資及陸資(不含自營商)", "投信", "自營商合計"),
    },
}


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
    """Latest revision per (date, institution), chosen deterministically."""
    rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT ON (trade_date, institution)
                   trade_date, institution, buy, sell, net
              FROM institutional_market_summary_versions
             WHERE source = :source AND trade_date BETWEEN :start AND :end
             ORDER BY trade_date, institution, ingested_at DESC, id DESC
            """
        ),
        {"source": source, "start": start, "end": end},
    ).mappings()
    grouped: dict[date, dict[str, tuple[int, int, int]]] = {}
    for row in rows:
        grouped.setdefault(row["trade_date"], {})[row["institution"]] = tuple(
            int(row[name]) for name in AMOUNTS
        )
    return grouped


def settled_at(day: date) -> datetime:
    """`exchange_daily_settled@1`: 03:00 Asia/Taipei on D+1."""
    return datetime.combine(day + timedelta(days=1), time(3), TAIPEI)


def legacy_file(archive: Path, day: date, legacy_market: str) -> Path:
    return archive / f"{day:%Y}" / f"{day:%Y%m%d}" / f"{legacy_market}.csv"


def legacy_file_rows(path: Path) -> dict[str, tuple[int, int, int]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    return {
        row[0].strip(): tuple(int(cell.replace(",", "")) for cell in row[1:4])
        for row in rows[1:]
        if len(row) >= 4
    }


def classify_value(
    archive: Path, day: date, legacy_market: str, name: str, theirs: tuple,
    ours_day: dict,
) -> str:
    """A class, or bare `value_differs` when the difference is unexplained."""
    buy, sell, net = theirs
    if buy - sell != net:
        return "value_differs:legacy_row_inconsistent"
    path = legacy_file(archive, day, legacy_market)
    if not path.exists():
        return "value_differs:legacy_file_missing"
    archived = legacy_file_rows(path)
    if archived.get(name) != theirs:
        return "value_differs"
    anchored = any(
        values != (0, 0, 0) and ours_day.get(other) == values
        for other, values in archived.items()
    )
    if not anchored:
        return "value_differs"
    saved = datetime.fromtimestamp(path.stat().st_mtime, TAIPEI)
    if saved < settled_at(day):
        return "value_differs:legacy_captured_before_settlement"
    return "value_differs:source_changed_after_legacy_capture"


def classify_missing(
    archive: Path, day: date, legacy_market: str, name: str, ours_day: dict
) -> str:
    path = legacy_file(archive, day, legacy_market)
    if legacy_market == "sii" and path.exists():
        rows = legacy_file_rows(path)
        if (
            "外資" in rows
            and "外資自營商" not in rows
            and rows["外資"] == ours_day.get(FOREIGN)
            and rows.get("合計") == ours_day.get("合計")
        ):
            return "legacy_missing_row:legacy_file_five_row_layout"
    return "legacy_missing_row"


def compare(
    connection, legacy, source: str, start: date, end: date, archive: Path
) -> dict:
    market, legacy_market = MARKETS[source]
    names = LEGACY_NAMES[legacy_market]
    stored = stored_by_date(connection, source, start, end)
    legacy_by_date: dict[date, dict[str, tuple]] = {}
    unmapped: Counter[str] = Counter()
    for row in legacy.execute(
        sa.text(
            """
            SELECT date, institution, buy, sell, net
              FROM institutional_summary
             WHERE market = :market AND date BETWEEN :start AND :end
            """
        ),
        {"market": legacy_market, "start": start.isoformat(), "end": end.isoformat()},
    ).mappings():
        name = names.get(row["institution"])
        if name is None:
            unmapped[row["institution"]] += 1
            continue
        legacy_by_date.setdefault(date.fromisoformat(row["date"]), {})[name] = tuple(
            None if row[field] is None else int(row[field]) for field in AMOUNTS
        )

    differences: Counter[str] = Counter()
    examples: dict[str, list[dict]] = {}
    source_only: Counter[str] = Counter()
    identity_failures: list[dict] = []
    total_failures: list[dict] = []
    compared = 0

    def note(kind: str, sample: dict) -> None:
        differences[kind] += 1
        bucket = examples.setdefault(kind, [])
        if len(bucket) < 20:
            bucket.append(sample)

    for day in sorted(set(stored) | set(legacy_by_date)):
        ours_day = stored.get(day, {})
        theirs_day = legacy_by_date.get(day, {})
        for name, (buy, sell, net) in ours_day.items():
            if buy - sell != net:
                identity_failures.append(
                    {"date": day.isoformat(), "institution": name}
                )
        for total, parts in TOTALS[market].items():
            if total not in ours_day or not all(part in ours_day for part in parts):
                continue
            summed = tuple(
                sum(ours_day[part][index] for part in parts) for index in range(3)
            )
            if summed != ours_day[total]:
                total_failures.append({
                    "date": day.isoformat(), "total": total,
                    "published": ours_day[total], "sum_of_parts": summed,
                })
        if not ours_day:
            for name in theirs_day:
                note("legacy_only:date_not_stored",
                     {"date": day.isoformat(), "institution": name})
            continue
        for name, theirs in theirs_day.items():
            sample = {"date": day.isoformat(), "institution": name}
            if name not in ours_day:
                note("legacy_only", sample)
                continue
            compared += 1
            ours = ours_day[name]
            if ours != theirs:
                sample.update({"ours": ours, "legacy": theirs})
                note(
                    classify_value(
                        archive, day, legacy_market, name, theirs, ours_day
                    ),
                    sample,
                )
        for name in ours_day:
            if name in theirs_day:
                continue
            if name in names.values():
                note(
                    classify_missing(archive, day, legacy_market, name, ours_day),
                    {"date": day.isoformat(), "institution": name},
                )
            else:
                source_only[name] += 1

    return {
        "market": market,
        "stored_rows": sum(len(rows) for rows in stored.values()),
        "stored_dates": len(stored),
        "legacy_rows": sum(len(rows) for rows in legacy_by_date.values()),
        "legacy_unmapped_institutions": dict(unmapped),
        "compared_rows": compared,
        "differences": dict(differences),
        "difference_examples": examples,
        "source_only_rows": dict(source_only),
        "published_identity_failures": len(identity_failures),
        "published_identity_examples": identity_failures[:10],
        "published_total_failures": len(total_failures),
        "published_total_examples": total_failures[:10],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile-institutional-summary")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", default=os.getenv("LEGACY_DATABASE_URL"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 2))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 11))
    parser.add_argument(
        "--legacy-archive",
        type=Path,
        default=Path.home() / "GitHubLL/my_stock_project/data/raw/institutional_summary",
    )
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
                        connection, legacy, source, args.start, args.end,
                        args.legacy_archive,
                    ),
                }
    finally:
        engine.dispose()
        legacy_engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    clean = all(
        entry["coverage"]["is_complete"]
        and not entry["legacy_reconciliation"]["legacy_unmapped_institutions"]
        # Every difference must carry a class; a bare kind is unexplained.
        and all(
            ":" in kind and not kind.startswith("legacy_only")
            and not kind.endswith("legacy_file_missing")
            for kind in entry["legacy_reconciliation"]["differences"]
        )
        and entry["legacy_reconciliation"]["published_identity_failures"] == 0
        and entry["legacy_reconciliation"]["published_total_failures"] == 0
        for entry in report["sources"].values()
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
