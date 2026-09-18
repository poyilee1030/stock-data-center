# Step 20-b Acceptance Report

Status: IN REVIEW (#31)

Scope: Institutional market summary. This step adds the TWSE `BFI82U` and TPEx
`insti/summary` adapters, the importer, the source policy and coverage
declarations, the CLI, and the 2020-01-02 → 2026-09-11 backfill for both
markets. It also settles the broken TPEx archive file of 2026-07-10.

Schema impact: none. `institutional_market_summary_versions` has existed since
Step 7. Migration `d5f8b2e4a0c7` only adds rows: the
`institutional_market_summary` catalog entry, two `dataset_sources` rows, their
release-rule mappings, and two expected-coverage declarations.
PIT impact: nothing new. Both sources follow `exchange_daily_settled@1`, and
the dataset joins `DATASET_TARGETS`, so a stored capture still falsifies the
rule on re-import (the #30 finding).
Size: `src/` changed by +547/−0 lines, below the ~800-line split threshold
(`CLAUDE.md` §1). The step also commits one script and six fixtures.

## Source decisions made during this step

Each decision is recorded in ROADMAP Step 20-b and audit §4.3.

1. **TPEx is read as JSON.** `insti/summary` is the data call of the page the
   legacy `3itrdsum.php` redirects to, with the same four fields.
2. **Each row is one version, keyed by the published name.** The logical key
   is `(market, trade_date, institution)`. The two exchanges name their rows
   differently: TWSE writes 外資及陸資(不含外資自營商), TPEx 外資及陸資(不含自營商).
   TPEx also publishes two subtotals, 外資及陸資合計 and 自營商合計, that TWSE
   does not. Rows are stored under the name each exchange publishes, as the
   indices are (Step 18), and nothing maps one market onto the other. Legacy's
   English codes appear only in the reconciliation script.
3. **TPEx's indent is layout.** TPEx indents its four subgroup rows with
   U+3000. The stored name drops the indent, and the raw artifact keeps it.
4. **Exactly the published rows, in the published order.** The adapter
   accepts one list of institutions per market. A new, missing, renamed or
   moved row fails the file as `schema_mismatch`. The order is what shows the
   hierarchy, and legacy has already seen the layout change once (see below).
5. **Unit is proven, not assumed.** TWSE's field labels carry no unit, so the
   adapter requires `hints` to be `單位：元`. TPEx's labels read `(元)`, and the
   header match covers them.
6. **No value is recomputed.** Nets are stored as published and signed. A
   negative buy or sell fails the file; none occurred.

## The broken 2026-07-10 file

The legacy archive's `institutional_summary/2026/20260710/otc.csv` does not
parse. It is TPEx's JSON answer, wrapped into CSV cells, and its table is empty.

2026-07-10, a Friday, was an unscheduled closure. It is not on TWSE's published
2026 holiday schedule, but it is absent from the TWSE trading calendar
(Step 16), and neither market has prices or flows for it. Legacy has no row
for it. Re-fetched live on 2026-09-18, TPEx answers the date exactly as it
answers a Sunday: `stat: ok` with an empty table. The date quarantined as
`no_data_for_date` (import `ad45b141-…`, quarantine row 225), and the official
bytes are kept as raw artifact `1c67b3a4-…`. The calendar-driven backfill never
requests the date, because it is not a trading day. It is not a gap. The
response is a permanent fixture,
`tests/fixtures/tpex_insti_summary_20260710_closed.json`.

## Baseline

Legacy `stock_db.institutional_summary`, 2020-01-02 → 2026-09-11:

| Market | Rows | Dates |
| --- | ---: | ---: |
| `sii` | 9,756 | 1,627 |
| `otc` | 9,762 | 1,627 |

`sii` is six rows short: three dates each lack two rows (see below).

## The run

```text
                     versions   dates   raw artifacts   evidence (release_rule)
twse_bfi82u             9,762   1,627           1,627   9,762, unknown 0
tpex_insti_summary     13,016   1,627           1,628  13,016, unknown 0
```

Coverage from the Step 16 validator for both markets: `expected 1627,
observed 1627, missing [], unexpected [], is_complete true`.

Both markets ran in parallel at `--min-interval-seconds 1.5`, one process per
host, in about 48 minutes.

**TWSE timed out on one date.** 2026-03-12 failed with `ReadTimeout`, an
operational error: nothing was fetched, so nothing was written. A single-date
rerun under a new import id imported it. The failed manifest stays as history.
TPEx's 1,628th artifact is the 2026-07-10 re-fetch.

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| Legacy `institutional_summary` reconciles for both markets | PASS | **TWSE:** 9,756 legacy rows, all compared; 20 differ on 5 dates, and 6 of ours are missing from legacy on 3 dates. **TPEx:** 9,762 legacy rows, all compared; 4 differ on 2 dates. No legacy row is missing from ours. |
| Every difference is classified | PASS | The classes are `legacy_captured_before_settlement`, `source_changed_after_legacy_capture`, and `legacy_file_five_row_layout`. Each is explained below. The script exits 0 only if every difference carries a class. |
| Coverage is complete | PASS | Both markets: 1,627 of 1,627 dates. |
| Every stored row satisfies buy − sell = net | PASS | 0 failures in 22,778 rows. |
| Every stored date satisfies its market's published totals | PASS | 0 failures. TWSE 合計 = self + hedge + trust + foreign on 1,627 dates. TPEx 外資及陸資合計 and 自營商合計 = their indented rows, and 三大法人合計* = foreign + trust + 自營商合計, on 1,627 dates. |
| The broken 2026-07-10 archive file is re-fetched or quarantined | PASS | Re-fetched live and quarantined as `no_data_for_date`, with the raw artifact kept. It is a closure (above). |
| Rerunning the same content creates no revision | PASS | Integration test: 8 deduplicated, 0 created, no new evidence. |

## The differences

**`legacy_captured_before_settlement`: 12 rows on 3 dates.**

| Date | TWSE | TPEx | Legacy file saved at (Asia/Taipei) |
| --- | ---: | ---: | --- |
| 2026-02-03 | 4 | 2 | 2026-02-03 18:15 |
| 2026-02-11 | — | 2 | 2026-02-11 22:00 |
| 2026-03-27 | 4 | — | 2026-03-27 14:56 |

Legacy saved these files on the trade date itself, before the `03:00 D+1`
instant of `exchange_daily_settled@1`. 2026-02-03 and 2026-02-11 are the same
same-day captures Step 20-a found in `institutional_investors`. Both rows are
internally consistent, and ours is the settled value. Legacy's earlier values
are not imported (ADR-0020 §10).

**`source_changed_after_legacy_capture`: TWSE, 12 rows on 3 dates.**

| Date | Legacy file saved at (Asia/Taipei) |
| --- | --- |
| 2022-09-27 | 2026-01-31 21:20 |
| 2022-10-25 | 2026-01-31 21:31 |
| 2026-01-23 | 2026-02-01 12:55 |

Legacy saved these files long after settlement. Both rows satisfy every
identity, yet TWSE now serves other amounts for the self-dealer-hedge, trust
and foreign rows and for the total. The self-dealer row is unchanged. For
example, on 2022-09-27 the foreign buy and sell are both 2,286,897,850 higher
in ours, with the net unchanged, and the trust net is 4,711,500 higher.

So between legacy's fetch in 2026-01/02 and ours on 2026-09-18, TWSE changed
the values it serves for these three dates. This step stores what the source
serves now, under the rule instant. That makes the corrected value visible from
D+1 03:00, which is the **correction look-ahead** that ADR-0020 (後果) and
ROADMAP Step 15 accept for exchange daily data before forward capture. Legacy
files are not first-seen evidence for this dataset (CLAUDE.md §32 names the
only two datasets they are, monthly revenue and XBRL), so they cannot falsify
the rule. The difference is counted here, not
corrected. It is the first measured instance of a post-settlement correction in
an exchange daily feed, 3 of 1,627 TWSE dates, and a data point for the
revision rate Step 27 is to measure.

**`legacy_file_five_row_layout`: TWSE, 6 rows on 3 dates.**

On 2021-08-26, 2022-09-22 and 2025-03-14, legacy has no foreign row and no
foreign-dealer row. The legacy files for those dates, saved 2026-01-30 →
2026-02-01, have five rows: one combined `外資` row and no `外資自營商`. The
legacy parser dropped the row it had no code for. On all three dates the
legacy `外資` equals our 外資及陸資(不含外資自營商) exactly, and legacy's 合計
equals ours.

TWSE now serves the six-row layout for these dates. If the five-row layout
appears again, the adapter fails the file as `schema_mismatch` rather than
guessing which row `外資` is. Audit §4.3 records the variant.

**Stored rows legacy never had: TPEx 3,254.** These are the 外資及陸資合計
and 自營商合計 subtotals, 1,627 each. Legacy kept neither. Both pass the totals
check above.

## Verification

Database migrated from zero:

```text
711 passed, 3 skipped, 1 warning
```

The baseline on `main` was 670 passed and 3 skipped. The 41 new tests are the
difference: 29 unit and 12 integration.

How each test was seen to fail first:

- Unit tests: all 29 failed against a stub adapter that raised
  `NotImplementedError`, and at import before the stub existed.
- Integration tests: all failed at import before the importer existed. With
  the importer in place but no migration or CLI, 9 of 12 failed: both
  resolution tests, the release-rule instant, correction, coverage, CLI, both
  downgrade tests, and the late-capture test.
- The late-capture test, and Step 20-a's permanent guard that every dataset
  accepting `capture_bound` has a `DATASET_TARGETS` entry, both failed after
  the migration and before the entry was added. The late-capture test found
  `release_rule` next to `capture_bound`, the #30 leak.

`alembic upgrade head` → `alembic check` reports no new operations. The
downgrade to `c4e7a1d3f9b6` removes only this migration's rows and leaves the
Step 20-a declarations alone. Upgrade restores them (tested). With a
quarantined run present, the downgrade guard raises `P0001` before any
mutation (tested). `ruff check` reports nothing new relative to `main`.

Reproduce the reconciliation:

```bash
python scripts/reconcile_institutional_summary.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \
    --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
```

`--legacy-archive` defaults to
`~/GitHubLL/my_stock_project/data/raw/institutional_summary`. It is needed only
to classify differences, from each file's save time and content.

## Code-review findings

The review of #31 found one issue, in the reconciliation script, and nothing
in the code that stores data. It was checked against the script before any
change was made.

| # | Finding | Verified by | Disposition |
| --- | --- | --- | --- |
| 1 | **A value class was granted on metadata alone.** `classify_value` labelled a difference `source_changed_after_legacy_capture` whenever the legacy file was saved after settlement and the legacy row was consistent. It never checked that legacy's database row matched its own file, or that the two sides agreed on scale. A systematic error in ours, such as every amount ×1000, would be absorbed as "the source changed". | A probe scaled every stored amount ×1000 and ran the committed script. 8,520 TWSE and 7,420 TPEx rows came out as `source_changed_after_legacy_capture`, and 1,400 more as `legacy_captured_before_settlement`. The script still exited 1, but only because the scaling also broke the six five-row-layout matches. Without those three dates it would have exited 0. | **Fixed.** A value difference now gets a class only if legacy's database row equals its archive file, and at least one nonzero row on that date agrees exactly with ours. That shows the same scale and row mapping. Anything else is bare `value_differs`, which fails the run. Under the same probe, 9,205 TWSE and 8,135 TPEx rows are now unexplained, and the script exits 1. On the real data every class and count is unchanged, and it exits 0. |

The reviewer also said the script never checks our row's buy − sell = net.
It does, separately, on every stored row: a failure makes the run exit 1 (0
failures in 22,778 rows). On each of the six dates classed
`source_changed_after_legacy_capture` or `legacy_captured_before_settlement`,
the anchoring row is 自營商(自行買賣) or one of the other unchanged rows.

## Scope exclusions confirmed

- 20-c and 20-d are not started.
- No cross-dataset check against Step 20-a. The per-security flows are in
  shares and the summary is in TWD, so their sums are not comparable without
  prices.
- The single-date CLI still exits with a traceback when a date quarantines.
  The quarantine is recorded correctly. This is shared lifecycle behaviour,
  unchanged here.
