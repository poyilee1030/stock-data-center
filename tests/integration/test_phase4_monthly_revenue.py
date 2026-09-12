from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError

from conftest import alembic_config
from stock_data_center.monthly_revenue import (
    MonthlyRevenueObservation,
    MonthlyRevenuePublication,
    MonthlyRevenueService,
    MonthlyRevenueWriter,
    RevenueLineageRef,
    RevenuePeriod,
    RevenueScale,
    SourceRevenueAmount,
)
from stock_data_center.pit import MarketPITContext, SystemPITContext


pytestmark = pytest.mark.integration

WRITER = MonthlyRevenueWriter()
SERVICE = MonthlyRevenueService()
PERIOD = RevenuePeriod(2025, 4)


def configure_source(
    db: Connection,
    source: str = "mops",
    *,
    canonical: bool = True,
) -> int:
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('monthly_revenue', 'Monthly revenue', 'v1')
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
                'monthly_revenue', :source, true, true, 100, 'verified',
                ARRAY['official'], :canonical
            )
            """
        ),
        {"source": source, "canonical": canonical},
    )
    return db.execute(
        sa.text(
            """
            INSERT INTO security (security_code)
            VALUES (:code)
            RETURNING id
            """
        ),
        {"code": f"revenue-{source}"},
    ).scalar_one()


def lineage(
    db: Connection,
    source: str = "mops",
    digest_character: str = "1",
) -> RevenueLineageRef:
    run_id = db.execute(
        sa.text(
            """
            INSERT INTO ingest_runs (dataset_code, source, status, started_at)
            VALUES ('monthly_revenue', :source, 'succeeded', statement_timestamp())
            RETURNING id
            """
        ),
        {"source": source},
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
            "uri": f"https://{source}.test/revenue",
        },
    )
    return RevenueLineageRef(artifact_id, run_id)


def revenue(
    db: Connection,
    security_id: int,
    lineage_ref: RevenueLineageRef,
    amount: str = "1000000",
    *,
    source: str = "mops",
    period: RevenuePeriod = PERIOD,
):
    return WRITER.append_revenue(
        db,
        security_id=security_id,
        source=source,
        observation=MonthlyRevenueObservation(
            period=period,
            revenue=Decimal(amount),
            currency="TWD",
        ),
        lineage=lineage_ref,
    )


def evidence(
    db: Connection,
    version_id: int,
    lineage_ref: RevenueLineageRef,
    *,
    kind: str = "assertion",
    published_at: datetime | None = datetime(2025, 5, 10, 6, tzinfo=UTC),
    supersedes: int | None = None,
    source: str = "mops",
) -> int:
    return WRITER.append_publication_evidence(
        db,
        source=source,
        version_id=version_id,
        observation=MonthlyRevenuePublication(
            evidence_kind=kind,
            published_at=published_at,
            evidence_source="mops",
            evidence_type="official",
            quality_rank=100,
            supersedes_evidence_id=supersedes,
        ),
        lineage=lineage_ref,
    )


def market(
    information_as_of: datetime,
    knowledge_as_of: datetime | None = None,
) -> MarketPITContext:
    return MarketPITContext(
        information_as_of=information_as_of,
        knowledge_as_of=knowledge_as_of
        or datetime.now(UTC) + timedelta(days=1),
    )


def test_unknown_publication_is_market_invisible_but_system_visible(
    db: Connection,
) -> None:
    security_id = configure_source(db)
    source_lineage = lineage(db)
    version = revenue(db, security_id, source_lineage)
    evidence(
        db,
        version.version_id,
        source_lineage,
        kind="unknown",
        published_at=None,
    )

    market_result = SERVICE.revenue(
        db,
        security_code="revenue-mops",
        period=PERIOD,
        context=market(datetime(2025, 12, 31, tzinfo=UTC)),
        source="mops",
    )
    system_result = SERVICE.revenue(
        db,
        security_code="revenue-mops",
        period=PERIOD,
        context=SystemPITContext(datetime.now(UTC) + timedelta(days=1)),
        source="mops",
    )

    assert market_result is None
    assert system_result is not None
    assert system_result.data["revenue"] == Decimal("1000000")


def test_later_official_evidence_improves_same_business_version(
    db: Connection,
) -> None:
    security_id = configure_source(db)
    source_lineage = lineage(db)
    version = revenue(db, security_id, source_lineage)
    unknown = evidence(
        db,
        version.version_id,
        source_lineage,
        kind="unknown",
        published_at=None,
    )
    old_knowledge = db.scalar(sa.select(sa.func.clock_timestamp()))
    evidence(
        db,
        version.version_id,
        source_lineage,
        supersedes=unknown,
    )

    historical = SERVICE.revenue(
        db,
        security_code="revenue-mops",
        period=PERIOD,
        context=market(
            datetime(2025, 5, 31, tzinfo=UTC), old_knowledge
        ),
        source="mops",
    )
    improved = SERVICE.revenue(
        db,
        security_code="revenue-mops",
        period=PERIOD,
        context=market(datetime(2025, 5, 31, tzinfo=UTC)),
        source="mops",
    )

    assert historical is None
    assert improved is not None
    assert improved.provenance.version_id == version.version_id
    assert db.scalar(sa.text("SELECT count(*) FROM monthly_revenue_versions")) == 1


def test_delayed_publication_uses_information_cutoff(db: Connection) -> None:
    security_id = configure_source(db)
    source_lineage = lineage(db)
    version = revenue(db, security_id, source_lineage)
    evidence(db, version.version_id, source_lineage)

    before = SERVICE.revenue(
        db,
        security_code="revenue-mops",
        period=PERIOD,
        context=market(datetime(2025, 5, 10, 5, 59, tzinfo=UTC)),
        source="mops",
    )
    after = SERVICE.revenue(
        db,
        security_code="revenue-mops",
        period=PERIOD,
        context=market(datetime(2025, 5, 10, 6, tzinfo=UTC)),
        source="mops",
    )

    assert before is None
    assert after is not None


def test_business_revision_respects_knowledge_cutoff(db: Connection) -> None:
    security_id = configure_source(db)
    first_lineage = lineage(db, digest_character="2")
    second_lineage = lineage(db, digest_character="3")
    first = revenue(db, security_id, first_lineage, "1000000")
    evidence(db, first.version_id, first_lineage)
    old_knowledge = db.scalar(sa.select(sa.func.clock_timestamp()))
    revised = revenue(db, security_id, second_lineage, "1100000")
    evidence(
        db,
        revised.version_id,
        second_lineage,
        published_at=datetime(2025, 5, 12, 6, tzinfo=UTC),
    )
    information = datetime(2025, 5, 31, tzinfo=UTC)

    historical = SERVICE.revenue(
        db,
        security_code="revenue-mops",
        period=PERIOD,
        context=market(information, old_knowledge),
        source="mops",
    )
    current_best = SERVICE.revenue(
        db,
        security_code="revenue-mops",
        period=PERIOD,
        context=market(information),
        source="mops",
    )

    assert historical is not None and historical.data["revenue"] == 1000000
    assert current_best is not None and current_best.data["revenue"] == 1100000


def test_unchanged_fetch_keeps_one_revision_and_new_evidence(
    db: Connection,
) -> None:
    security_id = configure_source(db)
    first_lineage = lineage(db, digest_character="4")
    second_lineage = lineage(db, digest_character="5")
    first = revenue(db, security_id, first_lineage)
    repeated = revenue(db, security_id, second_lineage)
    unknown = evidence(
        db,
        first.version_id,
        first_lineage,
        kind="unknown",
        published_at=None,
    )
    official = evidence(
        db,
        repeated.version_id,
        second_lineage,
        supersedes=unknown,
    )
    repeated_official = evidence(
        db,
        repeated.version_id,
        first_lineage,
        supersedes=unknown,
    )

    resolved = SERVICE.revenue(
        db,
        security_code="revenue-mops",
        period=PERIOD,
        context=market(datetime(2025, 5, 31, tzinfo=UTC)),
        source="mops",
    )
    assert first.created is True
    assert repeated.created is False
    assert repeated.version_id == first.version_id
    assert repeated_official == official
    assert resolved is not None
    assert db.scalar(sa.text("SELECT count(*) FROM monthly_revenue_versions")) == 1
    assert db.scalar(sa.text("SELECT count(*) FROM publication_evidence")) == 2
    assert db.scalar(sa.text("SELECT count(*) FROM raw_artifact_observations")) == 2
    assert db.scalar(
        sa.text("SELECT count(*) FROM monthly_revenue_version_observations")
    ) == 2
    assert db.scalar(
        sa.text("SELECT count(*) FROM publication_evidence_observations")
    ) == 3
    observations = SERVICE.observations(db, version_id=first.version_id)
    assert {item.raw_artifact_id for item in observations} == {
        first_lineage.raw_artifact_id,
        second_lineage.raw_artifact_id,
    }
    assert {item.ingest_run_id for item in observations} == {
        first_lineage.ingest_run_id,
        second_lineage.ingest_run_id,
    }


def test_source_thousand_scale_normalizes_to_currency_major_unit(
    db: Connection,
) -> None:
    security_id = configure_source(db)
    source_lineage = lineage(db, digest_character="9")
    equivalent_lineage = lineage(db, digest_character="a")
    normalized = MonthlyRevenueObservation.from_source(
        period=PERIOD,
        amount=SourceRevenueAmount(
            value=Decimal("410000000"),
            currency="TWD",
            scale=RevenueScale.THOUSAND,
        ),
    )
    canonical = MonthlyRevenueObservation(
        period=PERIOD,
        revenue=Decimal("410000000000"),
        currency="TWD",
    )

    first = WRITER.append_revenue(
        db,
        security_id=security_id,
        source="mops",
        observation=normalized,
        lineage=source_lineage,
    )
    equivalent = WRITER.append_revenue(
        db,
        security_id=security_id,
        source="mops",
        observation=canonical,
        lineage=equivalent_lineage,
    )

    assert normalized.revenue == Decimal("410000000000")
    assert first.version_id == equivalent.version_id
    assert first.business_content_hash == equivalent.business_content_hash
    assert equivalent.created is False
    assert db.scalar(sa.text("SELECT count(*) FROM monthly_revenue_versions")) == 1
    assert db.scalar(
        sa.text("SELECT count(*) FROM monthly_revenue_version_observations")
    ) == 2


def test_version_observation_rejects_cross_source_lineage(db: Connection) -> None:
    security_id = configure_source(db, "mops")
    configure_source(db, "vendor", canonical=False)
    mops_lineage = lineage(db, "mops", "b")
    vendor_lineage = lineage(db, "vendor", "c")
    version = revenue(db, security_id, mops_lineage)

    with pytest.raises(DBAPIError, match="dataset/source mismatch"):
        with db.begin_nested():
            db.execute(
                sa.text(
                    """
                    INSERT INTO monthly_revenue_version_observations (
                        monthly_revenue_version_id, raw_artifact_id, ingest_run_id
                    ) VALUES (:version, :artifact, :run)
                    """
                ),
                {
                    "version": version.version_id,
                    "artifact": vendor_lineage.raw_artifact_id,
                    "run": vendor_lineage.ingest_run_id,
                },
            )


def test_lineage_migration_backfills_existing_version_and_evidence(
    isolated_database_url: str,
) -> None:
    config = alembic_config(isolated_database_url)
    command.downgrade(config, "f3a74c12e690")
    migration_engine = sa.create_engine(isolated_database_url)
    try:
        with migration_engine.begin() as connection:
            security_id = configure_source(connection)
            source_lineage = lineage(connection, digest_character="d")
            version_id = connection.execute(
                sa.text(
                    """
                    INSERT INTO monthly_revenue_versions (
                        security_id, source, revenue_year, revenue_month,
                        revenue, currency, business_content_hash, ingested_at,
                        raw_artifact_id, ingest_run_id
                    ) VALUES (
                        :security, 'mops', 2025, 4, 100, 'TWD', :hash,
                        statement_timestamp(), :artifact, :run
                    ) RETURNING id
                    """
                ),
                {
                    "security": security_id,
                    "hash": "0" * 64,
                    "artifact": source_lineage.raw_artifact_id,
                    "run": source_lineage.ingest_run_id,
                },
            ).scalar_one()
            evidence_id = connection.execute(
                sa.text(
                    """
                    INSERT INTO publication_evidence (
                        dataset_code, source, evidence_kind, published_at,
                        evidence_source, evidence_type, quality_rank,
                        monthly_revenue_version_id, publication_evidence_hash,
                        recorded_at, raw_artifact_id, ingest_run_id
                    ) VALUES (
                        'monthly_revenue', 'mops', 'assertion',
                        TIMESTAMPTZ '2025-05-10 06:00+00', 'mops', 'official',
                        100, :version, :hash, statement_timestamp(),
                        :artifact, :run
                    ) RETURNING id
                    """
                ),
                {
                    "version": version_id,
                    "hash": "0" * 64,
                    "artifact": source_lineage.raw_artifact_id,
                    "run": source_lineage.ingest_run_id,
                },
            ).scalar_one()
        migration_engine.dispose()

        command.upgrade(config, "head")
        migration_engine = sa.create_engine(isolated_database_url)
        with migration_engine.connect() as connection:
            version_link = connection.execute(
                sa.text(
                    """
                    SELECT raw_artifact_id, ingest_run_id
                    FROM monthly_revenue_version_observations
                    WHERE monthly_revenue_version_id = :version
                    """
                ),
                {"version": version_id},
            ).one()
            evidence_link = connection.execute(
                sa.text(
                    """
                    SELECT raw_artifact_id, ingest_run_id
                    FROM publication_evidence_observations
                    WHERE publication_evidence_id = :evidence
                    """
                ),
                {"evidence": evidence_id},
            ).one()
            expected = (
                source_lineage.raw_artifact_id,
                source_lineage.ingest_run_id,
            )
            assert version_link == expected
            assert evidence_link == expected
    finally:
        migration_engine.dispose()


def test_source_histories_are_independent(db: Connection) -> None:
    security_id = configure_source(db, "mops")
    configure_source(db, "vendor", canonical=False)
    mops_lineage = lineage(db, "mops", "6")
    vendor_lineage = lineage(db, "vendor", "7")
    mops = revenue(db, security_id, mops_lineage, "100", source="mops")
    vendor = revenue(
        db, security_id, vendor_lineage, "101", source="vendor"
    )
    evidence(db, mops.version_id, mops_lineage, source="mops")
    evidence(db, vendor.version_id, vendor_lineage, source="vendor")

    mops_result = SERVICE.revenue(
        db,
        security_code="revenue-mops",
        period=PERIOD,
        context=market(datetime(2025, 5, 31, tzinfo=UTC)),
        source="mops",
    )
    vendor_result = SERVICE.revenue(
        db,
        security_code="revenue-mops",
        period=PERIOD,
        context=market(datetime(2025, 5, 31, tzinfo=UTC)),
        source="vendor",
    )
    assert mops_result is not None and mops_result.data["revenue"] == 100
    assert vendor_result is not None and vendor_result.data["revenue"] == 101


def test_history_orders_periods_and_omits_future_publications(
    db: Connection,
) -> None:
    security_id = configure_source(db)
    source_lineage = lineage(db, digest_character="8")
    for period, amount, published_at in (
        (RevenuePeriod(2025, 3), "90", datetime(2025, 4, 10, tzinfo=UTC)),
        (RevenuePeriod(2025, 4), "100", datetime(2025, 5, 10, tzinfo=UTC)),
        (RevenuePeriod(2025, 5), "110", datetime(2025, 6, 10, tzinfo=UTC)),
    ):
        version = revenue(
            db, security_id, source_lineage, amount, period=period
        )
        evidence(
            db,
            version.version_id,
            source_lineage,
            published_at=published_at,
        )

    history = SERVICE.history(
        db,
        security_code="revenue-mops",
        start_period=RevenuePeriod(2025, 3),
        end_period=RevenuePeriod(2025, 5),
        context=market(datetime(2025, 5, 31, tzinfo=UTC)),
        source="mops",
    )
    assert [row.data["revenue_month"] for row in history] == [3, 4]
