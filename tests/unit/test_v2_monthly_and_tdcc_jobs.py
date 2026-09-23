"""Step 35-c-2: the v2 jobs for monthly revenue and TDCC, and their row mapping.

The column contract comes from `schema_v2`: a job fills every value column of
its table and nothing else; `published_at` is the writer's, not the mapper's.
Expected values are the ones migrated to `stockdc_backfill` from v1.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.v2 import monthly_revenue as mr
from stock_data_center.v2 import shareholding as sh

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
JULY = date(2026, 7, 1)
BOOKKEEPING = {"recorded_at", "fetch_id", "published_at"}


def _revenue_rows(key: str, fixture: str) -> list[dict]:
    job = mr.JOBS[key]
    return job.rows(job.adapter.parse((FIXTURES / fixture).read_bytes(), job.request(JULY)))


def _tdcc_rows() -> list[dict]:
    job = sh.JOBS["shareholding_distributions/tdcc_opendata"]
    content = (FIXTURES / "tdcc_od_1_5_20190628_slashed.csv").read_bytes()
    return job.rows(job.adapter.parse(content, job.request(None)))


def test_each_market_has_a_domestic_and_a_foreign_page_job() -> None:
    assert set(mr.JOBS) == {
        "monthly_revenues/mops_t21sc03_sii",
        "monthly_revenues/mops_t21sc03_sii/foreign",
        "monthly_revenues/mops_t21sc03_otc",
        "monthly_revenues/mops_t21sc03_otc/foreign",
    }
    # The fetch log keeps v1's dataset code and resource key, so resume continues.
    job = mr.JOBS["monthly_revenues/mops_t21sc03_sii/foreign"]
    assert job.dataset == "monthly_revenue"
    assert job.adapter.resource(job.request(JULY)).resource_key == (
        "mops_t21sc03_sii:monthly_revenue:2026-07:1"
    )
    assert set(sh.JOBS) == {"shareholding_distributions/tdcc_opendata"}


@pytest.mark.parametrize(("key", "fixture"), [
    ("monthly_revenues/mops_t21sc03_sii", "mops_t21sc03_sii_115_7_0.html"),
    ("monthly_revenues/mops_t21sc03_sii/foreign", "mops_t21sc03_sii_115_7_1.html"),
])
def test_a_revenue_job_fills_exactly_its_tables_columns(key: str, fixture: str) -> None:
    job = mr.JOBS[key]
    rows = _revenue_rows(key, fixture)
    assert rows
    expected = {c.name for c in job.table.columns} - BOOKKEEPING
    assert {frozenset(row) for row in rows} == {frozenset(expected)}


def test_revenue_values_match_the_migrated_rows() -> None:
    rows = {row["stock_id"]: row for row in _revenue_rows(
        "monthly_revenues/mops_t21sc03_sii", "mops_t21sc03_sii_115_7_0.html")}
    assert (rows["2330"]["revenue"], rows["2330"]["mom_pct"]) == (467_580_548_000, Decimal("5.62"))
    assert rows["1101"]["revenue_month"] == JULY
    assert type(rows["1101"]["cumulative_revenue"]) is int
    assert rows["1101"]["note"] == "-"
    foreign = {row["stock_id"] for row in _revenue_rows(
        "monthly_revenues/mops_t21sc03_sii/foreign", "mops_t21sc03_sii_115_7_1.html")}
    assert "1256" in foreign


def test_a_revenue_month_is_complete_once_the_month_after_next_begins() -> None:
    job = mr.JOBS["monthly_revenues/mops_t21sc03_sii"]
    # 2026-09-01 00:00 Asia/Taipei: filings due on 08-10, late filers into August.
    assert job.settled_at(JULY) == datetime(2026, 8, 31, 16, tzinfo=UTC)


def test_a_tdcc_row_is_one_week_of_all_seventeen_levels() -> None:
    job = sh.JOBS["shareholding_distributions/tdcc_opendata"]
    rows = {row["stock_id"]: row for row in _tdcc_rows()}
    expected = {c.name for c in job.table.columns} - BOOKKEEPING
    assert {frozenset(row) for row in rows.values()} == {frozenset(expected)}
    tsmc = rows["2330"]
    assert tsmc["snapshot_date"] == date(2019, 6, 28)
    assert (tsmc["holders_1"], tsmc["shares_15"], tsmc["percent_15"]) == (
        146394, 23754169105, Decimal("91.60"))
    assert (tsmc["adjustment_shares"], tsmc["total_holders"], tsmc["total_shares"]) == (
        0, 354991, 25930380458)
    assert type(tsmc["shares_1"]) is int
