"""Step 22-c — reading the legacy monthly-revenue archive.

`market.csv` is one file per month holding both markets. It is not an official
response: legacy's scraper wrote it, and `revswarm` later rewrote one cell per
row with the announcement date it recovered (audit §7.4). So the adapter reads
it as `legacy_archive` bytes and says exactly what each row is worth — a value
legacy captured, and a date whose meaning depends on which window it falls in.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.ingestion.adapters import LegacyMonthlyRevenueArchiveAdapter
from stock_data_center.ingestion.models import (
    MonthlyRevenueArchiveRequest,
    SourceDataError,
)
from stock_data_center.monthly_revenue.models import RevenuePeriod

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MARCH = RevenuePeriod(2021, 3)
JUNE = RevenuePeriod(2026, 6)
WINDOW_A = (FIXTURES / "legacy_market_202103.csv").read_bytes()
WINDOW_B = (FIXTURES / "legacy_market_202606.csv").read_bytes()


def parse(content: bytes, *, source: str, period: RevenuePeriod):
    adapter = LegacyMonthlyRevenueArchiveAdapter(source)
    return adapter.parse(content, MonthlyRevenueArchiveRequest(period))


def test_the_sii_adapter_reads_only_the_sii_rows() -> None:
    parsed = parse(WINDOW_A, source="mops_t21sc03_sii", period=MARCH)
    assert [row.security_code for row in parsed.rows] == ["1101", "1102", "2330", "2025"]


def test_the_otc_adapter_reads_only_the_otc_rows() -> None:
    parsed = parse(WINDOW_A, source="mops_t21sc03_otc", period=MARCH)
    assert [row.security_code for row in parsed.rows] == ["6488"]


def test_amounts_are_thousands_converted_to_twd() -> None:
    parsed = parse(WINDOW_A, source="mops_t21sc03_sii", period=MARCH)
    tsmc = next(row for row in parsed.rows if row.security_code == "2330")
    assert tsmc.observation.revenue == Decimal(129127388) * 1000
    assert tsmc.observation.cumulative_revenue == Decimal(362410230) * 1000
    assert tsmc.observation.currency == "TWD"


def test_the_recovered_date_is_read_as_a_date() -> None:
    parsed = parse(WINDOW_A, source="mops_t21sc03_sii", period=MARCH)
    assert {row.captured_on for row in parsed.rows} == {date(2021, 4, 9)}
    otc = parse(WINDOW_A, source="mops_t21sc03_otc", period=MARCH)
    assert otc.rows[0].captured_on == date(2021, 4, 10)


def test_a_blank_percentage_is_null_and_a_dash_note_is_kept() -> None:
    parsed = parse(WINDOW_B, source="mops_t21sc03_sii", period=JUNE)
    cement = next(row for row in parsed.rows if row.security_code == "1101")
    assert cement.observation.note == "-"
    blank = parse(WINDOW_A, source="mops_t21sc03_sii", period=MARCH)
    assert all(row.observation.mom_pct is not None for row in blank.rows)


def test_the_archive_states_its_own_markets_and_row_count() -> None:
    parsed = parse(WINDOW_B, source="mops_t21sc03_otc", period=JUNE)
    assert parsed.market == "otc"
    assert parsed.source_rows == 6
    assert len(parsed.rows) == 2


def test_an_unknown_header_fails_the_whole_file() -> None:
    broken = WINDOW_A.replace(b"publish_time", b"announced_on")
    with pytest.raises(SourceDataError) as error:
        parse(broken, source="mops_t21sc03_sii", period=MARCH)
    assert error.value.reason_code == "schema_mismatch"


def test_a_market_outside_the_v1_universe_fails_the_whole_file() -> None:
    """`rotc` and `pub` are rejected at the adapter boundary (CLAUDE.md §2)."""
    broken = WINDOW_A.replace(b",SII,20210409", b",ROTC,20210409", 1)
    with pytest.raises(SourceDataError) as error:
        parse(broken, source="mops_t21sc03_sii", period=MARCH)
    assert error.value.reason_code == "unrecognised_value"


def test_an_unreadable_date_fails_the_whole_file() -> None:
    broken = WINDOW_A.replace(b",20210409", b",2021-04-09", 1)
    with pytest.raises(SourceDataError) as error:
        parse(broken, source="mops_t21sc03_sii", period=MARCH)
    assert error.value.reason_code == "unrecognised_value"


def test_the_file_is_located_by_month_under_the_archive_root() -> None:
    adapter = LegacyMonthlyRevenueArchiveAdapter(
        "mops_t21sc03_sii", archive_root=Path("/archive")
    )
    resource = adapter.resource(MonthlyRevenueArchiveRequest(MARCH))
    assert resource.source_uri == "/archive/2021/2021M03/market.csv"
    assert resource.resource_key == (
        "mops_t21sc03_sii:monthly_revenue_archive:2021-03"
    )
