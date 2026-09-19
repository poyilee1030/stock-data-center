"""Step 22-a — the MOPS monthly-revenue adapter, against captured bytes.

Every fixture is a live response from 2026-09-20, fetched with the URL the
adapter builds: `mopsov.twse.com.tw/nas/t21/{sii|otc}/t21sc03_{ROC year}_{month}_{0|1}.html`
(audit §4.7). The page is Big5 as Microsoft writes it (cp950: strict big5
rejects it), one table per industry, each headed `單位：千元`, 11 cells a row:

    公司代號, 公司名稱, 當月營收, 上月營收, 去年當月營收, 上月比較增減(%),
    去年同月增減(%), 當月累計營收, 去年累計營收, 前期比較增減(%), 備註

`_0` lists domestic issuers and `_1` foreign/KY issuers; the two are disjoint.
Each industry table ends with a `合計` row, which is not a company. The page
title names the market and the ROC year-month; `出表日期` is the page's
generation date, not a publication time.

Amounts are 千元 and stored × 1,000 in TWD. The published comparatives are
stored exactly as published (ROADMAP Step 22, audit §7.3); a blank percentage
(no base to compare with) is NULL.

- `mops_t21sc03_sii_115_9_0_no_data.html` is a month not yet published: 查無資料.
- `mops_t21sc03_unreachable.html` is the 18 bytes the server answered once in
  place of a page; the next request succeeded.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from stock_data_center.ingestion.adapters import (
    MOPSOtcMonthlyRevenueAdapter,
    MOPSSiiMonthlyRevenueAdapter,
)
from stock_data_center.ingestion.models import (
    MonthlyRevenueRequest,
    RevenuePage,
    SourceDataError,
)
from stock_data_center.monthly_revenue.models import RevenuePeriod

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SII_0 = (FIXTURES / "mops_t21sc03_sii_115_7_0.html").read_bytes()
SII_1 = (FIXTURES / "mops_t21sc03_sii_115_7_1.html").read_bytes()
OTC_1_2020 = (FIXTURES / "mops_t21sc03_otc_109_1_1.html").read_bytes()
OTC_0_M06 = (FIXTURES / "mops_t21sc03_otc_115_6_0.html").read_bytes()
NO_DATA = (FIXTURES / "mops_t21sc03_sii_115_9_0_no_data.html").read_bytes()
UNREACHABLE = (FIXTURES / "mops_t21sc03_unreachable.html").read_bytes()

JULY = RevenuePeriod(2026, 7)
JUNE = RevenuePeriod(2026, 6)
JAN_2020 = RevenuePeriod(2020, 1)
SEPT = RevenuePeriod(2026, 9)


def sii(content: bytes = SII_0, period=JULY, page=RevenuePage.DOMESTIC):
    return MOPSSiiMonthlyRevenueAdapter().parse(content, MonthlyRevenueRequest(period, page))


def otc(content: bytes, period, page):
    return MOPSOtcMonthlyRevenueAdapter().parse(content, MonthlyRevenueRequest(period, page))


def edit(raw: bytes, old: str, new: str, count: int = 1) -> bytes:
    text = raw.decode("cp950")
    assert old in text, old
    return text.replace(old, new, count).encode("cp950")


def row_edit(raw: bytes, code: str, old: str, new: str) -> bytes:
    """Replace `old` inside one company's row only."""
    text = raw.decode("cp950")
    start = text.index(f"<tr align=right><td align=center>{code}</td>")
    end = text.index("</tr>", start)
    row = text[start:end]
    assert row.count(old) == 1, (code, old)
    return (text[:start] + row.replace(old, new) + text[end:]).encode("cp950")


def one(parsed, code: str):
    matches = [row for row in parsed.rows if row.security_code == code]
    assert len(matches) == 1, f"{code} appears {len(matches)} times"
    return matches[0].observation


def fails(reason: str, parse, content: bytes) -> None:
    with pytest.raises(SourceDataError) as error:
        parse(content)
    assert error.value.reason_code == reason


# ---- identity and requests -------------------------------------------------


def test_each_market_is_its_own_source() -> None:
    """A security moving market can appear on both markets' pages in one month
    (legacy 5236, 2026M06); one source would alternate its revisions."""
    assert MOPSSiiMonthlyRevenueAdapter.dataset_code == "monthly_revenue"
    assert MOPSSiiMonthlyRevenueAdapter.source == "mops_t21sc03_sii"
    assert MOPSOtcMonthlyRevenueAdapter.source == "mops_t21sc03_otc"


@pytest.mark.parametrize(
    "adapter, page, path, key",
    [
        (MOPSSiiMonthlyRevenueAdapter(), RevenuePage.DOMESTIC,
         "/nas/t21/sii/t21sc03_115_7_0.html", "mops_t21sc03_sii:monthly_revenue:2026-07:0"),
        (MOPSOtcMonthlyRevenueAdapter(), RevenuePage.FOREIGN,
         "/nas/t21/otc/t21sc03_115_7_1.html", "mops_t21sc03_otc:monthly_revenue:2026-07:1"),
    ],
)
def test_the_request_names_market_roc_period_and_page(adapter, page, path, key) -> None:
    resource = adapter.resource(MonthlyRevenueRequest(JULY, page))
    url = urlsplit(resource.source_uri)
    assert (url.scheme, url.netloc, url.path, url.query) == ("https", "mopsov.twse.com.tw", path, "")
    assert resource.resource_key == key


# ---- reading the page -------------------------------------------------------


def test_every_company_row_is_read_and_no_total_row() -> None:
    parsed = sii()
    assert len(parsed.rows) == 992
    assert all(row.security_code.isalnum() for row in parsed.rows)
    assert len(sii(SII_1, page=RevenuePage.FOREIGN).rows) == 94
    assert parsed.header_variant == "t21sc03_11"


def test_2330_is_stored_in_twd_with_its_comparatives_as_published() -> None:
    observation = one(sii(), "2330")
    assert observation.period == JULY
    assert observation.currency == "TWD"
    assert observation.revenue == Decimal(467580548000)
    assert observation.revenue_last_month == Decimal(442679969000)
    assert observation.revenue_last_year_month == Decimal(323165707000)
    assert observation.mom_pct == Decimal("5.62")
    assert observation.yoy_pct == Decimal("44.68")
    assert observation.cumulative_revenue == Decimal(2872064238000)
    assert observation.cumulative_revenue_last_year == Decimal(2096211240000)
    assert observation.cumulative_yoy_pct == Decimal("37.01")
    assert observation.note == "-"


def test_a_free_text_note_is_kept_verbatim() -> None:
    note = one(otc(OTC_0_M06, JUNE, RevenuePage.DOMESTIC), "6441").note
    assert note == "因客戶舊專案已全數出貨，新專案尚未開始出貨，使本期拉貨數量減少，致營收下降。"


def test_ky_issuers_come_from_the_foreign_page() -> None:
    """Legacy fetched only `_0` (`fetch_monthly_revenue.py`), so it has none."""
    assert one(sii(SII_1, page=RevenuePage.FOREIGN), "5871").revenue == Decimal(8463270000)
    assert one(otc(OTC_1_2020, JAN_2020, RevenuePage.FOREIGN), "1591").revenue == Decimal(45465000)
    assert not [row for row in sii().rows if row.security_code == "5871"]


def test_a_blank_percentage_is_null() -> None:
    raw = row_edit(SII_0, "2330", "5.62</td>", "</td>")
    assert one(sii(raw), "2330").mom_pct is None


def test_a_negative_percentage_is_kept() -> None:
    assert one(sii(), "1903").mom_pct == Decimal("-3.50")


# ---- what fails the file ---------------------------------------------------


def test_a_month_not_published_is_no_data() -> None:
    fails("no_data_for_period", lambda c: sii(c, period=SEPT), NO_DATA)


def test_an_unreachable_server_answer_is_unusable_not_data() -> None:
    fails("unusable_response", sii, UNREACHABLE)


def test_the_page_for_another_month_fails() -> None:
    fails("period_mismatch", lambda c: sii(c, period=JUNE), SII_0)


def test_the_page_for_another_market_fails() -> None:
    fails("market_mismatch", lambda c: otc(c, JULY, RevenuePage.DOMESTIC), SII_0)


def test_a_restated_unit_fails_the_file() -> None:
    fails("unit_declaration_changed", sii, edit(SII_0, "單位：千元", "單位：元"))


def test_a_changed_header_fails_the_file() -> None:
    fails("schema_mismatch", sii, edit(SII_0, "當月營收</th>", "本月營收</th>"))


def test_the_same_company_twice_fails_the_file() -> None:
    text = SII_0.decode("cp950")
    start = text.index("<tr align=right><td align=center>1101</td>")
    end = text.index("</tr>", start) + len("</tr>")
    row = text[start:end]
    fails("duplicate_security", sii, text.replace(row, row + row, 1).encode("cp950"))


def test_an_unreadable_amount_fails_the_file() -> None:
    raw = row_edit(SII_0, "1101", "13,744,103</td>", "N/A</td>")
    fails("unrecognised_value", sii, raw)


def test_a_blank_revenue_fails_the_file() -> None:
    raw = row_edit(SII_0, "1101", "13,744,103</td>", "</td>")
    fails("unrecognised_value", sii, raw)


def test_bytes_that_are_not_cp950_fail_the_file() -> None:
    fails("invalid_encoding", sii, SII_0[:2000] + b"\xff\xff" + SII_0[2000:])


def test_a_company_row_missing_a_cell_fails_the_file() -> None:
    """Every `<td>` row is a one-cell layout row or an 11-cell company row;
    anything else must not be dropped silently."""
    raw = row_edit(SII_0, "1101", "<td align=center>-</td>", "")
    fails("schema_mismatch", sii, raw)


def test_a_malformed_company_code_fails_the_file() -> None:
    raw = row_edit(SII_0, "1101", "<td align=center>1101</td>", "<td align=center>11 01</td>")
    fails("invalid_identity", sii, raw)
