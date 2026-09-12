from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import UTC, datetime
from decimal import Decimal
from threading import Event
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import DBAPIError, IntegrityError

from conftest import alembic_config


pytestmark = pytest.mark.integration


V1_TABLES = {
    "dataset_catalog", "dataset_sources", "security", "ingest_runs",
    "raw_artifacts", "raw_artifact_observations",
    "security_metadata_versions", "daily_price_versions",
    "monthly_revenue_versions", "financial_filing_versions",
    "financial_facts", "quarterly_financial_summary",
    "financial_filing_seals", "tdcc_snapshot_versions",
    "tdcc_distribution", "tdcc_snapshot_seals",
    "institutional_investor_versions", "foreign_holding_versions",
    "institutional_market_summary_versions", "margin_trading_versions",
    "securities_lending_versions", "market_index", "market_index_versions",
    "corporate_action_versions", "official_valuation_versions",
    "security_tag_versions", "xbrl_concept_catalog_versions",
    "derived_dataset_definitions", "derived_computation_runs",
    "derived_metric_versions", "publication_evidence",
}


def rejected(db: Connection, sql: str, **params: Any) -> None:
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(sa.text(sql), params)


def register_tdcc_profile(db: Connection, profile: str, *bucket_codes: str) -> str:
    """Register a test-only TDCC distribution profile of holding buckets."""
    db.execute(
        sa.text(
            "INSERT INTO tdcc_distribution_schemas (distribution_schema, description) "
            "VALUES (:profile, 'test profile')"
        ),
        {"profile": profile},
    )
    for code in bucket_codes:
        db.execute(
            sa.text(
                """
                INSERT INTO tdcc_distribution_schema_buckets (
                    distribution_schema, bucket_code, bucket_role, description
                ) VALUES (:profile, :code, 'holding', 'test bucket')
                """
            ),
            {"profile": profile, "code": code},
        )
    return profile


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
            WITH inserted AS (
                INSERT INTO security (security_code)
                VALUES (:security_code)
                ON CONFLICT (security_code) DO NOTHING
                RETURNING id
            )
            SELECT id FROM inserted
            UNION ALL
            SELECT id FROM security WHERE security_code = :security_code
            LIMIT 1
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
            tables = set(sa.inspect(connection).get_table_names())
            assert V1_TABLES <= tables
            assert connection.execute(sa.text("SELECT count(*) FROM visible_financial_filings")).scalar_one() == 0
    finally:
        command.upgrade(config, "head")


def test_alembic_metadata_has_no_drift() -> None:
    command.check(alembic_config())


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


def test_extended_daily_quote_fields_participate_in_business_hash(db: Connection) -> None:
    security_id, artifact_id, run_id = seed_lineage(db, "daily_price")
    statement = sa.text(
        """
        INSERT INTO daily_price_versions (
            security_id, source, trade_date, open_price, high_price, low_price,
            close_price, volume, trade_value, trade_count, price_change,
            price_direction, bid_snapshot, ask_snapshot, last_bid_price,
            last_ask_price, last_bid_volume, last_ask_volume,
            business_content_hash, ingested_at, raw_artifact_id, ingest_run_id
        ) VALUES (
            :security_id, 'official', DATE '2026-09-10', 100, 110, 90,
            105, 1000, 105000, :trade_count, 5, '+',
            '104.5@10', '105@12', 104.5, 105, 10, 12,
            :hash, TIMESTAMPTZ '2000-01-01+00', :artifact_id, :run_id
        ) RETURNING business_content_hash
        """
    )
    common = {
        "security_id": security_id,
        "hash": "f" * 64,
        "artifact_id": artifact_id,
        "run_id": run_id,
    }
    first = db.execute(statement, {**common, "trade_count": 100}).scalar_one()
    second = db.execute(statement, {**common, "trade_count": 101}).scalar_one()
    assert first != second


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
    register_tdcc_profile(db, "test-one-bucket-v1", "1-999")
    snapshot_id = db.execute(
        sa.text(
            """
            INSERT INTO tdcc_snapshot_versions (
                security_id, source, snapshot_date, business_content_hash,
                raw_artifact_id, ingest_run_id, distribution_schema
            ) VALUES (
                :security_id, 'official', DATE '2026-09-04', :forged_hash,
                :artifact_id, :run_id, 'test-one-bucket-v1'
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
                security_id, source, effective_from, market, name, industry,
                business_content_hash, ingested_at, raw_artifact_id, ingest_run_id
            ) VALUES (
                :security_id, 'official', DATE '2026-01-01', 'TWSE', 'Example Corp',
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


@pytest.mark.parametrize(
    ("table", "dataset", "identity_columns", "identity_values"),
    [
        ("institutional_investor_versions", "institutional_investor", "security_id, source, trade_date", ":security_id, 'official', DATE '2026-09-10'"),
        ("foreign_holding_versions", "foreign_holding", "security_id, source, trade_date", ":security_id, 'official', DATE '2026-09-10'"),
        ("institutional_market_summary_versions", "institutional_market_summary", "market, source, trade_date, institution", "'TWSE', 'official', DATE '2026-09-10', 'foreign'"),
        ("margin_trading_versions", "margin_trading", "security_id, source, trade_date", ":security_id, 'official', DATE '2026-09-10'"),
        ("securities_lending_versions", "securities_lending", "security_id, source, trade_date", ":security_id, 'official', DATE '2026-09-10'"),
        ("corporate_action_versions", "corporate_action", "security_id, source, action_type, ex_date", ":security_id, 'official', 'cash_dividend', DATE '2026-09-10'"),
        ("official_valuation_versions", "official_valuation", "security_id, source, trade_date", ":security_id, 'official', DATE '2026-09-10'"),
        ("security_tag_versions", "security_tag", "security_id, source, tag, effective_from", ":security_id, 'official', 'listed', DATE '2026-01-01'"),
        ("xbrl_concept_catalog_versions", "xbrl_concept_catalog", "source, concept_qname, statement_type", "'official', '{https://example.test/tifrs}Assets', 'balance_sheet'"),
    ],
)
def test_new_observed_domains_enforce_lineage_hash_time_and_immutability(
    db: Connection,
    table: str,
    dataset: str,
    identity_columns: str,
    identity_values: str,
) -> None:
    security_id, artifact_id, run_id = seed_lineage(db, dataset)
    row = db.execute(
        sa.text(
            f"""
            INSERT INTO {table} (
                {identity_columns}, business_content_hash, ingested_at,
                raw_artifact_id, ingest_run_id
            ) VALUES (
                {identity_values}, :forged_hash,
                TIMESTAMPTZ '2000-01-01+00', :artifact_id, :run_id
            ) RETURNING id, business_content_hash, ingested_at
            """
        ),
        {
            "security_id": security_id,
            "forged_hash": "f" * 64,
            "artifact_id": artifact_id,
            "run_id": run_id,
        },
    ).mappings().one()
    assert row["business_content_hash"] != "f" * 64
    assert row["ingested_at"].year != 2000
    rejected(db, f"UPDATE {table} SET business_content_hash = :hash WHERE id = :id", hash="0" * 64, id=row["id"])
    rejected(db, f"DELETE FROM {table} WHERE id = :id", id=row["id"])


def test_market_index_version_and_extended_publication_target(db: Connection) -> None:
    _, artifact_id, run_id = seed_lineage(db, "market_index", digest_char="8")
    index_id = db.execute(
        sa.text("INSERT INTO market_index (index_code, market, name) VALUES ('TAIEX', 'TWSE', 'Taiwan Capitalization Weighted Index') RETURNING id")
    ).scalar_one()
    version = db.execute(
        sa.text(
            """
            INSERT INTO market_index_versions (
                market_index_id, source, trade_date, close_value,
                business_content_hash, ingested_at, raw_artifact_id, ingest_run_id
            ) VALUES (
                :index_id, 'official', DATE '2026-09-10', 25000,
                :hash, TIMESTAMPTZ '2000-01-01+00', :artifact_id, :run_id
            ) RETURNING id, business_content_hash
            """
        ),
        {"index_id": index_id, "hash": "f" * 64, "artifact_id": artifact_id, "run_id": run_id},
    ).mappings().one()
    evidence = db.execute(
        sa.text(
            """
            INSERT INTO publication_evidence (
                dataset_code, source, evidence_kind, published_at,
                evidence_source, evidence_type, quality_rank,
                market_index_version_id, publication_evidence_hash,
                recorded_at, raw_artifact_id, ingest_run_id
            ) VALUES (
                'market_index', 'official', 'assertion',
                TIMESTAMPTZ '2026-09-10 06:00:00+00', 'exchange',
                'official', 100, :version_id, :hash,
                TIMESTAMPTZ '2000-01-01+00', :artifact_id, :run_id
            ) RETURNING recorded_at, publication_evidence_hash
            """
        ),
        {"version_id": version["id"], "hash": "e" * 64, "artifact_id": artifact_id, "run_id": run_id},
    ).mappings().one()
    assert evidence["recorded_at"].year != 2000
    assert evidence["publication_evidence_hash"] != "e" * 64


def test_derived_contract_versions_pit_lineage_and_computed_time(db: Connection) -> None:
    security_id, _, _ = seed_lineage(db, "daily_price", digest_char="9")
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('technical_indicators', 'canonical technical indicators', 'v1')
            """
        )
    )
    definition_sql = sa.text(
        """
        INSERT INTO derived_dataset_definitions (
            dataset_code, derivation_version, storage_strategy,
            formula_specification, implementation_version,
            input_dataset_codes, calendar_timezone, calendar_convention,
            price_adjustment_convention, definition_hash, registered_at
        ) VALUES (
            'technical_indicators', :version, 'materialized', :formula,
            :implementation, '["daily_price"]'::jsonb, 'Asia/Taipei',
            'TWSE sessions', 'unadjusted', :hash, TIMESTAMPTZ '2000-01-01+00'
        ) RETURNING id, definition_hash, registered_at
        """
    )
    v1 = db.execute(definition_sql, {"version": "v1", "formula": "MA20=mean(close[-20:])", "implementation": "commit-a", "hash": "f" * 64}).mappings().one()
    v2 = db.execute(definition_sql, {"version": "v2", "formula": "MA20=adjusted_mean(close[-20:])", "implementation": "commit-b", "hash": "f" * 64}).mappings().one()
    assert v1["definition_hash"] != "f" * 64
    assert v1["definition_hash"] != v2["definition_hash"]
    assert v1["registered_at"].year != 2000

    run_id = db.execute(
        sa.text(
            """
            INSERT INTO derived_computation_runs (
                definition_id, status, implementation_version, started_at
            ) VALUES (:definition_id, 'running', 'commit-a', statement_timestamp())
            RETURNING id
            """
        ),
        {"definition_id": v1["id"]},
    ).scalar_one()
    result = db.execute(
        sa.text(
            """
            INSERT INTO derived_metric_versions (
                definition_id, security_id, observation_date, metric_code,
                pit_mode, information_as_of, knowledge_as_of,
                input_dataset_identity, input_fingerprint, computation_run_id,
                numeric_value, business_content_hash, computed_at
            ) VALUES (
                :definition_id, :security_id, DATE '2026-09-10', 'ma20',
                'market', TIMESTAMPTZ '2026-09-10 08:00:00+00',
                TIMESTAMPTZ '2026-09-10 09:00:00+00',
                '{"daily_price":{"source":"official","through":"2026-09-10"}}'::jsonb,
                :fingerprint, :run_id, 101.25, :hash,
                TIMESTAMPTZ '2000-01-01+00'
            ) RETURNING id, business_content_hash, computed_at
            """
        ),
        {"definition_id": v1["id"], "security_id": security_id, "fingerprint": "a" * 64, "run_id": run_id, "hash": "f" * 64},
    ).mappings().one()
    assert result["business_content_hash"] != "f" * 64
    assert result["computed_at"].year != 2000
    assert "published_at" not in {column["name"] for column in sa.inspect(db).get_columns("derived_metric_versions")}
    rejected(db, "UPDATE derived_metric_versions SET numeric_value = 0 WHERE id = :id", id=result["id"])
    rejected(db, "UPDATE derived_dataset_definitions SET formula_specification = 'changed' WHERE id = :id", id=v1["id"])

    rejected(
        db,
        """
        INSERT INTO derived_metric_versions (
            definition_id, security_id, observation_date, metric_code, pit_mode,
            system_as_of, input_dataset_identity, input_fingerprint,
            computation_run_id, numeric_value, business_content_hash, computed_at
        ) VALUES (
            :wrong_definition, :security_id, DATE '2026-09-10', 'ma20',
            'system', statement_timestamp(), '{}'::jsonb, :fingerprint,
            :run_id, 1, :hash, statement_timestamp()
        )
        """,
        wrong_definition=v2["id"], security_id=security_id,
        fingerprint="b" * 64, run_id=run_id, hash="f" * 64,
    )


def test_financial_seal_serializes_with_concurrent_child_mutation(
    isolated_database_url: str,
) -> None:
    test_engine = sa.create_engine(isolated_database_url, pool_pre_ping=True)
    try:
        with test_engine.begin() as setup:
            security_id, artifact_id, run_id = seed_lineage(
                setup, "financial_filing", digest_char="7"
            )
            first_filing = insert_filing_with_fact(
                setup,
                security_id,
                artifact_id,
                run_id,
                filing_key="concurrency-seal-first",
            )
            second_filing = insert_filing_with_fact(
                setup,
                security_id,
                artifact_id,
                run_id,
                filing_key="concurrency-child-first",
            )

        seal_connection = test_engine.connect()
        seal_transaction = seal_connection.begin()
        first_hash = seal_connection.execute(
            sa.text(
                """
                INSERT INTO financial_filing_seals (
                    filing_version_id, business_content_hash, ingested_at
                ) VALUES (:filing_id, :hash, statement_timestamp())
                RETURNING business_content_hash
                """
            ),
            {"filing_id": first_filing, "hash": "f" * 64},
        ).scalar_one()

        child_started = Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            child_future = executor.submit(
                concurrent_fact_insert,
                test_engine,
                first_filing,
                child_started,
                "{https://example.test/tifrs}Liabilities",
            )
            assert child_started.wait(timeout=2)
            with pytest.raises(FutureTimeout):
                child_future.result(timeout=0.25)
            seal_transaction.commit()
            assert child_future.result(timeout=5) == "55000"
        seal_connection.close()

        with test_engine.connect() as check:
            assert check.execute(
                sa.text(
                    "SELECT count(*) FROM financial_facts WHERE filing_version_id = :id"
                ),
                {"id": first_filing},
            ).scalar_one() == 1

        child_connection = test_engine.connect()
        child_transaction = child_connection.begin()
        insert_extra_fact(
            child_connection,
            second_filing,
            "{https://example.test/tifrs}Liabilities",
        )

        seal_started = Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            seal_future = executor.submit(
                concurrent_seal, test_engine, second_filing, seal_started
            )
            assert seal_started.wait(timeout=2)
            with pytest.raises(FutureTimeout):
                seal_future.result(timeout=0.25)
            child_transaction.commit()
            second_hash = seal_future.result(timeout=5)
        child_connection.close()

        assert second_hash != first_hash
        with test_engine.connect() as check:
            assert check.execute(
                sa.text(
                    "SELECT count(*) FROM financial_facts WHERE filing_version_id = :id"
                ),
                {"id": second_filing},
            ).scalar_one() == 2
            assert check.execute(
                sa.text(
                    """
                    SELECT business_content_hash
                    FROM financial_filing_versions
                    WHERE id = :id
                    """
                ),
                {"id": second_filing},
            ).scalar_one() == second_hash
    finally:
        test_engine.dispose()


def insert_filing_with_fact(
    db: Connection,
    security_id: int,
    artifact_id: str,
    run_id: str,
    *,
    filing_key: str,
) -> int:
    filing_id = db.execute(
        sa.text(
            """
            INSERT INTO financial_filing_versions (
                security_id, source, filing_key, report_year, report_quarter,
                period_start, period_end, currency,
                raw_artifact_id, ingest_run_id
            ) VALUES (
                :security_id, 'official', :filing_key, 2026, 2,
                DATE '2026-01-01', DATE '2026-06-30', 'TWD',
                :artifact_id, :run_id
            ) RETURNING id
            """
        ),
        {
            "security_id": security_id,
            "filing_key": filing_key,
            "artifact_id": artifact_id,
            "run_id": run_id,
        },
    ).scalar_one()
    db.execute(
        sa.text(
            """
            INSERT INTO financial_facts (
                filing_version_id, concept_qname, context_hash,
                entity_identifier, period_type, instant_date,
                unit_identity, numeric_value
            ) VALUES (
                :filing_id, '{https://example.test/tifrs}Assets', :hash,
                'TW-2330', 'instant', DATE '2026-06-30', 'TWD', 100
            )
            """
        ),
        {"filing_id": filing_id, "hash": "0" * 64},
    )
    return filing_id


def insert_extra_fact(db: Connection, filing_id: int, concept: str) -> None:
    db.execute(
        sa.text(
            """
            INSERT INTO financial_facts (
                filing_version_id, concept_qname, context_hash,
                entity_identifier, period_type, instant_date,
                unit_identity, numeric_value
            ) VALUES (
                :filing_id, :concept, :hash, 'TW-2330', 'instant',
                DATE '2026-06-30', 'TWD', 50
            )
            """
        ),
        {"filing_id": filing_id, "concept": concept, "hash": "0" * 64},
    )


def concurrent_fact_insert(
    engine: Engine,
    filing_id: int,
    started: Event,
    concept: str,
) -> str:
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            started.set()
            insert_extra_fact(connection, filing_id, concept)
    except DBAPIError as exc:
        return str(getattr(exc.orig, "sqlstate", "unknown"))
    return "committed"


def concurrent_seal(engine: Engine, filing_id: int, started: Event) -> str:
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
        started.set()
        return connection.execute(
            sa.text(
                """
                INSERT INTO financial_filing_seals (
                    filing_version_id, business_content_hash, ingested_at
                ) VALUES (:filing_id, :hash, statement_timestamp())
                RETURNING business_content_hash
                """
            ),
            {"filing_id": filing_id, "hash": "f" * 64},
        ).scalar_one()


def test_tdcc_seal_serializes_with_concurrent_child_mutation(
    isolated_database_url: str,
) -> None:
    test_engine = sa.create_engine(isolated_database_url, pool_pre_ping=True)
    try:
        with test_engine.begin() as setup:
            security_id, artifact_id, run_id = seed_lineage(
                setup, "tdcc_snapshot", digest_char="8"
            )
            complete_with_one = register_tdcc_profile(
                setup, "test-one-bucket-v1", "1-999"
            )
            # The seal can only pass completeness if it sees the child that
            # commits while it waits for the aggregate lock.
            needs_concurrent_child = register_tdcc_profile(
                setup, "test-two-bucket-v1", "1-999", "1000-5000"
            )
            first_snapshot = insert_snapshot_with_distribution(
                setup,
                security_id,
                artifact_id,
                run_id,
                snapshot_date="2026-09-04",
                distribution_schema=complete_with_one,
            )
            second_snapshot = insert_snapshot_with_distribution(
                setup,
                security_id,
                artifact_id,
                run_id,
                snapshot_date="2026-09-11",
                distribution_schema=needs_concurrent_child,
            )

        seal_connection = test_engine.connect()
        seal_transaction = seal_connection.begin()
        first_hash = seal_connection.execute(
            sa.text(
                """
                INSERT INTO tdcc_snapshot_seals (
                    snapshot_version_id, business_content_hash, ingested_at
                ) VALUES (:snapshot_id, :hash, statement_timestamp())
                RETURNING business_content_hash
                """
            ),
            {"snapshot_id": first_snapshot, "hash": "f" * 64},
        ).scalar_one()

        child_started = Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            child_future = executor.submit(
                concurrent_distribution_insert,
                test_engine,
                first_snapshot,
                child_started,
                "1000-5000",
            )
            assert child_started.wait(timeout=2)
            with pytest.raises(FutureTimeout):
                child_future.result(timeout=0.25)
            seal_transaction.commit()
            assert child_future.result(timeout=5) == "55000"
        seal_connection.close()

        with test_engine.connect() as check:
            assert check.execute(
                sa.text(
                    "SELECT count(*) FROM tdcc_distribution WHERE snapshot_version_id = :id"
                ),
                {"id": first_snapshot},
            ).scalar_one() == 1

        child_connection = test_engine.connect()
        child_transaction = child_connection.begin()
        insert_extra_distribution(
            child_connection, second_snapshot, "1000-5000"
        )

        seal_started = Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            seal_future = executor.submit(
                concurrent_tdcc_seal, test_engine, second_snapshot, seal_started
            )
            assert seal_started.wait(timeout=2)
            with pytest.raises(FutureTimeout):
                seal_future.result(timeout=0.25)
            child_transaction.commit()
            second_hash = seal_future.result(timeout=5)
        child_connection.close()

        assert second_hash != first_hash
        with test_engine.connect() as check:
            assert check.execute(
                sa.text(
                    "SELECT count(*) FROM tdcc_distribution WHERE snapshot_version_id = :id"
                ),
                {"id": second_snapshot},
            ).scalar_one() == 2
            assert check.execute(
                sa.text(
                    """
                    SELECT business_content_hash
                    FROM tdcc_snapshot_versions
                    WHERE id = :id
                    """
                ),
                {"id": second_snapshot},
            ).scalar_one() == second_hash
    finally:
        test_engine.dispose()


def insert_snapshot_with_distribution(
    db: Connection,
    security_id: int,
    artifact_id: str,
    run_id: str,
    *,
    snapshot_date: str,
    distribution_schema: str,
) -> int:
    snapshot_id = db.execute(
        sa.text(
            """
            INSERT INTO tdcc_snapshot_versions (
                security_id, source, snapshot_date,
                raw_artifact_id, ingest_run_id, distribution_schema
            ) VALUES (
                :security_id, 'official', CAST(:snapshot_date AS date),
                :artifact_id, :run_id, :distribution_schema
            ) RETURNING id
            """
        ),
        {
            "security_id": security_id,
            "snapshot_date": snapshot_date,
            "distribution_schema": distribution_schema,
            "artifact_id": artifact_id,
            "run_id": run_id,
        },
    ).scalar_one()
    db.execute(
        sa.text(
            """
            INSERT INTO tdcc_distribution (
                snapshot_version_id, bucket_code, holder_count,
                shares, ownership_percent
            ) VALUES (:snapshot_id, '1-999', 10, 1000, 10.5)
            """
        ),
        {"snapshot_id": snapshot_id},
    )
    return snapshot_id


def insert_extra_distribution(
    db: Connection, snapshot_id: int, bucket_code: str
) -> None:
    db.execute(
        sa.text(
            """
            INSERT INTO tdcc_distribution (
                snapshot_version_id, bucket_code, holder_count,
                shares, ownership_percent
            ) VALUES (:snapshot_id, :bucket_code, 2, 500, 5)
            """
        ),
        {"snapshot_id": snapshot_id, "bucket_code": bucket_code},
    )


def concurrent_distribution_insert(
    engine: Engine,
    snapshot_id: int,
    started: Event,
    bucket_code: str,
) -> str:
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            started.set()
            insert_extra_distribution(connection, snapshot_id, bucket_code)
    except DBAPIError as exc:
        return str(getattr(exc.orig, "sqlstate", "unknown"))
    return "committed"


def concurrent_tdcc_seal(
    engine: Engine, snapshot_id: int, started: Event
) -> str:
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
        started.set()
        return connection.execute(
            sa.text(
                """
                INSERT INTO tdcc_snapshot_seals (
                    snapshot_version_id, business_content_hash, ingested_at
                ) VALUES (:snapshot_id, :hash, statement_timestamp())
                RETURNING business_content_hash
                """
            ),
            {"snapshot_id": snapshot_id, "hash": "f" * 64},
        ).scalar_one()
