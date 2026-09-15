"""Step 16 — reading the trading calendar, and refusing to answer beyond it.

A calendar that guesses is worse than one that refuses: a caller asking whether
an unimported date was a trading day must get an error, never `False`.
"""

from __future__ import annotations

from datetime import date
from itertools import count

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.market_calendar import (
    CalendarCoverageError,
    TradingCalendarObservation,
    TradingCalendarService,
    TradingCalendarWriter,
)
from stock_data_center.market_calendar.models import CalendarLineageRef


pytestmark = pytest.mark.integration

_DIGESTS = count(0x26000)

WRITER = TradingCalendarWriter()
SERVICE = TradingCalendarService()

JULY_2024 = tuple(
    date(2024, 7, day)
    for day in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19, 22, 23, 26, 29, 30, 31)
)
AUGUST_2024 = tuple(
    date(2024, 8, day)
    for day in (1, 2, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 19, 20, 21, 22, 23, 26, 27, 28, 29, 30)
)


def lineage(db: Connection) -> CalendarLineageRef:
    run_id = db.execute(
        sa.text(
            "INSERT INTO ingest_runs (dataset_code, source, status, started_at) "
            "VALUES ('trading_calendar', 'twse', 'succeeded', statement_timestamp()) "
            "RETURNING id"
        )
    ).scalar_one()
    digest = f"{next(_DIGESTS):064x}"
    artifact_id = db.execute(
        sa.text(
            "INSERT INTO raw_artifacts "
            "(raw_artifact_hash, storage_uri, byte_size, media_type) "
            "VALUES (:digest, :uri, 1, 'application/json') RETURNING id"
        ),
        {"digest": digest, "uri": f"data/raw/{digest[:2]}/{digest}"},
    ).scalar_one()
    db.execute(
        sa.text(
            "INSERT INTO raw_artifact_observations "
            "(raw_artifact_id, ingest_run_id, source_uri, fetched_at) "
            "VALUES (:artifact, :run, 'https://twse.test', statement_timestamp())"
        ),
        {"artifact": artifact_id, "run": run_id},
    )
    return CalendarLineageRef(artifact_id, run_id)


def store(
    db: Connection,
    month: date,
    days: tuple[date, ...],
    coverage_through: date | None = None,
):
    return WRITER.append_month(
        db,
        source="twse",
        observation=TradingCalendarObservation(
            market="TWSE",
            calendar_month=month,
            trading_days=days,
            coverage_through=coverage_through or max(days),
        ),
        lineage=lineage(db),
    )


def test_trading_days_are_returned_for_a_covered_range(db: Connection) -> None:
    store(db, date(2024, 7, 1), JULY_2024, date(2024, 7, 31))

    days = SERVICE.trading_days(db, market="TWSE", start=date(2024, 7, 1), end=date(2024, 7, 31))

    assert days == JULY_2024


def test_a_range_spanning_months_is_answered_from_both(db: Connection) -> None:
    store(db, date(2024, 7, 1), JULY_2024, date(2024, 7, 31))
    store(db, date(2024, 8, 1), AUGUST_2024, date(2024, 8, 31))

    days = SERVICE.trading_days(db, market="TWSE", start=date(2024, 7, 29), end=date(2024, 8, 2))

    assert days == (
        date(2024, 7, 29),
        date(2024, 7, 30),
        date(2024, 7, 31),
        date(2024, 8, 1),
        date(2024, 8, 2),
    )


def test_a_closure_is_not_a_trading_day(db: Connection) -> None:
    store(db, date(2024, 7, 1), JULY_2024, date(2024, 7, 31))

    assert SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 7, 23))
    assert not SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 7, 24))
    assert not SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 7, 25))
    assert SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 7, 26))


def test_an_uncovered_date_raises_instead_of_answering_false(db: Connection) -> None:
    store(db, date(2024, 7, 1), JULY_2024, date(2024, 7, 31))

    with pytest.raises(CalendarCoverageError):
        SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 8, 1))
    with pytest.raises(CalendarCoverageError):
        SERVICE.trading_days(
            db, market="TWSE", start=date(2024, 7, 1), end=date(2024, 8, 1)
        )


def test_a_partial_month_answers_only_within_its_coverage_bound(db: Connection) -> None:
    """Days after the bound are unknown, not closed."""
    store(
        db,
        date(2024, 7, 1),
        tuple(day for day in JULY_2024 if day <= date(2024, 7, 11)),
        date(2024, 7, 11),
    )

    assert SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 7, 11))
    with pytest.raises(CalendarCoverageError):
        SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 7, 12))


def test_next_trading_day_moves_a_deadline_off_a_closure(db: Connection) -> None:
    """ADR-0020 needs this: every release rule shifts to the next business day."""
    store(db, date(2024, 7, 1), JULY_2024, date(2024, 7, 31))

    assert SERVICE.next_trading_day_on_or_after(
        db, market="TWSE", day=date(2024, 7, 24)
    ) == date(2024, 7, 26)
    assert SERVICE.next_trading_day_on_or_after(
        db, market="TWSE", day=date(2024, 7, 26)
    ) == date(2024, 7, 26)


def test_next_trading_day_refuses_to_run_past_the_coverage_bound(
    db: Connection,
) -> None:
    """The last covered day still answers; a day beyond coverage must not."""
    store(db, date(2024, 7, 1), JULY_2024, date(2024, 7, 31))

    assert SERVICE.next_trading_day_on_or_after(
        db, market="TWSE", day=date(2024, 7, 31)
    ) == date(2024, 7, 31)

    with pytest.raises(CalendarCoverageError):
        SERVICE.next_trading_day_on_or_after(db, market="TWSE", day=date(2024, 8, 1))


def test_a_later_version_of_a_month_supersedes_the_earlier_one(
    db: Connection,
) -> None:
    """A corrected closure is a new version, and the latest one answers."""
    store(db, date(2024, 7, 1), JULY_2024, date(2024, 7, 31))
    corrected = tuple(sorted(JULY_2024 + (date(2024, 7, 24),)))
    store(db, date(2024, 7, 1), corrected, date(2024, 7, 31))

    assert SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 7, 24))
    assert SERVICE.trading_days(
        db, market="TWSE", start=date(2024, 7, 1), end=date(2024, 7, 31)
    ) == corrected


def test_an_identical_refetch_keeps_one_version_and_two_lineages(
    db: Connection,
) -> None:
    first = store(db, date(2024, 7, 1), JULY_2024, date(2024, 7, 31))
    again = store(db, date(2024, 7, 1), JULY_2024, date(2024, 7, 31))

    assert first.version_id == again.version_id
    assert first.created and not again.created
    observations = db.execute(
        sa.text(
            "SELECT count(*) FROM trading_calendar_version_observations "
            "WHERE calendar_version_id = :id"
        ),
        {"id": first.version_id},
    ).scalar_one()
    assert observations == 2


def test_coverage_through_reports_how_far_the_calendar_reaches(
    db: Connection,
) -> None:
    assert SERVICE.coverage_through(db, market="TWSE") is None

    store(db, date(2024, 7, 1), JULY_2024, date(2024, 7, 31))
    assert SERVICE.coverage_through(db, market="TWSE") == date(2024, 7, 31)

    store(db, date(2024, 8, 1), AUGUST_2024, date(2024, 8, 30))
    assert SERVICE.coverage_through(db, market="TWSE") == date(2024, 8, 30)


def test_a_gap_between_imported_months_is_not_silently_bridged(
    db: Connection,
) -> None:
    store(db, date(2024, 7, 1), JULY_2024, date(2024, 7, 31))
    store(db, date(2024, 9, 1), (date(2024, 9, 2),), date(2024, 9, 30))

    # August was never imported, so the calendar stops at the end of July.
    assert SERVICE.coverage_through(db, market="TWSE") == date(2024, 7, 31)
    with pytest.raises(CalendarCoverageError):
        SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 9, 2))
