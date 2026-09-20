#!/usr/bin/env python
"""Check the monthly-revenue publication evidence against the archive it came from.

Step 22-c's acceptance, measured on the whole window rather than on a fixture.
Every stored version is compared with the legacy `market.csv` row it was read
from, and every claim is checked against what that row is allowed to prove:

- **2020M01–2026M01.** A row whose recovered `publish_time` is not the
  statutory 10th carries `press_report_bound` at the end of that day, and
  resolves then. A row still on the 10th carries the release rule instead, at
  the end of the 10th or of the next trading day when the 10th is closed — so
  it never resolves before the rule.
- **2026M02 onward.** Every row carries `legacy_capture_bound` at the end of
  its first-seen day, which is never before the 22:45 run that saw it. Where
  the issuer corrected the row afterwards, the version that resolves is the
  one legacy captured, because the corrected value has nothing proving when it
  became public.
- **KY issuers.** The archive holds none, so they keep `unknown` and Market
  PIT sees nothing (owner decision, 2026-09-20).

The bulk checks run in SQL over every row. A sample is then resolved through
`MonthlyRevenueService` end to end, because the evidence instant is only the
answer if the resolver agrees with it.

Usage:

    python scripts/verify_monthly_revenue_evidence.py \
        --database-url postgresql+psycopg://... \
        --archive-root ~/GitHubLL/my_stock_project/data/raw/monthly_revenue
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.evidence.release_rules import ReleaseRuleService
from stock_data_center.ingestion.adapters.monthly_revenue_archive import (
    DEFAULT_ARCHIVE_ROOT,
    MARKETS,
)
from stock_data_center.ingestion.monthly_revenue_archive import (
    FIRST_CAPTURE_WINDOW_START,
    STATUTORY_RULE,
    end_of_day,
    statutory_day,
)
from stock_data_center.monthly_revenue import MonthlyRevenueService
from stock_data_center.monthly_revenue.models import RevenuePeriod
from stock_data_center.pit import MarketPITContext

SOURCES = tuple(MARKETS.values())
SAMPLE = 200


def periods(start: RevenuePeriod, end: RevenuePeriod) -> list[RevenuePeriod]:
    out, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(RevenuePeriod(year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def archive_dates(root: Path, months: list[RevenuePeriod]) -> dict:
    """(source, code, period) -> the day the archive dates that row."""
    dates: dict[tuple[str, str, tuple[int, int]], object] = {}
    for period in months:
        path = root / str(period.year) / f"{period.year}M{period.month:02d}" / "market.csv"
        for row in csv.DictReader(path.open(encoding="utf-8-sig")):
            source = MARKETS[row["market"].strip()]
            text = row["publish_time"].strip()
            # A calendar day, not an instant: the file dates rows to the day.
            dates[(source, row["symbol"].strip(), (period.year, period.month))] = (
                date(int(text[:4]), int(text[4:6]), int(text[6:8]))
            )
    return dates


def stored_evidence(connection, source: str) -> dict:
    """The highest-ranked evidence on each (code, period), and how many rows."""
    rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT ON (s.security_code, v.revenue_year, v.revenue_month)
                   s.security_code, v.revenue_year, v.revenue_month,
                   e.evidence_type, e.published_at, e.quality_rank,
                   e.evidence_source, v.revenue, v.id AS version_id
              FROM monthly_revenue_versions v
              JOIN security s ON s.id = v.security_id
              LEFT JOIN publication_evidence e
                ON e.monthly_revenue_version_id = v.id
             WHERE v.source = :source
             ORDER BY s.security_code, v.revenue_year, v.revenue_month,
                      e.quality_rank DESC, e.id
            """
        ),
        {"source": source},
    ).mappings()
    return {
        (row["security_code"], (row["revenue_year"], row["revenue_month"])): row
        for row in rows
    }


def check(connection, *, source: str, dates: dict, evidence: dict, rules) -> dict:
    counts: Counter[str] = Counter()
    failures: list[dict] = []
    # One instant per month, not per row: the rule asks the calendar, and the
    # rule-bound rows would ask it the same 80 questions thousands of times.
    instants: dict[tuple[int, int], datetime] = {}

    def fail(kind: str, key, detail: str) -> None:
        counts[kind] += 1
        if len(failures) < 20:
            failures.append(
                {
                    "check": kind,
                    "security_code": key[0],
                    "period": f"{key[1][0]:04d}-{key[1][1]:02d}",
                    "detail": detail,
                }
            )

    for (row_source, code, period), captured_on in dates.items():
        if row_source != source:
            continue
        key = (code, period)
        row = evidence.get(key)
        if row is None:
            # Step 22-b: the source dropped issuers that left the market, and
            # the archive still holds them. Nothing to carry evidence.
            counts["archive_row_without_a_version"] += 1
            continue
        revenue_period = RevenuePeriod(*period)
        first_capture = revenue_period >= FIRST_CAPTURE_WINDOW_START
        rule_day = statutory_day(revenue_period)
        expected_bound = end_of_day(captured_on)
        if first_capture:
            counts["first_capture_rows"] += 1
            if row["evidence_type"] != "legacy_capture_bound":
                fail("first_capture_type", key, str(row["evidence_type"]))
            elif row["published_at"] != expected_bound:
                fail("first_capture_instant", key, str(row["published_at"]))
            elif row["published_at"] < _run_start(captured_on):
                fail("earlier_than_the_2245_run", key, str(row["published_at"]))
        elif captured_on == rule_day:
            counts["rule_bound_rows"] += 1
            if period not in instants:
                instants[period] = rules.instant_for(
                    connection,
                    rule_id=STATUTORY_RULE[0],
                    version=STATUTORY_RULE[1],
                    period=_month_start(period),
                )
            instant = instants[period]
            if row["evidence_type"] != "release_rule":
                fail("rule_bound_type", key, str(row["evidence_type"]))
            elif row["published_at"] != instant:
                fail("rule_bound_instant", key, str(row["published_at"]))
            elif row["published_at"] < end_of_day(rule_day):
                fail("earlier_than_the_rule", key, str(row["published_at"]))
        else:
            counts["press_report_rows"] += 1
            if row["evidence_type"] != "press_report_bound":
                fail("press_report_type", key, str(row["evidence_type"]))
            elif row["published_at"] != expected_bound:
                fail("press_report_instant", key, str(row["published_at"]))
    return {"counts": dict(counts), "failures": failures}


def _month_start(period: tuple[int, int]):
    return date(period[0], period[1], 1)


def _run_start(day):
    """The legacy monthly job starts at 22:45 Asia/Taipei (audit §7.1)."""
    from datetime import datetime as dt
    from datetime import time
    from zoneinfo import ZoneInfo

    return dt.combine(day, time(22, 45), tzinfo=ZoneInfo("Asia/Taipei"))


def resolve_sample(connection, *, source: str, evidence: dict, dates: dict,
                   seed: int) -> dict:
    """Resolve a sample end to end: the evidence instant must be the answer."""
    service = MonthlyRevenueService()
    archived = _swapped(dates)
    keys = [key for key in sorted(evidence) if (source, *key) in archived]
    random.Random(seed).shuffle(keys)
    checked = 0
    failures: list[dict] = []
    now = datetime.now(UTC) + timedelta(minutes=1)
    for code, period in keys[:SAMPLE]:
        row = evidence[(code, period)]
        instant = row["published_at"]
        if instant is None:
            continue
        revenue_period = RevenuePeriod(*period)
        at = service.revenue(
            connection, security_code=code, period=revenue_period,
            context=MarketPITContext(information_as_of=instant, knowledge_as_of=now),
            source=source,
        )
        before = service.revenue(
            connection, security_code=code, period=revenue_period,
            context=MarketPITContext(
                information_as_of=instant - timedelta(seconds=1),
                knowledge_as_of=now,
            ),
            source=source,
        )
        checked += 1
        if at is None or before is not None:
            failures.append(
                {
                    "security_code": code,
                    "period": f"{period[0]:04d}-{period[1]:02d}",
                    "visible_at_the_instant": at is not None,
                    "visible_one_second_earlier": before is not None,
                }
            )
    return {"resolved": checked, "failures": failures}


def _swapped(dates: dict) -> set:
    return {(source, code, period) for source, code, period in dates}


def ky_issuers(connection, source: str, dates: dict) -> dict:
    """Issuers the archive never held: they must still resolve to nothing."""
    archived = {code for row_source, code, _ in dates if row_source == source}
    rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT s.security_code
              FROM monthly_revenue_versions v
              JOIN security s ON s.id = v.security_id
             WHERE v.source = :source
            """
        ),
        {"source": source},
    ).scalars().all()
    missing = sorted(set(rows) - archived)
    claimed = connection.execute(
        sa.text(
            """
            SELECT count(*)
              FROM publication_evidence e
              JOIN monthly_revenue_versions v
                ON v.id = e.monthly_revenue_version_id
              JOIN security s ON s.id = v.security_id
             WHERE v.source = :source AND s.security_code = ANY(:codes)
               AND e.evidence_type <> 'official'
            """
        ),
        {"source": source, "codes": missing},
    ).scalar() if missing else 0
    return {
        "issuers_not_in_the_archive": len(missing),
        "sample": missing[:10],
        "evidence_claimed_for_them": claimed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify-monthly-revenue-evidence")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--start", default="2020-01")
    parser.add_argument("--end", default="2026-08")
    parser.add_argument("--seed", type=int, default=22)
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url is required")
    months = periods(
        RevenuePeriod(*(int(part) for part in args.start.split("-"))),
        RevenuePeriod(*(int(part) for part in args.end.split("-"))),
    )
    dates = archive_dates(args.archive_root, months)

    engine = sa.create_engine(args.database_url)
    rules = ReleaseRuleService()
    report: dict = {
        "window": {"start": args.start, "end": args.end, "months": len(months)},
        "archive_rows": len(dates),
        "sources": {},
    }
    try:
        with engine.connect() as connection:
            for source in SOURCES:
                evidence = stored_evidence(connection, source)
                report["sources"][source] = {
                    "stored_rows": len(evidence),
                    "claims": check(
                        connection, source=source, dates=dates,
                        evidence=evidence, rules=rules,
                    ),
                    "resolution_sample": resolve_sample(
                        connection, source=source, evidence=evidence,
                        dates=dates, seed=args.seed,
                    ),
                    "issuers_the_archive_never_held": ky_issuers(
                        connection, source, dates
                    ),
                }
    finally:
        engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    clean = all(
        not entry["claims"]["failures"]
        and not entry["resolution_sample"]["failures"]
        and entry["issuers_the_archive_never_held"]["evidence_claimed_for_them"] == 0
        for entry in report["sources"].values()
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
