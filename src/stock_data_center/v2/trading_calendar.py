"""The v2 writer of `trading_days`, from TWSE FMTQIK (ROADMAP Step 28-a).

Every daily job takes its periods from `trading_days`, so this table is what
the scheduler expects to exist. FMTQIK lists the days the market actually
traded, one month per file (audit §4.12): a closure is an absence, and a day
enters the table only because the exchange listed it.

A listed day is inserted once and names the fetch that first listed it; a
later file listing it again changes nothing. A stored day the exchange no
longer lists is not deleted: that fetch quarantines as `calendar_day_removed`
and names the day, because a calendar that shrinks quietly would turn a gap
into a closure. A month whose first trading day has not closed yet answers
TWSE's no-data stat and is logged `empty`, asked again later.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.schema_v2 import fetches, trading_days
from stock_data_center.ingestion.adapters import TWSETradingCalendarAdapter
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.models import TradingCalendarRequest
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2.exchange_daily import Outcome, available_from, fetch_and_parse

ADAPTER = TWSETradingCalendarAdapter()
KEY = "trading_days/twse"
TAIPEI = ZoneInfo("Asia/Taipei")


def months(start: date, end: date) -> list[date]:
    """The first day of every month that overlaps [start, end]."""
    month, last = start.replace(day=1), end.replace(day=1)
    out = []
    while month <= last:
        out.append(month)
        month = (month + timedelta(days=32)).replace(day=1)
    return out


def month_start(month: date) -> datetime:
    return datetime.combine(month, time(0), tzinfo=TAIPEI).astimezone(UTC)


def settled_at(month: date) -> datetime:
    """The month's file is complete once its last day has settled."""
    return available_from((month + timedelta(days=32)).replace(day=1) - timedelta(days=1))


def resource_key(month: date) -> str:
    return ADAPTER.resource(TradingCalendarRequest(month)).resource_key


def ingest(
    connection: Connection,
    month: date,
    *,
    fetcher: SourceFetcher,
    git_commit: str,
    purpose: str,
    store: LocalRawArtifactStore | None = None,
) -> Outcome:
    """Fetch one month's calendar and insert the days not stored yet."""
    fetched = fetch_and_parse(
        connection, ADAPTER, TradingCalendarRequest(month), dataset=ADAPTER.dataset_code,
        parse=lambda parsed: parsed.trading_days, fetcher=fetcher, git_commit=git_commit,
        purpose=purpose, store=store, empty=frozenset({"no_data_for_period"}),
    )
    if isinstance(fetched, Outcome):
        return fetched
    listed = set(fetched.value)
    next_month = (month + timedelta(days=32)).replace(day=1)
    stored = set(connection.scalars(
        sa.select(trading_days.c.trade_date)
        .where(trading_days.c.trade_date >= month, trading_days.c.trade_date < next_month)
    ))
    if removed := sorted(stored - listed):
        detail = "no longer listed: " + ", ".join(day.isoformat() for day in removed)
        fetch_id = fetched.log("quarantined", reason="calendar_day_removed", detail=detail)
        return Outcome("quarantined", fetch_id, len(listed), reason_code="calendar_day_removed")
    fetch_id = fetched.log("succeeded")
    new = sorted(listed - stored)
    if new:
        connection.execute(sa.insert(trading_days),
                           [{"trade_date": day, "fetch_id": fetch_id} for day in new])
    return Outcome("succeeded", fetch_id, len(listed), len(new), len(listed) - len(new))


def pending(connection: Connection, wanted: Sequence[date]) -> list[date]:
    """The months not yet done: the latest fetch succeeded after the month settled."""
    by_key = {resource_key(month): month for month in wanted}
    f = fetches
    done = {
        by_key[key]
        for key, status, fetched_at in connection.execute(
            sa.select(f.c.resource_key, f.c.status, f.c.fetched_at)
            .where(f.c.dataset == ADAPTER.dataset_code, f.c.source == ADAPTER.source,
                   f.c.resource_key.in_(list(by_key)))
            .order_by(f.c.resource_key, f.c.fetched_at.desc(), f.c.attempt.desc())
            .distinct(f.c.resource_key)
        )
        if status == "succeeded" and fetched_at >= settled_at(by_key[key])
    }
    return [month for month in wanted if month not in done]


def run(bind, start: date, end: date, *, fetcher, git_commit, purpose, unit, store=None,
        refetch=False, progress=None) -> dict[str, int]:
    """Every month overlapping [start, end] that is not done (all of them with `refetch`)."""
    wanted = months(start, end)
    with unit(bind) as connection:
        todo = wanted if refetch else pending(connection, wanted)
    counts = Counter(periods=len(wanted), skipped=len(wanted) - len(todo),
                     appended=0, unchanged=0)
    for month in todo:
        with unit(bind) as connection:
            outcome = ingest(connection, month, fetcher=fetcher, git_commit=git_commit,
                             purpose=purpose, store=store)
        counts[outcome.status] += 1
        counts["appended"] += outcome.appended
        counts["unchanged"] += outcome.unchanged
        if progress:
            progress(KEY, month, outcome)
    return dict(counts)
