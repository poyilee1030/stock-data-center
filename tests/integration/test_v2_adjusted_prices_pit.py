"""Step 36: `adjusted_prices_pit:v1` computed on demand under an explicit PIT context.

CLAUDE.md §43 and §51.3: adjusted prices keep PIT corporate-action visibility.
Both inputs are read through `visibility.rows`, so an event adjusts the series
only in a context that sees it — never before its ex-date, never before the
Data Center recorded it, never after its retraction — and a correction changes
the factor only from its own `recorded_at`. The series is anchored at the last
price the context sees, so it never depends on the requested window.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from stock_data_center.db.schema_v2 import corporate_actions, daily_prices
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import adjusted_prices as ap
from stock_data_center.v2 import exchange_daily as xd
from stock_data_center.v2 import visibility as vis
from stock_data_center.v2.derived import UnknownStockError
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch
from stock_data_center.v2.release_rules import corporate_action_available_from

pytestmark = pytest.mark.integration

STOCK = "6488"
PRICES = "tpex_otc_quotes"
FEED = "tpex_exdailyq"
SERVICE = ap.AdjustedPrices(git_commit="abc")
NOW = datetime(2030, 1, 1, tzinfo=UTC)
LATEST = vis.MarketPIT(NOW, NOW)
D1, D2, D3, D4 = (date(2024, 7, d) for d in (1, 2, 3, 4))
EX = D3


@pytest.fixture
def fetch_id(db, tmp_path):
    fetch = record_fetch(
        db,
        FetchRecord("daily_price", PRICES, "t", None, "gap_fill", "t", "abc",
                    datetime(2024, 1, 1, tzinfo=UTC)),
        content=b"t", status="succeeded", store=LocalRawArtifactStore(tmp_path))
    for stock_id, market in ((STOCK, "otc"), ("2330", "sii")):
        db.execute(sa.text("INSERT INTO stocks (stock_id, name, fetch_id) "
                           "VALUES (:s, :n, :f) ON CONFLICT DO NOTHING"),
                   {"s": stock_id, "n": stock_id, "f": fetch})
    return fetch


def _price(db, fetch_id, day, close, *, source=PRICES, stock_id=STOCK, recorded_at=None):
    db.execute(sa.insert(daily_prices).values(
        stock_id=stock_id, source=source, trade_date=day, open_price=close, high_price=close,
        low_price=close, close_price=close, volume=1000, fetch_id=fetch_id,
        recorded_at=recorded_at or xd.available_from(day)))


def _week(db, fetch_id, **kwargs):
    for day, close in ((D1, "100"), (D2, "100"), (D3, "91"), (D4, "92")):
        _price(db, fetch_id, day, close, **kwargs)


def _event(db, fetch_id, recorded_at, *, reference="90", retracted=False, source=FEED,
           stock_id=STOCK, ex_date=EX):
    values = {"stock_id": stock_id, "source": source, "ex_date": ex_date, "event_type": "除息",
              "close_before": Decimal(100), "reference_price": Decimal(reference),
              "retracted": retracted, "fetch_id": fetch_id, "recorded_at": recorded_at}
    if source in ("twse_twt49u", "twse_twtauu"):
        values["detail_fetch_id"] = fetch_id
    db.execute(sa.insert(corporate_actions).values(values))


def _factors(db, pit=LATEST, start=D1, end=D4, **kwargs):
    series = SERVICE.compute(db, stock_id=STOCK, start_date=start, end_date=end, pit=pit,
                             **kwargs)
    return {row.bar.trade_date: row.factor for row in series.rows}


def test_a_visible_event_adjusts_every_earlier_price(db, fetch_id) -> None:
    _week(db, fetch_id)
    _event(db, fetch_id, datetime(2024, 7, 3, 1, tzinfo=UTC))
    series = SERVICE.compute(db, stock_id=STOCK, start_date=D1, end_date=D4, pit=LATEST)
    assert (series.stock_id, series.source, series.git_commit) == (STOCK, PRICES, "abc")
    factors = {row.bar.trade_date: row.factor for row in series.rows}
    assert factors == {D1: pytest.approx(0.9), D2: pytest.approx(0.9), D3: 1.0, D4: 1.0}
    [event] = series.events
    assert (event.row["ex_date"], event.row["source"], event.factor) == \
        (EX, FEED, Decimal("0.9"))
    assert event.row["available_at"] == corporate_action_available_from(EX)
    assert event.row["fetch_id"] == fetch_id


def test_no_event_adjusts_the_series_before_its_ex_date_price_is_public(db, fetch_id) -> None:
    # corporate_action_ex_date@1 makes the event public at 00:00 on the ex-date,
    # but the series still ends on the day before until the ex-date's own close
    # is public at 03:00 the next morning: until then nothing moves.
    _week(db, fetch_id)
    _event(db, fetch_id, datetime(2024, 6, 20, tzinfo=UTC))
    released = xd.available_from(EX)
    before = _factors(db, vis.MarketPIT(released - timedelta(seconds=1), NOW))
    assert before == {D1: 1.0, D2: 1.0}
    assert _factors(db, vis.MarketPIT(released, NOW))[D2] == pytest.approx(0.9)


def test_an_event_the_data_center_had_not_recorded_adjusts_nothing(db, fetch_id) -> None:
    _week(db, fetch_id)
    recorded = datetime(2024, 8, 1, tzinfo=UTC)
    _event(db, fetch_id, recorded)
    assert set(_factors(db, vis.MarketPIT(NOW, recorded - timedelta(seconds=1))).values()) \
        == {1.0}
    assert _factors(db, vis.MarketPIT(NOW, recorded))[D1] == pytest.approx(0.9)
    assert set(_factors(db, vis.SystemPIT(recorded - timedelta(seconds=1))).values()) == {1.0}
    assert _factors(db, vis.SystemPIT(recorded))[D1] == pytest.approx(0.9)


def test_a_retracted_event_stops_adjusting_from_its_retraction(db, fetch_id) -> None:
    _week(db, fetch_id)
    _event(db, fetch_id, datetime(2024, 7, 3, 1, tzinfo=UTC))
    retracted_at = datetime(2024, 8, 1, tzinfo=UTC)
    _event(db, fetch_id, retracted_at, retracted=True)
    assert _factors(db, vis.MarketPIT(retracted_at - timedelta(seconds=1), NOW))[D1] == \
        pytest.approx(0.9)
    assert _factors(db, vis.MarketPIT(retracted_at, NOW))[D1] == 1.0


def test_a_corrected_event_changes_the_factor_from_its_recorded_at(db, fetch_id) -> None:
    _week(db, fetch_id)
    _event(db, fetch_id, datetime(2024, 7, 3, 1, tzinfo=UTC))
    corrected = datetime(2024, 8, 1, tzinfo=UTC)
    _event(db, fetch_id, corrected, reference="80")
    assert _factors(db, vis.MarketPIT(corrected - timedelta(seconds=1), NOW))[D1] == \
        pytest.approx(0.9)
    assert _factors(db, vis.MarketPIT(corrected, NOW))[D1] == pytest.approx(0.8)


def test_an_event_adjusts_only_the_prices_of_its_own_market(db, fetch_id) -> None:
    # A stock that moved market keeps one series per source (CLAUDE.md §30):
    # the TPEx event adjusts the TPEx prices and never the TWSE ones.
    _week(db, fetch_id)
    _week(db, fetch_id, source="twse_mi_index")
    _event(db, fetch_id, datetime(2024, 7, 3, 1, tzinfo=UTC))
    assert _factors(db, source=PRICES)[D1] == pytest.approx(0.9)
    assert set(_factors(db, source="twse_mi_index").values()) == {1.0}
    _event(db, fetch_id, datetime(2024, 7, 3, 1, tzinfo=UTC), source="twse_twt49u",
           reference="50")
    assert _factors(db, source="twse_mi_index")[D1] == pytest.approx(0.5)
    assert _factors(db, source=PRICES)[D1] == pytest.approx(0.9)


def test_a_stock_with_two_price_sources_must_name_one(db, fetch_id) -> None:
    _week(db, fetch_id)
    _week(db, fetch_id, source="twse_mi_index")
    with pytest.raises(ValueError, match="name the source"):
        _factors(db)


def test_a_stock_not_on_the_list_is_refused(db, fetch_id) -> None:
    with pytest.raises(UnknownStockError):
        SERVICE.compute(db, stock_id="9999", start_date=D1, end_date=D4, pit=LATEST)


def test_a_stock_without_prices_has_an_empty_series(db, fetch_id) -> None:
    series = SERVICE.compute(db, stock_id=STOCK, start_date=D1, end_date=D4, pit=LATEST)
    assert (series.rows, series.events) == ((), ())


def test_the_window_does_not_move_the_anchor(db, fetch_id) -> None:
    # A request ending before the event is still adjusted to the last price the
    # context sees: a value never depends on the window it was asked in.
    _week(db, fetch_id)
    _event(db, fetch_id, datetime(2024, 7, 3, 1, tzinfo=UTC))
    assert _factors(db, start=D1, end=D2) == {D1: pytest.approx(0.9), D2: pytest.approx(0.9)}
    series = SERVICE.compute(db, stock_id=STOCK, start_date=D1, end_date=D2, pit=LATEST)
    assert [e.row["ex_date"] for e in series.events] == [EX]
    # An event on or before the window's first day adjusts nothing in it.
    later = SERVICE.compute(db, stock_id=STOCK, start_date=D3, end_date=D4, pit=LATEST)
    assert later.events == ()


def test_events_list_only_what_adjusts_a_returned_row(db, fetch_id) -> None:
    # Code review of #67: a window starting on a closed day begins at its first
    # trade date, and an event on that date adjusts nothing in it.
    _week(db, fetch_id)
    _event(db, fetch_id, datetime(2024, 7, 3, 1, tzinfo=UTC))
    _event(db, fetch_id, datetime(2024, 7, 1, 1, tzinfo=UTC), ex_date=D1)
    saturday = date(2024, 6, 29)
    series = SERVICE.compute(db, stock_id=STOCK, start_date=saturday, end_date=D4, pit=LATEST)
    assert series.rows[0].bar.trade_date == D1
    assert [e.row["ex_date"] for e in series.events] == [EX]
    # A window with no row lists no event, though later prices anchor the series.
    empty = SERVICE.compute(db, stock_id=STOCK, start_date=saturday,
                            end_date=date(2024, 6, 30), pit=LATEST)
    assert (empty.rows, empty.events) == ((), ())


def test_the_source_is_chosen_from_what_the_context_sees_in_the_window(db, fetch_id) -> None:
    # Code review of #67 (§19): a stock that moved from TPEx to TWSE has one
    # series before the move, and a context that has not seen the second source
    # neither fails on it nor names it.
    moved = datetime(2024, 8, 1, tzinfo=UTC)
    for day, close in ((D1, "100"), (D2, "100")):
        _price(db, fetch_id, day, close)
    for day, close in ((D3, "91"), (D4, "92")):
        _price(db, fetch_id, day, close, source="twse_mi_index", recorded_at=moved)
    before_move = SERVICE.compute(db, stock_id=STOCK, start_date=D1, end_date=D2, pit=LATEST)
    assert before_move.source == PRICES
    with pytest.raises(ValueError, match="name the source"):
        _factors(db)
    unseen = SERVICE.compute(db, stock_id=STOCK, start_date=D1, end_date=D4,
                             pit=vis.SystemPIT(moved - timedelta(seconds=1)))
    assert unseen.source == PRICES
    assert [row.bar.trade_date for row in unseen.rows] == [D1, D2]
    nothing = SERVICE.compute(db, stock_id=STOCK, start_date=D1, end_date=D4,
                              pit=vis.SystemPIT(datetime(2024, 1, 1, tzinfo=UTC)))
    assert (nothing.source, nothing.rows) == ("", ())
