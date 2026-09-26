"""Step 28-a: what the scheduler decides for one period, from its fetches alone.

The decision is a pure function of the declaration, the period, the period's
fetch attempts and the current instant: no fetch happens to make it, so the
whole set of pending jobs can be listed before anything is fetched.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from stock_data_center.v2 import schedule as s
from stock_data_center.v2.backfill import ALL_KEYS

TAIPEI = ZoneInfo("Asia/Taipei")
DAY = date(2026, 9, 30)  # a Wednesday


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TAIPEI)


def attempt(when: datetime, status: str = "succeeded", reason: str | None = None,
            purpose: str = "first_capture") -> s.Attempt:
    return s.Attempt(when.astimezone(UTC), status, reason, purpose)


EARLIEST = at(DAY, 15)
SETTLED = at(DAY + timedelta(days=1), 3)
ESCALATE = at(DAY + timedelta(days=1), 8)
RECHECK = SETTLED + timedelta(days=7)

TEST = s.Declaration(
    key="test/daily", dataset="d", source="x",
    periods=lambda context: [DAY],
    resources=lambda period: (f"x:{period}",),
    earliest=lambda period: EARLIEST,
    settled=lambda period: SETTLED,
    escalate=lambda period: ESCALATE,
    recheck=lambda period: RECHECK,
    capture_until=lambda period: SETTLED + timedelta(days=1),
    give_up=timedelta(days=14),
)
REFRESHING = s.Declaration(
    key="test/monthly", dataset="d", source="x",
    periods=lambda context: [DAY],
    resources=lambda period: (f"x:{period}",),
    earliest=lambda period: EARLIEST,
    settled=lambda period: SETTLED + timedelta(days=30),
    refresh=timedelta(hours=1),
    stale_after=timedelta(days=1),
    capture_until=lambda period: SETTLED + timedelta(days=31),
    give_up=timedelta(days=60),
)


def decide(declaration, attempts, now):
    return s.decide(declaration, DAY, attempts, now.astimezone(UTC))


# ------------------------------------------------------------ never fetched


def test_nothing_is_asked_before_the_earliest_fetch_time() -> None:
    decision = decide(TEST, [], at(DAY, 14, 59))
    assert (decision.state, decision.dispatch) == ("not_due", False)


def test_a_period_never_fetched_is_dispatched_as_a_first_capture() -> None:
    decision = decide(TEST, [], EARLIEST)
    assert (decision.state, decision.dispatch, decision.purpose) == (
        "missing", True, "first_capture")
    assert decision.overdue is False


def test_a_failed_attempt_waits_for_the_next_hour_without_a_retry_timer() -> None:
    tried = [attempt(at(DAY, 18), "empty", "no_data_for_date")]
    waiting = decide(TEST, tried, at(DAY, 18, 59))
    assert (waiting.state, waiting.dispatch) == ("missing", False)
    again = decide(TEST, tried, at(DAY, 19))
    assert (again.state, again.dispatch, again.last_reason) == (
        "missing", True, "no_data_for_date")


def test_still_missing_at_the_escalation_time_is_overdue() -> None:
    tried = [attempt(at(DAY, 18), "failed", "fetch_error")]
    decision = decide(TEST, tried, ESCALATE)
    assert (decision.state, decision.overdue, decision.dispatch) == ("missing", True, True)
    assert (decision.first_missing, decision.last_reason) == (
        at(DAY, 18).astimezone(UTC), "fetch_error")


def test_missing_for_more_than_a_day_is_asked_daily_not_hourly() -> None:
    next_day = DAY + timedelta(days=1)
    tried = [attempt(at(DAY, 18), "failed"), attempt(at(next_day, 18, 30), "failed")]
    assert decide(TEST, tried, at(next_day, 19, 30)).dispatch is False
    assert decide(TEST, tried, at(next_day + timedelta(days=1), 18, 30)).dispatch is True


def test_a_period_missing_past_its_give_up_time_is_no_longer_asked() -> None:
    tried = [attempt(at(DAY, 18), "empty", "no_data_for_date")]
    decision = decide(TEST, tried, at(DAY, 18) + timedelta(days=14))
    assert (decision.state, decision.dispatch, decision.overdue) == ("given_up", False, True)


def test_a_success_that_held_rows_back_is_not_complete() -> None:
    tried = [attempt(SETTLED + timedelta(hours=1), "succeeded", "close_unverified")]
    decision = decide(TEST, tried, SETTLED + timedelta(hours=3))
    assert (decision.state, decision.dispatch) == ("missing", True)


def test_a_fetch_long_after_the_capture_window_is_a_gap_fill() -> None:
    decision = decide(TEST, [], SETTLED + timedelta(days=1))
    assert (decision.dispatch, decision.purpose) == (True, "gap_fill")


# ------------------------------------------------------------ fetched before it settled


def test_a_success_before_the_period_settles_is_fresh_until_it_settles() -> None:
    tried = [attempt(at(DAY, 16))]
    assert (decide(TEST, tried, at(DAY, 23)).state,
            decide(TEST, tried, at(DAY, 23)).dispatch) == ("fresh", False)
    settled = decide(TEST, tried, SETTLED)
    assert (settled.state, settled.dispatch, settled.purpose) == (
        "unsettled", True, "first_capture")


def test_unsettled_at_the_escalation_time_is_overdue() -> None:
    tried = [attempt(at(DAY, 16)), attempt(SETTLED, "failed", "fetch_error")]
    decision = decide(TEST, tried, ESCALATE)
    assert (decision.state, decision.overdue) == ("unsettled", True)
    assert decision.first_missing == SETTLED.astimezone(UTC)


def test_a_refreshing_period_is_asked_again_once_its_copy_is_an_hour_old() -> None:
    tried = [attempt(at(DAY, 16))]
    assert decide(REFRESHING, tried, at(DAY, 16, 59)).state == "fresh"
    refresh = decide(REFRESHING, tried, at(DAY, 17))
    assert (refresh.state, refresh.dispatch, refresh.overdue) == ("refresh", True, False)


def test_a_refreshing_period_whose_copy_is_stale_is_overdue() -> None:
    tried = [attempt(at(DAY, 16)), attempt(at(DAY + timedelta(days=1), 16, 30), "failed")]
    decision = decide(REFRESHING, tried, at(DAY + timedelta(days=1), 17))
    assert (decision.state, decision.overdue, decision.dispatch) == ("refresh", True, False)


# ------------------------------------------------------------ settled


def test_a_success_after_the_period_settled_is_done() -> None:
    tried = [attempt(at(DAY, 16)), attempt(SETTLED + timedelta(minutes=5))]
    decision = decide(TEST, tried, SETTLED + timedelta(days=2))
    assert (decision.state, decision.dispatch) == ("done", False)


def test_a_settled_period_is_checked_once_more_for_corrections() -> None:
    tried = [attempt(SETTLED + timedelta(minutes=5))]
    decision = decide(TEST, tried, RECHECK)
    assert (decision.state, decision.dispatch, decision.purpose) == (
        "recheck", True, "correction_check")
    checked = tried + [attempt(RECHECK + timedelta(minutes=1), purpose="correction_check")]
    assert decide(TEST, checked, RECHECK + timedelta(hours=2)).state == "done"


# ------------------------------------------------------------ the declarations


def test_every_backfill_job_and_the_calendar_has_a_declaration() -> None:
    assert set(s.DECLARATIONS) == {*ALL_KEYS, "trading_days/twse"}


def test_exchange_daily_times() -> None:
    prices = s.DECLARATIONS["daily_prices/twse_mi_index"]
    assert prices.earliest(DAY) == at(DAY, 15)
    assert prices.settled(DAY) == SETTLED
    assert prices.escalate(DAY) == ESCALATE
    assert prices.recheck(DAY) == SETTLED + timedelta(days=7)
    assert prices.capture_until(DAY) == SETTLED + timedelta(days=1)
    assert s.DECLARATIONS["margin_trading/twse_mi_margn"].earliest(DAY) == at(DAY, 21)
    assert s.DECLARATIONS["securities_lending/tpex_margin_sbl"].earliest(DAY) == at(DAY, 21)


def test_monthly_revenue_is_refreshed_hourly_through_its_settling_month() -> None:
    revenue = s.DECLARATIONS["monthly_revenues/mops_t21sc03_sii/foreign"]
    month = date(2026, 9, 1)
    assert revenue.earliest(month) == at(date(2026, 10, 1), 0)
    assert revenue.settled(month) == at(date(2026, 11, 1), 0)
    assert revenue.refresh == timedelta(hours=1)
    assert revenue.escalate(month) == at(date(2026, 10, 11), 8)
    assert revenue.capture_until(month) == at(date(2026, 11, 2), 0)
    assert revenue.recheck is None


def test_financial_reports_are_asked_daily_from_35_days_before_the_deadline() -> None:
    reports = s.DECLARATIONS["financial_reports/mops_t164sb01"]
    q3 = ("2330", 2026, 3)
    assert reports.earliest(q3) == at(date(2026, 10, 11), 0)
    assert reports.settled(q3) == at(date(2026, 11, 16), 0)
    assert reports.retry == timedelta(days=1)
    assert reports.escalate is None  # a late filer is not a gap
    assert reports.resources(q3) == ("mops_t164sb01:financial_filing:2330:2026Q3:C",
                                     "mops_t164sb01:financial_filing:2330:2026Q3:A")


def test_a_final_quarantine_completes_a_financial_report() -> None:
    reports = s.DECLARATIONS["financial_reports/mops_t164sb01"]
    q3 = ("2881", 2026, 3)
    after = at(date(2026, 11, 20), 9)
    decision = s.decide(reports, q3, [attempt(after, "quarantined", "financial_industry_issuer")],
                        after + timedelta(days=1))
    assert decision.state == "done"


def test_the_calendar_and_the_yearly_feeds_refresh_until_their_period_ends() -> None:
    calendar = s.DECLARATIONS["trading_days/twse"]
    month = date(2026, 10, 1)
    assert calendar.earliest(month) == at(month, 0)
    assert calendar.settled(month) == at(date(2026, 11, 1), 3)
    assert calendar.refresh == timedelta(hours=1)
    dividends = s.DECLARATIONS["corporate_actions/twse_twt49u"]
    assert dividends.earliest(2026) == at(date(2026, 1, 1), 0)
    assert dividends.settled(2026) == at(date(2027, 1, 1), 0)
    assert dividends.refresh == timedelta(days=1)


def test_tdcc_has_one_period_refreshed_daily() -> None:
    tdcc = s.DECLARATIONS["shareholding_distributions/tdcc_opendata"]
    assert tdcc.settled is None and tdcc.refresh == timedelta(days=1)
    assert tdcc.resources(None) == ("tdcc_opendata:opendata:1-5",)


def _example(key: str):
    if key.startswith("financial_reports/"):
        return ("2330", 2026, 3)
    if key.startswith("corporate_actions/"):
        return 2026
    if key.startswith("shareholding_distributions/"):
        return None
    return date(2026, 9, 1)


@pytest.mark.parametrize("key", sorted({*ALL_KEYS, "trading_days/twse"}))
def test_every_declaration_captures_no_later_than_a_day_after_it_settles(key) -> None:
    declaration = s.DECLARATIONS[key]
    period = _example(key)
    if declaration.settled is not None:
        assert declaration.capture_until(period) == (
            declaration.settled(period) + timedelta(days=1))
