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
from itertools import pairwise
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
         sources: Sequence[str] | None = None,
         index_names: Sequence[str] | None = None) -> list[sa.RowMapping]:
    """Each key's row as `pit` sees it, for keys whose date is in [start, end].

    `stock_ids`, `sources` and `index_names` narrow the keys; a filter never
    changes which row a key's PIT context sees.

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
    if index_names is not None:
        if "index_name" not in t.c:
            raise ValueError(f"{dataset} has no index name")
        inner = inner.where(t.c.index_name.in_(list(index_names)))
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


# ---------------------------------------------------------------- industry periods (Step 39-c)
#
# A listing span's industry periods are derived at query time, never stored
# (ADR-0030 §3): from the changes its market's exchange announced
# (`industry_changes`) and the anchor of its last period — today's ISIN category
# (`stocks.industry`) for an open span, the by-category quote of its last trading
# day (`industry_observations`) for one that ended.
#
# The chain is built from what was recorded by knowledge_as_of (system_as_of):
# the period before the first change has that change's old category and is
# public from its own start, because the category in use was public (owner
# decision 3); each change's period is public from its notice's release instant,
# a correction from when it was recorded (decision 2). Under market PIT a period
# not yet public is left out, and so is its start as the end of the one before:
# before a change is public, the period it will end has no end. A span's last
# period ends at its delisting once that day has come. A period never lies
# outside its category's existence (`industry.existence`), and 16 is split where
# 觀光事業 became 觀光餐旅, public from the day after both exchanges' notice.

@dataclass(frozen=True, slots=True)
class Span:
    stock_id: str
    market: str
    start: date
    end: date | None


@dataclass(frozen=True, slots=True)
class ChangeVersion:
    """One stored row of an announced change, with its computed `available_at`."""

    source: str
    announced_on: date
    old_code: str
    new_code: str
    available_at: datetime
    recorded_at: datetime
    fetch_id: object
    attachment_fetch_id: object | None


@dataclass(frozen=True, slots=True)
class Anchor:
    code: str
    source: str
    recorded_at: datetime
    fetch_id: object
    on: date | None = None  # a quote's trade date; None for today's ISIN list


@dataclass(frozen=True, slots=True)
class IndustryPeriod:
    stock_id: str
    market: str
    source: str
    basis: str  # before_change, change, anchor, rename
    industry_code: str
    industry_name: str
    effective_from: date
    effective_to: date | None
    available_at: datetime
    recorded_at: datetime
    fetch_id: object
    attachment_fetch_id: object | None


def _midnight(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), _TAIPEI)


def _known_by(pit: PIT) -> datetime:
    return pit.knowledge_as_of if isinstance(pit, MarketPIT) else pit.system_as_of


def _within(span: Span, day: date) -> bool:
    return span.start < day and (span.end is None or day < span.end)


def _recorded(span: Span, changes: dict[date, list[ChangeVersion]],
              known: datetime) -> dict[date, list[ChangeVersion]]:
    kept = {day: [v for v in versions if v.recorded_at <= known]
            for day, versions in changes.items() if _within(span, day)}
    return {day: versions for day, versions in sorted(kept.items()) if versions}


def industry_periods_of(span: Span, changes: dict[date, list[ChangeVersion]],
                        anchor: Anchor | None, pit: PIT) -> list[IndustryPeriod]:
    """One span's periods as `pit` sees them. `changes` maps each effective date
    to its versions in recording order."""
    from stock_data_center.v2 import industry
    from stock_data_center.v2.release_rules import industry_announcement_available_from

    known = _known_by(pit)
    market_pit = isinstance(pit, MarketPIT)
    recorded = _recorded(span, changes, known)
    if anchor is not None and anchor.recorded_at > known:
        anchor = None
    # (from, code, basis, available_at, row) of each period that can be seen.
    drafts = []
    if recorded:
        # The old category as public then: a correction of it counts from its
        # own available_at; before the notice is public, the original's.
        versions = next(iter(recorded.values()))
        public = [v for v in versions if not market_pit or v.available_at <= pit.information_as_of]
        first = public[-1] if public else versions[0]
        drafts.append((span.start, first.old_code, "before_change", _midnight(span.start), first))
        for day, versions in recorded.items():
            public = [v for v in versions if not market_pit or v.available_at <= pit.information_as_of]
            if public:
                drafts.append((day, public[-1].new_code, "change", public[-1].available_at,
                               public[-1]))
    elif anchor is not None:
        drafts.append((span.start, anchor.code, "anchor", _midnight(span.start), anchor))

    # Clip each to its category's existence, then split 16 at its rename. A
    # piece's end is public on its own only when it is the delisting (from that
    # day) or a change of the categories themselves (from their notice); a
    # change's start ends the piece before only once the change is public.
    categories_public = industry_announcement_available_from(industry.CATEGORIES_ANNOUNCED_2023)
    pieces = []
    for n, (since, code, basis, available, row) in enumerate(drafts):
        until = drafts[n + 1][0] if n + 1 < len(drafts) else span.end
        end_public = None if n + 1 < len(drafts) or span.end is None else _midnight(span.end)
        exists_from, exists_until = industry.existence(code, span.market)
        if exists_from is not None and since < exists_from:
            since, available = exists_from, max(available, _midnight(exists_from))
        if exists_until is not None and (until is None or exists_until < until):
            until, end_public = exists_until, categories_public
        if until is not None and since >= until:
            continue
        for day, _ in industry.FORMER_NAMES.get(code, []):
            if since < day and (until is None or day < until):
                pieces.append((since, day, code, basis, available, row, categories_public))
                since, basis = day, "rename"
                available = max(available, categories_public)
        pieces.append((since, until, code, basis, available, row, end_public))
    if market_pit:
        pieces = [p for p in pieces if p[4] <= pit.information_as_of]
    out = []
    for n, (since, until, code, basis, available, row, end_public) in enumerate(pieces):
        ceased = (until is not None and end_public is not None
                  and (not market_pit or end_public <= pit.information_as_of))
        if n + 1 < len(pieces):
            if not (ceased and until < pieces[n + 1][0]):  # a category that ceased keeps its end
                until = pieces[n + 1][0]
        elif end_public is None or (market_pit and end_public > pit.information_as_of):
            until = None
        out.append(IndustryPeriod(
            stock_id=span.stock_id, market=span.market, source=row.source, basis=basis,
            industry_code=code, industry_name=industry.name_of(code, since),
            effective_from=since, effective_to=until,
            available_at=available.astimezone(_TAIPEI), recorded_at=row.recorded_at,
            fetch_id=row.fetch_id, attachment_fetch_id=getattr(row, "attachment_fetch_id", None)))
    return out


def industry_chain_issues(span: Span, changes: dict[date, list[ChangeVersion]],
                          anchor: Anchor | None) -> list[str]:
    """Where a span's latest chain does not hold together (ADR-0030 §6)."""
    from stock_data_center.v2 import industry

    recorded = _recorded(span, changes, FOREVER)
    issues = [f"{day} is outside the span {span.start}..{span.end}"
              for day in sorted(changes) if not _within(span, day)]
    days = list(recorded)
    for before, after in pairwise(days):
        left, starts = recorded[before][-1].new_code, recorded[after][-1].old_code
        if left != starts:
            issues.append(f"{after} starts from {starts}, but {before} left it in {left}")
    if days and anchor is not None:
        if anchor.on is None:
            said, where = recorded[days[-1]][-1].new_code, "the last period is"
        else:  # a quote anchors the day it was quoted, which may precede a change
            later = [d for d in days if d > anchor.on]
            said = (recorded[later[0]][-1].old_code if later
                    else recorded[days[-1]][-1].new_code)
            where = f"on {anchor.on} the chain says"
        if said != anchor.code:
            issues.append(f"{where} {said}, but the anchor ({anchor.source}) says {anchor.code}")
    bounds = [span.start, *days, span.end]
    codes = ([recorded[days[0]][-1].old_code] + [recorded[d][-1].new_code for d in days]
             if days else [anchor.code] if anchor is not None else [])
    for code, since, until in zip(codes, bounds, bounds[1:]):
        exists_from, exists_until = industry.existence(code, span.market)
        if exists_from is not None and since < exists_from:
            issues.append(f"{code} did not exist on {span.market} from {since}; "
                          f"its period starts {exists_from}")
        if exists_until is not None and (until is None or exists_until < until):
            issues.append(f"{code} did not exist on {span.market} until {until}; "
                          f"its period ends {exists_until}")
    return issues


# Which exchange announces a market's changes, and whose quotes anchor its ended spans.
INDUSTRY_ANNOUNCEMENTS = {"sii": "twse_announcement", "otc": "tpex_announcement"}
INDUSTRY_QUOTES = {"sii": "twse_mi_index", "otc": "tpex_otc_quotes"}


def industry_inputs(connection: Connection, pit: PIT, *,
                    stock_ids: Sequence[str] | None = None) -> list[tuple]:
    """Each listing span with its changes and its anchor as recorded by `pit`'s
    knowledge (system) instant: [(span, changes, anchor)]."""
    from stock_data_center.v2 import industry
    from stock_data_center.v2.release_rules import industry_announcement_available_from

    known = _known_by(pit)
    spans_t, changes_t, obs_t = v2.listings, v2.industry_changes, v2.industry_observations
    stocks_t, fetches_t = v2.stocks, v2.fetches

    def narrowed(query, table):
        return query if stock_ids is None else query.where(table.c.stock_id.in_(list(stock_ids)))

    spans = [Span(r.stock_id, r.market, max(r.listed_on or industry.WINDOW_START,
                                            industry.WINDOW_START), r.delisted_on)
             for r in connection.execute(narrowed(sa.select(spans_t), spans_t)
                                         .order_by(spans_t.c.stock_id, spans_t.c.market,
                                                   spans_t.c.listed_on.asc().nulls_first()))]
    # A change's available_at, as for every rule family: a row recorded before
    # its notice's release instant, and the first one at or after it, are
    # available at the instant; every later row is a correction.
    changes: dict[tuple[str, str], dict[date, list[ChangeVersion]]] = {}
    rows = connection.execute(narrowed(sa.select(changes_t), changes_t).order_by(
        changes_t.c.stock_id, changes_t.c.source, changes_t.c.effective_date,
        changes_t.c.recorded_at)).mappings()
    market_of = {source: market for market, source in INDUSTRY_ANNOUNCEMENTS.items()}
    for r in rows:
        versions = changes.setdefault((r["stock_id"], market_of[r["source"]]), {}) \
            .setdefault(r["effective_date"], [])
        released = industry_announcement_available_from(r["announced_on"])
        settled = any(v.recorded_at >= released for v in versions)
        available = r["recorded_at"] if settled and r["recorded_at"] >= released else released
        versions.append(ChangeVersion(
            r["source"], r["announced_on"], industry.code_of(r["old_industry"]),
            industry.code_of(r["new_industry"]), available, r["recorded_at"], r["fetch_id"],
            r["attachment_fetch_id"]))
    # Open spans: today's ISIN category, known from the fetch that stored it.
    isin = {r.stock_id: Anchor(industry.code_of(r.industry), f.source, f.fetched_at, r.fetch_id)
            for r, f in (
                (row, row) for row in connection.execute(narrowed(
                    sa.select(stocks_t.c.stock_id, stocks_t.c.industry, stocks_t.c.fetch_id,
                              fetches_t.c.source, fetches_t.c.fetched_at)
                    .join(fetches_t, fetches_t.c.id == stocks_t.c.fetch_id)
                    .where(stocks_t.c.industry.is_not(None)), stocks_t)))
            if industry.code_of(r.industry) is not None}
    # Ended spans: the last by-category quote inside the span, as known then.
    ended = sorted({s.stock_id for s in spans if s.end is not None})
    quoted: dict[tuple[str, str], list] = {}
    if ended:
        for r in connection.execute(
                sa.select(obs_t).where(obs_t.c.stock_id.in_(ended), obs_t.c.recorded_at <= known)
                .order_by(obs_t.c.stock_id, obs_t.c.source, obs_t.c.trade_date,
                          obs_t.c.recorded_at.desc())
                .distinct(obs_t.c.stock_id, obs_t.c.source, obs_t.c.trade_date)).mappings():
            quoted.setdefault((r["stock_id"], r["source"]), []).append(r)
    out = []
    for span in spans:
        if span.end is None:
            anchor = isin.get(span.stock_id)
        else:
            inside = [r for r in quoted.get((span.stock_id, INDUSTRY_QUOTES[span.market]), [])
                      if span.start <= r["trade_date"] < span.end]
            last = inside[-1] if inside else None
            anchor = None if last is None else Anchor(
                last["industry_code"], last["source"], last["recorded_at"], last["fetch_id"],
                last["trade_date"])
        out.append((span, changes.get((span.stock_id, span.market), {}), anchor))
    return out


def industry_periods(connection: Connection, pit: PIT, *, start: date, end: date,
                     stock_ids: Sequence[str] | None = None) -> list[IndustryPeriod]:
    """Every period overlapping [start, end] as `pit` sees it, by stock, market and start."""
    found = [p for span, changes, anchor in industry_inputs(connection, pit, stock_ids=stock_ids)
             for p in industry_periods_of(span, changes, anchor, pit)
             if p.effective_from <= end and (p.effective_to is None or p.effective_to > start)]
    return sorted(found, key=lambda p: (p.stock_id, p.effective_from, p.market))
