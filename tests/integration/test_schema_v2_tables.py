"""ADR-0027: the schema v2 tables (the universe loader is Step 38-a's `test_v2_listings`)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

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


def test_a_value_row_is_append_only(db: Connection, tmp_path) -> None:
    fetch_id = _fetch(db, tmp_path)
    db.execute(
        sa.text(
            "INSERT INTO stocks (stock_id, name, fetch_id) "
            "VALUES ('1101', '台泥', :f)"
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
            "INSERT INTO stocks (stock_id, name, fetch_id) "
            "VALUES ('1101', '台泥', :f)"
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


def test_a_price_beyond_eight_integer_digits_is_refused(db: Connection, tmp_path) -> None:
    fetch_id = _fetch(db, tmp_path)
    db.execute(
        sa.text(
            "INSERT INTO stocks (stock_id, name, fetch_id) "
            "VALUES ('1101', '台泥', :f)"
        ),
        {"f": fetch_id},
    )
    with pytest.raises(DBAPIError):
        _price(db, fetch_id, close=123456789.0)  # 9 integer digits


def test_a_third_decimal_is_refused_not_rounded(db: Connection, tmp_path) -> None:
    """numeric(10, 2) would store a published 30.555 as 30.56 without a word."""
    fetch_id = _fetch(db, tmp_path)
    db.execute(
        sa.text(
            "INSERT INTO stocks (stock_id, name, fetch_id) "
            "VALUES ('1101', '台泥', :f)"
        ),
        {"f": fetch_id},
    )
    savepoint = db.begin_nested()
    with pytest.raises(IntegrityError, match="close_price_precision"):
        _price(db, fetch_id, close="30.555")
    savepoint.rollback()
    _price(db, fetch_id, close="30.55")
    assert db.scalar(sa.text("SELECT close_price::text FROM daily_prices")) == "30.55"


def test_trailing_zeros_are_not_extra_decimals(db: Connection, tmp_path) -> None:
    """v1 stored prices as numeric(20, 6): 30.500000 is 30.5, and must be accepted."""
    fetch_id = _fetch(db, tmp_path)
    db.execute(
        sa.text(
            "INSERT INTO stocks (stock_id, name, fetch_id) "
            "VALUES ('1101', '台泥', :f)"
        ),
        {"f": fetch_id},
    )
    _price(db, fetch_id, close="30.500000")
    assert db.scalar(sa.text("SELECT close_price = 30.5 FROM daily_prices"))
