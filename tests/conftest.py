from __future__ import annotations

import os
import uuid
from datetime import date  # noqa: F401 - used in annotations
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine
from sqlalchemy.engine import make_url


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc",
)


def alembic_config(database_url: str = TEST_DATABASE_URL) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["database_url"] = database_url
    return config


def alembic_head(database_url: str = TEST_DATABASE_URL) -> str:
    """The single head revision of the migration chain.

    Tests that assert a blocked downgrade left the version untouched compare
    against this, not a literal, so adding a migration does not break them.
    """
    return ScriptDirectory.from_config(alembic_config(database_url)).get_current_head()


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    engine = sa.create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    command.upgrade(alembic_config(), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.fixture
def empty_database_url() -> Iterator[str]:
    """A brand-new database with no schema, for DDL emitted from metadata.

    Migrations are the only thing that builds the real schema, so nothing else
    ever asks the metadata to produce DDL — which is how a CHECK naming a column
    its table does not have can sit in metadata with the whole suite green.
    """
    base_url = make_url(TEST_DATABASE_URL)
    admin_url = base_url.set(database="postgres")
    database_name = f"stockdc_ddl_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
    try:
        yield base_url.set(database=database_name).render_as_string(
            hide_password=False
        )
    finally:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()


@pytest.fixture
def isolated_database_url() -> Iterator[str]:
    base_url = make_url(TEST_DATABASE_URL)
    admin_url = base_url.set(database="postgres")
    database_name = f"stockdc_concurrency_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

    test_url = base_url.set(database=database_name).render_as_string(
        hide_password=False
    )
    command.upgrade(alembic_config(test_url), "head")
    try:
        yield test_url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                sa.text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database_name
                      AND pid <> pg_backend_pid()
                    """
                ),
                {"database_name": database_name},
            )
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()


def store_trading_calendar(
    connection: Connection,
    *,
    month: "date",
    days=None,
    through: "date | None" = None,
    market: str = "TWSE",
) -> None:
    """One month of trading days, with the provenance the writer insists on.

    Any import whose evidence comes from a statutory deadline asks the calendar
    to move that deadline off a closed day, and the calendar refuses outside
    its imported coverage rather than guessing (Step 15-b). Tests that reach
    that path therefore have to put a month in first, and this is the one way
    they do it.

    `days` defaults to the month's weekdays, which is close enough for a test
    that only needs the deadline not to land on a weekend; a test that cares
    about a specific holiday passes its own.
    """

    import calendar as _calendar
    import hashlib
    from datetime import date as _date

    from stock_data_center.market_calendar import TradingCalendarWriter
    from stock_data_center.market_calendar.models import (
        CalendarLineageRef,
        TradingCalendarObservation,
    )

    last_day = _calendar.monthrange(month.year, month.month)[1]
    if days is None:
        days = tuple(
            _date(month.year, month.month, day)
            for day in range(1, last_day + 1)
            if _date(month.year, month.month, day).weekday() < 5
        )
    through = through or _date(month.year, month.month, last_day)
    digest = hashlib.sha256(f"{market}:{month}".encode()).hexdigest()
    run_id = connection.scalar(
        sa.text(
            "INSERT INTO ingest_runs (dataset_code, source, status, started_at, "
            " purpose) VALUES ('trading_calendar', 'twse', 'succeeded', now(), "
            "'gap_fill') RETURNING id"
        )
    )
    artifact_id = connection.scalar(
        sa.text(
            "INSERT INTO raw_artifacts (raw_artifact_hash, storage_uri, byte_size, "
            " media_type) VALUES (:h, :uri, 1, 'application/json') RETURNING id"
        ),
        {"h": digest, "uri": f"file://{digest[:2]}/{digest}"},
    )
    connection.execute(
        sa.text(
            "INSERT INTO raw_artifact_observations (raw_artifact_id, ingest_run_id, "
            " source_uri, fetched_at, artifact_origin) "
            "VALUES (:a, :r, 'https://x', now(), 'official_fetch')"
        ),
        {"a": artifact_id, "r": run_id},
    )
    TradingCalendarWriter().append_month(
        connection,
        source="twse",
        observation=TradingCalendarObservation(
            market=market,
            calendar_month=month,
            trading_days=tuple(days),
            coverage_through=through,
        ),
        lineage=CalendarLineageRef(artifact_id, run_id),
    )
    connection.commit()
