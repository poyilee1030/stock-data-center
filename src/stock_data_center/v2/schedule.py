"""What the forward capture should fetch now, and why (ROADMAP Step 28-a).

    DATABASE_URL=postgresql+psycopg://stockdc:stockdc@localhost:26519/stockdc_backfill \\
        .venv/bin/python -m stock_data_center.v2.schedule [--now 2026-09-28T20:00+08:00]

Each job has a declaration: where its periods come from, when a period can
first be fetched, when it settles, when a still-missing period is overdue,
when a settled one is checked once more for corrections. These instants are
operational, not publication times: when the market could know a value is
still the release rule's, computed on read.

The decision for one period is a pure function of its declaration, its
`fetches` rows and the current instant (`decide`), so the whole pending set is
listed before anything is fetched (`plan`). Nothing else keeps state: the
first time a period went missing, the attempts since and the last reason code
are all in `fetches`.

    not_due    before the earliest fetch time
    missing    no complete fetch yet
    given_up   missing for longer than the declaration's give-up time
    fresh      fetched, not settled, and not due for a refresh
    refresh    fetched, not settled, and the copy is older than `refresh`
    unsettled  fetched only before the period settled, and it has settled
    recheck    settled and fetched, and its correction check is due
    done

A period that needs a fetch is asked at most once per `retry` during its first
`retry_for` missing, then once per `backoff`, and not at all after `give_up`:
a wrongly declared window cannot turn the hourly loop against a source. The
purpose follows CLAUDE.md §32: a fetch is `first_capture` until a day after its
period settles and `gap_fill` after that; a correction check is
`correction_check`. Only monthly revenue and financial reports store what a
first capture proves (`published_at`); for every other dataset the purpose is
the audit trail.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import cached_property
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db import schema_v2 as v2
from stock_data_center.v2 import corporate_actions, financial_reports, trading_calendar
from stock_data_center.v2.backfill import JOBS
from stock_data_center.v2.exchange_daily import Job
from stock_data_center.v2.listings import WINDOW_START, listed_stock_ids

TAIPEI = ZoneInfo("Asia/Taipei")
HOUR, DAY = timedelta(hours=1), timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Attempt:
    fetched_at: datetime
    status: str
    reason_code: str | None
    purpose: str


@dataclass(frozen=True, slots=True)
class Decision:
    state: str
    dispatch: bool
    purpose: str | None = None
    overdue: bool = False
    first_missing: datetime | None = None
    last_reason: str | None = None


def _succeeded(attempt: Attempt) -> bool:
    # A succeeded fetch that held rows back is not done (exchange_daily._UNFINISHED).
    return attempt.status == "succeeded" and attempt.reason_code != "close_unverified"


@dataclass(frozen=True)
class Declaration:
    key: str
    dataset: str
    source: str
    periods: Callable[[Context], Iterable]
    resources: Callable[[object], tuple[str, ...]]
    earliest: Callable[[object], datetime]
    settled: Callable[[object], datetime] | None = None
    escalate: Callable[[object], datetime] | None = None
    recheck: Callable[[object], datetime] | None = None
    capture_until: Callable[[object], datetime] | None = None
    refresh: timedelta | None = None
    stale_after: timedelta | None = None
    retry: timedelta = HOUR
    retry_for: timedelta = DAY
    backoff: timedelta = DAY
    give_up: timedelta = timedelta(days=14)
    complete: Callable[[Attempt], bool] = _succeeded
    # Attempts the fetch log alone does not show, by period (stored versions).
    stored: Callable[[Context], dict[object, list[Attempt]]] | None = None


def decide(d: Declaration, period, attempts: Sequence[Attempt], now: datetime) -> Decision:
    attempts = sorted(attempts, key=lambda a: a.fetched_at)
    last = attempts[-1] if attempts else None
    last_reason = last.reason_code if last else None
    complete = [a.fetched_at for a in attempts if d.complete(a)]
    last_ok = complete[-1] if complete else None
    since_ok = [a.fetched_at for a in attempts if last_ok is None or a.fetched_at > last_ok]
    first_missing = since_ok[0] if since_ok else None
    overdue = d.escalate is not None and now >= d.escalate(period)
    captured = d.capture_until is None or now < d.capture_until(period)
    purpose = "first_capture" if captured else "gap_fill"

    def ask(state: str, overdue: bool = overdue, purpose: str = purpose) -> Decision:
        if first_missing is not None and now - first_missing >= d.give_up:
            return Decision("given_up", False, None, overdue, first_missing, last_reason)
        gap = d.retry if first_missing is None or now - first_missing < d.retry_for else d.backoff
        due = last is None or now - last.fetched_at >= gap
        return Decision(state, due, purpose, overdue, first_missing, last_reason)

    if last_ok is None:
        if now < d.earliest(period):
            return Decision("not_due", False, last_reason=last_reason)
        return ask("missing")
    settled = d.settled(period) if d.settled is not None else None
    if settled is not None and last_ok >= settled:
        if d.recheck is not None and now >= (check := d.recheck(period)) and last_ok < check:
            return ask("recheck", False, "correction_check")
        return Decision("done", False, last_reason=last_reason)
    if settled is not None and now >= settled:
        return ask("unsettled")
    if d.refresh is not None and now - last_ok >= d.refresh:
        stale = d.stale_after is not None and now - last_ok >= d.stale_after
        return ask("refresh", stale)
    return Decision("fresh", False, last_reason=last_reason)


# ---------------------------------------------------------------- periods


@dataclass
class Context:
    """What one plan reads once and every declaration shares."""

    connection: Connection
    now: datetime

    @property
    def today(self) -> date:
        return self.now.astimezone(TAIPEI).date()

    @cached_property
    def trading_days(self) -> list[date]:
        return list(self.connection.scalars(
            sa.select(v2.trading_days.c.trade_date)
            .where(v2.trading_days.c.trade_date.between(WINDOW_START, self.today))
            .order_by(v2.trading_days.c.trade_date)))

    @cached_property
    def trading_months(self) -> list[date]:
        return sorted({day.replace(day=1) for day in self.trading_days})

    @cached_property
    def stock_ids(self) -> list[str]:
        return sorted(listed_stock_ids(self.connection))


def _taipei(day: date, at: time = time(0)) -> datetime:
    return datetime.combine(day, at, tzinfo=TAIPEI)


def _next_month(month: date) -> date:
    return (month.replace(day=1) + timedelta(days=32)).replace(day=1)


# ---------------------------------------------------------------- declarations

# Operational guesses at when a trade date's file first exists, to be replaced
# by the times 28-c measures. Asking earlier only costs an empty answer an hour.
AFTER_CLOSE = time(15, 0)  # after the 14:30 odd-lot close (ROADMAP Step 28, point 2)
LATE_EVENING = time(21, 0)  # margin and lending: incomplete at 23:30 on 3 of 27 days (audit §7.2)
_LATE_TABLES = frozenset({"margin_trading", "securities_lending"})
ESCALATE_AFTER_SETTLED = timedelta(hours=5)  # 03:00 settles, 08:00 escalates
RECHECK_AFTER_SETTLED = timedelta(days=7)
CAPTURE_AFTER_SETTLED = DAY


def _resource(job: Job) -> Callable[[object], tuple[str, ...]]:
    return lambda period: (job.adapter.resource(job.request(period)).resource_key,)


def _exchange(job: Job) -> Declaration:
    after = LATE_EVENING if job.table.name in _LATE_TABLES else AFTER_CLOSE
    return Declaration(
        key=job.key, dataset=job.dataset, source=job.source,
        periods=(lambda c: c.trading_months) if job.monthly else (lambda c: c.trading_days),
        resources=_resource(job),
        earliest=lambda period: _taipei(period, after),
        settled=job.settled_at,
        escalate=lambda period: job.settled_at(period) + ESCALATE_AFTER_SETTLED,
        recheck=lambda period: job.settled_at(period) + RECHECK_AFTER_SETTLED,
        capture_until=lambda period: job.settled_at(period) + CAPTURE_AFTER_SETTLED,
    )


def _revenue(job: Job) -> Declaration:
    # Filed by each issuer until the 10th of the next month; the page keeps
    # changing until it settles, so it is read hourly and each new filer's
    # first capture is at most an hour late.
    return Declaration(
        key=job.key, dataset=job.dataset, source=job.source,
        periods=lambda c: [m for m in c.trading_months if _taipei(_next_month(m)) <= c.now],
        resources=_resource(job),
        earliest=lambda month: _taipei(_next_month(month)),
        settled=job.settled_at,
        escalate=lambda month: _taipei(_next_month(month).replace(day=11), time(8)),
        capture_until=lambda month: job.settled_at(month) + CAPTURE_AFTER_SETTLED,
        refresh=HOUR, stale_after=DAY, give_up=timedelta(days=60),
    )


def _latest_only(job: Job) -> Declaration:
    # TDCC serves only its latest week under one resource: one period, read
    # daily; the week is the file's own date and dedup keeps a week once.
    return Declaration(
        key=job.key, dataset=job.dataset, source=job.source,
        periods=lambda c: [None], resources=_resource(job),
        earliest=lambda period: datetime.min.replace(tzinfo=UTC),
        refresh=DAY, stale_after=timedelta(days=3),
    )


def _calendar() -> Declaration:
    adapter = trading_calendar.ADAPTER
    return Declaration(
        key=trading_calendar.KEY, dataset=adapter.dataset_code, source=adapter.source,
        periods=lambda c: trading_calendar.months(WINDOW_START, c.today),
        resources=lambda month: (trading_calendar.resource_key(month),),
        earliest=lambda month: _taipei(month),
        settled=trading_calendar.settled_at,
        capture_until=lambda month: trading_calendar.settled_at(month) + CAPTURE_AFTER_SETTLED,
        # The daily jobs learn a trade date from here: stale is overdue quickly.
        refresh=HOUR, stale_after=timedelta(hours=6),
    )


def _corporate(source: str) -> Declaration:
    adapter, _ = corporate_actions.FEEDS[source]

    def resources(year: int) -> tuple[str, ...]:
        # The key is the year's; the executed-through date is not part of it.
        return (adapter.resource(corporate_actions.request(year, date(year, 12, 31))).resource_key,)

    return Declaration(
        key=corporate_actions.key(source), dataset=corporate_actions.DATASET, source=source,
        periods=lambda c: range(WINDOW_START.year, c.today.year + 1),
        resources=resources,
        earliest=lambda year: _taipei(date(year, 1, 1)),
        settled=corporate_actions.settled_at,
        capture_until=lambda year: corporate_actions.settled_at(year) + CAPTURE_AFTER_SETTLED,
        refresh=DAY, stale_after=timedelta(days=3),
        # A held-back row or a detail that failed leaves the year to ask again.
        complete=lambda a: a.status == "succeeded" and a.reason_code is None,
    )


# The first filing seen before a deadline was 32 days early (2026Q1, legacy
# xbrl_scrape_daily.sh); asking from 35 days keeps first captures near filing.
FINANCIAL_LEAD = timedelta(days=35)
# A quarter stays declared this long after it settles. A filer missing an older
# quarter was usually not public then (8,205 of 50,622 stock-quarters in
# 2020Q1-2026Q2 answer no report): that history is a backfill's business.
FINANCIAL_OPEN = timedelta(days=90)


def _deadline(year: int, quarter: int) -> date:
    month, day = financial_reports.DEADLINES[quarter]
    return date(year + (quarter == 4), month, day)


def _financial() -> Declaration:
    adapter = financial_reports.ADAPTER

    def open_quarters(c: Context) -> list[tuple[int, int]]:
        quarters = financial_reports.quarters(date(c.today.year - 1, 1, 1),
                                              date(c.today.year, 12, 31))
        return [(year, quarter) for year, quarter in quarters
                if _taipei(_deadline(year, quarter) - FINANCIAL_LEAD) <= c.now
                < financial_reports.settled_at(year, quarter) + FINANCIAL_OPEN]

    def periods(c: Context) -> list[tuple[str, int, int]]:
        return [(stock, year, quarter) for year, quarter in open_quarters(c)
                for stock in c.stock_ids]

    def stored(c: Context) -> dict[object, list[Attempt]]:
        # Step 23-c wrote most versions from the legacy archive, under the
        # archive's resource keys: a stored version is a complete fetch.
        reports, f = v2.financial_reports, v2.fetches
        found: dict[object, list[Attempt]] = defaultdict(list)
        for stock, year, quarter, fetched_at, purpose in c.connection.execute(
            sa.select(reports.c.stock_id, reports.c.report_year, reports.c.report_quarter,
                      f.c.fetched_at, f.c.purpose)
            .join(f, f.c.id == reports.c.fetch_id)
            .where(sa.tuple_(reports.c.report_year, reports.c.report_quarter)
                   .in_(open_quarters(c) or [(0, 0)]))
        ):
            found[(stock, year, quarter)].append(Attempt(fetched_at, "succeeded", None, purpose))
        return found

    return Declaration(
        key=financial_reports.KEY, dataset=adapter.dataset_code, source=adapter.source,
        periods=periods,
        resources=lambda p: tuple(financial_reports.resource_key(*p, report_id)
                                  for report_id in financial_reports.REPORT_IDS),
        earliest=lambda p: _taipei(_deadline(p[1], p[2]) - FINANCIAL_LEAD),
        settled=lambda p: financial_reports.settled_at(p[1], p[2]),
        capture_until=lambda p: financial_reports.settled_at(p[1], p[2]) + CAPTURE_AFTER_SETTLED,
        # One sweep of every filer is ~1,900 x 2 requests at MOPS's 3 s: daily.
        retry=DAY, backoff=DAY, give_up=FINANCIAL_OPEN,
        complete=lambda a: a.status == "succeeded" or (
            a.status == "quarantined" and a.reason_code in financial_reports.FINAL_QUARANTINE),
        stored=stored,
    )


def _declarations() -> dict[str, Declaration]:
    """In dispatch order: the calendar first, the daily files, then the rest."""
    daily = [job for job in JOBS.values() if not job.monthly and not job.latest_only]
    monthly = [job for job in JOBS.values() if job.monthly and job.settled_of is None]
    revenue = [job for job in JOBS.values() if job.settled_of is not None and job.monthly]
    latest = [job for job in JOBS.values() if job.latest_only]
    declared = [
        _calendar(),
        *(_exchange(job) for job in sorted(daily + monthly, key=lambda job: job.key)),
        *(_corporate(source) for source in corporate_actions.FEEDS),
        *(_latest_only(job) for job in latest),
        *(_revenue(job) for job in sorted(revenue, key=lambda job: job.key)),
        _financial(),
    ]
    return {d.key: d for d in declared}


DECLARATIONS: dict[str, Declaration] = _declarations()


# ---------------------------------------------------------------- plan


@dataclass(frozen=True, slots=True)
class Planned:
    key: str
    period: object
    decision: Decision


@dataclass
class Plan:
    now: datetime
    items: list[Planned] = field(default_factory=list)  # every period that is not quiet
    counts: dict[str, Counter] = field(default_factory=dict)

    @property
    def dispatch(self) -> list[Planned]:
        return [item for item in self.items if item.decision.dispatch]


QUIET = frozenset({"done", "not_due", "fresh"})


def _attempts(connection: Connection, dataset: str, source: str) -> dict[str, list[Attempt]]:
    f = v2.fetches
    found: dict[str, list[Attempt]] = defaultdict(list)
    for key, *values in connection.execute(
        sa.select(f.c.resource_key, f.c.fetched_at, f.c.status, f.c.reason_code, f.c.purpose)
        .where(f.c.dataset == dataset, f.c.source == source)
        .order_by(f.c.fetched_at, f.c.attempt)
    ):
        found[key].append(Attempt(*values))
    return found


def plan(connection: Connection, now: datetime | None = None,
         declarations: Iterable[Declaration] | None = None) -> Plan:
    """Every declared period's decision at `now`, from stored rows only."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    context = Context(connection, now)
    result = Plan(now)
    loaded: dict[tuple[str, str], dict[str, list[Attempt]]] = {}
    for d in declarations or DECLARATIONS.values():
        if (d.dataset, d.source) not in loaded:
            loaded[(d.dataset, d.source)] = _attempts(connection, d.dataset, d.source)
        by_resource = loaded[(d.dataset, d.source)]
        counts = result.counts[d.key] = Counter()
        stored = d.stored(context) if d.stored is not None else {}
        for period in d.periods(context):
            attempts = [a for key in d.resources(period) for a in by_resource.get(key, ())]
            attempts += stored.get(period, ())
            decision = decide(d, period, attempts, now)
            counts[decision.state] += 1
            if decision.state not in QUIET:
                result.items.append(Planned(d.key, period, decision))
    return result


def describe(period) -> str:
    if period is None:
        return "latest"
    if isinstance(period, tuple):
        stock, year, quarter = period
        return f"{stock} {year}Q{quarter}"
    return period.isoformat() if isinstance(period, date) else str(period)


def summary(result: Plan) -> dict:
    dispatch: dict[str, list[str]] = defaultdict(list)
    for item in result.dispatch:
        dispatch[item.key].append(f"{describe(item.period)} {item.decision.state} "
                                  f"{item.decision.purpose}")
    return {
        "now": result.now.isoformat(),
        "dispatch": dict(dispatch),
        "overdue": [
            {"job": item.key, "period": describe(item.period), "state": item.decision.state,
             "since": item.decision.first_missing and item.decision.first_missing.isoformat(),
             "reason": item.decision.last_reason}
            for item in result.items
            if item.decision.overdue and item.decision.state != "given_up"
        ],
        "counts": {key: dict(counts) for key, counts in result.counts.items()},
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--now", type=datetime.fromisoformat,
                        help="an aware ISO instant; default: now")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    if args.now is not None and args.now.tzinfo is None:
        raise SystemExit("--now needs a UTC offset")
    with sa.create_engine(url).connect() as connection:
        result = plan(connection, args.now)
    print(json.dumps(summary(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
