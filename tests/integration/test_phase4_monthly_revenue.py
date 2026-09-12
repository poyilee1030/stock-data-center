from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.monthly_revenue import (
    MonthlyRevenueObservation,
    MonthlyRevenuePublication,
    MonthlyRevenueService,
    MonthlyRevenueWriter,
    RevenueLineageRef,
    RevenuePeriod,
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
    evidence(
        db,
        repeated.version_id,
        second_lineage,
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
    assert resolved is not None
    assert db.scalar(sa.text("SELECT count(*) FROM monthly_revenue_versions")) == 1
    assert db.scalar(sa.text("SELECT count(*) FROM publication_evidence")) == 2
    assert db.scalar(sa.text("SELECT count(*) FROM raw_artifact_observations")) == 2


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
