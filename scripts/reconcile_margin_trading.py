#!/usr/bin/env python
"""Reconcile imported margin trading against legacy `stock_db`.

Step 21-a, the shape of `reconcile_foreign_holding.py`: import counts from the
per-date manifests, the Step 16 coverage report, and the row-level comparison
with every difference classified (CLAUDE.md §78).

Legacy `margin_trading` holds the same 13 quantities in lots, as floats; they are
compared × 1,000 against the stored shares, exactly. Legacy has no utilization
ratio.

Every class is proven before it is granted (the lesson of #33's review):

- A legacy date that disagrees with ours is blamed on legacy's file only when
  another stored date reproduces its rows, or, failing that, when our rows for
  the date are continuous with the neighbouring stored dates.
- A value difference is classified from the legacy archive file it came from
  (`--legacy-archive`, `<yyyy>/<yyyymmdd>/<sii|otc>.csv`): the legacy row must
  equal its own file and another row of that file must agree with ours, before
  the file's save time may explain it.

Checks that need no legacy, run on every stored row:

- **Balances roll forward.** margin: previous + buy − sell − cash repayment =
  balance; short: previous + sell − buy − stock repayment = balance. Reported,
  and a failure fails the run.
- **The lot sizes are right.** Both exchanges stop margin at 25% of listed
  shares, so a next-day limit, converted at the security's lot size (1,000, or
  the adapter's `TWSE_LOT_SHARES`), is at most about 25% of the issued shares
  Step 20-d stored. A ratio above 5 is explained only where the issued shares
  moved at least twofold nearby; any other one fails the run, which is how
  008201's 100-share lot was found.

Usage:

    python scripts/reconcile_margin_trading.py \
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
from stock_data_center.ingestion.adapters.margin_trading import TWSE_LOT_SHARES

DATASET = "margin_trading"
MARKETS = {"twse_mi_margn": ("TWSE", "sii"), "tpex_margin_balance": ("TPEx", "otc")}
# Shares per lot for the listed exceptions (TWSE's note: offshore ETFs and
# foreign secondary listings); every other security trades in lots of 1,000.
LOT_SHARES = {"twse_mi_margn": dict(TWSE_LOT_SHARES), "tpex_margin_balance": {}}
# Step 20-d's issued shares for the lot check, per market.
ISSUED_SOURCE = {"TWSE": "twse_mi_qfiis", "TPEx": "mops_t13sa150_otc"}
LEGACY = {
    "margin_buy": "margin_long_buy",
    "margin_sell": "margin_long_sell",
    "margin_cash_repayment": "margin_long_cash_repay",
    "margin_previous_balance": "margin_long_prev_balance",
    "margin_balance": "margin_long_balance",
    "margin_next_limit": "margin_long_limit",
    "short_buy": "margin_short_buy",
    "short_sell": "margin_short_sell",
    "short_stock_repayment": "margin_short_cash_repay",
    "short_previous_balance": "margin_short_prev_balance",
    "short_balance": "margin_short_balance",
    "short_next_limit": "margin_short_limit",
    "offset_balance": "offset_balance",
}
FIELDS = tuple(LEGACY)
# Archive CSV column of each compared value, by legacy market.
ARCHIVE_COLUMNS = {
    "sii": dict(zip(FIELDS, (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14), strict=True)),
    "otc": {
        "margin_previous_balance": 2, "margin_buy": 3, "margin_sell": 4,
        "margin_cash_repayment": 5, "margin_balance": 6, "margin_next_limit": 9,
        "short_previous_balance": 10, "short_sell": 11, "short_buy": 12,
        "short_stock_repayment": 13, "short_balance": 14, "short_next_limit": 17,
        "offset_balance": 18,
    },
}
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
        **{key: totals[key] for key in (
            "raw_artifact_count", "business_version_count", "dedup_count",
            "evidence_count", "unknown_publication_count", "rejected_quarantined_count",
        )},
    }


def stored_by_date(connection, source: str, start: date, end: date) -> dict:
    """Latest revision per (security, date), in lots, chosen deterministically."""
    rows = connection.execute(
        sa.text(
            f"""
            SELECT DISTINCT ON (v.security_id, v.trade_date)
                   s.security_code, v.trade_date,
                   {", ".join(f"v.{name}" for name in FIELDS)}
              FROM margin_trading_versions v
              JOIN security s ON s.id = v.security_id
             WHERE v.source = :source AND v.trade_date BETWEEN :start AND :end
             ORDER BY v.security_id, v.trade_date, v.ingested_at DESC, v.id DESC
            """
        ),
        {"source": source, "start": start, "end": end},
    ).mappings()
    grouped: dict[date, dict[str, dict]] = {}
    for row in rows:
        lots = {}
        lot = LOT_SHARES[source].get(row["security_code"], 1000)
        for name in FIELDS:
            shares = int(row[name])
            if shares % lot:
                raise ValueError(
                    f"{row['security_code']} {row['trade_date']} {name} = {shares} "
                    "is not a whole number of lots"
                )
            lots[name] = shares // lot
        grouped.setdefault(row["trade_date"], {})[row["security_code"]] = lots
    return grouped


def issued_by_date(connection, source: str, start: date, end: date) -> dict:
    rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT ON (v.security_id, v.trade_date)
                   s.security_code, v.trade_date, v.issued_shares
              FROM foreign_holding_versions v
              JOIN security s ON s.id = v.security_id
             WHERE v.source = :source AND v.trade_date BETWEEN :start AND :end
             ORDER BY v.security_id, v.trade_date, v.ingested_at DESC, v.id DESC
            """
        ),
        {"source": source, "start": start, "end": end},
    ).all()
    grouped: dict[date, dict[str, int]] = {}
    for code, day, issued in rows:
        if issued:
            grouped.setdefault(day, {})[code] = int(issued)
    return grouped


def issued_moved(issued: dict, code: str, day: date, window: int = 150) -> bool:
    """Whether the security's issued shares changed at least twofold within
    `window` stored dates either side of `day`."""
    days = sorted(issued)
    index = days.index(day)
    values = [
        issued[other][code]
        for other in days[max(0, index - window): index + window + 1]
        if code in issued[other]
    ]
    return bool(values) and max(values) >= 2 * min(values)


def rolls_forward(row: dict) -> list[str]:
    failed = []
    if (row["margin_previous_balance"] + row["margin_buy"] - row["margin_sell"]
            - row["margin_cash_repayment"] != row["margin_balance"]):
        failed.append("margin_balance")
    if (row["short_previous_balance"] + row["short_sell"] - row["short_buy"]
            - row["short_stock_repayment"] != row["short_balance"]):
        failed.append("short_balance")
    return failed


def agreement(ours_day: dict, theirs_day: dict) -> float:
    present = [code for code in theirs_day if code in ours_day]
    if not present:
        return 0.0
    return sum(ours_day[code] == theirs_day[code] for code in present) / len(present)


def matching_date(stored: dict, theirs_day: dict, exclude: date) -> date | None:
    for other_day, ours_day in stored.items():
        if other_day == exclude:
            continue
        if sum(code in ours_day for code in theirs_day) < 0.9 * len(theirs_day):
            continue
        if agreement(ours_day, theirs_day) == 1.0:
            return other_day
    return None


def neighbour_continuity(stored: dict, day: date) -> float | None:
    """The lower share of our securities whose next-day margin limit equals the
    previous and the next stored date's."""
    days = sorted(stored)
    index = days.index(day)
    if index == 0 or index == len(days) - 1:
        return None
    scores = []
    for other in (days[index - 1], days[index + 1]):
        shared = [code for code in stored[day] if code in stored[other]]
        if not shared:
            return None
        scores.append(sum(
            stored[day][code]["margin_next_limit"] == stored[other][code]["margin_next_limit"]
            for code in shared
        ) / len(shared))
    return min(scores)


def settled_at(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time(3), TAIPEI)


def legacy_file(archive: Path, day: date, legacy_market: str) -> Path:
    return archive / f"{day:%Y}" / f"{day:%Y%m%d}" / f"{legacy_market}.csv"


def legacy_file_rows(path: Path, legacy_market: str) -> dict[str, dict]:
    columns = ARCHIVE_COLUMNS[legacy_market]
    rows: dict[str, dict] = {}
    in_table = legacy_market == "otc"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        if in_table:
            next(reader, None)
        for cells in reader:
            if not cells:
                continue
            if not in_table:
                # The TWSE file starts with the market summary block.
                in_table = cells[0].strip() == "代號"
                continue
            try:
                rows[cells[0].strip()] = {
                    name: int(cells[index].replace(",", "")) for name, index in columns.items()
                }
            except (IndexError, ValueError):
                continue
    return rows


def classify_value(
    archive: Path, day: date, legacy_market: str, code: str, theirs: dict,
    ours_day: dict, cache: dict,
) -> str:
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
    if not any(
        other != code and any(values.values()) and ours_day.get(other) == values
        for other, values in archived.items()
    ):
        return "value_differs"
    saved = datetime.fromtimestamp(path.stat().st_mtime, TAIPEI)
    if saved < settled_at(day):
        return "value_differs:legacy_captured_before_settlement"
    return "value_differs:source_changed_after_legacy_capture"


def compare(connection, legacy, archive: Path, source: str, start: date, end: date) -> dict:
    market, legacy_market = MARKETS[source]
    stored = stored_by_date(connection, source, start, end)
    issued = issued_by_date(connection, ISSUED_SOURCE[market], start, end)
    columns = ", ".join(LEGACY.values())
    legacy_by_date: dict[date, dict[str, dict]] = {}
    for row in legacy.execute(
        sa.text(
            f"""
            SELECT date, symbol, {columns}
              FROM margin_trading
             WHERE market = :market AND date BETWEEN :start AND :end
            """
        ),
        {"market": legacy_market, "start": start.isoformat(), "end": end.isoformat()},
    ).mappings():
        legacy_by_date.setdefault(date.fromisoformat(row["date"]), {})[row["symbol"]] = {
            name: None if row[column] is None else int(row[column])
            for name, column in LEGACY.items()
        }

    differences: Counter[str] = Counter()
    by_date: dict[str, Counter[str]] = {}
    examples: dict[str, list[dict]] = {}
    wrong_dates: dict[str, dict] = {}
    source_only: Counter[str] = Counter()
    ever_in_legacy = {code for rows in legacy_by_date.values() for code in rows}
    roll: Counter[str] = Counter()
    roll_examples: list[dict] = []
    lot_ratio: Counter[str] = Counter()
    lot_examples: list[dict] = []
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
            for name in rolls_forward(row):
                roll[name] += 1
                if len(roll_examples) < 10:
                    roll_examples.append({"date": day.isoformat(), "code": code, "balance": name})
            shares = issued.get(day, {}).get(code)
            if shares and row["margin_next_limit"]:
                lot = LOT_SHARES[source].get(code, 1000)
                ratio = row["margin_next_limit"] * lot / (shares * 0.25)
                if ratio <= 5:
                    lot_ratio["1.02_to_5" if ratio > 1.02 else "0.98_to_1.02"
                              if ratio >= 0.98 else "below_0.98"] += 1
                elif issued_moved(issued, code, day):
                    # The comparison's baseline moved, not the lot: the issued
                    # shares changed at least twofold within 150 trading dates
                    # (a capital change, or a one-day slip in the foreign-holding
                    # source), and the limit lags or leads it.
                    lot_ratio["above_5:issued_shares_moved"] += 1
                else:
                    lot_ratio["above_5:unexplained"] += 1
                    if len(lot_examples) < 10:
                        lot_examples.append({"date": day.isoformat(), "code": code,
                                             "ratio": round(ratio, 3)})
        present = [code for code in theirs_day if code in ours_day]
        if present and agreement(ours_day, theirs_day) < 0.5:
            match = matching_date(stored, theirs_day, day)
            continuity = neighbour_continuity(stored, day) if match is None else None
            if match is not None or (continuity is not None and continuity >= 0.9):
                kind = ("legacy_captured_another_date" if match is not None
                        else "legacy_file_matches_no_date_in_window")
                wrong_dates[day.isoformat()] = {
                    "legacy_rows": len(theirs_day),
                    "agreement_with_same_date": round(agreement(ours_day, theirs_day), 4),
                    "reproduces_stored_date": match.isoformat() if match else None,
                    "ours_continuous_with_neighbours": continuity,
                }
                differences[f"{kind}:legacy_rows"] += len(theirs_day)
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
                sample.update({name: {"ours": ours[name], "legacy": theirs[name]}
                               for name in differing})
                note(classify_value(archive, day, legacy_market, code, theirs, ours_day, cache),
                     day, sample)
                differing_fields.update(differing)
        for code in ours_day:
            if code not in theirs_day:
                source_only["security_absent_from_legacy_entirely" if code not in ever_in_legacy
                            else "absent_from_legacy_that_date"] += 1

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
        "legacy_file_dates": wrong_dates,
        "source_only_rows": dict(source_only),
        "differing_fields": dict(differing_fields),
        "roll_forward_failures": dict(roll),
        "roll_forward_examples": roll_examples,
        "lot_check_limit_over_quarter_of_issued": dict(lot_ratio),
        "lot_check_examples_above_5": lot_examples,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile-margin-trading")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", default=os.getenv("LEGACY_DATABASE_URL"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 2))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 11))
    parser.add_argument(
        "--legacy-archive", type=Path,
        default=Path.home() / "GitHubLL/my_stock_project/data/raw/margin_trading",
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
                        connection, legacy, args.legacy_archive, source, args.start, args.end
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
        and not entry["legacy_reconciliation"]["roll_forward_failures"]
        and not entry["legacy_reconciliation"]["lot_check_examples_above_5"]
        for entry in report["sources"].values()
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
