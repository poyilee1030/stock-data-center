#!/usr/bin/env python
"""Reconcile imported foreign holding against legacy `stock_db`.

Step 20-d, the shape of `reconcile_institutional_investors.py` and
`reconcile_institutional_summary.py`: import counts from the per-date manifests,
the Step 16 coverage report, and the row-level comparison with every difference
classified (CLAUDE.md §78).

TPEx has two sources (migration f2b6d8a4c1e9): MOPS drops a security that is
no longer listed from every past date, and TPEx's `insti/qfii` omits most ETFs.
A legacy row one of them lacks is explained when the other has it on that
date, and unexplained otherwise. `insti/qfii` rounds D where MOPS truncates it,
so a legacy row (saved from MOPS) differing only that way is its own class.

Legacy `foreign_holding` stores six of the contract's nine values, as floats:
issued, investable and held shares, the two ratios, and the shared legal limit.
Shares compare exactly; ratios compare at the two decimals both sides publish.
The mainland limit, change reason and last-update date have no legacy column.

Two checks need no legacy, and fail the run if any stored row breaks them. Both
are the source's own arithmetic, stated on TPEx's `insti/qfii` page as
B = A×F − C, D = B/A, E = C/A (audit §4.4), and measured on both markets:

- **Ratios are the share ratios at two decimals.** E = trunc(C / A, 2) in
  every source; D = trunc(B / A, 2) in TWSE and MOPS and round(B / A, 2) in
  `insti/qfii`, in percent, whenever A > 0.
- **Holding never exceeds the cap.** B + C ≤ floor(A × F). The source
  sometimes withholds capacity (B is 0 for some issuers, smaller for others),
  so rows below the cap are counted, not failed.

A value difference is classified from the legacy archive file it came from
(`--legacy-archive`, `<yyyy>/<yyyymmdd>/<sii|otc>.csv`), the way Step 20-b's
review required: the legacy row must equal its own file, and some other row of
that file must agree with ours exactly, before the file's save time may explain
anything. A file saved before 03:00 on D+1 is a same-day capture; a later one
means the source changed after legacy fetched it.

Usage:

    python scripts/reconcile_foreign_holding.py \
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
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.coverage import CoverageValidator

DATASET = "foreign_holding"
MARKETS = {
    "twse_mi_qfiis": ("TWSE", "sii"),
    "mops_t13sa150_otc": ("TPEx", "otc"),
    "tpex_insti_qfii": ("TPEx", "otc"),
}
# How each source derives D (investable ratio) from B / A: MOPS and TWSE
# truncate, TPEx's insti/qfii rounds half up. E is truncated everywhere.
INVESTABLE_ROUNDING = {
    "twse_mi_qfiis": ROUND_DOWN,
    "mops_t13sa150_otc": ROUND_DOWN,
    "tpex_insti_qfii": ROUND_HALF_UP,
}
# Published rows that break the source's own B = A×F − C, listed one by one so
# that a new one fails the run. Stored as published; nothing is recomputed.
KNOWN_SOURCE_ANOMALIES = {
    # insti/qfii kept B at 25,000,000 while C rose to 1,000; MOPS published
    # B = 24,999,000 for the same date.
    ("tpex_insti_qfii", "2026-04-07", "6028"): "above_cap",
}
# The other TPEx source, whose rows explain what one of them lacks.
COMPANION = {"mops_t13sa150_otc": "tpex_insti_qfii", "tpex_insti_qfii": "mops_t13sa150_otc"}
SHARES = ("issued_shares", "investable_shares", "held_shares")
RATIOS = ("investable_ratio", "held_ratio", "foreign_legal_limit_ratio")
LEGACY = {
    "issued_shares": "issued_shares",
    "investable_shares": "foreign_investable_shares",
    "held_shares": "foreign_held_shares",
    "investable_ratio": "foreign_investable_ratio",
    "held_ratio": "foreign_held_ratio",
    "foreign_legal_limit_ratio": "foreign_legal_limit_ratio",
}
# Archive CSV column of each compared value, by legacy market.
ARCHIVE_COLUMNS = {
    "sii": {"issued_shares": 3, "investable_shares": 4, "held_shares": 5,
            "investable_ratio": 6, "held_ratio": 7, "foreign_legal_limit_ratio": 8},
    "otc": {"issued_shares": 2, "investable_shares": 3, "held_shares": 4,
            "investable_ratio": 5, "held_ratio": 6, "foreign_legal_limit_ratio": 7},
}
CENT = Decimal("0.01")
TAIPEI = ZoneInfo("Asia/Taipei")


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
    rows = connection.execute(
        sa.text(
            f"""
            SELECT DISTINCT ON (v.security_id, v.trade_date)
                   s.security_code, v.trade_date,
                   {", ".join(f"v.{name}" for name in (*SHARES, *RATIOS))}
              FROM foreign_holding_versions v
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
            **{name: int(row[name]) for name in SHARES},
            **{name: Decimal(row[name]).quantize(CENT) for name in RATIOS},
        }
    return grouped


def arithmetic(row: dict, source: str) -> tuple[list[str], bool]:
    """(identities broken, capacity withheld) for one stored row."""
    issued, investable, held = (row[name] for name in SHARES)
    broken: list[str] = []
    if issued > 0:
        for ratio, shares, rounding in (
            ("investable_ratio", investable, INVESTABLE_ROUNDING[source]),
            ("held_ratio", held, ROUND_DOWN),
        ):
            expected = (Decimal(shares) * 100 / issued).quantize(CENT, rounding)
            if row[ratio] != expected:
                broken.append(ratio)
    cap = (Decimal(issued) * row["foreign_legal_limit_ratio"] / 100).to_integral_value(
        ROUND_DOWN
    )
    if investable + held > cap:
        broken.append("above_cap")
    return broken, investable + held < cap


def rounded_only(ours: dict, theirs: dict) -> bool:
    """insti/qfii against a legacy row saved from MOPS: the same shares, and D
    differing exactly as rounding versus truncation of the same B / A."""
    if any(ours[name] != theirs[name] for name in LEGACY if name != "investable_ratio"):
        return False
    issued = ours["issued_shares"]
    if issued <= 0:
        return False
    share = Decimal(ours["investable_shares"]) * 100 / issued
    return (
        ours["investable_ratio"] == share.quantize(CENT, ROUND_HALF_UP)
        and theirs["investable_ratio"] == share.quantize(CENT, ROUND_DOWN)
    )


def legacy_value(name: str, value) -> object:
    if value is None:
        return None
    if name in SHARES:
        return int(value)
    return Decimal(repr(value)).quantize(CENT)


def settled_at(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time(3), TAIPEI)


def legacy_file(archive: Path, day: date, legacy_market: str) -> Path:
    return archive / f"{day:%Y}" / f"{day:%Y%m%d}" / f"{legacy_market}.csv"


def legacy_file_rows(path: Path, legacy_market: str) -> dict[str, dict]:
    columns = ARCHIVE_COLUMNS[legacy_market]
    rows: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for cells in reader:
            if not cells or not cells[0].strip():
                continue
            try:
                rows[cells[0].strip()] = {
                    name: int(cells[index].replace(",", "")) if name in SHARES
                    else Decimal(cells[index].strip()).quantize(CENT)
                    for name, index in columns.items()
                }
            except (IndexError, ArithmeticError, ValueError):
                continue
    return rows


def classify_value(
    archive: Path, day: date, legacy_market: str, code: str, theirs: dict,
    ours_day: dict, cache: dict,
) -> str:
    """A class, or bare `value_differs` when the difference is unexplained."""
    if any(value is None for value in theirs.values()):
        return "value_differs:legacy_row_incomplete"
    path = legacy_file(archive, day, legacy_market)
    if not path.exists():
        return "value_differs:legacy_file_missing"
    if path not in cache:
        cache[path] = legacy_file_rows(path, legacy_market)
    archived = cache[path]
    if archived.get(code) != theirs:
        return "value_differs"
    anchored = any(
        other != code and values["issued_shares"] > 0 and ours_day.get(other) == values
        for other, values in archived.items()
    )
    if not anchored:
        return "value_differs"
    saved = datetime.fromtimestamp(path.stat().st_mtime, TAIPEI)
    if saved < settled_at(day):
        return "value_differs:legacy_captured_before_settlement"
    return "value_differs:source_changed_after_legacy_capture"


def compare(connection, legacy, archive: Path, source: str, start: date, end: date) -> dict:
    market, legacy_market = MARKETS[source]
    stored = stored_by_date(connection, source, start, end)
    companion = (
        stored_by_date(connection, COMPANION[source], start, end)
        if source in COMPANION else {}
    )
    ever_stored = {code for rows in stored.values() for code in rows}
    companion_ever = {code for rows in companion.values() for code in rows}
    first_seen: dict[str, date] = {}
    for day_, rows in sorted(stored.items()):
        for code in rows:
            first_seen.setdefault(code, day_)
    columns = ", ".join(LEGACY.values())
    legacy_by_date: dict[date, dict[str, dict]] = {}
    for row in legacy.execute(
        sa.text(
            f"""
            SELECT date, symbol, {columns}
              FROM foreign_holding
             WHERE market = :market AND date BETWEEN :start AND :end
            """
        ),
        {"market": legacy_market, "start": start.isoformat(), "end": end.isoformat()},
    ).mappings():
        legacy_by_date.setdefault(date.fromisoformat(row["date"]), {})[row["symbol"]] = {
            name: legacy_value(name, row[column]) for name, column in LEGACY.items()
        }

    ever_in_legacy = {code for rows in legacy_by_date.values() for code in rows}
    differences: Counter[str] = Counter()
    by_date: dict[str, Counter[str]] = {}
    examples: dict[str, list[dict]] = {}
    wrong_dates: dict[str, dict] = {}
    source_only: Counter[str] = Counter()
    broken: Counter[str] = Counter()
    broken_examples: list[dict] = []
    withheld = 0
    known_anomalies: list[dict] = []
    differing_fields: Counter[str] = Counter()
    cache: dict = {}
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
            failed, below = arithmetic(row, source)
            withheld += below
            for name in failed:
                if KNOWN_SOURCE_ANOMALIES.get((source, day.isoformat(), code)) == name:
                    known_anomalies.append({"date": day.isoformat(), "code": code,
                                            "identity": name})
                    continue
                broken[name] += 1
                if len(broken_examples) < 10:
                    broken_examples.append({"date": day.isoformat(), "code": code,
                                            "identity": name})
        present = [code for code in theirs_day if code in ours_day]
        agreeing = sum(
            ours_day[code] == theirs_day[code]
            or (source == "tpex_insti_qfii" and rounded_only(ours_day[code], theirs_day[code]))
            for code in present
        )
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
                other = companion.get(day, {}).get(code)
                if source == "mops_t13sa150_otc" and code not in ever_stored:
                    # MOPS drops a security that is no longer listed on TPEx
                    # from every past date (migration f2b6d8a4c1e9); insti/qfii
                    # usually still has it on that date.
                    sample["in_insti_qfii"] = other is not None
                    note("legacy_only:mops_omits_security_no_longer_listed", day, sample)
                elif (
                    source == "tpex_insti_qfii" and code in first_seen
                    and day < first_seen[code] and other is None
                ):
                    # Legacy (MOPS) listed 5236 on 2021-07-28, the day before its
                    # first trade; insti/qfii starts at the first trade, and MOPS
                    # dropped 5236 after it moved to TWSE.
                    note("legacy_only:before_security_first_appears_in_source", day, sample)
                elif other is None:
                    note("legacy_only", day, sample)
                else:
                    # insti/qfii omits most ETFs; MOPS lists them (audit 4.4).
                    note("legacy_only:qfii_omits_security_listed_in_mops", day, sample)
                continue
            compared += 1
            ours = ours_day[code]
            differing = [name for name in LEGACY if ours[name] != theirs[name]]
            if differing and source == "tpex_insti_qfii" and rounded_only(ours, theirs):
                note("investable_ratio_rounded_not_truncated", day, sample)
                continue
            mops_row = companion.get(day, {}).get(code) if source == "tpex_insti_qfii" else None
            if mops_row is None and source == "tpex_insti_qfii" and code not in companion_ever:
                # MOPS no longer carries the security; legacy's archived MOPS
                # page for the date stands in for it.
                path = legacy_file(archive, day, legacy_market)
                if path.exists():
                    if path not in cache:
                        cache[path] = legacy_file_rows(path, legacy_market)
                    mops_row = cache[path].get(code)
            if differing and mops_row is not None and all(
                mops_row[name] == theirs[name] for name in LEGACY
            ):
                # Legacy's file is a MOPS page and equals the MOPS row: the two
                # TPEx sources disagree on this date. Both are stored; nothing
                # is merged (CLAUDE.md §30).
                sample.update({name: {"ours": str(ours[name]), "legacy": str(theirs[name])}
                               for name in differing})
                note("value_differs:qfii_disagrees_with_mops_same_date", day, sample)
                differing_fields.update(differing)
                continue
            if differing:
                sample.update({
                    name: {"ours": str(ours[name]), "legacy": str(theirs[name])}
                    for name in differing
                })
                note(classify_value(archive, day, legacy_market, code, theirs,
                                    ours_day, cache), day, sample)
                differing_fields.update(differing)
        for code in ours_day:
            if code not in theirs_day:
                if code not in ever_in_legacy:
                    # Never in legacy at all: instruments legacy did not keep,
                    # and TPEx securities that left before legacy's 2026-02
                    # MOPS fetch (the same survivorship, one fetch earlier).
                    source_only["security_absent_from_legacy_entirely"] += 1
                else:
                    source_only["common_stock" if len(code) == 4 and code.isdigit()
                                else "etf_or_other_instrument"] += 1

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
        "differing_fields": dict(differing_fields),
        "arithmetic_failures": dict(broken),
        "arithmetic_examples": broken_examples,
        "known_source_anomalies": known_anomalies,
        "capacity_withheld_rows": withheld,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile-foreign-holding")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", default=os.getenv("LEGACY_DATABASE_URL"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 2))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 11))
    parser.add_argument(
        "--legacy-archive", type=Path,
        default=Path.home() / "GitHubLL/my_stock_project/data/raw/foreign_holding",
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
                # The market's declaration names one source; measure this one's
                # own dates against the same expected calendar.
                observed = set(connection.execute(
                    sa.text(
                        "SELECT DISTINCT trade_date FROM foreign_holding_versions "
                        "WHERE source = :source AND trade_date BETWEEN :start AND :end"
                    ),
                    {"source": source, "start": args.start, "end": args.end},
                ).scalars())
                expected = set(coverage.expected)
                report["sources"][source] = {
                    "import": import_counts(connection, source, args.start, args.end),
                    "coverage": {
                        "expected_dates": len(expected),
                        "observed_dates": len(observed & expected),
                        "missing_dates": sorted(d.isoformat() for d in expected - observed),
                        "unexpected_dates": sorted(d.isoformat() for d in observed - expected),
                        "is_complete": expected <= observed <= expected,
                    },
                    "legacy_reconciliation": compare(
                        connection, legacy, args.legacy_archive, source,
                        args.start, args.end,
                    ),
                }
    finally:
        engine.dispose()
        legacy_engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    clean = all(
        entry["coverage"]["is_complete"]
        and "legacy_only" not in entry["legacy_reconciliation"]["differences"]
        and "value_differs" not in entry["legacy_reconciliation"]["differences"]
        and not entry["legacy_reconciliation"]["arithmetic_failures"]
        for entry in report["sources"].values()
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
