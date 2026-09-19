#!/usr/bin/env python
"""Reconcile imported securities lending against legacy `stock_db`.

Step 21-b, the shape of `reconcile_margin_trading.py`: import counts from the
per-date manifests, the Step 16 coverage report, and the row-level comparison
with every difference classified (CLAUDE.md §78).

Legacy `margin_sbl` is one row per security and date, in shares, as floats,
built from the same two tables. Its columns compare as:

- `sbl_prev_balance`, `sbl_sell`, `sbl_repay`, `sbl_balance` with the stored
  `securities_lending` previous balance, borrowed, returned and balance;
- `margin_short_prev_balance`, `margin_short_sell`, `margin_short_buy`,
  `margin_short_balance` — the 融券 group, which this step does not store —
  with Step 21-a's `margin_trading` short side, which is where v1 keeps it.

Every class is proven before it is granted, as in 21-a: a value difference is
explained from the legacy archive file it came from (`--legacy-archive`,
`<yyyy>/<yyyymmdd>/<sii|otc>.csv`) only when the legacy row equals its own file
and another row of that file agrees with ours.

Checks that need no legacy, run on every stored row or file:

- **The balance rolls forward**, by the formula both exchanges print:
  前日餘額 + 當日賣出 − 當日還券 + 當日調整 = 當日餘額. The one exception is
  proven, not assumed: on a 停止買賣 day (note `!`) TWSE clears the balance to
  zero with no flow, and the chain holds only if the next stored date starts
  from zero (`cleared_on_suspension`).
- **The file is in shares and its 融券 group is `margin_trading`.** Each date's
  raw artifact is re-read: its 融券 flows and balances must equal the stored
  `margin_trading` short side exactly, and its limit, rounded down to the
  security's lot (1,000, or 21-a's `TWSE_LOT_SHARES`), must equal the stored
  short limit. This is the unit proof for TPEx, whose JSON states no unit, and
  a second one for TWSE. A security the margin table does not list is counted,
  not failed: margin trading lists only securities eligible for it — as long
  as its 融券 group in the file is all zero, which is what it then publishes.

  A non-zero group for an unlisted security is explained only when the other
  market's margin table lists the same values that day: on the evening before a
  security moves between the markets, both exchanges' lending tables list it
  (TWSE's note: 第二次更新時將納入原為上櫃次日將轉為上市交易之個股).
  `KNOWN_LIMIT_DIFFERENCES` names the dates where the two tables' limits
  disagree with the flows equal.

Legacy's short columns for such a security are the file's zeros, where v1 has
no margin row; that difference is explained only when the file shows the same
zeros (`short_side_absent_from_margin_table:file_all_zero`), or when the other
market's margin table holds legacy's values (`...:other_market_margin_table`).
A legacy date whose rows disagree wholesale is blamed on legacy's file only
when another stored date reproduces them, as in 21-a.

Usage:

    python scripts/reconcile_securities_lending.py \
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

DATASET = "securities_lending"
# Lending source -> (market, legacy market, the 21-a margin source).
MARKETS = {
    "twse_twt93u": ("TWSE", "sii", "twse_mi_margn"),
    "tpex_margin_sbl": ("TPEx", "otc", "tpex_margin_balance"),
}
LOT_SHARES = {
    "twse_twt93u": {code: shares for code, (shares, _, _) in TWSE_LOT_SHARES.items()},
    "tpex_margin_sbl": {},
}
LENDING = (
    "previous_balance", "borrowed", "returned", "adjustment", "balance",
    "next_limit", "next_available_limit",
)
LENDING_VALUES = LENDING + ("note",)
SHORT = (
    "short_previous_balance", "short_sell", "short_buy", "short_stock_repayment",
    "short_balance", "short_next_limit",
)
# The 融券 group's position in the published row, in SHORT's order (both markets).
SHORT_COLUMNS = dict(zip(SHORT, (2, 3, 4, 5, 6, 7), strict=True))
LEGACY = {
    "previous_balance": "sbl_prev_balance",
    "borrowed": "sbl_sell",
    "returned": "sbl_repay",
    "balance": "sbl_balance",
    "short_previous_balance": "margin_short_prev_balance",
    "short_sell": "margin_short_sell",
    "short_buy": "margin_short_buy",
    "short_balance": "margin_short_balance",
}
FIELDS = tuple(LEGACY)
LEGACY_LENDING = ("previous_balance", "borrowed", "returned", "balance")
# Archive CSV column of each compared value; the same in both legacy markets.
ARCHIVE_COLUMNS = {
    "previous_balance": 8, "borrowed": 9, "returned": 10, "balance": 12,
    "short_previous_balance": 2, "short_sell": 3, "short_buy": 4, "short_balance": 6,
}
TAIPEI = ZoneInfo("Asia/Taipei")
# (lending source, code, date) where the lending table's 融券 limit, rounded to
# lots, is not the margin table's, with every flow and balance equal.
KNOWN_LIMIT_DIFFERENCES = {
    # 國慶科技: MI_MARGN moved to its new limit (42,456 lots) a day before
    # TWT93U did (47,248,750 shares, then 42,456,680 from 2021-02-17).
    ("twse_twt93u", "1721", date(2021, 2, 5)),
}
OTHER_MARGIN = {"twse_twt93u": "tpex_margin_balance", "tpex_margin_sbl": "twse_mi_margn"}
# The difference classes a proof stands behind.
EXPLAINED = frozenset({
    "legacy_captured_another_date:legacy_rows",
    "short_side_absent_from_margin_table:file_all_zero",
    "short_side_absent_from_margin_table:other_market_margin_table",
    "value_differs:legacy_row_incomplete",
    "value_differs:legacy_captured_before_settlement",
    "value_differs:source_changed_after_legacy_capture",
})


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


def latest(connection, table: str, columns: tuple[str, ...], source: str,
           start: date, end: date) -> dict[date, dict[str, dict]]:
    """Latest revision per (security, date), chosen deterministically."""
    rows = connection.execute(
        sa.text(
            f"""
            SELECT DISTINCT ON (v.security_id, v.trade_date)
                   s.security_code, v.trade_date,
                   {", ".join(f"v.{name}" for name in columns)}
              FROM {table} v
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
            name: row[name] if row[name] is None or isinstance(row[name], str)
            else int(row[name])
            for name in columns
        }
    return grouped


def latest_raw(connection, source: str, start: date, end: date) -> dict[date, Path]:
    """The most recently stored raw artifact each date's rows were observed in."""
    rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT ON (v.trade_date) v.trade_date, r.storage_uri
              FROM securities_lending_versions v
              JOIN securities_lending_version_observations o
                ON o.securities_lending_version_id = v.id
              JOIN raw_artifacts r ON r.id = o.raw_artifact_id
             WHERE v.source = :source AND v.trade_date BETWEEN :start AND :end
             ORDER BY v.trade_date, r.stored_at DESC, r.id
            """
        ),
        {"source": source, "start": start, "end": end},
    ).all()
    return {day: Path(uri) for day, uri in rows}


def published_short(path: Path) -> dict[str, dict[str, int]]:
    """Each security's 融券 group, as the raw file publishes it (the 合計 row,
    which has no code, left out)."""
    payload = json.loads(path.read_bytes())
    data = payload["data"] if "data" in payload else payload["tables"][0]["data"]
    return {
        row[0].strip(): {
            name: int(row[index].replace(",", "")) for name, index in SHORT_COLUMNS.items()
        }
        for row in data
        if row[0].strip()
    }


def other_market_short(connection, source: str, code: str, day: date) -> dict | None:
    """The other market's latest margin_trading short side for one security-date."""
    row = connection.execute(
        sa.text(
            f"""
            SELECT {", ".join(f"v.{name}" for name in SHORT)}
              FROM margin_trading_versions v
              JOIN security s ON s.id = v.security_id
             WHERE v.source = :source AND s.security_code = :code AND v.trade_date = :day
             ORDER BY v.ingested_at DESC, v.id DESC
             LIMIT 1
            """
        ),
        {"source": OTHER_MARGIN[source], "code": code, "day": day},
    ).mappings().first()
    return None if row is None else {name: int(row[name]) for name in SHORT}


def check_short_group(
    connection, source: str, stored_short: dict, files: dict[date, dict], lots: dict
) -> dict:
    """The raw file's 融券 group against the stored margin_trading short side."""
    result: Counter[str] = Counter()
    examples: list[dict] = []
    moved: list[dict] = []
    for day, file_rows in sorted(files.items()):
        margin_day = stored_short.get(day, {})
        for code, published in file_rows.items():
            ours = margin_day.get(code)
            if ours is None:
                flows = {name: published[name] for name in SHORT if name != "short_next_limit"}
                if not any(flows.values()):
                    result["not_in_margin_table:file_zero"] += 1
                    continue
                other = other_market_short(connection, source, code, day)
                if other is not None and all(other[name] == value for name, value in flows.items()):
                    # Listed by both exchanges the evening before it moves market.
                    result["not_in_margin_table:other_market_margin_table"] += 1
                    moved.append({"date": day.isoformat(), "code": code})
                else:
                    result["not_in_margin_table:file_nonzero"] += 1
                    if len(examples) < 10:
                        examples.append({"date": day.isoformat(), "code": code, **published})
                continue
            lot = lots.get(code, 1000)
            expected = dict(published, short_next_limit=published["short_next_limit"] // lot * lot)
            differing = [name for name in SHORT if ours[name] != expected[name]]
            if differing == ["short_next_limit"] and (source, code, day) in KNOWN_LIMIT_DIFFERENCES:
                result["limit_differs:known"] += 1
            elif differing:
                result["differs"] += 1
                if len(examples) < 10:
                    examples.append({
                        "date": day.isoformat(), "code": code,
                        **{name: {"file": expected[name], "margin_trading": ours[name]}
                           for name in differing},
                    })
            else:
                result["equal"] += 1
        # A security's last margin date, which the lending table no longer lists.
        result["margin_rows_not_in_file"] += sum(code not in file_rows for code in margin_day)
    return {"rows": dict(result), "examples": examples, "moved_market": moved}


def rolls_forward(row: dict) -> bool:
    return (row["previous_balance"] + row["borrowed"] - row["returned"]
            + row["adjustment"] == row["balance"])


def cleared_on_suspension(lending: dict, day: date, code: str) -> bool:
    """停止買賣 (note `!`), the balance set to zero with no flow, and the next
    stored date for the security starting from zero."""
    row = lending[day][code]
    if not ("!" in (row["note"] or "") and row["balance"] == 0
            and row["borrowed"] == row["returned"] == row["adjustment"] == 0):
        return False
    later = next((other for other in sorted(lending) if other > day and code in lending[other]),
                 None)
    return later is None or lending[later][code]["previous_balance"] == 0


def agreement(ours_day: dict, theirs_day: dict) -> float:
    """The share of legacy's rows whose lending values equal ours."""
    present = [code for code in theirs_day if code in ours_day]
    if not present:
        return 0.0
    return sum(
        all(ours_day[code][name] == theirs_day[code][name] for name in LEGACY_LENDING)
        for code in present
    ) / len(present)


def matching_date(lending: dict, theirs_day: dict, exclude: date) -> date | None:
    """Another stored date whose lending rows equal all of legacy's."""
    for other_day, rows in lending.items():
        if other_day == exclude:
            continue
        if sum(code in rows for code in theirs_day) < 0.9 * len(theirs_day):
            continue
        if agreement(rows, theirs_day) == 1.0:
            return other_day
    return None


def settled_at(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time(3), TAIPEI)


def legacy_file(archive: Path, day: date, legacy_market: str) -> Path:
    return archive / f"{day:%Y}" / f"{day:%Y%m%d}" / f"{legacy_market}.csv"


def legacy_file_rows(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for cells in csv.reader(handle):
            try:
                rows[cells[0].strip()] = {
                    name: int(cells[index].replace(",", ""))
                    for name, index in ARCHIVE_COLUMNS.items()
                }
            except (IndexError, ValueError):
                continue  # the header rows and the 合計 row
    return rows


def classify_value(
    archive: Path, day: date, legacy_market: str, code: str, theirs: dict,
    ours_day: dict, cache: dict,
) -> str:
    differing = [name for name in FIELDS if ours_day[code][name] != theirs[name]]
    if differing and all(theirs[name] is None for name in differing):
        return "value_differs:legacy_row_incomplete"
    if any(value is None for value in theirs.values()):
        return "value_differs"
    path = legacy_file(archive, day, legacy_market)
    if not path.exists():
        return "value_differs:legacy_file_missing"
    if path not in cache:
        cache[path] = legacy_file_rows(path)
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
    market, legacy_market, margin_source = MARKETS[source]
    lending = latest(connection, "securities_lending_versions", LENDING_VALUES, source, start, end)
    short = latest(connection, "margin_trading_versions", SHORT, margin_source, start, end)
    files = {
        day: published_short(path)
        for day, path in latest_raw(connection, source, start, end).items()
    }
    columns = ", ".join(LEGACY.values())
    legacy_by_date: dict[date, dict[str, dict]] = {}
    for row in legacy.execute(
        sa.text(
            f"""
            SELECT date, symbol, {columns}
              FROM margin_sbl
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
    source_only: Counter[str] = Counter()
    ever_in_legacy = {code for rows in legacy_by_date.values() for code in rows}
    roll: Counter[str] = Counter()
    roll_examples: list[dict] = []
    wrong_dates: dict[str, dict] = {}
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

    for day in sorted(set(lending) | set(legacy_by_date)):
        lending_day = lending.get(day, {})
        short_day = short.get(day, {})
        # Ours, in legacy's shape: the lending row, plus the short side where
        # margin trading lists the security.
        ours_day = {
            code: {
                **{name: row[name] for name in FIELDS if name in LENDING},
                **{name: (short_day[code][name] if code in short_day else None)
                   for name in FIELDS if name in SHORT},
            }
            for code, row in lending_day.items()
        }
        theirs_day = legacy_by_date.get(day, {})
        legacy_total += len(theirs_day)
        for code, row in lending_day.items():
            if rolls_forward(row):
                continue
            if cleared_on_suspension(lending, day, code):
                roll["cleared_on_suspension"] += 1
                continue
            roll["unexplained"] += 1
            if len(roll_examples) < 10:
                roll_examples.append({"date": day.isoformat(), "code": code, **row})
        present = [code for code in theirs_day if code in ours_day]
        if present and agreement(ours_day, theirs_day) < 0.5:
            match = matching_date(lending, theirs_day, day)
            if match is not None:
                wrong_dates[day.isoformat()] = {
                    "legacy_rows": len(theirs_day),
                    "agreement_with_same_date": round(agreement(ours_day, theirs_day), 4),
                    "reproduces_stored_date": match.isoformat(),
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
                sample.update({name: {"ours": ours[name], "legacy": theirs[name]}
                               for name in differing})
                published = files.get(day, {}).get(code)
                short_only = code not in short_day and all(name in SHORT for name in differing)
                other = (other_market_short(connection, source, code, day)
                         if short_only and published is not None else None)
                if (
                    short_only
                    and published is not None
                    and all(theirs[name] == published[name] == 0 for name in differing)
                ):
                    # v1 keeps the short side in margin_trading, which does not
                    # list this security; the file shows the zeros legacy copied.
                    kind = "short_side_absent_from_margin_table:file_all_zero"
                elif other is not None and all(
                    theirs[name] == published[name] == other[name] for name in differing
                ):
                    # The evening before it moves market: the other exchange's
                    # margin table holds the values legacy copied.
                    kind = "short_side_absent_from_margin_table:other_market_margin_table"
                else:
                    kind = classify_value(
                        archive, day, legacy_market, code, theirs, ours_day, cache
                    )
                note(kind, day, sample)
                differing_fields.update(differing)
        for code in ours_day:
            if code not in theirs_day:
                source_only["security_absent_from_legacy_entirely" if code not in ever_in_legacy
                            else "absent_from_legacy_that_date"] += 1

    return {
        "market": market,
        "stored_rows": sum(len(rows) for rows in lending.values()),
        "legacy_rows": legacy_total,
        "compared_rows": compared,
        "differences": dict(differences),
        "difference_dates": {
            kind: dict(sorted(counter.items())) if len(counter) <= 20
            else {"date_count": len(counter), "top": dict(counter.most_common(10))}
            for kind, counter in by_date.items()
        },
        "difference_examples": examples,
        "source_only_rows": dict(source_only),
        "differing_fields": dict(differing_fields),
        "legacy_file_dates": wrong_dates,
        "roll_forward": dict(roll),
        "roll_forward_examples": roll_examples,
        "short_group_against_margin_trading": check_short_group(
            connection, source, short, files, LOT_SHARES[source]
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile-securities-lending")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", default=os.getenv("LEGACY_DATABASE_URL"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 2))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 11))
    parser.add_argument(
        "--legacy-archive", type=Path,
        default=Path.home() / "GitHubLL/my_stock_project/data/raw/margin_sbl",
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
            for source, (market, _, _) in MARKETS.items():
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
    # Only classes that were proven pass; anything else fails the run.
    clean = all(
        entry["coverage"]["is_complete"]
        and set(entry["legacy_reconciliation"]["differences"]) <= EXPLAINED
        and not entry["legacy_reconciliation"]["roll_forward"].get("unexplained")
        and not {"differs", "not_in_margin_table:file_nonzero"}
        & set(entry["legacy_reconciliation"]["short_group_against_margin_trading"]["rows"])
        for entry in report["sources"].values()
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
