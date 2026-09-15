"""Step 16 — expected coverage, declared and queryable, and the gap report.

The validator cannot report a gap until it knows what a dataset *should* hold.
That knowledge is a declaration in the database, queryable on its own, because
Step 27 turns it into fetch jobs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from itertools import count

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.coverage import CoverageValidator, ExpectedCoverageService
from stock_data_center.market_calendar import (
    CalendarCoverageError,
    TradingCalendarObservation,
    TradingCalendarWriter,
)
from stock_data_center.market_calendar.models import CalendarLineageRef


pytestmark = pytest.mark.integration

_DIGESTS = count(0x36000)

CALENDAR = TradingCalendarWriter()
EXPECTED = ExpectedCoverageService()
VALIDATOR = CoverageValidator()

JULY_2024 = tuple(
    date(2024, 7, day)
    for day in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19, 22, 23, 26, 29, 30, 31)
)


def lineage(db: Connection, dataset: str = "trading_calendar", source: str = "twse"):
    run_id = db.execute(
        sa.text(
            "INSERT INTO ingest_runs (dataset_code, source, status, started_at) "
            "VALUES (:dataset, :source, 'succeeded', statement_timestamp()) RETURNING id"
        ),
        {"dataset": dataset, "source": source},
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


def declare_daily_price(db: Connection) -> None:
    """Step 17 will ship this declaration with its adapter; here it is a fixture."""
    db.execute(
        sa.text(
            "INSERT INTO dataset_catalog (dataset_code, description, schema_version) "
            "VALUES ('daily_price', 'daily price', 'v1') "
            "ON CONFLICT (dataset_code) DO NOTHING"
        )
    )
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_sources
                (dataset_code, source, supports_market_pit, supports_system_pit,
                 publication_time_quality, evidence_status, accepted_evidence_types,
                 is_canonical)
            VALUES ('daily_price', 'twse', true, true, 100, 'verified',
                    ARRAY['official'], true)
            ON CONFLICT (dataset_code, source) DO NOTHING
            """
        )
    )
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_expected_coverage
                (dataset_code, market, cadence, period_column, window_start, note)
            VALUES ('daily_price', 'TWSE', 'trading_day', 'trade_date',
                    DATE '2020-01-02', 'one whole-market file per trade date')
            ON CONFLICT (dataset_code, market) DO NOTHING
            """
        )
    )


def store_july(db: Connection) -> None:
    CALENDAR.append_month(
        db,
        source="twse",
        observation=TradingCalendarObservation(
            market="TWSE",
            calendar_month=date(2024, 7, 1),
            trading_days=JULY_2024,
            coverage_through=date(2024, 7, 31),
        ),
        lineage=lineage(db),
    )


def add_security(db: Connection, code: str) -> int:
    return db.execute(
        sa.text("INSERT INTO security (security_code) VALUES (:code) RETURNING id"),
        {"code": code},
    ).scalar_one()


def store_price(db: Connection, security_id: int, trade_date: date) -> None:
    refs = lineage(db, dataset="daily_price", source="twse")
    db.execute(
        sa.text(
            """
            INSERT INTO daily_price_versions
                (security_id, source, trade_date, open_price, high_price,
                 low_price, close_price, volume, raw_artifact_id, ingest_run_id)
            VALUES (:security, 'twse', :day, :p, :p, :p, :p, 1000,
                    CAST(:artifact AS uuid), CAST(:run AS uuid))
            """
        ),
        {
            "security": security_id,
            "day": trade_date,
            "p": Decimal("100.0"),
            "artifact": refs.raw_artifact_id,
            "run": refs.ingest_run_id,
        },
    )


def test_expected_coverage_is_declared_and_queryable_on_its_own(
    db: Connection,
) -> None:
    """Step 27 reads this directly; it is not a by-product of rendering a report."""
    migrated = {
        (row.dataset_code, row.market): row for row in EXPECTED.declarations(db)
    }
    calendar = migrated[("trading_calendar", "TWSE")]
    assert calendar.cadence == "calendar_month"
    assert calendar.period_column == "calendar_month"
    assert calendar.window_start == date(2020, 1, 1)

    declare_daily_price(db)
    daily = EXPECTED.declaration(db, dataset_code="daily_price", market="TWSE")
    assert daily.cadence == "trading_day"
    assert daily.period_column == "trade_date"
    assert daily.window_start == date(2020, 1, 2)


def test_an_undeclared_dataset_is_an_error_not_an_empty_expectation(
    db: Connection,
) -> None:
    with pytest.raises(LookupError):
        EXPECTED.expected_periods(
            db,
            dataset_code="daily_price",
            market="TWSE",
            start=date(2024, 7, 1),
            end=date(2024, 7, 2),
        )


def test_expected_periods_for_a_daily_dataset_are_the_trading_days(
    db: Connection,
) -> None:
    store_july(db)
    declare_daily_price(db)

    periods = EXPECTED.expected_periods(
        db,
        dataset_code="daily_price",
        market="TWSE",
        start=date(2024, 7, 22),
        end=date(2024, 7, 26),
    )

    # 07-24 and 07-25 were typhoon closures, so they are never expected.
    assert periods == (date(2024, 7, 22), date(2024, 7, 23), date(2024, 7, 26))


def test_expected_periods_for_a_monthly_dataset_are_month_starts(
    db: Connection,
) -> None:
    periods = EXPECTED.expected_periods(
        db,
        dataset_code="trading_calendar",
        market="TWSE",
        start=date(2024, 6, 15),
        end=date(2024, 8, 2),
    )

    assert periods == (date(2024, 6, 1), date(2024, 7, 1), date(2024, 8, 1))


def test_the_report_separates_closures_from_missing_data(db: Connection) -> None:
    store_july(db)
    declare_daily_price(db)
    security_id = add_security(db, "2330")
    for day in (date(2024, 7, 22), date(2024, 7, 26)):
        store_price(db, security_id, day)

    report = VALIDATOR.report(
        db,
        dataset_code="daily_price",
        market="TWSE",
        start=date(2024, 7, 22),
        end=date(2024, 7, 26),
    )

    assert report.observed == (date(2024, 7, 22), date(2024, 7, 26))
    assert report.missing == (date(2024, 7, 23),)
    assert report.non_trading_days == (
        date(2024, 7, 24),
        date(2024, 7, 25),
    )
    assert not set(report.missing) & set(report.non_trading_days)


def test_the_report_does_not_depend_on_todays_security_universe(
    db: Connection,
) -> None:
    """A date is covered or not; adding securities must not change that."""
    store_july(db)
    declare_daily_price(db)
    first = add_security(db, "2330")
    store_price(db, first, date(2024, 7, 22))

    before = VALIDATOR.report(
        db,
        dataset_code="daily_price",
        market="TWSE",
        start=date(2024, 7, 22),
        end=date(2024, 7, 23),
    )

    for code in ("2317", "2454"):
        store_price(db, add_security(db, code), date(2024, 7, 22))

    after = VALIDATOR.report(
        db,
        dataset_code="daily_price",
        market="TWSE",
        start=date(2024, 7, 22),
        end=date(2024, 7, 23),
    )

    assert before.observed == after.observed == (date(2024, 7, 22),)
    assert before.missing == after.missing == (date(2024, 7, 23),)


def test_the_report_refuses_a_range_the_calendar_does_not_cover(
    db: Connection,
) -> None:
    store_july(db)
    declare_daily_price(db)

    with pytest.raises(CalendarCoverageError):
        VALIDATOR.report(
            db,
            dataset_code="daily_price",
            market="TWSE",
            start=date(2024, 7, 22),
            end=date(2024, 8, 5),
        )


def test_a_fully_covered_range_reports_no_gap(db: Connection) -> None:
    store_july(db)
    declare_daily_price(db)
    security_id = add_security(db, "2330")
    for day in JULY_2024:
        store_price(db, security_id, day)

    report = VALIDATOR.report(
        db,
        dataset_code="daily_price",
        market="TWSE",
        start=date(2024, 7, 1),
        end=date(2024, 7, 31),
    )

    assert report.expected == JULY_2024
    assert report.missing == ()
    assert report.is_complete


def test_a_range_outside_the_declared_window_is_reported_as_out_of_scope(
    db: Connection,
) -> None:
    """v1 starts on 2020-01-02; earlier dates are not this dataset's gap."""
    store_july(db)
    declare_daily_price(db)

    report = VALIDATOR.report(
        db,
        dataset_code="daily_price",
        market="TWSE",
        start=date(2024, 7, 22),
        end=date(2024, 7, 23),
    )
    assert report.window_start == date(2020, 1, 2)
