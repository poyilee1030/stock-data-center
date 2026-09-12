from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from conftest import alembic_config
from stock_data_center.financials import (
    FilingLineageRef,
    FilingPeriod,
    FinancialFactObservation,
    FinancialFilingObservation,
    FinancialFilingService,
    FinancialFilingWriter,
    FinancialPublication,
    QuarterlySummaryObservation,
    XBRLContext,
)
from stock_data_center.pit import MarketPITContext, SystemPITContext


pytestmark = pytest.mark.integration

WRITER = FinancialFilingWriter()
SERVICE = FinancialFilingService()
PERIOD = FilingPeriod(2024, 4)
PUBLISHED = datetime(2025, 3, 15, 6, tzinfo=UTC)


def configure(db: Connection, source: str = "mops", *, canonical: bool = True) -> int:
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('financial_filing', 'Financial filings', 'v1')
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
                'financial_filing', :source, true, true, 100, 'verified',
                ARRAY['official'], :canonical
            )
            """
        ),
        {"source": source, "canonical": canonical},
    )
    return db.execute(
        sa.text("INSERT INTO security (security_code) VALUES (:code) RETURNING id"),
        {"code": f"financial-{source}"},
    ).scalar_one()


def lineage(db: Connection, marker: str, source: str = "mops") -> FilingLineageRef:
    run_id = db.execute(
        sa.text(
            """
            INSERT INTO ingest_runs (dataset_code, source, status, started_at)
            VALUES ('financial_filing', :source, 'succeeded', statement_timestamp())
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
            ) VALUES (:digest, :uri, 1, 'application/xml') RETURNING id
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
            "uri": f"https://{source}.test/{marker}.xml",
        },
    )
    return FilingLineageRef(artifact_id, run_id)


def filing_observation(key: str = "2024-q4-v1") -> FinancialFilingObservation:
    return FinancialFilingObservation(
        filing_key=key,
        report_year=2024,
        report_quarter=4,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        currency="TWD",
    )


def duration_context(**overrides: object) -> XBRLContext:
    values: dict[str, object] = {
        "entity_identifier": "TW-2330",
        "period_type": "duration",
        "period_start": date(2024, 1, 1),
        "period_end": date(2024, 12, 31),
    }
    values.update(overrides)
    return XBRLContext(**values)  # type: ignore[arg-type]


def begin_with_eps(
    db: Connection,
    security_id: int,
    lineage_ref: FilingLineageRef,
    *,
    key: str = "2024-q4-v1",
) -> int:
    written = WRITER.begin_filing(
        db,
        security_id=security_id,
        source="mops",
        observation=filing_observation(key),
        lineage=lineage_ref,
    )
    WRITER.append_fact(
        db,
        version_id=written.version_id,
        observation=FinancialFactObservation(
            concept_qname="{https://xbrl.ifrs.org/taxonomy/2024}BasicEarningsLossPerShare",
            context=duration_context(),
            unit_identity="TWD/shares",
            numeric_value=Decimal("38.45"),
            decimals="2",
        ),
    )
    WRITER.append_summary(
        db,
        version_id=written.version_id,
        observation=QuarterlySummaryObservation(
            metric_code="basic_eps",
            value=Decimal("38.45"),
            unit_identity="TWD/shares",
        ),
    )
    return written.version_id


def add_evidence(
    db: Connection,
    version_id: int,
    lineage_ref: FilingLineageRef,
    *,
    published_at: datetime | None = PUBLISHED,
) -> int:
    return WRITER.append_publication_evidence(
        db,
        source="mops",
        version_id=version_id,
        observation=FinancialPublication(
            evidence_kind="assertion" if published_at else "unknown",
            published_at=published_at,
            evidence_source="mops",
            evidence_type="official",
            quality_rank=100,
        ),
        lineage=lineage_ref,
    )


def market(at: datetime, knowledge: datetime | None = None) -> MarketPITContext:
    return MarketPITContext(
        information_as_of=at,
        knowledge_as_of=knowledge or datetime.now(UTC) + timedelta(days=1),
    )


def test_unsealed_filing_is_invisible_and_children_are_immutable_after_seal(
    db: Connection,
) -> None:
    security_id = configure(db)
    lineage_ref = lineage(db, "1")
    version_id = begin_with_eps(db, security_id, lineage_ref)
    add_evidence(db, version_id, lineage_ref)

    assert SERVICE.filing(
        db,
        security_code="financial-mops",
        period=PERIOD,
        context=SystemPITContext(datetime.now(UTC) + timedelta(days=1)),
    ) is None

    WRITER.seal(db, version_id=version_id)
    resolved = SERVICE.filing(
        db,
        security_code="financial-mops",
        period=PERIOD,
        context=SystemPITContext(datetime.now(UTC) + timedelta(days=1)),
    )
    assert resolved is not None
    assert len(resolved.facts) == 1
    assert resolved.summary[0].metric_code == "basic_eps"

    with pytest.raises(DBAPIError) as error:
        WRITER.append_summary(
            db,
            version_id=version_id,
            observation=QuarterlySummaryObservation(
                metric_code="diluted_eps",
                value=Decimal("38.00"),
                unit_identity="TWD/shares",
            ),
        )
    assert error.value.orig.sqlstate == "55000"


def test_q4_and_actual_eps_visibility_follow_two_clock_evidence(db: Connection) -> None:
    security_id = configure(db)
    lineage_ref = lineage(db, "2")
    version_id = begin_with_eps(db, security_id, lineage_ref)
    WRITER.seal(db, version_id=version_id)
    before_evidence = db.scalar(sa.select(sa.func.clock_timestamp()))
    add_evidence(db, version_id, lineage_ref)

    assert SERVICE.actual_eps(
        db,
        security_code="financial-mops",
        period=PERIOD,
        context=market(datetime(2025, 2, 28, 23, 59, tzinfo=UTC)),
    ) is None
    assert SERVICE.actual_eps(
        db,
        security_code="financial-mops",
        period=PERIOD,
        context=market(datetime(2025, 3, 16, tzinfo=UTC), before_evidence),
    ) is None
    actual = SERVICE.actual_eps(
        db,
        security_code="financial-mops",
        period=PERIOD,
        context=market(datetime(2025, 3, 16, tzinfo=UTC)),
    )
    assert actual is not None
    assert actual.value == Decimal("38.45")
    assert actual.filing.authoritative_evidence is not None
    assert actual.filing.authoritative_evidence.published_at == PUBLISHED


def test_unknown_publication_remains_market_invisible_but_system_visible(
    db: Connection,
) -> None:
    security_id = configure(db)
    lineage_ref = lineage(db, "3")
    version_id = begin_with_eps(db, security_id, lineage_ref)
    WRITER.seal(db, version_id=version_id)
    add_evidence(db, version_id, lineage_ref, published_at=None)

    assert SERVICE.filing(
        db,
        security_code="financial-mops",
        period=PERIOD,
        context=market(datetime(2030, 1, 1, tzinfo=UTC)),
    ) is None
    assert SERVICE.filing(
        db,
        security_code="financial-mops",
        period=PERIOD,
        context=SystemPITContext(datetime.now(UTC) + timedelta(days=1)),
    ) is not None


def test_context_identity_includes_dimensions_scenario_and_segment(db: Connection) -> None:
    security_id = configure(db)
    lineage_ref = lineage(db, "4")
    version_id = WRITER.begin_filing(
        db,
        security_id=security_id,
        source="mops",
        observation=filing_observation(),
        lineage=lineage_ref,
    ).version_id
    concept = "{https://xbrl.ifrs.org/taxonomy/2024}Revenue"
    contexts = (
        duration_context(explicit_dimensions={"{urn:axis}Segment": "{urn:member}A"}),
        duration_context(explicit_dimensions={"{urn:axis}Segment": "{urn:member}B"}),
        duration_context(typed_dimensions={"{urn:axis}Customer": {"value": "X"}}),
        duration_context(scenario={"forecast": False}),
        duration_context(segment={"geography": "TW"}),
    )
    for index, context in enumerate(contexts):
        WRITER.append_fact(
            db,
            version_id=version_id,
            observation=FinancialFactObservation(
                concept_qname=concept,
                context=context,
                unit_identity="TWD",
                numeric_value=Decimal(index),
            ),
        )
    hashes = db.execute(
        sa.text(
            "SELECT context_hash FROM financial_facts WHERE filing_version_id = :id"
        ),
        {"id": version_id},
    ).scalars().all()
    assert len(set(hashes)) == len(contexts)

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            WRITER.append_fact(
                db,
                version_id=version_id,
                observation=FinancialFactObservation(
                    concept_qname=concept,
                    context=duration_context(
                        explicit_dimensions={"{urn:axis}Segment": "{urn:member}A"}
                    ),
                    unit_identity="TWD",
                    numeric_value=Decimal("999"),
                ),
            )


def test_qname_and_exactly_one_value_are_database_contracts(db: Connection) -> None:
    security_id = configure(db)
    lineage_ref = lineage(db, "5")
    version_id = WRITER.begin_filing(
        db,
        security_id=security_id,
        source="mops",
        observation=filing_observation(),
        lineage=lineage_ref,
    ).version_id
    for concept, numeric, text in (("Revenue", 1, None), ("{urn:test}Note", 1, "x")):
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(
                    sa.text(
                        """
                        INSERT INTO financial_facts (
                            filing_version_id, concept_qname, context_hash,
                            entity_identifier, period_type, instant_date,
                            unit_identity, numeric_value, text_value
                        ) VALUES (
                            :id, :concept, :hash, 'TW-2330', 'instant',
                            DATE '2024-12-31', 'TWD', :numeric, :text
                        )
                        """
                    ),
                    {
                        "id": version_id,
                        "concept": concept,
                        "hash": "0" * 64,
                        "numeric": numeric,
                        "text": text,
                    },
                )


def test_repeated_fetch_reuses_business_version_and_keeps_both_lineages(
    db: Connection,
) -> None:
    security_id = configure(db)
    first_lineage = lineage(db, "6")
    first = WRITER.begin_filing(
        db,
        security_id=security_id,
        source="mops",
        observation=filing_observation(),
        lineage=first_lineage,
    )
    begin_with_eps(db, security_id, first_lineage)
    WRITER.seal(db, version_id=first.version_id)
    evidence_id = add_evidence(db, first.version_id, first_lineage)

    second_lineage = lineage(db, "7")
    repeated = WRITER.begin_filing(
        db,
        security_id=security_id,
        source="mops",
        observation=filing_observation(),
        lineage=second_lineage,
    )
    repeated_evidence = add_evidence(db, repeated.version_id, second_lineage)

    assert repeated.version_id == first.version_id
    assert repeated.created is False
    assert repeated_evidence == evidence_id
    assert len(SERVICE.observations(db, version_id=first.version_id)) == 2
    assert db.scalar(sa.text("SELECT count(*) FROM financial_filing_versions")) == 1
    assert db.scalar(
        sa.text(
            """
            SELECT count(*) FROM publication_evidence_observations
            WHERE publication_evidence_id = :id
            """
        ),
        {"id": evidence_id},
    ) == 2


def test_cross_source_filing_observation_lineage_is_rejected(db: Connection) -> None:
    security_id = configure(db)
    lineage_ref = lineage(db, "8")
    version_id = WRITER.begin_filing(
        db,
        security_id=security_id,
        source="mops",
        observation=filing_observation(),
        lineage=lineage_ref,
    ).version_id
    configure(db, "vendor", canonical=False)
    wrong_lineage = lineage(db, "9", source="vendor")
    with pytest.raises(DBAPIError) as error:
        with db.begin_nested():
            db.execute(
                sa.text(
                    """
                    INSERT INTO financial_filing_version_observations (
                        filing_version_id, raw_artifact_id, ingest_run_id
                    ) VALUES (:version, :artifact, :run)
                    """
                ),
                {
                    "version": version_id,
                    "artifact": wrong_lineage.raw_artifact_id,
                    "run": wrong_lineage.ingest_run_id,
                },
            )
    assert error.value.orig.sqlstate == "23514"


def test_lineage_migration_backfills_existing_filing(
    isolated_database_url: str,
) -> None:
    config = alembic_config(isolated_database_url)
    command.downgrade(config, "c92e81a40d17")
    migration_engine = sa.create_engine(isolated_database_url)
    try:
        with migration_engine.begin() as connection:
            security_id = configure(connection)
            source_lineage = lineage(connection, "a")
            version_id = connection.execute(
                sa.text(
                    """
                    INSERT INTO financial_filing_versions (
                        security_id, source, filing_key, report_year,
                        report_quarter, period_start, period_end, currency,
                        raw_artifact_id, ingest_run_id
                    ) VALUES (
                        :security, 'mops', 'legacy-q4', 2024, 4,
                        DATE '2024-01-01', DATE '2024-12-31', 'TWD',
                        :artifact, :run
                    ) RETURNING id
                    """
                ),
                {
                    "security": security_id,
                    "artifact": source_lineage.raw_artifact_id,
                    "run": source_lineage.ingest_run_id,
                },
            ).scalar_one()
        migration_engine.dispose()

        command.upgrade(config, "head")
        migration_engine = sa.create_engine(isolated_database_url)
        with migration_engine.connect() as connection:
            linked = connection.execute(
                sa.text(
                    """
                    SELECT raw_artifact_id, ingest_run_id
                    FROM financial_filing_version_observations
                    WHERE filing_version_id = :version
                    """
                ),
                {"version": version_id},
            ).one()
            assert linked == (
                source_lineage.raw_artifact_id,
                source_lineage.ingest_run_id,
            )
    finally:
        migration_engine.dispose()
