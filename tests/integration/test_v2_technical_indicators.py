"""Step 35-c-4: `technical_indicators_pit:v1` computed on demand from v2 `daily_prices`.

The PIT reference for the stored `technical_indicators:v1` (Step 26-a); it
stores nothing. The rolling series
computes observation date D at D's own release instant; every other PIT
context is `compute`, and the two must agree. Visibility is the one
`exchange_daily.visible` defines: the rule instant for a key's settled value,
`recorded_at` for a correction.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.schema_v2 import daily_prices
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import derived
from stock_data_center.v2 import exchange_daily as xd
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch
from stock_data_center.v2.indicators import METRIC_CODES, DailyBar, technical_indicators

pytestmark = pytest.mark.integration

STOCK = "2330"
SOURCE = "twse_mi_index"
SERVICE = derived.TechnicalIndicators(git_commit="abc")
# Seeded rows are recorded at their own rule instant, as a first capture
# would record them; a correction is recorded later. The knowledge cutoff has to
# be after now, when the backfilled rows of some tests are recorded.
KNOWLEDGE = datetime.now(UTC) + timedelta(days=1)
WEEK = {
    date(2024, 7, 1): "10",
    date(2024, 7, 2): "12",
    date(2024, 7, 3): "14",
    date(2024, 7, 4): "16",
    date(2024, 7, 5): "18",
}


@pytest.fixture
def fetch_id(db: Connection, tmp_path):
    fetch = record_fetch(
        db,
        FetchRecord("daily_price", SOURCE, "t", None, "gap_fill", "t", "abc", datetime.now(UTC)),
        content=b"t", status="succeeded", store=LocalRawArtifactStore(tmp_path),
    )
    for stock_id, market in ((STOCK, "sii"), ("6446", "otc")):
        db.execute(
            sa.text("INSERT INTO stocks (stock_id, name, market, fetch_id) "
                    "VALUES (:s, :n, :m, :f) ON CONFLICT DO NOTHING"),
            {"s": stock_id, "n": stock_id, "m": market, "f": fetch},
        )
    return fetch


def _price(db, fetch_id, day: date, close: str, *, recorded_at: datetime | None = None,
           source: str = SOURCE, stock_id: str = STOCK) -> None:
    values = {"stock_id": stock_id, "source": source, "trade_date": day, "open_price": close,
              "high_price": close, "low_price": close, "close_price": close, "volume": 1000,
              "fetch_id": fetch_id}
    if recorded_at is not None:
        values["recorded_at"] = recorded_at
    db.execute(sa.insert(daily_prices).values(values))


def _recorded(db, stock_id: str, source: str) -> datetime:
    return db.scalar(sa.text("SELECT min(recorded_at) FROM daily_prices "
                             "WHERE stock_id = :s AND source = :src"), {"s": stock_id, "src": source})


def _seed(db, fetch_id, closes=WEEK) -> None:
    for day, close in sorted(closes.items()):
        _price(db, fetch_id, day, close, recorded_at=xd.available_from(day))


def _rolling(db, end: date = date(2024, 7, 5), **kwargs):
    return SERVICE.rolling(db, stock_id=STOCK, start_date=date(2024, 7, 1), end_date=end,
                           source=SOURCE, knowledge_as_of=KNOWLEDGE, **kwargs)


def _at(db, day: date, information_as_of: datetime):
    rows = SERVICE.compute(db, stock_id=STOCK, start_date=day, end_date=day,
                           information_as_of=information_as_of, knowledge_as_of=KNOWLEDGE,
                           source=SOURCE)
    return rows[0] if rows else None


def _assert_rolling_equals_compute_at_each_cutoff(db, rows) -> None:
    for row in rows:
        assert _at(db, row.observation_date, xd.available_from(row.observation_date)) == row


def test_the_rolling_series_uses_each_date_own_release_cutoff(db, fetch_id) -> None:
    _seed(db, fetch_id)
    rows = _rolling(db)
    assert [row.observation_date for row in rows] == sorted(WEEK)
    for row in rows:
        # exchange_daily_settled@1: D's own close is public at 03:00 on D+1.
        assert row.information_as_of == xd.available_from(row.observation_date)
        assert row.knowledge_as_of == KNOWLEDGE
        assert (row.stock_id, row.source, row.git_commit) == (STOCK, SOURCE, "abc")
        assert (row.dataset_code, row.derivation_version) == ("technical_indicators_pit", "v1")
    assert [row.metrics["ma5"] for row in rows[:4]] == [None] * 4
    assert rows[4].metrics["ma5"] == pytest.approx(14)
    assert [row.input_count for row in rows] == [1, 2, 3, 4, 5]


def test_rolling_and_on_demand_agree_for_one_context(db, fetch_id) -> None:
    _seed(db, fetch_id)
    _assert_rolling_equals_compute_at_each_cutoff(db, _rolling(db))


def test_the_fingerprint_is_the_ordered_input_rows(db, fetch_id) -> None:
    _seed(db, fetch_id)
    stored = db.execute(sa.text(
        "SELECT trade_date, recorded_at FROM daily_prices ORDER BY trade_date")).all()
    expected = hashlib.sha256(
        ",".join(f"{day.isoformat()}@{at.astimezone(UTC).isoformat()}"
                 for day, at in stored).encode()
    ).hexdigest()
    assert _rolling(db)[-1].input_fingerprint == expected


def test_the_fingerprint_does_not_depend_on_the_session_time_zone(db, fetch_id) -> None:
    # psycopg returns timestamptz in the session's TimeZone (code review of #51).
    _seed(db, fetch_id)
    db.execute(sa.text("SET LOCAL TIME ZONE 'UTC'"))
    utc = _rolling(db)[-1].input_fingerprint
    db.execute(sa.text("SET LOCAL TIME ZONE 'Asia/Taipei'"))
    assert _rolling(db)[-1].input_fingerprint == utc


def test_a_later_correction_does_not_reach_back_into_an_earlier_date(db, fetch_id) -> None:
    _seed(db, fetch_id)
    corrected_at = xd.available_from(date(2024, 7, 20))
    _price(db, fetch_id, date(2024, 7, 1), "99", recorded_at=corrected_at)
    # The uncorrected week averages to 14; the correction would make it 31.8.
    assert _rolling(db)[-1].metrics["ma5"] == pytest.approx(14)
    assert _at(db, date(2024, 7, 5), corrected_at).metrics["ma5"] == pytest.approx(31.8)
    assert _at(db, date(2024, 7, 5), corrected_at - timedelta(microseconds=1)).metrics[
        "ma5"] == pytest.approx(14)


def test_a_correction_inside_the_window_splits_the_series_where_it_lands(db, fetch_id) -> None:
    """7-01 corrected an hour after 7-02's cutoff: 7-02 keeps the old close."""
    _seed(db, fetch_id)
    _price(db, fetch_id, date(2024, 7, 1), "99",
           recorded_at=xd.available_from(date(2024, 7, 2)) + timedelta(hours=1))
    rows = _rolling(db)
    _assert_rolling_equals_compute_at_each_cutoff(db, rows)
    by_date = {row.observation_date: row for row in rows}
    assert by_date[date(2024, 7, 1)].metrics["ma5"] is None
    assert by_date[date(2024, 7, 5)].metrics["ma5"] == pytest.approx(31.8)
    # Five rows read, six stored: 7-05 read the correction, not the original.
    assert by_date[date(2024, 7, 5)].input_count == 5


def test_the_history_sees_what_exchange_daily_visible_sees(db, fetch_id) -> None:
    """One visibility rule: the in-memory history and the SQL `visible` agree."""
    _seed(db, fetch_id)
    # A provisional 7-03 row recorded before its rule instant, a settled one
    # after it, and a correction of 7-02 two days later.
    _price(db, fetch_id, date(2024, 7, 3), "13",
           recorded_at=xd.available_from(date(2024, 7, 3)) - timedelta(hours=5))
    _price(db, fetch_id, date(2024, 7, 2), "50",
           recorded_at=xd.available_from(date(2024, 7, 4)))
    history = derived.history(db, stock_id=STOCK, source=SOURCE, through=date(2024, 7, 5),
                              knowledge_as_of=KNOWLEDGE)
    for cutoff_day in [date(2024, 6, 30), *sorted(WEEK), date(2024, 7, 9)]:
        for instant in (xd.available_from(cutoff_day),
                        xd.available_from(cutoff_day) - timedelta(microseconds=1)):
            expected = {
                row["trade_date"]: row["recorded_at"]
                for row in xd.visible(db, daily_prices, as_of=instant, start=date(2024, 7, 1),
                                      end=date(2024, 7, 5), source=SOURCE)
            }
            assert {row.trade_date: row.recorded_at
                    for row in history.visible(instant)} == expected


def test_the_settled_value_supersedes_a_provisional_one_at_the_rule_instant(
    db, fetch_id
) -> None:
    _seed(db, fetch_id, {day: close for day, close in WEEK.items() if day != date(2024, 7, 5)})
    released = xd.available_from(date(2024, 7, 5))
    _price(db, fetch_id, date(2024, 7, 5), "0", recorded_at=released - timedelta(hours=4))
    _price(db, fetch_id, date(2024, 7, 5), "18")  # the settled file, recorded now
    assert _rolling(db)[-1].metrics["ma5"] == pytest.approx(14)


def test_a_knowledge_cutoff_keeps_only_the_rows_recorded_by_then(db, fetch_id) -> None:
    _seed(db, fetch_id)
    _price(db, fetch_id, date(2024, 7, 1), "99", recorded_at=xd.available_from(date(2024, 7, 20)))

    def rows(knowledge: datetime):
        return SERVICE.rolling(db, stock_id=STOCK, start_date=date(2024, 7, 1),
                               end_date=date(2024, 7, 5), source=SOURCE, knowledge_as_of=knowledge)

    assert rows(datetime(2024, 7, 1, tzinfo=UTC)) == ()
    # Recorded by 7-04 03:00: the first three days only.
    assert [row.observation_date for row in rows(xd.available_from(date(2024, 7, 3)))] == [
        date(2024, 7, 1), date(2024, 7, 2), date(2024, 7, 3)]
    # The correction is unknown before it is recorded, whatever the market axis says.
    before = SERVICE.compute(db, stock_id=STOCK, start_date=date(2024, 7, 5),
                             end_date=date(2024, 7, 5), information_as_of=KNOWLEDGE,
                             knowledge_as_of=xd.available_from(date(2024, 7, 19)), source=SOURCE)
    assert before[0].metrics["ma5"] == pytest.approx(14)


def test_a_backfilled_row_is_available_from_its_rule_instant(db, fetch_id) -> None:
    # Recorded now, two years after the trade dates, as 35-a/35-b recorded
    # history: the rule says each settled file was public at D+1 03:00.
    for day, close in sorted(WEEK.items()):
        _price(db, fetch_id, day, close)
    rows = _rolling(db)
    assert [row.observation_date for row in rows] == sorted(WEEK)
    assert rows[-1].metrics["ma5"] == pytest.approx(14)


def test_a_day_after_the_window_cannot_change_a_value(db, fetch_id) -> None:
    _seed(db, fetch_id)
    expected = technical_indicators(tuple(
        DailyBar(trade_date=day, high=float(close), low=float(close), close=float(close),
                 volume=1000.0)
        for day, close in sorted(WEEK.items())
    ))[-1].metrics["ma5"]
    _price(db, fetch_id, date(2024, 7, 8), "100")
    rows = {row.observation_date: row for row in _rolling(db, end=date(2024, 7, 8))}
    assert rows[date(2024, 7, 5)].metrics["ma5"] == pytest.approx(expected)


def test_a_long_history_is_one_row_per_trade_date(db, fetch_id) -> None:
    days = [date(2024, 1, 1) + timedelta(days=offset) for offset in range(400)]
    _seed(db, fetch_id, {day: str(10 + index % 7) for index, day in enumerate(days)})
    rows = SERVICE.rolling(db, stock_id=STOCK, start_date=days[0], end_date=days[-1],
                           source=SOURCE, knowledge_as_of=KNOWLEDGE)
    assert [row.observation_date for row in rows] == days
    assert all(set(row.metrics) == set(METRIC_CODES) for row in rows)


def test_a_stock_on_two_markets_needs_its_source_named(db, fetch_id) -> None:
    # CLAUDE.md §30: one series per source; a market transfer is two series.
    _price(db, fetch_id, date(2024, 1, 24), "10", source="tpex_otc_quotes", stock_id="6446",
           recorded_at=xd.available_from(date(2024, 1, 24)))
    _price(db, fetch_id, date(2024, 1, 25), "11", stock_id="6446",
           recorded_at=xd.available_from(date(2024, 1, 25)))
    with pytest.raises(ValueError, match="tpex_otc_quotes, twse_mi_index"):
        SERVICE.rolling(db, stock_id="6446", start_date=date(2024, 1, 1),
                        end_date=date(2024, 1, 31), knowledge_as_of=KNOWLEDGE)
    (only,) = SERVICE.rolling(db, stock_id="6446", start_date=date(2024, 1, 1),
                              end_date=date(2024, 1, 31), source="tpex_otc_quotes",
                              knowledge_as_of=KNOWLEDGE)
    assert only.observation_date == date(2024, 1, 24)
    # A second source recorded after the knowledge cutoff did not exist then:
    # the question at that cutoff has one source (code review of #51).
    before_transfer = SERVICE.rolling(db, stock_id="6446", start_date=date(2024, 1, 1),
                                      end_date=date(2024, 1, 31),
                                      knowledge_as_of=_recorded(db, "6446", "twse_mi_index")
                                      - timedelta(microseconds=1))
    assert [row.source for row in before_transfer] == ["tpex_otc_quotes"]
    # One source: it need not be named.
    _seed(db, fetch_id)
    assert len(SERVICE.rolling(db, stock_id=STOCK, start_date=date(2024, 7, 1),
                               end_date=date(2024, 7, 5), knowledge_as_of=KNOWLEDGE)) == 5


def test_an_unknown_stock_is_refused(db, fetch_id) -> None:
    with pytest.raises(derived.UnknownStockError):
        SERVICE.rolling(db, stock_id="0000", start_date=date(2024, 7, 1),
                        end_date=date(2024, 7, 5), source=SOURCE, knowledge_as_of=KNOWLEDGE)


def test_the_definition_is_a_code_constant() -> None:
    # ADR-0027: no definition table. §42's fields live on the constant; the
    # implementation version is the git commit each computed row carries.
    definition = derived.TECHNICAL_INDICATORS_PIT_V1
    assert (definition.dataset_code, definition.derivation_version) == (
        "technical_indicators_pit", "v1")
    assert definition.input_tables == ("daily_prices",)
    assert definition.calendar_timezone == "Asia/Taipei"
    assert definition.formula_specification and definition.calendar_convention
    assert definition.price_adjustment_convention.startswith("raw_official_close")
