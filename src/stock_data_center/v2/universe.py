"""Today's v1 universe: the listed and OTC common stocks on the TWSE ISIN list.

ADR-0026. The ISIN list (`isin.twse.com.tw/isin/C_public.jsp`, `strMode=2` for
上市 and `strMode=4` for 上櫃) groups every security under a category title row.
Only the rows under `股票` are in scope: that excludes ETFs, ETNs, preferred
shares, TDRs, beneficiary certificates, warrants and the innovation board
(`創新板`, whose CFI code is the same `ESVUFR` as ordinary common stock, which is
why the category and not the CFI code decides).

The list is today's snapshot: it says which stocks are listed now. The
companies delisted since the v1 window opened, and every stock's listing spans,
come from the exchanges' tables (`stock_data_center.v2.listings`, Step 38-a).
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
