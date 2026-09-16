"""Step 17-c — walking a date range, and reporting what it did and did not get."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.coverage import CoverageValidator, securities_without_metadata
from stock_data_center.ingestion.adapters import TWSEWholeMarketDailyAdapter
from stock_data_center.ingestion.backfill import WholeMarketDailyBackfill
from stock_data_center.ingestion.models import FetchedArtifact
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.ingestion.whole_market_daily import WholeMarketDailyImporter
from stock_data_center.market_calendar import (
    CalendarCoverageError,
    TradingCalendarObservation,
    TradingCalendarWriter,
)
from stock_data_center.market_calendar.models import CalendarLineageRef
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE_BYTES = (FIXTURES / "twse_mi_index_allbut0999_20260911.json").read_bytes()
TWSE_CLOSED = (FIXTURES / "twse_mi_index_allbut0999_20240724_closed.json").read_bytes()
TWSE_ROWS = 1379

# 2026-09-09, 09-10, 09-11 open; 09-12 is a closure the calendar knows about.
OPEN_DAYS = (date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11))


class DateFetcher:
    """Serves the captured file, restamped for whichever date was asked for."""

    def __init__(self, closed: set[date] | None = None) -> None:
        self.closed = closed or set()
        self.fetched: list[date] = []

    def fetch(self, resource):
        requested = date.fromisoformat(resource.resource_key.split(":")[-1])
        self.fetched.append(requested)
        if requested in self.closed:
            content = TWSE_CLOSED
        else:
            payload = json.loads(TWSE_BYTES)
            payload["date"] = requested.strftime("%Y%m%d")
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return FetchedArtifact(
            content=content,
            source_uri=resource.source_uri,
            fetched_at=datetime.now(UTC),
            media_type="application/json",
        )


class RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def store_calendar(connection, days=OPEN_DAYS) -> None:
    run_id = connection.scalar(
        sa.text(
            "INSERT INTO ingest_runs (dataset_code, source, status, started_at, "
            " purpose) VALUES ('trading_calendar', 'twse', 'succeeded', now(), "
            "'gap_fill') RETURNING id"
        )
    )
    artifact_id = connection.scalar(
        sa.text(
            "INSERT INTO raw_artifacts (raw_artifact_hash, storage_uri, byte_size, "
            " media_type) VALUES (:h, :uri, 1, 'application/json') RETURNING id"
        ),
        {"h": "a" * 64, "uri": f"file://{'a' * 64}"},
    )
    connection.execute(
        sa.text(
            "INSERT INTO raw_artifact_observations (raw_artifact_id, ingest_run_id, "
            " source_uri, fetched_at, artifact_origin) "
            "VALUES (:a, :r, 'https://x', now(), 'official_fetch')"
        ),
        {"a": artifact_id, "r": run_id},
    )
    TradingCalendarWriter().append_month(
        connection,
        source="twse",
        observation=TradingCalendarObservation(
            market="TWSE",
            calendar_month=date(2026, 9, 1),
            trading_days=tuple(days),
            coverage_through=date(2026, 9, 30),
        ),
        lineage=CalendarLineageRef(artifact_id, run_id),
    )


def backfill(engine, tmp_path, *, fetcher, sleep=None):
    return WholeMarketDailyBackfill(
        WholeMarketDailyImporter(
            engine,
            raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=fetcher,
        ),
        sleep=sleep or RecordingSleep(),
    )


def test_the_runner_requests_only_the_days_the_calendar_says_were_open(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """A closure is not a gap, and asking for it would quarantine a benign date."""
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            store_calendar(connection)
        fetcher = DateFetcher()
        report = backfill(engine, tmp_path, fetcher=fetcher).run(
            adapter=TWSEWholeMarketDailyAdapter(),
            start=date(2026, 9, 8),
            end=date(2026, 9, 14),
            base_import_id=uuid4(),
            purpose=IngestPurpose.GAP_FILL,
        )
        assert fetcher.fetched == list(OPEN_DAYS)
        assert report.requested == OPEN_DAYS
        assert report.non_trading_days == (
            date(2026, 9, 8),
            date(2026, 9, 12),
            date(2026, 9, 13),
            date(2026, 9, 14),
        )
        assert report.imported == 3
        assert report.failed == 0
        assert report.rows == 3 * TWSE_ROWS
    finally:
        engine.dispose()


def test_the_runner_refuses_a_range_the_calendar_does_not_cover(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Fail before 3,300 requests, not after: an unimported month and a month
    of closures look identical in the data."""
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            store_calendar(connection)
        fetcher = DateFetcher()
        with pytest.raises(CalendarCoverageError):
            backfill(engine, tmp_path, fetcher=fetcher).run(
                adapter=TWSEWholeMarketDailyAdapter(),
                start=date(2026, 9, 9),
                end=date(2026, 10, 15),
                base_import_id=uuid4(),
                purpose=IngestPurpose.GAP_FILL,
            )
        assert fetcher.fetched == []
    finally:
        engine.dispose()


def test_the_runner_throttles_between_dates_but_not_before_the_first(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            store_calendar(connection)
        sleep = RecordingSleep()
        backfill(engine, tmp_path, fetcher=DateFetcher(), sleep=sleep).run(
            adapter=TWSEWholeMarketDailyAdapter(),
            start=date(2026, 9, 9),
            end=date(2026, 9, 11),
            base_import_id=uuid4(),
            purpose=IngestPurpose.GAP_FILL,
            min_interval_seconds=1.5,
        )
        assert sleep.calls == [1.5, 1.5]
    finally:
        engine.dispose()


def test_a_resumed_run_continues_instead_of_refetching(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Each date gets its own import id, derived from the run's, so a run that
    dies midway resumes date by date rather than starting over."""
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            store_calendar(connection)
        base = uuid4()
        first = DateFetcher()
        backfill(engine, tmp_path, fetcher=first).run(
            adapter=TWSEWholeMarketDailyAdapter(),
            start=date(2026, 9, 9),
            end=date(2026, 9, 10),
            base_import_id=base,
            purpose=IngestPurpose.GAP_FILL,
        )
        assert first.fetched == [date(2026, 9, 9), date(2026, 9, 10)]

        second = DateFetcher()
        report = backfill(engine, tmp_path, fetcher=second).run(
            adapter=TWSEWholeMarketDailyAdapter(),
            start=date(2026, 9, 9),
            end=date(2026, 9, 11),
            base_import_id=base,
            purpose=IngestPurpose.GAP_FILL,
        )
        # The two finished dates are not fetched again; only the new one is.
        assert second.fetched == [date(2026, 9, 11)]
        assert report.resumed == 2
        assert report.imported == 1
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM daily_price_versions")
            ) == 3 * TWSE_ROWS
    finally:
        engine.dispose()


def test_a_date_the_source_has_no_data_for_is_reported_not_fatal(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """One bad date must not end a 3,300-request run, and it must be visible."""
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            store_calendar(connection)
        fetcher = DateFetcher(closed={date(2026, 9, 10)})
        report = backfill(engine, tmp_path, fetcher=fetcher).run(
            adapter=TWSEWholeMarketDailyAdapter(),
            start=date(2026, 9, 9),
            end=date(2026, 9, 11),
            base_import_id=uuid4(),
            purpose=IngestPurpose.GAP_FILL,
        )
        assert fetcher.fetched == list(OPEN_DAYS)
        assert report.imported == 2
        assert report.failed == 1
        failure = next(item for item in report.results if item.status == "failed")
        assert failure.trade_date == date(2026, 9, 10)
        assert failure.reason_code == "no_data_for_date"
        assert report.is_complete is False
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM daily_price_versions")
            ) == 2 * TWSE_ROWS
    finally:
        engine.dispose()


def test_the_gap_report_is_the_step_16_coverage_validator(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Every trading date is imported or reported as a gap — by the validator
    Step 16 built for it, not by a second implementation."""
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            store_calendar(connection)
        backfill(engine, tmp_path, fetcher=DateFetcher(closed={date(2026, 9, 10)})).run(
            adapter=TWSEWholeMarketDailyAdapter(),
            start=date(2026, 9, 9),
            end=date(2026, 9, 11),
            base_import_id=uuid4(),
            purpose=IngestPurpose.GAP_FILL,
        )
        with engine.connect() as connection:
            coverage = CoverageValidator().report(
                connection,
                dataset_code="daily_price",
                market="TWSE",
                start=date(2026, 9, 9),
                end=date(2026, 9, 11),
            )
        assert coverage.observed == (date(2026, 9, 9), date(2026, 9, 11))
        assert coverage.missing == (date(2026, 9, 10),)
        assert coverage.is_complete is False
    finally:
        engine.dispose()


def test_priced_securities_with_no_metadata_row_are_reported(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """ETFs, TDRs and preferred shares are priced by the whole-market feed but
    absent from the company snapshots Step 10 imports."""
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            store_calendar(connection)
        backfill(engine, tmp_path, fetcher=DateFetcher()).run(
            adapter=TWSEWholeMarketDailyAdapter(),
            start=date(2026, 9, 11),
            end=date(2026, 9, 11),
            base_import_id=uuid4(),
            purpose=IngestPurpose.GAP_FILL,
        )
        with engine.connect() as connection:
            unknown = securities_without_metadata(
                connection, source="twse_mi_index"
            )
        # No metadata has been imported at all here, so every priced security
        # is reported; the point is the shape and the ordering.
        assert len(unknown) == TWSE_ROWS
        codes = [item.security_code for item in unknown]
        assert codes == sorted(codes)
        first = unknown[0]
        assert first.first_priced_on == date(2026, 9, 11)
        assert first.last_priced_on == date(2026, 9, 11)
        assert first.priced_days == 1
    finally:
        engine.dispose()


def test_a_tpex_run_follows_the_calendar_its_declaration_names(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """No official TPEx calendar exists, so TPEx datasets declare the TWSE one.

    Step 16 measured the equivalence and put `calendar_market` on the
    declaration for exactly this. Asking for a `TPEx` calendar finds nothing and
    fails the run before it starts — which is what happened on the first live
    backfill, on a path every TWSE test covers identically and none of them
    could see.
    """
    from stock_data_center.ingestion.adapters import TPExWholeMarketDailyAdapter

    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            store_calendar(connection)
        fetcher = DateFetcher()
        report = WholeMarketDailyBackfill(
            WholeMarketDailyImporter(
                engine,
                raw_store=LocalRawArtifactStore(tmp_path / "raw"),
                fetcher=fetcher,
            ),
            sleep=RecordingSleep(),
        ).run(
            adapter=TPExWholeMarketDailyAdapter(),
            start=date(2026, 9, 9),
            end=date(2026, 9, 11),
            base_import_id=uuid4(),
            purpose=IngestPurpose.GAP_FILL,
        )
        assert report.market == "TPEx"
        assert report.calendar_market == "TWSE"
        assert report.requested == OPEN_DAYS
        assert fetcher.fetched == list(OPEN_DAYS)
    finally:
        engine.dispose()


def test_a_resumed_date_is_not_throttled(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Politeness is owed to the source, not to the checkpoint table.

    A resumed date makes no request, so sleeping before the next one buys
    nothing. It costs a great deal: re-running the window to retry a handful of
    failed dates would otherwise sleep once per already-finished date — over
    half an hour of waiting to make eight requests.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            store_calendar(connection)
        base = uuid4()
        backfill(engine, tmp_path, fetcher=DateFetcher()).run(
            adapter=TWSEWholeMarketDailyAdapter(),
            start=date(2026, 9, 9),
            end=date(2026, 9, 10),
            base_import_id=base,
            purpose=IngestPurpose.GAP_FILL,
        )

        sleep = RecordingSleep()
        fetcher = DateFetcher()
        report = backfill(engine, tmp_path, fetcher=fetcher, sleep=sleep).run(
            adapter=TWSEWholeMarketDailyAdapter(),
            start=date(2026, 9, 9),
            end=date(2026, 9, 11),
            base_import_id=base,
            purpose=IngestPurpose.GAP_FILL,
            min_interval_seconds=1.5,
        )
        assert report.resumed == 2
        assert fetcher.fetched == [date(2026, 9, 11)]
        # Two resumed dates, then one real fetch: nothing to wait for.
        assert sleep.calls == []
    finally:
        engine.dispose()
