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
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.sql.elements import ColumnElement

from stock_data_center.db import schema_v2 as v2
from stock_data_center.v2.exchange_daily import available_from_sql, key_columns
from stock_data_center.v2.release_rules import (
    corporate_action_available_from_sql,
    tdcc_available_from_sql,
)

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


def report_facts(connection: Connection,
                 report_ids: Iterable[int]) -> dict[int, list[sa.RowMapping]]:
    """Each report version's own facts, by statement, account and period."""
    ids = list(report_ids)
    out: dict[int, list[sa.RowMapping]] = {report_id: [] for report_id in ids}
    if not ids:
        return out
    f = v2.financial_report_facts
    for fact in connection.execute(
            sa.select(f).where(f.c.report_id.in_(ids))
            .order_by(f.c.report_id, f.c.statement, f.c.account_code, f.c.period_start,
                      f.c.period_end, f.c.concept)).mappings():
        out[fact["report_id"]].append(fact)
    return out
