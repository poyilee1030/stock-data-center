"""The v2 write path for corporate actions (ADR-0027 "35-c 定案", Step 35-c-3).

Six exchange result feeds, one list file per feed and year (audit §4.10). A
TWSE dividend (`TWT49U`) or reduction (`TWTAUU`) row publishes its terms only on
its own detail page, fetched for today's common stocks only; the other feeds'
rows are complete. A key is (stock, feed, ex-date), the executed event itself
(CLAUDE.md §51.5); a current-year file also lists coming events, which the
adapter counts and never returns (`executed_through`, ADR-0019).

- An event already stored is not asked for its detail again, except by a
  correction check: a rerun of a year costs one request, not thousands.
- A detail that fails holds back its own row; the year stays pending and the
  next run asks for that detail only.
- A stored event the feed no longer lists inside the executed range is
  retracted by a new row carrying `retracted`; listed again, it gets another.

Each detail commits on its own, so no transaction waits on HTTP for the whole
year; the list's fetch row commits with the rows it produced. Publication
follows `corporate_action_ex_date@1`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db import schema_v2 as v2
from stock_data_center.ingestion import models as m
from stock_data_center.ingestion.adapters.corporate_action import (
    TPExExRightDailyAdapter,
    TPExParValueChangeAdapter,
    TPExReductionAdapter,
    TWSEDividendDetailAdapter,
    TWSEExRightAdapter,
    TWSEParValueChangeAdapter,
    TWSEReductionAdapter,
    TWSEReductionDetailAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2.exchange_daily import Outcome as FetchOutcome
from stock_data_center.v2.exchange_daily import (
    _latest,
    check_precision,
    fetch_and_parse,
    key_columns,
    value_columns,
)
from stock_data_center.v2.release_rules import corporate_action_available_from

TABLE = v2.corporate_actions
# source -> (list adapter, detail adapter or None)
FEEDS = {
    adapter.source: (adapter, detail)
    for adapter, detail in (
        (TWSEExRightAdapter(), TWSEDividendDetailAdapter()),
        (TWSEReductionAdapter(), TWSEReductionDetailAdapter()),
        (TWSEParValueChangeAdapter(), None),
        (TPExExRightDailyAdapter(), None),
        (TPExReductionAdapter(), None),
        (TPExParValueChangeAdapter(), None),
    )
}
DATASET = "corporate_action"  # the v1 dataset code, as every v2 fetch keeps


def key(source: str) -> str:
    return f"{TABLE.name}/{source}"


@dataclass(frozen=True, slots=True)
class Outcome:
    status: str
    fetch_id: object
    parsed: int = 0
    appended: int = 0
    unchanged: int = 0
    retracted: int = 0
    rejected: int = 0
    not_yet_executed: int = 0
    reason_code: str | None = None


def _money(value):
    return None if value is None else value.value


def _row(source: str, stock_id: str, observation) -> dict:
    return {
        "stock_id": stock_id,
        "source": source,
        "ex_date": observation.ex_date,
        "event_type": observation.source_event_type,
        "close_before": _money(observation.close_before),
        "reference_price": _money(observation.official_reference_price),
        "rights_dividend_value": _money(observation.official_rights_dividend_value),
        "cash_dividend_per_share": _money(observation.cash_dividend_per_share),
        "free_share_ratio": observation.free_share_ratio,
        "rights_ratio": observation.rights_ratio,
        "subscription_price": _money(observation.subscription_price),
        "old_shares": observation.old_shares,
        "new_shares": observation.new_shares,
        "cash_return_per_share": _money(observation.capital_reduction_cash_return_per_share),
        "retracted": False,
    }


def settled_at(year: int) -> datetime:
    """A year's file is complete once its last day has been executed and the
    next year begins; until then it still lists events to come."""
    return corporate_action_available_from(date(year + 1, 1, 1))


def request(year: int, executed_through: date) -> m.CorporateActionRangeRequest:
    end = date(year, 12, 31)
    return m.CorporateActionRangeRequest(date(year, 1, 1), end, min(end, executed_through))


def _stored(connection, source: str, start: date, through: date) -> dict:
    """Each key's latest row with an ex-date inside the executed range."""
    days = connection.scalars(
        sa.select(TABLE.c.ex_date).distinct()
        .where(TABLE.c.source == source, TABLE.c.ex_date.between(start, through))
    ).all()
    return _latest(connection, TABLE, source=source, dates=days, column="ex_date")


def ingest(
    bind,
    source: str,
    year: int,
    *,
    fetcher: SourceFetcher,
    git_commit: str,
    purpose: str,
    unit,
    executed_through: date,
    store: LocalRawArtifactStore | None = None,
    stock_ids: frozenset[str] | None = None,
) -> Outcome:
    """Fetch one feed's year, its missing details, and write what changed."""
    adapter, detail_adapter = FEEDS[source]
    wanted = request(year, executed_through)
    common = {"dataset": DATASET, "fetcher": fetcher, "git_commit": git_commit,
              "purpose": purpose, "store": store}
    with unit(bind) as connection:
        if stock_ids is None:
            stock_ids = frozenset(connection.scalars(sa.select(v2.stocks.c.stock_id)))
        listed = fetch_and_parse(connection, adapter, wanted, parse=lambda p: p, **common)
        if isinstance(listed, FetchOutcome):
            return Outcome(listed.status, listed.fetch_id, reason_code=listed.reason_code)
        stored = _stored(connection, source, wanted.start, wanted.executed_through)
    parsed = listed.parsed
    rows, rejected, listed_keys = [], [], set()
    for item in parsed.rows:
        if item.security_code not in stock_ids:
            continue
        detail = None
        if item.detail_request is not None:
            # Every feed's ex-date is its row's event date, so the key is known
            # before the detail is read.
            event_key = (item.security_code, source, item.event_date)
            old = stored.get(event_key)
            if old is not None and not old["retracted"] and purpose != "correction_check":
                listed_keys.add(event_key)
                continue  # already stored: nothing to ask, nothing to write
            with unit(bind) as connection:
                fetched = fetch_and_parse(connection, detail_adapter, item.detail_request,
                                          parse=lambda p: p, **common)
                if isinstance(fetched, FetchOutcome):
                    rejected.append(f"{item.security_code} {item.event_date}: "
                                    f"{fetched.reason_code}")
                    # Listed, so not retracted, though its terms are unknown today.
                    listed_keys.add(event_key)
                    continue
                fetched.log("succeeded")
                detail = fetched.parsed
        try:
            row = _row(source, item.security_code, adapter.observation(item, detail))
            check_precision(TABLE, [row])
        except (m.SourceDataError, ValueError) as error:
            rejected.append(f"{item.security_code} {item.event_date}: {error}")
            continue
        rows.append(row)
        listed_keys.add(tuple(row[c] for c in key_columns(TABLE)))
    with unit(bind) as connection:
        return _write(connection, source, parsed, listed, rows, rejected, listed_keys, stock_ids)


def _write(connection, source, parsed, listed, rows, rejected, listed_keys, stock_ids) -> Outcome:
    connection.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(key(source)))))
    stored = _stored(connection, source, parsed.start, parsed.executed_through)
    keys = key_columns(TABLE)
    values = value_columns(TABLE)
    if len({tuple(row[c] for c in keys) for row in rows}) != len(rows):
        fetch_id = listed.log("quarantined", "duplicate_key", "a key appears twice", into=connection)
        return Outcome("quarantined", fetch_id, reason_code="duplicate_key")
    reason = "rows_rejected" if rejected else None
    fetch_id = listed.log("succeeded", reason, "; ".join(rejected)[:2000] or None, into=connection)
    changed = [
        {**row, "fetch_id": fetch_id} for row in rows
        if (old := stored.get(tuple(row[c] for c in keys))) is None
        or any(row[c] != old[c] for c in values)
    ]
    # A stored event this file no longer lists, inside its executed range, is
    # withdrawn by a new row; the old rows stay (CLAUDE.md §51.5).
    retractions = [
        {**{c: old[c] for c in (*keys, *values)}, "retracted": True, "fetch_id": fetch_id}
        for stored_key, old in stored.items()
        if stored_key not in listed_keys and not old["retracted"]
        and old["stock_id"] in stock_ids
    ]
    for batch in (changed, retractions):
        for row in batch:
            row.pop("recorded_at", None)
        if batch:
            connection.execute(sa.insert(TABLE), batch)
    unchanged = len(listed_keys) - len(changed)
    return Outcome(
        "succeeded", fetch_id, len(parsed.rows), len(changed), unchanged, len(retractions),
        len(rejected), parsed.not_yet_executed, reason,
    )


def pending(connection: Connection, source: str, years: Sequence[int]) -> list[int]:
    """The years not yet done: done means the latest list fetch succeeded with
    no row held back and was made after the year ended."""
    adapter, _ = FEEDS[source]
    by_key = {adapter.resource(request(year, date(year, 12, 31))).resource_key: year
              for year in years}
    f = v2.fetches
    done = {
        by_key[resource_key]
        for resource_key, status, reason, fetched_at in connection.execute(
            sa.select(f.c.resource_key, f.c.status, f.c.reason_code, f.c.fetched_at)
            .where(f.c.dataset == DATASET, f.c.source == source,
                   f.c.resource_key.in_(list(by_key)))
            .order_by(f.c.resource_key, f.c.fetched_at.desc(), f.c.attempt.desc())
            .distinct(f.c.resource_key)
        )
        if status == "succeeded" and reason is None
        and fetched_at >= settled_at(by_key[resource_key])
    }
    return [year for year in years if year not in done]


def run(bind, sources: Sequence[str], start: date, end: date, *, fetcher, git_commit,
        purpose, unit, store=None, refetch=False, progress=None,
        today: date | None = None) -> dict[str, dict[str, int]]:
    """Every year in [start, end] of each feed; `today` bounds what is executed."""
    today = today or datetime.now(UTC).date()
    years = list(range(start.year, end.year + 1))
    report = {}
    for source in sources:
        with unit(bind) as connection:
            todo = years if refetch else pending(connection, source, years)
        counts = Counter(periods=len(years), skipped=len(years) - len(todo), appended=0,
                         unchanged=0, retracted=0, rejected=0)
        for year in todo:
            outcome = ingest(bind, source, year, fetcher=fetcher, git_commit=git_commit,
                             purpose=purpose, unit=unit, executed_through=today, store=store)
            counts[outcome.status] += 1
            for name in ("appended", "unchanged", "retracted", "rejected"):
                counts[name] += getattr(outcome, name)
            if progress:
                progress(key(source), date(year, 1, 1), outcome)
        report[key(source)] = dict(counts)
    return report
