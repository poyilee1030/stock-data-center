"""ADR-0026: the v1 universe is the `股票` section of today's ISIN lists."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from stock_data_center.v2.universe import UniverseFormatError, parse_isin_page

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v2"


def _page(market: str) -> bytes:
    return (FIXTURES / f"isin_{market}_excerpt.html").read_bytes()


def test_only_the_common_stock_section_is_in_scope() -> None:
    listed = parse_isin_page(_page("sii"), market="sii")
    # The excerpt keeps three rows of every section; only `股票` survives, so
    # innovation-board, preferred, TDR, ETF, ETN and REIT rows are all dropped.
    assert [stock.stock_id for stock in listed] == ["1101", "1102", "1103"]
    assert listed[0].name == "台泥"
    assert listed[0].industry == "水泥工業"
    assert listed[0].listed_on == date(1962, 2, 9)
    assert {stock.market for stock in listed} == {"sii"}


def test_the_otc_page_lists_its_common_stocks_after_warrants_and_etfs() -> None:
    listed = parse_isin_page(_page("otc"), market="otc")
    assert len(listed) == 3
    assert {stock.market for stock in listed} == {"otc"}
    assert all(stock.stock_id.isdigit() and len(stock.stock_id) == 4 for stock in listed)


def test_a_row_on_the_wrong_market_is_refused() -> None:
    with pytest.raises(UniverseFormatError, match="market"):
        parse_isin_page(_page("sii"), market="otc")


def test_a_page_without_the_stock_section_is_refused() -> None:
    page = _page("sii").decode("big5hkscs").replace("<B> 股票 <B>", "<B> 其他 <B>")
    with pytest.raises(UniverseFormatError, match="股票"):
        parse_isin_page(page.encode("big5hkscs"), market="sii")
