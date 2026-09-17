"""Step 19-c — the corporate-action import path: registration, per-row detail
fetches before the write transaction, and retraction of a row that dropped
out of a range's covered window."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.ingestion.adapters import TWSEExRightAdapter
from stock_data_center.ingestion.corporate_action import CorporateActionImporter
from stock_data_center.ingestion.models import (
    CorporateActionRangeRequest,
    FetchedArtifact,
    ResourceQuarantinedError,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_bytes())


def _twt49u(rows: list[list[str]], *, start: str, end: str) -> bytes:
    payload = _load("twse_twt49u_2024.json")
    payload["data"] = rows
    payload["strDate"] = start
    payload["endDate"] = end
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


_ALL_2024 = _load("twse_twt49u_2024.json")["data"]


def _row(code: str, event_date: str) -> list[str]:
    return next(
        r for r in _ALL_2024 if r[1].strip() == code and r[0] == event_date
    )


ROW_2454 = _row("2454", "113年01月04日")  # 息 (ex_dividend)
ROW_6442 = _row("6442", "113年01月10日")  # 權 (ex_right)
ROW_2543 = _row("2543", "113年05月30日")  # 權息 (ex_right_dividend)

DETAIL_2454 = (FIXTURES / "twse_detail_49_2454_20240104.json").read_bytes()
DETAIL_6442 = (FIXTURES / "twse_detail_49_6442_20240110.json").read_bytes()
DETAIL_2543 = (FIXTURES / "twse_detail_49_2543_20240530.json").read_bytes()
DETAIL_EMPTY = (FIXTURES / "twse_detail_empty.json").read_bytes()

FULL_YEAR = _twt49u(
    [ROW_2454, ROW_6442, ROW_2543], start="20240101", end="20241231"
)
JANUARY_ONLY = _twt49u([ROW_2454, ROW_6442], start="20240101", end="20240131")
JANUARY_WITHOUT_6442 = _twt49u([ROW_2454], start="20240101", end="20240131")


class DispatchFetcher:
    """Answers each resource by its `resource_key`, list and details alike."""

    def __init__(self, content_by_key: dict[str, bytes]) -> None:
        self._content = content_by_key
        self.calls: list[str] = []

    def fetch(self, resource: SourceResource) -> FetchedArtifact:
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


def _detail_key(code: str, locator_date: str) -> str:
    return f"twse_twt49u:detail:{code}:TWT49U:{locator_date}"


DETAILS_2024 = {
    _detail_key("2454", "20240104"): DETAIL_2454,
    _detail_key("6442", "20240110"): DETAIL_6442,
    _detail_key("2543", "20240530"): DETAIL_2543,
}


def run_range(
    engine, tmp_path, *, content: bytes, details: dict[str, bytes],
    start: date, end: date, executed_through: date,
    import_id=None, purpose=IngestPurpose.FIRST_CAPTURE,
):
    list_key = f"twse_twt49u:{start.isoformat()}:{end.isoformat()}"
    fetcher = DispatchFetcher({list_key: content, **details})
    importer = CorporateActionImporter(
        engine, raw_store=LocalRawArtifactStore(tmp_path / "raw"), fetcher=fetcher,
        sleep=lambda _seconds: None,
    )
    used_id = import_id or uuid4()
    result = importer.run(
        adapter=TWSEExRightAdapter(),
        request=CorporateActionRangeRequest(start, end, executed_through),
        import_id=used_id,
        git_commit="test-commit",
        purpose=purpose,
    )
    with engine.connect() as connection:
        manifest = importer.manifest(connection, used_id)
    return result, manifest, fetcher


def test_a_range_registers_events_and_completes_them_from_their_details(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        result, manifest, fetcher = run_range(
            engine, tmp_path, content=FULL_YEAR, details=DETAILS_2024,
            start=date(2024, 1, 1), end=date(2024, 12, 31),
            executed_through=date(2024, 12, 31),
        )
        assert manifest.status == "succeeded"
        assert result.normalized_rows == 3
        assert result.business_versions_created == 3
        # The list fetch plus the two dividend/reduction-style detail fetches
        # this feed's rows need; 6442 and 2543 both publish on TWT49UDetail.
        assert set(fetcher.calls) == {
            "twse_twt49u:2024-01-01:2024-12-31",
            *DETAILS_2024.keys(),
        }
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM corporate_action_events "
                        "WHERE source = 'twse_twt49u'")
            ) == 3
            row = connection.execute(
                sa.text(
                    "SELECT v.action_type, v.cash_dividend_per_share, "
                    "v.free_share_ratio, v.rights_ratio, v.subscription_price "
                    "FROM corporate_action_versions v "
                    "JOIN corporate_action_events e ON e.id = v.event_id "
                    "WHERE e.source_event_key = 'TWT49U:20240530'"
                )
            ).one()
            assert row.action_type == "ex_right_dividend"
            assert float(row.cash_dividend_per_share) == 0.4
            assert float(row.free_share_ratio) == 0.14
            assert float(row.rights_ratio) == 0.20211906001
            assert float(row.subscription_price) == 33
            evidence_types = set(
                connection.scalars(
                    sa.text(
                        "SELECT evidence_type FROM publication_evidence "
                        "WHERE dataset_code = 'corporate_action'"
                    )
                )
            )
            assert evidence_types == {"capture_bound"}
    finally:
        engine.dispose()


def test_rerunning_the_same_range_creates_no_revision(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run_range(
            engine, tmp_path, content=FULL_YEAR, details=DETAILS_2024,
            start=date(2024, 1, 1), end=date(2024, 12, 31),
            executed_through=date(2024, 12, 31),
        )
        repeated, _, _ = run_range(
            engine, tmp_path, content=FULL_YEAR, details=DETAILS_2024,
            start=date(2024, 1, 1), end=date(2024, 12, 31),
            executed_through=date(2024, 12, 31),
        )
        assert repeated.business_versions_created == 0
        assert repeated.business_versions_deduplicated == 3
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM corporate_action_versions")
            ) == 3
            # The same bytes twice: no absence was ever proven.
            assert connection.scalar(
                sa.text("SELECT count(*) FROM corporate_action_retractions")
            ) == 0
    finally:
        engine.dispose()


def test_a_corrected_detail_creates_a_new_revision_of_the_same_event(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run_range(
            engine, tmp_path, content=FULL_YEAR, details=DETAILS_2024,
            start=date(2024, 1, 1), end=date(2024, 12, 31),
            executed_through=date(2024, 12, 31),
        )
        corrected_detail = json.loads(DETAIL_2454)
        corrected_detail["data"][0][2] = "24.7 元／股"
        corrected = {
            **DETAILS_2024,
            _detail_key("2454", "20240104"): json.dumps(
                corrected_detail, ensure_ascii=False
            ).encode("utf-8"),
        }
        result, _, _ = run_range(
            engine, tmp_path, content=FULL_YEAR, details=corrected,
            start=date(2024, 1, 1), end=date(2024, 12, 31),
            executed_through=date(2024, 12, 31),
        )
        assert result.business_versions_created == 1
        assert result.business_versions_deduplicated == 2
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM corporate_action_events e "
                    "JOIN corporate_action_versions v ON v.event_id = e.id "
                    "WHERE e.source_event_key = 'TWT49U:20240104'"
                )
            ) == 2
    finally:
        engine.dispose()


def test_a_row_dropped_from_the_covered_range_is_retracted_not_deleted(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        run_range(
            engine, tmp_path, content=JANUARY_ONLY,
            details={k: v for k, v in DETAILS_2024.items() if "2543" not in k},
            start=date(2024, 1, 1), end=date(2024, 1, 31),
            executed_through=date(2024, 1, 31),
        )
        with engine.connect() as connection:
            before = dict(
                connection.execute(
                    sa.text(
                        "SELECT e.source_event_key, v.business_content_hash "
                        "FROM corporate_action_versions v "
                        "JOIN corporate_action_events e ON e.id = v.event_id"
                    )
                ).all()
            )
        result, _, _ = run_range(
            engine, tmp_path, content=JANUARY_WITHOUT_6442,
            details={_detail_key("2454", "20240104"): DETAIL_2454},
            start=date(2024, 1, 1), end=date(2024, 1, 31),
            executed_through=date(2024, 1, 31),
        )
        assert result.business_versions_created == 0
        with engine.connect() as connection:
            after = dict(
                connection.execute(
                    sa.text(
                        "SELECT e.source_event_key, v.business_content_hash "
                        "FROM corporate_action_versions v "
                        "JOIN corporate_action_events e ON e.id = v.event_id"
                    )
                ).all()
            )
            # Nothing was updated or deleted — same rows, same hashes.
            assert after == before
            retraction = connection.execute(
                sa.text(
                    "SELECT r.reason FROM corporate_action_retractions r "
                    "JOIN corporate_action_events e ON e.id = r.event_id "
                    "WHERE e.source_event_key = 'TWT49U:20240110'"
                )
            ).one()
            assert retraction.reason == "absent_from_covered_range"
            assert connection.scalar(
                sa.text("SELECT count(*) FROM corporate_action_retractions")
            ) == 1
    finally:
        engine.dispose()


def test_a_row_outside_the_covered_window_is_never_retracted(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """2543 (May) is absent from a January-only rerun, but a January window
    never covered it, so its absence proves nothing about it."""
    engine = sa.create_engine(isolated_database_url)
    try:
        run_range(
            engine, tmp_path, content=FULL_YEAR, details=DETAILS_2024,
            start=date(2024, 1, 1), end=date(2024, 12, 31),
            executed_through=date(2024, 12, 31),
        )
        run_range(
            engine, tmp_path, content=JANUARY_ONLY,
            details={k: v for k, v in DETAILS_2024.items() if "2543" not in k},
            start=date(2024, 1, 1), end=date(2024, 1, 31),
            executed_through=date(2024, 1, 31),
        )
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM corporate_action_retractions")
            ) == 0
    finally:
        engine.dispose()


def test_a_failing_detail_quarantines_the_range_and_keeps_raw_artifacts(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        details = {**DETAILS_2024, _detail_key("2454", "20240104"): DETAIL_EMPTY}
        with pytest.raises(ResourceQuarantinedError, match="no_data_for_date"):
            run_range(
                engine, tmp_path, content=FULL_YEAR, details=details,
                start=date(2024, 1, 1), end=date(2024, 12, 31),
                executed_through=date(2024, 12, 31),
            )
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM corporate_action_versions")
            ) == 0
            assert connection.scalar(
                sa.text("SELECT count(*) FROM corporate_action_events")
            ) == 0
            assert connection.scalar(
                sa.text("SELECT count(*) FROM raw_artifacts")
            ) >= 2
            assert connection.scalar(
                sa.text("SELECT reason_code FROM import_quarantine")
            ) == "no_data_for_date"
    finally:
        engine.dispose()


def test_detail_fetches_are_throttled_but_not_before_the_first(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """TWT49U alone can need thousands of these for one range (ADR-0019)."""
    engine = sa.create_engine(isolated_database_url)
    try:
        list_key = "twse_twt49u:2024-01-01:2024-12-31"
        fetcher = DispatchFetcher({list_key: FULL_YEAR, **DETAILS_2024})
        sleeps: list[float] = []
        importer = CorporateActionImporter(
            engine, raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=fetcher, min_detail_interval_seconds=2.5,
            sleep=sleeps.append,
        )
        importer.run(
            adapter=TWSEExRightAdapter(),
            request=CorporateActionRangeRequest(
                date(2024, 1, 1), date(2024, 12, 31), date(2024, 12, 31)
            ),
            import_id=uuid4(),
            git_commit="test-commit",
            purpose=IngestPurpose.FIRST_CAPTURE,
        )
        # Three distinct detail locators, so two waits between three fetches.
        assert sleeps == [2.5, 2.5]
    finally:
        engine.dispose()


def test_a_fully_resumed_run_makes_no_detail_fetches_or_waits(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Politeness is owed to the source, not to the checkpoint table: a run
    resumed under its original import id must not wait for requests it never
    makes."""
    engine = sa.create_engine(isolated_database_url)
    try:
        import_id = uuid4()
        run_range(
            engine, tmp_path, content=FULL_YEAR, details=DETAILS_2024,
            start=date(2024, 1, 1), end=date(2024, 12, 31),
            executed_through=date(2024, 12, 31), import_id=import_id,
        )
        list_key = "twse_twt49u:2024-01-01:2024-12-31"
        fetcher = DispatchFetcher({list_key: FULL_YEAR, **DETAILS_2024})
        sleeps: list[float] = []
        importer = CorporateActionImporter(
            engine, raw_store=LocalRawArtifactStore(tmp_path / "raw"),
            fetcher=fetcher, min_detail_interval_seconds=2.5, sleep=sleeps.append,
        )
        result = importer.run(
            adapter=TWSEExRightAdapter(),
            request=CorporateActionRangeRequest(
                date(2024, 1, 1), date(2024, 12, 31), date(2024, 12, 31)
            ),
            import_id=import_id,
            git_commit="test-commit",
            purpose=IngestPurpose.FIRST_CAPTURE,
        )
        assert result.resumed_from_checkpoint is True
        assert fetcher.calls == []
        assert sleeps == []
    finally:
        engine.dispose()
