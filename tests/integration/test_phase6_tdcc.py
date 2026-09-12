from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError

from conftest import alembic_config
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.tdcc import (
    TDCCBucketObservation,
    TDCCLineageRef,
    TDCCPublication,
    TDCCSnapshotObservation,
    TDCCSnapshotService,
    TDCCSnapshotWriter,
)


pytestmark = pytest.mark.integration

WRITER = TDCCSnapshotWriter()
SERVICE = TDCCSnapshotService()
TAIPEI = ZoneInfo("Asia/Taipei")
SNAPSHOT_DATE = date(2024, 1, 5)
PUBLISHED = datetime(2024, 1, 6, 18, tzinfo=TAIPEI)
PRE_PHASE6_REVISION = "f62a8c9d315e"


def configure(db: Connection, source: str = "tdcc", *, canonical: bool = True) -> int:
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('tdcc_snapshot', 'TDCC shareholding distribution', 'v1')
            ON CONFLICT (dataset_code) DO NOTHING
            """
        )
    )
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_sources (
                dataset_code, source, supports_market_pit, supports_system_pit,
                publication_time_quality, evidence_status,
                accepted_evidence_types, is_canonical
            ) VALUES (
                'tdcc_snapshot', :source, true, true, 100, 'verified',
                ARRAY['official'], :canonical
            )
            """
        ),
        {"source": source, "canonical": canonical},
    )
    db.execute(
        sa.text(
            "INSERT INTO security (security_code) VALUES ('2330') ON CONFLICT DO NOTHING"
        )
    )
    return db.scalar(sa.text("SELECT id FROM security WHERE security_code = '2330'"))


def lineage(db: Connection, marker: str, source: str = "tdcc") -> TDCCLineageRef:
    run_id = db.execute(
        sa.text(
            """
            INSERT INTO ingest_runs (dataset_code, source, status, started_at)
            VALUES ('tdcc_snapshot', :source, 'succeeded', statement_timestamp())
            RETURNING id
            """
        ),
        {"source": source},
    ).scalar_one()
    digest = marker * 64
    artifact_id = db.execute(
        sa.text(
            """
            INSERT INTO raw_artifacts (
                raw_artifact_hash, storage_uri, byte_size, media_type
            ) VALUES (:digest, :uri, 1, 'text/csv') RETURNING id
            """
        ),
        {"digest": digest, "uri": f"data/raw/{marker}/{digest}"},
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
            "uri": f"https://{source}.test/{marker}.csv",
        },
    )
    return TDCCLineageRef(artifact_id, run_id)


def buckets(total_holders: int = 1000) -> tuple[TDCCBucketObservation, ...]:
    return (
        TDCCBucketObservation("1", total_holders - 100, Decimal("300000"), Decimal("3.00")),
        TDCCBucketObservation("15", 100, Decimal("9700000"), Decimal("97.00")),
        TDCCBucketObservation("17", total_holders, Decimal("10000000"), Decimal("100")),
    )


def snapshot(
    snapshot_date: date = SNAPSHOT_DATE, *, total_holders: int = 1000
) -> TDCCSnapshotObservation:
    return TDCCSnapshotObservation(
        snapshot_date=snapshot_date, distribution=buckets(total_holders)
    )


def add_evidence(
    db: Connection,
    version_id: int,
    lineage_ref: TDCCLineageRef,
    *,
    published_at: datetime | None = PUBLISHED,
    kind: str | None = None,
    supersedes: int | None = None,
    source: str = "tdcc",
) -> int:
    return WRITER.append_publication_evidence(
        db,
        source=source,
        version_id=version_id,
        observation=TDCCPublication(
            evidence_kind=kind or ("assertion" if published_at else "unknown"),
            published_at=published_at,
            evidence_source=source,
            evidence_type="official",
            quality_rank=100,
            supersedes_evidence_id=supersedes,
        ),
        lineage=lineage_ref,
    )


def later() -> datetime:
    return datetime.now(UTC) + timedelta(days=1)


def market(at: datetime, knowledge: datetime | None = None) -> MarketPITContext:
    return MarketPITContext(information_as_of=at, knowledge_as_of=knowledge or later())


def db_now(db: Connection) -> datetime:
    return db.scalar(sa.select(sa.func.clock_timestamp()))


def draft(db: Connection, security_id: int, lineage_ref: TDCCLineageRef) -> int:
    version_id = WRITER.begin_snapshot(
        db,
        security_id=security_id,
        source="tdcc",
        snapshot_date=SNAPSHOT_DATE,
        lineage=lineage_ref,
    )
    for item in buckets():
        WRITER.append_bucket(db, version_id=version_id, observation=item)
    return version_id


def assert_sqlstate(db: Connection, sqlstate: str, statement: str, **params: object) -> None:
    with pytest.raises(DBAPIError) as error:
        with db.begin_nested():
            db.execute(sa.text(statement), params)
    assert error.value.orig.sqlstate == sqlstate


def test_draft_snapshot_is_invisible_until_seal(db: Connection) -> None:
    security_id = configure(db)
    lineage_ref = lineage(db, "1")
    version_id = draft(db, security_id, lineage_ref)
    add_evidence(db, version_id, lineage_ref)

    for context in (SystemPITContext(later()), market(later())):
        assert SERVICE.snapshot(
            db, security_code="2330", snapshot_date=SNAPSHOT_DATE, context=context
        ) is None
    assert SERVICE.history(
        db,
        security_code="2330",
        start_date=SNAPSHOT_DATE,
        end_date=SNAPSHOT_DATE,
        context=SystemPITContext(later()),
    ) == ()

    sealed = WRITER.seal(db, version_id=version_id)
    for context in (SystemPITContext(later()), market(later())):
        resolved = SERVICE.snapshot(
            db, security_code="2330", snapshot_date=SNAPSHOT_DATE, context=context
        )
        assert resolved is not None
        assert resolved.snapshot.provenance.version_id == version_id
        assert resolved.snapshot.provenance.aggregate_seal_id == version_id
        assert resolved.snapshot.provenance.business_content_hash == (
            sealed.business_content_hash
        )
        assert resolved.snapshot.data["snapshot_date"] == SNAPSHOT_DATE
        assert [item.bucket_code for item in resolved.distribution] == ["1", "15", "17"]
        assert resolved.distribution[2].holder_count == 1000
        assert resolved.distribution[2].ownership_percent == Decimal("100")


def test_distribution_parent_and_seal_cannot_mutate_after_seal(db: Connection) -> None:
    security_id = configure(db)
    version_id = draft(db, security_id, lineage(db, "2"))
    WRITER.seal(db, version_id=version_id)

    with pytest.raises(DBAPIError) as error:
        with db.begin_nested():
            WRITER.append_bucket(
                db,
                version_id=version_id,
                observation=TDCCBucketObservation("2", 1, Decimal("1"), Decimal("0")),
            )
    assert error.value.orig.sqlstate == "55000"
    assert_sqlstate(
        db,
        "55000",
        "UPDATE tdcc_distribution SET holder_count = 1 WHERE snapshot_version_id = :id",
        id=version_id,
    )
    assert_sqlstate(
        db,
        "55000",
        "DELETE FROM tdcc_distribution WHERE snapshot_version_id = :id",
        id=version_id,
    )
    assert_sqlstate(
        db,
        "55000",
        "UPDATE tdcc_snapshot_versions SET snapshot_date = DATE '2024-01-12' WHERE id = :id",
        id=version_id,
    )
    assert_sqlstate(
        db, "55000", "DELETE FROM tdcc_snapshot_versions WHERE id = :id", id=version_id
    )
    assert_sqlstate(
        db,
        "55000",
        "UPDATE tdcc_snapshot_seals SET ingested_at = now() WHERE snapshot_version_id = :id",
        id=version_id,
    )
    with pytest.raises(DBAPIError) as error:
        with db.begin_nested():
            WRITER.seal(db, version_id=version_id)
    assert error.value.orig.sqlstate == "23505"


def test_backfilled_history_does_not_falsify_system_pit(db: Connection) -> None:
    security_id = configure(db)
    historical_date = date(2019, 3, 8)
    before_backfill = db_now(db)
    written = WRITER.write_snapshot(
        db,
        security_id=security_id,
        source="tdcc",
        observation=snapshot(historical_date),
        lineage=lineage(db, "3"),
    )
    # A historical snapshot date grants neither system nor market visibility.
    assert written.ingested_at > before_backfill
    assert SERVICE.snapshot(
        db,
        security_code="2330",
        snapshot_date=historical_date,
        context=SystemPITContext(before_backfill),
    ) is None
    resolved = SERVICE.snapshot(
        db,
        security_code="2330",
        snapshot_date=historical_date,
        context=SystemPITContext(later()),
    )
    assert resolved is not None
    assert resolved.snapshot.provenance.ingested_at == written.ingested_at
    assert SERVICE.snapshot(
        db,
        security_code="2330",
        snapshot_date=historical_date,
        context=market(later()),
    ) is None

    # A caller cannot backdate the trusted seal time through a raw write either.
    raw_version = draft(db, security_id, lineage(db, "4"))
    db.execute(
        sa.text(
            """
            UPDATE tdcc_distribution SET holder_count = holder_count + 1
            WHERE snapshot_version_id = :id AND bucket_code = '17'
            """
        ),
        {"id": raw_version},
    )
    forged = db.execute(
        sa.text(
            """
            INSERT INTO tdcc_snapshot_seals (
                snapshot_version_id, business_content_hash, ingested_at
            ) VALUES (:id, :hash, TIMESTAMPTZ '2019-03-09 09:00+08')
            RETURNING business_content_hash, ingested_at
            """
        ),
        {"id": raw_version, "hash": "f" * 64},
    ).one()
    assert forged.ingested_at > before_backfill
    assert forged.business_content_hash != "f" * 64


def test_backfill_with_proven_publication_respects_knowledge_cutoff(
    db: Connection,
) -> None:
    security_id = configure(db)
    historical_date = date(2019, 3, 8)
    proven_publication = datetime(2019, 3, 9, 12, tzinfo=TAIPEI)
    lineage_ref = lineage(db, "5")
    written = WRITER.write_snapshot(
        db,
        security_id=security_id,
        source="tdcc",
        observation=snapshot(historical_date),
        lineage=lineage_ref,
    )
    before_evidence = db_now(db)
    evidence_id = add_evidence(
        db, written.version_id, lineage_ref, published_at=proven_publication
    )
    recorded_at = db.scalar(
        sa.text("SELECT recorded_at FROM publication_evidence WHERE id = :id"),
        {"id": evidence_id},
    )
    assert recorded_at > before_evidence

    def resolve(context: MarketPITContext):
        return SERVICE.snapshot(
            db, security_code="2330", snapshot_date=historical_date, context=context
        )

    assert resolve(market(datetime(2019, 3, 10, tzinfo=TAIPEI), before_evidence)) is None
    assert resolve(market(datetime(2019, 3, 9, 11, tzinfo=TAIPEI))) is None
    resolved = resolve(market(datetime(2019, 3, 10, tzinfo=TAIPEI)))
    assert resolved is not None
    assert resolved.snapshot.authoritative_evidence is not None
    assert resolved.snapshot.authoritative_evidence.published_at == proven_publication


def test_publication_cannot_precede_the_snapshot_date(db: Connection) -> None:
    security_id = configure(db)
    lineage_ref = lineage(db, "6")
    written = WRITER.write_snapshot(
        db,
        security_id=security_id,
        source="tdcc",
        observation=snapshot(),
        lineage=lineage_ref,
    )
    with pytest.raises(DBAPIError) as error:
        with db.begin_nested():
            add_evidence(
                db,
                written.version_id,
                lineage_ref,
                published_at=datetime(2024, 1, 4, 23, 59, 59, tzinfo=TAIPEI),
            )
    assert error.value.orig.sqlstate == "23514"
    # The lower bound is the start of the snapshot date in Asia/Taipei.
    assert add_evidence(
        db,
        written.version_id,
        lineage_ref,
        published_at=datetime(2024, 1, 5, 0, 0, tzinfo=TAIPEI),
    )
    assert add_evidence(db, written.version_id, lineage_ref, published_at=None)


def test_repeated_identical_snapshot_reuses_version_and_keeps_lineage(
    db: Connection,
) -> None:
    security_id = configure(db)
    first_lineage = lineage(db, "7")
    first = WRITER.write_snapshot(
        db,
        security_id=security_id,
        source="tdcc",
        observation=snapshot(),
        lineage=first_lineage,
    )
    evidence_id = add_evidence(db, first.version_id, first_lineage)

    second_lineage = lineage(db, "8")
    reordered = TDCCSnapshotObservation(
        snapshot_date=SNAPSHOT_DATE, distribution=tuple(reversed(buckets()))
    )
    repeated = WRITER.write_snapshot(
        db,
        security_id=security_id,
        source="tdcc",
        observation=reordered,
        lineage=second_lineage,
    )
    repeated_evidence = add_evidence(db, repeated.version_id, second_lineage)

    assert first.created is True
    assert repeated.created is False
    assert repeated.version_id == first.version_id
    assert repeated.business_content_hash == first.business_content_hash
    assert repeated.ingested_at == first.ingested_at
    assert repeated_evidence == evidence_id
    assert db.scalar(sa.text("SELECT count(*) FROM tdcc_snapshot_versions")) == 1
    assert db.scalar(sa.text("SELECT count(*) FROM tdcc_snapshot_seals")) == 1
    observations = SERVICE.observations(db, version_id=first.version_id)
    assert {item.ingest_run_id for item in observations} == {
        first_lineage.ingest_run_id,
        second_lineage.ingest_run_id,
    }


def test_identical_revision_sealed_concurrently_is_reused_not_duplicated(
    db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    security_id = configure(db)
    first = WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(), lineage=lineage(db, "1"),
    )
    lookup = TDCCSnapshotWriter._sealed_revision
    calls = []

    def missed_first_lookup(*args):
        # Simulate a concurrent writer committing between lookup and seal.
        calls.append(args)
        return None if len(calls) == 1 else lookup(*args)

    monkeypatch.setattr(
        TDCCSnapshotWriter, "_sealed_revision", staticmethod(missed_first_lookup)
    )
    second_lineage = lineage(db, "2")
    repeated = WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(), lineage=second_lineage,
    )
    assert len(calls) == 2
    assert repeated.created is False
    assert repeated.version_id == first.version_id
    assert db.scalar(sa.text("SELECT count(*) FROM tdcc_snapshot_versions")) == 1
    assert second_lineage.ingest_run_id in {
        item.ingest_run_id for item in SERVICE.observations(db, version_id=first.version_id)
    }


def test_changed_distribution_is_an_independent_revision(db: Connection) -> None:
    security_id = configure(db)
    first_lineage = lineage(db, "9")
    first = WRITER.write_snapshot(
        db,
        security_id=security_id,
        source="tdcc",
        observation=snapshot(),
        lineage=first_lineage,
    )
    add_evidence(db, first.version_id, first_lineage)
    between = db_now(db)
    second_lineage = lineage(db, "a")
    second = WRITER.write_snapshot(
        db,
        security_id=security_id,
        source="tdcc",
        observation=snapshot(total_holders=1001),
        lineage=second_lineage,
    )
    corrected_at = datetime(2024, 1, 8, 9, tzinfo=TAIPEI)
    add_evidence(db, second.version_id, second_lineage, published_at=corrected_at)

    assert second.created is True
    assert second.version_id != first.version_id
    assert second.business_content_hash != first.business_content_hash

    def total_holders(context) -> int:
        resolved = SERVICE.snapshot(
            db, security_code="2330", snapshot_date=SNAPSHOT_DATE, context=context
        )
        assert resolved is not None
        return resolved.distribution[-1].holder_count

    assert total_holders(SystemPITContext(between)) == 1000
    assert total_holders(SystemPITContext(later())) == 1001
    assert total_holders(market(datetime(2024, 1, 7, tzinfo=TAIPEI))) == 1000
    assert total_holders(market(datetime(2024, 1, 9, tzinfo=TAIPEI))) == 1001
    assert total_holders(market(datetime(2024, 1, 9, tzinfo=TAIPEI), between)) == 1000


def test_evidence_retraction_hides_snapshot_without_new_revision(db: Connection) -> None:
    security_id = configure(db)
    lineage_ref = lineage(db, "b")
    written = WRITER.write_snapshot(
        db,
        security_id=security_id,
        source="tdcc",
        observation=snapshot(),
        lineage=lineage_ref,
    )
    assertion = add_evidence(db, written.version_id, lineage_ref)
    before_retraction = db_now(db)
    add_evidence(
        db,
        written.version_id,
        lineage_ref,
        published_at=None,
        kind="retraction",
        supersedes=assertion,
    )
    at = datetime(2024, 1, 10, tzinfo=TAIPEI)

    assert SERVICE.snapshot(
        db,
        security_code="2330",
        snapshot_date=SNAPSHOT_DATE,
        context=market(at, before_retraction),
    ) is not None
    assert SERVICE.snapshot(
        db, security_code="2330", snapshot_date=SNAPSHOT_DATE, context=market(at)
    ) is None
    assert db.scalar(sa.text("SELECT count(*) FROM tdcc_snapshot_versions")) == 1


def test_history_returns_only_pit_visible_snapshots(db: Connection) -> None:
    security_id = configure(db)
    lineage_ref = lineage(db, "c")
    dates = (date(2024, 1, 5), date(2024, 1, 12), date(2024, 1, 19), date(2024, 1, 26))
    published = WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(dates[0]), lineage=lineage_ref,
    )
    add_evidence(db, published.version_id, lineage_ref)
    WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(dates[1]), lineage=lineage_ref,
    )
    unsealed = WRITER.begin_snapshot(
        db, security_id=security_id, source="tdcc",
        snapshot_date=dates[2], lineage=lineage_ref,
    )
    WRITER.append_bucket(db, version_id=unsealed, observation=buckets()[0])
    WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(dates[3]), lineage=lineage_ref,
    )

    def history_dates(context, start: date = dates[0], end: date = dates[2]):
        return [
            item.snapshot.data["snapshot_date"]
            for item in SERVICE.history(
                db, security_code="2330", start_date=start, end_date=end,
                context=context,
            )
        ]

    assert history_dates(market(datetime(2024, 2, 1, tzinfo=TAIPEI))) == [dates[0]]
    assert history_dates(SystemPITContext(later())) == [dates[0], dates[1]]
    assert history_dates(SystemPITContext(later()), end=dates[3]) == [
        dates[0], dates[1], dates[3]
    ]
    with pytest.raises(ValueError):
        SERVICE.history(
            db, security_code="2330", start_date=dates[1], end_date=dates[0],
            context=SystemPITContext(later()),
        )


def test_sources_and_observation_lineage_are_isolated(db: Connection) -> None:
    security_id = configure(db)
    configure(db, "vendor", canonical=False)
    vendor_lineage = lineage(db, "d", source="vendor")
    WRITER.write_snapshot(
        db, security_id=security_id, source="vendor",
        observation=snapshot(), lineage=vendor_lineage,
    )
    assert SERVICE.snapshot(
        db, security_code="2330", snapshot_date=SNAPSHOT_DATE,
        context=SystemPITContext(later()),
    ) is None
    assert SERVICE.snapshot(
        db, security_code="2330", snapshot_date=SNAPSHOT_DATE,
        context=SystemPITContext(later()), source="vendor",
    ) is not None

    tdcc_version = WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(), lineage=lineage(db, "e"),
    ).version_id
    assert_sqlstate(
        db,
        "23514",
        """
        INSERT INTO tdcc_snapshot_version_observations (
            snapshot_version_id, raw_artifact_id, ingest_run_id
        ) VALUES (:version, :artifact, :run)
        """,
        version=tdcc_version,
        artifact=vendor_lineage.raw_artifact_id,
        run=vendor_lineage.ingest_run_id,
    )
    assert_sqlstate(
        db,
        "55000",
        "DELETE FROM tdcc_snapshot_version_observations WHERE snapshot_version_id = :id",
        id=tdcc_version,
    )


def test_database_hash_function_matches_seal_and_orders_buckets_bytewise(
    db: Connection,
) -> None:
    security_id = configure(db)
    version_id = WRITER.begin_snapshot(
        db, security_id=security_id, source="tdcc",
        snapshot_date=SNAPSHOT_DATE, lineage=lineage(db, "f"),
    )
    for code in ("b", "B", "a", "_"):
        WRITER.append_bucket(
            db,
            version_id=version_id,
            observation=TDCCBucketObservation(code, 1, Decimal("1"), Decimal("1")),
        )
    draft_hash = db.scalar(
        sa.text("SELECT stockdc_tdcc_snapshot_business_hash(:id)"), {"id": version_id}
    )
    expected_payload = db.scalar(
        sa.text(
            """
            SELECT jsonb_build_object(
                'snapshot_date', DATE '2024-01-05',
                'distribution', jsonb_agg(jsonb_build_object(
                    'bucket_code', code,
                    'holder_count', 1,
                    'shares', 1::numeric(30, 0),
                    'ownership_percent', 1::numeric(12, 8)
                ) ORDER BY ordinal)
            )::text
            FROM unnest(ARRAY['B', '_', 'a', 'b']) WITH ORDINALITY AS item(code, ordinal)
            """
        )
    )
    assert draft_hash == db.scalar(
        sa.text("SELECT encode(digest(convert_to(:payload, 'UTF8'), 'sha256'), 'hex')"),
        {"payload": expected_payload},
    )
    assert WRITER.seal(db, version_id=version_id).business_content_hash == draft_hash


def test_lineage_migration_backfills_and_preserves_existing_seal_hash(
    isolated_database_url: str,
) -> None:
    config = alembic_config(isolated_database_url)
    command.downgrade(config, PRE_PHASE6_REVISION)
    migration_engine = sa.create_engine(isolated_database_url)
    try:
        with migration_engine.begin() as connection:
            security_id = configure(connection)
            source_lineage = lineage(connection, "0")
            version_id = connection.execute(
                sa.text(
                    """
                    INSERT INTO tdcc_snapshot_versions (
                        security_id, source, snapshot_date,
                        raw_artifact_id, ingest_run_id
                    ) VALUES (
                        :security, 'tdcc', DATE '2024-01-05', :artifact, :run
                    ) RETURNING id
                    """
                ),
                {
                    "security": security_id,
                    "artifact": source_lineage.raw_artifact_id,
                    "run": source_lineage.ingest_run_id,
                },
            ).scalar_one()
            for code, holders in (("1", 900), ("15", 100), ("17", 1000)):
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO tdcc_distribution (
                            snapshot_version_id, bucket_code, holder_count,
                            shares, ownership_percent
                        ) VALUES (:id, :code, :holders, 10, 5.5)
                        """
                    ),
                    {"id": version_id, "code": code, "holders": holders},
                )
            legacy_hash = connection.execute(
                sa.text(
                    """
                    INSERT INTO tdcc_snapshot_seals (
                        snapshot_version_id, business_content_hash, ingested_at
                    ) VALUES (:id, :hash, now()) RETURNING business_content_hash
                    """
                ),
                {"id": version_id, "hash": "0" * 64},
            ).scalar_one()
        migration_engine.dispose()

        command.upgrade(config, "head")
        migration_engine = sa.create_engine(isolated_database_url)
        with migration_engine.connect() as connection:
            linked = connection.execute(
                sa.text(
                    """
                    SELECT raw_artifact_id, ingest_run_id
                    FROM tdcc_snapshot_version_observations
                    WHERE snapshot_version_id = :version
                    """
                ),
                {"version": version_id},
            ).one()
            assert linked == (
                source_lineage.raw_artifact_id,
                source_lineage.ingest_run_id,
            )
            assert connection.scalar(
                sa.text("SELECT stockdc_tdcc_snapshot_business_hash(:id)"),
                {"id": version_id},
            ) == legacy_hash
    finally:
        migration_engine.dispose()
