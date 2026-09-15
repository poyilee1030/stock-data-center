# Step 16 Acceptance Report

Status: IN REVIEW

Scope: Trading Calendar and Coverage Validator

Delivered by GitHub pull request [#15](https://github.com/poyilee1030/stock-data-center/pull/15). Step numbers
and pull-request numbers diverged here and are not reconciled: ADR-0020 went
straight to `main` without a pull request, so Step 16 opened as #15 (ROADMAP §20).

Schema impact: `trading_calendar_versions`, its observation link, a seventeenth
`publication_evidence` target, and `dataset_expected_coverage`.
Migration: `1a6f3b7c8d24`. PIT impact: none — the calendar carries `unknown`
evidence with `published_at = NULL` until ADR-0020 lands.

## Baseline, measured before any code was written

| Measurement | Result |
| --- | ---: |
| TWSE `FMTQIK`, 2020-01-02 → 2026-09-11 | 1,627 trading days |
| Legacy archive `daily_quotes/*/sii.csv` | 1,627 dates |
| Legacy archive `daily_quotes/*/otc.csv` | 1,627 dates |
| TWSE vs TPEx archive date sets | identical, 0 differences either way |
| 2024-07-24 / 07-25 in `FMTQIK` | absent (typhoon closure) |

The TPEx equivalence this PR relies on is therefore **measured, not assumed**.

## Acceptance evidence

Full history imported live through the CLI into a database migrated from zero
(81 requests, 1.2 s apart):

```text
months imported          81
raw artifacts            81
manifests                {'succeeded': 81}
evidence rows            81, of which non-null published_at: 0
coverage_through         2026-09-15
stored trading days      1,627   (2020-01-02 → 2026-09-11)
```

| Criterion | Result | Evidence |
| --- | --- | --- |
| The 2020-01-02 → 2026-09-11 calendar matches the legacy archive | PASS | 1,627 stored days vs 1,627 archive dates; `calendar-only = []`, `archive-only = []`. No difference to explain. |
| Typhoon closures appear as closures | PASS | 2024-07-24 and 07-25 are absent from the stored month and `is_trading_day` returns `False` for both. The real captured bytes are a permanent fixture (`tests/fixtures/twse_fmtqik_202407.json`). |
| The coverage report separates non-trading days from missing data | PASS | `CoverageReport.missing` and `.non_trading_days` are disjoint by construction: a closure is never expected. Regression asserts both fields for the 07-22..07-26 window. |
| The report does not rely on today's security universe | PASS | The report is period-grained. A regression stores one security, takes a report, adds two more securities for the same date, and asserts both reports are identical. |
| Expected coverage is queryable for a (dataset, period) range | PASS | `dataset_expected_coverage` is a table; `ExpectedCoverageService.expected_periods(...)` answers directly, and an undeclared dataset raises instead of returning an empty expectation. |

## Design decisions (ADR-0021)

**The version is month-grained.** A closure is an *absence* from the published
list, so a corrected closure cannot be expressed day-grained: adding a row can
say "this day did open", but rows are never deleted, so "this day did not open
after all" would be inexpressible. With the month as the version, the day list
changes, the business hash changes, and it becomes a new version of that month —
both directions work. ROADMAP describes the table as
`(market, trading date, source, lineage)`; that holds at query level, while the
storage grain follows the source's own publication and revision unit.

**`coverage_through` bounds what a version may answer.** A month fetched before
it ends publishes a partial list, and the days after the last published one are
unknown rather than closed. A still-running month claims only the last day the
source actually showed — claiming *today* would read an unpublished day as a
closure.

**Out of coverage raises.** An unimported month and a month of closures are
indistinguishable in the data, so `False` would quietly turn "we never imported
August" into "the market never opened in August". A gap between imported months
stops coverage at the gap for the same reason.

**No TPEx row is written.** No official TPEx calendar source exists, so writing
one would be inventing data. TPEx datasets declare the TWSE calendar instead,
and the declaration records the measurement behind it.

## Verification

Database migrated from zero to `1a6f3b7c8d24`:

```text
305 passed, 3 skipped, 1 warning
```

Baseline on `main`, same database state: 300 passed after the registry and
downgrade-helper fixes below; the 41 tests added here are the difference.

Every storage rejection was checked to fire for **its own** constraint rather
than a neighbour's:

```text
not first day        ck_trading_calendar_versions_calendar_month_is_first_day
unsorted             ck_trading_calendar_versions_trading_days_sorted_distinct
duplicate            ck_trading_calendar_versions_trading_days_sorted_distinct
empty                ck_trading_calendar_versions_month_has_open_day
coverage too early   ck_trading_calendar_versions_coverage_through_inside_month
coverage outside     ck_trading_calendar_versions_coverage_through_inside_month
```

That check found a real gap in the tests: "a day outside its month" was being
caught by the coverage bound, not by `trading_days_inside_month`. The test now
uses a day *before* the month, which only that constraint can reject, and
asserts the constraint by name.

## Two incidental fixes this PR forced

**The Step 14 storage-contract guard did its job.** Adding the tables failed
`test_every_table_in_the_schema_is_classified_or_explicitly_excluded`
immediately. Both are classified: the calendar with its per-column source
mapping, the observation link and `dataset_expected_coverage` as excluded with
reasons.

**Three downgrade tests hardcoded the head revision** (`7c9e2a4b6d81`), so every
new migration would break them. They now compare against the script head through
a `conftest.alembic_head()` helper — the assertion's intent was "the blocked
downgrade left the version untouched", not "the head is this literal".

## Scope exclusions confirmed

- No TPEx calendar source is used or invented.
- `holidaySchedule` is not used: it lists planned closures only, returned
  nothing before 2023, and cannot represent a typhoon closure.
- Only `trading_calendar` declares its expected coverage here. Steps 17–24
  declare their own alongside the adapters that fill them.
- No release-rule evidence is written; that is Step 15 under ADR-0020.
