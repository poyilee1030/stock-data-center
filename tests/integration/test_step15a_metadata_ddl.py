"""The metadata must be valid DDL on its own, not only as an Alembic target."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from stock_data_center.db.metadata import metadata


pytestmark = pytest.mark.integration


# Predicates a CHECK cannot express inline. The migrations own them; the list
# stays here, explicit and short, so a constraint that depends on some new
# undeclared function fails this test instead of only failing a real deploy.
REQUIRED_FUNCTIONS = {
    "stockdc_dates_sorted_distinct": """
        CREATE FUNCTION stockdc_dates_sorted_distinct(days date[])
        RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
            SELECT days = (
                SELECT array_agg(DISTINCT day ORDER BY day)
                  FROM unnest(days) AS day
            )
        $$;
    """,
}


def test_metadata_alone_builds_the_whole_schema(empty_database_url: str) -> None:
    """A constraint naming a column its table lacks fails only here.

    Alembic autogenerate does not compare CHECK constraints, so such a mistake
    is invisible to every other test in this suite. Two real ones were sitting
    in metadata when this test was written.
    """
    engine = sa.create_engine(empty_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
            for definition in REQUIRED_FUNCTIONS.values():
                connection.execute(sa.text(definition))

        metadata.create_all(engine, checkfirst=False)

        with engine.connect() as connection:
            built = set(sa.inspect(connection).get_table_names())
        assert set(metadata.tables) <= built
    finally:
        engine.dispose()
