"""Step 38-a: parsing the exchanges' listing and delisting tables, and assembling spans.

Every fixture is an excerpt of an official response fetched 2026-09-25; the
companies in it are the cases the assembly rules were written for (ROADMAP Step
38, audit §4.11):

- 5236 凌陽創新 moved from TPEx to TWSE on 2026-07-16: two spans.
- 6757 台灣虎航 listed on the innovation board in 2023 and joined the main
  board on the ISIN list's date, 2024-11-29.
- 6423 億而得 left the innovation board for TPEx on 2026-01-22: only its TPEx
  span is in scope.
- 9188 精熙-DR is a TDR with a four-digit code: code format proves nothing.
- 2809 京城銀 was listed before the TWSE table begins; the ISIN lookup still
  shows it as 普通股.
- 1701 中化 was listed before the table begins and is gone from ISIN: its
  category cannot be proven.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from stock_data_center.v2 import listings as ls
from stock_data_center.v2.universe import ListedStock

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v2" / "listings"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _twse_listed():
    return ls.parse_twse_listed(_read("twse_newlisting_excerpt.json"))


def _twse_delisted():
    return ls.parse_twse_delisted(_read("twse_suspendlisting_excerpt.json"))


def _tpex(kind: str, year: int):
    parse = ls.parse_tpex_listed if kind == "latest" else ls.parse_tpex_delisted
    return parse(_read(f"tpex_{kind}_{year}_excerpt.json"), year)


def _events():
    listed = [*_twse_listed(), *_tpex("latest", 2006), *_tpex("latest", 2021),
              *_tpex("latest", 2026)]
    delisted = [*_twse_delisted(), *_tpex("deListed", 2011), *_tpex("deListed", 2022),
                *_tpex("deListed", 2026)]
    return listed, delisted


# Today's ISIN rows, as `C_public.jsp` lists them on 2026-09-23.
TODAY = [
    ListedStock("2330", "台積電", "sii", "半導體業", date(1994, 9, 5)),
    ListedStock("5236", "凌陽創新", "sii", "半導體業", date(2026, 7, 16)),
    ListedStock("6757", "台灣虎航", "sii", "航運業", date(2024, 11, 29)),
    ListedStock("6423", "億而得", "otc", "半導體業", date(2026, 1, 22)),
    ListedStock("3718", "中光電投控", "otc", "光電業", date(1999, 1, 20)),
]

LOOKUPS = {
    "2809": ls.parse_isin_lookup(_read("isin_lookup_2809.html"), "2809"),
    "1701": ls.parse_isin_lookup(_read("isin_lookup_1701.html"), "1701"),
    "6806": ls.parse_isin_lookup(_read("isin_lookup_6806.html"), "6806"),
}


def _assembly(today=TODAY, lookups=LOOKUPS):
    listed, delisted = _events()
    return ls.assemble([(stock, None) for stock in today], listed, delisted, lookups)


def _spans(assembly, stock_id):
    return sorted(((s.market, s.listed_on, s.delisted_on) for s in assembly.spans
                   if s.stock_id == stock_id), key=lambda s: (s[2] or date.max))


def _reasons(entries):
    return {(e.stock_id, e.market): e.reason for e in entries}


# ---------------------------------------------------------------- parsing


def test_twse_listing_rows_carry_the_trading_start_and_the_note() -> None:
    rows = {e.stock_id: e for e in _twse_listed()}
    assert rows["5236"].on == date(2026, 7, 16) and rows["5236"].market == "sii"
    assert rows["5236"].note == "櫃轉市"
    assert rows["6757"].on == date(2023, 8, 15) and rows["6757"].note == "創新板"
    assert rows["2888"].on == date(2002, 2, 19)


def test_twse_delisting_rows_are_roc_dates_and_keep_the_published_name() -> None:
    rows = {e.stock_id: e for e in _twse_delisted()}
    assert rows["2809"].on == date(2025, 10, 1) and rows["2809"].name == "京城銀"
    assert rows["9188"].name == "精熙-DR"
    assert rows["1505"].on == date(2001, 1, 20)


def test_tpex_rows_strip_the_code_and_parse_each_date_format() -> None:
    (row,) = _tpex("deListed", 2022)
    assert row.stock_id == "1752" and row.market == "otc"  # published as " 1752"
    assert row.on == date(2022, 1, 19)
    (latest,) = _tpex("latest", 2021)
    assert (latest.stock_id, latest.on) == ("5236", date(2021, 7, 29))


def test_a_tpex_answer_for_another_year_is_refused() -> None:
    # TPEx answers every unknown parameter with the current year (2026-09-25:
    # `year=2024` returned 2026), so the year must be checked, not assumed.
    with pytest.raises(ls.ListingFormatError, match="2025"):
        ls.parse_tpex_delisted(_read("tpex_deListed_2026_excerpt.json"), 2025)


@pytest.mark.parametrize("name, parse", [
    ("twse_newlisting_excerpt.json", ls.parse_twse_listed),
    ("twse_suspendlisting_excerpt.json", ls.parse_twse_delisted),
])
def test_changed_twse_fields_are_refused(name, parse) -> None:
    payload = json.loads(_read(name))
    payload["fields"] = [*payload["fields"][:-1], "新欄位"]
    with pytest.raises(ls.ListingFormatError, match="fields"):
        parse(json.dumps(payload, ensure_ascii=False).encode())


def test_changed_tpex_fields_are_refused() -> None:
    payload = json.loads(_read("tpex_deListed_2026_excerpt.json"))
    payload["tables"][0]["fields"][2] = "終止日期"
    with pytest.raises(ls.ListingFormatError, match="fields"):
        ls.parse_tpex_delisted(json.dumps(payload, ensure_ascii=False).encode(), 2026)


def test_a_page_short_of_its_declared_total_is_refused() -> None:
    # TPEx serves its delisting table ten rows at a time: 2020 declares 12 and
    # returns 10 (2026-09-25), and 3452's 2020-01-13 is on the missing page.
    with pytest.raises(ls.ListingFormatError, match="10 of 12"):
        ls.parse_tpex_delisted(_read("tpex_deListed_2020_page1.json"), 2020)
    payload = json.loads(_read("twse_newlisting_excerpt.json"))
    payload["total"] += 1
    with pytest.raises(ls.ListingFormatError, match="of"):
        ls.parse_twse_listed(json.dumps(payload, ensure_ascii=False).encode())


def test_the_paged_answer_completes_a_truncated_year() -> None:
    first, total = ls.tpex_delisted_first_page(_read("tpex_deListed_2020_page1.json"), 2020)
    assert (len(first), total) == (10, 12)
    rows = ls.parse_tpex_delisted_paged(_read("tpex_deListed_2020_all.json"), 2020, first,
                                        total)
    assert len(rows) == 12
    assert ("3452", date(2020, 1, 13)) in {(e.stock_id, e.on) for e in rows}
    assert "paging-size=12" in ls.TPEX_DELISTED_PAGED_URL.format(year=2020, size=12)


def test_a_paged_answer_that_disagrees_with_the_first_page_is_refused() -> None:
    first, total = ls.tpex_delisted_first_page(_read("tpex_deListed_2020_page1.json"), 2020)
    payload = json.loads(_read("tpex_deListed_2020_all.json"))
    payload["data"][0][2] = "109-12-24"
    with pytest.raises(ls.ListingFormatError, match="first page"):
        ls.parse_tpex_delisted_paged(json.dumps(payload, ensure_ascii=False).encode(), 2020,
                                     first, total)
    with pytest.raises(ls.ListingFormatError, match="13"):
        ls.parse_tpex_delisted_paged(_read("tpex_deListed_2020_all.json"), 2020, first, 13)


def test_the_isin_lookup_gives_the_category_or_nothing() -> None:
    found = LOOKUPS["2809"]
    assert (found.name, found.category, found.industry) == ("京城銀", "普通股", "金融保險業")
    assert found.market_label == "公開發行"
    assert LOOKUPS["1701"] is None  # the page redirects to class_nofind.html


def test_an_unrecognised_isin_lookup_page_is_refused() -> None:
    with pytest.raises(ls.ListingFormatError):
        ls.parse_isin_lookup(b"<html>maintenance</html>", "2809")


# ---------------------------------------------------------------- assembly


def test_a_transfer_between_markets_is_two_spans() -> None:
    assert _spans(_assembly(), "5236") == [
        ("otc", date(2021, 7, 29), date(2026, 7, 16)),
        ("sii", date(2026, 7, 16), None),
    ]


def test_a_move_from_the_innovation_board_starts_on_the_isin_date() -> None:
    # The TWSE table's 2023-08-15 is the innovation-board listing, outside the
    # universe (ADR-0026); the main-board span begins on the ISIN list's date.
    assert _spans(_assembly(), "6757") == [("sii", date(2024, 11, 29), None)]


def test_an_innovation_board_span_is_left_out() -> None:
    assembly = _assembly()
    assert _spans(assembly, "6423") == [("otc", date(2026, 1, 22), None)]
    assert _reasons(assembly.excluded)[("6423", "sii")] == "innovation_board"


def test_the_listing_table_date_wins_over_the_isin_date() -> None:
    # ISIN's 上市日 for 3718 is 1999-01-20; TPEx lists it from 2026-09-03, and
    # its first trade is 2026-09-03 (audit §4.11).
    assert _spans(_assembly(), "3718") == [("otc", date(2026, 9, 3), None)]


def test_a_listing_before_the_tables_begin_has_no_date() -> None:
    assert _spans(_assembly(), "2330") == [("sii", None, None)]


def test_a_delisted_company_proven_common_by_isin_is_kept() -> None:
    assembly = _assembly()
    assert _spans(assembly, "2809") == [("sii", None, date(2025, 10, 1))]
    company = next(c for c in assembly.companies if c.stock_id == "2809")
    assert (company.name, company.industry) == ("京城銀", "金融保險業")


def test_a_delisted_company_proven_by_its_listing_row_is_kept() -> None:
    assembly = _assembly()
    assert _spans(assembly, "3454") == [
        ("sii", date(2011, 7, 22), date(2026, 3, 27)),
    ]  # its TPEx span closed on 2011-07-22, before the v1 window
    assert _spans(assembly, "6806") == [("sii", date(2021, 11, 15), date(2026, 6, 23))]
    company = next(c for c in assembly.companies if c.stock_id == "3454")
    assert company.name == "晶睿" and company.industry is None


def test_an_unproven_category_is_quarantined_not_guessed() -> None:
    assembly = _assembly()
    quarantined = _reasons(assembly.quarantined)
    assert quarantined[("1701", "sii")] == "unproven_category"
    assert quarantined[("9188", "sii")] == "unproven_category"  # a TDR, four digits
    assert quarantined[("912398", "sii")] == "unproven_category"
    kept = {c.stock_id for c in assembly.companies}
    assert not kept & {"1701", "9188", "912398"}


def test_a_lookup_naming_another_category_is_left_out() -> None:
    lookups = {**LOOKUPS, "2809": ls.Lookup("2809", "京城銀", "公開發行", "特別股", None,
                                            date(1982, 3, 3))}
    assembly = _assembly(lookups=lookups)
    assert _reasons(assembly.excluded)[("2809", "sii")] == "category:特別股"
    assert _spans(assembly, "2809") == []


def test_spans_closed_before_the_window_are_not_kept() -> None:
    assembly = _assembly()
    assert _spans(assembly, "1505") == []  # delisted 2001-01-20
    assert not {("1505", "sii")} & set(_reasons(assembly.quarantined))
    assert assembly.before_window >= 4


def test_every_stock_on_todays_list_has_exactly_one_open_span() -> None:
    assembly = _assembly()
    open_spans = [s.stock_id for s in assembly.spans if s.delisted_on is None]
    assert sorted(open_spans) == sorted(stock.stock_id for stock in TODAY)


def test_a_listing_row_for_a_company_not_on_todays_list_is_quarantined() -> None:
    today = [stock for stock in TODAY if stock.stock_id != "5236"]
    assembly = _assembly(today=today)
    assert _reasons(assembly.quarantined)[("5236", "sii")] == "listed_not_on_isin"


def test_which_companies_need_an_isin_lookup() -> None:
    # Only a span inside the window whose listing the tables do not show.
    listed, delisted = _events()
    wanted = ls.needs_lookup([(stock, None) for stock in TODAY], listed, delisted)
    # 2888, 6288, 6806 and 3454 have their TWSE listing rows; 6423's is on the
    # innovation board; 1505 and the others left before 2020.
    assert wanted == {"2809", "1701", "9188", "912398", "5371", "1752"}


def test_assembly_does_not_depend_on_input_order() -> None:
    listed, delisted = _events()
    today = [(stock, None) for stock in TODAY]
    forward = ls.assemble(today, listed, delisted, LOOKUPS)
    backward = ls.assemble(today[::-1], listed[::-1], delisted[::-1], LOOKUPS)
    assert forward.spans == backward.spans  # sorted by `span_order`
    assert sorted(forward.companies) == sorted(backward.companies)


def test_two_listings_with_no_delisting_between_are_quarantined() -> None:
    # A shape no source row has shown yet: which listing opened the span is
    # ambiguous, so neither date is kept rather than one picked.
    listed = [ls.Event("7777", "otc", date(2021, 1, 4), "甲"),
              ls.Event("7777", "otc", date(2022, 1, 3), "甲")]
    closed = ls.assemble([], listed, [ls.Event("7777", "otc", date(2023, 1, 2), "甲")], {})
    assert _reasons(closed.quarantined) == {("7777", "otc"): "listed_twice"}
    assert closed.spans == []
    still_open = ls.assemble([], listed, [], {})
    assert _reasons(still_open.quarantined) == {("7777", "otc"): "listed_twice"}


# ---------------------------------------------------------------- code review of #66


def test_the_isin_lookup_carries_its_registration_date() -> None:
    assert LOOKUPS["2809"].registered_on == date(1982, 3, 3)


def test_a_lookup_registered_after_the_delisting_is_another_security() -> None:
    # A code can be reused (2301). A lookup registered after the delisting is not
    # the delisted security, so neither its category nor its name is used.
    reused = ls.Lookup("2809", "新公司", "上市", "股票", "其他業", date(2025, 11, 3))
    assembly = _assembly(lookups={**LOOKUPS, "2809": reused})
    assert _reasons(assembly.quarantined)[("2809", "sii")] == "isin_lookup_is_another_security"
    assert _spans(assembly, "2809") == []
    assert "2809" not in {c.stock_id for c in assembly.companies}


def test_a_relisting_without_a_listing_row_starts_on_the_isin_date() -> None:
    # Left TPEx in 2021, back in 2023 with no listing row: the ISIN date is the
    # only evidence of the return, and a NULL start would claim it never left.
    lookup = ls.Lookup("7777", "甲", "上櫃", "股票", None, date(2019, 5, 2))
    today = [(ListedStock("7777", "甲", "otc", None, date(2023, 5, 2)), None)]
    left = [ls.Event("7777", "otc", date(2021, 3, 1), "甲")]
    assembly = ls.assemble(today, [], left, {"7777": lookup})
    assert [(s.listed_on, s.delisted_on) for s in assembly.spans] == [
        (None, date(2021, 3, 1)), (date(2023, 5, 2), None)]
    assert _reasons(assembly.warnings) == {("7777", "otc"): "relisted_without_listing_row"}


def test_a_relisting_whose_isin_date_predates_the_delisting_stays_unknown() -> None:
    lookup = ls.Lookup("7777", "甲", "上櫃", "股票", None, date(2019, 5, 2))
    today = [(ListedStock("7777", "甲", "otc", None, date(2019, 5, 2)), None)]
    left = [ls.Event("7777", "otc", date(2021, 3, 1), "甲")]
    assembly = ls.assemble(today, [], left, {"7777": lookup})
    opened = [s for s in assembly.spans if s.delisted_on is None]
    assert [(s.listed_on, s.listed_fetch_id) for s in opened] == [(None, None)]
    assert _reasons(assembly.warnings) == {("7777", "otc"): "relisted_without_listing_row"}


def test_an_ambiguous_open_listing_on_todays_list_starts_on_the_isin_date() -> None:
    listed = [ls.Event("7777", "otc", date(2021, 1, 4), "甲"),
              ls.Event("7777", "otc", date(2022, 1, 3), "甲")]
    today = [(ListedStock("7777", "甲", "otc", None, date(2022, 1, 3)), None)]
    assembly = ls.assemble(today, listed, [], {})
    assert _reasons(assembly.quarantined) == {("7777", "otc"): "listed_twice"}
    assert [(s.listed_on, s.delisted_on) for s in assembly.spans] == [(date(2022, 1, 3), None)]


@pytest.mark.parametrize("second", [date(2022, 6, 1), date(2021, 3, 1)])
def test_two_delistings_with_no_listing_between_are_quarantined(second) -> None:
    # The same date twice would also collide on the table's unique key.
    left = [ls.Event("7777", "otc", date(2021, 3, 1), "甲"),
            ls.Event("7777", "otc", second, "甲")]
    lookup = ls.Lookup("7777", "甲", "公開發行", "普通股", None, date(2010, 1, 4))
    assembly = ls.assemble([], [], left, {"7777": lookup})
    assert "delisted_twice" in {left.reason for left in assembly.quarantined}
    spans = [(s.market, s.delisted_on) for s in assembly.spans]
    assert len(spans) == len(set(spans))
    assert all(s.listed_on is not None or s.delisted_on == date(2021, 3, 1)
               for s in assembly.spans)
