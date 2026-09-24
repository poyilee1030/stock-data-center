"""The metadata must be valid DDL on its own, not only as an Alembic target."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from stock_data_center.db.schema_v2 import metadata

pytestmark = pytest.mark.integration


def test_metadata_alone_builds_the_whole_schema(empty_database_url: str) -> None:
    """A constraint naming a column its table lacks fails only here.

    Alembic autogenerate does not compare CHECK constraints, so such a mistake
    is invisible to every other test in this suite. Two real ones were sitting
    in the v1 metadata when this test was written. The v2 tables need no
    extension and no helper function: every CHECK is inline SQL.
    """
    engine = sa.create_engine(empty_database_url)
    try:
        metadata.create_all(engine, checkfirst=False)

        with engine.connect() as connection:
            built = set(sa.inspect(connection).get_table_names())
        assert set(metadata.tables) == built
    finally:
        engine.dispose()
