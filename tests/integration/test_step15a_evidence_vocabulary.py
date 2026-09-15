"""Step 15-a — the availability-time evidence vocabulary (ADR-0020).

The four new evidence types exist, their ranking is a storage invariant rather
than caller discipline, and every ingest declares why it fetched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError


pytestmark = pytest.mark.integration

_DIGESTS = count(0x56000)

# ADR-0020 §1. The ordering is the decision; the numbers only express it.
ADR_0020_RANKS = {
    "official": 90,
    "capture_bound": 80,
    "legacy_capture_bound": 70,
    "press_report_bound": 60,
    "release_rule": 40,
}


def rejected(db: Connection, sql: str, **params: Any) -> None:
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(sa.text(sql), params)


def lineage(db: Connection, *, purpose: str | None = None, origin: str | None = None):
    columns = ["dataset_code", "source", "status", "started_at"]
    values = ["'security_metadata'", "'twse'", "'succeeded'", "statement_timestamp()"]
    if purpose is not None:
        columns.append("purpose")
        values.append(f"'{purpose}'")
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('security_metadata', 'metadata', 'v1')
            ON CONFLICT (dataset_code) DO NOTHING
            """
        )
    )
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_sources
                (dataset_code, source, supports_market_pit, supports_system_pit,
                 publication_time_quality, evidence_status, accepted_evidence_types,
                 is_canonical)
            VALUES ('security_metadata', 'twse', true, true, 100, 'verified',
                    ARRAY['official', 'capture_bound', 'release_rule'], true)
            ON CONFLICT (dataset_code, source) DO NOTHING
            """
        )
    )
    run_id = db.execute(
        sa.text(
            f"INSERT INTO ingest_runs ({', '.join(columns)}) "
            f"VALUES ({', '.join(values)}) RETURNING id"
        )
    ).scalar_one()
    digest = f"{next(_DIGESTS):064x}"
    artifact_id = db.execute(
        sa.text(
            "INSERT INTO raw_artifacts "
            "(raw_artifact_hash, storage_uri, byte_size, media_type) "
            "VALUES (:digest, :uri, 1, 'application/json') RETURNING id"
        ),
        {"digest": digest, "uri": f"data/raw/{digest[:2]}/{digest}"},
    ).scalar_one()
    observation_columns = [
        "raw_artifact_id", "ingest_run_id", "source_uri", "fetched_at"
    ]
    observation_values = [":artifact", ":run", "'https://twse.test'", "statement_timestamp()"]
    if origin is not None:
        observation_columns.append("artifact_origin")
        observation_values.append(f"'{origin}'")
    db.execute(
        sa.text(
            f"INSERT INTO raw_artifact_observations "
            f"({', '.join(observation_columns)}) "
            f"VALUES ({', '.join(observation_values)})"
        ),
        {"artifact": artifact_id, "run": run_id},
    )
    return artifact_id, run_id


def version(db: Connection, artifact_id, run_id) -> int:
    security_id = db.execute(
        sa.text("INSERT INTO security (security_code) VALUES ('2330') RETURNING id")
    ).scalar_one()
    return db.execute(
        sa.text(
            """
            INSERT INTO security_metadata_versions
                (security_id, source, effective_from, market, name,
                 raw_artifact_id, ingest_run_id)
            VALUES (:security, 'twse', DATE '2024-07-01', 'TWSE', '台積電',
                    CAST(:artifact AS uuid), CAST(:run AS uuid))
            RETURNING id
            """
        ),
        {"security": security_id, "artifact": artifact_id, "run": run_id},
    ).scalar_one()


def evidence(
    db: Connection,
    version_id: int,
    artifact_id,
    run_id,
    *,
    evidence_type: str,
    quality_rank: int,
    kind: str = "assertion",
    published_at: datetime | None = datetime(2024, 7, 2, 1, 0, tzinfo=UTC),
) -> int:
    return db.execute(
        sa.text(
            """
            INSERT INTO publication_evidence
                (dataset_code, source, evidence_kind, published_at, recorded_at,
                 evidence_source, evidence_type, quality_rank,
                 security_metadata_version_id, publication_evidence_hash,
                 raw_artifact_id, ingest_run_id)
            VALUES ('security_metadata', 'twse', :kind, :published_at,
                    statement_timestamp(), 'test', :evidence_type, :rank,
                    :version, :hash, CAST(:artifact AS uuid), CAST(:run AS uuid))
            RETURNING id
            """
        ),
        {
            "kind": kind,
            "published_at": None if kind in {"unknown", "retraction"} else published_at,
            "evidence_type": evidence_type,
            "rank": quality_rank,
            "version": version_id,
            "hash": f"{next(_DIGESTS):064x}",
            "artifact": artifact_id,
            "run": run_id,
        },
    ).scalar_one()


def test_adr_0020_evidence_types_are_registered_with_their_ranks(
    db: Connection,
) -> None:
    rows = dict(
        db.execute(
            sa.text("SELECT evidence_type, quality_rank FROM evidence_types")
        ).all()
    )

    assert rows == ADR_0020_RANKS
    pinned = set(
        db.execute(
            sa.text(
                "SELECT evidence_type FROM evidence_types WHERE rank_is_enforced"
            )
        ).scalars()
    )
    # `official` predates ADR-0020 and is left unpinned; the four types the ADR
    # introduces are what a forged rank could abuse.
    assert pinned == set(ADR_0020_RANKS) - {"official"}
    order = [name for name, _ in sorted(rows.items(), key=lambda kv: -kv[1])]
    assert order == [
        "official",
        "capture_bound",
        "legacy_capture_bound",
        "press_report_bound",
        "release_rule",
    ]


def test_every_registered_type_documents_its_authority(db: Connection) -> None:
    empty = db.execute(
        sa.text(
            "SELECT count(*) FROM evidence_types "
            "WHERE description IS NULL OR btrim(description) = ''"
        )
    ).scalar_one()
    assert empty == 0


def test_a_registered_type_cannot_be_written_with_a_forged_rank(
    db: Connection,
) -> None:
    """Ranking is a storage invariant, not caller discipline."""
    artifact_id, run_id = lineage(db)
    version_id = version(db, artifact_id, run_id)

    with pytest.raises(DBAPIError):
        with db.begin_nested():
            evidence(
                db, version_id, artifact_id, run_id,
                evidence_type="release_rule", quality_rank=95,
            )

    accepted = evidence(
        db, version_id, artifact_id, run_id,
        evidence_type="release_rule", quality_rank=40,
    )
    assert accepted


def test_non_affirmative_evidence_of_a_pinned_type_keeps_rank_zero(
    db: Connection,
) -> None:
    """An `unknown` head must never outrank a real assertion."""
    artifact_id, run_id = lineage(db)
    version_id = version(db, artifact_id, run_id)

    with pytest.raises(DBAPIError):
        with db.begin_nested():
            evidence(
                db, version_id, artifact_id, run_id,
                evidence_type="release_rule", quality_rank=40, kind="unknown",
            )

    accepted = evidence(
        db, version_id, artifact_id, run_id,
        evidence_type="release_rule", quality_rank=0, kind="unknown",
    )
    assert accepted


def test_the_legacy_official_type_keeps_the_ranks_existing_rows_use(
    db: Connection,
) -> None:
    """Pinning `official` would break every pre-ADR-0020 row and fixture."""
    artifact_id, run_id = lineage(db)
    version_id = version(db, artifact_id, run_id)

    assert evidence(
        db, version_id, artifact_id, run_id,
        evidence_type="official", quality_rank=100,
    )


def test_an_unregistered_type_still_obeys_its_sources_allowlist(
    db: Connection,
) -> None:
    """ADR-0010 is unchanged: registration is about ranking, not permission."""
    artifact_id, run_id = lineage(db)
    version_id = version(db, artifact_id, run_id)

    accepted = evidence(
        db, version_id, artifact_id, run_id,
        evidence_type="estimated", quality_rank=55,
    )
    assert accepted


def test_an_ingest_run_declares_why_it_fetched(db: Connection) -> None:
    for purpose in ("first_capture", "gap_fill", "correction_check", "unspecified"):
        artifact_id, run_id = lineage(db, purpose=purpose)
        stored = db.execute(
            sa.text("SELECT purpose FROM ingest_runs WHERE id = :id"), {"id": run_id}
        ).scalar_one()
        assert stored == purpose


def test_an_ingest_run_without_a_declared_purpose_is_unspecified(
    db: Connection,
) -> None:
    """Pre-policy runs must not silently claim to be first captures."""
    _, run_id = lineage(db)
    stored = db.execute(
        sa.text("SELECT purpose FROM ingest_runs WHERE id = :id"), {"id": run_id}
    ).scalar_one()
    assert stored == "unspecified"


def test_an_invented_purpose_is_rejected(db: Connection) -> None:
    rejected(
        db,
        "INSERT INTO ingest_runs (dataset_code, source, status, started_at, purpose) "
        "VALUES ('security_metadata', 'twse', 'succeeded', statement_timestamp(), "
        "'whenever')",
    )


def test_an_artifact_observation_records_how_the_bytes_were_obtained(
    db: Connection,
) -> None:
    """ROADMAP §14 names two origins; neither was modelled before."""
    for origin in ("official_fetch", "legacy_archive"):
        artifact_id, run_id = lineage(db, origin=origin)
        stored = db.execute(
            sa.text(
                "SELECT artifact_origin FROM raw_artifact_observations "
                "WHERE raw_artifact_id = :artifact AND ingest_run_id = :run"
            ),
            {"artifact": artifact_id, "run": run_id},
        ).scalar_one()
        assert stored == origin


def test_an_invented_artifact_origin_is_rejected(db: Connection) -> None:
    artifact_id, run_id = lineage(db)
    rejected(
        db,
        """
        INSERT INTO raw_artifact_observations
            (raw_artifact_id, ingest_run_id, source_uri, fetched_at, artifact_origin)
        VALUES (CAST(:artifact AS uuid), CAST(:run AS uuid), 'https://x.test',
                statement_timestamp(), 'scraped_from_a_blog')
        """,
        artifact=artifact_id,
        run=run_id,
    )


def test_an_import_records_its_declared_purpose_and_artifact_origin(
    isolated_database_url: str, tmp_path,
) -> None:
    """The lifecycle writes what the caller declared, not a guess."""
    from datetime import date
    from uuid import uuid4

    from stock_data_center.ingestion.adapters import TWSETradingCalendarAdapter
    from stock_data_center.ingestion.models import (
        ArtifactOrigin,
        FetchedArtifact,
        IngestPurpose,
        TradingCalendarRequest,
    )
    from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
    from stock_data_center.ingestion.trading_calendar import TradingCalendarImporter

    payload = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "fixtures" / "twse_fmtqik_202407.json"
    ).read_bytes()

    class Fetcher:
        def fetch(self, resource):
            return FetchedArtifact(
                content=payload,
                source_uri=resource.source_uri,
                fetched_at=datetime.now(UTC),
                media_type="application/json",
            )

    engine = sa.create_engine(isolated_database_url)
    try:
        TradingCalendarImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=Fetcher(),
            today=date(2026, 9, 15),
        ).run(
            adapter=TWSETradingCalendarAdapter(),
            request=TradingCalendarRequest(date(2024, 7, 1)),
            import_id=uuid4(),
            purpose=IngestPurpose.GAP_FILL,
            artifact_origin=ArtifactOrigin.LEGACY_ARCHIVE,
        )

        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT r.purpose, o.artifact_origin "
                    "FROM ingest_runs r "
                    "JOIN raw_artifact_observations o ON o.ingest_run_id = r.id "
                    "WHERE r.dataset_code = 'trading_calendar'"
                )
            ).mappings().one()
        assert row["purpose"] == "gap_fill"
        assert row["artifact_origin"] == "legacy_archive"
    finally:
        engine.dispose()


def test_an_import_that_declares_nothing_is_not_called_a_first_capture(
    isolated_database_url: str, tmp_path,
) -> None:
    """A default is an inference, and ADR-0020 forbids inferring the purpose.

    Silently calling an undeclared run a first capture would let a 2026 re-fetch
    of 2024 history claim a capture bound at the 2026 instant — the exact
    failure the ADR names.
    """
    from datetime import date
    from uuid import uuid4

    from stock_data_center.ingestion.adapters import TWSETradingCalendarAdapter
    from stock_data_center.ingestion.models import (
        FetchedArtifact,
        TradingCalendarRequest,
    )
    from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
    from stock_data_center.ingestion.trading_calendar import TradingCalendarImporter

    payload = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "fixtures" / "twse_fmtqik_202407.json"
    ).read_bytes()

    class Fetcher:
        def fetch(self, resource):
            return FetchedArtifact(
                content=payload,
                source_uri=resource.source_uri,
                fetched_at=datetime.now(UTC),
                media_type="application/json",
            )

    engine = sa.create_engine(isolated_database_url)
    try:
        TradingCalendarImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=Fetcher(),
            today=date(2026, 9, 15),
        ).run(
            adapter=TWSETradingCalendarAdapter(),
            request=TradingCalendarRequest(date(2024, 7, 1)),
            import_id=uuid4(),
        )

        with engine.connect() as connection:
            purpose = connection.scalar(
                sa.text(
                    "SELECT purpose FROM ingest_runs "
                    "WHERE dataset_code = 'trading_calendar'"
                )
            )
        assert purpose == "unspecified"
    finally:
        engine.dispose()


def test_downgrade_refuses_to_erase_declared_ingest_provenance(
    isolated_database_url: str,
) -> None:
    """Purpose and origin are declarations; nothing can recompute them."""
    from alembic import command
    from sqlalchemy.exc import DBAPIError

    from conftest import alembic_config, alembic_head

    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            with connection.begin():
                lineage(connection, purpose="gap_fill", origin="legacy_archive")

        with pytest.raises(DBAPIError) as blocked:
            command.downgrade(alembic_config(isolated_database_url), "1a6f3b7c8d24")
        assert blocked.value.orig.sqlstate == "P0001"
        assert "cannot be reconstructed once dropped" in str(blocked.value)

        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == alembic_head()
            assert connection.scalar(
                sa.text("SELECT purpose FROM ingest_runs LIMIT 1")
            ) == "gap_fill"
    finally:
        engine.dispose()
