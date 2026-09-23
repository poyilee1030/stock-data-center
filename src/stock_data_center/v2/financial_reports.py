"""The v2 write path for financial reports (ADR-0027 "35-c 定案", Step 35-c-2).

One MOPS `t164sb01` document per filer and quarter. A version is the whole
report: the `financial_reports` row and every fact of the three statements are
written in one transaction, and a document whose category or any fact differs
from the latest version is a new version carrying its full set, so a fact a
restatement drops stays representable. An unchanged document logs its fetch
and writes nothing.

A filer files 合併 (`C`) or 個體 (`A`), never both, and MOPS answers
`檔案不存在!` for the other: the writer asks for `C`, then `A`.

A key's first version is public from the fetch time of a first capture; any
other purpose proves nothing (CLAUDE.md §32), and a later version is public
from its own `recorded_at`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db import schema_v2 as v2
from stock_data_center.ingestion import models as m
from stock_data_center.ingestion.adapters.financial_filing import (
    REPORT_CATEGORIES,
    MOPSFinancialFilingAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2.exchange_daily import (
    Fetched,
    Outcome,
    check_precision,
    fetch_and_parse,
)

ADAPTER = MOPSFinancialFilingAdapter()
KEY = "financial_reports/mops_t164sb01"
REPORT_IDS = ("C", "A")
# Source answers that will not change on a refetch: the filer is outside v1.
FINAL_QUARANTINE = frozenset({"financial_industry_issuer", "outside_v1_universe"})
# financial_statements_general@1: Q4 03/31 and Q1 05/15 statutory, Q2 08/15 and
# Q3 11/15 one day after the statutory 08/14 and 11/14 (audit §7.1).
DEADLINES = {1: (5, 15), 2: (8, 15), 3: (11, 15), 4: (3, 31)}
_FACT_KEY = ("statement", "concept", "period_start", "period_end")


def settled_at(year: int, quarter: int) -> datetime:
    """00:00 Asia/Taipei the day after the filing deadline: a report fetched
    earlier may still be filed or refiled. Completeness, not visibility."""
    month, day = DEADLINES[quarter]
    deadline = date(year + (quarter == 4), month, day)
    local = datetime.combine(deadline + timedelta(days=1), time(0), tzinfo=ZoneInfo("Asia/Taipei"))
    return local.astimezone(UTC)


def quarters(start: date, end: date) -> list[tuple[int, int]]:
    """The quarters whose period ends inside [start, end]."""
    found = []
    for year in range(start.year, end.year + 1):
        for quarter, (month, day) in ((1, (3, 31)), (2, (6, 30)), (3, (9, 30)), (4, (12, 31))):
            if start <= date(year, month, day) <= end:
                found.append((year, quarter))
    return found


def facts_of(parsed) -> list[dict]:
    """The stored facts of one document; refuses what the v2 identity cannot hold."""
    facts = []
    for fact in parsed.facts:
        observation = fact.observation
        context = observation.context
        if (context.explicit_dimensions or context.typed_dimensions
                or context.scenario or context.segment):
            # ADR-0027, overriding CLAUDE.md §33: none of the 16,158,302 stored
            # facts has one, so the key has no place for it; fail closed.
            raise m.SourceDataError(
                "dimensioned_fact", f"{observation.concept_qname} carries a dimension")
        if context.period_type == "forever" or observation.numeric_value is None:
            raise m.SourceDataError(
                "unstorable_fact", f"{observation.concept_qname} is not a numeric period fact")
        instant = context.period_type == "instant"
        facts.append({
            "statement": observation.statement,
            "account_code": fact.account_code,
            "concept": observation.concept_qname,
            "period_start": None if instant else context.period_start,
            "period_end": context.instant_date if instant else context.period_end,
            "unit": observation.unit_identity,
            "value": observation.numeric_value,
        })
    keys = [tuple(f[c] for c in _FACT_KEY) for f in facts]
    if len(keys) != len(set(keys)):
        raise m.SourceDataError("duplicate_key", "a fact identity appears twice")
    return check_precision(v2.financial_report_facts, facts)


def _identity(facts) -> frozenset:
    return frozenset(tuple(f[c] for c in (*_FACT_KEY, "account_code", "unit", "value"))
                     for f in facts)


def _write(connection, stock_id, year, quarter, fetched: Fetched, first_seen) -> Outcome:
    connection.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(
        f"{KEY}/{stock_id}"))))
    reports, facts = v2.financial_reports, v2.financial_report_facts
    category = REPORT_CATEGORIES[fetched.parsed.report_category]
    latest = connection.execute(
        sa.select(reports.c.id, reports.c.report_category)
        .where(reports.c.stock_id == stock_id, reports.c.report_year == year,
               reports.c.report_quarter == quarter)
        .order_by(reports.c.recorded_at.desc()).limit(1)
    ).first()
    new = fetched.value
    if latest is not None and latest.report_category == category:
        stored = connection.execute(sa.select(facts).where(facts.c.report_id == latest.id))
        if _identity(dict(row._mapping) for row in stored) == _identity(new):
            fetch_id = fetched.log("succeeded")
            return Outcome("succeeded", fetch_id, len(new), unchanged=1)
    fetch_id = fetched.log("succeeded")
    report_id = connection.execute(
        sa.insert(reports).values(
            stock_id=stock_id, report_year=year, report_quarter=quarter,
            report_category=category, fetch_id=fetch_id,
            published_at=first_seen if latest is None else None,
        ).returning(reports.c.id)
    ).scalar_one()
    connection.execute(sa.insert(facts), [{**fact, "report_id": report_id} for fact in new])
    return Outcome("succeeded", fetch_id, len(new), appended=1)


def ingest(
    connection: Connection,
    stock_id: str,
    year: int,
    quarter: int,
    *,
    fetcher: SourceFetcher,
    git_commit: str,
    purpose: str,
    store: LocalRawArtifactStore | None = None,
) -> Outcome:
    """Fetch one filer's quarter, 合併 then 個體, and write a version if it changed."""
    for report_id in REPORT_IDS:
        fetched = fetch_and_parse(
            connection, ADAPTER, m.FinancialFilingRequest(stock_id, year, quarter, report_id),
            dataset=ADAPTER.dataset_code, parse=facts_of, fetcher=fetcher,
            git_commit=git_commit, purpose=purpose, store=store,
            empty=frozenset({"no_such_report"}),
        )
        if isinstance(fetched, Outcome):
            if fetched.reason_code == "no_such_report" and report_id != REPORT_IDS[-1]:
                continue
            return fetched
        first_seen = fetched.fetched_at if purpose == "first_capture" else None
        return _write(connection, stock_id, year, quarter, fetched, first_seen)
    raise AssertionError("unreachable")  # pragma: no cover


def _resource_key(stock_id: str, year: int, quarter: int, report_id: str) -> str:
    request = m.FinancialFilingRequest(stock_id, year, quarter, report_id)
    return ADAPTER.resource(request).resource_key


def pending(connection: Connection, wanted: Sequence[tuple[str, int, int]]) -> list:
    """The (stock, year, quarter) not yet done. Done means a stored version or
    the latest fetch of either report id succeeded after the quarter settled,
    or the filer is outside v1 (a final source answer). An empty answer is a
    report not filed yet, asked again."""
    reports, f = v2.financial_reports, v2.fetches
    stored = {
        (row.stock_id, row.report_year, row.report_quarter): row.fetched_at
        for row in connection.execute(
            sa.select(reports.c.stock_id, reports.c.report_year, reports.c.report_quarter,
                      sa.func.max(f.c.fetched_at).label("fetched_at"))
            .join(f, f.c.id == reports.c.fetch_id)
            .where(reports.c.stock_id.in_({stock for stock, _, _ in wanted}))
            .group_by(reports.c.stock_id, reports.c.report_year, reports.c.report_quarter)
        )
    }
    by_key = {_resource_key(*item, rid): item for item in wanted for rid in REPORT_IDS}
    done = {item for item, fetched_at in stored.items()
            if fetched_at >= settled_at(*item[1:])}
    for key, status, reason, fetched_at in connection.execute(
        sa.select(f.c.resource_key, f.c.status, f.c.reason_code, f.c.fetched_at)
        .where(f.c.dataset == ADAPTER.dataset_code, f.c.source == ADAPTER.source,
               f.c.resource_key.in_(list(by_key)))
        .order_by(f.c.resource_key, f.c.fetched_at.desc(), f.c.attempt.desc())
        .distinct(f.c.resource_key)
    ):
        item = by_key[key]
        if (status == "succeeded" and fetched_at >= settled_at(*item[1:])) or (
            status == "quarantined" and reason in FINAL_QUARANTINE
        ):
            done.add(item)
    return [item for item in wanted if item not in done]


def run(bind, start: date, end: date, *, fetcher, git_commit, purpose, store=None,
        refetch=False, progress=None, unit) -> dict[str, int]:
    """Every stock on today's list for every quarter ending in [start, end]."""
    with unit(bind) as connection:
        stock_ids = sorted(connection.scalars(sa.select(v2.stocks.c.stock_id)))
        wanted = [(stock, year, quarter) for year, quarter in quarters(start, end)
                  for stock in stock_ids]
        todo = wanted if refetch else pending(connection, wanted)
    counts = Counter(periods=len(wanted), skipped=len(wanted) - len(todo),
                     appended=0, unchanged=0)
    for stock_id, year, quarter in todo:
        with unit(bind) as connection:
            outcome = ingest(connection, stock_id, year, quarter, fetcher=fetcher,
                             git_commit=git_commit, purpose=purpose, store=store)
        counts[outcome.status] += 1
        counts["appended"] += outcome.appended
        counts["unchanged"] += outcome.unchanged
        if progress:
            progress(KEY, date(year, quarter * 3, 1), outcome)
    return dict(counts)
