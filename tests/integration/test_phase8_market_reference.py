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
from stock_data_center.market_reference import (
    AmountScale,
    CorporateActionObservation,
    MarketIndexObservation,
    MarketIndexMetadataObservation,
    MarketReferenceService,
    MarketReferenceWriter,
    OfficialValuationObservation,
    Phase8LineageRef,
    Phase8Publication,
    SourceTwdAmount,
    TwdAmount,
)
from stock_data_center.pit import MarketPITContext, SystemPITContext


pytestmark = pytest.mark.integration
WRITER = MarketReferenceWriter()
SERVICE = MarketReferenceService()
TRADE_DATE = date(2026, 9, 10)
_DIGESTS = count(8000)


def configure(db: Connection, dataset: str, source: str, *, canonical: bool = True) -> None:
    db.execute(sa.text("""
        INSERT INTO dataset_catalog (dataset_code, description, schema_version)
        VALUES (:dataset, :description, 'v1') ON CONFLICT (dataset_code) DO NOTHING
    """), {"dataset": dataset, "description": dataset})
    db.execute(sa.text("""
        INSERT INTO dataset_sources (
            dataset_code, source, supports_market_pit, supports_system_pit,
            publication_time_quality, evidence_status, accepted_evidence_types,
            is_canonical
        ) VALUES (:dataset, :source, true, true, 100, 'verified',
                  ARRAY['official'], :canonical)
    """), {"dataset": dataset, "source": source, "canonical": canonical})


def add_security(db: Connection, code: str = "2330-phase8") -> int:
    return db.scalar(sa.text(
        "INSERT INTO security (security_code) VALUES (:code) RETURNING id"
    ), {"code": code})


def lineage(db: Connection, dataset: str, source: str) -> Phase8LineageRef:
    run_id = db.scalar(sa.text("""
        INSERT INTO ingest_runs (dataset_code, source, status, started_at)
        VALUES (:dataset, :source, 'succeeded', statement_timestamp()) RETURNING id
    """), {"dataset": dataset, "source": source})
    digest = f"{next(_DIGESTS):064x}"
    artifact_id = db.scalar(sa.text("""
        INSERT INTO raw_artifacts (raw_artifact_hash, storage_uri, byte_size, media_type)
        VALUES (:digest, :uri, 1, 'application/json') RETURNING id
    """), {"digest": digest, "uri": f"data/raw/{digest[:2]}/{digest}"})
    db.execute(sa.text("""
        INSERT INTO raw_artifact_observations
            (raw_artifact_id, ingest_run_id, source_uri, fetched_at)
        VALUES (:artifact, :run, :uri, statement_timestamp())
    """), {"artifact": artifact_id, "run": run_id, "uri": f"https://{source}.test"})
    return Phase8LineageRef(artifact_id, run_id)


def publication(at: datetime | None) -> Phase8Publication:
    return Phase8Publication(
        evidence_kind="assertion" if at else "unknown",
        published_at=at,
        evidence_source="official endpoint",
        evidence_type="official",
        quality_rank=100,
    )


def market(at: datetime) -> MarketPITContext:
    return MarketPITContext(at, datetime.now(UTC) + timedelta(days=1))


def test_index_history_is_pit_safe_and_source_isolated(db: Connection) -> None:
    for source, close, canonical in (("twse", "25000", True), ("vendor", "24999", False)):
        configure(db, "market_index", source, canonical=canonical)
        index_id = WRITER.register_index(db, index_code="TAIEX-P8")
        link = lineage(db, "market_index", source)
        written = WRITER.append_index(
            db, market_index_id=index_id, source=source,
            observation=MarketIndexObservation(
                TRADE_DATE, Decimal(close), trade_value=TwdAmount(Decimal("500000"))
            ), lineage=link,
        )
        WRITER.append_publication_evidence(
            db, dataset_code="market_index", source=source,
            version_id=written.version_id,
            publication=publication(datetime(2026, 9, 10, 8, tzinfo=UTC)), lineage=link,
        )
    before = SERVICE.index(
        db, index_code="TAIEX-P8", trade_date=TRADE_DATE,
        context=market(datetime(2026, 9, 10, 7, 59, tzinfo=UTC)), source="twse",
    )
    assert before is None
    twse = SERVICE.history(
        db, dataset_code="market_index", index_code="TAIEX-P8",
        start_date=TRADE_DATE, end_date=TRADE_DATE,
        context=market(datetime(2026, 9, 10, 8, 1, tzinfo=UTC)), source="twse",
    )
    vendor = SERVICE.index(
        db, index_code="TAIEX-P8", trade_date=TRADE_DATE,
        context=market(datetime(2026, 9, 10, 8, 1, tzinfo=UTC)), source="vendor",
    )
    assert [row.data["close_value"] for row in twse] == [Decimal("25000")]
    assert vendor is not None and vendor.data["close_value"] == Decimal("24999")


def test_corporate_action_is_visible_on_announcement_not_ex_date(db: Connection) -> None:
    configure(db, "corporate_action", "mops")
    security_id = add_security(db)
    event_id = WRITER.register_corporate_action_event(
        db, security_id=security_id, source="mops", source_event_key="MOPS-1234"
    )
    link = lineage(db, "corporate_action", "mops")
    written = WRITER.append_corporate_action(
        db, event_id=event_id, source="mops",
        observation=CorporateActionObservation(
            action_type="cash_dividend", announcement_date=date(2026, 8, 1),
            ex_date=TRADE_DATE, cash_dividend_per_share=TwdAmount(Decimal("3.5")),
        ), lineage=link,
    )
    announced_at = datetime(2026, 8, 1, 6, tzinfo=UTC)
    WRITER.append_publication_evidence(
        db, dataset_code="corporate_action", source="mops",
        version_id=written.version_id, publication=publication(announced_at), lineage=link,
    )
    result = SERVICE.corporate_action(
        db, event_id=event_id,
        context=market(announced_at + timedelta(seconds=1)), source="mops",
    )
    assert result is not None
    assert result.data["announcement_date"] == date(2026, 8, 1)
    assert result.data["ex_date"] == TRADE_DATE


def test_corporate_action_corrections_are_revisions_of_one_stable_event(
    db: Connection,
) -> None:
    configure(db, "corporate_action", "mops")
    security_id = add_security(db)
    event_id = WRITER.register_corporate_action_event(
        db, security_id=security_id, source="mops", source_event_key="DOC-2026-77"
    )
    initial_lineage = lineage(db, "corporate_action", "mops")
    initial = WRITER.append_corporate_action(
        db, event_id=event_id, source="mops",
        observation=CorporateActionObservation(
            action_type="cash_dividend", announcement_date=date(2026, 8, 1),
            ex_date=date(2026, 9, 10), payment_date=date(2026, 10, 1),
            cash_dividend_per_share=TwdAmount(Decimal("3.5")),
        ), lineage=initial_lineage,
    )
    first_evidence = WRITER.append_publication_evidence(
        db, dataset_code="corporate_action", source="mops",
        version_id=initial.version_id,
        publication=publication(datetime(2026, 8, 1, 6, tzinfo=UTC)),
        lineage=initial_lineage,
    )
    first_recorded = db.scalar(sa.text(
        "SELECT recorded_at FROM publication_evidence WHERE id=:id"
    ), {"id": first_evidence})

    corrected_lineage = lineage(db, "corporate_action", "mops")
    corrected_date = WRITER.append_corporate_action(
        db, event_id=event_id, source="mops",
        observation=CorporateActionObservation(
            action_type="cash_dividend", announcement_date=date(2026, 8, 1),
            ex_date=date(2026, 9, 15), payment_date=date(2026, 10, 1),
            cash_dividend_per_share=TwdAmount(Decimal("3.5")),
        ), lineage=corrected_lineage,
    )
    second_evidence = WRITER.append_publication_evidence(
        db, dataset_code="corporate_action", source="mops",
        version_id=corrected_date.version_id,
        publication=publication(datetime(2026, 8, 10, 6, tzinfo=UTC)),
        lineage=corrected_lineage,
    )
    second_recorded = db.scalar(sa.text(
        "SELECT recorded_at FROM publication_evidence WHERE id=:id"
    ), {"id": second_evidence})

    amount_lineage = lineage(db, "corporate_action", "mops")
    corrected_terms = WRITER.append_corporate_action(
        db, event_id=event_id, source="mops",
        observation=CorporateActionObservation(
            action_type="cash_dividend", announcement_date=date(2026, 8, 1),
            ex_date=date(2026, 9, 15), payment_date=date(2026, 10, 5),
            cash_dividend_per_share=TwdAmount(Decimal("4")),
        ), lineage=amount_lineage,
    )
    WRITER.append_publication_evidence(
        db, dataset_code="corporate_action", source="mops",
        version_id=corrected_terms.version_id,
        publication=publication(datetime(2026, 8, 20, 6, tzinfo=UTC)),
        lineage=amount_lineage,
    )
    assert len({initial.version_id, corrected_date.version_id,
                corrected_terms.version_id}) == 3
    assert initial.business_content_hash != corrected_date.business_content_hash
    assert corrected_date.business_content_hash != corrected_terms.business_content_hash
    assert db.scalar(sa.text(
        "SELECT count(*) FROM corporate_action_versions WHERE event_id=:event"
    ), {"event": event_id}) == 3

    before_knowledge = SERVICE.corporate_action(
        db, event_id=event_id, source="mops",
        context=MarketPITContext(
            datetime(2026, 8, 11, tzinfo=UTC), first_recorded
        ),
    )
    after_date_correction = SERVICE.corporate_action(
        db, event_id=event_id, source="mops",
        context=MarketPITContext(
            datetime(2026, 8, 21, tzinfo=UTC), second_recorded
        ),
    )
    current = SERVICE.corporate_action(
        db, event_id=event_id, source="mops",
        context=market(datetime(2026, 8, 21, tzinfo=UTC)),
    )
    assert before_knowledge is not None
    assert before_knowledge.data["ex_date"] == date(2026, 9, 10)
    assert before_knowledge.data["cash_dividend_per_share"] == Decimal("3.5")
    assert after_date_correction is not None
    assert after_date_correction.data["ex_date"] == date(2026, 9, 15)
    assert after_date_correction.data["payment_date"] == date(2026, 10, 1)
    assert after_date_correction.data["cash_dividend_per_share"] == Decimal("3.5")
    assert current is not None
    assert current.data["payment_date"] == date(2026, 10, 5)
    assert current.data["cash_dividend_per_share"] == Decimal("4")


def test_distinct_events_can_share_action_type_and_ex_date(db: Connection) -> None:
    configure(db, "corporate_action", "mops")
    security_id = add_security(db)
    event_ids = [
        WRITER.register_corporate_action_event(
            db, security_id=security_id, source="mops", source_event_key=key
        )
        for key in ("DOC-A", "DOC-B")
    ]
    for event_id, amount in zip(event_ids, ("2", "3"), strict=True):
        WRITER.append_corporate_action(
            db, event_id=event_id, source="mops",
            observation=CorporateActionObservation(
                action_type="cash_dividend", ex_date=TRADE_DATE,
                announcement_date=date(2026, 8, 1),
                cash_dividend_per_share=TwdAmount(Decimal(amount)),
            ), lineage=lineage(db, "corporate_action", "mops"),
        )
    assert event_ids[0] != event_ids[1]
    assert WRITER.register_corporate_action_event(
        db, security_id=security_id, source="mops", source_event_key="DOC-A"
    ) == event_ids[0]
    assert db.scalar(sa.text(
        "SELECT count(*) FROM corporate_action_versions WHERE event_id=ANY(:events)"
    ), {"events": event_ids}) == 2
    with pytest.raises(DBAPIError) as immutable:
        with db.begin_nested():
            db.execute(sa.text(
                "UPDATE corporate_action_events SET source_event_key='changed' WHERE id=:id"
            ), {"id": event_ids[0]})
    assert immutable.value.orig.sqlstate == "55000"


def test_identical_corporate_action_refetch_keeps_one_revision_and_two_lineages(
    db: Connection,
) -> None:
    configure(db, "corporate_action", "mops")
    security_id = add_security(db)
    event_id = WRITER.register_corporate_action_event(
        db, security_id=security_id, source="mops", source_event_key="DOC-REPEAT"
    )
    observation = CorporateActionObservation(
        action_type="rights", ex_date=TRADE_DATE,
        announcement_date=date(2026, 8, 1), rights_ratio=Decimal("0.1"),
    )
    writes = [
        WRITER.append_corporate_action(
            db, event_id=event_id, source="mops", observation=observation,
            lineage=lineage(db, "corporate_action", "mops"),
        )
        for _ in range(2)
    ]
    assert writes[0].created is True and writes[1].created is False
    assert writes[0].version_id == writes[1].version_id
    assert len(SERVICE.observations(
        db, dataset_code="corporate_action", version_id=writes[0].version_id
    )) == 2


def test_index_name_is_effective_dated_metadata_not_identity(db: Connection) -> None:
    configure(db, "market_index_metadata", "twse")
    index_id = WRITER.register_index(db, index_code="IX0038-P8")
    assert WRITER.register_index(db, index_code="IX0038-P8") == index_id
    cases = (
        (date(2020, 1, 1), "觀光事業類指數"),
        (date(2023, 7, 3), "觀光餐旅類指數"),
    )
    for effective_from, name in cases:
        link = lineage(db, "market_index_metadata", "twse")
        written = WRITER.append_index_metadata(
            db, market_index_id=index_id, source="twse",
            observation=MarketIndexMetadataObservation(
                effective_from=effective_from, market="TWSE", name=name
            ), lineage=link,
        )
        WRITER.append_publication_evidence(
            db, dataset_code="market_index_metadata", source="twse",
            version_id=written.version_id,
            publication=publication(datetime.combine(
                effective_from, datetime.min.time(), tzinfo=UTC
            )), lineage=link,
        )
    old = SERVICE.index_metadata(
        db, index_code="IX0038-P8", effective_on=date(2023, 7, 2),
        context=market(datetime(2023, 7, 3, tzinfo=UTC)), source="twse",
    )
    renamed = SERVICE.index_metadata(
        db, index_code="IX0038-P8", effective_on=date(2023, 7, 3),
        context=market(datetime(2023, 7, 4, tzinfo=UTC)), source="twse",
    )
    assert old is not None and old.data["name"] == "觀光事業類指數"
    assert renamed is not None and renamed.data["name"] == "觀光餐旅類指數"


def test_unknown_publication_is_system_visible_but_market_invisible(db: Connection) -> None:
    configure(db, "official_valuation", "twse")
    security_id = add_security(db)
    link = lineage(db, "official_valuation", "twse")
    written = WRITER.append_official_valuation(
        db, security_id=security_id, source="twse",
        observation=OfficialValuationObservation(TRADE_DATE, pe_ratio=Decimal("18.2")),
        lineage=link,
    )
    WRITER.append_publication_evidence(
        db, dataset_code="official_valuation", source="twse",
        version_id=written.version_id, publication=publication(None), lineage=link,
    )
    assert SERVICE.official_valuation(
        db, security_code="2330-phase8", trade_date=TRADE_DATE,
        context=market(datetime(2026, 9, 11, tzinfo=UTC)), source="twse",
    ) is None
    system = SERVICE.official_valuation(
        db, security_code="2330-phase8", trade_date=TRADE_DATE,
        context=SystemPITContext(datetime.now(UTC) + timedelta(days=1)), source="twse",
    )
    assert system is not None and system.data["pe_ratio"] == Decimal("18.2")


def test_historical_backfill_uses_actual_ingestion_time(db: Connection) -> None:
    configure(db, "official_valuation", "twse")
    security_id = add_security(db)
    link = lineage(db, "official_valuation", "twse")
    written = WRITER.append_official_valuation(
        db, security_id=security_id, source="twse",
        observation=OfficialValuationObservation(
            date(2019, 1, 2), pe_ratio=Decimal("12.5")
        ), lineage=link,
    )
    before = SERVICE.official_valuation(
        db, security_code="2330-phase8", trade_date=date(2019, 1, 2),
        context=SystemPITContext(written.ingested_at - timedelta(microseconds=1)),
        source="twse",
    )
    after = SERVICE.official_valuation(
        db, security_code="2330-phase8", trade_date=date(2019, 1, 2),
        context=SystemPITContext(written.ingested_at), source="twse",
    )
    assert before is None
    assert after is not None and after.provenance.ingested_at == written.ingested_at


def test_equivalent_amounts_deduplicate_while_preserving_fetch_lineage(db: Connection) -> None:
    configure(db, "market_index", "twse")
    index_id = WRITER.register_index(db, index_code="TAIEX-DEDUP")
    observations = (
        SourceTwdAmount(Decimal("500"), AmountScale.THOUSAND).to_canonical(),
        SourceTwdAmount(Decimal("500000"), AmountScale.MAJOR).to_canonical(),
    )
    written = []
    for amount in observations:
        written.append(WRITER.append_index(
            db, market_index_id=index_id, source="twse",
            observation=MarketIndexObservation(TRADE_DATE, Decimal("25000"), trade_value=amount),
            lineage=lineage(db, "market_index", "twse"),
        ))
    assert written[0].created is True and written[1].created is False
    assert written[0].version_id == written[1].version_id
    assert len(SERVICE.observations(
        db, dataset_code="market_index", version_id=written[0].version_id
    )) == 2


def test_database_rejects_impossible_values_and_early_publication(db: Connection) -> None:
    configure(db, "official_valuation", "twse")
    security_id = add_security(db)
    link = lineage(db, "official_valuation", "twse")
    with pytest.raises(DBAPIError) as invalid:
        with db.begin_nested():
            db.execute(sa.text("""
                INSERT INTO official_valuation_versions (
                    security_id, source, trade_date, pe_ratio,
                    business_content_hash, ingested_at, raw_artifact_id, ingest_run_id
                ) VALUES (:security, 'twse', :day, -1, repeat('0', 64),
                          statement_timestamp(), :artifact, :run)
            """), {"security": security_id, "day": TRADE_DATE,
                    "artifact": link.raw_artifact_id, "run": link.ingest_run_id})
    assert invalid.value.orig.sqlstate == "23514"
    written = WRITER.append_official_valuation(
        db, security_id=security_id, source="twse",
        observation=OfficialValuationObservation(TRADE_DATE, pb_ratio=Decimal("5")),
        lineage=link,
    )
    with pytest.raises(DBAPIError) as early:
        with db.begin_nested():
            WRITER.append_publication_evidence(
                db, dataset_code="official_valuation", source="twse",
                version_id=written.version_id,
                publication=publication(datetime(2026, 9, 9, tzinfo=UTC)), lineage=link,
            )
    assert early.value.orig.sqlstate == "23514"


def test_populated_phase8_migration_backfills_lineage_and_round_trips_hash(
    isolated_database_url: str,
) -> None:
    config = alembic_config(isolated_database_url)
    command.downgrade(config, "3f7c9a2d6e10")
    isolated_engine = sa.create_engine(isolated_database_url)
    try:
        with isolated_engine.begin() as connection:
            configure(connection, "corporate_action", "mops")
            security_id = add_security(connection, "2330-p8-migration")
            link = lineage(connection, "corporate_action", "mops")
            before = connection.execute(sa.text("""
                INSERT INTO corporate_action_versions (
                    security_id, source, action_type, announcement_date, ex_date,
                    cash_dividend_per_share, business_content_hash, ingested_at,
                    raw_artifact_id, ingest_run_id
                ) VALUES (:security, 'mops', 'cash_dividend', DATE '2026-08-01',
                          DATE '2026-09-10', 3.5, repeat('0', 64),
                          TIMESTAMPTZ '2000-01-01Z', :artifact, :run)
                RETURNING id, business_content_hash, ingested_at
            """), {"security": security_id, "artifact": link.raw_artifact_id,
                    "run": link.ingest_run_id}).mappings().one()
            configure(connection, "market_index", "twse")
            index_lineage = lineage(connection, "market_index", "twse")
            legacy_index_id = connection.scalar(sa.text("""
                INSERT INTO market_index(index_code,market,name)
                VALUES('IX0038-MIGRATION','TWSE','觀光事業類指數') RETURNING id
            """))
            connection.execute(sa.text("""
                INSERT INTO market_index_versions(
                    market_index_id,source,trade_date,close_value,
                    business_content_hash,ingested_at,raw_artifact_id,ingest_run_id
                ) VALUES(:index,'twse',DATE '2020-01-02',100,repeat('0',64),
                         statement_timestamp(),:artifact,:run)
            """), {"index": legacy_index_id,
                    "artifact": index_lineage.raw_artifact_id,
                    "run": index_lineage.ingest_run_id})
        command.upgrade(config, "head")
        with isolated_engine.connect() as connection:
            after = connection.execute(sa.text("""
                SELECT business_content_hash, ingested_at,
                       (SELECT count(*) FROM corporate_action_version_observations
                         WHERE corporate_action_version_id=:id) AS observations
                  FROM corporate_action_versions WHERE id=:id
            """), {"id": before["id"]}).mappings().one()
            # The predecessor already hashed action_type/ex_date as content;
            # the stable event migration must preserve that correct identity.
            assert after["business_content_hash"] == before["business_content_hash"]
            assert after["ingested_at"] == before["ingested_at"]
            assert after["observations"] == 1
            legacy_event = connection.execute(sa.text("""
                SELECT e.source_event_key,e.security_id
                  FROM corporate_action_events e
                  JOIN corporate_action_versions v ON v.event_id=e.id
                 WHERE v.id=:id
            """), {"id": before["id"]}).mappings().one()
            assert legacy_event == {
                "source_event_key": f"legacy-version:{before['id']}",
                "security_id": security_id,
            }
            migrated_name = connection.execute(sa.text("""
                SELECT market,name,effective_from
                  FROM market_index_metadata_versions
                 WHERE market_index_id=:index
            """), {"index": legacy_index_id}).mappings().one()
            assert migrated_name == {
                "market": "TWSE", "name": "觀光事業類指數",
                "effective_from": date(2020, 1, 2),
            }
        command.downgrade(config, "3f7c9a2d6e10")
        with isolated_engine.connect() as connection:
            assert connection.scalar(sa.text(
                "SELECT business_content_hash FROM corporate_action_versions WHERE id=:id"
            ), {"id": before["id"]}) == before["business_content_hash"]
            restored = connection.execute(sa.text(
                "SELECT market,name FROM market_index WHERE id=:id"
            ), {"id": legacy_index_id}).mappings().one()
            assert restored == {"market": "TWSE", "name": "觀光事業類指數"}
        command.upgrade(config, "head")
    finally:
        isolated_engine.dispose()
