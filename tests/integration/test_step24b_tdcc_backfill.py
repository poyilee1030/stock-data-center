"""Step 24-b — walking the TDCC archive, and the weekly coverage cadence."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.coverage import CoverageValidator, ExpectedCoverageService
from stock_data_center.ingestion.backfill import (
    TDCCArchiveBackfill,
    default_base_import_id,
)
from stock_data_center.ingestion.http import TDCCArchiveFetcher
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.ingestion.tdcc_shareholding import TDCCShareholdingImporter
from stock_data_center.market_calendar import (
    TradingCalendarObservation,
    TradingCalendarWriter,
)
from stock_data_center.market_calendar.models import CalendarLineageRef
from stock_data_center.market_data import MarketDataWriter

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LATEST = (FIXTURES / "tdcc_od_1_5_20260918.csv").read_bytes()
TRUNCATED = (FIXTURES / "tdcc_od_1_5_20231020_truncated.csv").read_bytes()
MISNAMED = (FIXTURES / "tdcc_od_1_5_20200612_named_20200619.csv").read_bytes()
OVER_HUNDRED = (FIXTURES / "tdcc_od_1_5_20200430_double_bom.csv").read_bytes()

WEEK = date(2026, 9, 18)


def write(root: Path, name: str, payload: bytes) -> Path:
    year = root / name[:4]
    year.mkdir(parents=True, exist_ok=True)
    path = year / name
    path.write_bytes(payload)
    return path


def zipped(payload: bytes, member: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, payload)
    return buffer.getvalue()


def register(engine, *codes: str) -> None:
    with engine.begin() as connection:
        MarketDataWriter().register_securities(
            connection, security_codes=list(codes)
        )


def backfill(engine, tmp_path: Path, root: Path) -> TDCCArchiveBackfill:
    return TDCCArchiveBackfill(
        TDCCShareholdingImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=TDCCArchiveFetcher(),
        ),
        archive_root=root,
    )


def test_the_walk_enumerates_the_weeks_the_archive_holds(tmp_path: Path) -> None:
    """Including the archive's three filename shapes, and both copies of a
    week counted once (audit §4.9)."""
    root = tmp_path / "shareholding"
    write(root, "20240105.7z", b"")
    write(root, "20200103.csv", b"")
    write(root, "20200103.zip", b"")
    write(root, "20201008_集保戶股權分散表20201008.7z", b"")
    write(root, "TDCC_OD_1-5_20260918.csv", b"")
    quarantine = root / "_quarantine"
    quarantine.mkdir(parents=True)
    (quarantine / "20200619.CSV").write_bytes(b"")
    walk = TDCCArchiveBackfill(None, archive_root=root)  # type: ignore[arg-type]
    assert walk.weeks_in(date(2019, 1, 1), date(2027, 1, 1)) == (
        date(2020, 1, 3),
        date(2020, 10, 8),
        date(2024, 1, 5),
        date(2026, 9, 18),
    )
    # The range is respected, and a file whose name states no date is loud.
    assert walk.weeks_in(date(2020, 1, 4), date(2024, 12, 31)) == (
        date(2020, 10, 8),
        date(2024, 1, 5),
    )
    write(root, "shareholding_backup.csv", b"")
    with pytest.raises(ValueError, match="states no date"):
        walk.weeks_in(date(2019, 1, 1), date(2027, 1, 1))


def test_each_week_imports_under_its_own_resumable_import_id(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "0056", "1101", "00673R")
        root = tmp_path / "shareholding"
        write(root, "20260918.csv", LATEST)
        write(root, "20200430.csv", OVER_HUNDRED)
        report = backfill(engine, tmp_path, root).run(
            start=date(2020, 1, 1), end=date(2026, 12, 31)
        )
        assert report.weeks == (date(2020, 4, 30), WEEK)
        assert (report.imported, report.failed, report.quarantined) == (2, 0, 0)
        assert report.created == 5
        assert report.as_dict()["truncated_weeks"] == []
        # Derived from the range, so the same command resumes rather than
        # starting a second walk.
        base = default_base_import_id(
            "tdcc_opendata", date(2020, 1, 1), date(2026, 12, 31)
        )
        assert len({item.import_id for item in report.results}) == 2
        assert all(item.import_id != base for item in report.results)

        repeated = backfill(engine, tmp_path, root).run(
            start=date(2020, 1, 1), end=date(2026, 12, 31)
        )
        # Every week comes back from its completed checkpoint, replaying the
        # counts the first run recorded rather than fetching or writing again.
        assert repeated.resumed == 2
        assert repeated.imported == 0
        with engine.connect() as connection:
            versions = connection.scalar(
                sa.text("SELECT count(*) FROM tdcc_snapshot_versions")
            )
            runs = connection.scalar(
                sa.text(
                    "SELECT count(*) FROM ingest_runs "
                    "WHERE dataset_code = 'tdcc_snapshot'"
                )
            )
        assert versions == 5
        assert runs == 2
    finally:
        engine.dispose()


def test_one_unreadable_week_is_reported_and_the_walk_continues(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The file named for 2020-06-19 holds the 2020-06-12 table (audit §4.9).

    Asking for it as 2020-06-19 quarantines that week alone; ending the walk
    would cost every other week its data.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "0056", "1101", "0050")
        root = tmp_path / "shareholding"
        write(root, "20200619.CSV", MISNAMED)
        write(root, "20260918.csv", LATEST)
        report = backfill(engine, tmp_path, root).run(
            start=date(2020, 1, 1), end=date(2026, 12, 31)
        )
        assert report.imported == 1
        assert report.quarantined == 1
        assert report.failed == 0
        assert report.is_complete is True
        failure = report.as_dict()["failures"][0]
        assert failure["snapshot_date"] == "2020-06-19"
        assert failure["reason_code"] == "snapshot_date_mismatch"
        with engine.connect() as connection:
            stored = set(
                connection.scalars(
                    sa.text("SELECT DISTINCT snapshot_date FROM tdcc_snapshot_versions")
                )
            )
        assert stored == {WEEK}
    finally:
        engine.dispose()


def test_a_truncated_week_is_named_in_the_walk_report(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "8162")
        root = tmp_path / "shareholding"
        write(root, "20231020.7z", TRUNCATED)
        report = backfill(engine, tmp_path, root).run(
            start=date(2023, 1, 1), end=date(2023, 12, 31)
        )
        assert report.as_dict()["truncated_weeks"] == ["2023-10-20"]
        assert report.as_dict()["row_quarantined_count"] == 1
        assert report.imported == 1
    finally:
        engine.dispose()


def test_a_week_held_twice_is_imported_once(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        register(engine, "2330", "0056", "1101")
        root = tmp_path / "shareholding"
        write(root, "20260918.csv", LATEST)
        write(root, "20260918.zip", zipped(LATEST, "20260918.csv"))
        report = backfill(engine, tmp_path, root).run(start=WEEK, end=WEEK)
        assert report.weeks == (WEEK,)
        assert report.created == 3
    finally:
        engine.dispose()


# --- the weekly coverage cadence -------------------------------------------

JANUARY_2021 = tuple(
    date(2021, 1, day)
    for day in (4, 5, 6, 7, 8, 11, 12, 13, 14, 15, 18, 19, 20, 21, 22, 25, 26, 27, 28, 29)
)
# Lunar New Year 2021: the exchange did not open between 2021-02-08 and
# 2021-02-16, so the week of 2021-02-08 holds no trading day at all.
FEBRUARY_2021 = tuple(
    date(2021, 2, day) for day in (1, 2, 3, 4, 5, 17, 18, 19, 20, 22, 23, 24, 25, 26)
)


def calendar_lineage(
    db: Connection, dataset: str = "trading_calendar", source: str = "twse"
) -> CalendarLineageRef:
    run_id = db.execute(
        sa.text(
            "INSERT INTO ingest_runs (dataset_code, source, status, started_at) "
            "VALUES (:dataset, :source, 'succeeded', statement_timestamp()) "
            "RETURNING id"
        ),
        {"dataset": dataset, "source": source},
    ).scalar_one()
    digest = f"{abs(hash((run_id, 'tdcc'))):064x}"[:64]
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
            "VALUES (:artifact, :run, 'https://twse.test', statement_timestamp())"
        ),
        {"artifact": artifact_id, "run": run_id},
    )
    return CalendarLineageRef(artifact_id, run_id)


def store_calendar(db: Connection) -> None:
    writer = TradingCalendarWriter()
    for month, days in (
        (date(2021, 1, 1), JANUARY_2021),
        (date(2021, 2, 1), FEBRUARY_2021),
    ):
        writer.append_month(
            db,
            source="twse",
            observation=TradingCalendarObservation(
                market="TWSE",
                calendar_month=month,
                trading_days=days,
                coverage_through=date(
                    month.year, month.month, 31 if month.month == 1 else 28
                ),
            ),
            lineage=calendar_lineage(db),
        )


def store_snapshot(db: Connection, code: str, week: date) -> None:
    security_id = db.execute(
        sa.text(
            "INSERT INTO security (security_code) VALUES (:code) "
            "ON CONFLICT (security_code) DO UPDATE SET security_code = EXCLUDED."
            "security_code RETURNING id"
        ),
        {"code": code},
    ).scalar_one()
    lineage = calendar_lineage(db, "tdcc_snapshot", "tdcc_opendata")
    db.execute(
        sa.text(
            """
            INSERT INTO tdcc_snapshot_versions
                (security_id, source, snapshot_date, distribution_schema,
                 raw_artifact_id, ingest_run_id)
            VALUES (:security, 'tdcc_opendata', :week, 'tdcc-opendata-v1',
                    CAST(:artifact AS uuid), CAST(:run AS uuid))
            """
        ),
        {
            "security": security_id,
            "week": week,
            "artifact": lineage.raw_artifact_id,
            "run": lineage.ingest_run_id,
        },
    )


def test_the_weekly_cadence_expects_a_week_the_market_opened_in(
    db: Connection,
) -> None:
    store_calendar(db)
    weeks = ExpectedCoverageService().expected_periods(
        db,
        dataset_code="tdcc_snapshot",
        market="TW",
        start=date(2021, 1, 4),
        end=date(2021, 2, 26),
    )
    # Mondays, one per week the exchange opened in. The Lunar New Year week of
    # 2021-02-08 is absent: TDCC published on 2021-02-09 with the market shut
    # all week, which is exactly what cannot be predicted from a calendar.
    assert weeks == (
        date(2021, 1, 4),
        date(2021, 1, 11),
        date(2021, 1, 18),
        date(2021, 1, 25),
        date(2021, 2, 1),
        date(2021, 2, 15),
        date(2021, 2, 22),
    )


def test_a_week_with_a_snapshot_is_covered_whichever_day_it_falls_on(
    db: Connection,
) -> None:
    """Friday, Thursday, a make-up Saturday — the week is the unit."""
    store_calendar(db)
    for week_end in (
        date(2021, 1, 8),
        date(2021, 1, 15),
        date(2021, 1, 22),
        date(2021, 1, 29),
        date(2021, 2, 5),
        date(2021, 2, 20),  # a make-up Saturday: the exchange never opened
        date(2021, 2, 26),
    ):
        store_snapshot(db, f"T{week_end:%m%d}", week_end)
    report = CoverageValidator().report(
        db,
        dataset_code="tdcc_snapshot",
        market="TW",
        start=date(2021, 1, 4),
        end=date(2021, 2, 26),
    )
    assert report.missing == ()
    assert report.unexpected == ()
    assert report.is_complete is True
    assert report.non_trading_days == (date(2021, 2, 8),)


def test_a_missing_week_is_a_gap_and_a_closed_week_is_not(
    db: Connection,
) -> None:
    store_calendar(db)
    store_snapshot(db, "T0108", date(2021, 1, 8))
    report = CoverageValidator().report(
        db,
        dataset_code="tdcc_snapshot",
        market="TW",
        start=date(2021, 1, 4),
        end=date(2021, 2, 26),
    )
    assert report.observed == (date(2021, 1, 4),)
    assert report.missing == (
        date(2021, 1, 11),
        date(2021, 1, 18),
        date(2021, 1, 25),
        date(2021, 2, 1),
        date(2021, 2, 15),
        date(2021, 2, 22),
    )
    # The closed week is neither observed nor missing: it is reported as the
    # closure it is.
    assert date(2021, 2, 8) not in report.missing
    assert report.non_trading_days == (date(2021, 2, 8),)


def test_a_snapshot_in_a_week_the_market_never_opened_is_unexpected_not_hidden(
    db: Connection,
) -> None:
    """2021-02-09: TDCC's books were open, the exchange was shut all week.

    Reported rather than filtered away — the same treatment as a price on a
    day the market never opened.
    """
    store_calendar(db)
    store_snapshot(db, "T0209", date(2021, 2, 9))
    report = CoverageValidator().report(
        db,
        dataset_code="tdcc_snapshot",
        market="TW",
        start=date(2021, 2, 1),
        end=date(2021, 2, 26),
    )
    assert report.unexpected == (date(2021, 2, 8),)
    assert report.is_complete is False
