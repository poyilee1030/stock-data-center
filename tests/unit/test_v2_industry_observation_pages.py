"""Step 39-b: the exchanges' by-category daily quotes, page by page.

The pages are the official answers fetched on 2026-09-26: TPEx
`afterTrading/otc?type=` gives each date's own classification, TWSE
`MI_INDEX?type=` today's classification rebuilt over history (audit §4.15).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from stock_data_center.v2 import industry
from stock_data_center.v2 import industry_observations as obs

PAGES = Path(__file__).resolve().parents[1] / "fixtures" / "v2" / "industry" / "observations"


def _page(name: str) -> bytes:
    return (PAGES / name).read_bytes()


def _edited(name: str, edit) -> bytes:
    payload = json.loads(_page(name))
    edit(payload)
    return json.dumps(payload, ensure_ascii=False).encode()


# ---------------------------------------------------------------- what is asked


def test_every_industry_code_is_asked_but_the_electronics_superset() -> None:
    tpex, twse = obs.codes_to_ask("tpex_otc_quotes"), obs.codes_to_ask("twse_mi_index")
    assert "13" not in tpex and "13" not in twse  # TWSE's 13 lists every 24-31 member again
    assert set(twse) == set(industry.NAMES) - {"13"}
    assert set(tpex) == set(twse) | {"80"}  # 80 is TPEx's 管理股票, not an industry
    assert len(set(tpex)) == len(tpex)


# ---------------------------------------------------------------- TPEx


def test_tpex_pages_are_the_classification_of_their_own_date() -> None:
    before = obs.parse_tpex_page(_page("tpex_20220701_18.json"), date(2022, 7, 1), "18")
    assert len(before) == 15 and {"5903", "5904", "5905", "9960"} <= set(before)
    assert obs.parse_tpex_page(_page("tpex_20230703_18.json"), date(2023, 7, 3), "18") == []
    after = obs.parse_tpex_page(_page("tpex_20230703_38.json"), date(2023, 7, 3), "38")
    assert {"5903", "5904"} <= set(after)


def test_tpex_labels_that_name_no_category_are_accepted() -> None:
    # 「其他」 for 20; 01 is a category TPEx does not have and answers with ''.
    assert len(obs.parse_tpex_page(_page("tpex_20240924_20.json"), date(2024, 9, 24), "20")) == 44
    assert obs.parse_tpex_page(_page("tpex_20240924_01.json"), date(2024, 9, 24), "01") == []
    assert obs.parse_tpex_page(_page("tpex_20240924_80.json"), date(2024, 9, 24), "80") == []


@pytest.mark.parametrize("label, code", [
    ("生技醫療類", "22"), ("半導體類", "24"), ("電腦及週邊類", "25"), ("光電業類", "26"),
    ("通信網路類", "27"), ("電子零組件類", "28"), ("電子通路類", "29"), ("資訊服務類", "30"),
    ("其他電子類", "31"), ("油電燃氣類", "23"), ("文化創意業", "32"), ("金融業", "17"),
    ("建材營造", "14"), ("觀光餐旅", "16"), ("觀光事業", "16"), ("貿易百貨", "18"),
    ("其他", "20"), ("居家生活", "38"), ("航運業", "15"),
])
def test_every_tpex_label_names_the_code_asked(label: str, code: str) -> None:
    assert obs.tpex_label_code(label) == code


def test_a_tpex_page_of_another_category_is_refused() -> None:
    def relabelled(payload):
        payload["tables"][0]["category"] = "金融業"

    with pytest.raises(obs.PageFormatError, match="category"):
        obs.parse_tpex_page(_edited("tpex_20220701_18.json", relabelled), date(2022, 7, 1), "18")


def test_a_tpex_page_of_another_date_is_refused() -> None:
    with pytest.raises(obs.PageFormatError, match="date"):
        obs.parse_tpex_page(_page("tpex_20220701_18.json"), date(2022, 7, 4), "18")

    def table_dated_otherwise(payload):
        payload["tables"][0]["date"] = "111/07/04"

    with pytest.raises(obs.PageFormatError, match="date"):
        obs.parse_tpex_page(_edited("tpex_20220701_18.json", table_dated_otherwise),
                            date(2022, 7, 1), "18")


@pytest.mark.parametrize("edit", [
    lambda p: p["tables"][0].update(totalCount=14),
    lambda p: p["tables"][0].update(fields=["名稱", "代號"] + p["tables"][0]["fields"][2:]),
    lambda p: p.update(stat="error"),
    lambda p: p.update(tables=p["tables"] * 2),
    lambda p: p["tables"][0]["data"][0].__setitem__(0, 5903),
    lambda p: p["tables"][0].update(data=None),
])
def test_a_tpex_page_this_code_does_not_understand_is_a_format_error(edit) -> None:
    with pytest.raises(obs.PageFormatError):
        obs.parse_tpex_page(_edited("tpex_20220701_18.json", edit), date(2022, 7, 1), "18")


def test_unreadable_bytes_are_a_format_error() -> None:
    for parse in (obs.parse_tpex_page, obs.parse_twse_page):
        with pytest.raises(obs.PageFormatError, match="unrecognised_layout"):
            parse(b"<html>busy</html>", date(2022, 7, 1), "18")
        with pytest.raises(obs.PageFormatError, match="unrecognised_layout"):
            parse(b"[1, 2]", date(2022, 7, 1), "18")


# ---------------------------------------------------------------- TWSE


def test_twse_pages_list_delisted_companies_under_their_last_category() -> None:
    finance = obs.parse_twse_page(_page("twse_20250711_17.json"), date(2025, 7, 11), "17")
    assert {"2888", "2867", "2809"} <= set(finance)
    assert "2881A" in finance  # preferred shares too; only stocks in `stocks` are stored
    assert "2499" in obs.parse_twse_page(_page("twse_20200406_26.json"), date(2020, 4, 6), "26")
    assert obs.parse_twse_page(_page("twse_20200406_19.json"), date(2020, 4, 6), "19") == []


def test_a_twse_page_of_another_category_or_date_is_refused() -> None:
    with pytest.raises(obs.PageFormatError, match="type"):
        obs.parse_twse_page(_page("twse_20250711_17.json"), date(2025, 7, 11), "18")
    with pytest.raises(obs.PageFormatError, match="date"):
        obs.parse_twse_page(_page("twse_20250711_17.json"), date(2025, 7, 14), "17")

    def retitled(payload):
        payload["tables"][8]["title"] = "114年07月11日 每日收盤行情(航運業)"

    with pytest.raises(obs.PageFormatError, match="category"):
        obs.parse_twse_page(_edited("twse_20250711_17.json", retitled), date(2025, 7, 11), "17")

    def title_dated_otherwise(payload):
        payload["tables"][8]["title"] = "114年07月14日 每日收盤行情(金融保險)"

    with pytest.raises(obs.PageFormatError, match="date"):
        obs.parse_twse_page(_edited("twse_20250711_17.json", title_dated_otherwise),
                            date(2025, 7, 11), "17")


def test_a_twse_page_with_two_quote_tables_is_refused() -> None:
    def doubled(payload):
        payload["tables"][9] = payload["tables"][8]

    with pytest.raises(obs.PageFormatError):
        obs.parse_twse_page(_edited("twse_20250711_17.json", doubled), date(2025, 7, 11), "17")


def test_a_twse_code_it_does_not_have_answers_an_empty_page() -> None:
    def unnamed(payload):  # how TWSE answers 32, 33, 34 and 80: 「每日收盤行情()」, no rows
        payload["tables"][8].update(title="114年07月11日 每日收盤行情()", data=[])
        payload["type"] = "33"

    assert obs.parse_twse_page(_edited("twse_20250711_17.json", unnamed),
                               date(2025, 7, 11), "33") == []

    def unnamed_with_rows(payload):
        payload["tables"][8]["title"] = "114年07月11日 每日收盤行情()"

    with pytest.raises(obs.PageFormatError, match="category"):
        obs.parse_twse_page(_edited("twse_20250711_17.json", unnamed_with_rows),
                            date(2025, 7, 11), "17")


# ---------------------------------------------------------------- one date's sweep


def test_a_sweep_places_each_stock_in_its_one_category() -> None:
    placed, managed = obs.check_sweep("tpex_otc_quotes", date(2022, 7, 1),
                                      {"18": ["5903", "5904"], "24": ["3105"], "80": ["4712"],
                                       "35": []}, fetched_on=date(2026, 9, 26))
    assert placed == {"5903": "18", "5904": "18", "3105": "24"}
    assert managed == ["4712"]


def test_a_stock_in_two_categories_is_refused() -> None:
    with pytest.raises(obs.PageFormatError, match="two_categories"):
        obs.check_sweep("tpex_otc_quotes", date(2022, 7, 1), {"18": ["5903"], "24": ["5903"]},
                        fetched_on=date(2026, 9, 26))
    with pytest.raises(obs.PageFormatError, match="two_categories"):
        obs.check_sweep("tpex_otc_quotes", date(2022, 7, 1), {"18": ["5903"], "80": ["5903"]},
                        fetched_on=date(2026, 9, 26))


def test_a_tpex_category_that_did_not_exist_yet_is_refused() -> None:
    # TPEx answers as of the date, so a 2022 page cannot list 居家生活 (from 2023-07-03).
    with pytest.raises(obs.PageFormatError, match="industry_not_in_effect"):
        obs.check_sweep("tpex_otc_quotes", date(2022, 7, 1), {"38": ["5903"]},
                        fetched_on=date(2026, 9, 26))
    with pytest.raises(obs.PageFormatError, match="industry_not_in_effect"):
        obs.check_sweep("tpex_otc_quotes", date(2023, 7, 3), {"18": ["5903"]},
                        fetched_on=date(2026, 9, 26))


def test_twse_is_checked_against_the_categories_of_the_day_it_was_fetched() -> None:
    # TWSE rebuilds history under today's categories: 2020 pages list 運動休閒.
    placed, _ = obs.check_sweep("twse_mi_index", date(2020, 4, 6), {"37": ["9914"]},
                                fetched_on=date(2026, 9, 26))
    assert placed == {"9914": "37"}
    with pytest.raises(obs.PageFormatError, match="industry_not_in_effect"):
        obs.check_sweep("twse_mi_index", date(2020, 4, 6), {"34": ["9914"]},
                        fetched_on=date(2026, 9, 26))


def test_a_sweep_with_nothing_on_any_page_is_refused() -> None:
    with pytest.raises(obs.PageFormatError, match="empty_sweep"):
        obs.check_sweep("tpex_otc_quotes", date(2020, 1, 4), {"20": [], "80": []},
                        fetched_on=date(2026, 9, 26))
