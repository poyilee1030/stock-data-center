"""`valuation_metrics:v1` — TTM EPS, PE, PE percentile and ROE from published reports.

Ported from legacy `calculate_valuation.py`, with two corrections the owner
chose on 2026-09-25 and two that make the trailing four quarters mean what
they say:

- A report counts on date D once it is public on D: its first version's
  `published_at`, in Asia/Taipei, is not after D (CLAUDE.md §43). Legacy used
  the statutory deadline; our `published_at` is the deadline moved to the next
  trading day, or an earlier proven first sighting. A report without a
  `published_at` is never used (§31).
- TTM EPS is the sum of the single-quarter basic EPS (account 9750) of the four
  consecutive quarters ending at the latest quarter public on D, and there is
  no value unless all four are public. Legacy rolled over its rows, so a
  missing quarter stretched the sum over five quarters. The fourth quarter is
  the annual figure less the third quarter's year to date, as legacy derived
  it; without the third quarter there is none, where legacy counted the whole
  year as the quarter.
- PE is the day's close over TTM EPS, NULL unless TTM EPS is positive, rounded
  as legacy's pandas `round(2)`. The percentile is legacy's: the PE's average
  rank among the series' PEs so far, as a percentage, rounded as numpy does.
- ROE is not legacy's (owner): legacy divided TTM EPS by a net asset value per
  share that assumed a par value of 10 and counted non-controlling interests.
  Ours is the four quarters' net income attributable to the parent (8610; 8200
  in an individual report) over the latest quarter's equity attributable to the
  parent (31XX; 3XXX in an individual report, which has no non-controlling
  interest), times 100, rounded half away from zero; NULL unless the equity is
  positive.

Legacy's `_official` suffix is dropped (owner): CLAUDE.md §53 keeps "official"
for values the source publishes.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right, insort
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

METRICS = ("ttm_eps", "pe_ratio", "pe_percentile", "roe")

EPS = "9750"
NET_INCOME = {"consolidated": "8610", "individual": "8200"}
EQUITY = {"consolidated": "31XX", "individual": "3XXX"}
_QUARTER_START = {1: (1, 1), 2: (4, 1), 3: (7, 1), 4: (10, 1)}
_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}

Key = tuple[int, int]  # (year, quarter)


@dataclass(frozen=True, slots=True)
class Report:
    """One quarter's report: when it became public, and its facts keyed by
    (account code, period start or None for an instant, period end)."""

    year: int
    quarter: int
    category: str
    published_on: date | None
    facts: Mapping[tuple[str, date | None, date], Decimal]


@dataclass(frozen=True, slots=True)
class Quarter:
    published_on: date
    eps: Decimal | None
    net_income: Decimal | None
    equity: Decimal | None


def _end(year: int, quarter: int) -> date:
    return date(year, *_QUARTER_END[quarter])


def _single(report: Report, code: str, ytd_before: Decimal | None) -> Decimal | None:
    """The report's single-quarter value of an account; a fourth quarter's needs the
    third quarter's year to date, `ytd_before`."""
    year, quarter = report.year, report.quarter
    if quarter in (2, 3):
        return report.facts.get((code, date(year, *_QUARTER_START[quarter]), _end(year, quarter)))
    to_date = report.facts.get((code, date(year, 1, 1), _end(year, quarter)))
    if quarter == 1:
        return to_date
    return None if to_date is None or ytd_before is None else to_date - ytd_before


def quarters(reports: Iterable[Report]) -> dict[Key, Quarter]:
    """Each public quarter's single-quarter EPS and net income and its quarter-end equity."""
    by_key = {(r.year, r.quarter): r for r in reports if r.published_on is not None}
    out = {}
    for (year, quarter), report in by_key.items():
        third = by_key.get((year, 3)) if quarter == 4 else None
        out[(year, quarter)] = Quarter(
            report.published_on,
            _single(report, EPS, _third_to_date(third, lambda _: EPS)),
            _single(report, NET_INCOME[report.category],
                    _third_to_date(third, NET_INCOME.__getitem__)),
            report.facts.get((EQUITY[report.category], None, _end(year, quarter))),
        )
    return out


def _third_to_date(third: Report | None, code_of) -> Decimal | None:
    """The third quarter's year to date of the account `code_of(its category)`."""
    if third is None:
        return None
    return third.facts.get((code_of(third.category), date(third.year, 1, 1),
                            _end(third.year, 3)))


def _previous(key: Key) -> Key:
    year, quarter = key
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def _numpy_round(value: float, places: int) -> float:
    """numpy's round: rint(value * 10^places) / 10^places, which legacy's pandas used."""
    scale = 10 ** places
    return round(value * scale) / scale


def _roe(window: list[Quarter]) -> float | None:
    equity = window[0].equity
    incomes = [q.net_income for q in window]
    if equity is None or equity <= 0 or any(i is None for i in incomes):
        return None
    value = sum(incomes) / equity * 100
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) + 0.0


def valuations(days: Sequence[tuple[date, float | None]],
               by_quarter: Mapping[Key, Quarter]) -> list[tuple[date, dict]]:
    """One series' metrics for each (day, close) in date order that has a TTM EPS."""
    public = sorted(by_quarter.items(), key=lambda item: item[1].published_on)
    seen: list[float] = []  # the series' PEs so far, sorted
    latest: Key | None = None
    index = 0
    out = []
    for day, close in days:
        while index < len(public) and public[index][1].published_on <= day:
            key = public[index][0]
            latest = key if latest is None else max(latest, key)
            index += 1
        if latest is None:
            continue
        window, key = [], latest
        for _ in range(4):
            quarter = by_quarter.get(key)
            if quarter is None or quarter.published_on > day or quarter.eps is None:
                break
            window.append(quarter)
            key = _previous(key)
        if len(window) < 4:
            continue
        ttm = float(sum(q.eps for q in window))
        pe = percentile = None
        if ttm > 0 and close is not None:
            pe = _numpy_round(close / ttm, 2)
            insort(seen, pe)
            less, through = bisect_left(seen, pe), bisect_right(seen, pe)
            rank = less + (through - less + 1) / 2
            percentile = _numpy_round(rank / len(seen) * 100, 4)
        out.append((day, {"ttm_eps": ttm, "pe_ratio": pe, "pe_percentile": percentile,
                          "roe": _roe(window)}))
    return out
