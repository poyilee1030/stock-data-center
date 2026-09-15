"""PR #16 — storage contract for the observed trading calendar.

The published artifact is one month, and the business content of that month is
the set of days the market actually opened. A closure is therefore an absence
inside a version, and a corrected closure is a new version — not an update.
"""

from __future__ import annotations

from datetime import date
from itertools import count
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError


pytestmark = pytest.mark.integration

_DIGESTS = count(0x16000)


JULY_2024 = [
    date(2024, 7, day)
    for day in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19, 22, 23, 26, 29, 30, 31)
]


def rejected(db: Connection, sql: str, **params: Any) -> None:
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(sa.text(sql), params)


def configure(db: Connection, dataset: str, source: str) -> None:
    db.execute(
        sa.text(
            "INSERT INTO dataset_catalog (dataset_code, description, schema_version) "
            "VALUES (:dataset, :description, 'v1') ON CONFLICT (dataset_code) DO NOTHING"
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
            VALUES (:dataset, :source, true, true, 100, 'verified',
                    ARRAY['official'], false)
            ON CONFLICT (dataset_code, source) DO NOTHING
            """
        ),
        {"dataset": dataset, "source": source},
    )


def lineage(
    db: Connection, dataset: str = "trading_calendar", source: str = "twse"
) -> dict[str, Any]:
    """Create one ingest run and raw-artifact observation for that source."""
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
    digest = f"{next(_DIGESTS):064x}"
    artifact_id = db.execute(
        sa.text(
            """
            INSERT INTO raw_artifacts
                (raw_artifact_hash, storage_uri, byte_size, media_type)
            VALUES (:digest, :uri, 2257, 'application/json')
            RETURNING id
            """
        ),
        {"digest": digest, "uri": f"data/raw/{digest[:2]}/{digest}"},
    ).scalar_one()
    db.execute(
        sa.text(
            """
            INSERT INTO raw_artifact_observations
                (raw_artifact_id, ingest_run_id, source_uri, fetched_at)
            VALUES (:artifact, :run, :uri, statement_timestamp())
            """
        ),
        {
            "artifact": artifact_id,
            "run": run_id,
            "uri": "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK",
        },
    )
    return {"artifact": artifact_id, "run": run_id}


def insert_month(
    db: Connection,
    *,
    month: date = date(2024, 7, 1),
    days: list[date] | None = None,
    coverage_through: date | None = None,
    market: str = "TWSE",
    source: str = "twse",
) -> int:
    refs = lineage(db)
    days = JULY_2024 if days is None else days
    return db.execute(
        sa.text(
            """
            INSERT INTO trading_calendar_versions
                (market, source, calendar_month, trading_days, coverage_through,
                 raw_artifact_id, ingest_run_id)
            VALUES (:market, :source, :month, :days, :through, :artifact, :run)
            RETURNING id
            """
        ).bindparams(sa.bindparam("days", type_=sa.ARRAY(sa.Date()))),
        {
            "market": market,
            "source": source,
            "month": month,
            "days": days,
            "through": coverage_through or date(2024, 7, 31),
            **refs,
        },
    ).scalar_one()


def test_a_month_of_trading_days_is_stored_as_one_observed_version(
    db: Connection,
) -> None:
    version_id = insert_month(db)

    row = db.execute(
        sa.text(
            "SELECT market, source, calendar_month, trading_days, coverage_through, "
            "business_content_hash, ingested_at "
            "FROM trading_calendar_versions WHERE id = :id"
        ),
        {"id": version_id},
    ).mappings().one()

    assert row["market"] == "TWSE"
    assert row["calendar_month"] == date(2024, 7, 1)
    assert row["trading_days"] == JULY_2024
    assert row["coverage_through"] == date(2024, 7, 31)
    assert row["ingested_at"] is not None
    assert len(row["business_content_hash"]) == 64


def test_an_absent_day_is_the_closure_and_changes_the_business_hash(
    db: Connection,
) -> None:
    """Adding back a closed day is a different month, so a different revision."""
    closed = insert_month(db)
    corrected_days = sorted(JULY_2024 + [date(2024, 7, 24)])
    corrected = insert_month(db, days=corrected_days)

    hashes = db.execute(
        sa.text(
            "SELECT id, business_content_hash FROM trading_calendar_versions "
            "WHERE id IN (:a, :b) ORDER BY id"
        ),
        {"a": closed, "b": corrected},
    ).mappings().all()

    assert hashes[0]["business_content_hash"] != hashes[1]["business_content_hash"]


def test_an_identical_month_cannot_be_stored_twice_as_a_revision(
    db: Connection,
) -> None:
    insert_month(db)

    with pytest.raises(DBAPIError):
        with db.begin_nested():
            insert_month(db)


def test_storage_rejects_a_month_that_is_not_a_first_day(db: Connection) -> None:
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            insert_month(db, month=date(2024, 7, 2))


def test_storage_rejects_a_day_outside_its_month(db: Connection) -> None:
    """A day before the month is the case the coverage bound cannot also catch."""
    with pytest.raises(DBAPIError) as before:
        with db.begin_nested():
            insert_month(db, days=[date(2024, 6, 28), *JULY_2024])
    assert "trading_days_inside_month" in str(before.value)

    with pytest.raises(DBAPIError):
        with db.begin_nested():
            insert_month(db, days=sorted(JULY_2024 + [date(2024, 8, 1)]))


def test_storage_rejects_an_unsorted_or_duplicated_day_list(db: Connection) -> None:
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            insert_month(db, days=list(reversed(JULY_2024)))
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            insert_month(db, days=sorted(JULY_2024 + [date(2024, 7, 23)]))


def test_storage_rejects_an_empty_month(db: Connection) -> None:
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            insert_month(db, days=[])


def test_coverage_through_must_cover_every_published_day(db: Connection) -> None:
    """An incomplete month may not claim the rest of the month was closed."""
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            insert_month(db, coverage_through=date(2024, 7, 20))

    partial = insert_month(
        db,
        days=[d for d in JULY_2024 if d <= date(2024, 7, 11)],
        coverage_through=date(2024, 7, 11),
    )
    assert partial


def test_coverage_through_must_stay_inside_its_month(db: Connection) -> None:
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            insert_month(db, coverage_through=date(2024, 8, 1))


def test_a_calendar_version_requires_matching_ingest_lineage(db: Connection) -> None:
    """Lineage from another dataset's run may not carry a calendar version."""
    configure(db, "daily_price", "twse")
    refs = lineage(db, dataset="daily_price", source="twse")

    rejected(
        db,
        """
        INSERT INTO trading_calendar_versions
            (market, source, calendar_month, trading_days, coverage_through,
             raw_artifact_id, ingest_run_id)
        VALUES ('TWSE', 'twse', DATE '2024-07-01',
                ARRAY[DATE '2024-07-01'], DATE '2024-07-31', :artifact::uuid, :run::uuid)
        """,
        artifact=refs["artifact"],
        run=refs["run"],
    )


def test_publication_evidence_can_target_a_calendar_version(db: Connection) -> None:
    version_id = insert_month(db)
    refs = lineage(db)

    evidence_id = db.execute(
        sa.text(
            """
            INSERT INTO publication_evidence
                (dataset_code, source, evidence_kind, published_at, recorded_at,
                 evidence_source, evidence_type, quality_rank,
                 trading_calendar_version_id, publication_evidence_hash,
                 raw_artifact_id, ingest_run_id)
            VALUES ('trading_calendar', 'twse', 'unknown', NULL,
                    statement_timestamp(), 'twse FMTQIK', 'official', 0,
                    :version, :hash, :artifact, :run)
            RETURNING id
            """
        ),
        {"version": version_id, "hash": "e" * 64, **refs},
    ).scalar_one()

    assert evidence_id
