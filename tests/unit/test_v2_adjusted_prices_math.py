"""Step 36: the adjustment arithmetic of `adjusted_prices_pit:v1`, without a database.

ROADMAP §18 and CLAUDE.md §80: every exchange result event on ex-date D has
`factor(D) = reference_price(D) / close_before(D)`; a price on date t is
multiplied by the product of the factors of the events after t, up to the
series' last price, so the last price is its own adjusted value. Raw prices are
never changed, and an event whose factor is unknown leaves every earlier
adjusted price unknown rather than silently unadjusted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from stock_data_center.v2 import adjusted_prices as ap
from stock_data_center.v2.corporate_actions import FEEDS

D1, D2, D3, D4, D5 = (date(2024, 7, d) for d in (1, 2, 3, 4, 5))


def _bar(day: date, close: str | None) -> ap.Bar:
    value = None if close is None else Decimal(close)
    return ap.Bar(day, value, value, value, value)


def _event(day: date, close_before: str | None, reference: str | None) -> ap.Event:
    return ap.Event(day, None if close_before is None else Decimal(close_before),
                    None if reference is None else Decimal(reference))


def _factors(rows) -> list[float | None]:
    return [row.factor for row in rows]


def test_without_events_the_adjusted_series_is_the_raw_one() -> None:
    rows = ap.adjust([_bar(D1, "100"), _bar(D2, "101")], [])
    assert _factors(rows) == [1.0, 1.0]
    assert [row.adjusted_close for row in rows] == [100.0, 101.0]


def test_an_event_scales_every_earlier_price_by_its_factor() -> None:
    rows = ap.adjust([_bar(D1, "100"), _bar(D2, "100"), _bar(D3, "91"), _bar(D4, "92")],
                     [_event(D3, "100", "90")])
    assert _factors(rows) == [pytest.approx(0.9), pytest.approx(0.9), 1.0, 1.0]
    assert rows[1].adjusted_close == pytest.approx(90.0)
    assert [row.adjusted_close for row in rows[2:]] == [91.0, 92.0]
    # Open, high and low take the same factor; the raw prices stay as they are.
    assert rows[0].adjusted_open == rows[0].adjusted_high == rows[0].adjusted_low \
        == pytest.approx(90.0)
    assert rows[0].bar == _bar(D1, "100")


def test_the_event_day_is_continuous_when_close_before_is_the_previous_close() -> None:
    # CLAUDE.md §80: the adjusted return into the ex-date is measured against the
    # reference price, not the raw previous close.
    rows = ap.adjust([_bar(D1, "50"), _bar(D2, "40"), _bar(D3, "36")],
                     [_event(D2, "50", "37.5")])
    assert rows[1].adjusted_close / rows[0].adjusted_close == pytest.approx(40 / 37.5)


def test_factors_accumulate_backwards() -> None:
    rows = ap.adjust([_bar(D1, "100"), _bar(D2, "90"), _bar(D3, "80"), _bar(D4, "70")],
                     [_event(D4, "80", "40"), _event(D2, "100", "90")])
    assert _factors(rows) == [pytest.approx(0.45), pytest.approx(0.5), pytest.approx(0.5), 1.0]


def test_events_on_one_day_multiply() -> None:
    rows = ap.adjust([_bar(D1, "100"), _bar(D2, "50")],
                     [_event(D2, "100", "80"), _event(D2, "100", "50")])
    assert rows[0].factor == pytest.approx(0.4)


def test_the_series_is_anchored_at_its_last_price() -> None:
    # An event after the last price is not applied yet: it is applied when the
    # price of its ex-date arrives, so the last price is always its own
    # adjusted value.
    rows = ap.adjust([_bar(D1, "100"), _bar(D2, "100")], [_event(D4, "100", "50")])
    assert _factors(rows) == [1.0, 1.0]


def test_an_event_before_the_first_price_changes_nothing() -> None:
    rows = ap.adjust([_bar(D2, "100"), _bar(D3, "100")], [_event(D1, "100", "50"),
                                                          _event(D2, "100", "50")])
    assert _factors(rows) == [1.0, 1.0]


def test_an_unknown_factor_leaves_every_earlier_price_unknown() -> None:
    rows = ap.adjust([_bar(D1, "100"), _bar(D2, "100"), _bar(D3, "100"), _bar(D4, "100")],
                     [_event(D3, None, "90"), _event(D4, "100", "50")])
    assert _factors(rows) == [None, None, pytest.approx(0.5), 1.0]
    assert [row.adjusted_close for row in rows] == [None, None, pytest.approx(50.0), 100.0]
    assert ap.adjust([_bar(D1, "100"), _bar(D2, "100")],
                     [_event(D2, "100", None)])[0].factor is None


def test_a_day_without_a_trade_has_a_factor_and_no_adjusted_price() -> None:
    rows = ap.adjust([_bar(D1, "100"), _bar(D2, None), _bar(D3, "45")],
                     [_event(D3, "100", "50")])
    assert _factors(rows) == [pytest.approx(0.5), pytest.approx(0.5), 1.0]
    assert rows[1].adjusted_close is None


def test_the_order_events_arrive_in_does_not_matter() -> None:
    bars = [_bar(D1, "100"), _bar(D2, "90"), _bar(D3, "80"), _bar(D5, "70")]
    events = [_event(D2, "100", "90"), _event(D3, "90", "81"), _event(D5, "80", "72")]
    assert ap.adjust(bars, events) == ap.adjust(bars, list(reversed(events)))


def test_a_factor_is_the_reference_price_over_the_close_before() -> None:
    assert _event(D1, "52.10", "51.10").factor == Decimal("51.10") / Decimal("52.10")
    assert _event(D1, None, "1").factor is None
    assert _event(D1, "0", "1").factor is None


def test_every_feed_adjusts_the_daily_prices_of_its_own_market() -> None:
    # CLAUDE.md §30: one series per source. A TWSE result feed lists TWSE
    # executions only, so it adjusts twse_mi_index and never tpex_otc_quotes.
    assert set(ap.PRICE_SOURCE) == set(FEEDS)
    for feed, prices in ap.PRICE_SOURCE.items():
        assert prices == ("twse_mi_index" if feed.startswith("twse_") else "tpex_otc_quotes")
    assert ap.feeds_of("tpex_otc_quotes") == sorted(
        f for f in FEEDS if f.startswith("tpex_"))


def test_the_definition_names_what_42_asks() -> None:
    d = ap.ADJUSTED_PRICES_PIT_V1
    assert (d.dataset_code, d.derivation_version) == ("adjusted_prices_pit", "v1")
    assert d.input_tables == ("daily_prices", "corporate_actions")
    assert "reference_price / close_before" in d.formula_specification
    assert "total-return" in d.price_adjustment_convention
