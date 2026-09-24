"""Step 35-d-3: the migration chain restarts at one baseline that builds schema v2.

The 45 migrations that built, reshaped and finally sat beside the v1 tables are
gone. A new database gets the v2 tables, their append-only triggers and the one
trigger function they share, and nothing from v1: no v1 table, view, function,
sequence or the pgcrypto extension the v1 business hashes needed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from conftest import alembic_config, alembic_head
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError

from stock_data_center.db.schema_v2 import metadata
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

V2_TABLES = set(metadata.tables)
# Step 26: derived tables follow the latest inputs and are recomputed in place.
DERIVED = {"technical_indicators", "institutional_streaks"}
BASELINE_TABLES = V2_TABLES - DERIVED
# `stocks` is today's list, replaced when the list changes; `trading_days` is a
# calendar. Every other table holds history and only grows.
APPEND_ONLY = BASELINE_TABLES - {"stocks", "trading_days"}


def _catalog(url: str) -> dict[str, set[str]]:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            def names(sql: str) -> set[str]:
                return set(connection.scalars(sa.text(sql)))

            return {
                "tables": names("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"),
                "views": names("SELECT viewname FROM pg_views WHERE schemaname = 'public'"),
                "functions": names(
                    "SELECT p.proname FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'"
                ),
                "extensions": names("SELECT extname FROM pg_extension"),
                "triggers": names(
                    "SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND NOT t.tgisinternal"
                ),
            }
    finally:
        engine.dispose()


def test_the_chain_starts_at_one_baseline() -> None:
    revisions = list(ScriptDirectory.from_config(alembic_config()).walk_revisions())
    assert [r.revision for r in revisions if r.down_revision is None] == ["a273160c0288"]


def test_the_derived_tables_are_the_only_ones_after_the_baseline(
    empty_database_url: str,
) -> None:
    assert DERIVED <= V2_TABLES
    config = alembic_config(empty_database_url)
    command.upgrade(config, "a273160c0288")
    assert _catalog(empty_database_url)["tables"] == BASELINE_TABLES | {"alembic_version"}
    command.upgrade(config, "head")
    assert _catalog(empty_database_url)["tables"] == V2_TABLES | {"alembic_version"}
    # Derived rows are recomputed from stored inputs, so dropping them loses no
    # history (CLAUDE.md §81): the downgrade needs no guard.
    command.downgrade(config, "a273160c0288")
    assert _catalog(empty_database_url)["tables"] == BASELINE_TABLES | {"alembic_version"}


def test_the_server_is_postgresql_18(engine: Engine) -> None:
    with engine.connect() as connection:
        version_num = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
    assert version_num >= 180000


def test_the_baseline_builds_exactly_schema_v2(empty_database_url: str) -> None:
    command.upgrade(alembic_config(empty_database_url), "head")

    catalog = _catalog(empty_database_url)
    assert catalog["tables"] == V2_TABLES | {"alembic_version"}
    assert catalog["views"] == set()
    assert catalog["functions"] == {"stockdc_reject_mutation"}
    assert catalog["extensions"] == {"plpgsql"}
    assert catalog["triggers"] == {
        f"{kind}_{table}" for table in APPEND_ONLY for kind in ("immutable", "no_truncate")
    }


def test_the_metadata_matches_the_baseline() -> None:
    command.check(alembic_config())


def test_an_empty_downgrade_drops_everything_and_upgrade_restores_it(
    empty_database_url: str,
) -> None:
    config = alembic_config(empty_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    catalog = _catalog(empty_database_url)
    assert catalog["tables"] == {"alembic_version"}
    assert catalog["functions"] == set()

    command.upgrade(config, "head")
    assert _catalog(empty_database_url)["tables"] == V2_TABLES | {"alembic_version"}


def test_the_downgrade_refuses_while_a_table_holds_rows(
    isolated_database_url: str, tmp_path
) -> None:
    from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore

    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            record_fetch(
                connection,
                FetchRecord("t", "t", "t", None, "gap_fill", "t", "abc", datetime.now(UTC)),
                content=b"raw", status="succeeded", store=LocalRawArtifactStore(tmp_path),
            )
        with pytest.raises(DBAPIError, match="fetches holds rows"):
            command.downgrade(alembic_config(isolated_database_url), "base")
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                alembic_head()
            )
            assert connection.scalar(sa.text("SELECT count(*) FROM fetches")) == 1
    finally:
        engine.dispose()
