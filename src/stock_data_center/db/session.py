from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine


def database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc",
    )


def create_database_engine(*, echo: bool = False) -> Engine:
    return create_engine(database_url(), echo=echo, pool_pre_ping=True)
