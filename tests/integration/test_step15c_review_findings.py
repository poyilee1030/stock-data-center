"""Step 15-c — regressions for the code-review findings."""

from __future__ import annotations

from datetime import UTC, date, datetime
from itertools import count

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.evidence import (
    EvidencePolicyService,
    UnacceptedEvidenceTypeError,
)
from stock_data_center.provenance import ArtifactOrigin, IngestPurpose


pytestmark = pytest.mark.integration

_DIGESTS = count(0x86000)
POLICY = EvidencePolicyService()

TRADE_DATE = date(2024, 7, 23)
RULE_INSTANT = datetime(2024, 7, 23, 19, 0, tzinfo=UTC)  # 07-24 03:00 Taipei
AFTER_THE_RULE = datetime(2024, 7, 24, 1, 0, tzinfo=UTC)


def source_row(
    db: Connection,
    *,
    dataset: str,
    source: str,
    accepted: tuple[str, ...],
) -> None:
    db.execute(
        sa.text(
            "INSERT INTO dataset_catalog (dataset_code, description, schema_version) "
            "VALUES (:dataset, :description, 'v1') "
            "ON CONFLICT (dataset_code) DO NOTHING"
        ),
        {"dataset": dataset, "description": dataset},
    )
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_sources
                (dataset_code, source, supports_market_pit, supports_system_pit,
                 publication_time_quality, evidence_status, accepted_evidence_types,
                 is_canonical)
            VALUES (:dataset, :source, true, true, 0, 'verified', :accepted, false)
            ON CONFLICT (dataset_code, source) DO UPDATE
               SET accepted_evidence_types = EXCLUDED.accepted_evidence_types
            """
        ),
        {"dataset": dataset, "source": source, "accepted": list(accepted)},
    )


def lineage(db: Connection, *, purpose: str, source: str = "twse"):
    run_id = db.execute(
        sa.text(
            "INSERT INTO ingest_runs (dataset_code, source, status, started_at, purpose) "
            "VALUES ('daily_price', :source, 'succeeded', statement_timestamp(), "
            ":purpose) RETURNING id"
        ),
        {"purpose": purpose, "source": source},
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
    db.execute(
        sa.text(
            "INSERT INTO raw_artifact_observations "
            "(raw_artifact_id, ingest_run_id, source_uri, fetched_at) "
            "VALUES (:artifact, :run, 'https://twse.test', :fetched)"
        ),
        {
            "artifact": artifact_id,
            "run": run_id,
            "fetched": AFTER_THE_RULE,
        },
    )
    return artifact_id, run_id


def price_version(db: Connection, artifact_id, run_id, *, code: str = "2330") -> int:
    security_id = db.execute(
        sa.text("INSERT INTO security (security_code) VALUES (:code) RETURNING id"),
        {"code": code},
    ).scalar_one()
    return db.execute(
        sa.text(
            """
            INSERT INTO daily_price_versions
                (security_id, source, trade_date, open_price, high_price,
                 low_price, close_price, volume, raw_artifact_id, ingest_run_id)
            VALUES (:security, 'twse', :day, 100, 100, 100, 100, 1000,
                    CAST(:artifact AS uuid), CAST(:run AS uuid))
            RETURNING id
            """
        ),
        {
            "security": security_id,
            "day": TRADE_DATE,
            "artifact": artifact_id,
            "run": run_id,
        },
    ).scalar_one()


def store_capture(db: Connection, version_id: int, artifact_id, run_id) -> None:
    db.execute(
        sa.text(
            """
            INSERT INTO publication_evidence
                (dataset_code, source, evidence_kind, published_at, recorded_at,
                 evidence_source, evidence_type, quality_rank,
                 daily_price_version_id, publication_evidence_hash,
                 raw_artifact_id, ingest_run_id)
            VALUES ('daily_price', 'twse', 'assertion', :published_at,
                    statement_timestamp(), 'data center first capture',
                    'capture_bound', 80, :version, :hash,
                    CAST(:artifact AS uuid), CAST(:run AS uuid))
            """
        ),
        {
            "published_at": AFTER_THE_RULE,
            "version": version_id,
            "hash": f"{next(_DIGESTS):064x}",
            "artifact": artifact_id,
            "run": run_id,
        },
    )


def test_a_reimport_does_not_resurrect_a_falsified_rule(db: Connection) -> None:
    """Finding 2. Append-only storage cannot take back a rule written by mistake.

    The first import captured after 03:00, so the rule was falsified and never
    written. A later run sees `version_created == False` and used to conclude
    nothing was falsified, appending the very rule the first run withheld.
    """
    artifact_id, run_id = lineage(db, purpose="first_capture")
    version_id = price_version(db, artifact_id, run_id)
    store_capture(db, version_id, artifact_id, run_id)

    planned = POLICY.plan(
        db,
        dataset_code="daily_price",
        source="twse",
        period=TRADE_DATE,
        purpose=IngestPurpose.GAP_FILL,
        version_created=False,
        captured_at=AFTER_THE_RULE,
        version_id=version_id,
    )

    assert planned == ()


def test_a_version_with_no_stored_capture_still_gets_its_rule(
    db: Connection,
) -> None:
    artifact_id, run_id = lineage(db, purpose="gap_fill")
    version_id = price_version(db, artifact_id, run_id, code="2317")

    planned = POLICY.plan(
        db,
        dataset_code="daily_price",
        source="twse",
        period=TRADE_DATE,
        purpose=IngestPurpose.GAP_FILL,
        version_created=True,
        captured_at=AFTER_THE_RULE,
        version_id=version_id,
    )

    assert tuple(item.evidence_type for item in planned) == ("release_rule",)
    assert planned[0].published_at == RULE_INSTANT


def test_a_source_that_declared_no_rule_degrades_instead_of_aborting(
    db: Connection,
) -> None:
    """Finding 3. "Nothing is enabled by default" must not mean "import fails"."""
    source_row(
        db, dataset="daily_price", source="future_source", accepted=("official",)
    )

    planned = POLICY.plan(
        db,
        dataset_code="daily_price",
        source="future_source",
        period=TRADE_DATE,
        purpose=IngestPurpose.FIRST_CAPTURE,
        version_created=True,
        captured_at=AFTER_THE_RULE,
        version_id=None,
    )

    assert tuple(item.evidence_type for item in planned) == ("official",)
    assert planned[0].published_at is None


def test_a_source_that_declared_a_rule_but_not_its_types_still_fails_loudly(
    db: Connection,
) -> None:
    """A half-configured opt-in is a mistake, not a default."""
    source_row(
        db, dataset="daily_price", source="half_configured", accepted=("official",)
    )
    db.execute(
        sa.text(
            "INSERT INTO dataset_release_rules "
            "(dataset_code, source, rule_id, version, note) "
            "VALUES ('daily_price', 'half_configured', 'exchange_daily_settled', 1, '')"
        )
    )

    with pytest.raises(UnacceptedEvidenceTypeError):
        POLICY.plan(
            db,
            dataset_code="daily_price",
            source="half_configured",
            period=TRADE_DATE,
            purpose=IngestPurpose.FIRST_CAPTURE,
            version_created=True,
            captured_at=AFTER_THE_RULE,
            version_id=None,
        )


def test_the_provenance_vocabulary_is_one_set_of_classes() -> None:
    """Finding 4. Two ArtifactOrigin classes made isinstance silently false."""
    from stock_data_center.ingestion import models

    assert models.ArtifactOrigin is ArtifactOrigin
    assert isinstance(models.ArtifactOrigin.LEGACY_ARCHIVE, ArtifactOrigin)
    assert models.IngestPurpose is IngestPurpose
