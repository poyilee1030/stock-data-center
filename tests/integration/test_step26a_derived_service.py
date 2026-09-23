"""Step 26-a: the derivation service — definitions, rolling as-of, no leakage.

Nothing is materialised (ROADMAP §17): the rolling series and every other PIT
context are computed on demand, and these tests pin that the two agree.
"""

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
from stock_data_center.pit import MarketPITContext, PITResolver

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


# The evidence rows this suite writes are recorded now, so the knowledge cutoff
# has to be after now. A fixed date was the first version, and it stopped
# seeing any evidence the day after it was written.
KNOWLEDGE = datetime.now(UTC) + timedelta(days=1)

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


def _rolling(db: Connection, end: date = date(2024, 7, 5)):
    return SERVICE.rolling(
        db,
        security_code="9999",
        start_date=date(2024, 7, 1),
        end_date=end,
        source=SOURCE,
        knowledge_as_of=KNOWLEDGE,
    )


def _at(db: Connection, day: date, information_as_of: datetime):
    rows = SERVICE.compute(
        db,
        security_code="9999",
        start_date=day,
        end_date=day,
        context=MarketPITContext(
            information_as_of=information_as_of, knowledge_as_of=KNOWLEDGE
        ),
        source=SOURCE,
    )
    return rows[0] if rows else None


def _assert_rolling_equals_compute_at_each_cutoff(db: Connection, rolling) -> None:
    """Each rolling row is exactly what one context at that date's cutoff sees."""
    for row in rolling:
        single = _at(db, row.observation_date, _settled(row.observation_date))
        assert single == row


def test_the_rolling_series_uses_each_date_own_release_cutoff(db: Connection) -> None:
    _seed(db, WEEK)
    rows = _rolling(db)

    assert [row.observation_date for row in rows] == sorted(WEEK)
    for row in rows:
        assert row.information_as_of == _settled(row.observation_date)
        # The market axis moves with the observation date; the Data Center's own
        # axis is the series', because the evidence was recorded when it was.
        assert row.knowledge_as_of == KNOWLEDGE
        assert row.source == SOURCE
        assert (row.dataset_code, row.derivation_version) == ("technical_indicators", "v1")

    # ma5 only exists once five closes are visible: the fifth day, not before.
    assert [row.metrics["ma5"] for row in rows[:4]] == [None, None, None, None]
    assert rows[4].metrics["ma5"] == pytest.approx(14)
    assert [row.input_version_count for row in rows] == [1, 2, 3, 4, 5]


def test_rolling_and_on_demand_agree_for_one_context(db: Connection) -> None:
    _seed(db, WEEK)
    _assert_rolling_equals_compute_at_each_cutoff(db, _rolling(db))


def test_the_fingerprint_is_the_ordered_input_version_ids(db: Connection) -> None:
    security_id = _seed(db, WEEK)
    ids = db.scalars(
        sa.text(
            "SELECT id FROM daily_price_versions WHERE security_id = :security "
            "ORDER BY trade_date"
        ),
        {"security": security_id},
    ).all()
    import hashlib

    expected = hashlib.sha256(",".join(str(i) for i in ids).encode()).hexdigest()
    assert _rolling(db)[-1].input_fingerprint == expected


def test_a_later_correction_does_not_reach_back_into_an_earlier_date(
    db: Connection,
) -> None:
    """A revision of 7-01 published after 7-05's cutoff must leave 7-05 alone."""
    security_id = _seed(db, WEEK)
    _add_price(
        db,
        security_id=security_id,
        trade_date=date(2024, 7, 1),
        close="99",
        lineage=_lineage(db, "b"),
        published_at=_settled(date(2024, 7, 20)),
    )
    # The uncorrected week averages to 14; the correction would make it 31.8.
    assert _rolling(db)[-1].metrics["ma5"] == pytest.approx(14)
    corrected = _at(db, date(2024, 7, 5), _settled(date(2024, 7, 21)))
    assert corrected is not None
    assert corrected.metrics["ma5"] == pytest.approx(31.8)


def test_a_correction_inside_the_window_splits_the_series_where_it_lands(
    db: Connection,
) -> None:
    """7-01 corrected at 7-03's cutoff: 7-01 and 7-02 keep the old close, the rest do not."""
    security_id = _seed(db, WEEK)
    _add_price(
        db,
        security_id=security_id,
        trade_date=date(2024, 7, 1),
        close="99",
        lineage=_lineage(db, "b"),
        published_at=_settled(date(2024, 7, 2)) + timedelta(hours=1),
    )
    rows = _rolling(db)
    _assert_rolling_equals_compute_at_each_cutoff(db, rows)
    by_date = {row.observation_date: row for row in rows}
    # 7-02 still read the original 7-01; from 7-03 on, the corrected one.
    assert by_date[date(2024, 7, 2)].metrics["ma5"] is None
    assert by_date[date(2024, 7, 5)].metrics["ma5"] == pytest.approx(31.8)
    assert _at(db, date(2024, 7, 2), _settled(date(2024, 7, 2))) == by_date[date(2024, 7, 2)]


def test_a_price_published_after_its_own_cutoff_has_no_row_that_day(
    db: Connection,
) -> None:
    """7-03 lands a day late: the market could not compute 7-03 at 7-03's cutoff."""
    security_id = _seed(db, {day: close for day, close in WEEK.items() if day != date(2024, 7, 3)})
    _add_price(
        db,
        security_id=security_id,
        trade_date=date(2024, 7, 3),
        close="14",
        lineage=_lineage(db, "b"),
        published_at=_settled(date(2024, 7, 4)),
    )
    rows = _rolling(db)
    _assert_rolling_equals_compute_at_each_cutoff(db, rows)
    assert date(2024, 7, 3) not in {row.observation_date for row in rows}
    later = {row.observation_date: row for row in rows}[date(2024, 7, 4)]
    assert later.input_version_count == 4


def test_a_day_after_the_window_cannot_change_a_value(db: Connection) -> None:
    security_id = _seed(db, WEEK)
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
    rows = {row.observation_date: row for row in _rolling(db, end=date(2024, 7, 8))}
    assert rows[date(2024, 7, 5)].metrics["ma5"] == pytest.approx(expected)


def test_computing_writes_nothing(db: Connection) -> None:
    _seed(db, WEEK)
    before = db.scalar(sa.text("SELECT count(*) FROM derived_metric_versions"))
    runs = db.scalar(sa.text("SELECT count(*) FROM derived_computation_runs"))
    _rolling(db)
    _at(db, date(2024, 7, 5), _settled(date(2024, 7, 5)))
    assert db.scalar(sa.text("SELECT count(*) FROM derived_metric_versions")) == before
    assert db.scalar(sa.text("SELECT count(*) FROM derived_computation_runs")) == runs


def test_the_history_resolves_every_key_as_resolve_does(db: Connection) -> None:
    """The batch path and the per-key resolver are one rule, at any cutoff."""
    security_id = _seed(db, WEEK)
    _add_price(
        db,
        security_id=security_id,
        trade_date=date(2024, 7, 2),
        close="50",
        lineage=_lineage(db, "b"),
        published_at=_settled(date(2024, 7, 3)),
    )
    resolver = PITResolver()
    history = resolver.market_history(
        db,
        dataset_code="daily_price",
        key_filter={"security_id": security_id},
        through={"trade_date": date(2024, 7, 5)},
        knowledge_as_of=KNOWLEDGE,
        source=SOURCE,
    )
    for cutoff_day in [date(2024, 6, 30), *sorted(WEEK), date(2024, 7, 9)]:
        information_as_of = _settled(cutoff_day)
        visible = history.visible(information_as_of)
        for day in sorted(WEEK):
            single = resolver.resolve(
                db,
                dataset_code="daily_price",
                logical_key={"security_id": security_id, "trade_date": day},
                context=MarketPITContext(
                    information_as_of=information_as_of, knowledge_as_of=KNOWLEDGE
                ),
                source=SOURCE,
            )
            batch = visible.get((security_id, day))
            if single is None:
                assert batch is None
            else:
                assert batch is not None
                assert batch.version_id == single.provenance.version_id
                assert batch.evidence == single.authoritative_evidence


def test_a_knowledge_cutoff_before_the_evidence_sees_nothing(db: Connection) -> None:
    _seed(db, WEEK)
    rows = SERVICE.rolling(
        db,
        security_code="9999",
        start_date=date(2024, 7, 1),
        end_date=date(2024, 7, 5),
        source=SOURCE,
        knowledge_as_of=datetime(2024, 7, 10, tzinfo=UTC),
    )
    assert rows == ()


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


def test_a_long_history_is_one_row_per_trade_date(db: Connection) -> None:
    days = [date(2024, 1, 1) + timedelta(days=offset) for offset in range(400)]
    closes = {day: str(10 + index % 7) for index, day in enumerate(days)}
    _seed(db, closes)
    rows = SERVICE.rolling(
        db,
        security_code="9999",
        start_date=days[0],
        end_date=days[-1],
        source=SOURCE,
        knowledge_as_of=KNOWLEDGE,
    )
    assert [row.observation_date for row in rows] == days
    assert all(set(row.metrics) == set(METRIC_CODES) for row in rows)
