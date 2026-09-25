"""Which row a PIT context sees, for every observed table (Step 27-a).

The one place that decides visibility (CLAUDE.md §19); the API asks it and
never improvises. Market PIT (§15) answers what was publicly knowable by
`information_as_of`, using what the Data Center had recorded by
`knowledge_as_of`: a row is eligible when

    available_at <= information_as_of AND recorded_at <= knowledge_as_of

and a key's answer is its latest eligible row. System PIT (§16) answers what
the Data Center had recorded by `system_as_of`: `recorded_at <= system_as_of`.

`available_at` is a property of the row, computed from all of its key's rows
and never supplied (`docs/pit_semantics.md`):

- a rule-based family (exchange daily data, TDCC, corporate actions) makes a
  key's rows recorded before the rule instant provisional and available from
  the instant, the first row recorded at or after it the settled value, also
  available from the instant, and every later row a correction, available from
  its own `recorded_at`;
- a published family (monthly revenue, financial reports) makes a key's first
  row available at its stored `published_at` — never, when that is NULL (§31) —
  and every later row from its own `recorded_at`.

A release rule belongs to its (dataset, source) (§29). Every exchange daily job
declares `exchange_daily_settled@1`, the TDCC job `tdcc_weekly@1`, and the
corporate-action result feeds `corporate_action_ex_date@1`, so each table has
one rule.

A corporate action whose visible row is a retraction is not there (§51.5: a
row the feed drops is retracted by a new row, never deleted). A financial
report's facts are its version's own (§20): `report_facts`.

A stored derived row follows the latest inputs and has no knowledge axis
(§43), so only its `available_at` is asked: when every input row it was
computed from was public (Step 27-c, `derived_rows`).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.sql.elements import ColumnElement

from stock_data_center.db import schema_v2 as v2
from stock_data_center.v2.exchange_daily import (
    available_from,
    available_from_sql,
    key_columns,
)
from stock_data_center.v2.release_rules import (
    corporate_action_available_from_sql,
    tdcc_available_from,
    tdcc_available_from_sql,
)

if TYPE_CHECKING:
    from stock_data_center.v2.derived_store import Input, StoredDataset

# The latest instant PostgreSQL and Python both represent, for "everything recorded".
FOREVER = datetime(9999, 12, 31, tzinfo=UTC)


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware instant")


@dataclass(frozen=True, slots=True)
class MarketPIT:
    information_as_of: datetime
    knowledge_as_of: datetime

    def __post_init__(self) -> None:
        _aware("information_as_of", self.information_as_of)
        _aware("knowledge_as_of", self.knowledge_as_of)


@dataclass(frozen=True, slots=True)
class SystemPIT:
    system_as_of: datetime

    def __post_init__(self) -> None:
        _aware("system_as_of", self.system_as_of)


PIT = MarketPIT | SystemPIT


@dataclass(frozen=True)
class Family:
    table: sa.Table
    keys: tuple[str, ...]
    # The key's date: what `start` and `end` filter.
    period: Callable[[sa.Table], ColumnElement]
    # A rule instant from the period, or None for a stored `published_at`.
    rule: Callable[[ColumnElement], ColumnElement] | None
    # A visible row that means the key is not there.
    hidden: Callable[[sa.Table], ColumnElement] | None = None

    def available_at(self) -> ColumnElement:
        t = self.table
        partition = [t.c[k] for k in self.keys]
        recorded = t.c.recorded_at
        if self.rule is None:
            first = sa.func.min(recorded).over(partition_by=partition)
            return sa.case((recorded == first, t.c.published_at), else_=recorded)
        released = self.rule(self.period(t))
        settled = sa.func.min(recorded).filter(recorded >= released).over(partition_by=partition)
        return sa.case((recorded < released, released), (recorded == settled, released),
                       else_=recorded)


def _column(name: str) -> Callable[[sa.Table], ColumnElement]:
    return lambda t: t.c[name]


def _quarter_end(t: sa.Table) -> ColumnElement:
    first_of_last_month = sa.func.make_date(t.c.report_year, t.c.report_quarter * 3, 1)
    return sa.cast(first_of_last_month + sa.literal_column("interval '1 month -1 day'"),
                   sa.Date)


def _rule_family(table: sa.Table, period: str, rule) -> Family:
    return Family(table, key_columns(table), _column(period), rule)


FAMILIES: dict[str, Family] = {
    family.table.name: family
    for family in (
        *(_rule_family(table, "trade_date", available_from_sql) for table in (
            v2.daily_prices, v2.index_prices, v2.valuations, v2.institutional_flows,
            v2.institutional_market_flows, v2.foreign_holdings, v2.margin_trading,
            v2.securities_lending)),
        _rule_family(v2.shareholding_distributions, "snapshot_date", tdcc_available_from_sql),
        Family(v2.corporate_actions, key_columns(v2.corporate_actions), _column("ex_date"),
               corporate_action_available_from_sql, hidden=lambda t: t.c.retracted),
        Family(v2.monthly_revenues, key_columns(v2.monthly_revenues),
               _column("revenue_month"), None),
        Family(v2.financial_reports, ("stock_id", "report_year", "report_quarter"),
               _quarter_end, None),
    )
}


def rows(connection: Connection, dataset: str, pit: PIT, *, start: date, end: date,
         stock_ids: Sequence[str] | None = None,
         sources: Sequence[str] | None = None) -> list[sa.RowMapping]:
    """Each key's row as `pit` sees it, for keys whose date is in [start, end].

    Every row carries the table's columns and its `available_at` (NULL for a
    first row with no proven publication), in key order."""
    family = FAMILIES[dataset]
    t = family.table
    inner = sa.select(t, family.available_at().label("available_at")).where(
        family.period(t).between(start, end))
    if stock_ids is not None:
        if "stock_id" not in t.c:
            raise ValueError(f"{dataset} has no stock: filter it by its own key")
        inner = inner.where(t.c.stock_id.in_(list(stock_ids)))
    if sources is not None:
        if "source" not in t.c:
            raise ValueError(f"{dataset} has one source: it takes no source filter")
        inner = inner.where(t.c.source.in_(list(sources)))
    inner = inner.subquery()
    if isinstance(pit, MarketPIT):
        eligible = sa.and_(inner.c.available_at <= pit.information_as_of,
                           inner.c.recorded_at <= pit.knowledge_as_of)
    else:
        eligible = inner.c.recorded_at <= pit.system_as_of
    keys = [inner.c[k] for k in family.keys]
    latest = (
        sa.select(*(inner.c[c.name] for c in t.columns), inner.c.available_at)
        .where(eligible)
        .order_by(*keys, inner.c.recorded_at.desc())
        .distinct(*keys)
    )
    if family.hidden is not None:
        seen = latest.subquery()
        latest = (sa.select(seen).where(sa.not_(family.hidden(seen)))
                  .order_by(*(seen.c[k] for k in family.keys)))
    return list(connection.execute(latest).mappings())


def report_facts(connection: Connection, report_ids: Iterable[int], *,
                 statements: Sequence[str] | None = None,
                 account_codes: Sequence[str] | None = None,
                 limit: int | None = None) -> dict[int, list[sa.RowMapping]]:
    """Each report version's own facts, by statement, account and period.

    `statements` and `account_codes` narrow them; `limit` caps how many are read
    in all, so a caller can tell a request too large to answer."""
    ids = list(report_ids)
    out: dict[int, list[sa.RowMapping]] = {report_id: [] for report_id in ids}
    if not ids:
        return out
    f = v2.financial_report_facts
    query = (sa.select(f).where(f.c.report_id.in_(ids))
             .order_by(f.c.report_id, f.c.statement, f.c.account_code, f.c.period_start,
                       f.c.period_end, f.c.concept))
    if statements is not None:
        query = query.where(f.c.statement.in_(list(statements)))
    if account_codes is not None:
        query = query.where(f.c.account_code.in_(list(account_codes)))
    if limit is not None:
        query = query.limit(limit)
    for fact in connection.execute(query).mappings():
        out[fact["report_id"]].append(fact)
    return out


# ---------------------------------------------------------------- stored derived

# A derived row's own date is public at its inputs' release instant for it.
_RELEASED = {"trade_date": available_from, "snapshot_date": tdcc_available_from}
_TAIPEI = ZoneInfo("Asia/Taipei")


def derived_rows(connection: Connection, dataset: StoredDataset, *,
                 information_as_of: datetime | None, start: date, end: date,
                 stock_ids: Sequence[str] | None = None,
                 sources: Sequence[str] | None = None) -> list[dict]:
    """The stored rows dated in [start, end] public by `information_as_of`, all if None.

    A row is available once every input row it was computed from is: its own
    date's release instant, or later where the latest row of an input key it
    reads is a correction available later. Only a key with more than one row
    can hold one: a key's only row is available at its own release instant, no
    later than the derived row's, and a report's first version on its day of
    publication. Every row carries its `available_at`, in key order."""
    t = dataset.table
    [day] = (c for c in t.primary_key.columns if c.name not in ("stock_id", "source"))
    query = sa.select(t).where(day.between(start, end))
    if stock_ids is not None:
        query = query.where(t.c.stock_id.in_(list(stock_ids)))
    if sources is not None:
        query = query.where(t.c.source.in_(list(sources)))
    rows = [dict(r) for r in connection.execute(
        query.order_by(t.c.stock_id, t.c.source, day)).mappings()]
    if not rows:
        return rows
    released = _RELEASED[day.name]
    stocks = sorted({r["stock_id"] for r in rows})
    series = sorted({r["source"] for r in rows})
    late = [(i, _late(connection, i, stocks, series, end)) for i in dataset.depends]
    out = []
    for row in rows:
        at = released(row[day.name])
        for i, by_series in late:
            key = row["stock_id"] if i.source is None else (row["stock_id"], i.source(row["source"]))
            dates, instants = by_series.get(key, ((), ()))
            n = bisect_right(dates, row[day.name])
            if n and (i.scope == "history" or dates[n - 1] == row[day.name]):
                at = max(at, instants[n - 1])
        if information_as_of is None or at <= information_as_of:
            row["available_at"] = at
            out.append(row)
    return out


def _late(connection: Connection, i: Input, stocks: list[str], series: list[str],
          end: date) -> dict:
    """Each input series' multi-row keys through `end`: their dates and the
    instants their latest rows became available, as a running maximum for a
    "history" input and as is for a "day" one."""
    family = FAMILIES[i.table.name]
    t = family.table
    keys = [t.c[k] for k in family.keys]
    where = [t.c.stock_id.in_(stocks)]
    if i.source is not None:
        where.append(t.c.source.in_(sorted({i.source(s) for s in series})))
    if i.source is None:
        # A report's versions: its first publication, its latest version.
        found = []
        multi = sa.select(*keys).where(*where).group_by(*keys).having(sa.func.count() > 1)
        versions = connection.execute(
            sa.select(t.c.stock_id, *keys[1:], t.c.published_at, t.c.recorded_at)
            .where(sa.tuple_(*keys).in_(multi)).order_by(*keys, t.c.recorded_at)).all()
        by_key: dict[tuple, list] = {}
        for version in versions:
            by_key.setdefault(tuple(version[:len(keys)]), []).append(version)
        for (stock_id, *_), vs in by_key.items():
            first = vs[0].published_at
            if first is not None:
                found.append((stock_id, first.astimezone(_TAIPEI).date(), vs[-1].recorded_at))
    else:
        period = family.period(t)
        where.append(period <= end)
        multi = sa.select(*keys).where(*where).group_by(*keys).having(sa.func.count() > 1)
        inner = sa.select(*keys, t.c.recorded_at, family.available_at().label("available_at")) \
            .where(sa.tuple_(*keys).in_(multi)).subquery()
        latest = (sa.select(inner.c.stock_id, inner.c.source, inner.c[period.name],
                            inner.c.available_at)
                  .order_by(*(inner.c[k] for k in family.keys), inner.c.recorded_at.desc())
                  .distinct(*(inner.c[k] for k in family.keys)))
        found = [((stock_id, source), day, at)
                 for stock_id, source, day, at in connection.execute(latest)]
    grouped: dict = {}
    for key, day, at in sorted(found, key=lambda f: (f[0], f[1])):
        dates, instants = grouped.setdefault(key, ([], []))
        if i.scope == "history" and instants:
            at = max(at, instants[-1])
        dates.append(day)
        instants.append(at)
    return grouped
