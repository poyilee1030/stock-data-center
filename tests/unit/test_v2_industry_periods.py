"""Step 39-c: a stock's industry periods, derived at query time (ADR-0030 §3).

A listing span's periods come from the changes its market's exchange announced
and the anchor of its last period: today's ISIN category for an open span, the
by-category quote of its last trading day for one that ended. The period
before the first change is public from its own start (owner decision 3); each
later one from its notice's release instant (decision 2), a correction from
when it was recorded. What is not yet public never leaks through a period's
end: before a change is public its period has no end.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from stock_data_center.v2 import visibility as vis
from stock_data_center.v2.release_rules import industry_announcement_available_from

TAIPEI = ZoneInfo("Asia/Taipei")
RECORDED = datetime(2026, 9, 26, 1, 0, tzinfo=UTC)
LATEST = vis.MarketPIT(information_as_of=vis.FOREVER, knowledge_as_of=vis.FOREVER)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), TAIPEI)


def _pit(information: datetime, knowledge: datetime = vis.FOREVER) -> vis.MarketPIT:
    return vis.MarketPIT(information_as_of=information, knowledge_as_of=knowledge)


def _change(announced: date, old: str, new: str, *, recorded: datetime = RECORDED,
            source: str = "twse_announcement", available: datetime | None = None) -> vis.ChangeVersion:
    return vis.ChangeVersion(
        source=source, announced_on=announced, old_code=old, new_code=new,
        available_at=available or industry_announcement_available_from(announced),
        recorded_at=recorded, fetch_id=uuid4(), attachment_fetch_id=None)


def _isin(code: str, recorded: datetime = RECORDED) -> vis.Anchor:
    return vis.Anchor(code=code, source="twse_isin", recorded_at=recorded, fetch_id=uuid4())


def _span(start=date(2020, 1, 2), end=None, market="sii", stock_id="3130") -> vis.Span:
    return vis.Span(stock_id=stock_id, market=market, start=start, end=end)


def _brief(periods) -> list[tuple]:
    return [(p.industry_code, p.industry_name, p.effective_from, p.effective_to, p.basis)
            for p in periods]


# ---------------------------------------------------------------- the chain


def test_a_stock_never_reclassified_has_one_period_from_its_start() -> None:
    [period] = vis.industry_periods_of(_span(stock_id="2330"), {}, _isin("24"), LATEST)
    assert _brief([period]) == [("24", "半導體業", date(2020, 1, 2), None, "anchor")]
    assert period.available_at == _midnight(date(2020, 1, 2))
    assert period.source == "twse_isin" and period.recorded_at == RECORDED


def test_a_change_splits_the_span_and_is_public_from_the_day_after_its_notice() -> None:
    changes = {date(2023, 7, 3): [_change(date(2023, 5, 22), "30", "36")]}
    before, after = vis.industry_periods_of(_span(), changes, _isin("36"), LATEST)
    assert _brief([before, after]) == [
        ("30", "資訊服務業", date(2020, 1, 2), date(2023, 7, 3), "before_change"),
        ("36", "數位雲端", date(2023, 7, 3), None, "change")]
    assert before.available_at == _midnight(date(2020, 1, 2))  # in use, so public (decision 3)
    assert after.available_at == _midnight(date(2023, 5, 23))
    assert before.source == after.source == "twse_announcement"


def test_before_its_notice_is_public_a_change_does_not_leak_through_a_period_end() -> None:
    changes = {date(2023, 7, 3): [_change(date(2023, 5, 22), "30", "36")]}
    seen = vis.industry_periods_of(_span(), changes, _isin("36"),
                                   _pit(datetime(2023, 5, 22, 23, 59, tzinfo=TAIPEI)))
    assert _brief(seen) == [("30", "資訊服務業", date(2020, 1, 2), None, "before_change")]
    seen = vis.industry_periods_of(_span(), changes, _isin("36"),
                                   _pit(_midnight(date(2023, 5, 23))))
    assert [p.effective_to for p in seen] == [date(2023, 7, 3), None]


def test_a_correction_is_seen_only_from_when_it_was_recorded() -> None:
    corrected = datetime(2026, 10, 1, tzinfo=UTC)
    changes = {date(2026, 9, 1): [
        _change(date(2026, 8, 19), "02", "29"),
        _change(date(2026, 8, 19), "02", "28", recorded=corrected, available=corrected)]}
    before = vis.industry_periods_of(_span(stock_id="3054"), changes, _isin("28"),
                                     _pit(corrected - timedelta(seconds=1)))
    assert [p.industry_code for p in before] == ["02", "29"]
    after = vis.industry_periods_of(_span(stock_id="3054"), changes, _isin("28"), LATEST)
    assert [p.industry_code for p in after] == ["02", "28"]
    assert after[1].available_at == corrected


def test_nothing_recorded_by_knowledge_as_of_gives_no_period() -> None:
    changes = {date(2023, 7, 3): [_change(date(2023, 5, 22), "30", "36")]}
    early = _pit(vis.FOREVER, RECORDED - timedelta(seconds=1))
    assert vis.industry_periods_of(_span(), changes, _isin("36"), early) == []


def test_a_change_recorded_after_knowledge_as_of_is_not_used() -> None:
    later = RECORDED + timedelta(days=1)
    changes = {date(2023, 7, 3): [_change(date(2023, 5, 22), "30", "36", recorded=later)]}
    seen = vis.industry_periods_of(_span(), changes, _isin("36"), _pit(vis.FOREVER, RECORDED))
    # Only today's category is known then, and 數位雲端 did not exist before 2023-07-03.
    assert _brief(seen) == [("36", "數位雲端", date(2023, 7, 3), None, "anchor")]


def test_a_closed_span_ends_at_its_delisting_once_that_has_happened() -> None:
    span = _span(end=date(2025, 7, 24), stock_id="2888")
    anchor = vis.Anchor(code="17", source="twse_mi_index", recorded_at=RECORDED, fetch_id=uuid4())
    [period] = vis.industry_periods_of(span, {}, anchor, LATEST)
    assert _brief([period]) == [("17", "金融保險業", date(2020, 1, 2), date(2025, 7, 24), "anchor")]
    [period] = vis.industry_periods_of(span, {}, anchor, _pit(_midnight(date(2025, 7, 23))))
    assert period.effective_to is None


def test_system_pit_sees_every_recorded_period_whatever_its_publication() -> None:
    changes = {date(2023, 7, 3): [_change(date(2023, 5, 22), "30", "36")]}
    seen = vis.industry_periods_of(_span(end=date(2026, 1, 5)), changes, _isin("36"),
                                   vis.SystemPIT(RECORDED))
    assert [(p.industry_code, p.effective_to) for p in seen] == [
        ("30", date(2023, 7, 3)), ("36", date(2026, 1, 5))]
    assert vis.industry_periods_of(_span(), changes, _isin("36"),
                                   vis.SystemPIT(RECORDED - timedelta(seconds=1))) == []


def test_the_2023_rename_splits_a_tourism_period() -> None:
    periods = vis.industry_periods_of(_span(stock_id="2707"), {}, _isin("16"), LATEST)
    assert _brief(periods) == [
        ("16", "觀光事業", date(2020, 1, 2), date(2023, 7, 3), "anchor"),
        ("16", "觀光餐旅", date(2023, 7, 3), None, "rename")]
    # Both exchanges announced the rename on 2023-03-28 (decision 2).
    assert periods[1].available_at == _midnight(date(2023, 3, 29))
    early = vis.industry_periods_of(_span(stock_id="2707"), {}, _isin("16"),
                                    _pit(_midnight(date(2023, 3, 28))))
    assert _brief(early) == [("16", "觀光事業", date(2020, 1, 2), None, "anchor")]


def test_a_change_into_tourism_on_the_rename_day_takes_the_new_name() -> None:
    changes = {date(2023, 7, 3): [_change(date(2023, 5, 22), "20", "16")]}
    periods = vis.industry_periods_of(_span(stock_id="2739"), changes, _isin("16"), LATEST)
    assert _brief(periods) == [
        ("20", "其他業", date(2020, 1, 2), date(2023, 7, 3), "before_change"),
        ("16", "觀光餐旅", date(2023, 7, 3), None, "change")]


def test_a_period_starts_no_earlier_than_its_category_existed() -> None:
    # An anchor alone, of a category created on 2023-07-03, says nothing earlier.
    [period] = vis.industry_periods_of(_span(), {}, _isin("36"), LATEST)
    assert (period.effective_from, period.available_at) == (
        date(2023, 7, 3), _midnight(date(2023, 7, 3)))


def test_a_period_ends_no_later_than_its_category_existed() -> None:
    # TPEx's 電子商務 was merged into 數位雲端 on 2023-07-03.
    span = _span(market="otc", end=date(2024, 5, 2), stock_id="8477")
    anchor = vis.Anchor(code="34", source="tpex_otc_quotes", recorded_at=RECORDED, fetch_id=uuid4())
    [period] = vis.industry_periods_of(span, {}, anchor, LATEST)
    assert (period.effective_from, period.effective_to) == (date(2020, 1, 2), date(2023, 7, 3))


def test_a_span_without_an_anchor_or_a_change_has_no_period() -> None:
    assert vis.industry_periods_of(_span(end=date(2020, 1, 9)), {}, None, LATEST) == []


def test_a_span_without_an_anchor_still_has_its_announced_periods() -> None:
    changes = {date(2024, 6, 3): [_change(date(2024, 5, 15), "02", "20",
                                          source="tpex_announcement")]}
    periods = vis.industry_periods_of(_span(market="otc", end=date(2024, 9, 25)), changes,
                                      None, LATEST)
    assert _brief(periods) == [
        ("02", "食品工業", date(2020, 1, 2), date(2024, 6, 3), "before_change"),
        ("20", "其他業", date(2024, 6, 3), date(2024, 9, 25), "change")]


def test_a_listing_starts_its_first_period() -> None:
    [period] = vis.industry_periods_of(_span(start=date(2024, 3, 1), stock_id="6919"), {},
                                       _isin("22"), LATEST)
    assert (period.effective_from, period.available_at) == (
        date(2024, 3, 1), _midnight(date(2024, 3, 1)))


# ---------------------------------------------------------------- consistency


def test_a_chain_that_does_not_link_is_reported() -> None:
    changes = {date(2021, 6, 1): [_change(date(2021, 5, 4), "30", "20")],
               date(2023, 7, 3): [_change(date(2023, 5, 22), "31", "36")]}
    issues = vis.industry_chain_issues(_span(), changes, _isin("38"))
    assert issues == [
        "2023-07-03 starts from 31, but 2021-06-01 left it in 20",
        "the last period is 36, but the anchor (twse_isin) says 38"]
    assert vis.industry_chain_issues(
        _span(), {date(2023, 7, 3): [_change(date(2023, 5, 22), "30", "36")]}, _isin("36")) == []


def test_a_category_outside_its_existence_is_reported() -> None:
    assert vis.industry_chain_issues(_span(), {}, _isin("36")) == [
        "36 did not exist on sii from 2020-01-02; its period starts 2023-07-03"]


@pytest.mark.parametrize("pit", [LATEST, vis.SystemPIT(vis.FOREVER)])
def test_every_period_names_the_row_it_came_from(pit) -> None:
    change = _change(date(2023, 5, 22), "30", "36")
    anchor = _isin("36")
    periods = vis.industry_periods_of(_span(), {date(2023, 7, 3): [change]}, anchor, pit)
    assert [p.fetch_id for p in periods] == [change.fetch_id, change.fetch_id]
    assert all(p.recorded_at == RECORDED for p in periods)
    assert periods[0].available_at.tzinfo is not None and periods[0].market == "sii"


def test_a_quote_anchor_is_checked_against_the_category_of_its_own_day() -> None:
    # 4712 last traded 2024-02-05 in 食品工業 and changed to 其他 on 2024-06-03,
    # before it left TPEx on 2024-09-25: the anchor predates the last change.
    changes = {date(2024, 6, 3): [_change(date(2024, 5, 15), "02", "20",
                                          source="tpex_announcement")]}
    anchor = vis.Anchor(code="02", source="tpex_otc_quotes", recorded_at=RECORDED,
                        fetch_id=uuid4(), on=date(2024, 2, 5))
    span = _span(market="otc", end=date(2024, 9, 25), stock_id="4712")
    assert vis.industry_chain_issues(span, changes, anchor) == []
    wrong = vis.Anchor(code="20", source="tpex_otc_quotes", recorded_at=RECORDED,
                       fetch_id=uuid4(), on=date(2024, 2, 5))
    assert vis.industry_chain_issues(span, changes, wrong) == [
        "on 2024-02-05 the chain says 02, but the anchor (tpex_otc_quotes) says 20"]


# ---------------------------------------------------------------- code review of #74


def test_the_category_before_a_change_is_the_one_public_then() -> None:
    # A correction of a change's old category is public from its own recorded_at.
    corrected = datetime(2026, 10, 1, tzinfo=UTC)
    first = _change(date(2023, 5, 22), "30", "36")
    fixed = _change(date(2023, 5, 22), "31", "36", recorded=corrected, available=corrected)
    changes = {date(2023, 7, 3): [first, fixed]}
    before = vis.industry_periods_of(_span(), changes, _isin("36"),
                                     _pit(corrected - timedelta(seconds=1)))
    assert (before[0].industry_code, before[0].fetch_id) == ("30", first.fetch_id)
    after = vis.industry_periods_of(_span(), changes, _isin("36"), LATEST)
    assert (after[0].industry_code, after[0].fetch_id) == ("31", fixed.fetch_id)
    # Before the notice itself is public, the category in use is the original's.
    early = vis.industry_periods_of(_span(), changes, _isin("36"),
                                    _pit(_midnight(date(2023, 5, 1))))
    assert [(p.industry_code, p.effective_to) for p in early] == [("30", None)]


def test_a_category_that_ceased_ends_its_period_even_before_a_later_change() -> None:
    # TPEx's 電子商務 ended on 2023-07-03; a company announced out of it later
    # does not stay in it past its end (code review of #74).
    changes = {date(2024, 6, 3): [_change(date(2024, 5, 15), "34", "36",
                                          source="tpex_announcement")]}
    periods = vis.industry_periods_of(_span(market="otc", stock_id="8477"), changes,
                                      _isin("36"), LATEST)
    assert [(p.industry_code, p.effective_from, p.effective_to) for p in periods] == [
        ("34", date(2020, 1, 2), date(2023, 7, 3)), ("36", date(2024, 6, 3), None)]
