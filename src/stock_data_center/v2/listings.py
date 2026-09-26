"""Every company's listing spans (Step 38-a, ADR-0028).

`stocks` holds a company's identity and `listings` each span it traded on one
market: `[listed_on, delisted_on)`. The spans come from the exchanges' own
listing and delisting tables (audit §4.11), which were checked against the
stored trades: of 320 listings since 2020, 315 dates equal the first trade and
the other 5 are halts or typhoon closures; every delisting date is the first
day without a trade.

- TWSE `company/newlisting` and `company/suspendListing`, each one table from
  2001; TPEx `company/latest` and `company/deListed`, one year per request from
  2005. TPEx serves a year's delistings ten at a time, so a year with more is
  asked again with the site's paging parameters. Every page must hold the total
  it declares.
- Today's ISIN list (`universe.parse_isin_page`) says which stocks are listed
  now. Its 上市日 is not a listing date: for 29 of the 1,238 stocks the tables
  also cover it is a later event's, so it only dates a move from the
  innovation board, which the tables do not show.
- The ISIN lookup (`class_main.jsp`) gives the category of a security still
  registered. It is asked only for a span whose listing the tables do not show.

The universe is still common stocks (ADR-0026): a span is kept only when an
official source says so. A listing row proves it unless it is the innovation
board's; otherwise the ISIN lookup must say 普通股 or 股票. A code's shape
proves nothing (9188 精熙-DR is a TDR), so an unproven span is quarantined.
Spans that closed before the v1 window are not kept.

The adapters' scope is `listed_stock_ids`: stocks with an open span. Adding a
delisted company therefore changes no adapter's requests (Step 38-b decides
whether to fetch their history).
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.v2.universe import (
    ISIN_URL,
    MARKETS,
    ListedStock,
    parse_isin_page,
)

WINDOW_START = date(2020, 1, 2)
FIRST_TPEX_YEAR = 2005
IN_SCOPE_CATEGORIES = frozenset({"普通股", "股票"})
INNOVATION_BOARD = "創新板"
PAUSE_SECONDS = 3.0
ADAPTER_VERSION = "listings:v1"
TAIPEI = ZoneInfo("Asia/Taipei")

TWSE_LISTED_URL = "https://www.twse.com.tw/rwd/zh/company/newlisting?response=json"
TWSE_DELISTED_URL = "https://www.twse.com.tw/rwd/zh/company/suspendListing?response=json"
TPEX_LISTED_URL = "https://www.tpex.org.tw/www/zh-tw/company/latest?response=json&date={year}"
TPEX_DELISTED_URL = "https://www.tpex.org.tw/www/zh-tw/company/deListed?response=json&date={year}"
# The delisting table is served ten rows at a time; the site's own pager asks
# for more with these parameters (`rsrc/js/tables.js`, `#pageViewOfServer`).
TPEX_DELISTED_PAGED_URL = TPEX_DELISTED_URL + "&paging-table=0&paging-size={size}&paging-offset=0"
ISIN_LOOKUP_URL = ("https://isin.twse.com.tw/isin/class_main.jsp?owncode={code}&stockname="
                   "&isincode=&market=&issuetype=&industry_code=&Page=1&chklike=N")

TWSE_LISTED_FIELDS = ["公司代號", "公司簡稱", "申請日期", "董事長", "申請時股本(仟元)",
                      "上市審議委員會審議日期", "交易所董事會通過上市日期",
                      "上市契約報請主管機關備查日期", "證期局核准上市契約日期",
                      "股票上市買賣日期", "承銷商", "承銷價", "備註"]
TWSE_DELISTED_FIELDS = ["終止上市日期", "公司名稱", "上市編號"]
TPEX_LISTED_FIELDS = ["索引", "股票代號", "公司名稱", "上櫃日期", "每股面額", "公司資訊連結",
                      "近期上櫃資訊連結"]
TPEX_DELISTED_FIELDS = ["股票代號", "公司名稱", "終止上櫃日期", "終止上櫃原因", "公司資料網址"]
ISIN_LOOKUP_FIELDS = ["頁面編號", "國際證券編碼", "有價證券代號", "有價證券名稱", "市場別",
                      "有價證券別", "產業別", "公開發行/上市(櫃)/發行日", "CFICode", "備註"]


class ListingFormatError(ValueError):
    """A page no longer has the shape this parser was written against."""


@dataclass(frozen=True, slots=True, order=True)
class Event:
    """One row of a listing or delisting table: `stock_id` joined or left `market` `on`."""

    stock_id: str
    market: str
    on: date
    name: str
    note: str = ""
    fetch_id: UUID | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class Lookup:
    """The ISIN lookup's row for a code still registered."""

    stock_id: str
    name: str
    market_label: str
    category: str
    industry: str | None
    registered_on: date | None = None  # 公開發行/上市(櫃)/發行日
    fetch_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Span:
    stock_id: str
    market: str
    listed_on: date | None
    delisted_on: date | None
    fetch_id: UUID | None = field(default=None, compare=False)
    listed_fetch_id: UUID | None = field(default=None, compare=False)
    delisted_fetch_id: UUID | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True, order=True)
class Company:
    stock_id: str
    name: str
    industry: str | None
    fetch_id: UUID | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class Left:
    """A span left out: `excluded` when a source says it is outside the universe,
    `quarantined` when nothing proves what it is."""

    stock_id: str
    market: str
    reason: str
    on: date | None


@dataclass
class Assembly:
    companies: list[Company]
    spans: list[Span]
    excluded: list[Left]
    quarantined: list[Left]
    warnings: list[Left]
    before_window: int


# ---------------------------------------------------------------- parsing


def _roc(text: str, separator: str) -> date:
    try:
        year, month, day = (int(part) for part in text.strip().split(separator))
        return date(year + 1911, month, day)
    except ValueError:
        raise ListingFormatError(f"not a ROC date with {separator!r}: {text!r}") from None


def _payload(content: bytes) -> dict:
    try:
        payload = json.loads(content)
    except ValueError as error:
        raise ListingFormatError(f"not JSON: {error}") from None
    if not isinstance(payload, dict):
        raise ListingFormatError("not a JSON object")
    return payload


def _check_fields(actual, expected: list[str], source: str) -> None:
    if actual != expected:
        raise ListingFormatError(f"{source} fields changed: {actual!r}")


def _complete(rows: list, total, source: str) -> list:
    """The rows, if they are every row the page declares."""
    if total is not None and total != len(rows):
        raise ListingFormatError(f"{source} returned {len(rows)} of {total} rows")
    return rows


def _code(value) -> str:
    code = str(value).strip()
    if not code:
        raise ListingFormatError("a row has no code")
    return code


def parse_twse_listed(content: bytes) -> list[Event]:
    payload = _payload(content)
    _check_fields(payload.get("fields"), TWSE_LISTED_FIELDS, "TWSE newlisting")
    rows = _complete(payload.get("data") or [], payload.get("total"), "TWSE newlisting")
    return [Event(_code(row[0]), "sii", _roc(row[9], "."), str(row[1]).strip(),
                  str(row[12]).strip()) for row in rows]


def parse_twse_delisted(content: bytes) -> list[Event]:
    payload = _payload(content)
    _check_fields(payload.get("fields"), TWSE_DELISTED_FIELDS, "TWSE suspendListing")
    rows = _complete(payload.get("data") or [], payload.get("total"), "TWSE suspendListing")
    return [Event(_code(row[2]), "sii", _roc(row[0], "/"), str(row[1]).strip()) for row in rows]


def _tpex_table(content: bytes, year: int, fields: list[str], source: str) -> tuple[list, int]:
    """A TPEx answer's rows and the total it declares."""
    payload = _payload(content)
    if payload.get("date") != str(year):
        raise ListingFormatError(f"{source} answered {payload.get('date')!r} for {year}")
    tables = payload.get("tables") or [{}]
    _check_fields(tables[0].get("fields"), fields, source)
    return tables[0].get("data") or [], tables[0].get("totalCount")


def _in_year(event: Event, year: int, source: str) -> Event:
    if event.on.year != year:
        raise ListingFormatError(f"{source} row for {event.stock_id} is dated {event.on}, "
                                 f"not in {year}")
    return event


def parse_tpex_listed(content: bytes, year: int) -> list[Event]:
    rows = _complete(*_tpex_table(content, year, TPEX_LISTED_FIELDS, "TPEx latest"),
                     "TPEx latest")
    return [_in_year(Event(_code(row[1]), "otc", _roc(row[3], "/"), str(row[2]).strip()),
                     year, "TPEx latest") for row in rows]


def _delisting(row, year: int) -> Event:
    if len(row) != len(TPEX_DELISTED_FIELDS):
        raise ListingFormatError(f"TPEx deListed row has {len(row)} cells: {row!r}")
    return _in_year(Event(_code(row[0]), "otc", _roc(row[2], "-"), str(row[1]).strip()),
                    year, "TPEx deListed")


def tpex_delisted_first_page(content: bytes, year: int) -> tuple[list[Event], int]:
    """The unpaged answer's rows and the year's declared total, which may be more."""
    rows, total = _tpex_table(content, year, TPEX_DELISTED_FIELDS, "TPEx deListed")
    if not isinstance(total, int) or total < len(rows):
        raise ListingFormatError(f"TPEx deListed declares {total!r} rows for {len(rows)}")
    return [_delisting(row, year) for row in rows], total


def parse_tpex_delisted(content: bytes, year: int) -> list[Event]:
    """A year whose unpaged answer holds every row."""
    first, total = tpex_delisted_first_page(content, year)
    return _complete(first, total, "TPEx deListed")


def parse_tpex_delisted_paged(content: bytes, year: int, first: list[Event],
                              total: int) -> list[Event]:
    """The paged answer for all `total` rows. It carries no field names, so its
    first rows must equal the unpaged page's, which does."""
    payload = _payload(content)
    if payload.get("date") != str(year) or payload.get("stat") != "ok":
        raise ListingFormatError(f"TPEx deListed paged answer for {year}: "
                                 f"{payload.get('date')!r}, {payload.get('stat')!r}")
    rows = _complete(payload.get("data") or [], payload.get("totalCount"), "TPEx deListed")
    events = _complete([_delisting(row, year) for row in rows], total, "TPEx deListed")
    if events[:len(first)] != first:
        raise ListingFormatError(f"TPEx deListed {year}: the paged rows differ from the "
                                 "first page")
    return events


_TR = re.compile(r"<tr.*?</tr>", re.DOTALL | re.IGNORECASE)
_TD = re.compile(r"<td.*?>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def parse_isin_lookup(content: bytes, stock_id: str) -> Lookup | None:
    """The row for exactly `stock_id`, or None when the page says nothing is registered."""
    html = content.decode("big5hkscs", errors="replace")
    if "class_nofind" in html:
        return None
    rows = [[_TAG.sub("", cell).strip() for cell in _TD.findall(row)] for row in _TR.findall(html)]
    rows = [row for row in rows if row]
    if not rows or rows[0] != ISIN_LOOKUP_FIELDS:
        raise ListingFormatError(f"ISIN lookup for {stock_id}: unrecognised page")
    matches = [row for row in rows[1:] if len(row) == len(ISIN_LOOKUP_FIELDS)
               and row[2] == stock_id]
    if not matches:
        return None
    if len(matches) > 1:
        raise ListingFormatError(f"ISIN lookup lists {stock_id} {len(matches)} times")
    row = matches[0]
    try:
        registered = date(*(int(part) for part in row[7].split("/"))) if row[7] else None
    except (TypeError, ValueError):
        raise ListingFormatError(f"ISIN lookup for {stock_id}: not a date: {row[7]!r}") from None
    return Lookup(stock_id, row[3], row[4], row[5], row[6] or None, registered)


# ---------------------------------------------------------------- assembly


def _pairs(listed: list[Event], delisted: list[Event]):
    """Pair each delisting with the listing that opened it, in date order.

    Yields (listing or None, delisting or None, problem or None); the last pair
    has no delisting when a listing is still open."""
    previous: date | None = None
    for leave in delisted:
        opened = [e for e in listed if e.on < leave.on and (previous is None or e.on >= previous)]
        if len(opened) > 1:
            problem = "listed_twice"
        elif not opened and previous is not None:
            # Left twice with no listing between, or one row published twice.
            problem = "delisted_twice"
        else:
            problem = None
        yield opened[-1] if opened else None, leave, problem
        previous = leave.on
    after = [e for e in listed if previous is None or e.on >= previous]
    if after:
        yield after[-1], None, "listed_twice" if len(after) > 1 else None


def _grouped(events: Iterable[Event]) -> dict[tuple[str, str], list[Event]]:
    grouped: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for event in events:
        grouped[(event.stock_id, event.market)].append(event)
    return {key: sorted(value) for key, value in grouped.items()}


def needs_lookup(today: Iterable[tuple[ListedStock, UUID | None]], listed: Iterable[Event],
                 delisted: Iterable[Event]) -> set[str]:
    """The codes with a span inside the window whose listing the tables do not show."""
    del today  # an open span on today's list is proven by the list itself
    ups, downs = _grouped(listed), _grouped(delisted)
    return {stock_id for (stock_id, market), leaves in downs.items()
            for opened, leave, _ in _pairs(ups.get((stock_id, market), []), leaves)
            if leave is not None and leave.on >= WINDOW_START and opened is None}


def assemble(today: Iterable[tuple[ListedStock, UUID | None]], listed: Iterable[Event],
             delisted: Iterable[Event], lookups: Mapping[str, Lookup | None]) -> Assembly:
    """Every in-scope span and company, and why each other span was left out.

    A pure function of its inputs, independent of their order."""
    current = {stock.stock_id: (stock, fetch_id) for stock, fetch_id in today}
    ups, downs = _grouped(listed), _grouped(delisted)
    keys = set(ups) | set(downs) | {(s.stock_id, s.market) for s, _ in current.values()}
    spans: list[Span] = []
    excluded: list[Left] = []
    quarantined: list[Left] = []
    warnings: list[Left] = []
    before_window = 0
    named_by_lookup: set[str] = set()
    for stock_id, market in sorted(keys):
        now = current.get(stock_id)
        on_list = now is not None and now[0].market == market
        open_seen = False
        ambiguous_open = False
        for opened, leave, problem in _pairs(ups.get((stock_id, market), []),
                                             downs.get((stock_id, market), [])):
            if leave is not None and leave.on < WINDOW_START:
                before_window += 1
                continue
            if problem:
                quarantined.append(Left(stock_id, market, problem, (leave or opened).on))
                ambiguous_open = ambiguous_open or leave is None
                continue
            innovation = opened is not None and INNOVATION_BOARD in opened.note
            if leave is None:  # still open by the tables
                if on_list:
                    stock, fetch_id = now
                    open_seen = True
                    if innovation:  # moved to the main board on the list's date
                        spans.append(Span(stock_id, market, stock.listed_on, None, fetch_id,
                                          fetch_id if stock.listed_on else None))
                    else:
                        spans.append(Span(stock_id, market, opened.on, None, fetch_id,
                                          opened.fetch_id))
                elif innovation:
                    excluded.append(Left(stock_id, market, "innovation_board", opened.on))
                else:
                    quarantined.append(Left(stock_id, market, "listed_not_on_isin", opened.on))
                continue
            if innovation:
                excluded.append(Left(stock_id, market, "innovation_board", leave.on))
                continue
            if opened is not None:
                proof = opened.fetch_id
            else:
                lookup = lookups.get(stock_id)
                if lookup is None:
                    quarantined.append(Left(stock_id, market, "unproven_category", leave.on))
                    continue
                if lookup.registered_on is None or lookup.registered_on > leave.on:
                    # Registered after the delisting: a code reused by another
                    # security (2301 shows codes are reused), not this one.
                    quarantined.append(Left(stock_id, market, "isin_lookup_is_another_security",
                                            leave.on))
                    continue
                if lookup.category not in IN_SCOPE_CATEGORIES:
                    excluded.append(Left(stock_id, market, f"category:{lookup.category}",
                                         leave.on))
                    continue
                proof = lookup.fetch_id
                named_by_lookup.add(stock_id)
            spans.append(Span(stock_id, market, opened.on if opened else None, leave.on, proof,
                              opened.fetch_id if opened else None, leave.fetch_id))
        if on_list and not open_seen:
            # Today's list says it trades, so it stays in scope. A NULL start
            # means "before the tables begin", which is false when the tables
            # show it leaving this market or listing twice: then the ISIN date,
            # the only other evidence, dates the span if it follows the last
            # delisting (code review of #66).
            stock, fetch_id = now
            leaves = downs.get((stock_id, market), [])
            start = None
            if leaves or ambiguous_open:
                if leaves:
                    warnings.append(Left(stock_id, market, "relisted_without_listing_row",
                                         None))
                if stock.listed_on and (not leaves or stock.listed_on >= leaves[-1].on):
                    start = stock.listed_on
            spans.append(Span(stock_id, market, start, None, fetch_id,
                              fetch_id if start else None))

    companies = []
    kept = {span.stock_id for span in spans}
    last_name = {}
    for event in sorted([*listed, *delisted], key=lambda e: (e.on, e.market)):
        last_name[event.stock_id] = event
    for stock_id in sorted(kept):
        if stock_id in current:
            stock, fetch_id = current[stock_id]
            companies.append(Company(stock_id, stock.name, stock.industry, fetch_id))
        elif stock_id in named_by_lookup:
            lookup = lookups[stock_id]
            companies.append(Company(stock_id, lookup.name, lookup.industry, lookup.fetch_id))
        else:
            event = last_name[stock_id]
            companies.append(Company(stock_id, event.name, None, event.fetch_id))
    return Assembly(companies, sorted(spans, key=span_order), excluded, quarantined, warnings,
                    before_window)


def span_order(span: Span) -> tuple:
    """Stock, market, then time; an unknown start sorts first, an open end last."""
    return (span.stock_id, span.market, span.listed_on or date.min,
            span.delisted_on or date.max)


# ---------------------------------------------------------------- storage


def listed_stock_ids(connection: Connection) -> frozenset[str]:
    """The adapters' scope: stocks with an open span, listed today."""
    from stock_data_center.db.schema_v2 import listings

    return frozenset(connection.scalars(
        sa.select(listings.c.stock_id).where(listings.c.delisted_on.is_(None))))


def write(connection: Connection, assembly: Assembly) -> dict[str, int]:
    """Upsert the companies and replace every span, in the caller's transaction.

    A company is never deleted: stored history references it. One that no
    longer has a span simply leaves the adapters' scope."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from stock_data_center.db.schema_v2 import listings, stocks

    if assembly.companies:
        statement = pg_insert(stocks).values([
            {"stock_id": c.stock_id, "name": c.name, "industry": c.industry,
             "fetch_id": c.fetch_id} for c in assembly.companies])
        # The fetch changes only with the values: it names the raw file they
        # first came from, so its time is when they were first recorded, which
        # the industry periods read as the ISIN category's (code review of #74).
        changed = sa.tuple_(stocks.c.name, stocks.c.industry).is_distinct_from(
            sa.tuple_(statement.excluded.name, statement.excluded.industry))
        connection.execute(statement.on_conflict_do_update(
            index_elements=[stocks.c.stock_id],
            set_={"name": statement.excluded.name, "industry": statement.excluded.industry,
                  "fetch_id": sa.case((changed, statement.excluded.fetch_id),
                                      else_=stocks.c.fetch_id)}))
    connection.execute(sa.delete(listings))
    if assembly.spans:
        connection.execute(sa.insert(listings), [
            {"stock_id": s.stock_id, "market": s.market, "listed_on": s.listed_on,
             "delisted_on": s.delisted_on, "fetch_id": s.fetch_id,
             "listed_fetch_id": s.listed_fetch_id, "delisted_fetch_id": s.delisted_fetch_id}
            for s in assembly.spans])
    return {"companies": len(assembly.companies), "spans": len(assembly.spans),
            "open_spans": sum(s.delisted_on is None for s in assembly.spans)}


class _Stop(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def refresh(connection: Connection, *, git_commit: str,
            get: Callable[[str], bytes] | None = None, now: Callable[[], datetime] | None = None,
            store=None, pause: Callable[[], None] | None = None) -> dict:
    """Fetch every page raw-first, then rebuild `stocks` and `listings` from them.

    Every page is one `fetches` row with its raw file. The spans are rebuilt
    whole, so a page that fails or no longer parses stops the refresh before
    anything is written; the result says which. `get(url) -> bytes`, `now()`,
    `store` and `pause()` (between requests) are injectable for tests."""
    import httpx

    from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
    from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

    get = get or (lambda url: httpx.get(url, timeout=120).raise_for_status().content)
    now = now or (lambda: datetime.now(UTC))
    store = store or LocalRawArtifactStore()
    pause = pause or (lambda: time.sleep(PAUSE_SECONDS))
    started = now()
    this_year = started.astimezone(TAIPEI).year
    asked = 0

    def fetch(dataset: str, source: str, key: str, url: str, parse):
        nonlocal asked
        if asked:
            pause()
        asked += 1
        fetched_at = now()
        record = FetchRecord(dataset, source, key, url, "unspecified", ADAPTER_VERSION,
                             git_commit, fetched_at)
        try:
            content = get(url)
        except Exception as error:  # noqa: BLE001 - every failure is logged, then stops
            record_fetch(connection, record, content=None, status="failed",
                         reason_code="request_failed", reason_detail=str(error)[:500])
            raise _Stop(f"{source} {key}: request failed: {error}") from None
        try:
            parsed = parse(content)
        # Anything parsing an outside page raises is a page this code does not
        # understand; ValueError covers the format errors and UnicodeDecodeError.
        except (ValueError, IndexError, KeyError, TypeError, AttributeError) as error:
            record_fetch(connection, record, content=content, status="quarantined",
                         store=store, reason_code="unrecognised_layout",
                         reason_detail=str(error)[:500])
            raise _Stop(f"{source} {key}: {error}") from None
        return parsed, record_fetch(connection, record, content=content, status="succeeded",
                                    store=store)

    def stamped(events, fetch_id):
        return [Event(e.stock_id, e.market, e.on, e.name, e.note, fetch_id) for e in events]

    try:
        day = started.astimezone(TAIPEI).date().isoformat()
        today: list[tuple[ListedStock, UUID]] = []
        for market, mode in MARKETS.items():
            rows, fetch_id = fetch("stocks", "twse_isin", f"twse_isin:{market}:{day}",
                                   ISIN_URL.format(mode=mode),
                                   lambda c, m=market: parse_isin_page(c, market=m))
            today += [(row, fetch_id) for row in rows]
        listed: list[Event] = []
        delisted: list[Event] = []
        rows, fetch_id = fetch("listings", "twse_newlisting", f"twse_newlisting:{day}",
                               TWSE_LISTED_URL, parse_twse_listed)
        listed += stamped(rows, fetch_id)
        rows, fetch_id = fetch("listings", "twse_suspendlisting", f"twse_suspendlisting:{day}",
                               TWSE_DELISTED_URL, parse_twse_delisted)
        delisted += stamped(rows, fetch_id)
        for year in range(FIRST_TPEX_YEAR, this_year + 1):
            rows, fetch_id = fetch("listings", "tpex_latest", f"tpex_latest:{year}:{day}",
                                   TPEX_LISTED_URL.format(year=year),
                                   lambda c, y=year: parse_tpex_listed(c, y))
            listed += stamped(rows, fetch_id)
            (rows, total), fetch_id = fetch(
                "listings", "tpex_delisted", f"tpex_delisted:{year}:{day}",
                TPEX_DELISTED_URL.format(year=year),
                lambda c, y=year: tpex_delisted_first_page(c, y))
            if total > len(rows):
                rows, fetch_id = fetch(
                    "listings", "tpex_delisted", f"tpex_delisted:{year}:all:{day}",
                    TPEX_DELISTED_PAGED_URL.format(year=year, size=total),
                    lambda c, y=year, f=rows, t=total: parse_tpex_delisted_paged(c, y, f, t))
            delisted += stamped(rows, fetch_id)
        lookups: dict[str, Lookup | None] = {}
        for code in sorted(needs_lookup(today, listed, delisted)):
            found, fetch_id = fetch("listings", "twse_isin_lookup",
                                    f"twse_isin_lookup:{code}:{day}",
                                    ISIN_LOOKUP_URL.format(code=code),
                                    lambda c, s=code: parse_isin_lookup(c, s))
            lookups[code] = None if found is None else Lookup(
                found.stock_id, found.name, found.market_label, found.category,
                found.industry, found.registered_on, fetch_id)
    except _Stop as stop:
        return {"written": False, "reason": stop.reason, "requests": asked}

    assembly = assemble(today, listed, delisted, lookups)
    counts = write(connection, assembly)
    return {
        "written": True,
        "requests": asked,
        **counts,
        "before_window": assembly.before_window,
        "excluded": dict(Counter(left.reason for left in assembly.excluded)),
        "quarantined": dict(Counter(left.reason for left in assembly.quarantined)),
        "quarantined_spans": [(q.stock_id, q.market, q.reason, q.on)
                              for q in assembly.quarantined],
        "warnings": [(w.stock_id, w.market, w.reason) for w in assembly.warnings],
    }


def main() -> int:
    """`DATABASE_URL=... python -m stock_data_center.v2.listings`: one refresh, one transaction."""
    import os

    from stock_data_center.v2.fetch_log import current_git_commit

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            report = refresh(connection, git_commit=current_git_commit())
    finally:
        engine.dispose()
    print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    return 0 if report["written"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
