"""The exchange-daily write path on schema v2 (ROADMAP Step 35-b-1, ADR-0027).

One job per (table, source). A job reuses the existing adapter unchanged: its
`resource(request)` names the request and its `parse(content, request)` reads
the file. What is new is everything after the parse:

    fetch -> raw file on disk -> parse -> keep `stocks` members
          -> compare with each key's latest row -> append only what changed

and one `fetches` row per attempt, whatever its outcome. The fetch row and the
rows it produced commit together, so a `succeeded` fetch always has its rows,
and resuming is just skipping resources whose latest fetch succeeded (`pending`).

Publication time is not stored. Every job here follows one release rule,
`exchange_daily_settled@1`: trade date D is public at 03:00 Asia/Taipei on
D+1. The settled value is available from that instant; a later, different
value from its own `recorded_at` (`visible`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db import schema_v2 as v2
from stock_data_center.ingestion import adapters as a
from stock_data_center.ingestion import models as m
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch
from stock_data_center.v2.indices import KEPT_INDICES

# ---------------------------------------------------------------- release rule


@dataclass(frozen=True, slots=True)
class ReleaseRule:
    """A versioned schedule. Correcting one means adding a version."""

    rule_id: str
    version: int
    days_after: int
    at: time
    timezone: str
    authority: str


RELEASE_RULE = ReleaseRule(
    rule_id="exchange_daily_settled",
    version=1,
    days_after=1,
    at=time(3, 0),
    timezone="Asia/Taipei",
    # Registered as release_rules row exchange_daily_settled@1 (ADR-0020 §3).
    authority=(
        "Owner decision (ADR-0020 §3, decision 1). The exchange serves same-day "
        "rows before they settle (audit §7), so no rule may resolve on the trade "
        "date; the legacy 23:30 run was incomplete on 5 of 27 observed trade "
        "dates and the 03:00 retry on 1 of 27 (audit §7.2). No business-day "
        "shift: the file exists at 03:00 whether or not that day is a trading day."
    ),
)


def available_from(trade_date: date, rule: ReleaseRule = RELEASE_RULE) -> datetime:
    local = datetime.combine(
        trade_date + timedelta(days=rule.days_after), rule.at, tzinfo=ZoneInfo(rule.timezone)
    )
    return local.astimezone(UTC)


def available_from_sql(trade_date, rule: ReleaseRule = RELEASE_RULE):
    """The same instant in SQL, for filtering stored rows."""
    offset = timedelta(days=rule.days_after, hours=rule.at.hour, minutes=rule.at.minute)
    return sa.func.timezone(rule.timezone, sa.cast(trade_date, sa.Date) + offset)


# ---------------------------------------------------------------- row mapping


def whole(value) -> int | None:
    """A share count or amount as an int; a fraction is refused, never truncated."""
    if value is None:
        return None
    value = getattr(value, "value", value)  # ShareQuantity, TwdAmount
    if isinstance(value, int):
        return value
    if value != value.to_integral_value():
        raise ValueError(f"{value} is not a whole number")
    return int(value)


def _converter(column: sa.Column) -> Callable:
    if isinstance(column.type, sa.Integer):  # BigInteger and SmallInteger too
        return whole
    return lambda value: value


def _stock_rows(table: sa.Table, renamed: dict[str, str] | None = None):
    """Rows of a per-stock file: `security_code` plus one observation each."""
    renamed = renamed or {}
    columns = [c for c in table.columns if c.name not in _BOOKKEEPING + _STOCK_KEY]
    plan = [(c.name, renamed.get(c.name, c.name), _converter(c)) for c in columns]

    def rows(parsed, source: str) -> list[dict]:
        return [
            {
                "stock_id": row.security_code,
                "source": source,
                "trade_date": row.observation.trade_date,
                **{name: convert(getattr(row.observation, attr)) for name, attr, convert in plan},
            }
            for row in parsed.rows
        ]

    return rows


def _market_flow_rows(parsed, source: str) -> list[dict]:
    return [
        {
            "source": source,
            "trade_date": row.trade_date,
            "institution": row.institution,
            "buy": whole(row.buy),
            "sell": whole(row.sell),
            "net": whole(row.net),
        }
        for row in parsed.rows
    ]


_INDEX_VALUES = (
    "open_value", "high_value", "low_value", "close_value", "change_points", "change_percent"
)


def _index_row(source: str, name: str, trade_date: date, observation) -> dict:
    return {
        "source": source,
        "index_name": name,
        "trade_date": trade_date,
        **{column: getattr(observation, column) for column in _INDEX_VALUES},
    }


def _index_rows(parsed, source: str) -> list[dict]:
    """Every published index whose `section:name` is on the kept list."""
    kept = KEPT_INDICES[source]
    return [
        _index_row(source, name, parsed.trade_date, row.observation)
        for row in parsed.rows
        if (name := f"{row.section}:{row.index_name}") in kept
    ]


def _taiex_rows(parsed, source: str) -> list[dict]:
    # MI_5MINS_HIST publishes no section label; v1 named it `指數`.
    name = f"指數:{parsed.index_name}"
    if name not in KEPT_INDICES[source]:
        raise m.SourceDataError("unrecognised_value", f"{name!r} is not a kept index")
    return [_index_row(source, name, row.trade_date, row.observation) for row in parsed.rows]


# The TAIEX close in MI_5MINS_HIST must equal the MI_INDEX list close (CLAUDE.md §52).
TAIEX_LIST_NAME = "指數/臺灣證券交易所:發行量加權股價指數"


@dataclass(frozen=True, slots=True)
class Checked:
    """A job check's verdict: the rows it could verify, and any disagreement."""

    rows: list[dict]
    mismatch: str | None = None
    unverified: tuple[date, ...] = ()


def _taiex_close_check(connection: Connection, rows: list[dict]) -> Checked:
    """Compare each date's close with the stored MI_INDEX close.

    A date the list does not hold yet is not written: letting it through would
    make the check depend on which job ran first (CLAUDE.md §30, §52). The
    fetch is logged `close_unverified`, so the month stays pending and the
    next run checks it once the list has the date."""
    listed = _latest(
        connection,
        v2.index_prices,
        source="twse_mi_index",
        dates={row["trade_date"] for row in rows},
        extra=v2.index_prices.c.index_name == TAIEX_LIST_NAME,
    )
    verified, unverified, wrong = [], [], []
    for row in rows:
        old = listed.get(("twse_mi_index", TAIEX_LIST_NAME, row["trade_date"]))
        if old is None:
            unverified.append(row["trade_date"])
        elif old["close_value"] != row["close_value"]:
            wrong.append(
                f"{row['trade_date'].isoformat()}: {row['close_value']} vs {old['close_value']}"
            )
        else:
            verified.append(row)
    return Checked(verified, "; ".join(wrong) or None, tuple(sorted(unverified)))


# Written by the runner, never compared: a row differs from the key's latest row
# only in its published values.
_BOOKKEEPING = ("recorded_at", "fetch_id", "published_at")
_STOCK_KEY = ("stock_id", "source", "trade_date")


def key_columns(table: sa.Table) -> tuple[str, ...]:
    return tuple(c.name for c in table.primary_key.columns if c.name != "recorded_at")


def value_columns(table: sa.Table) -> tuple[str, ...]:
    keys = set(key_columns(table))
    return tuple(c.name for c in table.columns if c.name not in keys | set(_BOOKKEEPING))


# ---------------------------------------------------------------- jobs


@dataclass(frozen=True)
class Job:
    table: sa.Table
    adapter: object
    request_type: type
    rows_of: Callable[[object, str], list[dict]]
    monthly: bool = False
    check: Callable[[Connection, list[dict]], Checked] | None = None
    release_rule: ReleaseRule = RELEASE_RULE
    # The key's date column; the other defaults describe an exchange-daily job.
    period_column: str = "trade_date"
    # A job whose key names more than table and source (a market's second page).
    variant: str = ""
    # Builds the request instead of `request_type(period)`.
    request_of: Callable[[date | None], object] | None = None
    # When a period's file stops changing, if not the release rule instant.
    settled_of: Callable[[date], datetime] | None = None
    # A source that serves only its latest file: every run fetches it once.
    latest_only: bool = False
    key_columns: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "key_columns", key_columns(self.table))

    @property
    def key(self) -> str:
        return f"{self.table.name}/{self.source}" + (f"/{self.variant}" if self.variant else "")

    @property
    def source(self) -> str:
        return self.adapter.source

    @property
    def dataset(self) -> str:
        # The v1 dataset code, so the fetch log continues the migrated history.
        return self.adapter.dataset_code

    @property
    def per_stock(self) -> bool:
        return "stock_id" in self.table.c

    def request(self, period: date | None):
        if self.request_of is not None:
            return self.request_of(period)
        return self.request_type(period.replace(day=1) if self.monthly else period)

    def rows(self, parsed) -> list[dict]:
        return self.rows_of(parsed, self.source)

    def release_rule_instant(self, trade_date: date) -> datetime:
        return available_from(trade_date, self.release_rule)

    def settled_at(self, period: date) -> datetime:
        """The rule instant of the last trade date a period's file can hold."""
        if self.settled_of is not None:
            return self.settled_of(period)
        if self.monthly:
            period = (period.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return self.release_rule_instant(period)


def _jobs() -> dict[str, Job]:
    margin = _stock_rows(
        v2.margin_trading, {"margin_limit": "margin_next_limit", "short_limit": "short_next_limit"}
    )
    lending = _stock_rows(v2.securities_lending, {"sold": "borrowed"})
    jobs = [
        *(
            Job(v2.daily_prices, adapter, m.WholeMarketDailyRequest, _stock_rows(v2.daily_prices))
            for adapter in (a.TWSEWholeMarketDailyAdapter(), a.TPExWholeMarketDailyAdapter())
        ),
        *(
            Job(v2.valuations, adapter, m.OfficialValuationRequest, _stock_rows(v2.valuations))
            for adapter in (a.TWSEOfficialValuationAdapter(), a.TPExOfficialValuationAdapter())
        ),
        *(
            Job(v2.institutional_flows, adapter, m.InstitutionalInvestorRequest,
                _stock_rows(v2.institutional_flows))
            for adapter in (
                a.TWSEInstitutionalInvestorAdapter(), a.TPExInstitutionalInvestorAdapter()
            )
        ),
        *(
            Job(v2.institutional_market_flows, adapter, m.InstitutionalMarketSummaryRequest,
                _market_flow_rows)
            for adapter in (
                a.TWSEInstitutionalMarketSummaryAdapter(),
                a.TPExInstitutionalMarketSummaryAdapter(),
            )
        ),
        *(
            Job(v2.foreign_holdings, adapter, m.ForeignHoldingRequest,
                _stock_rows(v2.foreign_holdings))
            for adapter in (a.TWSEForeignHoldingAdapter(), a.MOPSForeignHoldingAdapter())
        ),
        *(
            Job(v2.margin_trading, adapter, m.MarginTradingRequest, margin)
            for adapter in (a.TWSEMarginTradingAdapter(), a.TPExMarginTradingAdapter())
        ),
        *(
            Job(v2.securities_lending, adapter, m.SecuritiesLendingRequest, lending)
            for adapter in (a.TWSESecuritiesLendingAdapter(), a.TPExSecuritiesLendingAdapter())
        ),
        *(
            Job(v2.index_prices, adapter, m.MarketIndexRequest, _index_rows)
            for adapter in (a.TWSEMarketIndexAdapter(), a.TPExMarketIndexAdapter())
        ),
        Job(v2.index_prices, a.TWSETaiexHistoryAdapter(), m.TaiexHistoryRequest, _taiex_rows,
            monthly=True, check=_taiex_close_check),
    ]
    return {job.key: job for job in jobs}


JOBS: dict[str, Job] = _jobs()


# ---------------------------------------------------------------- ingest

# Content that is not a source answer at all (an HTML maintenance page): an
# operational failure, retried once live, never a quarantine (Step 19-d).
_RETRY_LIVE = frozenset({"invalid_json", "unusable_response"})
_EMPTY = frozenset({"no_data_for_date"})
# A succeeded fetch that held rows back is not done: the next run asks again.
_UNFINISHED = frozenset({"close_unverified"})


@dataclass(frozen=True, slots=True)
class Outcome:
    status: str
    fetch_id: object
    parsed: int = 0
    appended: int = 0
    unchanged: int = 0
    out_of_scope: int = 0
    unverified: int = 0
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class Fetched:
    """A fetched, stored and parsed resource whose fetch row is not written yet.

    `log(status, reason, detail)` writes it, in the caller's transaction, once
    the caller knows the outcome of its write."""

    parsed: object
    value: object
    fetched_at: datetime
    log: Callable[..., object]


def fetch_and_parse(
    connection: Connection,
    adapter,
    request,
    *,
    dataset: str,
    parse: Callable[[object], object],
    fetcher: SourceFetcher,
    git_commit: str,
    purpose: str,
    store: LocalRawArtifactStore | None = None,
    empty: frozenset[str] = frozenset({"no_data_for_date"}),
) -> Fetched | Outcome:
    """Fetch one resource, keep its raw file, and parse it.

    Every outcome but success is logged here and returned as an `Outcome`:
    a failed request, a maintenance page (retried once live), an empty
    answer, a quarantine. `parse(parsed)` turns the adapter's result into the
    caller's rows; a `ValueError` from it quarantines like the adapter's own."""
    store = store or LocalRawArtifactStore()
    resource = adapter.resource(request)
    record = FetchRecord(
        dataset=dataset,
        source=adapter.source,
        resource_key=resource.resource_key,
        source_uri=resource.source_uri,
        purpose=purpose,
        adapter_version=adapter.version,
        git_commit=git_commit,
        fetched_at=datetime.now(UTC),
    )

    def logger(record, content, attempt):
        def log(status, reason=None, detail=None):
            return record_fetch(
                connection, record, content=content, status=status, store=store,
                reason_code=reason, reason_detail=detail and detail[:2000], attempt=attempt,
            )
        return log

    for attempt in (1, 2):
        record = replace(record, fetched_at=datetime.now(UTC))
        try:
            fetched = fetcher.fetch(resource)
        except (httpx.HTTPError, OSError) as error:
            fetch_id = logger(record, None, attempt)("failed", "fetch_error", repr(error))
            return Outcome("failed", fetch_id, reason_code="fetch_error")
        content = fetched.content
        store.put(content)  # raw-first: kept before anything parses it
        record = replace(record, source_uri=fetched.source_uri, fetched_at=fetched.fetched_at)
        log = logger(record, content, attempt)
        try:
            parsed = adapter.parse(content, request)
            value = parse(parsed)
        except m.SourceDataError as error:
            code = error.reason_code
            if code in _RETRY_LIVE:
                fetch_id = log("failed", reason=code, detail=str(error))
                if attempt == 1:
                    continue
                return Outcome("failed", fetch_id, reason_code=code)
            status = "empty" if code in empty else "quarantined"
            return Outcome(status, log(status, reason=code, detail=str(error)), reason_code=code)
        except (ValueError, ArithmeticError) as error:
            fetch_id = log("quarantined", reason="unrecognised_value", detail=str(error))
            return Outcome("quarantined", fetch_id, reason_code="unrecognised_value")
        return Fetched(parsed, value, record.fetched_at, log)
    raise AssertionError("unreachable")  # pragma: no cover


def ingest(
    connection: Connection,
    job: Job,
    period: date | None,
    *,
    fetcher: SourceFetcher,
    git_commit: str,
    purpose: str,
    store: LocalRawArtifactStore | None = None,
    stock_ids: frozenset[str] | None = None,
) -> Outcome:
    """Fetch one resource and write what changed, on `connection`'s transaction."""
    fetched = fetch_and_parse(
        connection, job.adapter, job.request(period), dataset=job.dataset, parse=job.rows,
        fetcher=fetcher, git_commit=git_commit, purpose=purpose, store=store, empty=_EMPTY,
    )
    if isinstance(fetched, Outcome):
        return fetched
    first_seen = fetched.fetched_at if purpose == "first_capture" else None
    return _write(connection, job, fetched.value, fetched.parsed, fetched.log, stock_ids,
                  first_seen)


def _write(connection, job, rows, parsed, log, stock_ids, first_seen) -> Outcome:
    parsed_count = len(rows)
    if job.per_stock:
        if stock_ids is None:
            stock_ids = frozenset(connection.scalars(sa.select(v2.stocks.c.stock_id)))
        rows = [row for row in rows if row["stock_id"] in stock_ids]
    out_of_scope = parsed_count - len(rows)

    keys = [tuple(row[c] for c in job.key_columns) for row in rows]
    if len(keys) != len(set(keys)):
        fetch_id = log("quarantined", reason="duplicate_key", detail="a key appears twice")
        return Outcome("quarantined", fetch_id, parsed_count, reason_code="duplicate_key")
    unverified: tuple[date, ...] = ()
    if job.check and rows:
        checked = job.check(connection, rows)
        if checked.mismatch:
            fetch_id = log("quarantined", reason="close_mismatch", detail=checked.mismatch)
            return Outcome("quarantined", fetch_id, parsed_count, reason_code="close_mismatch")
        rows, unverified = checked.rows, checked.unverified
        keys = [tuple(row[c] for c in job.key_columns) for row in rows]

    # Two writers of one source would both see "no latest row" and both append.
    connection.execute(
        sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(job.key)))
    )
    rejected = getattr(parsed, "rejected", ())
    if unverified:
        reason = "close_unverified"
        detail = "no list close yet: " + ", ".join(d.isoformat() for d in unverified)
    else:
        reason = "rows_rejected" if rejected else None
        detail = "; ".join(f"{r.security_code}: {r.reason_code}" for r in rejected) or None
    fetch_id = log("succeeded", reason=reason, detail=detail)
    latest = _latest(
        connection, job.table, source=job.source,
        dates={row[job.period_column] for row in rows}, column=job.period_column,
    )
    values = value_columns(job.table)
    changed = [
        {**row, "fetch_id": fetch_id}
        for row, key in zip(rows, keys, strict=True)
        if (old := latest.get(key)) is None or any(row[c] != old[c] for c in values)
    ]
    if "published_at" in job.table.c:
        # A key's first row seen by a first capture was public by that fetch; a
        # gap fill proves nothing (CLAUDE.md §32), and a later row is a
        # correction, public from its own recorded_at.
        for row, key in zip(changed, (tuple(r[c] for c in job.key_columns) for r in changed),
                            strict=True):
            row["published_at"] = first_seen if key not in latest else None
    if changed:
        connection.execute(sa.insert(job.table), changed)
    return Outcome(
        "succeeded", fetch_id, parsed_count, len(changed), len(rows) - len(changed),
        out_of_scope, len(unverified), reason,
    )


def _latest(
    connection, table, *, source, dates: Iterable[date], extra=None, column="trade_date"
) -> dict:
    """Each key's latest row for these dates, by key tuple."""
    dates = sorted(set(dates))
    if not dates:
        return {}
    keys = key_columns(table)
    query = (
        sa.select(table)
        .where(table.c.source == source, table.c[column].in_(dates))
        .order_by(*(table.c[k] for k in keys), table.c.recorded_at.desc())
        .distinct(*(table.c[k] for k in keys))
    )
    if extra is not None:
        query = query.where(extra)
    return {
        tuple(row[k] for k in keys): row for row in connection.execute(query).mappings()
    }


def pending(connection: Connection, job: Job, periods: Sequence[date]) -> list[date]:
    """The periods not yet done: done means the latest fetch succeeded, held
    no row back, and was made after the period's last trade date settled.

    A completeness rule, not a visibility one: a file fetched before D+1 03:00
    can still change (audit §7), and a current-month TAIEX file still lacks the
    rest of the month, so either is fetched again by the next run. An empty
    answer is not done either: periods come from `trading_days`, and no trading
    day in 2020-2026 answered empty, so it is a gap to ask again. What a client
    sees is decided at read time by `visible`."""
    by_key = {job.adapter.resource(job.request(p)).resource_key: p for p in periods}
    f = v2.fetches
    latest = (
        connection.execute(
            sa.select(f.c.resource_key, f.c.status, f.c.reason_code, f.c.fetched_at)
            .where(f.c.dataset == job.dataset, f.c.source == job.source,
                   f.c.resource_key.in_(list(by_key)))
            .order_by(f.c.resource_key, f.c.fetched_at.desc(), f.c.attempt.desc())
            .distinct(f.c.resource_key)
        ).all()
    )
    finished = {
        key for key, status, reason, fetched_at in latest
        if status == "succeeded" and reason not in _UNFINISHED
        and fetched_at >= job.settled_at(by_key[key])
    }
    return [period for key, period in by_key.items() if key not in finished]


# ---------------------------------------------------------------- visibility


def visible(
    connection: Connection,
    table: sa.Table,
    *,
    as_of: datetime,
    start: date,
    end: date,
    source: str | None = None,
    rule: ReleaseRule = RELEASE_RULE,
) -> list[sa.RowMapping]:
    """Each key's value as the market could know it at `as_of`.

    The rule says the settled file is public at its instant, so the first row
    recorded at or after it is the settled value, available from the instant
    however late the Data Center recorded it. A row recorded before the instant
    is provisional: available from the instant too, but the settled row, being
    recorded later, supersedes it there. Every row after the settled one is a
    correction, available from its own `recorded_at`.
    """
    keys = [table.c[k] for k in key_columns(table)]
    released = available_from_sql(table.c.trade_date, rule)
    recorded = table.c.recorded_at
    settled = sa.func.min(recorded).filter(recorded >= released).over(partition_by=keys)
    available = sa.case(
        (recorded < released, released),
        (recorded == settled, released),
        else_=recorded,
    )
    inner = sa.select(table, available.label("available_at")).where(
        table.c.trade_date.between(start, end)
    )
    if source is not None:
        inner = inner.where(table.c.source == source)
    inner = inner.subquery()
    outer_keys = [inner.c[k.name] for k in keys]
    query = (
        sa.select(*(inner.c[c.name] for c in table.columns), inner.c.available_at)
        .where(inner.c.available_at <= as_of)
        .order_by(*outer_keys, inner.c.recorded_at.desc())
        .distinct(*outer_keys)
    )
    return list(connection.execute(query).mappings())

