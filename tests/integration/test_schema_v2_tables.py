"""ADR-0027: the schema v2 tables and the universe loader."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from stock_data_center.v2.fetch_log import FetchRecord, record_fetch
from stock_data_center.v2.universe import load_universe

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v2"
NOW = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)


def _pages(url: str) -> bytes:
    market = "sii" if url.endswith("strMode=2") else "otc"
    return (FIXTURES / f"isin_{market}_excerpt.html").read_bytes()


def _fetch(db: Connection, tmp_path, **overrides) -> object:
    from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore

    record = FetchRecord(
        dataset="daily_price",
        source="twse_mi_index",
        resource_key="twse_mi_index:2026-09-11",
        source_uri="https://example.test/mi",
        purpose="first_capture",
        adapter_version="test:v1",
        git_commit="abc",
        fetched_at=NOW,
    )
    return record_fetch(
        db,
        record,
        content=overrides.pop("content", b"raw"),
        status=overrides.pop("status", "succeeded"),
        store=LocalRawArtifactStore(tmp_path),
        **overrides,
    )


def _price(db: Connection, fetch_id, **values) -> None:
    db.execute(
        sa.text(
            "INSERT INTO daily_prices (stock_id, source, trade_date, close_price, fetch_id) "
            "VALUES (:stock, 'twse_mi_index', :day, :close, :fetch)"
        ),
        {"stock": "1101", "day": date(2026, 9, 11), "close": 30.5, "fetch": fetch_id, **values},
    )


def test_the_loader_keeps_the_raw_pages_and_fills_stocks(db: Connection, tmp_path) -> None:
    from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore

    store = LocalRawArtifactStore(tmp_path)
    counts = load_universe(db, git_commit="abc", get=_pages, now=lambda: NOW, store=store)
    assert counts == {"sii": 3, "otc": 3}
    fetched = db.execute(
        sa.text("SELECT status, length(sha256) FROM fetches WHERE dataset = 'stocks'")
    ).all()
    assert fetched == [("succeeded", 32), ("succeeded", 32)]
    assert len([p for p in tmp_path.rglob("*") if p.is_file()]) == 2  # raw pages kept

    # A second load refreshes in place: same stocks, no duplicates.
    load_universe(db, git_commit="abc", get=_pages, now=lambda: NOW, store=store)
    assert db.scalar(sa.text("SELECT count(*) FROM stocks")) == 6


def test_a_value_row_is_append_only(db: Connection, tmp_path) -> None:
    fetch_id = _fetch(db, tmp_path)
    db.execute(
        sa.text(
            "INSERT INTO stocks (stock_id, name, market, fetch_id) "
            "VALUES ('1101', '台泥', 'sii', :f)"
        ),
        {"f": fetch_id},
    )
    _price(db, fetch_id)
    for statement in (
        "UPDATE daily_prices SET close_price = 31",
        "DELETE FROM daily_prices",
        "TRUNCATE daily_prices",
        "UPDATE fetches SET status = 'failed'",
    ):
        savepoint = db.begin_nested()
        with pytest.raises(DBAPIError):
            db.execute(sa.text(statement))
        savepoint.rollback()


def test_a_correction_is_a_second_row_not_an_edit(db: Connection, tmp_path) -> None:
    fetch_id = _fetch(db, tmp_path)
    db.execute(
        sa.text(
            "INSERT INTO stocks (stock_id, name, market, fetch_id) "
            "VALUES ('1101', '台泥', 'sii', :f)"
        ),
        {"f": fetch_id},
    )
    _price(db, fetch_id)
    # A later write is a later statement, so it gets a later recorded_at.
    db.execute(sa.text("SELECT pg_sleep(0.01)"))
    _price(db, fetch_id, close=31.0)
    closes = db.scalars(
        sa.text("SELECT close_price FROM daily_prices ORDER BY recorded_at")
    ).all()
    assert [float(close) for close in closes] == [30.5, 31.0]


def test_a_value_must_name_a_known_stock_and_fetch(db: Connection, tmp_path) -> None:
    fetch_id = _fetch(db, tmp_path)
    with pytest.raises(IntegrityError):
        _price(db, fetch_id)  # 1101 is not in stocks


def test_a_successful_fetch_must_name_its_raw_file(db: Connection, tmp_path) -> None:
    with pytest.raises(IntegrityError):
        _fetch(db, tmp_path, content=None, status="succeeded")


def test_a_failed_fetch_needs_no_raw_file(db: Connection, tmp_path) -> None:
    fetch_id = _fetch(db, tmp_path, content=None, status="failed", reason_code="timeout")
    assert db.scalar(
        sa.text("SELECT sha256 IS NULL FROM fetches WHERE id = :f"), {"f": fetch_id}
    )


def test_prices_keep_two_decimals_and_no_more(db: Connection, tmp_path) -> None:
    fetch_id = _fetch(db, tmp_path)
    db.execute(
        sa.text(
            "INSERT INTO stocks (stock_id, name, market, fetch_id) "
            "VALUES ('1101', '台泥', 'sii', :f)"
        ),
        {"f": fetch_id},
    )
    with pytest.raises(DBAPIError):
        _price(db, fetch_id, close=123456789.0)  # beyond numeric(10,2)
