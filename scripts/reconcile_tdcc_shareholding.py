#!/usr/bin/env python
"""Reconcile imported TDCC weeks against legacy `stock_db.shareholding`.

Step 24-b. The comparison is done one week at a time, because both sides are
large — about 15 million stored distribution rows against legacy's 11.9 million
— and a week is the unit both sides agree on.

Three structural differences are known before a single value is compared, and
each is counted rather than treated as a discrepancy:

* **Legacy holds levels 1-15 only.** It has no 差異數調整 row and no 合計 row
  at all (`SELECT DISTINCT level` returns 1-15). So the row comparison is over
  levels 1-15, and levels 16 and 17 are reported as stored-only.
* **Legacy's universe is smaller, and shrinks again on 2023-09-15.** The
  legacy scraper filtered the OpenData file to its own active-stock list from
  that date (audit §4.9): 2023-09-08 holds 2,709 securities and 2023-09-15
  holds 1,767. Before that it was already smaller than the file — 2,485 on
  2020-01-03 against the file's 2,774.
* **Our universe is the v1 one.** TDCC reports custody for warrants,
  beneficiary certificates and other codes the exchange feeds never register,
  and Step 24-a writes a snapshot only for a security we already hold. A
  security legacy has and we do not is therefore expected where that security
  is outside 上市/上櫃, and is listed by code so the claim can be checked.

What is compared, for every `(week, security, level 1-15)` both sides hold:
holder count, shares, and the published percentage to two decimal places,
which is the scale the source publishes and the scale legacy stored as a
float.

Usage:

    python scripts/reconcile_tdcc_shareholding.py \\
        --database-url postgresql+psycopg://... \\
        --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from decimal import Decimal

import sqlalchemy as sa

from stock_data_center.coverage import CoverageValidator

SOURCE = "tdcc_opendata"
# The levels both sides hold. 16 and 17 are stored-only (module docstring).
COMPARED_LEVELS = tuple(str(level) for level in range(1, 16))
# The date the legacy scraper began filtering the file to its own universe.
LEGACY_FILTER_FROM = date(2023, 9, 15)
EXAMPLES = 20


def stored_weeks(connection: sa.Connection, start: date, end: date) -> list[date]:
    return list(
        connection.scalars(
            sa.text(
                "SELECT DISTINCT snapshot_date FROM tdcc_snapshot_versions "
                "WHERE source = :source AND snapshot_date BETWEEN :start AND :end "
                "ORDER BY 1"
            ),
            {"source": SOURCE, "start": start, "end": end},
        )
    )


def legacy_weeks(connection: sa.Connection, start: date, end: date) -> list[date]:
    rows = connection.execute(
        sa.text(
            "SELECT DISTINCT date FROM shareholding "
            "WHERE date BETWEEN :start AND :end ORDER BY 1"
        ),
        {"start": start.isoformat(), "end": end.isoformat()},
    ).scalars()
    return [date.fromisoformat(value) for value in rows]


def stored_week(connection: sa.Connection, week: date) -> dict:
    """Every stored level of one week, keyed on (security code, level)."""
    rows = connection.execute(
        sa.text(
            """
            SELECT s.security_code AS code, d.bucket_code AS level,
                   d.holder_count AS holders, d.shares AS shares,
                   d.ownership_percent AS percent
              FROM tdcc_distribution d
              JOIN tdcc_snapshot_versions v ON v.id = d.snapshot_version_id
              JOIN tdcc_snapshot_seals seal ON seal.snapshot_version_id = v.id
              JOIN security s ON s.id = v.security_id
             WHERE v.source = :source AND v.snapshot_date = :week
            """
        ),
        {"source": SOURCE, "week": week},
    ).mappings()
    return {(row["code"], row["level"]): row for row in rows}


def legacy_week(connection: sa.Connection, week: date) -> dict:
    rows = connection.execute(
        sa.text(
            "SELECT symbol, level, holders, shares, percentage "
            "FROM shareholding WHERE date = :week"
        ),
        {"week": week.isoformat()},
    ).mappings()
    return {(row["symbol"].strip(), str(row["level"])): row for row in rows}


def universe(keys) -> set[str]:
    return {code for code, _ in keys}


def compare_week(stored: dict, legacy: dict) -> dict:
    """One week's differences, each classified (CLAUDE.md §78)."""
    ours, theirs = universe(stored), universe(legacy)
    differences: list[dict] = []
    compared = 0
    for key, legacy_row in legacy.items():
        code, level = key
        if level not in COMPARED_LEVELS or code not in ours:
            continue
        row = stored.get(key)
        if row is None:
            differences.append(
                {
                    "security_code": code,
                    "level": level,
                    "class": "level_absent_from_stored_distribution",
                }
            )
            continue
        if any(
            legacy_row[column] is None
            for column in ("holders", "shares", "percentage")
        ):
            # Legacy holds no nulls today, and a null is not a value that can
            # disagree. Classified rather than raised: crashing at week 300 of
            # 376 would throw away everything already compared (§78).
            differences.append(
                {
                    "security_code": code,
                    "level": level,
                    "class": "legacy_value_is_null",
                }
            )
            continue
        compared += 1
        if (
            int(row["holders"]) == int(legacy_row["holders"])
            and Decimal(row["shares"]) == Decimal(legacy_row["shares"])
            and Decimal(row["percent"]).quantize(Decimal("0.01"))
            == Decimal(str(legacy_row["percentage"])).quantize(Decimal("0.01"))
        ):
            continue
        differences.append(
            {
                "security_code": code,
                "level": level,
                "class": "value_difference",
                "stored": {
                    "holders": int(row["holders"]),
                    "shares": str(row["shares"]),
                    "percent": str(row["percent"]),
                },
                "legacy": {
                    "holders": int(legacy_row["holders"]),
                    "shares": str(legacy_row["shares"]),
                    "percent": str(legacy_row["percentage"]),
                },
            }
        )
    # The loop above walks legacy's rows, so a level only we hold would go
    # unmentioned. It cannot happen — a stored distribution is complete for
    # its profile — and it is counted rather than assumed away.
    for code, level in stored:
        if (
            level in COMPARED_LEVELS
            and code in theirs
            and (code, level) not in legacy
        ):
            differences.append(
                {
                    "security_code": code,
                    "level": level,
                    "class": "level_absent_from_legacy",
                }
            )
    return {
        "stored_securities": len(ours),
        "legacy_securities": len(theirs),
        "compared_rows": compared,
        "legacy_only_securities": sorted(theirs - ours),
        "stored_only_securities": sorted(ours - theirs),
        "differences": differences,
    }


def truncated_weeks(connection: sa.Connection) -> set[date]:
    """Weeks whose source file was cut, as the import manifests recorded it.

    A truncated week is short of securities by definition, so legacy holding a
    security we do not is explained there and nowhere else (audit §4.9).
    """
    rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT (reconciliation ->> 'actual_snapshot_date') AS week
              FROM import_manifests
             WHERE dataset_code = 'tdcc_snapshot'
               AND reconciliation ->> 'truncated_payload' = 'true'
            """
        )
    ).scalars()
    return {date.fromisoformat(row) for row in rows if row}


def priced_securities(connection: sa.Connection, codes: list[str]) -> list[str]:
    """Which of these codes our own price history ever priced.

    The claim behind `legacy_only_securities` is that they are outside the v1
    universe the exchange feeds establish. A code we never priced supports it;
    one we did price would mean the TDCC import skipped a security it should
    have written, so the two are counted apart rather than asserted.
    """
    if not codes:
        return []
    return sorted(
        connection.scalars(
            sa.text(
                """
                SELECT DISTINCT s.security_code
                  FROM daily_price_versions v
                  JOIN security s ON s.id = v.security_id
                 WHERE s.security_code = ANY(:codes)
                """
            ),
            {"codes": codes},
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--legacy-database-url", required=True)
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-09-11")
    parser.add_argument("--output", help="write the manifest here as well")
    args = parser.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    engine = sa.create_engine(args.database_url)
    legacy_engine = sa.create_engine(args.legacy_database_url)
    weeks_reported: dict[str, dict] = {}
    difference_classes: Counter[str] = Counter()
    legacy_only: Counter[str] = Counter()
    legacy_only_in_truncated_weeks: Counter[str] = Counter()
    stored_only: Counter[str] = Counter()
    examples: list[dict] = []
    compared_rows = 0
    try:
        with engine.connect() as connection, legacy_engine.connect() as legacy:
            cut_weeks = truncated_weeks(connection)
            ours = stored_weeks(connection, start, end)
            theirs = legacy_weeks(legacy, start, end)
            shared = [week for week in ours if week in set(theirs)]
            for week in shared:
                result = compare_week(
                    stored_week(connection, week), legacy_week(legacy, week)
                )
                compared_rows += result["compared_rows"]
                for code in result["legacy_only_securities"]:
                    legacy_only[code] += 1
                    if week in cut_weeks:
                        legacy_only_in_truncated_weeks[code] += 1
                for code in result["stored_only_securities"]:
                    stored_only[code] += 1
                for difference in result["differences"]:
                    difference_classes[difference["class"]] += 1
                    if len(examples) < EXAMPLES:
                        examples.append({"week": week.isoformat(), **difference})
                weeks_reported[week.isoformat()] = {
                    "stored_securities": result["stored_securities"],
                    "legacy_securities": result["legacy_securities"],
                    "compared_rows": result["compared_rows"],
                    "differences": len(result["differences"]),
                }
    finally:
        engine.dispose()
        legacy_engine.dispose()

    # A security legacy holds and we do not is explained by one of two things,
    # and the two are separated rather than summed: the week's file was cut, or
    # the security is outside the priced v1 universe. Anything else would be a
    # real hole in the import.
    lost_to_truncation = {
        code
        for code, weeks in legacy_only_in_truncated_weeks.items()
        if weeks == legacy_only[code]
    }
    with engine.connect() as connection:
        priced = priced_securities(
            connection, sorted(set(legacy_only) - lost_to_truncation)
        )
        # The Step 16 report against the declared `trading_week` cadence: a
        # week the market never opened is a closure, not a gap (Step 24-b).
        coverage = CoverageValidator().report(
            connection,
            dataset_code="tdcc_snapshot",
            market="TW",
            start=start,
            end=end,
        )
    engine.dispose()

    manifest = {
        "source": SOURCE,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "weeks": {
            "stored": len(ours),
            "legacy": len(theirs),
            "compared": len(shared),
            "stored_only": [
                week.isoformat() for week in ours if week not in set(theirs)
            ],
            "legacy_only": [
                week.isoformat() for week in theirs if week not in set(ours)
            ],
        },
        "coverage": {
            "cadence": "trading_week",
            "expected_weeks": len(coverage.expected),
            "observed_weeks": len(coverage.observed),
            "missing_weeks": [week.isoformat() for week in coverage.missing],
            "unexpected_weeks": [week.isoformat() for week in coverage.unexpected],
            "closed_weeks": [week.isoformat() for week in coverage.non_trading_days],
            "is_complete": coverage.is_complete,
        },
        "compared_levels": list(COMPARED_LEVELS),
        "stored_only_levels": ["16", "17"],
        "compared_rows": compared_rows,
        "difference_classes": dict(difference_classes),
        "difference_examples": examples,
        "legacy_only_securities": {
            "count": len(legacy_only),
            "weeks_each": dict(legacy_only.most_common(EXAMPLES)),
            "lost_to_a_truncated_week": {
                "count": len(lost_to_truncation),
                "weeks": sorted(week.isoformat() for week in cut_weeks),
                "securities": sorted(lost_to_truncation)[:EXAMPLES],
            },
            # Zero is the claim for the rest: they are outside the priced v1
            # universe, which is why no snapshot was written for them.
            "also_priced_by_us": priced,
        },
        "stored_only_securities": {
            "count": len(stored_only),
            "weeks_each": dict(stored_only.most_common(EXAMPLES)),
        },
        "legacy_universe_filtered_from": LEGACY_FILTER_FROM.isoformat(),
        "weeks_detail": weeks_reported,
    }
    text = json.dumps(manifest, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    if not shared or not compared_rows:
        # Nothing was compared, which is not the same as nothing disagreeing:
        # a wrong legacy URL, an empty database or a window with no overlap
        # all look like this. Reported as a failure so a scripted run notices.
        print(
            "reconciled nothing: no week is held by both sides in this window",
            file=sys.stderr,
        )
        return 1
    return 0 if not difference_classes else 1


if __name__ == "__main__":
    raise SystemExit(main())
