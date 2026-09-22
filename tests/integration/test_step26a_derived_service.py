"""Step 26-a: the derivation service — definitions, rolling as-of, no leakage."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.derived import (
    METRIC_CODES,
    TECHNICAL_INDICATORS_V1,
    DerivationRegistry,
    TechnicalIndicatorService,
)
from stock_data_center.derived.indicators import DailyBar, technical_indicators
from stock_data_center.market_data import (
    DailyPriceObservation,
    LineageRef,
    MarketDataWriter,
    PublicationObservation,
)
from stock_data_center.pit import MarketPITContext

pytestmark = pytest.mark.integration

WRITER = MarketDataWriter()
REGISTRY = DerivationRegistry()
SERVICE = TechnicalIndicatorService()
SOURCE = "twse_mi_index"
TAIPEI = "Asia/Taipei"


def _configure_daily_price_source(db: Connection) -> None:
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('daily_price', 'daily price', 'v1')
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
            ) VALUES ('daily_price', :source, true, true, 0, 'verified',
                      ARRAY['official', 'release_rule']::varchar[], false)
            ON CONFLICT (dataset_code, source) DO NOTHING
            """
        ),
        {"source": SOURCE},
    )


def _lineage(db: Connection, digest_character: str) -> LineageRef:
    run_id = db.execute(
        sa.text(
            """
            INSERT INTO ingest_runs (dataset_code, source, status, started_at, purpose)
            VALUES ('daily_price', :source, 'succeeded', statement_timestamp(),
                    'gap_fill')
            RETURNING id
            """
        ),
        {"source": SOURCE},
    ).scalar_one()
    digest = digest_character * 64
    artifact_id = db.execute(
        sa.text(
            """
            INSERT INTO raw_artifacts (raw_artifact_hash, storage_uri, byte_size,
                                       media_type)
            VALUES (:digest, :uri, 1, 'application/json')
            RETURNING id
            """
        ),
        {"digest": digest, "uri": f"data/raw/{digest[:2]}/{digest}"},
    ).scalar_one()
    db.execute(
        sa.text(
            """
            INSERT INTO raw_artifact_observations (raw_artifact_id, ingest_run_id,
                                                   source_uri, fetched_at)
            VALUES (:artifact, :run, 'https://twse.test/mi', statement_timestamp())
            """
        ),
        {"artifact": artifact_id, "run": run_id},
    )
    return LineageRef(artifact_id, run_id)


def _settled(trade_date: date) -> datetime:
    """`exchange_daily_settled@1`: trade date D is public at 03:00 on D+1."""
    from zoneinfo import ZoneInfo

    return datetime.combine(
        trade_date + timedelta(days=1),
        datetime.min.time().replace(hour=3),
        tzinfo=ZoneInfo(TAIPEI),
    )


def _add_price(
    db: Connection,
    *,
    security_id: int,
    trade_date: date,
    close: str,
    lineage: LineageRef,
    published_at: datetime | None = None,
) -> None:
    written = WRITER.append_daily_price(
        db,
        security_id=security_id,
        source=SOURCE,
        observation=DailyPriceObservation(
            trade_date=trade_date,
            open_price=Decimal(close),
            high_price=Decimal(close),
            low_price=Decimal(close),
            close_price=Decimal(close),
            volume=Decimal("1000"),
        ),
        lineage=lineage,
    )
    WRITER.append_publication_evidence(
        db,
        dataset_code="daily_price",
        source=SOURCE,
        version_id=written.version_id,
        observation=PublicationObservation(
            evidence_kind="assertion",
            published_at=published_at or _settled(trade_date),
            evidence_source="exchange_daily_settled@1",
            evidence_type="release_rule",
            quality_rank=40,
        ),
        lineage=lineage,
    )


def _seed(db: Connection, closes: dict[date, str]) -> int:
    _configure_daily_price_source(db)
    security_id = WRITER.register_security(db, security_code="9999")
    lineage = _lineage(db, "a")
    for trade_date, close in sorted(closes.items()):
        _add_price(
            db,
            security_id=security_id,
            trade_date=trade_date,
            close=close,
            lineage=lineage,
        )
    return security_id


# The evidence rows this suite writes are recorded now, so every run states the
# same knowledge cutoff rather than inheriting the wall clock.
KNOWLEDGE = datetime(2026, 9, 22, tzinfo=UTC) + timedelta(days=1)

WEEK = {
    date(2024, 7, 1): "10",
    date(2024, 7, 2): "12",
    date(2024, 7, 3): "14",
    date(2024, 7, 4): "16",
    date(2024, 7, 5): "18",
}


def test_registering_the_same_definition_twice_stores_one_row(db: Connection) -> None:
    first = REGISTRY.register(db, TECHNICAL_INDICATORS_V1)
    second = REGISTRY.register(db, TECHNICAL_INDICATORS_V1)
    assert first == second
    assert db.scalar(
        sa.text(
            "SELECT count(*) FROM derived_dataset_definitions "
            "WHERE dataset_code = :code AND derivation_version = :version"
        ),
        {
            "code": TECHNICAL_INDICATORS_V1.dataset_code,
            "version": TECHNICAL_INDICATORS_V1.derivation_version,
        },
    ) == 1


def test_a_changed_formula_may_not_reuse_its_derivation_version(
    db: Connection,
) -> None:
    REGISTRY.register(db, TECHNICAL_INDICATORS_V1)
    from dataclasses import replace

    changed = replace(
        TECHNICAL_INDICATORS_V1,
        formula_specification=TECHNICAL_INDICATORS_V1.formula_specification + " (edited)",
    )
    with pytest.raises(ValueError, match="derivation_version"):
        REGISTRY.register(db, changed)


def test_the_rolling_series_uses_each_date_own_release_cutoff(db: Connection) -> None:
    security_id = _seed(db, WEEK)
    REGISTRY.register(db, TECHNICAL_INDICATORS_V1)
    written = SERVICE.materialize(
        db,
        security_code="9999",
        start_date=date(2024, 7, 1),
        end_date=date(2024, 7, 5),
        source=SOURCE,
        knowledge_as_of=KNOWLEDGE,
    )
    assert written > 0

    rows = db.execute(
        sa.text(
            """
            SELECT observation_date, information_as_of, knowledge_as_of, system_as_of,
                   metric_code, numeric_value, pit_mode
              FROM derived_metric_versions
             WHERE security_id = :security_id AND metric_code = 'ma5'
             ORDER BY observation_date
            """
        ),
        {"security_id": security_id},
    ).mappings().all()

    assert [row["observation_date"] for row in rows] == sorted(WEEK)
    for row in rows:
        assert row["pit_mode"] == "market"
        assert row["system_as_of"] is None
        assert row["information_as_of"] == _settled(row["observation_date"])
        # The market axis moves with the observation date; the Data Center's own
        # axis is the run's, because the evidence was recorded when it was.
        assert row["knowledge_as_of"] == KNOWLEDGE

    # ma5 only exists once five closes are visible: the fifth day, not before.
    assert [row["numeric_value"] for row in rows[:4]] == [None, None, None, None]
    assert rows[4]["numeric_value"] == Decimal("14")


def test_materialized_and_virtual_agree_for_one_context(db: Connection) -> None:
    _seed(db, WEEK)
    REGISTRY.register(db, TECHNICAL_INDICATORS_V1)
    SERVICE.materialize(
        db,
        security_code="9999",
        start_date=date(2024, 7, 1),
        end_date=date(2024, 7, 5),
        source=SOURCE,
        knowledge_as_of=KNOWLEDGE,
    )
    cutoff = _settled(date(2024, 7, 5))
    virtual = SERVICE.compute(
        db,
        security_code="9999",
        start_date=date(2024, 7, 5),
        end_date=date(2024, 7, 5),
        context=MarketPITContext(
            information_as_of=cutoff, knowledge_as_of=KNOWLEDGE
        ),
        source=SOURCE,
    )
    stored = db.execute(
        sa.text(
            """
            SELECT metric_code, numeric_value FROM derived_metric_versions
             WHERE observation_date = :day
            """
        ),
        {"day": date(2024, 7, 5)},
    ).mappings().all()
    stored_by_code = {row["metric_code"]: row["numeric_value"] for row in stored}
    assert stored_by_code
    for metric_code, value in virtual[0].metrics.items():
        if value is None:
            assert stored_by_code[metric_code] is None
        else:
            assert float(stored_by_code[metric_code]) == pytest.approx(value, abs=1e-9)


def test_a_later_correction_does_not_reach_back_into_an_earlier_date(
    db: Connection,
) -> None:
    """A revision of 7-01 published after 7-05's cutoff must leave 7-05 alone."""
    security_id = _seed(db, WEEK)
    REGISTRY.register(db, TECHNICAL_INDICATORS_V1)
    lineage = _lineage(db, "b")
    _add_price(
        db,
        security_id=security_id,
        trade_date=date(2024, 7, 1),
        close="99",
        lineage=lineage,
        published_at=_settled(date(2024, 7, 20)),
    )
    SERVICE.materialize(
        db,
        security_code="9999",
        start_date=date(2024, 7, 1),
        end_date=date(2024, 7, 5),
        source=SOURCE,
        knowledge_as_of=KNOWLEDGE,
    )
    value = db.scalar(
        sa.text(
            "SELECT numeric_value FROM derived_metric_versions "
            "WHERE observation_date = :day AND metric_code = 'ma5'"
        ),
        {"day": date(2024, 7, 5)},
    )
    # The uncorrected week averages to 14; the correction would make it 31.8.
    assert value == Decimal("14")

    late = _settled(date(2024, 7, 21))
    corrected = SERVICE.compute(
        db,
        security_code="9999",
        start_date=date(2024, 7, 5),
        end_date=date(2024, 7, 5),
        context=MarketPITContext(
            information_as_of=late, knowledge_as_of=KNOWLEDGE
        ),
        source=SOURCE,
    )
    assert corrected[0].metrics["ma5"] == pytest.approx(31.8)


def test_rematerialising_the_same_window_adds_nothing(db: Connection) -> None:
    _seed(db, WEEK)
    REGISTRY.register(db, TECHNICAL_INDICATORS_V1)
    window = {
        "security_code": "9999",
        "start_date": date(2024, 7, 1),
        "end_date": date(2024, 7, 5),
        "source": SOURCE,
        "knowledge_as_of": KNOWLEDGE,
    }
    SERVICE.materialize(db, **window)
    before = db.scalar(sa.text("SELECT count(*) FROM derived_metric_versions"))
    SERVICE.materialize(db, **window)
    after = db.scalar(sa.text("SELECT count(*) FROM derived_metric_versions"))
    assert before == after


def test_a_day_after_the_window_cannot_change_a_materialised_value(
    db: Connection,
) -> None:
    security_id = _seed(db, WEEK)
    REGISTRY.register(db, TECHNICAL_INDICATORS_V1)
    expected = technical_indicators(
        tuple(
            DailyBar(
                trade_date=day,
                high=float(close),
                low=float(close),
                close=float(close),
                volume=1000.0,
            )
            for day, close in sorted(WEEK.items())
        )
    )[-1].metrics["ma5"]

    _add_price(
        db,
        security_id=security_id,
        trade_date=date(2024, 7, 8),
        close="100",
        lineage=_lineage(db, "c"),
    )
    SERVICE.materialize(
        db,
        security_code="9999",
        start_date=date(2024, 7, 1),
        end_date=date(2024, 7, 8),
        source=SOURCE,
        knowledge_as_of=KNOWLEDGE,
    )
    value = db.scalar(
        sa.text(
            "SELECT numeric_value FROM derived_metric_versions "
            "WHERE observation_date = :day AND metric_code = 'ma5'"
        ),
        {"day": date(2024, 7, 5)},
    )
    assert float(value) == pytest.approx(expected)


def test_computed_at_is_never_offered_as_a_publication_time(db: Connection) -> None:
    """`derived_metric_versions` has no `published_at`, and must not grow one."""
    columns = {
        row[0]
        for row in db.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'derived_metric_versions'"
            )
        )
    }
    assert "published_at" not in columns
    assert {"computed_at", "input_fingerprint", "computation_run_id"} <= columns


def test_a_long_history_is_written_rather_than_refused(db: Connection) -> None:
    """One statement binds at most 65,535 parameters; a series easily exceeds it.

    A security with real history has 1,600 trading days and 22 metrics each.
    The securities that overflow are exactly the ones worth having, so the
    failure would have been invisible in a five-day fixture and total in
    production.
    """
    from datetime import timedelta

    days = [date(2024, 1, 1) + timedelta(days=offset) for offset in range(400)]
    closes = {day: str(10 + index % 7) for index, day in enumerate(days)}
    _seed(db, closes)
    REGISTRY.register(db, TECHNICAL_INDICATORS_V1)
    written = SERVICE.materialize(
        db,
        security_code="9999",
        start_date=days[0],
        end_date=days[-1],
        source=SOURCE,
        knowledge_as_of=KNOWLEDGE,
    )
    assert written == len(days) * len(METRIC_CODES)
    assert db.scalar(
        sa.text(
            "SELECT count(DISTINCT observation_date) FROM derived_metric_versions"
        )
    ) == len(days)
