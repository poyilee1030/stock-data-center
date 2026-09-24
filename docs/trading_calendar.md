# Trading calendar

The calendar answers one question — **was the market open on this date?** — and
the backfill uses it so that a closure is never requested and a genuine gap is
never mistaken for one.

## Source

TWSE `afterTrading/FMTQIK?date=YYYYMM01&response=json` returns one row per
**actual** trading day of the requested month (audit §4.12). A closure is
therefore an *absence*, never a flag: July 2024 simply has no 07-24 or 07-25
row, because a typhoon closed the market. The adapter is
`ingestion.adapters.trading_calendar`.

`holidaySchedule` lists *planned* closures only, and the probe returned nothing
before 2023, so it cannot produce the historical calendar. It is not used.

There is **no TPEx equivalent**. That is not a guess about TPEx: measured on
2026-09-15 against the legacy archive, TWSE and TPEx opened on exactly the same
1,627 dates from 2020-01-02 to 2026-09-11, with zero differences either way.
TPEx datasets therefore use the TWSE calendar. If an official TPEx calendar
source is ever found, it becomes a second source and the equivalence stops being
an assumption.

## Storage

`trading_days` holds one row per TWSE trading date, with the fetch of the
FMTQIK month it came from. It is a calendar, not history, and is not
append-only. A date inside a successfully fetched month that has no row is a
closure; a month that was never fetched says nothing, and a month fetched before
it ended says nothing about its later days.

`stockdc_backfill` holds 2020-01-02 onward, carried over from v1 by Step 35-a.
No v2 job writes it yet: Step 28's forward capture adds one.

## How it is used

`python -m stock_data_center.v2.backfill` takes its periods from `trading_days`:
a daily job asks once per stored trading date in the range, a monthly job once
per month that has one. A trading date whose file comes back empty is therefore
a gap, not a closure — no trading date in `stockdc_backfill`'s 77,332 fetches
came back empty — so the fetch is logged `empty`, the date stays pending, and the
backfill exits 1.

TDCC's expectation is decided by the same calendar, by week (`docs/tdcc.md`).

FMTQIK publishes no release instant. The calendar needs none: a date's
visibility is its datasets' own release rule, never the calendar's.
