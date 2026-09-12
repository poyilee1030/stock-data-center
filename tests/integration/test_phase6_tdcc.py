from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from alembic import command
from threading import Event

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import DBAPIError

from conftest import alembic_config
from stock_data_center.pit import MarketPITContext, SystemPITContext
from stock_data_center.tdcc import (
    TDCC_OPENDATA_V1,
    TDCCBucketObservation,
    TDCCDistributionError,
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


def buckets(
    total_holders: int = 1000,
    *,
    adjustment_shares: str = "-2000",
    adjustment_holders: int | None = None,
) -> tuple[TDCCBucketObservation, ...]:
    """A complete tdcc-opendata-v1 distribution: levels 1-15, 16, and 17."""
    holding = [
        TDCCBucketObservation(
            str(level),
            total_holders - 140 if level == 1 else 10,
            Decimal(level * 100000),
            Decimal("5.5"),
        )
        for level in range(1, 16)
    ]
    adjustment = TDCCBucketObservation(
        "16", adjustment_holders, Decimal(adjustment_shares), Decimal("0")
    )
    total = TDCCBucketObservation(
        "17", total_holders, Decimal("12000000"), Decimal("100")
    )
    return (*holding, adjustment, total)


def snapshot(
    snapshot_date: date = SNAPSHOT_DATE, *, total_holders: int = 1000, **options: object
) -> TDCCSnapshotObservation:
    return TDCCSnapshotObservation(
        snapshot_date=snapshot_date,
        distribution_schema=TDCC_OPENDATA_V1,
        distribution=buckets(total_holders, **options),  # type: ignore[arg-type]
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


def draft(
    db: Connection,
    security_id: int,
    lineage_ref: TDCCLineageRef,
    items: tuple[TDCCBucketObservation, ...] | None = None,
) -> int:
    version_id = WRITER.begin_snapshot(
        db,
        security_id=security_id,
        source="tdcc",
        snapshot_date=SNAPSHOT_DATE,
        distribution_schema=TDCC_OPENDATA_V1,
        lineage=lineage_ref,
    )
    for item in buckets() if items is None else items:
        WRITER.append_bucket(db, version_id=version_id, observation=item)
    return version_id


def add_profile(db: Connection, profile: str, roles: dict[str, str]) -> None:
    db.execute(
        sa.text(
            "INSERT INTO tdcc_distribution_schemas (distribution_schema, description) "
            "VALUES (:profile, 'test profile')"
        ),
        {"profile": profile},
    )
    for code, role in roles.items():
        db.execute(
            sa.text(
                """
                INSERT INTO tdcc_distribution_schema_buckets (
                    distribution_schema, bucket_code, bucket_role, description
                ) VALUES (:profile, :code, :role, 'test bucket')
                """
            ),
            {"profile": profile, "code": code, "role": role},
        )


def distribution_rows(db: Connection, version_id: int) -> dict[str, tuple]:
    return {
        row.bucket_code: (row.holder_count, row.shares)
        for row in db.execute(
            sa.text(
                """
                SELECT bucket_code, holder_count, shares FROM tdcc_distribution
                WHERE snapshot_version_id = :id
                """
            ),
            {"id": version_id},
        )
    }


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
        assert resolved.snapshot.data["distribution_schema"] == TDCC_OPENDATA_V1
        assert [item.bucket_code for item in resolved.distribution] == [
            str(level) for level in range(1, 18)
        ]
        assert resolved.distribution[-1].bucket_role == "total"
        assert resolved.distribution[-1].holder_count == 1000
        assert resolved.distribution[-1].ownership_percent == Decimal("100")


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
        snapshot_date=SNAPSHOT_DATE,
        distribution_schema=TDCC_OPENDATA_V1,
        distribution=tuple(reversed(buckets())),
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
        db, security_id=security_id, source="tdcc", snapshot_date=dates[2],
        distribution_schema=TDCC_OPENDATA_V1, lineage=lineage_ref,
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


def test_signed_level16_adjustment_is_stored_and_hashed_verbatim(db: Connection) -> None:
    security_id = configure(db)
    negative = WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(adjustment_shares="-2000"), lineage=lineage(db, "1"),
    )
    assert distribution_rows(db, negative.version_id)["16"] == (None, Decimal("-2000"))
    resolved = SERVICE.snapshot(
        db, security_code="2330", snapshot_date=SNAPSHOT_DATE,
        context=SystemPITContext(later()),
    )
    assert resolved is not None
    adjustment = resolved.distribution[15]
    assert (adjustment.bucket_code, adjustment.bucket_role) == ("16", "adjustment")
    assert adjustment.shares == Decimal("-2000")
    assert adjustment.holder_count is None

    # The sign is business content: the positive value is a different revision.
    positive = WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(adjustment_shares="2000"), lineage=lineage(db, "2"),
    )
    assert positive.created is True
    assert positive.business_content_hash != negative.business_content_hash
    assert negative.business_content_hash == db.scalar(
        sa.text("SELECT stockdc_tdcc_snapshot_business_hash(:id)"),
        {"id": negative.version_id},
    )


def test_adjustment_holder_count_blank_or_zero_is_canonical_null(
    db: Connection,
) -> None:
    security_id = configure(db)
    blank = WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(adjustment_holders=None), lineage=lineage(db, "3"),
    )
    zero = WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(adjustment_holders=0), lineage=lineage(db, "4"),
    )
    # Historical blank and open-data 0 are the same canonical business content.
    assert zero.created is False
    assert zero.version_id == blank.version_id
    assert distribution_rows(db, blank.version_id)["16"][0] is None

    with pytest.raises(TDCCDistributionError, match="holder_count"):
        WRITER.write_snapshot(
            db, security_id=security_id, source="tdcc",
            observation=snapshot(adjustment_holders=3), lineage=lineage(db, "5"),
        )
    raw = draft(db, security_id, lineage(db, "6"), buckets()[:15])
    assert_sqlstate(
        db,
        "23514",
        """
        INSERT INTO tdcc_distribution (
            snapshot_version_id, bucket_code, holder_count, shares, ownership_percent
        ) VALUES (:id, '16', 3, -2000, 0)
        """,
        id=raw,
    )


def test_non_adjustment_buckets_reject_negative_or_missing_values(
    db: Connection,
) -> None:
    security_id = configure(db)
    items = list(buckets())
    items[4] = TDCCBucketObservation("5", 10, Decimal("-1"), Decimal("1"))
    with pytest.raises(TDCCDistributionError, match="bucket 5"):
        WRITER.write_snapshot(
            db, security_id=security_id, source="tdcc",
            observation=TDCCSnapshotObservation(
                snapshot_date=SNAPSHOT_DATE,
                distribution_schema=TDCC_OPENDATA_V1,
                distribution=tuple(items),
            ),
            lineage=lineage(db, "7"),
        )
    raw = draft(db, security_id, lineage(db, "8"), buckets()[:4])
    for holders, shares, percent in (("10", "-1", "1"), ("NULL", "1", "1"), ("10", "1", "-1")):
        assert_sqlstate(
            db,
            "23514",
            f"""
            INSERT INTO tdcc_distribution (
                snapshot_version_id, bucket_code, holder_count, shares, ownership_percent
            ) VALUES (:id, '5', {holders}, {shares}, {percent})
            """,
            id=raw,
        )
    assert_sqlstate(
        db,
        "23514",
        """
        INSERT INTO tdcc_distribution (
            snapshot_version_id, bucket_code, holder_count, shares, ownership_percent
        ) VALUES (:id, '18', 1, 1, 1)
        """,
        id=raw,
    )


def test_incomplete_distribution_cannot_be_sealed(db: Connection) -> None:
    security_id = configure(db)
    missing_total = TDCCSnapshotObservation(
        snapshot_date=SNAPSHOT_DATE,
        distribution_schema=TDCC_OPENDATA_V1,
        distribution=buckets()[:16],
    )
    with pytest.raises(TDCCDistributionError, match="missing buckets: 17"):
        WRITER.write_snapshot(
            db, security_id=security_id, source="tdcc",
            observation=missing_total, lineage=lineage(db, "9"),
        )
    only_level15 = TDCCSnapshotObservation(
        snapshot_date=SNAPSHOT_DATE,
        distribution_schema=TDCC_OPENDATA_V1,
        distribution=(buckets()[14],),
    )
    with pytest.raises(TDCCDistributionError, match="missing buckets"):
        WRITER.write_snapshot(
            db, security_id=security_id, source="tdcc",
            observation=only_level15, lineage=lineage(db, "a"),
        )
    assert db.scalar(sa.text("SELECT count(*) FROM tdcc_snapshot_versions")) == 0

    # The DB seal enforces the same completeness for every write path.
    partial = draft(db, security_id, lineage(db, "b"), (buckets()[14],))
    with pytest.raises(DBAPIError) as error:
        with db.begin_nested():
            WRITER.seal(db, version_id=partial)
    assert error.value.orig.sqlstate == "23514"
    assert SERVICE.snapshot(
        db, security_code="2330", snapshot_date=SNAPSHOT_DATE,
        context=SystemPITContext(later()),
    ) is None

    complete = WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(), lineage=lineage(db, "c"),
    )
    assert complete.created is True
    assert len(distribution_rows(db, complete.version_id)) == 17


def test_distribution_schema_must_be_a_registered_profile(db: Connection) -> None:
    security_id = configure(db)
    unknown = TDCCSnapshotObservation(
        snapshot_date=SNAPSHOT_DATE,
        distribution_schema="vendor-layout-v9",
        distribution=buckets(),
    )
    with pytest.raises(TDCCDistributionError, match="unknown distribution schema"):
        WRITER.write_snapshot(
            db, security_id=security_id, source="tdcc",
            observation=unknown, lineage=lineage(db, "d"),
        )
    assert_sqlstate(
        db,
        "55000",
        "UPDATE tdcc_distribution_schema_buckets SET bucket_role = 'holding' "
        "WHERE bucket_code = '16'",
    )
    assert_sqlstate(
        db,
        "55000",
        "DELETE FROM tdcc_distribution_schema_buckets WHERE bucket_code = '17'",
    )


PROFILE_BUCKET_INSERT = """
    INSERT INTO tdcc_distribution_schema_buckets (
        distribution_schema, bucket_code, bucket_role, description
    ) VALUES (:profile, :code, 'holding', 'late bucket')
"""


def test_used_profile_definition_is_frozen_and_changes_need_a_new_code(
    db: Connection,
) -> None:
    security_id = configure(db)
    # Case A: a profile referenced by any snapshot (even a draft) is frozen.
    first = WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(), lineage=lineage(db, "1"),
    )
    assert_sqlstate(
        db, "55000", PROFILE_BUCKET_INSERT, profile=TDCC_OPENDATA_V1, code="18"
    )

    # Case B: an unused profile can still be defined bucket by bucket.
    v2_roles = {str(level): "holding" for level in range(1, 17)}
    v2_roles |= {"17": "adjustment", "18": "total"}
    add_profile(db, "tdcc-opendata-v2", v2_roles)

    # Case D: changed semantics use the new code; the old profile keeps its
    # 1-17 contract and its sealed snapshots stay complete and resolvable.
    v2_items = tuple(
        TDCCBucketObservation(code, None if role == "adjustment" else 1,
                              Decimal(1), Decimal(1))
        for code, role in v2_roles.items()
    )
    v2 = WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=TDCCSnapshotObservation(
            snapshot_date=date(2024, 1, 12),
            distribution_schema="tdcc-opendata-v2",
            distribution=v2_items,
        ),
        lineage=lineage(db, "2"),
    )
    assert v2.created is True
    v1_again = WRITER.write_snapshot(
        db, security_id=security_id, source="tdcc",
        observation=snapshot(date(2024, 1, 19)), lineage=lineage(db, "3"),
    )
    assert v1_again.created is True
    resolved = SERVICE.snapshot(
        db, security_code="2330", snapshot_date=SNAPSHOT_DATE,
        context=SystemPITContext(later()),
    )
    assert resolved is not None
    assert resolved.snapshot.provenance.version_id == first.version_id
    assert len(resolved.distribution) == 17

    # Case C: once v2 is used, it is frozen as well.
    assert_sqlstate(
        db, "55000", PROFILE_BUCKET_INSERT, profile="tdcc-opendata-v2", code="19"
    )


def test_profile_freeze_serializes_with_first_snapshot_use(
    isolated_database_url: str,
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as setup:
            security_id = configure(setup)
            lineage_ref = lineage(setup, "1")
            add_profile(setup, "test-first-use-v1", {"a": "holding"})
            add_profile(setup, "test-first-use-v2", {"a": "holding"})

        def begin(connection: Connection, profile: str) -> int:
            return WRITER.begin_snapshot(
                connection, security_id=security_id, source="tdcc",
                snapshot_date=SNAPSHOT_DATE, distribution_schema=profile,
                lineage=lineage_ref,
            )

        # First use commits first: the concurrent definition change is rejected.
        with engine.connect() as user:
            transaction = user.begin()
            begin(user, "test-first-use-v1")
            started = Event()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    concurrent_bucket_insert, engine, "test-first-use-v1", started
                )
                assert started.wait(timeout=2)
                with pytest.raises(FutureTimeout):
                    future.result(timeout=0.25)
                transaction.commit()
                assert future.result(timeout=5) == "55000"

        # Definition commits first: the snapshot then uses the extended profile.
        with engine.connect() as definer:
            transaction = definer.begin()
            definer.execute(
                sa.text(PROFILE_BUCKET_INSERT),
                {"profile": "test-first-use-v2", "code": "b"},
            )
            started = Event()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    concurrent_first_use, engine, begin, "test-first-use-v2", started
                )
                assert started.wait(timeout=2)
                with pytest.raises(FutureTimeout):
                    future.result(timeout=0.25)
                transaction.commit()
                assert future.result(timeout=5) == "committed"

        with engine.connect() as check:
            assert check.scalar(
                sa.text(
                    "SELECT count(*) FROM tdcc_distribution_schema_buckets "
                    "WHERE distribution_schema = :profile"
                ),
                {"profile": "test-first-use-v1"},
            ) == 1
            assert check.scalar(
                sa.text(
                    "SELECT count(*) FROM tdcc_distribution_schema_buckets "
                    "WHERE distribution_schema = :profile"
                ),
                {"profile": "test-first-use-v2"},
            ) == 2
    finally:
        engine.dispose()


def concurrent_bucket_insert(engine: Engine, profile: str, started: Event) -> str:
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            started.set()
            connection.execute(
                sa.text(PROFILE_BUCKET_INSERT), {"profile": profile, "code": "late"}
            )
    except DBAPIError as error:
        return str(getattr(error.orig, "sqlstate", "unknown"))
    return "committed"


def concurrent_first_use(engine: Engine, begin, profile: str, started: Event) -> str:
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            started.set()
            begin(connection, profile)
    except DBAPIError as error:
        return str(getattr(error.orig, "sqlstate", "unknown"))
    return "committed"


def test_database_hash_function_matches_seal_and_orders_buckets_bytewise(
    db: Connection,
) -> None:
    security_id = configure(db)
    add_profile(db, "test-bytewise-v1", {"b": "holding", "B": "holding",
                                         "a": "holding", "_": "holding"})
    version_id = WRITER.begin_snapshot(
        db, security_id=security_id, source="tdcc", snapshot_date=SNAPSHOT_DATE,
        distribution_schema="test-bytewise-v1", lineage=lineage(db, "f"),
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
                'distribution_schema', 'test-bytewise-v1',
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


def insert_legacy_snapshot(
    connection: Connection, levels: range, *, sealed: bool = True
) -> tuple[int, TDCCLineageRef]:
    security_id = configure(connection)
    source_lineage = lineage(connection, "0")
    version_id = connection.execute(
        sa.text(
            """
            INSERT INTO tdcc_snapshot_versions (
                security_id, source, snapshot_date, raw_artifact_id, ingest_run_id
            ) VALUES (:security, 'tdcc', DATE '2024-01-05', :artifact, :run)
            RETURNING id
            """
        ),
        {
            "security": security_id,
            "artifact": source_lineage.raw_artifact_id,
            "run": source_lineage.ingest_run_id,
        },
    ).scalar_one()
    for level in levels:
        connection.execute(
            sa.text(
                """
                INSERT INTO tdcc_distribution (
                    snapshot_version_id, bucket_code, holder_count,
                    shares, ownership_percent
                ) VALUES (:id, :code, :holders, 10, 5.5)
                """
            ),
            # Legacy forms wrote 0 for the adjustment holder count.
            {"id": version_id, "code": str(level), "holders": 0 if level == 16 else 7},
        )
    if sealed:
        connection.execute(
            sa.text(
                """
                INSERT INTO tdcc_snapshot_seals (
                    snapshot_version_id, business_content_hash, ingested_at
                ) VALUES (:id, :hash, now())
                """
            ),
            {"id": version_id, "hash": "0" * 64},
        )
    return version_id, source_lineage


def test_migration_profiles_backfills_and_rehashes_existing_snapshot(
    isolated_database_url: str,
) -> None:
    config = alembic_config(isolated_database_url)
    command.downgrade(config, PRE_PHASE6_REVISION)
    migration_engine = sa.create_engine(isolated_database_url)
    try:
        with migration_engine.begin() as connection:
            version_id, source_lineage = insert_legacy_snapshot(connection, range(1, 18))
            legacy_hash = connection.scalar(
                sa.text(
                    "SELECT business_content_hash FROM tdcc_snapshot_seals "
                    "WHERE snapshot_version_id = :id"
                ),
                {"id": version_id},
            )
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
            migrated = connection.execute(
                sa.text(
                    """
                    SELECT snapshot.distribution_schema,
                           snapshot.business_content_hash AS parent_hash,
                           seal.business_content_hash AS seal_hash,
                           stockdc_tdcc_snapshot_business_hash(snapshot.id) AS recomputed
                    FROM tdcc_snapshot_versions snapshot
                    JOIN tdcc_snapshot_seals seal
                      ON seal.snapshot_version_id = snapshot.id
                    WHERE snapshot.id = :id
                    """
                ),
                {"id": version_id},
            ).one()
            assert migrated.distribution_schema == TDCC_OPENDATA_V1
            assert migrated.parent_hash == migrated.seal_hash == migrated.recomputed
            assert migrated.recomputed != legacy_hash
            assert distribution_rows(connection, version_id)["16"][0] is None
    finally:
        migration_engine.dispose()


def test_migration_aborts_on_existing_incomplete_sealed_snapshot(
    isolated_database_url: str,
) -> None:
    config = alembic_config(isolated_database_url)
    command.downgrade(config, PRE_PHASE6_REVISION)
    migration_engine = sa.create_engine(isolated_database_url)
    try:
        with migration_engine.begin() as connection:
            insert_legacy_snapshot(connection, range(1, 17))
        migration_engine.dispose()
        with pytest.raises(DBAPIError, match="incomplete"):
            command.upgrade(config, "head")
    finally:
        migration_engine.dispose()
