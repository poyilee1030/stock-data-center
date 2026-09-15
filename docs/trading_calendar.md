# Trading calendar and coverage validation

The calendar answers one question — **was this market open on this date?** —
and the coverage validator uses it to tell a genuine data gap apart from a day
the market never opened.

## Source

TWSE `afterTrading/FMTQIK?date=YYYYMM01&response=json` returns one row per
**actual** trading day of the requested month (audit §4.12). A closure is
therefore an *absence*, never a flag: July 2024 simply has no 07-24 or 07-25
row, because a typhoon closed the market.

`holidaySchedule` lists *planned* closures only, and the probe returned nothing
before 2023, so it cannot produce the historical calendar. It is not used.

There is **no TPEx equivalent**, so no TPEx row is ever written. That is not a
guess about TPEx: measured on 2026-09-15 against the legacy archive, TWSE and
TPEx opened on exactly the same 1,627 dates from 2020-01-02 to 2026-09-11, with
zero differences either way. TPEx datasets therefore declare the TWSE calendar
as their expectation, and the declaration records why (ROADMAP §22, Step 16). If
an official TPEx calendar source is ever found, it becomes a second source and
the equivalence stops being an assumption.

## Storage

The artifact is a **month**, so the version is month-grained:

```text
trading_calendar_versions(market, source, calendar_month, trading_days[],
                          coverage_through, business_content_hash, lineage)
```

The business content is the day list. A corrected closure changes that list and
therefore produces a **new version** under the same month; nothing is updated in
place. PostgreSQL enforces that the list is non-empty, sorted, distinct and
inside its month.

`coverage_through` is how far one version may speak. A month fetched before it
ends publishes a partial list, and the days after the last published one are
**unknown, not closed**. Only a month that has already ended claims its whole
month.

## Reading it

```python
service = TradingCalendarService()
service.trading_days(connection, market="TWSE", start=..., end=...)
service.is_trading_day(connection, market="TWSE", day=...)
service.next_trading_day_on_or_after(connection, market="TWSE", day=...)
service.coverage_through(connection, market="TWSE")
```

Every call answers only inside imported, **contiguous** coverage; outside it the
service raises `CalendarCoverageError`. A missing month is indistinguishable
from a month of closures, so returning `False` would quietly turn "we never
imported August" into "the market never opened in August". A gap between
imported months stops coverage at the gap for the same reason.

`next_trading_day_on_or_after` is what ADR-0020's release rules call: every rule
instant is at least the statutory deadline moved to the next business day.

## Expected coverage

The validator cannot report a gap until it knows what a dataset *should* hold.
That knowledge is a row, not logic inside a report, because Step 27 turns it into
fetch jobs:

```text
dataset_expected_coverage(dataset_code, market, cadence, period_column,
                          window_start, window_end, note)
```

`cadence` is `trading_day` or `calendar_month`. Each adapter step declares its own
coverage next to the adapter that fills it; Step 16 declares only the calendar it
owns, and an undeclared dataset is an error rather than an empty expectation.

```python
ExpectedCoverageService().expected_periods(
    connection, dataset_code="daily_price", market="TWSE", start=..., end=...
)
```

## The coverage report

```python
CoverageValidator().report(
    connection, dataset_code="daily_price", market="TWSE", start=..., end=...
)
```

- `expected` — the periods the declaration implies, with closures already
  excluded;
- `observed` — the periods the dataset actually holds a row for;
- `missing` — expected minus observed, the genuine gap;
- `non_trading_days` — days in the range the market never opened.

`missing` and `non_trading_days` can never overlap: a closure is never expected,
so it can never be reported as a gap.

The report is **period-grained on purpose**. Whether a date is covered must not
depend on the security universe as it looks today — otherwise an old report
would change whenever a security is added, and a delisted security would make a
covered date look incomplete. Per-security completeness is a different question
that needs a PIT-resolved universe.

A range the calendar does not cover raises rather than reporting everything in
it as missing.

## Running an import

```bash
python -m stock_data_center.ingestion.cli trading-calendar --month 2024-07

# a throttled history run; each month gets its own derived import id, so a run
# that fails midway resumes with the rest instead of restarting
python -m stock_data_center.ingestion.cli trading-calendar \
  --month 2020-01 --through 2026-09 --min-interval-seconds 1.5
```

FMTQIK publishes no release instant, so each month is stored with `unknown`
publication evidence and `published_at = NULL`. The report date is not a
publication time and is never used as one. Approved evidence is appended later
under ADR-0020 without touching the business versions.
