"""Step 22-b — the two pages of one market-month list different companies.

`_0` is the domestic list and `_1` the foreign and KY one. If a company ever
appeared on both, one month would hold two versions of one logical key from
one source and they would alternate (CLAUDE.md §30). The backfill's
reconciliation scans every stored month for it; this is the same check on the
captured pages, kept as a permanent fixture.
"""

from __future__ import annotations

from pathlib import Path

from stock_data_center.ingestion.adapters import MOPSSiiMonthlyRevenueAdapter
from stock_data_center.ingestion.models import MonthlyRevenueRequest, RevenuePage
from stock_data_center.ingestion.observations import RevenuePeriod

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
JULY = RevenuePeriod(2026, 7)


def codes(content: bytes, page: RevenuePage) -> set[str]:
    parsed = MOPSSiiMonthlyRevenueAdapter().parse(
        content, MonthlyRevenueRequest(JULY, page)
    )
    return {row.security_code for row in parsed.rows}


def test_the_two_pages_of_one_month_share_no_company() -> None:
    domestic = codes(
        (FIXTURES / "mops_t21sc03_sii_115_7_0.html").read_bytes(), RevenuePage.DOMESTIC
    )
    foreign = codes(
        (FIXTURES / "mops_t21sc03_sii_115_7_1.html").read_bytes(), RevenuePage.FOREIGN
    )
    assert domestic and foreign
    assert domestic & foreign == set()


def test_the_ky_issuers_are_on_the_foreign_page_only() -> None:
    """5871 中租-KY is what legacy's `_0`-only scraper never saw."""
    domestic = codes(
        (FIXTURES / "mops_t21sc03_sii_115_7_0.html").read_bytes(), RevenuePage.DOMESTIC
    )
    foreign = codes(
        (FIXTURES / "mops_t21sc03_sii_115_7_1.html").read_bytes(), RevenuePage.FOREIGN
    )
    assert "5871" in foreign
    assert "5871" not in domestic
