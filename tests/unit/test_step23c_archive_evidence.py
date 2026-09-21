"""Step 23-c — what one archived iXBRL document proves about its own release.

The archive holds two different things and the file's own mtime tells them
apart (ROADMAP §23, audit §4.8):

* the daily job fetched 2025Q4, 2026Q1 and 2026Q2 as the filings appeared, so
  those files carry a first sighting — a `legacy_capture_bound` at the instant
  the file was written;
* every other file was written by a later bulk run — the February 2026
  re-fetch of 2020Q1–2025Q3, and the catch-up runs of 2026-08-01, 08-16 and
  08-17 — which noticed a filing long after it was published and therefore
  proves nothing about when. Those resolve by the statutory release rule.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from stock_data_center.ingestion.adapters.financial_filing_archive import (
    BACKFILL_RUN_DATES,
    LEGACY_CAPTURE_WINDOW_START,
    STATUTORY_RULE,
    archive_capture_bound,
)

TAIPEI = ZoneInfo("Asia/Taipei")


def mtime(text: str) -> datetime:
    """A file mtime, as the filesystem hands it over: UTC."""
    return datetime.fromisoformat(text).astimezone(UTC)


def test_a_daily_job_file_proves_a_capture_at_its_own_mtime() -> None:
    bound = archive_capture_bound(
        report_year=2026, report_quarter=1, mtime=mtime("2026-05-13 21:44:05+08:00")
    )
    assert bound == datetime(2026, 5, 13, 21, 44, 5, tzinfo=TAIPEI)


def test_the_capture_window_starts_at_2025q4() -> None:
    assert LEGACY_CAPTURE_WINDOW_START == (2025, 4)
    assert (
        archive_capture_bound(
            report_year=2025,
            report_quarter=4,
            mtime=mtime("2026-03-14 20:00:00+08:00"),
        )
        is not None
    )
    # 2025Q3 and everything before it was re-fetched in February 2026. The
    # file is the same document, but nobody saw it then for the first time.
    assert (
        archive_capture_bound(
            report_year=2025,
            report_quarter=3,
            mtime=mtime("2026-02-23 11:00:00+08:00"),
        )
        is None
    )


@pytest.mark.parametrize("day", sorted(BACKFILL_RUN_DATES))
def test_a_backfill_run_date_proves_nothing(day: date) -> None:
    assert (
        archive_capture_bound(
            report_year=2026,
            report_quarter=2,
            mtime=datetime(day.year, day.month, day.day, 23, 30, tzinfo=TAIPEI),
        )
        is None
    )


def test_the_backfill_dates_are_the_runs_that_crossed_reporting_windows() -> None:
    """Each is a run that wrote files for quarters long closed (audit §4.8)."""
    assert BACKFILL_RUN_DATES == frozenset(
        {
            date(2026, 2, 21),
            date(2026, 2, 22),
            date(2026, 2, 23),
            date(2026, 2, 24),
            date(2026, 2, 26),
            date(2026, 8, 1),
            date(2026, 8, 16),
            date(2026, 8, 17),
        }
    )


def test_a_naive_mtime_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        archive_capture_bound(
            report_year=2026,
            report_quarter=1,
            mtime=datetime(2026, 5, 13, 21, 44),
        )


def test_the_rule_is_named_by_version() -> None:
    assert STATUTORY_RULE == ("financial_statements_general", 1)
