from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError

from conftest import alembic_config
from stock_data_center.market_data import (
    DailyPriceObservation,
    InvalidDateRangeError,
    LineageRef,
    MarketDataService,
    MarketDataWriter,
    PublicationObservation,
    SecurityMetadataObservation,
)
from stock_data_center.pit import MarketPITContext, SystemPITContext


pytestmark = pytest.mark.integration

WRITER = MarketDataWriter()
SERVICE = MarketDataService()


def configure_source(db: Connection, dataset: str, source: str) -> None:
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES (:dataset, :description, 'v1')
            ON CONFLICT (dataset_code) DO NOTHING
            """
        ),
        {"dataset": dataset, "description": dataset},
    )
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_sources (
                dataset_code, source, supports_market_pit, supports_system_pit,
                publication_time_quality, evidence_status,
                accepted_evidence_types, is_canonical
            ) VALUES (:dataset, :source, true, true, 100, 'verified',
                      ARRAY['official'], :canonical)
            """
        ),
        {"dataset": dataset, "source": source, "canonical": source == "twse"},
    )


def lineage(
    db: Connection,
    dataset: str,
    source: str,
    digest_character: str,
) -> LineageRef:
    run_id = db.execute(
        sa.text(
            """
            INSERT INTO ingest_runs (dataset_code, source, status, started_at)
            VALUES (:dataset, :source, 'succeeded', statement_timestamp())
            RETURNING id
            """
        ),
        {"dataset": dataset, "source": source},
    ).scalar_one()
    digest = digest_character * 64
    artifact_id = db.execute(
        sa.text(
            """
            INSERT INTO raw_artifacts (
                raw_artifact_hash, storage_uri, byte_size, media_type
            ) VALUES (:digest, :uri, 1, 'application/json')
            RETURNING id
            """
        ),
        {"digest": digest, "uri": f"data/raw/{digest[:2]}/{digest}"},
    ).scalar_one()
    db.execute(
        sa.text(
            """
            INSERT INTO raw_artifact_observations (
                raw_artifact_id, ingest_run_id, source_uri, fetched_at
            ) VALUES (:artifact, :run, :uri, statement_timestamp())
            """
        ),
        {
            "artifact": artifact_id,
            "run": run_id,
            "uri": f"https://{source}.test/{dataset}",
        },
    )
    return LineageRef(artifact_id, run_id)


def publication(published_at: datetime) -> PublicationObservation:
    return PublicationObservation(
        evidence_kind="assertion",
        published_at=published_at,
        evidence_source="exchange",
        evidence_type="official",
        quality_rank=100,
    )


def market_context(year: int) -> MarketPITContext:
    return MarketPITContext(
        information_as_of=datetime(year, 12, 31, 23, tzinfo=UTC),
        knowledge_as_of=datetime.now(UTC) + timedelta(days=1),
    )


def add_metadata(
    db: Connection,
    *,
    security_id: int,
    source: str,
    lineage_ref: LineageRef,
    observation: SecurityMetadataObservation,
    published_at: datetime,
) -> int:
    written = WRITER.append_security_metadata(
        db,
        security_id=security_id,
        source=source,
        observation=observation,
        lineage=lineage_ref,
    )
    WRITER.append_publication_evidence(
        db,
        dataset_code="security_metadata",
        source=source,
        version_id=written.version_id,
        observation=publication(published_at),
        lineage=lineage_ref,
    )
    return written.version_id


def add_price(
    db: Connection,
    *,
    security_id: int,
    source: str,
    lineage_ref: LineageRef,
    observation: DailyPriceObservation,
    published_at: datetime,
) -> int:
    written = WRITER.append_daily_price(
        db,
        security_id=security_id,
        source=source,
        observation=observation,
        lineage=lineage_ref,
    )
    WRITER.append_publication_evidence(
        db,
        dataset_code="daily_price",
        source=source,
        version_id=written.version_id,
        observation=publication(published_at),
        lineage=lineage_ref,
    )
    return written.version_id


def test_historical_universe_is_not_current_survivors_only(db: Connection) -> None:
    configure_source(db, "security_metadata", "twse")
    source_lineage = lineage(db, "security_metadata", "twse", "1")
    old_id = WRITER.register_security(db, security_code="1111")
    new_id = WRITER.register_security(db, security_code="2222")
    add_metadata(
        db,
        security_id=old_id,
        source="twse",
        lineage_ref=source_lineage,
        observation=SecurityMetadataObservation(
            effective_from=date(2010, 1, 1),
            market="TWSE",
            name="Old Listed Company",
            listed_on=date(2010, 1, 1),
            delisted_on=date(2021, 6, 1),
        ),
        published_at=datetime(2010, 1, 1, tzinfo=UTC),
    )
    add_metadata(
        db,
        security_id=new_id,
        source="twse",
        lineage_ref=source_lineage,
        observation=SecurityMetadataObservation(
            effective_from=date(2021, 7, 1),
            market="TWSE",
            name="New Listed Company",
            listed_on=date(2021, 7, 1),
        ),
        published_at=datetime(2021, 7, 1, tzinfo=UTC),
    )

    historical = SERVICE.security_universe(
        db,
        effective_on=date(2020, 1, 2),
        context=market_context(2020),
        source="twse",
    )
    current = SERVICE.security_universe(
        db,
        effective_on=date(2022, 1, 2),
        context=market_context(2022),
        source="twse",
    )

    assert [item.security_code for item in historical] == ["1111"]
    assert [item.security_code for item in current] == ["2222"]


def test_security_name_and_listing_history_are_queryable(db: Connection) -> None:
    configure_source(db, "security_metadata", "twse")
    source_lineage = lineage(db, "security_metadata", "twse", "2")
    security_id = WRITER.register_security(db, security_code="2330")
    add_metadata(
        db,
        security_id=security_id,
        source="twse",
        lineage_ref=source_lineage,
        observation=SecurityMetadataObservation(
            effective_from=date(1994, 9, 5),
            market="TWSE",
            effective_to=date(2020, 12, 31),
            name="Historical Name",
            industry="Semiconductor",
            listed_on=date(1994, 9, 5),
        ),
        published_at=datetime(1994, 9, 5, tzinfo=UTC),
    )
    add_metadata(
        db,
        security_id=security_id,
        source="twse",
        lineage_ref=source_lineage,
        observation=SecurityMetadataObservation(
            effective_from=date(2021, 1, 1),
            market="TWSE",
            name="Current Name",
            industry="Semiconductor",
            listed_on=date(1994, 9, 5),
        ),
        published_at=datetime(2021, 1, 1, tzinfo=UTC),
    )

    historical = SERVICE.security_state(
        db,
        security_code="2330",
        effective_on=date(2020, 6, 1),
        context=market_context(2020),
        source="twse",
    )
    current = SERVICE.security_state(
        db,
        security_code="2330",
        effective_on=date(2022, 6, 1),
        context=market_context(2022),
        source="twse",
    )

    assert historical is not None
    assert historical.record.data["name"] == "Historical Name"
    assert current is not None and current.record.data["name"] == "Current Name"
    assert (
        historical.record.provenance.raw_artifact_id
        == source_lineage.raw_artifact_id
    )


def test_market_transfer_is_effective_dated_on_one_stable_identity(
    db: Connection,
) -> None:
    configure_source(db, "security_metadata", "official")
    source_lineage = lineage(db, "security_metadata", "official", "c")
    security_id = WRITER.register_security(db, security_code="5236")
    add_metadata(
        db,
        security_id=security_id,
        source="official",
        lineage_ref=source_lineage,
        observation=SecurityMetadataObservation(
            effective_from=date(2020, 1, 1),
            effective_to=date(2026, 7, 15),
            market="TPEx",
            name="Market Transfer Company",
            listed_on=date(2020, 1, 1),
        ),
        published_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    add_metadata(
        db,
        security_id=security_id,
        source="official",
        lineage_ref=source_lineage,
        observation=SecurityMetadataObservation(
            effective_from=date(2026, 7, 16),
            market="TWSE",
            name="Market Transfer Company",
            listed_on=date(2020, 1, 1),
        ),
        published_at=datetime(2026, 7, 16, tzinfo=UTC),
    )

    before = SERVICE.security_state(
        db,
        security_code="5236",
        effective_on=date(2025, 6, 1),
        context=market_context(2025),
        source="official",
    )
    after = SERVICE.security_state(
        db,
        security_code="5236",
        effective_on=date(2026, 8, 1),
        context=market_context(2026),
        source="official",
    )
    tpex_before = SERVICE.security_universe(
        db,
        effective_on=date(2025, 6, 1),
        context=market_context(2025),
        source="official",
        market="TPEx",
    )
    twse_before = SERVICE.security_universe(
        db,
        effective_on=date(2025, 6, 1),
        context=market_context(2025),
        source="official",
        market="TWSE",
    )
    tpex_after = SERVICE.security_universe(
        db,
        effective_on=date(2026, 8, 1),
        context=market_context(2026),
        source="official",
        market="TPEx",
    )
    twse_after = SERVICE.security_universe(
        db,
        effective_on=date(2026, 8, 1),
        context=market_context(2026),
        source="official",
        market="TWSE",
    )

    assert before is not None and before.market == "TPEx"
    assert after is not None and after.market == "TWSE"
    assert before.security_id == after.security_id == security_id
    assert [item.security_code for item in tpex_before] == ["5236"]
    assert twse_before == ()
    assert tpex_after == ()
    assert [item.security_code for item in twse_after] == ["5236"]


def test_market_transfer_daily_sources_share_security_identity(
    db: Connection,
) -> None:
    configure_source(db, "daily_price", "tpex")
    configure_source(db, "daily_price", "twse")
    tpex_lineage = lineage(db, "daily_price", "tpex", "d")
    twse_lineage = lineage(db, "daily_price", "twse", "e")
    security_id = WRITER.register_security(db, security_code="5236")
    add_price(
        db,
        security_id=security_id,
        source="tpex",
        lineage_ref=tpex_lineage,
        observation=DailyPriceObservation(
            trade_date=date(2025, 6, 2), close_price=Decimal("80")
        ),
        published_at=datetime(2025, 6, 2, 6, tzinfo=UTC),
    )
    add_price(
        db,
        security_id=security_id,
        source="twse",
        lineage_ref=twse_lineage,
        observation=DailyPriceObservation(
            trade_date=date(2026, 8, 3), close_price=Decimal("100")
        ),
        published_at=datetime(2026, 8, 3, 6, tzinfo=UTC),
    )

    old_price = SERVICE.daily_price(
        db,
        security_code="5236",
        trade_date=date(2025, 6, 2),
        context=market_context(2025),
        source="tpex",
    )
    new_price = SERVICE.daily_price(
        db,
        security_code="5236",
        trade_date=date(2026, 8, 3),
        context=market_context(2026),
        source="twse",
    )

    assert old_price is not None and old_price.data["security_id"] == security_id
    assert new_price is not None and new_price.data["security_id"] == security_id
    assert old_price.source == "tpex"
    assert new_price.source == "twse"


def test_expired_latest_metadata_interval_does_not_revive_older_state(
    db: Connection,
) -> None:
    configure_source(db, "security_metadata", "twse")
    source_lineage = lineage(db, "security_metadata", "twse", "b")
    security_id = WRITER.register_security(db, security_code="1234")
    add_metadata(
        db,
        security_id=security_id,
        source="twse",
        lineage_ref=source_lineage,
        observation=SecurityMetadataObservation(
            effective_from=date(2010, 1, 1),
            market="TWSE",
            name="Original",
            listed_on=date(2010, 1, 1),
        ),
        published_at=datetime(2010, 1, 1, tzinfo=UTC),
    )
    add_metadata(
        db,
        security_id=security_id,
        source="twse",
        lineage_ref=source_lineage,
        observation=SecurityMetadataObservation(
            effective_from=date(2020, 1, 1),
            market="TWSE",
            effective_to=date(2020, 12, 31),
            name="Terminal State",
            listed_on=date(2010, 1, 1),
            delisted_on=date(2021, 1, 1),
        ),
        published_at=datetime(2020, 1, 1, tzinfo=UTC),
    )

    assert SERVICE.security_state(
        db,
        security_code="1234",
        effective_on=date(2022, 1, 1),
        context=market_context(2022),
        source="twse",
    ) is None


def test_daily_price_exposes_every_preserved_legacy_observable(db: Connection) -> None:
    configure_source(db, "daily_price", "twse")
    source_lineage = lineage(db, "daily_price", "twse", "3")
    security_id = WRITER.register_security(db, security_code="0050")
    observed = DailyPriceObservation(
        trade_date=date(2020, 1, 2),
        open_price=Decimal("100.1"),
        high_price=Decimal("105.2"),
        low_price=Decimal("99.8"),
        close_price=Decimal("104.5"),
        volume=Decimal("123456"),
        trade_value=Decimal("12800000.25"),
        trade_count=987,
        price_change=Decimal("2.5"),
        price_direction="up",
        bid_snapshot='[["104.4", "12"]]',
        ask_snapshot='[["104.5", "8"]]',
        last_bid_price=Decimal("104.4"),
        last_ask_price=Decimal("104.5"),
        last_bid_volume=Decimal("12"),
        last_ask_volume=Decimal("8"),
    )
    add_price(
        db,
        security_id=security_id,
        source="twse",
        lineage_ref=source_lineage,
        observation=observed,
        published_at=datetime(2020, 1, 2, 6, tzinfo=UTC),
    )

    resolved = SERVICE.daily_price(
        db,
        security_code="0050",
        trade_date=date(2020, 1, 2),
        context=market_context(2020),
        source="twse",
    )

    assert resolved is not None
    for field in observed.__dataclass_fields__:
        assert resolved.data[field] == getattr(observed, field)
    assert resolved.provenance.raw_artifact_id == source_lineage.raw_artifact_id
    assert resolved.authoritative_evidence is not None


def test_daily_price_revision_respects_knowledge_cutoff(db: Connection) -> None:
    configure_source(db, "daily_price", "twse")
    first_lineage = lineage(db, "daily_price", "twse", "4")
    second_lineage = lineage(db, "daily_price", "twse", "5")
    security_id = WRITER.register_security(db, security_code="2317")
    add_price(
        db,
        security_id=security_id,
        source="twse",
        lineage_ref=first_lineage,
        observation=DailyPriceObservation(
            trade_date=date(2020, 1, 2), close_price=Decimal("90")
        ),
        published_at=datetime(2020, 1, 2, 6, tzinfo=UTC),
    )
    old_knowledge = db.scalar(sa.select(sa.func.clock_timestamp()))
    add_price(
        db,
        security_id=security_id,
        source="twse",
        lineage_ref=second_lineage,
        observation=DailyPriceObservation(
            trade_date=date(2020, 1, 2), close_price=Decimal("91")
        ),
        published_at=datetime(2020, 1, 2, 7, tzinfo=UTC),
    )
    information = datetime(2020, 1, 3, tzinfo=UTC)

    historical = SERVICE.daily_price(
        db,
        security_code="2317",
        trade_date=date(2020, 1, 2),
        context=MarketPITContext(information, old_knowledge),
        source="twse",
    )
    current_best = SERVICE.daily_price(
        db,
        security_code="2317",
        trade_date=date(2020, 1, 2),
        context=MarketPITContext(
            information, datetime.now(UTC) + timedelta(days=1)
        ),
        source="twse",
    )

    assert historical is not None and historical.data["close_price"] == 90
    assert current_best is not None and current_best.data["close_price"] == 91


def test_daily_price_source_histories_remain_independent(db: Connection) -> None:
    configure_source(db, "daily_price", "twse")
    configure_source(db, "daily_price", "vendor")
    twse_lineage = lineage(db, "daily_price", "twse", "6")
    vendor_lineage = lineage(db, "daily_price", "vendor", "7")
    security_id = WRITER.register_security(db, security_code="2303")
    for source, source_lineage, close in (
        ("twse", twse_lineage, "50"),
        ("vendor", vendor_lineage, "51"),
    ):
        add_price(
            db,
            security_id=security_id,
            source=source,
            lineage_ref=source_lineage,
            observation=DailyPriceObservation(
                trade_date=date(2020, 1, 2), close_price=Decimal(close)
            ),
            published_at=datetime(2020, 1, 2, 6, tzinfo=UTC),
        )

    twse = SERVICE.daily_price(
        db,
        security_code="2303",
        trade_date=date(2020, 1, 2),
        context=market_context(2020),
        source="twse",
    )
    vendor = SERVICE.daily_price(
        db,
        security_code="2303",
        trade_date=date(2020, 1, 2),
        context=market_context(2020),
        source="vendor",
    )

    assert twse is not None and twse.data["close_price"] == 50
    assert vendor is not None and vendor.data["close_price"] == 51
    assert twse.provenance.raw_artifact_id != vendor.provenance.raw_artifact_id


def test_duplicate_fetch_preserves_lineage_without_false_revision(
    db: Connection,
) -> None:
    configure_source(db, "daily_price", "twse")
    first_lineage = lineage(db, "daily_price", "twse", "8")
    second_lineage = lineage(db, "daily_price", "twse", "9")
    security_id = WRITER.register_security(db, security_code="2882")
    observed = DailyPriceObservation(
        trade_date=date(2020, 1, 2), close_price=Decimal("40")
    )

    first = WRITER.append_daily_price(
        db,
        security_id=security_id,
        source="twse",
        observation=observed,
        lineage=first_lineage,
    )
    repeated = WRITER.append_daily_price(
        db,
        security_id=security_id,
        source="twse",
        observation=observed,
        lineage=second_lineage,
    )
    evidence = publication(datetime(2020, 1, 2, 6, tzinfo=UTC))
    first_evidence = WRITER.append_publication_evidence(
        db,
        dataset_code="daily_price",
        source="twse",
        version_id=first.version_id,
        observation=evidence,
        lineage=first_lineage,
    )
    repeated_evidence = WRITER.append_publication_evidence(
        db,
        dataset_code="daily_price",
        source="twse",
        version_id=first.version_id,
        observation=evidence,
        lineage=second_lineage,
    )

    assert first.created is True
    assert repeated.created is False
    assert repeated.version_id == first.version_id
    assert repeated_evidence == first_evidence
    assert db.scalar(sa.text("SELECT count(*) FROM daily_price_versions")) == 1
    assert db.scalar(sa.text("SELECT count(*) FROM publication_evidence")) == 1
    assert db.scalar(sa.text("SELECT count(*) FROM raw_artifact_observations")) == 2


def test_daily_history_uses_system_pit_and_validates_range(db: Connection) -> None:
    configure_source(db, "daily_price", "twse")
    source_lineage = lineage(db, "daily_price", "twse", "a")
    security_id = WRITER.register_security(db, security_code="2891")
    for day, close in ((2, "20"), (3, "21")):
        WRITER.append_daily_price(
            db,
            security_id=security_id,
            source="twse",
            observation=DailyPriceObservation(
                trade_date=date(2020, 1, day), close_price=Decimal(close)
            ),
            lineage=source_lineage,
        )
    context = SystemPITContext(datetime.now(UTC) + timedelta(days=1))

    history = SERVICE.daily_price_history(
        db,
        security_code="2891",
        start_date=date(2020, 1, 2),
        end_date=date(2020, 1, 3),
        context=context,
        source="twse",
    )
    assert [row.data["close_price"] for row in history] == [20, 21]

    with pytest.raises(InvalidDateRangeError):
        SERVICE.daily_price_history(
            db,
            security_code="2891",
            start_date=date(2020, 1, 3),
            end_date=date(2020, 1, 2),
            context=context,
            source="twse",
        )


def test_register_security_reuses_stable_code_identity(db: Connection) -> None:
    first = WRITER.register_security(db, security_code="1101")
    repeated = WRITER.register_security(db, security_code="1101")
    assert repeated == first


def test_security_identity_is_db_timestamped_and_immutable(db: Connection) -> None:
    created_at = db.execute(
        sa.text(
            """
            INSERT INTO security (security_code, created_at)
            VALUES ('7777', TIMESTAMPTZ '1900-01-01+00')
            RETURNING created_at
            """
        )
    ).scalar_one()
    assert created_at.year != 1900

    with pytest.raises(DBAPIError, match="append-only"):
        with db.begin_nested():
            db.execute(
                sa.text(
                    "UPDATE security SET security_code = '7778' "
                    "WHERE security_code = '7777'"
                )
            )
    with pytest.raises(DBAPIError, match="append-only"):
        with db.begin_nested():
            db.execute(sa.text("DELETE FROM security WHERE security_code = '7777'"))


def test_market_history_migration_backfills_and_rehashes_existing_metadata(
    isolated_database_url: str,
) -> None:
    config = alembic_config(isolated_database_url)
    command.downgrade(config, "d81b5c9a3f20")
    migration_engine = sa.create_engine(isolated_database_url)
    try:
        with migration_engine.begin() as connection:
            configure_source(connection, "security_metadata", "official")
            source_lineage = lineage(
                connection, "security_metadata", "official", "f"
            )
            security_id = connection.execute(
                sa.text(
                    """
                    INSERT INTO security (security_code, market)
                    VALUES ('5236', 'TPEx')
                    RETURNING id
                    """
                )
            ).scalar_one()
            old_hash = connection.execute(
                sa.text(
                    """
                    INSERT INTO security_metadata_versions (
                        security_id, source, effective_from, name, listed_on,
                        business_content_hash, ingested_at,
                        raw_artifact_id, ingest_run_id
                    ) VALUES (
                        :security, 'official', DATE '2020-01-01',
                        'Market Transfer Company', DATE '2020-01-01',
                        :hash, statement_timestamp(), :artifact, :run
                    ) RETURNING business_content_hash
                    """
                ),
                {
                    "security": security_id,
                    "hash": "0" * 64,
                    "artifact": source_lineage.raw_artifact_id,
                    "run": source_lineage.ingest_run_id,
                },
            ).scalar_one()
        migration_engine.dispose()

        command.upgrade(config, "head")
        migration_engine = sa.create_engine(isolated_database_url)
        with migration_engine.connect() as connection:
            migrated = connection.execute(
                sa.text(
                    """
                    SELECT market, business_content_hash
                    FROM security_metadata_versions
                    WHERE security_id = :security
                    """
                ),
                {"security": security_id},
            ).mappings().one()
            identity_columns = set(
                connection.scalars(
                    sa.text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'security'
                        """
                    )
                )
            )

            assert migrated["market"] == "TPEx"
            assert migrated["business_content_hash"] != old_hash
            assert "market" not in identity_columns
    finally:
        migration_engine.dispose()
