"""The exchanges' by-category daily quotes: which category listed a stock on a date (Step 39-b, ADR-0030).

Both exchanges answer their daily quotes one industry category at a time, by
the ISIN industry code (`stock_data_center.v2.industry`):

- TPEx `afterTrading/otc?date=YYYY/MM/DD&type=<code>` answers as of the date:
  2022-07-01's 貿易百貨 lists 15 stocks, 2023-07-03's none, and 居家生活 does
  not exist before 2023-07-03. Source `tpex_otc_quotes`; it anchors the OTC
  spans that have ended and reconciles the announcements (ADR-0030 §3, §6).
- TWSE `MI_INDEX?date=YYYYMMDD&type=<code>` rebuilds history under the
  categories of the day it is asked (2020 pages list 數位雲端), but still lists
  delisted companies. Source `twse_mi_index`; it gives a delisted company only
  its last known category.

One date's sweep asks every code but 13, whose TWSE page lists every member of
24-31 again; TPEx is also asked 80 (管理股票), a trading bucket that hides a
stock's industry and is reported, never stored. The category is the code
asked, never a page's label: TPEx labels 33 and 34 with ''. A sweep is written
whole or not at all: a stock on two pages, a TPEx category that did not exist
on the date, or a page this code cannot read quarantines the date, and a later
run asks it again.

Raw-first: every page is one `fetches` row with its raw file. A date whose
every page's latest fetch succeeded is not asked again unless `refetch`; its
rows are then compared, and only a changed category is a new row.
"""

from __future__ import annotations

import functools
import json
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.v2 import industry

ADAPTER_VERSION = "industry_observations:v1"
DATASET = "industry_observations"
SOURCES = {"tpex_otc_quotes": "otc", "twse_mi_index": "sii"}
PAUSE_SECONDS = {"tpex_otc_quotes": 2.0, "twse_mi_index": 3.0}
TAIPEI = ZoneInfo("Asia/Taipei")
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"

SUPERSET = "13"  # TWSE's 電子工業 page repeats every 24-31 member; TPEx's is empty
MANAGED = "80"  # TPEx 管理股票
TPEX_FIELDS = ["代號", "名稱"]
TWSE_FIELD = "證券代號"
# TPEx labels no rule in `industry` derives; '' is how it labels 33 and 34.
TPEX_LABELS = {"電腦及週邊類": "25", "管理股票": MANAGED}
_TWSE_TITLE = re.compile(r"^(\d{2,3})年(\d{2})月(\d{2})日 每日收盤行情\((.*)\)$")


class PageFormatError(ValueError):
    """A page this parser cannot read, or a sweep that cannot be true.

    `pages` are the codes whose fetches are quarantined for it."""

    def __init__(self, reason: str, detail: str = "", pages: Iterable[str] = ()) -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.pages = set(pages)


def codes_to_ask(source: str) -> tuple[str, ...]:
    codes = tuple(sorted(code for code in industry.NAMES if code != SUPERSET))
    return codes + ((MANAGED,) if source == "tpex_otc_quotes" else ())


def tpex_label_code(label: str) -> str | None:
    label = label.strip()
    return TPEX_LABELS.get(label) or industry.code_of(label.removesuffix("類"))


# ---------------------------------------------------------------- parsing


def _readable(parse):
    """A parser whose every failure is a format error, so it quarantines one
    date instead of rolling back the run (as in Step 39-a)."""

    @functools.wraps(parse)
    def wrapped(*args, **kwargs):
        try:
            return parse(*args, **kwargs)
        except PageFormatError:
            raise
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
            raise PageFormatError(
                "unrecognised_layout", f"{type(error).__name__}: {error}"[:300]) from None

    return wrapped


def _payload(content: bytes, stat: str) -> dict:
    payload = json.loads(content)
    if not isinstance(payload, dict) or payload.get("stat") != stat:
        raise PageFormatError("unrecognised_layout", f"not an answer with stat {stat}")
    return payload


def _codes(rows) -> list[str]:
    if not isinstance(rows, list):
        raise PageFormatError("unrecognised_layout", "data is not a list")
    codes = [row[0].strip() for row in rows]
    if not all(codes):
        raise PageFormatError("unrecognised_layout", "a row without a code")
    return codes


@_readable
def parse_tpex_page(content: bytes, day: date, code: str) -> list[str]:
    """The codes a TPEx page lists under `code` on `day`."""
    payload = _payload(content, "ok")
    if payload.get("date") != f"{day:%Y%m%d}":
        raise PageFormatError("date_mismatch", f"answered {payload.get('date')!r} for {day}")
    tables = payload.get("tables")
    if not isinstance(tables, list) or len(tables) != 1:
        raise PageFormatError("unrecognised_layout", "not exactly one table")
    table = tables[0]
    if table.get("fields", [])[:2] != TPEX_FIELDS:
        raise PageFormatError("unrecognised_layout", f"fields {table.get('fields')!r}")
    if table.get("date") != f"{day.year - 1911}/{day:%m/%d}":
        raise PageFormatError("date_mismatch", f"table dated {table.get('date')!r}")
    label = table.get("category")
    if label.strip() and tpex_label_code(label) != code:
        raise PageFormatError("category_mismatch", f"type {code} answered {label!r}")
    codes = _codes(table.get("data"))
    if table.get("totalCount") != len(codes):
        raise PageFormatError("unrecognised_layout",
                              f"{len(codes)} of {table.get('totalCount')} rows")
    return codes


@_readable
def parse_twse_page(content: bytes, day: date, code: str) -> list[str]:
    """The codes a TWSE page lists under `code` on `day`."""
    payload = _payload(content, "OK")
    if payload.get("date") != f"{day:%Y%m%d}":
        raise PageFormatError("date_mismatch", f"answered {payload.get('date')!r} for {day}")
    if payload.get("type") != code:
        raise PageFormatError("type_mismatch", f"asked {code}, answered {payload.get('type')!r}")
    quotes = [table for table in payload.get("tables") or []
              if (table.get("fields") or [None])[0] == TWSE_FIELD]
    if len(quotes) != 1:
        raise PageFormatError("unrecognised_layout", f"{len(quotes)} quote tables")
    match = _TWSE_TITLE.match(quotes[0].get("title") or "")
    if match is None:
        raise PageFormatError("unrecognised_layout", f"title {quotes[0].get('title')!r}")
    year, month, dom, name = match.groups()
    if date(int(year) + 1911, int(month), int(dom)) != day:
        raise PageFormatError("date_mismatch", f"title {quotes[0]['title']!r}")
    codes = _codes(quotes[0].get("data"))
    # A code TWSE does not have answers 「每日收盤行情()」 with no rows.
    if (name or codes) and industry.code_of(name) != code:
        raise PageFormatError("category_mismatch", f"type {code} answered {name!r}")
    return codes


PARSERS = {"tpex_otc_quotes": parse_tpex_page, "twse_mi_index": parse_twse_page}


def check_sweep(source: str, day: date, pages: dict[str, list[str]], *,
                fetched_on: date) -> tuple[dict[str, str], list[str]]:
    """(stock -> category, stocks under 管理股票) for one date's pages, if they can be true.

    TPEx answers as of `day`, so each category must have existed then; TWSE
    rebuilds under the categories of `fetched_on`."""
    market = SOURCES[source]
    on = day if source == "tpex_otc_quotes" else fetched_on
    where: dict[str, list[str]] = {}
    for code, stocks in pages.items():
        if stocks and code != MANAGED and not industry.exists(code, market, on):
            raise PageFormatError("industry_not_in_effect", f"{code} lists {len(stocks)} on {day}",
                                  pages=[code])
        for stock in stocks:
            where.setdefault(stock, []).append(code)
    if twice := {stock: codes for stock, codes in where.items() if len(codes) > 1}:
        raise PageFormatError("two_categories", ", ".join(f"{s} {c}" for s, c in
                                                           sorted(twice.items()))[:300],
                              pages={code for codes in twice.values() for code in codes})
    if not where:
        raise PageFormatError("empty_sweep", str(day), pages=pages)
    placed = {stock: codes[0] for stock, codes in where.items() if codes[0] != MANAGED}
    return placed, sorted(stock for stock, codes in where.items() if codes[0] == MANAGED)


# ---------------------------------------------------------------- ingestion


def resource_key(source: str, day: date, code: str) -> str:
    return f"{source}:by-category:{day}:{code}"


def _request(source: str, day: date, code: str) -> tuple[str, dict]:
    if source == "tpex_otc_quotes":
        return TPEX_URL, {"date": f"{day:%Y/%m/%d}", "type": code, "response": "json"}
    return TWSE_URL, {"date": f"{day:%Y%m%d}", "type": code, "response": "json"}


def _done(connection: Connection, source: str, day: date) -> bool:
    """Whether every page of the date's latest sweep succeeded."""
    from stock_data_center.db.schema_v2 import fetches as f

    keys = [resource_key(source, day, code) for code in codes_to_ask(source)]
    latest = dict(connection.execute(
        sa.select(f.c.resource_key, f.c.status)
        .where(f.c.dataset == DATASET, f.c.source == source, f.c.resource_key.in_(keys))
        .order_by(f.c.resource_key, f.c.fetched_at.desc(), f.c.attempt.desc())
        .distinct(f.c.resource_key)).all())
    return all(latest.get(key) == "succeeded" for key in keys)


def write(connection: Connection, source: str, rows: list[dict]) -> Counter:
    """Append each row whose category differs from its key's latest."""
    from stock_data_center.db.schema_v2 import industry_observations as table

    connection.execute(sa.select(sa.func.pg_advisory_xact_lock(
        sa.func.hashtext(f"{DATASET}/{source}"))))
    days = sorted({row["trade_date"] for row in rows})
    stored = {(stock, day): code for stock, day, code in connection.execute(
        sa.select(table.c.stock_id, table.c.trade_date, table.c.industry_code)
        .where(table.c.source == source, table.c.trade_date.in_(days))
        .order_by(table.c.stock_id, table.c.trade_date, table.c.recorded_at.desc())
        .distinct(table.c.stock_id, table.c.trade_date)).all()} if days else {}
    fresh = [{"source": source, **row} for row in rows
             if stored.get((row["stock_id"], row["trade_date"])) != row["industry_code"]]
    if fresh:
        connection.execute(sa.insert(table), fresh)
    return Counter(appended=len(fresh), unchanged=len(rows) - len(fresh))


Http = Callable[[str, dict], bytes]


def _http(url: str, params: dict) -> bytes:
    import httpx

    return httpx.get(url, params=params, timeout=60).raise_for_status().content


def ingest(connection: Connection, source: str, days: Iterable[date], *, git_commit: str,
           http: Http | None = None, now: Callable[[], datetime] | None = None, store=None,
           pause: Callable[[], None] | None = None, purpose: str = "gap_fill",
           refetch: bool = False) -> dict:
    """Sweep each trading day's pages raw-first and write its observations, in
    the caller's transaction."""
    from stock_data_center.db.schema_v2 import stocks, trading_days
    from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
    from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

    http = http or _http
    now = now or (lambda: datetime.now(UTC))
    store = store or LocalRawArtifactStore()
    pause = pause or (lambda: time.sleep(PAUSE_SECONDS[source]))
    parse = PARSERS[source]
    days = sorted(set(days))
    calendar = set(connection.scalars(
        sa.select(trading_days.c.trade_date).where(trading_days.c.trade_date.in_(days))))
    universe = set(connection.scalars(sa.select(stocks.c.stock_id)))
    report: Counter = Counter(dates=0, appended=0, unchanged=0, outside_universe=0)
    quarantined, off_calendar, managed = [], [], []
    asked = 0

    for day in days:
        if day not in calendar:
            off_calendar.append(day)
            continue
        if not refetch and _done(connection, source, day):
            report["skipped"] += 1
            continue
        got: dict[str, tuple[FetchRecord, bytes]] = {}
        for code in codes_to_ask(source):
            url, params = _request(source, day, code)
            if asked:
                pause()
            asked += 1
            record = FetchRecord(DATASET, source, resource_key(source, day, code),
                                 f"{url}?{urlencode(params)}", purpose, ADAPTER_VERSION,
                                 git_commit, now())
            try:
                got[code] = (record, http(url, params))
            except Exception as error:  # noqa: BLE001 - every failure is logged
                record_fetch(connection, record, content=None, status="failed",
                             reason_code="request_failed", reason_detail=str(error)[:500])
                report["failed"] += 1
                break

        def log(bad: PageFormatError | None = None, got=got) -> dict[str, UUID]:
            """Each page's fetch; the pages `bad` names are quarantined for it."""
            return {code: record_fetch(
                connection, record, content=content, store=store,
                status="quarantined" if bad and code in bad.pages else "succeeded",
                reason_code=bad.reason if bad and code in bad.pages else None,
                reason_detail=str(bad)[:500] if bad and code in bad.pages else None)
                for code, (record, content) in got.items()}

        if len(got) < len(codes_to_ask(source)):
            log()  # the pages fetched before the failure keep their raw files
            continue
        try:
            pages = {}
            for code, (_, content) in got.items():
                try:
                    pages[code] = parse(content, day, code)
                except PageFormatError as error:
                    error.pages = {code}
                    raise
            placed, in_managed = check_sweep(source, day, pages,
                                             fetched_on=now().astimezone(TAIPEI).date())
        except PageFormatError as error:
            log(error)
            quarantined.append((day, error.reason))
            continue
        fetch_ids = log()
        managed += [(day, stock) for stock in in_managed]
        rows = [{"stock_id": stock, "trade_date": day, "industry_code": code,
                 "fetch_id": fetch_ids[code]} for stock, code in sorted(placed.items())
                if stock in universe]
        report["outside_universe"] += len(placed) - len(rows)
        report.update(write(connection, source, rows))
        report["dates"] += 1
    return {**dict(report), "requests": asked, "quarantined": quarantined,
            "off_calendar": off_calendar, "managed": managed}


# ---------------------------------------------------------------- which dates


@dataclass(frozen=True, slots=True)
class Anchor:
    """A listing span that has ended, and the last date its market's
    whole-market quote file lists the stock (None: never inside the window)."""

    stock_id: str
    market: str
    delisted_on: date
    last_quoted: date | None


def anchors(connection: Connection, *, store=None) -> list[Anchor]:
    """Every span that ended inside the window, with the date to anchor it on.

    A company often stops trading weeks before its delisting date (4712 last
    traded 2024-02-05 and left TPEx on 2024-09-25), so the date is read from the
    stored whole-market daily-price files, which list every security that
    traded or could trade that day."""
    from stock_data_center.db.schema_v2 import fetches as f
    from stock_data_center.db.schema_v2 import listings
    from stock_data_center.ingestion.adapters.whole_market_daily import (
        TPExWholeMarketDailyAdapter,
        TWSEWholeMarketDailyAdapter,
    )
    from stock_data_center.ingestion.models import WholeMarketDailyRequest
    from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore

    store = store or LocalRawArtifactStore()
    adapters = {"otc": TPExWholeMarketDailyAdapter(), "sii": TWSEWholeMarketDailyAdapter()}
    files: dict[str, list[tuple[date, bytes, int]]] = {}
    for market, adapter in adapters.items():
        latest = connection.execute(
            sa.select(f.c.resource_key, f.c.status, f.c.sha256, f.c.byte_size)
            .where(f.c.dataset == "daily_price", f.c.source == adapter.source,
                   f.c.resource_key.like(f"{adapter.source}:daily-quotes:%"))
            .order_by(f.c.resource_key, f.c.fetched_at.desc(), f.c.attempt.desc())
            .distinct(f.c.resource_key)).all()
        files[market] = sorted(
            ((date.fromisoformat(key.rsplit(":", 1)[1]), sha, size)
             for key, status, sha, size in latest if status == "succeeded"), reverse=True)

    @functools.cache
    def listed(market: str, day: date, sha: bytes, size: int) -> frozenset[str]:
        parsed = adapters[market].parse(store.get(sha.hex(), size), WholeMarketDailyRequest(day))
        return frozenset(row.security_code for row in parsed.rows)

    found = []
    for stock, market, off in connection.execute(
            sa.select(listings.c.stock_id, listings.c.market, listings.c.delisted_on)
            .where(listings.c.delisted_on > industry.WINDOW_START)
            .order_by(listings.c.delisted_on, listings.c.stock_id)).all():
        last = next((day for day, sha, size in files[market]
                     if industry.WINDOW_START <= day < off
                     and stock in listed(market, day, sha, size)), None)
        found.append(Anchor(stock, market, off, last))
    return found


def reconciliation_dates(connection: Connection) -> list[date]:
    """The OTC dates to check the announcements on (ROADMAP Step 39 decision 4):
    the window's first trading day, the trading day before and the day of each
    TPEx effective date, and the calendar's last day."""
    from stock_data_center.db.schema_v2 import industry_changes, trading_days

    calendar = sorted(connection.scalars(
        sa.select(trading_days.c.trade_date)
        .where(trading_days.c.trade_date >= industry.WINDOW_START)))
    if not calendar:
        return []
    chosen = {calendar[0], calendar[-1]}
    for effective in connection.scalars(
            sa.select(industry_changes.c.effective_date).distinct()
            .where(industry_changes.c.source == "tpex_announcement")):
        before = [day for day in calendar if day < effective]
        after = [day for day in calendar if day >= effective]
        chosen.update(before[-1:] + after[:1])
    return sorted(chosen)


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    """`DATABASE_URL=... python -m stock_data_center.v2.industry_observations
    --source S (--anchors | --reconcile | --date D ...)`: one transaction per date."""
    import argparse
    import os

    from stock_data_center.v2.fetch_log import current_git_commit

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=sorted(SOURCES), action="append")
    parser.add_argument("--date", type=date.fromisoformat, action="append", default=[])
    parser.add_argument("--anchors", action="store_true",
                        help="the last quoted date of every span that ended")
    parser.add_argument("--reconcile", action="store_true",
                        help="the OTC reconciliation dates (tpex_otc_quotes only)")
    parser.add_argument("--purpose", default="gap_fill",
                        choices=["gap_fill", "correction_check", "first_capture", "unspecified"])
    parser.add_argument("--refetch", action="store_true")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    engine = sa.create_engine(url)
    commit = current_git_commit()
    reports = {}
    try:
        with engine.connect() as connection:
            found = anchors(connection) if args.anchors else []
            reconcile = reconciliation_dates(connection) if args.reconcile else []
        reports["anchors"] = [
            {"stock_id": a.stock_id, "market": a.market, "delisted_on": a.delisted_on,
             "last_quoted": a.last_quoted} for a in found]
        for source in args.source or sorted(SOURCES):
            days = set(args.date)
            days |= {a.last_quoted for a in found if a.market == SOURCES[source] and a.last_quoted}
            if source == "tpex_otc_quotes":
                days |= set(reconcile)
            total: Counter = Counter()
            lists: dict[str, list] = {"quarantined": [], "off_calendar": [], "managed": []}
            for day in sorted(days):
                with engine.begin() as connection:
                    one = ingest(connection, source, [day], git_commit=commit,
                                 purpose=args.purpose, refetch=args.refetch)
                print(json.dumps({"source": source, "date": day, **one},
                                 ensure_ascii=False, default=str), flush=True)
                for key in lists:
                    lists[key] += one.pop(key)
                total.update(one)
            reports[source] = {**dict(total), **lists}
    finally:
        engine.dispose()
    print(json.dumps(reports, ensure_ascii=False, default=str, indent=2))
    return 0 if all(not report["quarantined"] and not report.get("failed")
                    for key, report in reports.items() if key in SOURCES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
