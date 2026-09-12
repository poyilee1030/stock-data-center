from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError

from conftest import alembic_config
from stock_data_center.institutional_financing import (
    ForeignHoldingObservation,
    InstitutionalFinancingService,
    InstitutionalFinancingWriter,
    InstitutionalInvestorObservation,
    InstitutionalMarketSummaryObservation,
    MarginTradingObservation,
    SecuritiesLendingObservation,
    SourceLineageRef,
    SourcePublication,
)
from stock_data_center.pit import MarketPITContext, SystemPITContext


pytestmark = pytest.mark.integration

WRITER = InstitutionalFinancingWriter()
SERVICE = InstitutionalFinancingService()
TRADE_DATE = date(2025, 6, 2)
_DIGESTS = count(1000)


def configure_dataset(
    db: Connection,
    dataset_code: str,
    source: str,
    *,
    canonical: bool = True,
) -> None:
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES (:dataset, :description, 'v1')
            ON CONFLICT (dataset_code) DO NOTHING
            """
        ),
        {"dataset": dataset_code, "description": dataset_code},
    )
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_sources (
                dataset_code, source, supports_market_pit, supports_system_pit,
                publication_time_quality, evidence_status,
                accepted_evidence_types, is_canonical
            ) VALUES (
                :dataset, :source, true, true, 100, 'verified',
                ARRAY['official'], :canonical
            )
            """
        ),
        {"dataset": dataset_code, "source": source, "canonical": canonical},
    )


def add_security(db: Connection, code: str = "2330-phase7") -> int:
    return db.scalar(
        sa.text(
            "INSERT INTO security (security_code) VALUES (:code) RETURNING id"
        ),
        {"code": code},
    )


def lineage(db: Connection, dataset_code: str, source: str) -> SourceLineageRef:
    run_id = db.scalar(
        sa.text(
            """
            INSERT INTO ingest_runs (dataset_code, source, status, started_at)
            VALUES (:dataset, :source, 'succeeded', statement_timestamp())
            RETURNING id
            """
        ),
        {"dataset": dataset_code, "source": source},
    )
    digest = f"{next(_DIGESTS):064x}"
    artifact_id = db.scalar(
        sa.text(
            """
            INSERT INTO raw_artifacts (
                raw_artifact_hash, storage_uri, byte_size, media_type
            ) VALUES (:digest, :uri, 1, 'application/json') RETURNING id
            """
        ),
        {"digest": digest, "uri": f"data/raw/{digest[:2]}/{digest}"},
    )
    db.execute(
        sa.text(
            """
            INSERT INTO raw_artifact_observations (
                raw_artifact_id, ingest_run_id, source_uri, fetched_at
            ) VALUES (:artifact, :run, :uri, statement_timestamp())
            """
        ),
        {"artifact": artifact_id, "run": run_id, "uri": f"https://{source}.test"},
    )
    return SourceLineageRef(artifact_id, run_id)


def market(at: datetime, knowledge: datetime | None = None) -> MarketPITContext:
    return MarketPITContext(
        information_as_of=at,
        knowledge_as_of=knowledge or datetime.now(UTC) + timedelta(days=1),
    )


def publication(published_at: datetime | None) -> SourcePublication:
    return SourcePublication(
        evidence_kind="assertion" if published_at else "unknown",
        published_at=published_at,
        evidence_source="official endpoint",
        evidence_type="official",
        quality_rank=100,
    )


def test_all_phase7_domains_round_trip_with_source_faithful_fields(
    db: Connection,
) -> None:
    security_id = add_security(db)
    source = "twse"
    cases = (
        (
            "institutional_investor",
            InstitutionalInvestorObservation(
                TRADE_DATE,
                foreign_buy=Decimal("1000"),
                foreign_sell=Decimal("1200"),
                foreign_net=Decimal("-200"),
                trust_net=Decimal("75"),
                dealer_net=Decimal("-25"),
                total_net=Decimal("-150"),
            ),
            lambda line, obs: WRITER.append_institutional_investor(
                db, security_id=security_id, source=source, observation=obs, lineage=line
            ),
            SERVICE.institutional_investor,
            {"foreign_net": Decimal("-200"), "total_net": Decimal("-150")},
        ),
        (
            "foreign_holding",
            ForeignHoldingObservation(
                TRADE_DATE,
                issued_shares=Decimal("10000"),
                investable_shares=Decimal("8000"),
                held_shares=Decimal("5000"),
                held_ratio=Decimal("50.125"),
            ),
            lambda line, obs: WRITER.append_foreign_holding(
                db, security_id=security_id, source=source, observation=obs, lineage=line
            ),
            SERVICE.foreign_holding,
            {"held_shares": Decimal("5000"), "held_ratio": Decimal("50.125")},
        ),
        (
            "margin_trading",
            MarginTradingObservation(
                TRADE_DATE,
                margin_balance=Decimal("9000"),
                margin_next_limit=Decimal("20000"),
                margin_utilization_ratio=Decimal("45"),
                short_balance=Decimal("700"),
            ),
            lambda line, obs: WRITER.append_margin_trading(
                db, security_id=security_id, source=source, observation=obs, lineage=line
            ),
            SERVICE.margin_trading,
            {"margin_balance": Decimal("9000"), "short_balance": Decimal("700")},
        ),
        (
            "securities_lending",
            SecuritiesLendingObservation(
                TRADE_DATE,
                previous_balance=Decimal("4000"),
                borrowed=Decimal("300"),
                returned=Decimal("100"),
                balance=Decimal("4175"),
                adjustment=Decimal("-25"),
            ),
            lambda line, obs: WRITER.append_securities_lending(
                db, security_id=security_id, source=source, observation=obs, lineage=line
            ),
            SERVICE.securities_lending,
            {"balance": Decimal("4175"), "adjustment": Decimal("-25")},
        ),
    )
    published_at = datetime(2025, 6, 2, 10, tzinfo=UTC)
    for dataset_code, observation, write, read, expected in cases:
        configure_dataset(db, dataset_code, source)
        source_lineage = lineage(db, dataset_code, source)
        written = write(source_lineage, observation)
        WRITER.append_publication_evidence(
            db,
            dataset_code=dataset_code,
            source=source,
            version_id=written.version_id,
            publication=publication(published_at),
            lineage=source_lineage,
        )
        result = read(
            db,
            security_code="2330-phase7",
            trade_date=TRADE_DATE,
            context=market(published_at + timedelta(seconds=1)),
            source=source,
        )
        assert result is not None
        for name, value in expected.items():
            assert result.data[name] == value

    configure_dataset(db, "institutional_market_summary", source)
    summary_lineage = lineage(db, "institutional_market_summary", source)
    summary = WRITER.append_market_summary(
        db,
        source=source,
        observation=InstitutionalMarketSummaryObservation(
            TRADE_DATE,
            market="TWSE",
            institution="foreign",
            buy=Decimal("1000000"),
            sell=Decimal("1100000"),
            net=Decimal("-100000"),
        ),
        lineage=summary_lineage,
    )
    WRITER.append_publication_evidence(
        db,
        dataset_code="institutional_market_summary",
        source=source,
        version_id=summary.version_id,
        publication=publication(published_at),
        lineage=summary_lineage,
    )
    result = SERVICE.market_summary(
        db,
        market="TWSE",
        trade_date=TRADE_DATE,
        institution="foreign",
        context=market(published_at + timedelta(seconds=1)),
        source=source,
    )
    assert result is not None
    assert result.data["net"] == Decimal("-100000")


def test_repeat_fetch_reuses_revision_and_preserves_each_observation(
    db: Connection,
) -> None:
    dataset = "margin_trading"
    source = "twse"
    configure_dataset(db, dataset, source)
    security_id = add_security(db)
    observation = MarginTradingObservation(TRADE_DATE, margin_balance=Decimal("50"))
    first = WRITER.append_margin_trading(
        db,
        security_id=security_id,
        source=source,
        observation=observation,
        lineage=lineage(db, dataset, source),
    )
    second = WRITER.append_margin_trading(
        db,
        security_id=security_id,
        source=source,
        observation=observation,
        lineage=lineage(db, dataset, source),
    )
    observations = SERVICE.observations(
        db, dataset_code=dataset, version_id=first.version_id
    )
    assert first.created is True
    assert second.created is False
    assert second.version_id == first.version_id
    assert len(observations) == 2


def test_backfill_uses_actual_ingestion_and_evidence_knowledge_times(
    db: Connection,
) -> None:
    dataset = "securities_lending"
    source = "twse"
    configure_dataset(db, dataset, source)
    security_id = add_security(db)
    before_ingest = db.scalar(sa.select(sa.func.clock_timestamp()))
    source_lineage = lineage(db, dataset, source)
    written = WRITER.append_securities_lending(
        db,
        security_id=security_id,
        source=source,
        observation=SecuritiesLendingObservation(
            date(2019, 1, 2), balance=Decimal("500")
        ),
        lineage=source_lineage,
    )
    before_evidence = db.scalar(sa.select(sa.func.clock_timestamp()))
    WRITER.append_publication_evidence(
        db,
        dataset_code=dataset,
        source=source,
        version_id=written.version_id,
        publication=publication(datetime(2019, 1, 2, 8, tzinfo=UTC)),
        lineage=source_lineage,
    )
    after = datetime.now(UTC) + timedelta(days=1)

    assert SERVICE.securities_lending(
        db,
        security_code="2330-phase7",
        trade_date=date(2019, 1, 2),
        context=SystemPITContext(before_ingest),
        source=source,
    ) is None
    assert SERVICE.securities_lending(
        db,
        security_code="2330-phase7",
        trade_date=date(2019, 1, 2),
        context=SystemPITContext(after),
        source=source,
    ) is not None
    assert SERVICE.securities_lending(
        db,
        security_code="2330-phase7",
        trade_date=date(2019, 1, 2),
        context=market(datetime(2019, 1, 3, tzinfo=UTC), before_evidence),
        source=source,
    ) is None


def test_source_histories_are_independent_and_history_is_inclusive(
    db: Connection,
) -> None:
    dataset = "foreign_holding"
    configure_dataset(db, dataset, "twse", canonical=True)
    configure_dataset(db, dataset, "tpex", canonical=False)
    security_id = add_security(db)
    for source, value in (("twse", "100"), ("tpex", "200")):
        for day in (2, 3):
            WRITER.append_foreign_holding(
                db,
                security_id=security_id,
                source=source,
                observation=ForeignHoldingObservation(
                    date(2025, 6, day), held_shares=Decimal(value)
                ),
                lineage=lineage(db, dataset, source),
            )
    context = SystemPITContext(datetime.now(UTC) + timedelta(days=1))
    twse = SERVICE.history(
        db,
        dataset_code=dataset,
        security_code="2330-phase7",
        start_date=date(2025, 6, 2),
        end_date=date(2025, 6, 3),
        context=context,
        source="twse",
    )
    tpex = SERVICE.history(
        db,
        dataset_code=dataset,
        security_code="2330-phase7",
        start_date=date(2025, 6, 2),
        end_date=date(2025, 6, 3),
        context=context,
        source="tpex",
    )
    assert [row.data["held_shares"] for row in twse] == [Decimal("100")] * 2
    assert [row.data["held_shares"] for row in tpex] == [Decimal("200")] * 2


def test_publication_before_trade_date_and_wrong_lineage_are_rejected(
    db: Connection,
) -> None:
    dataset = "institutional_investor"
    source = "twse"
    configure_dataset(db, dataset, source)
    security_id = add_security(db)
    source_lineage = lineage(db, dataset, source)
    written = WRITER.append_institutional_investor(
        db,
        security_id=security_id,
        source=source,
        observation=InstitutionalInvestorObservation(
            TRADE_DATE, foreign_net=Decimal("-1")
        ),
        lineage=source_lineage,
    )
    with pytest.raises(DBAPIError) as early:
        with db.begin_nested():
            WRITER.append_publication_evidence(
                db,
                dataset_code=dataset,
                source=source,
                version_id=written.version_id,
                publication=publication(datetime(2025, 6, 1, 10, tzinfo=UTC)),
                lineage=source_lineage,
            )
    assert early.value.orig.sqlstate == "23514"

    configure_dataset(db, dataset, "tpex", canonical=False)
    wrong_lineage = lineage(db, dataset, "tpex")
    with pytest.raises(DBAPIError) as mismatch:
        with db.begin_nested():
            db.execute(
                sa.text(
                    """
                    INSERT INTO institutional_investor_version_observations (
                        institutional_investor_version_id,
                        raw_artifact_id, ingest_run_id
                    ) VALUES (:version, :artifact, :run)
                    """
                ),
                {
                    "version": written.version_id,
                    "artifact": wrong_lineage.raw_artifact_id,
                    "run": wrong_lineage.ingest_run_id,
                },
            )
    assert mismatch.value.orig.sqlstate == "23514"


def test_database_guards_values_and_generates_hash_and_ingestion_time(
    db: Connection,
) -> None:
    dataset = "securities_lending"
    source = "twse"
    configure_dataset(db, dataset, source)
    security_id = add_security(db)
    source_lineage = lineage(db, dataset, source)
    with pytest.raises(DBAPIError) as negative:
        with db.begin_nested():
            db.execute(
                sa.text(
                    """
                    INSERT INTO securities_lending_versions (
                        security_id, source, trade_date, borrowed,
                        business_content_hash, ingested_at,
                        raw_artifact_id, ingest_run_id
                    ) VALUES (
                        :security, :source, :trade_date, -1,
                        repeat('f', 64), '2000-01-01T00:00:00Z',
                        :artifact, :run
                    )
                    """
                ),
                {
                    "security": security_id,
                    "source": source,
                    "trade_date": TRADE_DATE,
                    "artifact": source_lineage.raw_artifact_id,
                    "run": source_lineage.ingest_run_id,
                },
            )
    assert negative.value.orig.sqlstate == "23514"

    written = WRITER.append_securities_lending(
        db,
        security_id=security_id,
        source=source,
        observation=SecuritiesLendingObservation(
            TRADE_DATE, balance=Decimal("10"), adjustment=Decimal("-3")
        ),
        lineage=source_lineage,
    )
    assert written.business_content_hash != "0" * 64
    assert written.ingested_at.year != 2000


def test_phase7_observation_links_are_append_only(db: Connection) -> None:
    dataset = "margin_trading"
    source = "twse"
    configure_dataset(db, dataset, source)
    security_id = add_security(db)
    written = WRITER.append_margin_trading(
        db,
        security_id=security_id,
        source=source,
        observation=MarginTradingObservation(TRADE_DATE, margin_balance=Decimal("1")),
        lineage=lineage(db, dataset, source),
    )
    with pytest.raises(DBAPIError) as mutation:
        with db.begin_nested():
            db.execute(
                sa.text(
                    """
                    DELETE FROM margin_trading_version_observations
                    WHERE margin_trading_version_id = :version
                    """
                ),
                {"version": written.version_id},
            )
    assert mutation.value.orig.sqlstate == "55000"


def test_populated_phase7_migration_backfills_lineage_and_round_trips_hash(
    isolated_database_url: str,
) -> None:
    config = alembic_config(isolated_database_url)
    command.downgrade(config, "a4c7e2d91b36")
    isolated_engine = sa.create_engine(isolated_database_url)
    try:
        with isolated_engine.begin() as connection:
            configure_dataset(
                connection, "institutional_market_summary", "twse"
            )
            source_lineage = lineage(
                connection, "institutional_market_summary", "twse"
            )
            before = connection.execute(
                sa.text(
                    """
                    INSERT INTO institutional_market_summary_versions (
                        market, source, trade_date, institution, buy, sell, net,
                        business_content_hash, ingested_at,
                        raw_artifact_id, ingest_run_id
                    ) VALUES (
                        'TWSE', 'twse', DATE '2025-06-02', 'foreign',
                        10, 12, -2, repeat('0', 64),
                        TIMESTAMPTZ '2000-01-01T00:00:00Z', :artifact, :run
                    ) RETURNING id, business_content_hash, ingested_at
                    """
                ),
                {
                    "artifact": source_lineage.raw_artifact_id,
                    "run": source_lineage.ingest_run_id,
                },
            ).mappings().one()
        command.upgrade(config, "head")
        with isolated_engine.connect() as connection:
            after = connection.execute(
                sa.text(
                    """
                    SELECT business_content_hash, ingested_at,
                           (SELECT count(*)
                              FROM institutional_market_summary_version_observations
                             WHERE institutional_market_summary_version_id = :id
                           ) AS observation_count
                      FROM institutional_market_summary_versions WHERE id = :id
                    """
                ),
                {"id": before["id"]},
            ).mappings().one()
            assert after["business_content_hash"] != before["business_content_hash"]
            assert after["ingested_at"] == before["ingested_at"]
            assert after["observation_count"] == 1
        command.downgrade(config, "a4c7e2d91b36")
        with isolated_engine.connect() as connection:
            restored_hash = connection.scalar(
                sa.text(
                    """
                    SELECT business_content_hash
                    FROM institutional_market_summary_versions WHERE id = :id
                    """
                ),
                {"id": before["id"]},
            )
            assert restored_hash == before["business_content_hash"]
        command.upgrade(config, "head")
    finally:
        isolated_engine.dispose()
