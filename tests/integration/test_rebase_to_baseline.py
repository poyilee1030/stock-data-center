"""Step 35-d-3: a database built by the old chain is moved onto the baseline.

`stockdc_backfill` holds six years of v2 history under the old chain's head. The
rebase drops what is left of v1 and records the baseline as its revision, in one
transaction that commits only if the result is the schema a fresh baseline
builds and every v2 table still holds exactly the rows it held.

The fixture stands in for the old chain, which no longer exists: the baseline,
then v1-shaped leftovers of every kind the real old head has (a table with a
trigger and its function, a table referring to it, a view, a sequence of its
own, the pgcrypto extension) and the old head's revision.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from conftest import alembic_config, alembic_head

from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "rebase_to_baseline.py"
_spec = importlib.util.spec_from_file_location("rebase_to_baseline", SCRIPT)
rebase_to_baseline = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rebase_to_baseline
_spec.loader.exec_module(rebase_to_baseline)

V1_LEFTOVERS = """
CREATE EXTENSION pgcrypto;
CREATE FUNCTION stockdc_prepare_security_identity() RETURNS trigger LANGUAGE plpgsql
    AS $$ BEGIN RETURN NEW; END $$;
CREATE TABLE security (id bigserial PRIMARY KEY, security_code text NOT NULL);
CREATE TRIGGER prepare_security BEFORE INSERT ON security
    FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_security_identity();
CREATE TABLE publication_evidence (
    id bigserial PRIMARY KEY, security_id bigint REFERENCES security (id));
CREATE VIEW visible_securities AS SELECT id FROM security;
CREATE SEQUENCE market_index_id_seq;
INSERT INTO security (security_code) VALUES ('2330');
"""


@pytest.fixture
def old_head(empty_database_url: str, tmp_path) -> str:
    from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore

    command.upgrade(alembic_config(empty_database_url), "head")
    engine = sa.create_engine(empty_database_url)
    try:
        with engine.begin() as connection:
            fetch = record_fetch(
                connection,
                FetchRecord("t", "t", "t", None, "gap_fill", "t", "abc", datetime.now(UTC)),
                content=b"raw", status="succeeded", store=LocalRawArtifactStore(tmp_path),
            )
            connection.execute(sa.text(
                "INSERT INTO stocks (stock_id, name, market, fetch_id) "
                "VALUES ('2330', 'x', 'sii', :f)"), {"f": fetch})
            connection.exec_driver_sql(V1_LEFTOVERS)
            connection.execute(
                sa.text("UPDATE alembic_version SET version_num = :v"),
                {"v": rebase_to_baseline.OLD_HEAD},
            )
    finally:
        engine.dispose()
    return empty_database_url


def _state(url: str) -> tuple[str, int, int, list[str]]:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            return (
                connection.scalar(sa.text("SELECT version_num FROM alembic_version")),
                connection.scalar(sa.text("SELECT count(*) FROM fetches")),
                connection.scalar(sa.text("SELECT count(*) FROM stocks")),
                sorted(connection.scalars(sa.text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))),
            )
    finally:
        engine.dispose()


def test_the_plan_names_every_v1_object_and_changes_nothing(old_head: str) -> None:
    before = _state(old_head)

    plan = rebase_to_baseline.rebase(old_head, execute=False)

    assert plan.tables == ["publication_evidence", "security"]
    assert plan.views == ["visible_securities"]
    assert plan.functions == ["stockdc_prepare_security_identity"]
    assert plan.sequences == ["market_index_id_seq"]
    assert plan.extensions == ["pgcrypto"]
    assert plan.row_counts["fetches"] == 1 and plan.row_counts["stocks"] == 1
    assert _state(old_head) == before


def test_the_rebase_leaves_the_schema_a_fresh_baseline_builds(old_head: str) -> None:
    rebase_to_baseline.rebase(old_head, execute=True)

    version, fetches, stocks, _ = _state(old_head)
    assert (version, fetches, stocks) == (alembic_head(), 1, 1)
    engine = sa.create_engine(old_head)
    try:
        with engine.connect() as connection:
            rebased = rebase_to_baseline.schema_signature(connection)
    finally:
        engine.dispose()
    assert rebased == rebase_to_baseline.baseline_signature(old_head)
    # A second run finds nothing to do.
    assert rebase_to_baseline.rebase(old_head, execute=True).already_rebased


def test_a_drop_that_would_reach_into_v2_changes_nothing(old_head: str) -> None:
    engine = sa.create_engine(old_head)
    try:
        with engine.begin() as connection:
            # A v2 table depending on v1 is exactly what a plain DROP refuses;
            # the rebase never cascades.
            connection.exec_driver_sql(
                "ALTER TABLE fetches ADD COLUMN security_id bigint REFERENCES security (id)"
            )
    finally:
        engine.dispose()
    before = _state(old_head)

    with pytest.raises(sa.exc.DBAPIError):
        rebase_to_baseline.rebase(old_head, execute=True)

    assert _state(old_head) == before


def test_a_schema_that_differs_from_the_baseline_is_refused(old_head: str) -> None:
    engine = sa.create_engine(old_head)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE stocks ADD COLUMN nickname text")
    finally:
        engine.dispose()
    before = _state(old_head)

    with pytest.raises(rebase_to_baseline.RebaseRefused, match="stocks"):
        rebase_to_baseline.rebase(old_head, execute=True)

    assert _state(old_head) == before


def test_a_database_at_another_revision_is_refused(old_head: str) -> None:
    engine = sa.create_engine(old_head)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("UPDATE alembic_version SET version_num = 'd3b8c6f1a294'")
    finally:
        engine.dispose()

    with pytest.raises(rebase_to_baseline.RebaseRefused, match="d3b8c6f1a294"):
        rebase_to_baseline.rebase(old_head, execute=False)
