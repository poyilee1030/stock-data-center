# Step 20-a Acceptance Report

Status: IN REVIEW (#30)

Scope: Per-security institutional flows. This step adds the TWSE `T86` and
TPEx `insti/dailyTrade` adapters, a set-based writer for the Phase 7 datasets,
the importer, the source policy and coverage declarations, the CLI, and the
2020-01-02 → 2026-09-11 backfill for both markets.

Schema impact: none. Migration `c4e7a1d3f9b6` only adds rows: the
`institutional_investor` catalog entry, two `dataset_sources` rows, their
release-rule mappings, and two expected-coverage declarations.
PIT impact: nothing new. Both sources follow `exchange_daily_settled@1`.
Size: `src/` changed by +832/−0 lines. That is at the ~800-line split
threshold (`CLAUDE.md` §1); 20-a is already the smallest seam, one dataset.
The step also commits one script and six fixtures.

## Why Step 20 is split

Step 20 covers three datasets across two markets, and the fetch-layer work
too. That is more than one reviewable change can hold (`CLAUDE.md` §1). The
ROADMAP now splits it into four parts:

| Part | Delivers |
| --- | --- |
| 20-a (this) | per-security flows: `T86`, `insti/dailyTrade` |
| 20-b | market summary: `BFI82U`, `insti/summary` |
| 20-c | a complete `SourceResource` (method, body, headers) and the per-host rate governor |
| 20-d | foreign holding: `MI_QFIIS`, MOPS `t13sa150_otc` |

## Source decisions made during this step

Each decision is recorded in ROADMAP Step 20 and audit §4.3–4.4.

1. **`insti/qfii` does not replace MOPS.** ROADMAP left open whether TPEx's own
   foreign-holding JSON could replace MOPS `t13sa150_otc`, which would remove
   the need for a POST resource. It cannot. Compared on 2026-09-11:
   - MOPS lists 1,010 securities and `insti/qfii` lists 892. The 119 that only
     MOPS lists are all ETFs.
   - `insti/qfii` has no mainland limit ratio and no issuer-report date. Both
     are `foreign_holding_versions` columns.
   - The investable ratio rounds differently in 436 rows.

   20-c and 20-d therefore keep MOPS.
2. **TPEx flows are read as JSON.** `insti/dailyTrade` is the data call of the
   page the legacy `3itrade_hedge.php` redirects to, with the same 24 fields.
   The request needs `type=Daily`, which is mandatory, and `sect=EW`, which
   excludes warrants and CBBCs. That makes it the counterpart of TWSE's
   `ALLBUT0999`.
3. **TPEx column groups come from the official page.** The JSON labels its 21
   value columns with only three repeated names. The group each column belongs
   to is proven by the `<template id="theads">` of the page that loads the
   table. The adapter accepts exactly that 24-field list. The page's other
   layout, 16 columns with no foreign-dealer group, is a format change here,
   not a variant: no window date uses it.
4. **Two TPEx totals are not stored.** These are the 外資及陸資 total and the
   自營商 total's buy and sell. The contract has no column for them. The
   reconciliation re-reads every raw artifact and finds each one equal to the
   sum of two stored groups on all 1,249,305 rows.
5. **No value is recomputed.** Nets are stored as published, signed. A
   negative gross quantity fails its file as a format change; none occurred.

## Baseline

Legacy `stock_db.institutional_investors`, 2020-01-02 → 2026-09-11:

| Market | Rows | Dates |
| --- | ---: | ---: |
| `sii` | 1,585,890 | 1,627 |
| `otc` | 1,081,815 | 1,627 |

## The run

```text
                         versions   dates   raw artifacts   evidence (release_rule)
twse_t86                1,876,161   1,627           1,653   1,876,161, unknown 0
tpex_insti_daily_trade  1,249,305   1,627           1,627   1,249,305, unknown 0
data/raw  1.3 GB after this step (783 MB after 18-c)
```

Coverage from the Step 16 validator for both markets: `expected 1627,
observed 1627, missing [], unexpected [], is_complete true`.

Both markets ran in parallel at `--min-interval-seconds 1.5`, one process per
host, in about 2 h 10 min.

**TWSE throttled 12 dates.** The dates were 2024-10-15 and 2024-11-05 →
2024-11-20. For each of them TWSE answered with the same 611-byte CDN error
page (`errorpage-twseweb.cdn.hinet.net`) instead of JSON. The adapter
quarantined those dates as `invalid_json`, kept the page as a raw artifact,
and moved on. The run still ended exit 0.

TWSE served the same date normally when it was fetched again. The window
2024-10-15 → 2024-11-20 was therefore rerun under a new base import id at a
3-second interval: 26 dates imported and 0 failed. 14 of those 26 dates had
already imported, so the rerun deduplicated 16,933 versions and created no
new ones. The 12 failed manifests and their quarantine rows stay as history.

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| Legacy `institutional_investors` reconciles for both markets | PASS | **TPEx:** all 1,081,815 legacy rows compared; 10 rows differ, on 2 dates. **TWSE:** 1,580,450 rows compared; 1,591 rows differ. The remaining 5,440 legacy rows fall on 6 dates where legacy saved another date's file. No legacy row is missing from ours. |
| Every difference is classified | PASS | The classes are `value_differs:legacy_row_incomplete`, `value_differs:both_rows_consistent` and `legacy_captured_another_date`. Each is explained below. The script exits 0. |
| Coverage is complete | PASS | Both markets: 1,627 of 1,627 dates. |
| Every stored row satisfies the published identities | PASS | 0 failures in 3,125,466 rows. Checked per group: buy − sell = net; self + hedge = dealer net; and foreign + trust + dealer = total. |
| TPEx's unstored totals equal the sums of stored groups | PASS | 1,627 artifacts, 1,249,305 rows, 0 failures. |
| Rerunning the same content creates no revision | PASS | The TWSE retry: 16,933 deduplicated, 0 created. Also covered by an integration test. |

## The differences

**`legacy_row_incomplete`: TWSE, 1,491 rows on 130 dates.** On these rows
legacy has NULLs, and the values it does hold sit in the wrong columns.
Example: 2007 on 2020-06-16 has legacy `dealer_hedge_buy` 26,000 with
sell, net and total NULL. Ours has the hedge group at 0 and a total of 26,000.
The legacy parser damaged these rows. Our rows pass every identity.

**`both_rows_consistent`: 110 rows on 4 dates, all in 2026.**

| Date | TWSE | TPEx | Legacy file saved at |
| --- | ---: | ---: | --- |
| 2026-02-03 | 23 | 5 | 2026-02-03 18:15 |
| 2026-02-11 | — | 5 | 2026-02-11 22:00 |
| 2026-04-07 | 52 | — | 2026-04-07 23:30 |
| 2026-06-05 | 25 | — | 2026-06-05 23:30 |

Both rows are internally consistent, and they differ by one amount across a
buy and its net, or across a buy and a sell. For example, 2317 on 2026-02-03
shows foreign buy and foreign sell both 1,425,000 higher in ours. Legacy saved
each of these files on the trade date itself, before the `03:00 D+1` instant
of `exchange_daily_settled@1`. This is the same-day unsettled capture that
audit §7 records and that the rule was set to exclude, so ours is the settled
value. Legacy's earlier values are not imported: the step stores what the
source serves now, and makes no first-seen claim for them.

**`legacy_captured_another_date`: TWSE, 6 dates, 5,440 legacy rows.** On
these dates legacy's values agree with ours on under 0.5% of rows:

| Legacy date | Legacy file is |
| --- | --- |
| 2023-08-04 | 2023-08-18 |
| 2024-10-22, 2024-10-29 | 2024-10-18 |
| 2021-06-17, 2022-04-20, 2024-04-08 | one out-of-window file, 812 rows, 2330 foreign buy 8,317,600, on all three |

This is the defect Step 18-c found in legacy `pe_ratio`: the file has the right
shape but belongs to another date.

**Stored rows legacy never had: 288,797 TWSE and 167,490 TPEx.** All are ETFs
and other codes that are not 4-digit. Legacy kept common stock only. No
4-digit code is source-only on any date.

## Verification

Database migrated from zero:

```text
670 passed, 3 skipped, 1 warning
```

The baseline on `main` was 633 passed and 3 skipped. The 37 new tests (25
unit, 12 integration) are the difference. Two of them come from code review;
see below.

How each test was seen to fail first:

- Unit tests: 24 against a stub adapter that raised `NotImplementedError`. The
  source-code constants test failed at import before the stub existed.
- Integration tests: all 10 failed at import before the importer existed. With
  the importer in place but no migration or CLI, 5 failed: resolution, the
  release-rule instant, coverage, CLI, and both downgrade tests. The
  release-rule test was tightened to require visibility at 19:00 UTC and
  seen to fail before the migration was added.

`alembic upgrade head` → `alembic check` reports no new operations. The
downgrade to `b3d6f0a2c8e5` removes only this migration's rows, and upgrade
restores them (tested). With a quarantined run present, the downgrade guard
raises `P0001` before any mutation (tested). `ruff check` reports nothing new
relative to `main`.

Reproduce the reconciliation:

```bash
python scripts/reconcile_institutional_investors.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \
    --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
```

## Code-review findings

The review of #30 found one bug and nothing else. It was checked against the
code and reproduced before any change was made.

| # | Finding | Verified by | Disposition |
| --- | --- | --- | --- |
| 1 | **Market-PIT leak.** `institutional_investor` had no entry in `DATASET_TARGETS` (`evidence/policy.py`), so `plan_many` could not see a `capture_bound` already stored for a version. Take a `first_capture` of D fetched after D+1 03:00: it correctly withholds the release rule. A later re-import of D would then append `release_rule` at D+1 03:00 into append-only evidence, making the version visible before its proven first sighting. | A new test runs a late `first_capture` and then a `gap_fill`. It found 1,330 `release_rule` rows next to the 1,330 `capture_bound` rows. | **Fixed.** The dataset is added to `DATASET_TARGETS`, and the test now finds `capture_bound` only. A second permanent test requires every dataset whose source accepts `capture_bound` to have a `DATASET_TARGETS` entry, so 20-b, 20-d and 21 cannot repeat the gap. Both tests failed before the fix. |

The stored backfill was not affected. It ran as `gap_fill`, which never writes
`capture_bound`. `stockdc_backfill` holds 3,125,466 `release_rule` rows for this
dataset and no other evidence type, so there was no capture for a re-import to
contradict.

## Scope exclusions confirmed

- 20-b, 20-c and 20-d are not started; this step only records their scope.
- Legacy's `name` column and `pced_*` coordinates are not stored. Security
  metadata owns names, and the raw artifact owns coordinates.
- The derived `institutional_cumulative_flow:v1` and `institutional_streaks:v1`
  remain Step 26's work.
