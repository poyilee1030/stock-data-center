"""Step 19-a — the columns the result feeds need, and their migration.

The exchanges publish share ratios per thousand with eight places, so the
canonical ratio needs eleven, and `權值+息值` is a signed difference. Before this
step PostgreSQL rounded the first silently and refused the second.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic import command
from conftest import alembic_config, alembic_head
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError
from test_phase8_market_reference import add_security, configure, lineage

from stock_data_center.market_reference import (
    CorporateActionObservation,
    MarketReferenceWriter,
    SignedTwdAmount,
    TwdAmount,
)

pytestmark = pytest.mark.integration
WRITER = MarketReferenceWriter()
BEFORE = "7a2c9e4d1b58"


def rights_issue(**overrides) -> CorporateActionObservation:
    terms = {
        "action_type": "ex_right",
        "ex_date": date(2024, 12, 12),
        "rights_ratio": Decimal("0.08002413128"),
        "subscription_price": TwdAmount(Decimal(33)),
        "close_before": TwdAmount(Decimal("30.75")),
        "official_reference_price": TwdAmount(Decimal("30.95")),
        "official_rights_dividend_value": SignedTwdAmount(Decimal("-0.204602")),
        "source_event_type": "除權",
    }
    return CorporateActionObservation(**{**terms, **overrides})


def write(connection: Connection, code: str, observation: CorporateActionObservation):
    configure(connection, "corporate_action", "tpex_exdailyq")
    event_id = WRITER.register_corporate_action_event(
        connection,
        security_id=add_security(connection, code),
        source="tpex_exdailyq",
        source_event_key="exDailyQ:20241212",
    )
    return event_id, WRITER.append_corporate_action(
        connection,
        event_id=event_id,
        source="tpex_exdailyq",
        observation=observation,
        lineage=lineage(connection, "corporate_action", "tpex_exdailyq"),
    )


def test_eleven_place_ratios_and_negative_differences_are_stored_exactly(
    db: Connection,
) -> None:
    _, written = write(db, "8444-step19a", rights_issue())
    stored = db.execute(
        sa.text(
            "SELECT rights_ratio, official_rights_dividend_value "
            "FROM corporate_action_versions WHERE id = :id"
        ),
        {"id": written.version_id},
    ).one()
    assert stored.rights_ratio == Decimal("0.08002413128")
    assert stored.official_rights_dividend_value == Decimal("-0.204602")


def test_the_other_prices_still_refuse_a_negative(db: Connection) -> None:
    _, written = write(db, "8444-step19a-neg", rights_issue())
    with pytest.raises(DBAPIError) as refused, db.begin_nested():
        db.execute(
            sa.text(
                """
                    INSERT INTO corporate_action_versions (
                        event_id, source, action_type, ex_date, close_before,
                        business_content_hash, ingested_at, raw_artifact_id,
                        ingest_run_id
                    )
                    SELECT event_id, source, action_type, ex_date, -1,
                           repeat('1', 64), ingested_at, raw_artifact_id,
                           ingest_run_id
                      FROM corporate_action_versions WHERE id = :id
                    """
            ),
            {"id": written.version_id},
        )
    assert refused.value.orig.sqlstate == "23514"


def test_history_written_before_the_widening_keeps_deduplicating(
    isolated_database_url: str,
) -> None:
    """The business hash is computed from each value's text, and widening a
    column's scale changes that text: 0.14000000 becomes 0.140000000000. Without
    rehashing, re-importing an unchanged event would create a fake revision."""
    config = alembic_config(isolated_database_url)
    command.downgrade(config, BEFORE)
    engine = sa.create_engine(isolated_database_url)
    unchanged = CorporateActionObservation(
        action_type="ex_right_dividend",
        ex_date=date(2024, 5, 29),
        cash_dividend_per_share=TwdAmount(Decimal(6)),
        free_share_ratio=Decimal("0.1"),
        official_rights_dividend_value=TwdAmount(Decimal("23.818182")),
    )
    try:
        with engine.begin() as connection:
            event_id, before = write(connection, "6712-step19a", unchanged)
        command.upgrade(config, "head")
        with engine.begin() as connection:
            widened = connection.scalar(
                sa.text(
                    "SELECT business_content_hash FROM corporate_action_versions "
                    "WHERE id = :id"
                ),
                {"id": before.version_id},
            )
            assert widened != before.business_content_hash
            again = WRITER.append_corporate_action(
                connection,
                event_id=event_id,
                source="tpex_exdailyq",
                observation=unchanged,
                lineage=lineage(connection, "corporate_action", "tpex_exdailyq"),
            )
            assert not again.created
            assert again.version_id == before.version_id
            assert again.business_content_hash == widened
        engine.dispose()

        command.downgrade(config, BEFORE)
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT business_content_hash FROM corporate_action_versions "
                    "WHERE id = :id"
                ),
                {"id": before.version_id},
            ) == before.business_content_hash
        command.upgrade(config, "head")
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "observation",
    [
        rights_issue(official_rights_dividend_value=None),
        rights_issue(rights_ratio=Decimal("0.1"), subscription_price=None),
    ],
    ids=["eleven-place-ratio", "negative-difference"],
)
def test_the_downgrade_refuses_history_the_old_columns_cannot_hold(
    isolated_database_url: str, observation: CorporateActionObservation
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            write(connection, "8444-step19a-down", observation)
        engine.dispose()
        with pytest.raises(DBAPIError) as blocked:
            command.downgrade(alembic_config(isolated_database_url), BEFORE)
        assert blocked.value.orig.sqlstate == "P0001"
        assert "cannot downgrade corporate-action terms" in str(blocked.value)
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == alembic_head(isolated_database_url)
            assert connection.scalar(
                sa.text(
                    "SELECT numeric_scale FROM information_schema.columns "
                    "WHERE table_name = 'corporate_action_versions' "
                    "AND column_name = 'rights_ratio'"
                )
            ) == 12
    finally:
        engine.dispose()
