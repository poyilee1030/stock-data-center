"""Today's v1 universe: the listed and OTC common stocks on the TWSE ISIN list.

ADR-0026. The ISIN list (`isin.twse.com.tw/isin/C_public.jsp`, `strMode=2` for
上市 and `strMode=4` for 上櫃) groups every security under a category title row.
Only the rows under `股票` are in scope: that excludes ETFs, ETNs, preferred
shares, TDRs, beneficiary certificates, warrants and the innovation board
(`創新板`, whose CFI code is the same `ESVUFR` as ordinary common stock, which is
why the category and not the CFI code decides).

The list is today's snapshot. Securities delisted before today are absent and
therefore out of scope everywhere; the owner accepted that survivorship bias.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
MARKETS = {"sii": 2, "otc": 4}
MARKET_LABEL = {"sii": "上市", "otc": "上櫃"}
IN_SCOPE_CATEGORY = "股票"
ENCODING = "big5hkscs"  # served as MS950; big5hkscs decodes every name on the list

_ROW = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_TITLE = re.compile(r"<B>\s*(.*?)\s*<B>", re.DOTALL)


class UniverseFormatError(ValueError):
    """The page no longer has the shape this parser was written against."""


@dataclass(frozen=True, slots=True)
class ListedStock:
    stock_id: str
    name: str
    market: str
    industry: str | None
    listed_on: date | None


def parse_isin_page(content: bytes, *, market: str) -> list[ListedStock]:
    html = content.decode(ENCODING)
    stocks: list[ListedStock] = []
    category: str | None = None
    seen_category = False
    for row in _ROW.findall(html):
        cells = _CELL.findall(row)
        if len(cells) == 1:
            title = _TITLE.search(cells[0])
            category = title.group(1).strip() if title else cells[0].strip()
            seen_category = True
            continue
        if category != IN_SCOPE_CATEGORY or len(cells) != 7:
            continue
        code_name, _isin, listed, market_label, industry, _cfi, _note = (
            cell.strip() for cell in cells
        )
        if market_label != MARKET_LABEL[market]:
            raise UniverseFormatError(
                f"{market} page lists {code_name!r} on market {market_label!r}"
            )
        stock_id, sep, name = code_name.partition("　")
        if not sep or not stock_id or not name:
            raise UniverseFormatError(f"cannot split code and name in {code_name!r}")
        stocks.append(
            ListedStock(
                stock_id=stock_id.strip(),
                name=name.strip(),
                market=market,
                industry=industry or None,
                listed_on=_date(listed),
            )
        )
    if not seen_category or not stocks:
        raise UniverseFormatError(f"no {IN_SCOPE_CATEGORY!r} section on the {market} page")
    return stocks


def _date(text: str) -> date | None:
    if not text:
        return None
    year, month, day = (int(part) for part in text.split("/"))
    return date(year, month, day)


ADAPTER_VERSION = "twse-isin-common-stocks:v1"
MARKET_TIMEZONE = "Asia/Taipei"


def load_universe(
    connection, *, git_commit: str, get=None, now=None, store=None
) -> dict[str, int | str]:
    """Fetch both lists raw-first, then upsert `stocks` from each that parses.

    Raw-first (CLAUDE.md §71): the page is stored before it is parsed, so a page
    whose layout changed is kept for diagnosis. It is logged as a `quarantined`
    fetch with the parser's reason and contributes no rows; the result maps that
    market to the reason instead of a count, and the caller decides whether a
    partial universe is acceptable.

    Rows are never deleted: a stock that leaves a later list keeps its row,
    because stored history references it. `get(url) -> bytes`, `now()` and the
    raw `store` are injectable for tests.
    """
    from zoneinfo import ZoneInfo

    import httpx
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from stock_data_center.db.schema_v2 import stocks
    from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
    from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

    get = get or (lambda url: httpx.get(url, timeout=120).raise_for_status().content)
    store = store or LocalRawArtifactStore()
    if now is None:
        from datetime import UTC, datetime

        def now():
            return datetime.now(UTC)

    results: dict[str, int | str] = {}
    for market, mode in MARKETS.items():
        url = ISIN_URL.format(mode=mode)
        fetched_at = now()
        content = get(url)
        store.put(content)
        market_date = fetched_at.astimezone(ZoneInfo(MARKET_TIMEZONE)).date()
        record = FetchRecord(
            dataset="stocks",
            source="twse_isin",
            resource_key=f"twse_isin:{market}:{market_date.isoformat()}",
            source_uri=url,
            purpose="first_capture",
            adapter_version=ADAPTER_VERSION,
            git_commit=git_commit,
            fetched_at=fetched_at,
        )
        try:
            parsed = parse_isin_page(content, market=market)
        except (UniverseFormatError, UnicodeDecodeError, ValueError) as error:
            record_fetch(
                connection,
                record,
                content=content,
                status="quarantined",
                store=store,
                reason_code="unrecognised_layout",
                reason_detail=str(error)[:500],
            )
            results[market] = f"quarantined: {error}"
            continue
        fetch_id = record_fetch(
            connection, record, content=content, status="succeeded", store=store
        )
        rows = [
            {
                "stock_id": item.stock_id,
                "name": item.name,
                "market": item.market,
                "industry": item.industry,
                "listed_on": item.listed_on,
                "fetch_id": fetch_id,
            }
            for item in parsed
        ]
        statement = pg_insert(stocks).values(rows)
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[stocks.c.stock_id],
                set_={
                    column: statement.excluded[column]
                    for column in ("name", "market", "industry", "listed_on", "fetch_id")
                },
            )
        )
        results[market] = len(rows)
    return results
