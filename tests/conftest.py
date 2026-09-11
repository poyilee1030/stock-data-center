from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
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
