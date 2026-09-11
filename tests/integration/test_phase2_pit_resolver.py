from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.pit import (
    AmbiguousSourceError,
    InvalidLogicalKeyError,
    InvalidPITContextError,
    MarketPITContext,
    PITResolver,
    SystemPITContext,
    UnsupportedPITModeError,
)


pytestmark = pytest.mark.integration


def configure_source(
    db: Connection,
    dataset: str,
    source: str = "official",
    *,
    market: bool = True,
    system: bool = True,
    verified: bool = True,
    canonical: bool = True,
) -> int:
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
                publication_time_quality, evidence_status, is_canonical
            ) VALUES (
                :dataset, :source, :market, :system, 100, :status, :canonical
            )
            """
        ),
        {
            "dataset": dataset,
            "source": source,
            "market": market,
            "system": system,
            "status": "verified" if verified else "unverified",
            "canonical": canonical,
        },
    )
    return db.execute(
        sa.text(
            """
            INSERT INTO security (security_code, market)
            VALUES (:code, 'TWSE')
            ON CONFLICT (security_code) DO UPDATE SET market = EXCLUDED.market
            RETURNING id
            """
        ),
        {"code": f"{dataset[:12]}-{source[:8]}"},
    ).scalar_one()


def seed_lineage(
    db: Connection,
    dataset: str,
    source: str = "official",
    *,
    digest_char: str = "a",
) -> tuple[str, str]:
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
    digest = digest_char * 64
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
            ) VALUES (
                :artifact, :run, 'https://source.test/data', statement_timestamp()
            )
            """
        ),
        {"artifact": artifact_id, "run": run_id},
    )
    return str(artifact_id), str(run_id)


def insert_price(
    db: Connection,
    security_id: int,
    artifact_id: str,
    run_id: str,
    *,
    close: int = 100,
    source: str = "official",
) -> int:
    return db.execute(
        sa.text(
            """
            INSERT INTO daily_price_versions (
                security_id, source, trade_date, close_price,
                business_content_hash, ingested_at, raw_artifact_id, ingest_run_id
            ) VALUES (
                :security, :source, DATE '2020-01-02', :close,
                :hash, TIMESTAMPTZ '2000-01-01+00', :artifact, :run
            ) RETURNING id
            """
        ),
        {
            "security": security_id,
            "source": source,
            "close": close,
            "hash": "f" * 64,
            "artifact": artifact_id,
            "run": run_id,
        },
    ).scalar_one()


def insert_evidence(
    db: Connection,
    version_id: int,
    artifact_id: str,
    run_id: str,
    *,
    kind: str = "assertion",
    published_at: datetime | None = datetime(2020, 1, 2, 6, tzinfo=UTC),
    supersedes: int | None = None,
    quality: int = 100,
    evidence_source: str = "exchange",
) -> int:
    return db.execute(
        sa.text(
            """
            INSERT INTO publication_evidence (
                dataset_code, source, evidence_kind, published_at,
                evidence_source, evidence_type, quality_rank,
                supersedes_evidence_id, daily_price_version_id,
                publication_evidence_hash, recorded_at,
                raw_artifact_id, ingest_run_id
            ) VALUES (
                'daily_price', 'official', :kind, :published_at,
                :evidence_source, 'official', :quality, :supersedes, :version,
                :hash, TIMESTAMPTZ '2000-01-01+00', :artifact, :run
            ) RETURNING id
            """
        ),
        {
            "kind": kind,
            "published_at": published_at,
            "evidence_source": evidence_source,
            "quality": quality,
            "supersedes": supersedes,
            "version": version_id,
            "hash": "e" * 64,
            "artifact": artifact_id,
            "run": run_id,
        },
    ).scalar_one()


def key(security_id: int) -> dict[str, Any]:
    return {"security_id": security_id, "trade_date": datetime(2020, 1, 2).date()}


def future() -> datetime:
    return datetime.now(UTC) + timedelta(days=1)


def test_market_historical_and_current_best_reconstruction(db: Connection) -> None:
    security_id = configure_source(db, "daily_price")
    artifact, run = seed_lineage(db, "daily_price")
    version = insert_price(db, security_id, artifact, run)
    assertion = insert_evidence(
        db, version, artifact, run,
        published_at=datetime(2020, 1, 2, 10, tzinfo=UTC),
    )
    historical_knowledge = db.scalar(sa.select(sa.func.clock_timestamp()))
    correction = insert_evidence(
        db, version, artifact, run,
        kind="correction",
        published_at=datetime(2020, 1, 2, 9, tzinfo=UTC),
        supersedes=assertion,
        quality=1,
    )
    resolver = PITResolver()

    historical = resolver.resolve(
        db,
        dataset_code="daily_price",
        logical_key=key(security_id),
        context=MarketPITContext(
            datetime(2020, 1, 2, 9, 30, tzinfo=UTC), historical_knowledge
        ),
    )
    current_best = resolver.resolve(
        db,
        dataset_code="daily_price",
        logical_key=key(security_id),
        context=MarketPITContext(
            datetime(2020, 1, 2, 9, 30, tzinfo=UTC), future()
        ),
    )

    assert historical is None
    assert current_best is not None
    assert current_best.data["close_price"] == 100
    assert current_best.authoritative_evidence is not None
    assert current_best.authoritative_evidence.evidence_id == correction
    assert current_best.authoritative_evidence.supersedes_evidence_id == assertion
    assert current_best.provenance.version_id == version
    assert (
        current_best.provenance.raw_artifact_id
        == current_best.authoritative_evidence.raw_artifact_id
    )
    assert current_best.authoritative_evidence.raw_artifact_hash == "a" * 64
    assert current_best.authoritative_evidence.source_uri == "https://source.test/data"
    assert current_best.provenance.raw_artifact_hash == "a" * 64
    assert current_best.provenance.source_uri == "https://source.test/data"
    assert current_best.information_as_of == datetime(2020, 1, 2, 9, 30, tzinfo=UTC)


def test_retraction_is_visible_only_before_it_is_recorded(db: Connection) -> None:
    security_id = configure_source(db, "daily_price")
    artifact, run = seed_lineage(db, "daily_price")
    version = insert_price(db, security_id, artifact, run)
    assertion = insert_evidence(db, version, artifact, run)
    before_retraction = db.scalar(sa.select(sa.func.clock_timestamp()))
    insert_evidence(
        db, version, artifact, run,
        kind="retraction", published_at=None, supersedes=assertion, quality=1,
    )
    resolver = PITResolver()
    information = datetime(2020, 1, 3, tzinfo=UTC)

    assert resolver.resolve(
        db,
        dataset_code="daily_price",
        source="official",
        logical_key=key(security_id),
        context=MarketPITContext(information, before_retraction),
    ) is not None
    assert resolver.resolve(
        db,
        dataset_code="daily_price",
        source="official",
        logical_key=key(security_id),
        context=MarketPITContext(information, future()),
    ) is None


def test_unknown_publication_is_market_invisible_but_system_visible(db: Connection) -> None:
    security_id = configure_source(db, "daily_price")
    artifact, run = seed_lineage(db, "daily_price")
    version = insert_price(db, security_id, artifact, run)
    insert_evidence(db, version, artifact, run, kind="unknown", published_at=None)
    resolver = PITResolver()

    market = resolver.resolve(
        db,
        dataset_code="daily_price",
        logical_key=key(security_id),
        context=MarketPITContext(datetime(2020, 1, 3, tzinfo=UTC), future()),
    )
    system = resolver.resolve(
        db,
        dataset_code="daily_price",
        logical_key=key(security_id),
        context=SystemPITContext(future()),
    )
    assert market is None
    assert system is not None
    assert system.authoritative_evidence is None
    assert system.pit_mode == "system"


def test_backfill_does_not_rewrite_system_ingestion_history(db: Connection) -> None:
    security_id = configure_source(db, "daily_price")
    before_ingestion = db.scalar(sa.select(sa.func.clock_timestamp()))
    artifact, run = seed_lineage(db, "daily_price", digest_char="b")
    version = insert_price(db, security_id, artifact, run)
    insert_evidence(
        db, version, artifact, run,
        published_at=datetime(2020, 1, 2, 6, tzinfo=UTC),
    )
    resolver = PITResolver()

    assert resolver.resolve(
        db,
        dataset_code="daily_price",
        logical_key=key(security_id),
        context=SystemPITContext(before_ingestion),
    ) is None
    assert resolver.resolve(
        db,
        dataset_code="daily_price",
        logical_key=key(security_id),
        context=SystemPITContext(future()),
    ) is not None
    assert resolver.resolve(
        db,
        dataset_code="daily_price",
        logical_key=key(security_id),
        context=MarketPITContext(datetime(2020, 1, 3, tzinfo=UTC), future()),
    ) is not None


def test_evidence_head_ranking_is_quality_then_recorded_time_then_id(db: Connection) -> None:
    security_id = configure_source(db, "daily_price")
    artifact, run = seed_lineage(db, "daily_price", digest_char="c")
    version = insert_price(db, security_id, artifact, run)
    preferred = insert_evidence(
        db, version, artifact, run,
        quality=90,
        published_at=datetime(2020, 1, 2, 7, tzinfo=UTC),
        evidence_source="high-quality",
    )
    insert_evidence(
        db, version, artifact, run,
        quality=10,
        published_at=datetime(2020, 1, 2, 8, tzinfo=UTC),
        evidence_source="later-low-quality",
    )
    result = PITResolver().resolve(
        db,
        dataset_code="daily_price",
        logical_key=key(security_id),
        context=MarketPITContext(datetime(2020, 1, 3, tzinfo=UTC), future()),
    )
    assert result is not None
    assert result.authoritative_evidence is not None
    assert result.authoritative_evidence.evidence_id == preferred
    assert result.authoritative_evidence.evidence_source == "high-quality"


def test_market_revision_selection_is_publication_time_then_version_id(db: Connection) -> None:
    security_id = configure_source(db, "daily_price")
    artifact, run = seed_lineage(db, "daily_price", digest_char="6")
    first = insert_price(db, security_id, artifact, run, close=100)
    second = insert_price(db, security_id, artifact, run, close=101)
    published = datetime(2020, 1, 2, 6, tzinfo=UTC)
    insert_evidence(db, first, artifact, run, published_at=published)
    insert_evidence(db, second, artifact, run, published_at=published)

    result = PITResolver().resolve(
        db,
        dataset_code="daily_price",
        logical_key=key(security_id),
        context=MarketPITContext(datetime(2020, 1, 3, tzinfo=UTC), future()),
    )
    assert result is not None
    assert result.provenance.version_id == second
    assert result.data["close_price"] == 101


def test_source_capability_is_exact_and_canonical_selection_is_explicit(db: Connection) -> None:
    security_id = configure_source(db, "daily_price", "source_a", canonical=True)
    configure_source(
        db, "daily_price", "source_b", market=False, canonical=False
    )
    artifact, run = seed_lineage(db, "daily_price", "source_a", digest_char="d")
    version = insert_price(db, security_id, artifact, run, source="source_a")
    db.execute(
        sa.text(
            """
            INSERT INTO publication_evidence (
                dataset_code, source, evidence_kind, published_at,
                evidence_source, evidence_type, quality_rank,
                daily_price_version_id, publication_evidence_hash, recorded_at,
                raw_artifact_id, ingest_run_id
            ) VALUES (
                'daily_price', 'source_a', 'assertion',
                TIMESTAMPTZ '2020-01-02 06:00:00+00', 'exchange', 'official',
                100, :version, :hash, TIMESTAMPTZ '2000-01-01+00', :artifact, :run
            )
            """
        ),
        {"version": version, "hash": "e" * 64, "artifact": artifact, "run": run},
    )
    context = MarketPITContext(datetime(2020, 1, 3, tzinfo=UTC), future())
    resolver = PITResolver()
    result = resolver.resolve(
        db,
        dataset_code="daily_price",
        logical_key=key(security_id),
        context=context,
    )
    assert result is not None
    assert result.source == "source_a"
    with pytest.raises(UnsupportedPITModeError):
        resolver.resolve(
            db,
            dataset_code="daily_price",
            source="source_b",
            logical_key=key(security_id),
            context=context,
        )

    db.execute(
        sa.text(
            "UPDATE dataset_sources SET is_canonical = false WHERE dataset_code = 'daily_price'"
        )
    )
    with pytest.raises(AmbiguousSourceError):
        resolver.resolve(
            db,
            dataset_code="daily_price",
            logical_key=key(security_id),
            context=context,
        )


def test_unsealed_aggregate_is_invisible_and_seal_is_system_visibility(db: Connection) -> None:
    security_id = configure_source(db, "financial_filing")
    artifact, run = seed_lineage(db, "financial_filing", digest_char="e")
    filing = db.execute(
        sa.text(
            """
            INSERT INTO financial_filing_versions (
                security_id, source, filing_key, report_year, report_quarter,
                period_start, period_end, currency, business_content_hash,
                raw_artifact_id, ingest_run_id
            ) VALUES (
                :security, 'official', '2020-q1', 2020, 1,
                DATE '2020-01-01', DATE '2020-03-31', 'TWD', :hash,
                :artifact, :run
            ) RETURNING id
            """
        ),
        {"security": security_id, "hash": "f" * 64, "artifact": artifact, "run": run},
    ).scalar_one()
    db.execute(
        sa.text(
            """
            INSERT INTO financial_facts (
                filing_version_id, concept_qname, context_hash,
                entity_identifier, period_type, instant_date,
                unit_identity, numeric_value
            ) VALUES (
                :filing, '{https://example.test/tifrs}Assets', :hash,
                'TW-TEST', 'instant', DATE '2020-03-31', 'TWD', 100
            )
            """
        ),
        {"filing": filing, "hash": "0" * 64},
    )
    db.execute(
        sa.text(
            """
            INSERT INTO publication_evidence (
                dataset_code, source, evidence_kind, published_at,
                evidence_source, evidence_type, quality_rank,
                financial_filing_version_id, publication_evidence_hash,
                recorded_at, raw_artifact_id, ingest_run_id
            ) VALUES (
                'financial_filing', 'official', 'assertion',
                TIMESTAMPTZ '2020-04-01 06:00:00+00', 'mops', 'official', 100,
                :filing, :hash, TIMESTAMPTZ '2000-01-01+00', :artifact, :run
            )
            """
        ),
        {"filing": filing, "hash": "e" * 64, "artifact": artifact, "run": run},
    )
    resolver = PITResolver()
    logical_key = {"security_id": security_id, "report_year": 2020, "report_quarter": 1}
    assert resolver.resolve(
        db,
        dataset_code="financial_filing",
        logical_key=logical_key,
        context=SystemPITContext(future()),
    ) is None
    assert resolver.resolve(
        db,
        dataset_code="financial_filing",
        logical_key=logical_key,
        context=MarketPITContext(datetime(2020, 4, 2, tzinfo=UTC), future()),
    ) is None

    before_seal = db.scalar(sa.select(sa.func.clock_timestamp()))
    db.execute(
        sa.text(
            """
            INSERT INTO financial_filing_seals (
                filing_version_id, business_content_hash, ingested_at
            ) VALUES (:filing, :hash, TIMESTAMPTZ '2000-01-01+00')
            """
        ),
        {"filing": filing, "hash": "f" * 64},
    )
    assert resolver.resolve(
        db,
        dataset_code="financial_filing",
        logical_key=logical_key,
        context=SystemPITContext(before_seal),
    ) is None
    assert resolver.resolve(
        db,
        dataset_code="financial_filing",
        logical_key=logical_key,
        context=MarketPITContext(
            datetime(2020, 4, 2, tzinfo=UTC), before_seal
        ),
    ) is None
    result = resolver.resolve(
        db,
        dataset_code="financial_filing",
        logical_key=logical_key,
        context=SystemPITContext(future()),
    )
    assert result is not None
    assert result.provenance.aggregate_seal_id == filing
    assert result.provenance.business_content_hash
    assert result.provenance.ingested_at > before_seal
    market_result = resolver.resolve(
        db,
        dataset_code="financial_filing",
        logical_key=logical_key,
        context=MarketPITContext(datetime(2020, 4, 2, tzinfo=UTC), future()),
    )
    assert market_result is not None
    assert market_result.authoritative_evidence is not None


def test_context_and_logical_key_validation_are_explicit(db: Connection) -> None:
    with pytest.raises(InvalidPITContextError):
        MarketPITContext(datetime(2020, 1, 1), datetime.now(UTC))
    with pytest.raises(InvalidPITContextError):
        SystemPITContext(datetime(2020, 1, 1))

    configure_source(db, "daily_price")
    with pytest.raises(InvalidLogicalKeyError):
        PITResolver().resolve(
            db,
            dataset_code="daily_price",
            logical_key={"security_id": 1},
            context=SystemPITContext(future()),
        )


def test_resolver_package_has_no_redis_dependency() -> None:
    pit_root = __import__("pathlib").Path(__file__).parents[2] / "src/stock_data_center/pit"
    assert "redis" not in "\n".join(
        path.read_text().lower() for path in pit_root.glob("*.py")
    )
