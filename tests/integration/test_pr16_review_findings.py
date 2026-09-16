"""Step 16 — regressions for the code-review findings.

Each test here failed before its fix. They are kept because the original suite
missed them: the partial-month test only covered a partial month in *last*
position, so the contiguity bug was invisible.
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
    TradingCalendarService,
    TradingCalendarWriter,
)
from stock_data_center.market_calendar.models import CalendarLineageRef
from stock_data_center.pit.contracts import get_contract


pytestmark = pytest.mark.integration

_DIGESTS = count(0x46000)

CALENDAR = TradingCalendarWriter()
SERVICE = TradingCalendarService()
EXPECTED = ExpectedCoverageService()
VALIDATOR = CoverageValidator()


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


def store(
    db: Connection,
    month: date,
    days: tuple[date, ...],
    coverage_through: date,
    source: str = "twse",
    market: str = "TWSE",
) -> None:
    CALENDAR.append_month(
        db,
        source=source,
        observation=TradingCalendarObservation(
            market=market,
            calendar_month=month,
            trading_days=days,
            coverage_through=coverage_through,
        ),
        lineage=lineage(db, source=source),
    )


def test_a_partial_month_stops_coverage_even_when_a_later_month_exists(
    db: Connection,
) -> None:
    """Finding 1. The contiguity walk advanced on month presence alone.

    July was published only through the 11th; August is complete. The days
    between are days the source never published, so they must raise — not
    answer False, which is the calendar claiming a closure it never saw.
    """
    store(db, date(2024, 7, 1), (date(2024, 7, 1), date(2024, 7, 11)), date(2024, 7, 11))
    store(db, date(2024, 8, 1), (date(2024, 8, 1), date(2024, 8, 30)), date(2024, 8, 31))

    assert SERVICE.coverage_through(db, market="TWSE") == date(2024, 7, 11)

    assert SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 7, 11))
    with pytest.raises(CalendarCoverageError):
        SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 7, 12))
    with pytest.raises(CalendarCoverageError):
        SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 8, 1))


def test_a_second_source_does_not_get_unioned_into_the_canonical_calendar(
    db: Connection,
) -> None:
    """Finding 2. The partition included source; the outer filter did not."""
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_sources
                (dataset_code, source, supports_market_pit, supports_system_pit,
                 publication_time_quality, evidence_status, accepted_evidence_types,
                 is_canonical)
            VALUES ('trading_calendar', 'other', true, true, 0, 'verified',
                    ARRAY['official'], false)
            """
        )
    )
    store(db, date(2024, 7, 1), (date(2024, 7, 1), date(2024, 7, 31)), date(2024, 7, 31))
    store(
        db,
        date(2024, 7, 1),
        (date(2024, 7, 1), date(2024, 7, 24), date(2024, 7, 31)),
        date(2024, 7, 31),
        source="other",
    )

    # 07-24 exists only in the non-canonical source's calendar.
    assert not SERVICE.is_trading_day(db, market="TWSE", day=date(2024, 7, 24))
    assert SERVICE.trading_days(
        db, market="TWSE", start=date(2024, 7, 1), end=date(2024, 7, 31)
    ) == (date(2024, 7, 1), date(2024, 7, 31))


def test_the_calendar_has_a_pit_contract_so_its_own_report_runs(
    db: Connection,
) -> None:
    """Finding 4. report() on the dataset the migration declares used to crash."""
    contract = get_contract("trading_calendar")
    assert contract.version_table.name == "trading_calendar_versions"

    store(db, date(2024, 7, 1), (date(2024, 7, 1), date(2024, 7, 31)), date(2024, 7, 31))

    report = VALIDATOR.report(
        db,
        dataset_code="trading_calendar",
        market="TWSE",
        start=date(2024, 7, 1),
        end=date(2024, 8, 31),
    )

    assert report.expected == (date(2024, 7, 1), date(2024, 8, 1))
    assert report.observed == (date(2024, 7, 1),)
    assert report.missing == (date(2024, 8, 1),)


def declare_daily_price(db: Connection, market: str, source: str) -> None:
    db.execute(
        sa.text(
            "INSERT INTO dataset_catalog (dataset_code, description, schema_version) "
            "VALUES ('daily_price', :description, 'v1') "
            "ON CONFLICT (dataset_code) DO NOTHING"
        ),
        {"description": "daily price"},
    )
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_sources
                (dataset_code, source, supports_market_pit, supports_system_pit,
                 publication_time_quality, evidence_status, accepted_evidence_types,
                 is_canonical)
            VALUES ('daily_price', :source, true, true, 100, 'verified',
                    ARRAY['official'], false)
            ON CONFLICT (dataset_code, source) DO NOTHING
            """
        ),
        {"source": source},
    )
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_expected_coverage
                (dataset_code, market, source, calendar_market, cadence,
                 period_column, window_start, note)
            VALUES ('daily_price', :market, :source, 'TWSE', 'trading_day',
                    'trade_date', DATE '2020-01-02',
                    'TPEx opened on the same dates as TWSE on all 1,627 dates measured')
            ON CONFLICT (dataset_code, market) DO NOTHING
            """
        ),
        {"market": market, "source": source},
    )


def store_price(db: Connection, code: str, trade_date: date, source: str) -> None:
    security_id = db.execute(
        sa.text(
            "INSERT INTO security (security_code) VALUES (:code) "
            "ON CONFLICT (security_code) DO UPDATE SET security_code = EXCLUDED.security_code "
            "RETURNING id"
        ),
        {"code": code},
    ).scalar_one()
    refs = lineage(db, dataset="daily_price", source=source)
    db.execute(
        sa.text(
            """
            INSERT INTO daily_price_versions
                (security_id, source, trade_date, open_price, high_price,
                 low_price, close_price, volume, raw_artifact_id, ingest_run_id)
            VALUES (:security, :source, :day, :p, :p, :p, :p, 1000,
                    CAST(:artifact AS uuid), CAST(:run AS uuid))
            """
        ),
        {
            "security": security_id,
            "source": source,
            "day": trade_date,
            "p": Decimal("100.0"),
            "artifact": refs.raw_artifact_id,
            "run": refs.ingest_run_id,
        },
    )


def test_one_markets_rows_do_not_count_as_another_markets_coverage(
    db: Connection,
) -> None:
    """Finding 3. The observed query had no source predicate."""
    store(db, date(2024, 7, 1), (date(2024, 7, 1), date(2024, 7, 2)), date(2024, 7, 31))
    declare_daily_price(db, "TWSE", "twse_mi_index")
    declare_daily_price(db, "TPEx", "tpex_otc_quotes")
    store_price(db, "2330", date(2024, 7, 1), "twse_mi_index")

    twse = VALIDATOR.report(
        db, dataset_code="daily_price", market="TWSE",
        start=date(2024, 7, 1), end=date(2024, 7, 2),
    )
    tpex = VALIDATOR.report(
        db, dataset_code="daily_price", market="TPEx",
        start=date(2024, 7, 1), end=date(2024, 7, 2),
    )

    assert twse.observed == (date(2024, 7, 1),)
    assert tpex.observed == ()
    assert tpex.missing == (date(2024, 7, 1), date(2024, 7, 2))


def test_a_row_the_calendar_does_not_expect_is_surfaced_not_hidden(
    db: Connection,
) -> None:
    """Finding 5. Unexpected periods were filtered away instead of reported."""
    store(
        db,
        date(2024, 7, 1),
        (date(2024, 7, 1), date(2024, 7, 2), date(2024, 7, 31)),
        date(2024, 7, 31),
    )
    declare_daily_price(db, "TWSE", "twse_mi_index")
    store_price(db, "2330", date(2024, 7, 1), "twse_mi_index")
    # 07-03 is not in the calendar: a price row on it is an anomaly.
    store_price(db, "2317", date(2024, 7, 3), "twse_mi_index")

    report = VALIDATOR.report(
        db, dataset_code="daily_price", market="TWSE",
        start=date(2024, 7, 1), end=date(2024, 7, 3),
    )

    assert report.observed == (date(2024, 7, 1),)
    assert report.unexpected == (date(2024, 7, 3),)
    assert report.missing == (date(2024, 7, 2),)
    assert not report.is_complete


def test_non_trading_days_respect_the_declared_window(db: Connection) -> None:
    """Finding 6. The closure list used the caller's unclipped range."""
    store(db, date(2019, 12, 1), (date(2019, 12, 2),), date(2019, 12, 31))
    store(db, date(2020, 1, 1), (date(2020, 1, 2), date(2020, 1, 3)), date(2020, 1, 31))
    declare_daily_price(db, "TWSE", "twse_mi_index")

    report = VALIDATOR.report(
        db, dataset_code="daily_price", market="TWSE",
        start=date(2019, 12, 30), end=date(2020, 1, 3),
    )

    # The declaration starts on 2020-01-02, so nothing before it is this
    # dataset's closure or its gap.
    assert report.expected == (date(2020, 1, 2), date(2020, 1, 3))
    assert all(day >= date(2020, 1, 2) for day in report.non_trading_days)


def test_downgrade_refuses_to_destroy_imported_calendar_history(
    isolated_database_url: str,
) -> None:
    """Finding 8. The downgrade dropped the table and its history with it."""
    import pytest as _pytest
    from alembic import command
    from sqlalchemy.exc import DBAPIError

    from conftest import alembic_config, alembic_head

    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            with connection.begin():
                store(
                    connection,
                    date(2024, 7, 1),
                    (date(2024, 7, 1), date(2024, 7, 31)),
                    date(2024, 7, 31),
                )

        with _pytest.raises(DBAPIError) as blocked:
            command.downgrade(
                alembic_config(isolated_database_url), "7c9e2a4b6d81"
            )
        assert blocked.value.orig.sqlstate == "P0001"
        assert "do not delete calendar history" in str(blocked.value)

        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == alembic_head()
            assert connection.scalar(
                sa.text("SELECT count(*) FROM trading_calendar_versions")
            ) == 1
    finally:
        engine.dispose()
