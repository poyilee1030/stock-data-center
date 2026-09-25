"""Step 26-f: `valuation_metrics:v1`.

TTM EPS, PE and the PE percentile are legacy `calculate_valuation.py`'s; the
fixture is legacy `stock_db` from 2021-03-31 to 2022-06-30 — single-quarter EPS
with legacy's statutory-deadline publish dates and daily closes in, legacy
`valuation_daily` out. Legacy summed EPS in double precision, so its TTM
carries binary residue (61.199999999999996); ours is the exact two-place sum.

ROE is not legacy's (owner, 2026-09-25): legacy divided TTM EPS by a net asset
value per share that assumed a par value of 10 and counted non-controlling
interests. Ours is the trailing four quarters' net income attributable to the
parent over the latest quarter-end equity attributable to the parent.

Legacy's `_official` suffix is dropped (owner, 2026-09-25): these values are
computed, and CLAUDE.md §53 keeps "official" for source-published ones.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.v2.valuation import (
    METRICS,
    Quarter,
    Report,
    quarters,
    valuations,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "step26f_valuation_legacy.json"


def _legacy_inputs(security: dict):
    by_quarter = {}
    for q in security["quarters"]:
        year, number = int(q["quarter"][:4]), int(q["quarter"][5])
        eps = None if q["eps"] is None else Decimal(q["eps"]).quantize(Decimal("0.01"))
        by_quarter[(year, number)] = Quarter(date.fromisoformat(q["published_on"]), eps,
                                             None, None)
    days = [(date.fromisoformat(d["date"]), None if d["close"] is None else float(d["close"]))
            for d in security["days"]]
    return days, by_quarter


def test_the_metrics() -> None:
    assert METRICS == ("ttm_eps", "pe_ratio", "pe_percentile", "roe")


@pytest.mark.parametrize("security_code", ["2330", "8069", "1316", "2424"])
def test_ttm_eps_and_pe_percentile_match_legacy(security_code: str) -> None:
    security = json.loads(FIXTURE.read_text(encoding="utf-8"))["securities"][security_code]
    days, by_quarter = _legacy_inputs(security)
    rows = dict(valuations(days, by_quarter))
    assert set(rows) == {day for day, _ in days}
    for expected in security["days"]:
        row = rows[date.fromisoformat(expected["date"])]
        legacy_ttm = Decimal(expected["ttm_eps"]).quantize(Decimal("0.01"))
        assert row["ttm_eps"] == float(legacy_ttm), expected["date"]
        legacy_percentile = (None if expected["pe_percentile"] is None
                             else float(expected["pe_percentile"]))
        assert row["pe_percentile"] == legacy_percentile, expected["date"]


# ---------------------------------------------------------------- quarters

Y = 2024


def _report(quarter, facts, *, category="consolidated", published=None, year=Y):
    return Report(year, quarter, category, published or date(year, 12, 31), facts)


def _full_year_reports(*, q3=True):
    q = {1: (date(Y, 1, 1), date(Y, 3, 31)), 2: (date(Y, 4, 1), date(Y, 6, 30)),
         3: (date(Y, 7, 1), date(Y, 9, 30))}
    ytd3 = (date(Y, 1, 1), date(Y, 9, 30))
    year = (date(Y, 1, 1), date(Y, 12, 31))
    reports = [
        _report(1, {("9750", *q[1]): Decimal("1.10"), ("8610", *q[1]): Decimal(100),
                    ("31XX", None, q[1][1]): Decimal(1000)}),
        _report(2, {("9750", *q[2]): Decimal("1.20"), ("8610", *q[2]): Decimal(120),
                    ("9750", date(Y, 1, 1), q[2][1]): Decimal("2.30"),
                    ("31XX", None, q[2][1]): Decimal(1100)}),
        _report(4, {("9750", *year): Decimal("5.00"), ("8610", *year): Decimal(460),
                    ("31XX", None, year[1]): Decimal(1300)}),
    ]
    if q3:
        reports.append(_report(3, {("9750", *q[3]): Decimal("1.30"),
                                   ("8610", *q[3]): Decimal(110),
                                   ("9750", *ytd3): Decimal("3.60"),
                                   ("8610", *ytd3): Decimal(330),
                                   ("31XX", None, q[3][1]): Decimal(1200)}))
    return reports


def test_single_quarters_and_the_derived_fourth() -> None:
    # Q1's year to date is its single quarter; Q2 and Q3 print theirs; Q4 is
    # the annual figure less the third quarter's year to date, as legacy did.
    got = quarters(_full_year_reports())
    assert {k: (v.eps, v.net_income, v.equity) for k, v in got.items()} == {
        (Y, 1): (Decimal("1.10"), Decimal(100), Decimal(1000)),
        (Y, 2): (Decimal("1.20"), Decimal(120), Decimal(1100)),
        (Y, 3): (Decimal("1.30"), Decimal(110), Decimal(1200)),
        (Y, 4): (Decimal("1.40"), Decimal(130), Decimal(1300)),
    }


def test_without_the_third_quarter_there_is_no_fourth() -> None:
    # Legacy fell back to the annual figure, a whole year counted as a quarter.
    got = quarters(_full_year_reports(q3=False))
    assert (got[(Y, 4)].eps, got[(Y, 4)].net_income) == (None, None)
    assert got[(Y, 4)].equity == Decimal(1300)


def test_a_fourth_quarter_waits_for_its_third_quarters_publication() -> None:
    # Q4's single quarter is annual less Q3's year to date, so a Q3 first
    # captured after Q4 holds Q4 back; otherwise the window led by the next
    # year's Q3, which never checks this Q3, would read it before it was public.
    reports = _full_year_reports()
    reports[-1] = Report(Y, 3, "consolidated", date(Y + 1, 12, 1), reports[-1].facts)
    got = quarters(reports)
    assert got[(Y, 4)].published_on == date(Y + 1, 12, 1)
    assert got[(Y, 4)].eps == Decimal("1.40")


def test_an_individual_report_reads_its_own_accounts() -> None:
    # 個體 reports have no 8610 or 31XX: profit is 8200 and, with no
    # non-controlling interest, equity is 3XXX.
    q1 = (date(Y, 1, 1), date(Y, 3, 31))
    got = quarters([_report(1, {("9750", *q1): Decimal(1), ("8200", *q1): Decimal(7),
                                ("8610", *q1): Decimal(999),
                                ("3XXX", None, q1[1]): Decimal(70),
                                ("31XX", None, q1[1]): Decimal(999)},
                            category="individual")])
    assert (got[(Y, 1)].net_income, got[(Y, 1)].equity) == (Decimal(7), Decimal(70))


def test_a_report_without_a_publication_time_is_never_used() -> None:
    # CLAUDE.md §31: nothing proves when it became public.
    reports = _full_year_reports()
    reports[0] = Report(Y, 1, "consolidated", None, reports[0].facts)
    got = quarters(reports)
    assert (Y, 1) not in got
    assert (Y, 2) in got


# ---------------------------------------------------------------- valuations


def _q(published: date, eps: str, net_income=100, equity=1000) -> Quarter:
    return Quarter(published, Decimal(eps), Decimal(net_income), Decimal(equity))


FOUR = {
    (2023, 2): _q(date(2023, 8, 14), "1.00"),
    (2023, 3): _q(date(2023, 11, 14), "1.00"),
    (2023, 4): _q(date(2024, 3, 31), "1.00"),
    (2024, 1): _q(date(2024, 5, 15), "2.00", net_income=200, equity=2000),
}


def test_a_value_waits_for_the_fourth_quarters_publication() -> None:
    rows = dict(valuations([(date(2024, 5, 14), 50.0), (date(2024, 5, 15), 50.0)], FOUR))
    assert date(2024, 5, 14) not in rows  # only three of the four are public
    assert rows[date(2024, 5, 15)] == {"ttm_eps": 5.0, "pe_ratio": 10.0,
                                        "pe_percentile": 100.0, "roe": 25.0}


def test_the_trailing_four_quarters_must_be_consecutive() -> None:
    # Legacy rolled over rows, so a missing quarter summed five quarters' span.
    quarters_ = {**FOUR}
    del quarters_[(2023, 3)]
    quarters_[(2023, 1)] = _q(date(2023, 5, 15), "1.00")
    assert valuations([(date(2024, 6, 3), 50.0)], quarters_) == []
    assert len(valuations([(date(2024, 6, 3), 50.0)], FOUR)) == 1


def test_the_latest_published_quarter_leads_even_when_filed_out_of_order() -> None:
    late = {**FOUR, (2024, 2): _q(date(2024, 8, 14), "3.00"),
            (2024, 1): _q(date(2024, 9, 1), "2.00")}
    rows = dict(valuations([(date(2024, 8, 20), 50.0), (date(2024, 9, 2), 50.0)], late))
    assert date(2024, 8, 20) not in rows  # 2024Q2 is out but 2024Q1 is not yet
    assert rows[date(2024, 9, 2)]["ttm_eps"] == 7.0


def test_no_pe_without_a_positive_ttm_or_a_close_and_the_percentile_skips_them() -> None:
    loss = {**FOUR, (2024, 1): _q(date(2024, 5, 15), "-4.00")}
    rows = valuations([(date(2024, 5, 15), 50.0)], loss)
    assert rows[0][1]["ttm_eps"] == -1.0
    assert (rows[0][1]["pe_ratio"], rows[0][1]["pe_percentile"]) == (None, None)
    days = [(date(2024, 5, 15), 40.0), (date(2024, 5, 16), None), (date(2024, 5, 17), 60.0)]
    rows = dict(valuations(days, FOUR))
    assert rows[date(2024, 5, 16)]["pe_ratio"] is None
    assert rows[date(2024, 5, 16)]["pe_percentile"] is None
    assert rows[date(2024, 5, 16)]["ttm_eps"] == 5.0
    assert rows[date(2024, 5, 17)]["pe_percentile"] == 100.0  # 2 of 2, the NULL left out


def test_the_percentile_is_legacys_expanding_average_rank() -> None:
    # pandas expanding().rank(pct=True): ties take their average rank, and
    # rank / n * 100 is rounded as numpy does (rint of x * 10^4).
    closes = [50.0, 40.0, 50.0, 45.0, 35.0, 50.0]
    days = [(date(2024, 5, 15 + i), c) for i, c in enumerate(closes)]
    rows = [r["pe_percentile"] for _, r in valuations(days, FOUR)]
    assert rows == [100.0, 50.0, 83.3333, 50.0, 20.0, 83.3333]


def test_pe_is_rounded_as_legacys_pandas_round() -> None:
    rows = dict(valuations([(date(2024, 5, 15), 12.345)], {
        **FOUR, (2024, 1): _q(date(2024, 5, 15), "-2.00")}))
    assert rows[date(2024, 5, 15)]["ttm_eps"] == 1.0
    # 12.345 / 1.0 * 100 = 1234.4999999999998 -> 1234 -> 12.34
    assert rows[date(2024, 5, 15)]["pe_ratio"] == 12.34


def test_roe_is_ttm_net_income_over_the_latest_equity() -> None:
    rows = dict(valuations([(date(2024, 5, 15), 50.0)], {
        **FOUR, (2024, 1): _q(date(2024, 5, 15), "2.00", net_income=201, equity=3000)}))
    assert rows[date(2024, 5, 15)]["roe"] == 16.7  # 501 / 3000 * 100 = 16.7
    rows = dict(valuations([(date(2024, 5, 15), 50.0)], {
        **FOUR, (2024, 1): _q(date(2024, 5, 15), "2.00", net_income=453, equity=20000)}))
    assert rows[date(2024, 5, 15)]["roe"] == 3.77  # 753 / 20000 * 100 = 3.765, half up


@pytest.mark.parametrize("equity", [0, -5, None])
def test_no_roe_without_positive_equity(equity) -> None:
    quarters_ = {**FOUR, (2024, 1): Quarter(date(2024, 5, 15), Decimal(2), Decimal(1),
                                            None if equity is None else Decimal(equity))}
    assert valuations([(date(2024, 5, 15), 50.0)], quarters_)[0][1]["roe"] is None


def test_no_roe_without_every_quarters_net_income() -> None:
    quarters_ = {**FOUR, (2023, 3): Quarter(date(2023, 11, 14), Decimal(1), None,
                                            Decimal(1000))}
    row = valuations([(date(2024, 5, 15), 50.0)], quarters_)[0][1]
    assert (row["ttm_eps"], row["roe"]) == (5.0, None)


def test_no_value_changes_with_a_later_day() -> None:
    days = [(date(2024, 5, 15 + i), 30.0 + 7 * (i % 4)) for i in range(8)]
    full = valuations(days, FOUR)
    assert len(full) == len(days)
    for end in range(1, len(days)):
        assert valuations(days[:end], FOUR) == full[:end]
