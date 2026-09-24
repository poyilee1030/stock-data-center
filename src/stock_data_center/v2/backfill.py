"""Backfill the v2 exchange-daily tables over the stored trading days.

    DATABASE_URL=postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \\
        .venv/bin/python -m stock_data_center.v2.backfill \\
        --job daily_prices/twse_mi_index --start 2026-09-01 --end 2026-09-11

Dates come from `trading_days`, so a closure is never requested; a monthly job
(TAIEX OHLC, monthly revenue) asks once per month that has a trading day in the
range. TDCC OpenData serves only its latest week, so its job fetches once. Each
resource commits on its own, so an interrupted run loses at most the resource
in flight, and a rerun skips every resource `pending` counts as done.
`--refetch` fetches them again anyway (a correction check): an unchanged file
then logs its fetch and appends nothing.

A job with a check runs after the jobs it checks against, so a TAIEX month is
compared with MI_INDEX closes written in the same run.

Requests to one host are spaced by `HostRateGovernor`: 3 s for MOPS
(Step 20-c), 1.5 s for TWSE and TPEx, the interval every v1 backfill used.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

from stock_data_center.db.schema_v2 import stocks, trading_days
from stock_data_center.ingestion.http import (
    MOPS_HOST,
    MOPS_MIN_INTERVAL_SECONDS,
    HostRateGovernor,
    HttpSourceFetcher,
    RetryingFetcher,
    SourceFetcher,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import (
    corporate_actions,
    financial_reports,
    monthly_revenue,
    shareholding,
)
from stock_data_center.v2.exchange_daily import JOBS as EXCHANGE_JOBS
from stock_data_center.v2.exchange_daily import Job, ingest, pending

JOBS: dict[str, Job] = {**EXCHANGE_JOBS, **monthly_revenue.JOBS, **shareholding.JOBS}
# Financial reports are one document per filer and quarter, written as a whole
# version, so they have their own runner rather than a `Job`.
# Corporate actions are a year's list plus per-event detail pages, with
# retractions; they have their own runner too.
CORPORATE_KEYS = {corporate_actions.key(source): source for source in corporate_actions.FEEDS}
ALL_KEYS = (*sorted(JOBS), financial_reports.KEY, *sorted(CORPORATE_KEYS))

EXCHANGE_MIN_INTERVAL_SECONDS = 1.5
HOST_INTERVALS = {
    MOPS_HOST: MOPS_MIN_INTERVAL_SECONDS,
    "www.twse.com.tw": EXCHANGE_MIN_INTERVAL_SECONDS,
    "www.tpex.org.tw": EXCHANGE_MIN_INTERVAL_SECONDS,
}


def periods(connection: Connection, job: Job, start: date, end: date) -> list[date]:
    if job.latest_only:
        return [end]  # the source serves one file, whatever the range
    days = connection.scalars(
        sa.select(trading_days.c.trade_date)
        .where(trading_days.c.trade_date.between(start, end))
        .order_by(trading_days.c.trade_date)
    ).all()
    if job.monthly:
        return sorted({day.replace(day=1) for day in days})
    return list(days)


@contextmanager
def _unit(bind: Connection | Engine) -> Iterator[Connection]:
    """One resource's transaction: its own commit, or a savepoint in a test."""
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            yield connection
    else:
        with bind.begin_nested():
            yield bind


def run(
    bind: Connection | Engine,
    jobs: Sequence[Job],
    start: date,
    end: date,
    *,
    fetcher: SourceFetcher,
    git_commit: str,
    purpose: str,
    store: LocalRawArtifactStore | None = None,
    refetch: bool = False,
    progress=None,
) -> dict[str, dict[str, int]]:
    report: dict[str, dict[str, int]] = {}
    with _unit(bind) as connection:
        stock_ids = frozenset(connection.scalars(sa.select(stocks.c.stock_id)))
    for job in sorted(jobs, key=lambda job: job.check is not None):
        with _unit(bind) as connection:
            wanted = periods(connection, job, start, end)
            todo = wanted if refetch or job.latest_only else pending(connection, job, wanted)
        counts = Counter(periods=len(wanted), skipped=len(wanted) - len(todo),
                         appended=0, unchanged=0, out_of_scope=0, unverified=0)
        for period in todo:
            with _unit(bind) as connection:
                outcome = ingest(
                    connection, job, period, fetcher=fetcher, git_commit=git_commit,
                    purpose=purpose, store=store, stock_ids=stock_ids,
                )
            counts[outcome.status] += 1
            counts["appended"] += outcome.appended
            counts["unchanged"] += outcome.unchanged
            counts["out_of_scope"] += outcome.out_of_scope
            counts["unverified"] += outcome.unverified
            if progress:
                progress(job.key, period, outcome)
        report[job.key] = dict(counts)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--job", action="append", required=True,
                        help=f"repeatable; 'all' or one of {list(ALL_KEYS)}")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--purpose", default="gap_fill",
                        choices=("first_capture", "gap_fill", "correction_check", "unspecified"))
    parser.add_argument("--refetch", action="store_true")
    parser.add_argument("--raw-root", default="data/raw")
    args = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    keys = list(ALL_KEYS) if "all" in args.job else args.job
    unknown = [key for key in keys if key not in ALL_KEYS]
    if unknown:
        raise SystemExit(f"unknown job(s): {unknown}")

    from stock_data_center.ingestion.lifecycle import current_git_commit

    def progress(key, period, outcome) -> None:
        print(f"{key} {period.isoformat()} {outcome.status} +{outcome.appended}"
              f"{' ' + outcome.reason_code if outcome.reason_code else ''}",
              file=sys.stderr, flush=True)

    engine = sa.create_engine(url)
    fetcher = RetryingFetcher(HttpSourceFetcher(governor=HostRateGovernor(HOST_INTERVALS)))
    common = {
        "fetcher": fetcher, "git_commit": current_git_commit(), "purpose": args.purpose,
        "store": LocalRawArtifactStore(args.raw_root), "refetch": args.refetch,
        "progress": progress,
    }
    report = run(engine, [JOBS[key] for key in keys if key in JOBS], args.start, args.end,
                 **common)
    if sources := [CORPORATE_KEYS[key] for key in keys if key in CORPORATE_KEYS]:
        report.update(corporate_actions.run(engine, sources, args.start, args.end,
                                            unit=_unit, **common))
    if financial_reports.KEY in keys:
        report[financial_reports.KEY] = financial_reports.run(
            engine, args.start, args.end, unit=_unit, **common)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # A failed fetch or an empty trading day is left for a rerun; say so. An
    # empty financial quarter is a report not filed yet, which is normal.
    return 1 if any(
        counts.get("failed") or (counts.get("empty") and key != financial_reports.KEY)
        for key, counts in report.items()
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
