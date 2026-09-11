from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import DBAPIError, IntegrityError

from conftest import alembic_config


pytestmark = pytest.mark.integration


def rejected(db: Connection, sql: str, **params: Any) -> None:
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(sa.text(sql), params)


def seed_lineage(
    db: Connection,
    dataset_code: str,
    *,
    source: str = "official",
    digest_char: str = "a",
    supports_market_pit: bool = True,
) -> tuple[int, str, str]:
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES (:dataset_code, :description, 'v1')
            ON CONFLICT (dataset_code) DO NOTHING
            """
        ),
        {"dataset_code": dataset_code, "description": f"{dataset_code} test data"},
    )
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_sources (
                dataset_code, source, supports_market_pit, supports_system_pit,
                publication_time_quality, evidence_status, is_canonical
            ) VALUES (
                :dataset_code, :source, :supports_market_pit, true,
                100, 'verified', false
            )
            ON CONFLICT (dataset_code, source) DO NOTHING
            """
        ),
        {
            "dataset_code": dataset_code,
            "source": source,
            "supports_market_pit": supports_market_pit,
        },
    )
    security_id = db.execute(
        sa.text(
            """
            INSERT INTO security (security_code, market)
            VALUES (:security_code, 'TWSE')
            ON CONFLICT (security_code) DO UPDATE SET market = EXCLUDED.market
            RETURNING id
            """
        ),
        {"security_code": f"{dataset_code[:10]}-{source[:8]}"},
    ).scalar_one()
    ingest_run_id = db.execute(
        sa.text(
            """
            INSERT INTO ingest_runs (dataset_code, source, status, started_at)
            VALUES (:dataset_code, :source, 'running', statement_timestamp())
            RETURNING id
            """
        ),
        {"dataset_code": dataset_code, "source": source},
    ).scalar_one()
    artifact_hash = digest_char * 64
    raw_artifact_id = db.execute(
        sa.text(
            """
            INSERT INTO raw_artifacts (
                raw_artifact_hash, storage_uri, byte_size, media_type
            ) VALUES (
                :artifact_hash, :storage_uri, 1, 'application/json'
            )
            ON CONFLICT (raw_artifact_hash) DO UPDATE
                SET raw_artifact_hash = EXCLUDED.raw_artifact_hash
            RETURNING id
            """
        ),
        {
            "artifact_hash": artifact_hash,
            "storage_uri": f"data/raw/{artifact_hash[:2]}/{artifact_hash}",
        },
    ).scalar_one()
    db.execute(
        sa.text(
            """
            INSERT INTO raw_artifact_observations (
                raw_artifact_id, ingest_run_id, source_uri, fetched_at
            ) VALUES (
                :raw_artifact_id, :ingest_run_id, 'https://source.test/data',
                statement_timestamp()
            )
            """
        ),
        {"raw_artifact_id": raw_artifact_id, "ingest_run_id": ingest_run_id},
    )
    return security_id, str(raw_artifact_id), str(ingest_run_id)


def insert_daily_price(
    db: Connection,
    security_id: int,
    raw_artifact_id: str,
    ingest_run_id: str,
    *,
    source: str = "official",
) -> dict[str, Any]:
    row = db.execute(
        sa.text(
            """
            INSERT INTO daily_price_versions (
                security_id, source, trade_date, open_price, high_price,
                low_price, close_price, volume, business_content_hash,
                ingested_at, raw_artifact_id, ingest_run_id
            ) VALUES (
                :security_id, :source, DATE '2026-09-10', 100, 110,
                90, 105, 1000, :forged_hash,
                TIMESTAMPTZ '2000-01-01 00:00:00+00',
                :raw_artifact_id, :ingest_run_id
            )
            RETURNING id, business_content_hash, ingested_at
            """
        ),
        {
            "security_id": security_id,
            "source": source,
            "forged_hash": "f" * 64,
            "raw_artifact_id": raw_artifact_id,
            "ingest_run_id": ingest_run_id,
        },
    ).mappings().one()
    return dict(row)


def test_postgresql_18_and_migration_round_trip(engine: Engine) -> None:
    with engine.connect() as connection:
        version_num = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
    assert version_num >= 180000

    config = alembic_config()
    command.downgrade(config, "base")
    try:
        with engine.connect() as connection:
            assert not sa.inspect(connection).has_table("daily_price_versions")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert sa.inspect(connection).has_table("daily_price_versions")
            assert connection.execute(sa.text("SELECT count(*) FROM visible_financial_filings")).scalar_one() == 0
    finally:
        command.upgrade(config, "head")


def test_single_row_versions_have_storage_hash_and_trusted_time(db: Connection) -> None:
    security_id, artifact_id, run_id = seed_lineage(db, "daily_price")
    before = datetime.now(UTC)
    price = insert_daily_price(db, security_id, artifact_id, run_id)
    after = datetime.now(UTC)

    assert price["business_content_hash"] != "f" * 64
    assert len(price["business_content_hash"]) == 64
    assert before <= price["ingested_at"] <= after

    rejected(
        db,
        "UPDATE daily_price_versions SET close_price = 999 WHERE id = :id",
        id=price["id"],
    )
    rejected(db, "DELETE FROM daily_price_versions WHERE id = :id", id=price["id"])
    rejected(db, "TRUNCATE daily_price_versions")

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            insert_daily_price(db, security_id, artifact_id, run_id)


def test_duplicate_fetch_preserves_observation_without_fake_revision(db: Connection) -> None:
    security_id, artifact_id, first_run_id = seed_lineage(db, "daily_price")
    insert_daily_price(db, security_id, artifact_id, first_run_id)
    second_run_id = db.execute(
        sa.text(
            """
            INSERT INTO ingest_runs (dataset_code, source, status, started_at)
            VALUES ('daily_price', 'official', 'running', statement_timestamp())
            RETURNING id
            """
        )
    ).scalar_one()
    db.execute(
        sa.text(
            """
            INSERT INTO raw_artifact_observations (
                raw_artifact_id, ingest_run_id, source_uri, fetched_at
            ) VALUES (:artifact_id, :run_id, 'https://source.test/repeat', statement_timestamp())
            """
        ),
        {"artifact_id": artifact_id, "run_id": second_run_id},
    )

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            insert_daily_price(db, security_id, artifact_id, str(second_run_id))

    assert db.execute(sa.text("SELECT count(*) FROM daily_price_versions")).scalar_one() == 1
    assert db.execute(sa.text("SELECT count(*) FROM raw_artifact_observations")).scalar_one() == 2


def test_evidence_is_append_only_and_does_not_create_business_revision(db: Connection) -> None:
    security_id, artifact_id, run_id = seed_lineage(db, "daily_price")
    price = insert_daily_price(db, security_id, artifact_id, run_id)

    before_evidence = datetime.now(UTC)
    first = db.execute(
        sa.text(
            """
            INSERT INTO publication_evidence (
                dataset_code, source, evidence_kind, published_at, recorded_at,
                evidence_source, evidence_type, quality_rank,
                daily_price_version_id, publication_evidence_hash,
                raw_artifact_id, ingest_run_id
            ) VALUES (
                'daily_price', 'official', 'assertion',
                TIMESTAMPTZ '2026-09-10 06:00:00+00',
                TIMESTAMPTZ '2000-01-01 00:00:00+00',
                'exchange page', 'official', 100, :price_id, :forged_hash,
                :artifact_id, :run_id
            ) RETURNING id, recorded_at, publication_evidence_hash
            """
        ),
        {
            "price_id": price["id"],
            "forged_hash": "e" * 64,
            "artifact_id": artifact_id,
            "run_id": run_id,
        },
    ).mappings().one()
    assert before_evidence <= first["recorded_at"] <= datetime.now(UTC)
    assert first["publication_evidence_hash"] != "e" * 64

    correction_id = db.execute(
        sa.text(
            """
            INSERT INTO publication_evidence (
                dataset_code, source, evidence_kind, published_at,
                evidence_source, evidence_type, quality_rank,
                supersedes_evidence_id, daily_price_version_id,
                raw_artifact_id, ingest_run_id
            ) VALUES (
                'daily_price', 'official', 'correction',
                TIMESTAMPTZ '2026-09-10 05:30:00+00',
                'exchange correction', 'official', 100,
                :supersedes, :price_id, :artifact_id, :run_id
            ) RETURNING id
            """
        ),
        {
            "supersedes": first["id"],
            "price_id": price["id"],
            "artifact_id": artifact_id,
            "run_id": run_id,
        },
    ).scalar_one()
    db.execute(
        sa.text(
            """
            INSERT INTO publication_evidence (
                dataset_code, source, evidence_kind, published_at,
                evidence_source, evidence_type, quality_rank,
                supersedes_evidence_id, daily_price_version_id,
                raw_artifact_id, ingest_run_id
            ) VALUES (
                'daily_price', 'official', 'retraction', NULL,
                'exchange retraction', 'official', 100,
                :supersedes, :price_id, :artifact_id, :run_id
            )
            """
        ),
        {
            "supersedes": correction_id,
            "price_id": price["id"],
            "artifact_id": artifact_id,
            "run_id": run_id,
        },
    )

    assert db.execute(sa.text("SELECT count(*) FROM publication_evidence")).scalar_one() == 3
    assert db.execute(sa.text("SELECT count(*) FROM daily_price_versions")).scalar_one() == 1
    rejected(
        db,
        "UPDATE publication_evidence SET quality_rank = 0 WHERE id = :id",
        id=first["id"],
    )
    rejected(
        db,
        """
        INSERT INTO publication_evidence (
            dataset_code, source, evidence_kind, published_at,
            evidence_source, evidence_type, quality_rank,
            supersedes_evidence_id, daily_price_version_id,
            raw_artifact_id, ingest_run_id
        ) VALUES (
            'daily_price', 'official', 'retraction', NULL,
            'wrong target', 'official', 100, :supersedes, NULL,
            :artifact_id, :run_id
        )
        """,
        supersedes=first["id"], artifact_id=artifact_id, run_id=run_id,
    )


def test_financial_aggregate_visibility_context_and_immutability(db: Connection) -> None:
    security_id, artifact_id, run_id = seed_lineage(
        db, "financial_filing", digest_char="b"
    )
    filing_id = db.execute(
        sa.text(
            """
            INSERT INTO financial_filing_versions (
                security_id, source, filing_key, report_year, report_quarter,
                period_start, period_end, currency, business_content_hash,
                raw_artifact_id, ingest_run_id
            ) VALUES (
                :security_id, 'official', 'filing-2026-q2', 2026, 2,
                DATE '2026-01-01', DATE '2026-06-30', 'TWD', :forged_hash,
                :artifact_id, :run_id
            ) RETURNING id, business_content_hash
            """
        ),
        {
            "security_id": security_id,
            "forged_hash": "f" * 64,
            "artifact_id": artifact_id,
            "run_id": run_id,
        },
    ).mappings().one()
    assert filing_id["business_content_hash"] is None
    assert db.execute(sa.text("SELECT count(*) FROM visible_financial_filings")).scalar_one() == 0

    fact_sql = sa.text(
        """
        INSERT INTO financial_facts (
            filing_version_id, concept_qname, context_hash, entity_identifier,
            period_type, instant_date, explicit_dimensions, typed_dimensions,
            scenario, segment, unit_identity, numeric_value
        ) VALUES (
            :filing_id, :concept, :forged_hash, 'TW-2330', 'instant',
            DATE '2026-06-30', CAST(:dimensions AS jsonb), '{}'::jsonb,
            '{}'::jsonb, '{}'::jsonb, 'TWD', :value
        ) RETURNING id, context_hash
        """
    )
    first_fact = db.execute(
        fact_sql,
        {
            "filing_id": filing_id["id"],
            "concept": "{https://example.test/tifrs}Assets",
            "forged_hash": "0" * 64,
            "dimensions": json.dumps({"axisB": "member2", "axisA": "member1"}),
            "value": Decimal("100"),
        },
    ).mappings().one()
    assert first_fact["context_hash"] != "0" * 64

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(
                fact_sql,
                {
                    "filing_id": filing_id["id"],
                    "concept": "{https://example.test/tifrs}Assets",
                    "forged_hash": "1" * 64,
                    "dimensions": json.dumps({"axisA": "member1", "axisB": "member2"}),
                    "value": Decimal("101"),
                },
            )

    second_fact = db.execute(
        fact_sql,
        {
            "filing_id": filing_id["id"],
            "concept": "{https://example.test/tifrs}Assets",
            "forged_hash": "2" * 64,
            "dimensions": json.dumps({"axisA": "different-member"}),
            "value": Decimal("50"),
        },
    ).mappings().one()
    assert second_fact["context_hash"] != first_fact["context_hash"]

    before = datetime.now(UTC)
    seal = db.execute(
        sa.text(
            """
            INSERT INTO financial_filing_seals (
                filing_version_id, business_content_hash, ingested_at
            ) VALUES (
                :filing_id, :forged_hash, TIMESTAMPTZ '2000-01-01 00:00:00+00'
            ) RETURNING business_content_hash, ingested_at
            """
        ),
        {"filing_id": filing_id["id"], "forged_hash": "f" * 64},
    ).mappings().one()
    after = datetime.now(UTC)
    assert seal["business_content_hash"] != "f" * 64
    assert before <= seal["ingested_at"] <= after
    assert db.execute(sa.text("SELECT count(*) FROM visible_financial_filings")).scalar_one() == 1

    rejected(
        db,
        "UPDATE financial_filing_versions SET currency = 'USD' WHERE id = :id",
        id=filing_id["id"],
    )
    rejected(db, "DELETE FROM financial_filing_versions WHERE id = :id", id=filing_id["id"])
    rejected(
        db,
        "UPDATE financial_facts SET numeric_value = 999 WHERE id = :id",
        id=first_fact["id"],
    )
    rejected(db, "DELETE FROM financial_facts WHERE id = :id", id=first_fact["id"])
    rejected(
        db,
        "UPDATE financial_filing_seals SET business_content_hash = :hash WHERE filing_version_id = :id",
        id=filing_id["id"], hash="4" * 64,
    )
    rejected(
        db,
        "DELETE FROM financial_filing_seals WHERE filing_version_id = :id",
        id=filing_id["id"],
    )
    rejected(
        db,
        """
        INSERT INTO financial_facts (
            filing_version_id, concept_qname, context_hash, entity_identifier,
            period_type, instant_date, unit_identity, numeric_value
        ) VALUES (
            :filing_id, '{https://example.test/tifrs}Liabilities', :hash,
            'TW-2330', 'instant', DATE '2026-06-30', 'TWD', 10
        )
        """,
        filing_id=filing_id["id"], hash="3" * 64,
    )


def test_tdcc_seal_controls_visibility_and_immutability(db: Connection) -> None:
    security_id, artifact_id, run_id = seed_lineage(
        db, "tdcc_snapshot", digest_char="c"
    )
    snapshot_id = db.execute(
        sa.text(
            """
            INSERT INTO tdcc_snapshot_versions (
                security_id, source, snapshot_date, business_content_hash,
                raw_artifact_id, ingest_run_id
            ) VALUES (
                :security_id, 'official', DATE '2026-09-04', :forged_hash,
                :artifact_id, :run_id
            ) RETURNING id, business_content_hash
            """
        ),
        {
            "security_id": security_id,
            "forged_hash": "f" * 64,
            "artifact_id": artifact_id,
            "run_id": run_id,
        },
    ).mappings().one()
    assert snapshot_id["business_content_hash"] is None
    db.execute(
        sa.text(
            """
            INSERT INTO tdcc_distribution (
                snapshot_version_id, bucket_code, holder_count, shares,
                ownership_percent
            ) VALUES (:snapshot_id, '1-999', 10, 1000, 10.5)
            """
        ),
        {"snapshot_id": snapshot_id["id"]},
    )
    assert db.execute(sa.text("SELECT count(*) FROM visible_tdcc_snapshots")).scalar_one() == 0
    before_seal = datetime.now(UTC)
    seal = db.execute(
        sa.text(
            """
            INSERT INTO tdcc_snapshot_seals (
                snapshot_version_id, business_content_hash, ingested_at
            ) VALUES (:snapshot_id, :forged_hash, TIMESTAMPTZ '2000-01-01+00')
            RETURNING business_content_hash, ingested_at
            """
        ),
        {"snapshot_id": snapshot_id["id"], "forged_hash": "f" * 64},
    ).mappings().one()
    assert seal["business_content_hash"] != "f" * 64
    assert before_seal <= seal["ingested_at"] <= datetime.now(UTC)
    assert db.execute(sa.text("SELECT count(*) FROM visible_tdcc_snapshots")).scalar_one() == 1

    rejected(
        db,
        "UPDATE tdcc_snapshot_versions SET snapshot_date = DATE '2026-09-05' WHERE id = :id",
        id=snapshot_id["id"],
    )
    rejected(
        db,
        """
        INSERT INTO tdcc_distribution (
            snapshot_version_id, bucket_code, holder_count, shares,
            ownership_percent
        ) VALUES (:id, '1000+', 2, 500, 5)
        """,
        id=snapshot_id["id"],
    )


def test_source_capabilities_and_lineage_are_isolated(db: Connection) -> None:
    security_id, artifact_id, run_id = seed_lineage(
        db, "daily_price", source="source_a", supports_market_pit=True
    )
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_sources (
                dataset_code, source, supports_market_pit, supports_system_pit,
                publication_time_quality, evidence_status, is_canonical
            ) VALUES ('daily_price', 'source_b', false, true, 0, 'unverified', false)
            """
        )
    )
    capabilities = dict(
        db.execute(
            sa.text(
                """
                SELECT source, supports_market_pit FROM dataset_sources
                WHERE dataset_code = 'daily_price' ORDER BY source
                """
            )
        ).all()
    )
    assert capabilities == {"source_a": True, "source_b": False}

    rejected(
        db,
        """
        INSERT INTO daily_price_versions (
            security_id, source, trade_date, business_content_hash, ingested_at,
            raw_artifact_id, ingest_run_id
        ) VALUES (
            :security_id, 'source_b', DATE '2026-09-10', :hash,
            statement_timestamp(), :artifact_id, :run_id
        )
        """,
        security_id=security_id, hash="f" * 64,
        artifact_id=artifact_id, run_id=run_id,
    )

    _, second_artifact_id, second_run_id = seed_lineage(
        db, "daily_price", source="source_a", digest_char="d"
    )
    rejected(
        db,
        """
        INSERT INTO daily_price_versions (
            security_id, source, trade_date, business_content_hash, ingested_at,
            raw_artifact_id, ingest_run_id
        ) VALUES (
            :security_id, 'source_a', DATE '2026-09-11', :hash,
            statement_timestamp(), :artifact_id, :run_id
        )
        """,
        security_id=security_id, hash="f" * 64,
        artifact_id=artifact_id, run_id=second_run_id,
    )
    assert second_artifact_id != artifact_id


def test_other_single_row_business_hashes_are_storage_generated(db: Connection) -> None:
    security_id, artifact_id, run_id = seed_lineage(
        db, "security_metadata", digest_char="e"
    )
    metadata_hash = db.execute(
        sa.text(
            """
            INSERT INTO security_metadata_versions (
                security_id, source, effective_from, name, industry,
                business_content_hash, ingested_at, raw_artifact_id, ingest_run_id
            ) VALUES (
                :security_id, 'official', DATE '2026-01-01', 'Example Corp',
                'Semiconductor', :hash, TIMESTAMPTZ '2000-01-01+00',
                :artifact_id, :run_id
            ) RETURNING business_content_hash, ingested_at
            """
        ),
        {
            "security_id": security_id, "hash": "f" * 64,
            "artifact_id": artifact_id, "run_id": run_id,
        },
    ).mappings().one()
    assert metadata_hash["business_content_hash"] != "f" * 64
    assert metadata_hash["ingested_at"].year != 2000

    revenue_security_id, revenue_artifact_id, revenue_run_id = seed_lineage(
        db, "monthly_revenue", digest_char="6"
    )
    revenue_hash = db.execute(
        sa.text(
            """
            INSERT INTO monthly_revenue_versions (
                security_id, source, revenue_year, revenue_month, revenue,
                currency, business_content_hash, ingested_at,
                raw_artifact_id, ingest_run_id
            ) VALUES (
                :security_id, 'official', 2026, 8, 123456.78, 'TWD',
                :hash, TIMESTAMPTZ '2000-01-01+00', :artifact_id, :run_id
            ) RETURNING business_content_hash, ingested_at
            """
        ),
        {
            "security_id": revenue_security_id, "hash": "f" * 64,
            "artifact_id": revenue_artifact_id, "run_id": revenue_run_id,
        },
    ).mappings().one()
    assert revenue_hash["business_content_hash"] != "f" * 64
    assert revenue_hash["ingested_at"].year != 2000
