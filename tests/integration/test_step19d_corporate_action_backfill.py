"""Step 19-d — walking a corporate-action result feed's range year by year."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.ingestion.adapters import TWSEParValueChangeAdapter
from stock_data_center.ingestion.backfill import (
    CorporateActionBackfill,
    default_base_import_id,
    year_import_id,
)
from stock_data_center.ingestion.corporate_action import CorporateActionImporter
from stock_data_center.ingestion.models import FetchedArtifact
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWTB8U_2025 = json.loads((FIXTURES / "twse_twtb8u_2025.json").read_bytes())
TWTB8U_2025_ROWS = len(TWTB8U_2025["data"])


def _year_payload(year: int, *, rows: list | None = None) -> bytes:
    payload = json.loads((FIXTURES / "twse_twtb8u_2023_empty.json").read_bytes())
    payload["params"]["startDate"] = f"{year}0101"
    payload["params"]["endDate"] = f"{year}1231"
    if rows is not None:
        payload["data"] = rows
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


EMPTY_2024 = _year_payload(2024)
FULL_2025 = _year_payload(2025, rows=TWTB8U_2025["data"])


class DispatchFetcher:
    def __init__(self, content_by_key: dict[str, bytes]) -> None:
        self._content = content_by_key
        self.calls: list[str] = []

    def fetch(self, resource):
        self.calls.append(resource.resource_key)
        try:
            content = self._content[resource.resource_key]
        except KeyError:
            raise AssertionError(
                f"unexpected fetch for {resource.resource_key!r}"
            ) from None
        return FetchedArtifact(
            content=content,
            source_uri=resource.source_uri,
            fetched_at=datetime.now(UTC),
            media_type="application/json",
        )


def run_backfill(
    engine, tmp_path, *, content_by_key, start, end, base_import_id=None,
    sleep=None, min_interval_seconds=1.5,
):
    fetcher = DispatchFetcher(content_by_key)
    importer = CorporateActionImporter(
        engine, raw_store=LocalRawArtifactStore(tmp_path / "raw"), fetcher=fetcher,
        sleep=lambda _seconds: None,
    )
    backfill = CorporateActionBackfill(importer, sleep=sleep or (lambda _s: None))
    report = backfill.run(
        adapter=TWSEParValueChangeAdapter(),
        start=start,
        end=end,
        base_import_id=base_import_id or uuid4(),
        purpose=IngestPurpose.FIRST_CAPTURE,
        min_interval_seconds=min_interval_seconds,
    )
    return report, fetcher


def test_the_runner_requests_one_year_at_a_time(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        report, fetcher = run_backfill(
            engine, tmp_path,
            content_by_key={
                "twse_twtb8u:2024-01-01:2024-12-31": EMPTY_2024,
                "twse_twtb8u:2025-01-01:2025-12-31": FULL_2025,
            },
            start=date(2024, 1, 1), end=date(2025, 12, 31),
        )
        assert fetcher.calls == [
            "twse_twtb8u:2024-01-01:2024-12-31",
            "twse_twtb8u:2025-01-01:2025-12-31",
        ]
        assert [item.year for item in report.results] == [2024, 2025]
        assert report.imported == 2
        assert report.failed == 0
        assert report.rows == TWTB8U_2025_ROWS
        assert report.is_complete is True
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM corporate_action_versions")
            ) == TWTB8U_2025_ROWS
    finally:
        engine.dispose()


def test_a_partial_final_year_is_clipped_to_the_requested_end(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """A 2020-2026 backfill's last year is not requested through 2026-12-31 —
    the window this step's ROADMAP entry names ends 2026-09-11."""
    engine = sa.create_engine(isolated_database_url)
    try:
        payload = _year_payload(2026)
        report, _ = run_backfill(
            engine, tmp_path,
            content_by_key={"twse_twtb8u:2026-01-01:2026-09-11": payload},
            start=date(2026, 1, 1), end=date(2026, 9, 11),
        )
        [result] = report.results
        assert result.start == date(2026, 1, 1)
        assert result.end == date(2026, 9, 11)
    finally:
        engine.dispose()


def test_the_runner_throttles_between_years_but_not_before_the_first(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        sleeps: list[float] = []
        run_backfill(
            engine, tmp_path,
            content_by_key={
                "twse_twtb8u:2024-01-01:2024-12-31": EMPTY_2024,
                "twse_twtb8u:2025-01-01:2025-12-31": FULL_2025,
            },
            start=date(2024, 1, 1), end=date(2025, 12, 31),
            sleep=sleeps.append, min_interval_seconds=3.0,
        )
        assert sleeps == [3.0]
    finally:
        engine.dispose()


def test_a_resumed_year_is_not_throttled(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        base = uuid4()
        run_backfill(
            engine, tmp_path,
            content_by_key={"twse_twtb8u:2024-01-01:2024-12-31": EMPTY_2024},
            start=date(2024, 1, 1), end=date(2024, 12, 31), base_import_id=base,
        )
        sleeps: list[float] = []
        report, fetcher = run_backfill(
            engine, tmp_path,
            content_by_key={
                "twse_twtb8u:2024-01-01:2024-12-31": EMPTY_2024,
                "twse_twtb8u:2025-01-01:2025-12-31": FULL_2025,
            },
            start=date(2024, 1, 1), end=date(2025, 12, 31), base_import_id=base,
            sleep=sleeps.append, min_interval_seconds=3.0,
        )
        assert report.resumed == 1
        assert report.imported == 1
        assert fetcher.calls == ["twse_twtb8u:2025-01-01:2025-12-31"]
        # The 2024 year resumed from checkpoint; nothing to wait for before
        # the one real request 2025 needed.
        assert sleeps == []
    finally:
        engine.dispose()


def test_a_bad_year_is_reported_not_fatal(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        bad = json.loads(EMPTY_2024)
        bad["stat"] = "error"
        report, fetcher = run_backfill(
            engine, tmp_path,
            content_by_key={
                "twse_twtb8u:2024-01-01:2024-12-31": json.dumps(bad).encode(),
                "twse_twtb8u:2025-01-01:2025-12-31": FULL_2025,
            },
            start=date(2024, 1, 1), end=date(2025, 12, 31),
        )
        assert fetcher.calls == [
            "twse_twtb8u:2024-01-01:2024-12-31",
            "twse_twtb8u:2025-01-01:2025-12-31",
        ]
        assert report.failed == 1
        assert report.imported == 1
        assert report.is_complete is False
        failure = next(item for item in report.results if item.status == "failed")
        assert failure.year == 2024
        assert failure.reason_code == "source_status"
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM corporate_action_versions")
            ) == TWTB8U_2025_ROWS
            assert connection.scalar(
                sa.text("SELECT count(*) FROM raw_artifacts")
            ) == 2
    finally:
        engine.dispose()


def test_year_import_id_is_derived_and_stable() -> None:
    base = default_base_import_id("twse_twtb8u", date(2020, 1, 1), date(2026, 9, 11))
    assert year_import_id(base, "twse_twtb8u", 2024) == year_import_id(
        default_base_import_id("twse_twtb8u", date(2020, 1, 1), date(2026, 9, 11)),
        "twse_twtb8u", 2024,
    )
    assert year_import_id(base, "twse_twtb8u", 2024) != year_import_id(
        base, "twse_twtb8u", 2025
    )
