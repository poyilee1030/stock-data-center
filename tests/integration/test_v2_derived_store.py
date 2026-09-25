"""Step 26-b: stored derived tables, computed incrementally from the latest inputs.

`technical_indicators:v1` and `institutional_streaks:v1` are wide tables keyed
by (stock, source, date). A run fixes its inputs at one instant, `computed_at`,
and recomputes each series from the earliest input date recorded since the
previous run's `computed_at`, reading a warm-up buffer before it; the rows it
writes replace what was there (derived tables are not append-only). A full run
recomputes everything, and is what the on-demand `technical_indicators_pit:v1`
must equal bit for bit while the inputs carry no correction.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import OperationalError

from stock_data_center.db.schema_v2 import (
    daily_prices,
    foreign_holdings,
    institutional_cumulative_flow,
    institutional_flows,
    institutional_streaks,
    margin_metrics,
    margin_trading,
    securities_lending,
    shareholding_concentration,
    shareholding_distributions,
    short_interest_metrics,
    technical_indicators,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import backfill, derived, derived_store
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch
from stock_data_center.v2.indicators import METRIC_CODES, DailyBar
from stock_data_center.v2.indicators import technical_indicators as formulas

pytestmark = pytest.mark.integration

TWSE = "twse_mi_index"
T86 = "twse_t86"
TECH = derived_store.TECHNICAL_INDICATORS
STREAKS = derived_store.INSTITUTIONAL_STREAKS
CUMULATIVE = derived_store.INSTITUTIONAL_CUMULATIVE_FLOW
CONCENTRATION = derived_store.SHAREHOLDING_CONCENTRATION
TDCC = "tdcc_opendata"
MARGIN = derived_store.MARGIN_METRICS
SHORT_INTEREST = derived_store.SHORT_INTEREST_METRICS
MI_MARGN = "twse_mi_margn"
TWT93U = "twse_twt93u"
QFIIS = "twse_mi_qfiis"
EXPONENTIAL = ("k", "d", "rsi6", "rsi12", "macd_dif", "macd_dea", "macd_hist")


@pytest.fixture
def fetch_id(db: Connection, tmp_path):
    fetch = record_fetch(
        db,
        FetchRecord("daily_price", TWSE, "t", None, "gap_fill", "t", "abc", datetime.now(UTC)),
        content=b"t", status="succeeded", store=LocalRawArtifactStore(tmp_path),
    )
    for stock_id in ("2330", "2317"):
        db.execute(
            sa.text("INSERT INTO stocks (stock_id, name, market, fetch_id) "
                    "VALUES (:s, :n, 'sii', :f) ON CONFLICT DO NOTHING"),
            {"s": stock_id, "n": stock_id, "f": fetch},
        )
    return fetch


def _trading_days(first: date, count: int) -> list[date]:
    days, day = [], first
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _close(index: int) -> str:
    # Deterministic, not monotonic: RSI and KD need both up and down days.
    return f"{100 + 7 * math.sin(index / 5) + (index % 3):.2f}"


def _prices(db, fetch_id, days, *, stock_id="2330", closes=None, volume=1000) -> None:
    rows = []
    for index, day in enumerate(days):
        close = closes[index] if closes else _close(index)
        rows.append({"stock_id": stock_id, "source": TWSE, "trade_date": day,
                     "open_price": close, "high_price": close, "low_price": close,
                     "close_price": close, "volume": volume, "fetch_id": fetch_id})
    db.execute(sa.insert(daily_prices), rows)


def _flow(db, fetch_id, day, *, foreign=0, trust=0, dealer=0, stock_id="2330") -> None:
    db.execute(sa.insert(institutional_flows).values(
        stock_id=stock_id, source=T86, trade_date=day, foreign_net=foreign, trust_net=trust,
        dealer_net=dealer, fetch_id=fetch_id))


def _stored(db, table, stock_id="2330") -> dict[date, sa.RowMapping]:
    return {
        row["trade_date"]: row
        for row in db.execute(sa.select(table).where(table.c.stock_id == stock_id)
                              .order_by(table.c.trade_date)).mappings()
    }


def _full(db, stock_id="2330") -> dict[date, dict]:
    """Every date's metrics from the whole series, as the formulas define them."""
    rows = db.execute(sa.text(
        "SELECT DISTINCT ON (trade_date) trade_date, high_price, low_price, close_price, volume "
        "FROM daily_prices WHERE stock_id = :s AND source = :src "
        "ORDER BY trade_date, recorded_at DESC"), {"s": stock_id, "src": TWSE}).all()
    bars = [DailyBar(day, *(None if v is None else float(v) for v in values))
            for day, *values in rows]
    return {row.trade_date: dict(row.metrics) for row in formulas(bars)}


def _metrics(row) -> dict:
    return {code: row[code] for code in METRIC_CODES}


# ---------------------------------------------------------------- definitions


def test_the_stored_and_on_demand_datasets_are_named_apart() -> None:
    # ROADMAP Step 26: the stored series is the default name; the on-demand PIT
    # reference carries `_pit`. One formula, so one version.
    assert (TECH.definition.dataset_code, TECH.definition.derivation_version) == (
        "technical_indicators", "v1")
    assert (derived.TECHNICAL_INDICATORS_PIT_V1.dataset_code,
            derived.TECHNICAL_INDICATORS_PIT_V1.derivation_version) == (
        "technical_indicators_pit", "v1")
    assert TECH.definition.formula_specification == (
        derived.TECHNICAL_INDICATORS_PIT_V1.formula_specification)
    assert (STREAKS.definition.dataset_code, STREAKS.definition.derivation_version) == (
        "institutional_streaks", "v1")
    assert STREAKS.definition.input_tables == ("daily_prices", "institutional_flows")


def test_a_run_waits_for_the_writers_of_its_inputs(engine: Engine) -> None:
    """A run fixes its inputs only once no writer of them is mid-transaction.

    A writer's rows carry the INSERT's statement time, which can be earlier than
    the run's `computed_at` although they commit after the run read its inputs;
    the next run, looking for rows recorded after `computed_at`, would never see
    them. Writers hold their job's advisory lock until they commit, so the run
    takes the same locks, shared, before fixing its instant.
    """
    with engine.connect() as writer, engine.connect() as runner:
        writer.begin()
        writer.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(
            f"daily_prices/{TWSE}"))))
        runner.begin()
        runner.execute(sa.text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            derived_store.run(runner, TECH)
        runner.rollback()
        writer.rollback()


def test_the_run_holds_its_readers_lock_until_it_commits(engine: Engine) -> None:
    # The converse: a writer that starts during a run waits for it, so every row
    # it records is stamped after the run's `computed_at`.
    with engine.connect() as runner, engine.connect() as writer:
        runner.begin()
        derived_store.run(runner, STREAKS)
        writer.begin()
        writer.execute(sa.text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            writer.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(
                f"institutional_flows/{T86}"))))
        writer.rollback()
        runner.rollback()


def test_a_run_takes_the_lock_of_every_writer_of_its_inputs() -> None:
    # A writer the run does not wait for can commit rows stamped before
    # `computed_at` after the run has read its inputs; the next run never sees them.
    for dataset in derived_store.DATASETS.values():
        writers = {key for key, job in backfill.JOBS.items() if job.table in dataset.inputs}
        assert {job.table for job in backfill.JOBS.values() if job.key in writers} == set(
            dataset.inputs), dataset.definition.dataset_code
        assert writers <= set(derived_store.writer_keys(dataset)), (
            dataset.definition.dataset_code)


def test_a_concentration_run_waits_for_the_tdcc_writer(engine: Engine) -> None:
    with engine.connect() as writer, engine.connect() as runner:
        writer.begin()
        writer.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(
            f"shareholding_distributions/{TDCC}"))))
        runner.begin()
        runner.execute(sa.text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            derived_store.run(runner, CONCENTRATION)
        runner.rollback()
        writer.rollback()


def test_two_runs_of_one_dataset_do_not_overlap(engine: Engine) -> None:
    with engine.connect() as first, engine.connect() as second:
        first.begin()
        derived_store.run(first, TECH)
        second.begin()
        second.execute(sa.text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            derived_store.run(second, TECH)
        second.rollback()
        first.rollback()


# ---------------------------------------------------------------- technical indicators


def test_a_first_run_stores_every_date_of_every_series(db, fetch_id) -> None:
    days = _trading_days(date(2024, 1, 1), 60)
    _prices(db, fetch_id, days)
    _prices(db, fetch_id, days[10:], stock_id="2317")

    result = derived_store.run(db, TECH)

    assert result.previous is None
    assert result.series == 2
    assert result.rows == 110
    stored = _stored(db, technical_indicators)
    assert sorted(stored) == days
    assert {row["computed_at"] for row in stored.values()} == {result.computed_at}
    assert {row["source"] for row in stored.values()} == {TWSE}
    full = _full(db)
    for day, row in stored.items():
        assert _metrics(row) == full[day]


def test_a_full_run_equals_the_pit_reference_bit_for_bit(db, fetch_id) -> None:
    days = _trading_days(date(2024, 1, 1), 300)
    _prices(db, fetch_id, days)
    derived_store.run(db, TECH)

    reference = derived.TechnicalIndicators(git_commit="abc").rolling(
        db, stock_id="2330", start_date=days[0], end_date=days[-1], source=TWSE,
        knowledge_as_of=datetime.now(UTC) + timedelta(days=1))
    stored = _stored(db, technical_indicators)
    assert len(reference) == len(stored) == 300
    for row in reference:
        assert _metrics(stored[row.observation_date]) == dict(row.metrics)


def test_a_run_with_nothing_new_writes_nothing(db, fetch_id) -> None:
    _prices(db, fetch_id, _trading_days(date(2024, 1, 1), 30))
    first = derived_store.run(db, TECH)
    second = derived_store.run(db, TECH)
    assert second.previous == first.computed_at
    assert (second.series, second.rows) == (0, 0)
    assert {row["computed_at"] for row in _stored(db, technical_indicators).values()} == {
        first.computed_at}


def test_a_new_day_is_computed_and_the_earlier_rows_are_left_alone(db, fetch_id) -> None:
    days = _trading_days(date(2024, 1, 1), 41)
    _prices(db, fetch_id, days[:40])
    first = derived_store.run(db, TECH)
    _prices(db, fetch_id, days[40:], closes=[_close(40)])

    second = derived_store.run(db, TECH)

    assert (second.series, second.rows) == (1, 1)
    stored = _stored(db, technical_indicators)
    assert stored[days[40]]["computed_at"] == second.computed_at
    assert {stored[day]["computed_at"] for day in days[:40]} == {first.computed_at}
    assert _metrics(stored[days[40]]) == _full(db)[days[40]]


def test_a_correction_recomputes_from_its_date_and_overwrites(db, fetch_id) -> None:
    days = _trading_days(date(2024, 1, 1), 40)
    _prices(db, fetch_id, days)
    first = derived_store.run(db, TECH)
    before = _stored(db, technical_indicators)
    # A corrected 20th day, recorded after the first run.
    _prices(db, fetch_id, [days[20]], closes=["250.00"])

    second = derived_store.run(db, TECH)

    assert second.rows == 20
    stored = _stored(db, technical_indicators)
    full = _full(db)
    for day in days[:20]:
        assert stored[day]["computed_at"] == first.computed_at
        assert _metrics(stored[day]) == _metrics(before[day])
    for day in days[20:]:
        assert stored[day]["computed_at"] == second.computed_at
        assert _metrics(stored[day]) == full[day]
    assert stored[days[20]]["ma5"] != before[days[20]]["ma5"]


def test_a_value_uses_no_input_dated_after_it(db, fetch_id) -> None:
    days = _trading_days(date(2024, 1, 1), 80)
    _prices(db, fetch_id, days[:50])
    derived_store.run(db, TECH)
    early = {day: _metrics(row) for day, row in _stored(db, technical_indicators).items()}
    _prices(db, fetch_id, days[50:], closes=[_close(i) for i in range(50, 80)])

    derived_store.run(db, TECH, full=True)

    stored = _stored(db, technical_indicators)
    for day in days[:50]:
        assert _metrics(stored[day]) == early[day]


def test_a_full_run_recomputes_every_row(db, fetch_id) -> None:
    _prices(db, fetch_id, _trading_days(date(2024, 1, 1), 30))
    first = derived_store.run(db, TECH)
    again = derived_store.run(db, TECH, full=True)
    assert again.previous == first.computed_at
    assert again.rows == 30
    assert {row["computed_at"] for row in _stored(db, technical_indicators).values()} == {
        again.computed_at}


def test_the_warm_up_reaches_back_the_legacy_buffer(db, fetch_id) -> None:
    """Legacy restarted each series 500 calendar days before the first new date.

    The windowed metrics need only their window and are exact; the exponential
    ones never forget their start, so an incremental value differs from the
    full series by a residue the owner accepted (2026-09-24) — bounded here
    and measured on the whole market in the acceptance report.
    """
    days = _trading_days(date(2021, 1, 1), 700)
    _prices(db, fetch_id, days[:699])
    derived_store.run(db, TECH)
    _prices(db, fetch_id, days[699:], closes=[_close(699)])

    derived_store.run(db, TECH)

    stored = _metrics(_stored(db, technical_indicators)[days[699]])
    full = _full(db)[days[699]]
    assert stored != full  # the buffer, not the whole series, was read
    close = float(_close(699))
    for code in METRIC_CODES:
        if code in EXPONENTIAL:
            assert derived_store.within_tolerance(code, stored[code], full[code], close), code
        else:
            assert stored[code] == full[code], code
    warm_up = [day for day in days if day >= days[699] - timedelta(days=500)]
    buffered = formulas([DailyBar(day, *(float(_close(days.index(day))),) * 3, 1000.0)
                         for day in warm_up])[-1].metrics
    assert stored == dict(buffered)


def test_the_tolerance_is_scaled_to_what_each_metric_measures() -> None:
    within = derived_store.within_tolerance
    assert within("macd_dif", 1.0 + 9e-4, 1.0, 100.0)
    assert not within("macd_dif", 1.0 + 2e-3, 1.0, 100.0)
    assert not within("macd_dif", 1.0 + 1e-9, 1.0, None)
    assert within("rsi12", 50.0 + 9e-7, 50.0, 1000.0)
    assert not within("rsi12", 50.0 + 2e-6, 50.0, 1000.0)
    # A windowed metric is exact, and a value never stands in for a missing one.
    assert not within("ma20", 10.0 + 1e-12, 10.0, 100.0)
    assert not within("k", None, 50.0, 100.0)
    assert within("ma20", None, None, None)


def test_the_warm_up_covers_the_longest_window_after_a_long_suspension(db, fetch_id) -> None:
    # 250 trading days, a suspension of two years, then two more days: 500
    # calendar days before the new day hold one row, and MA240 needs 240.
    days = _trading_days(date(2020, 1, 1), 250)
    resumed = _trading_days(days[-1] + timedelta(days=730), 2)
    _prices(db, fetch_id, days + resumed[:1],
            closes=[_close(i) for i in range(251)])
    derived_store.run(db, TECH)
    _prices(db, fetch_id, resumed[1:], closes=[_close(251)])

    derived_store.run(db, TECH)

    stored = _stored(db, technical_indicators)[resumed[1]]
    full = _full(db)[resumed[1]]
    for code in ("ma240", "vma240", "ma120", "bb_upper"):
        assert stored[code] == full[code] and stored[code] is not None, code


def test_each_source_is_its_own_series(db, fetch_id) -> None:
    # CLAUDE.md §30: a stock that moved market has one series per source.
    _prices(db, fetch_id, _trading_days(date(2024, 1, 1), 10))
    db.execute(sa.insert(daily_prices), [
        {"stock_id": "2330", "source": "tpex_otc_quotes", "trade_date": day,
         "close_price": "50", "high_price": "50", "low_price": "50", "volume": 1,
         "fetch_id": fetch_id}
        for day in _trading_days(date(2023, 1, 2), 5)])
    derived_store.run(db, TECH)
    sources = db.execute(sa.text(
        "SELECT source, count(*) FROM technical_indicators GROUP BY source ORDER BY source")).all()
    assert sources == [("tpex_otc_quotes", 5), (TWSE, 10)]
    tpex = db.execute(sa.text(
        "SELECT ma5 FROM technical_indicators WHERE source = 'tpex_otc_quotes' "
        "ORDER BY trade_date DESC LIMIT 1")).scalar()
    assert tpex == 50.0


# ---------------------------------------------------------------- institutional streaks


def test_streaks_run_over_the_days_the_stock_traded(db, fetch_id) -> None:
    """Legacy's axis: its daily quotes, which kept only days with a trade.

    A traded day with no institutional row is a zero net and breaks the
    streak; a listed day without a trade is not a day of the series at all.
    """
    days = _trading_days(date(2024, 7, 1), 6)
    _prices(db, fetch_id, days[:3])
    _prices(db, fetch_id, days[3:4], volume=0, closes=[None])
    _prices(db, fetch_id, days[4:])
    _flow(db, fetch_id, days[0], foreign=5, trust=-1, dealer=3)
    _flow(db, fetch_id, days[1], foreign=5, trust=-1)
    # days[2]: traded, no institutional row. days[3]: no trade.
    _flow(db, fetch_id, days[4], foreign=2, trust=-4, dealer=1)
    _flow(db, fetch_id, days[5], foreign=1, trust=-4, dealer=1)

    derived_store.run(db, STREAKS)

    stored = _stored(db, institutional_streaks)
    assert sorted(stored) == [days[0], days[1], days[2], days[4], days[5]]
    assert {row["source"] for row in stored.values()} == {T86}
    assert [(r["foreign_streak_days"], r["trust_streak_days"], r["dealer_streak_days"])
            for _, r in sorted(stored.items())] == [
        (1, -1, 1), (2, -2, 0), (0, 0, 0), (1, -1, 1), (2, -2, 2)]


def test_a_stock_no_institution_traded_has_zero_streaks(db, fetch_id) -> None:
    _prices(db, fetch_id, _trading_days(date(2024, 7, 1), 3), stock_id="2317")
    derived_store.run(db, STREAKS)
    stored = _stored(db, institutional_streaks, "2317")
    assert len(stored) == 3
    assert {(r["source"], r["foreign_streak_days"], r["trust_streak_days"],
             r["dealer_streak_days"]) for r in stored.values()} == {(T86, 0, 0, 0)}


def test_a_late_flow_row_recomputes_the_streaks_from_its_date(db, fetch_id) -> None:
    days = _trading_days(date(2024, 7, 1), 5)
    _prices(db, fetch_id, days)
    for day in days:
        if day != days[2]:
            _flow(db, fetch_id, day, foreign=1)
    first = derived_store.run(db, STREAKS)
    assert [r["foreign_streak_days"] for _, r in sorted(_stored(
        db, institutional_streaks).items())] == [1, 2, 0, 1, 2]
    _flow(db, fetch_id, days[2], foreign=1)  # the missing day, fetched late

    second = derived_store.run(db, STREAKS)

    stored = sorted(_stored(db, institutional_streaks).items())
    assert [r["foreign_streak_days"] for _, r in stored] == [1, 2, 3, 4, 5]
    assert [r["computed_at"] for _, r in stored] == [first.computed_at] * 2 + [
        second.computed_at] * 3


def test_a_new_price_day_alone_extends_the_streaks(db, fetch_id) -> None:
    days = _trading_days(date(2024, 7, 1), 4)
    _prices(db, fetch_id, days[:3])
    for day in days[:3]:
        _flow(db, fetch_id, day, dealer=-2)
    derived_store.run(db, STREAKS)
    _prices(db, fetch_id, days[3:], closes=["10"])

    derived_store.run(db, STREAKS)

    assert [r["dealer_streak_days"] for _, r in sorted(_stored(
        db, institutional_streaks).items())] == [-1, -2, -3, 0]


def test_a_streak_longer_than_the_buffer_is_counted_from_its_start(db, fetch_id) -> None:
    # A count must equal a full recomputation exactly (CLAUDE.md §46), and a
    # streak unbroken across the whole 500-day buffer began before it.
    days = _trading_days(date(2021, 1, 1), 400)
    _prices(db, fetch_id, days[:399])
    for day in days:
        _flow(db, fetch_id, day, foreign=-1, trust=1 if day >= days[300] else 0)
    derived_store.run(db, STREAKS)
    _prices(db, fetch_id, days[399:], closes=[_close(399)])

    derived_store.run(db, STREAKS)

    last = _stored(db, institutional_streaks)[days[399]]
    assert days[399] - days[0] > timedelta(days=500)
    assert (last["foreign_streak_days"], last["trust_streak_days"]) == (-400, 100)


# ---------------------------------------------------------------- cumulative flow


def _issued(db, fetch_id, day, shares, *, stock_id="2330") -> None:
    db.execute(sa.insert(foreign_holdings).values(
        stock_id=stock_id, source=QFIIS, trade_date=day, issued_shares=shares,
        fetch_id=fetch_id))


def _cumulative(db, stock_id="2330") -> list[tuple]:
    return [
        (r["trade_date"], r["trust_cumulative_net_shares"], r["trust_cumulative_net_ratio"],
         r["dealer_cumulative_net_shares"], r["dealer_cumulative_net_ratio"])
        for _, r in sorted(_stored(db, institutional_cumulative_flow, stock_id).items())
    ]


def test_the_cumulative_flow_definition() -> None:
    definition = CUMULATIVE.definition
    assert (definition.dataset_code, definition.derivation_version) == (
        "institutional_cumulative_flow", "v1")
    assert definition.input_tables == ("institutional_flows", "foreign_holdings")


def test_cumulative_flows_sum_each_institutional_day_from_zero(db, fetch_id) -> None:
    """Legacy trust_holding/dealer_holding: one row per day of the institutional
    file, the nets summed from the series' first day, divided by the same day's
    issued shares. A day without a foreign-holding row has no ratio."""
    days = _trading_days(date(2024, 7, 1), 4)
    _flow(db, fetch_id, days[0], trust=100, dealer=-40)
    _flow(db, fetch_id, days[1], trust=-30, dealer=0)
    # days[2]: no institutional row, so no cumulative row.
    _flow(db, fetch_id, days[3], trust=5, dealer=-40)
    _issued(db, fetch_id, days[0], 1_000_000)
    _issued(db, fetch_id, days[3], 2_000_000)

    derived_store.run(db, CUMULATIVE)

    assert _cumulative(db) == [
        (days[0], 100, 0.01, -40, -0.004),
        (days[1], 70, None, -40, None),
        (days[3], 75, 0.0038, -80, -0.004),
    ]
    assert {r["source"] for r in _stored(db, institutional_cumulative_flow).values()} == {T86}


def test_each_market_starts_its_own_sum(db, fetch_id) -> None:
    # CLAUDE.md §30: a stock that moved market is two series, as legacy
    # partitioned by (market, symbol).
    _flow(db, fetch_id, date(2023, 1, 3), trust=7)
    db.execute(sa.insert(institutional_flows).values(
        stock_id="2330", source="tpex_insti_daily_trade", trade_date=date(2022, 1, 3),
        trust_net=50, dealer_net=1, fetch_id=fetch_id))
    derived_store.run(db, CUMULATIVE)
    rows = db.execute(sa.text(
        "SELECT source, trust_cumulative_net_shares FROM institutional_cumulative_flow "
        "ORDER BY trade_date")).all()
    assert rows == [("tpex_insti_daily_trade", 50), (T86, 7)]


def test_a_late_flow_row_carries_into_every_later_sum(db, fetch_id) -> None:
    days = _trading_days(date(2024, 7, 1), 4)
    for day in days:
        _flow(db, fetch_id, day, trust=1)
    first = derived_store.run(db, CUMULATIVE)
    _flow(db, fetch_id, days[1], trust=10)  # a correction of the second day

    second = derived_store.run(db, CUMULATIVE)

    stored = sorted(_stored(db, institutional_cumulative_flow).items())
    assert [r["trust_cumulative_net_shares"] for _, r in stored] == [1, 11, 12, 13]
    assert [r["computed_at"] for _, r in stored] == [first.computed_at] + [
        second.computed_at] * 3


def test_a_late_issued_share_count_changes_only_its_own_ratio(db, fetch_id) -> None:
    days = _trading_days(date(2024, 7, 1), 3)
    for day in days:
        _flow(db, fetch_id, day, trust=1000)
    first = derived_store.run(db, CUMULATIVE)
    _issued(db, fetch_id, days[1], 1_000_000)

    second = derived_store.run(db, CUMULATIVE)

    stored = sorted(_stored(db, institutional_cumulative_flow).items())
    assert [r["trust_cumulative_net_ratio"] for _, r in stored] == [None, 0.2, None]
    assert [r["computed_at"] for _, r in stored] == [first.computed_at] + [
        second.computed_at] * 2


def test_an_incremental_sum_equals_the_full_one_beyond_the_buffer(db, fetch_id) -> None:
    # A sum never forgets its first day: no warm-up buffer is enough, so every
    # series is summed from its start.
    days = _trading_days(date(2021, 1, 1), 450)
    for index, day in enumerate(days[:449]):
        _flow(db, fetch_id, day, trust=index % 7 - 3, dealer=1)
    derived_store.run(db, CUMULATIVE)
    _flow(db, fetch_id, days[449], trust=2, dealer=1)

    derived_store.run(db, CUMULATIVE)

    incremental = _cumulative(db)
    derived_store.run(db, CUMULATIVE, full=True)
    assert incremental == _cumulative(db)
    assert incremental[-1][3] == 450


def test_a_cumulative_value_uses_no_input_dated_after_it(db, fetch_id) -> None:
    days = _trading_days(date(2024, 7, 1), 6)
    for day in days[:3]:
        _flow(db, fetch_id, day, trust=2, dealer=-1)
        _issued(db, fetch_id, day, 10_000)
    derived_store.run(db, CUMULATIVE)
    early = _cumulative(db)
    for day in days[3:]:
        _flow(db, fetch_id, day, trust=50, dealer=50)
        _issued(db, fetch_id, day, 99)

    derived_store.run(db, CUMULATIVE, full=True)

    assert _cumulative(db)[:3] == early


# ---------------------------------------------------------------- shareholding concentration


def _snapshot(db, fetch_id, day, *, percent="1.00", large="1.00", holders=10,
              stock_id="2330") -> None:
    """Levels 1-11 at `percent`, 12-15 at `large`, every level `holders`."""
    values = {"stock_id": stock_id, "source": TDCC, "snapshot_date": day, "fetch_id": fetch_id}
    for level in range(1, 16):
        values[f"holders_{level}"] = holders
        values[f"shares_{level}"] = holders * 1000
        values[f"percent_{level}"] = large if level >= 12 else percent
    db.execute(sa.insert(shareholding_distributions).values(**values))


def _concentration(db, *columns, stock_id="2330") -> list[tuple]:
    t = shareholding_concentration.c
    return [tuple(row) for row in db.execute(
        sa.select(t.snapshot_date, *(t[c] for c in columns))
        .where(t.stock_id == stock_id).order_by(t.snapshot_date))]


def test_the_concentration_definition() -> None:
    definition = CONCENTRATION.definition
    assert (definition.dataset_code, definition.derivation_version) == (
        "shareholding_concentration", "v1")
    assert definition.input_tables == ("shareholding_distributions",)


def test_concentration_is_keyed_by_the_snapshot_date(db, fetch_id) -> None:
    # A TDCC week is dated by its snapshot, which need not be a trading day
    # (2021-02-09 fell in a week the exchange was shut).
    weeks = [date(2021, 2, 5), date(2021, 2, 9), date(2021, 2, 20)]
    for week in weeks:
        _snapshot(db, fetch_id, week)

    run = derived_store.run(db, CONCENTRATION)

    assert (run.series, run.rows) == (1, 3)
    assert _concentration(db, "source", "large_holder_ratio", "small_holder_count") == [
        (week, TDCC, 4.0, 80) for week in weeks]


def test_a_change_is_against_the_previous_snapshot(db, fetch_id) -> None:
    # Legacy's LAG: the previous snapshot of the series, however far back.
    _snapshot(db, fetch_id, date(2024, 7, 5), large="1.00")
    _snapshot(db, fetch_id, date(2024, 7, 12), large="2.00")
    _snapshot(db, fetch_id, date(2024, 8, 2), large="2.50")

    derived_store.run(db, CONCENTRATION)

    assert _concentration(db, "large_holder_ratio_wow", "concentration_spread_wow") == [
        (date(2024, 7, 5), None, None),
        (date(2024, 7, 12), 4.0, 4.0),
        (date(2024, 8, 2), 2.0, 2.0),
    ]


def test_a_corrected_snapshot_recomputes_its_week_and_the_next(db, fetch_id) -> None:
    weeks = [date(2024, 7, 5), date(2024, 7, 12), date(2024, 7, 19)]
    for week in weeks:
        _snapshot(db, fetch_id, week)
    first = derived_store.run(db, CONCENTRATION)
    _snapshot(db, fetch_id, weeks[1], large="3.00")  # a correction of the second week

    second = derived_store.run(db, CONCENTRATION)

    assert _concentration(db, "large_holder_ratio", "large_holder_ratio_wow", "computed_at") == [
        (weeks[0], 4.0, None, first.computed_at),
        (weeks[1], 12.0, 8.0, second.computed_at),
        (weeks[2], 4.0, -8.0, second.computed_at),
    ]


def test_a_late_earlier_snapshot_becomes_the_next_ones_previous(db, fetch_id) -> None:
    _snapshot(db, fetch_id, date(2024, 7, 5), large="1.00")
    _snapshot(db, fetch_id, date(2024, 7, 19), large="2.00")
    derived_store.run(db, CONCENTRATION)
    _snapshot(db, fetch_id, date(2024, 7, 12), large="1.50")

    derived_store.run(db, CONCENTRATION)

    assert _concentration(db, "large_holder_ratio_wow") == [
        (date(2024, 7, 5), None), (date(2024, 7, 12), 2.0), (date(2024, 7, 19), 2.0)]


def test_an_incremental_concentration_equals_the_full_one(db, fetch_id) -> None:
    weeks = [date(2021, 1, 1) + timedelta(weeks=i) for i in range(60)]
    for index, week in enumerate(weeks[:59]):
        _snapshot(db, fetch_id, week, large=f"{index % 9 + 1}.25", holders=index + 1)
    derived_store.run(db, CONCENTRATION)
    _snapshot(db, fetch_id, weeks[59], large="7.75")
    derived_store.run(db, CONCENTRATION)
    columns = tuple(c.name for c in shareholding_concentration.c
                    if c.name not in ("stock_id", "source", "snapshot_date", "computed_at"))

    incremental = _concentration(db, *columns)
    derived_store.run(db, CONCENTRATION, full=True)

    assert incremental == _concentration(db, *columns)
    assert len(incremental) == 60


def test_a_concentration_uses_no_snapshot_dated_after_it(db, fetch_id) -> None:
    weeks = [date(2024, 7, 5) + timedelta(weeks=i) for i in range(6)]
    for week in weeks[:3]:
        _snapshot(db, fetch_id, week, large="2.00")
    derived_store.run(db, CONCENTRATION)
    early = _concentration(db, "large_holder_ratio", "large_holder_ratio_wow")
    for week in weeks[3:]:
        _snapshot(db, fetch_id, week, large="9.00")

    derived_store.run(db, CONCENTRATION, full=True)

    assert _concentration(db, "large_holder_ratio", "large_holder_ratio_wow")[:3] == early


# ---------------------------------------------------------------- margin and short interest


def _margin(db, fetch_id, day, *, balance=1_500, previous=1_000, limit=10_000,
            source=MI_MARGN, stock_id="2330") -> None:
    db.execute(sa.insert(margin_trading).values(
        stock_id=stock_id, source=source, trade_date=day, margin_buy=0, margin_sell=0,
        margin_cash_repayment=0, margin_previous_balance=previous, margin_balance=balance,
        margin_limit=limit, short_buy=30, short_sell=0, short_stock_repayment=10,
        short_previous_balance=200, short_balance=150, short_limit=limit, offset_balance=0,
        fetch_id=fetch_id))


def _lending(db, fetch_id, day, *, balance=4_500, previous=4_000, sold=900, returned=400,
             stock_id="2330") -> None:
    db.execute(sa.insert(securities_lending).values(
        stock_id=stock_id, source=TWT93U, trade_date=day, previous_balance=previous,
        sold=sold, returned=returned, adjustment=0, balance=balance, fetch_id=fetch_id))


def _rows(db, table, *columns, stock_id="2330") -> list[tuple]:
    t = table.c
    return [tuple(row) for row in db.execute(
        sa.select(t.trade_date, *(t[c] for c in columns))
        .where(t.stock_id == stock_id).order_by(t.trade_date))]


def test_the_margin_and_short_interest_definitions() -> None:
    assert (MARGIN.definition.dataset_code, MARGIN.definition.derivation_version) == (
        "margin_metrics", "v1")
    assert MARGIN.definition.input_tables == ("margin_trading",)
    assert (SHORT_INTEREST.definition.dataset_code,
            SHORT_INTEREST.definition.derivation_version) == ("short_interest_metrics", "v1")
    assert SHORT_INTEREST.definition.input_tables == ("securities_lending",)


def test_margin_metrics_come_from_each_days_own_row(db, fetch_id) -> None:
    # Legacy: a change is the day's balance minus the previous balance the
    # source publishes on the same row, not the stored row before it.
    days = _trading_days(date(2024, 7, 1), 2)
    _margin(db, fetch_id, days[0], balance=1_500, previous=1_000)
    _margin(db, fetch_id, days[1], balance=1_200, previous=1_600)  # the source's own previous

    run = derived_store.run(db, MARGIN)

    assert (run.series, run.rows) == (1, 2)
    assert _rows(db, margin_metrics, "source", "margin_usage_ratio", "margin_balance_change",
                 "margin_balance_change_pct", "short_cover_pressure") == [
        (days[0], MI_MARGN, 15.0, 500, 50.0, 20.0),
        (days[1], MI_MARGN, 12.0, -400, -25.0, 20.0),
    ]


def test_short_interest_metrics_come_from_each_days_own_row(db, fetch_id) -> None:
    days = _trading_days(date(2024, 7, 1), 2)
    _lending(db, fetch_id, days[0])
    _lending(db, fetch_id, days[1], balance=100, previous=0, sold=100, returned=0)

    derived_store.run(db, SHORT_INTEREST)

    assert _rows(db, short_interest_metrics, "source", "sbl_balance_change",
                 "sbl_balance_change_pct", "sbl_sell_repay_ratio") == [
        (days[0], TWT93U, 500, 12.5, 2.25),
        (days[1], TWT93U, 100, None, None),
    ]


def test_a_corrected_margin_row_recomputes_its_day(db, fetch_id) -> None:
    days = _trading_days(date(2024, 7, 1), 3)
    for day in days:
        _margin(db, fetch_id, day)
    first = derived_store.run(db, MARGIN)
    _margin(db, fetch_id, days[1], balance=2_500)  # a correction of the second day

    second = derived_store.run(db, MARGIN)

    assert _rows(db, margin_metrics, "margin_balance_change", "computed_at") == [
        (days[0], 500, first.computed_at),
        (days[1], 1_500, second.computed_at),
        (days[2], 500, second.computed_at),
    ]


def test_each_margin_and_lending_source_is_its_own_series(db, fetch_id) -> None:
    day = date(2024, 7, 1)
    _margin(db, fetch_id, day)
    _margin(db, fetch_id, day + timedelta(days=1), source="tpex_margin_balance", limit=0)
    derived_store.run(db, MARGIN)
    assert _rows(db, margin_metrics, "source", "margin_usage_ratio") == [
        (day, MI_MARGN, 15.0), (day + timedelta(days=1), "tpex_margin_balance", None)]


def test_an_incremental_margin_run_equals_the_full_one(db, fetch_id) -> None:
    days = _trading_days(date(2021, 1, 1), 60)
    for index, day in enumerate(days[:59]):
        _margin(db, fetch_id, day, balance=1_000 + 37 * index, previous=1_000 + 11 * index)
        _lending(db, fetch_id, day, balance=5_000 + index, sold=index, returned=index % 4)
    for dataset in (MARGIN, SHORT_INTEREST):
        derived_store.run(db, dataset)
    _margin(db, fetch_id, days[59], balance=9_999)
    _lending(db, fetch_id, days[59], balance=1)

    for dataset, table, columns in ((MARGIN, margin_metrics, derived_store.MARGIN_COLUMNS),
                                    (SHORT_INTEREST, short_interest_metrics,
                                     derived_store.SHORT_INTEREST_COLUMNS)):
        derived_store.run(db, dataset)
        incremental = _rows(db, table, *columns)
        derived_store.run(db, dataset, full=True)
        assert incremental == _rows(db, table, *columns), table.name
        assert len(incremental) == 60


def test_a_margin_value_uses_no_row_dated_after_it(db, fetch_id) -> None:
    days = _trading_days(date(2024, 7, 1), 6)
    for day in days[:3]:
        _margin(db, fetch_id, day)
        _lending(db, fetch_id, day)
    for dataset in (MARGIN, SHORT_INTEREST):
        derived_store.run(db, dataset)
    early = (_rows(db, margin_metrics, *derived_store.MARGIN_COLUMNS),
             _rows(db, short_interest_metrics, *derived_store.SHORT_INTEREST_COLUMNS))
    for day in days[3:]:
        _margin(db, fetch_id, day, balance=1, previous=9, limit=3)
        _lending(db, fetch_id, day, balance=1, previous=9, sold=1, returned=3)

    for dataset in (MARGIN, SHORT_INTEREST):
        derived_store.run(db, dataset, full=True)

    assert (_rows(db, margin_metrics, *derived_store.MARGIN_COLUMNS)[:3],
            _rows(db, short_interest_metrics, *derived_store.SHORT_INTEREST_COLUMNS)[:3]) == early


# ---------------------------------------------------------------- storage


def test_derived_rows_are_overwritten_not_appended(db, fetch_id) -> None:
    # §43: derived tables follow the latest inputs; they are not history.
    _prices(db, fetch_id, _trading_days(date(2024, 1, 1), 3))
    derived_store.run(db, TECH)
    db.execute(sa.text("UPDATE technical_indicators SET ma5 = 1"))
    db.execute(sa.text("DELETE FROM technical_indicators"))


def test_the_command_line_runs_one_dataset(monkeypatch, capsys) -> None:
    calls = []

    class _Engine:
        def begin(self):
            class _Tx:
                def __enter__(self):
                    return "connection"

                def __exit__(self, *exc):
                    return False
            return _Tx()

        def dispose(self):
            pass

    monkeypatch.setattr(derived_store.sa, "create_engine", lambda url: _Engine())
    monkeypatch.setattr(
        derived_store, "run",
        lambda connection, dataset, full=False: calls.append((connection, dataset, full))
        or derived_store.RunResult(datetime(2026, 9, 24, tzinfo=UTC), None, 1, 2))
    assert derived_store.main(["--database-url", "x", "--dataset", "institutional_streaks",
                               "--full"]) == 0
    assert calls == [("connection", STREAKS, True)]
    assert "institutional_streaks" in capsys.readouterr().out

