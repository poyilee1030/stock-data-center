# Step 17-b Acceptance Report

Status: IN REVIEW

Scope: Whole-market daily-price import path

Schema impact: none. `daily_price_versions` already covers every sourced field.
Migration `5e3b8d1a9c42` adds rows only: two `dataset_sources` with their
accepted evidence types, their release-rule mappings, and the two `daily_price`
expected-coverage declarations.
PIT impact: none new — both sources follow `exchange_daily_settled@1`, the rule
Step 15-c already applies to `daily_price`.
`src/` changed by +633/−10 lines.

## Baseline

Step 17-a's parse, merged as #19, and its reconciliation: 4,423 legacy rows
across five market-dates, zero differences. This step stores those same rows, so
the same comparison is re-run against the **stored** versions rather than the
parsed ones — a parse that was right and a write that loses something would
otherwise look identical.

Before this step, `daily_price` had two sources (`twse`, `tpex`) and no expected
coverage declared; `dataset_expected_coverage` held only `trading_calendar`.

## Acceptance evidence

Live imports through the CLI into a database migrated from zero, one request
each, purpose `gap_fill`:

```text
twse_mi_index   2026-09-11  1,379 rows  1,379 created  1,379 evidence  variant allbut0999
tpex_otc_quotes 2026-09-11  1,012 rows  1,012 created  1,012 evidence  variant volume_in_lots
twse_mi_index   2020-01-02  1,114 rows  1,114 created  1,114 evidence  variant allbut0999
tpex_otc_quotes 2020-01-02    876 rows    876 created    876 evidence  variant prices_only
tpex_otc_quotes 2020-04-30    885 rows    885 created    885 evidence  variant volume_in_thousand_shares
total                       5,266 versions, 5 raw artifacts, one request each
```

| Criterion | Result | Evidence |
| --- | --- | --- |
| One real trade date per market imports end to end, raw-first | PASS | Five market-dates above, covering all three TPEx header variants, each fetched live and stored through the Step 9 raw-first lifecycle: artifact captured and checkpointed before parsing, business rows written in the same transaction as the checkpoint completion. |
| Stored values equal legacy `daily_quotes` | PASS | The same 4,423 legacy rows, compared against what is **in the database** rather than what the adapter returned: zero differences in open/high/low/close, volume, trade value and trade count, and no legacy-only row. |
| Units survive the write | PASS | 2330 on 2026-09-11 stores `last_bid_volume = 1078`, and the manifest records `source_units.disclosed_volume = share` for TWSE against `lot_1000_shares` for TPEx. This is the #19 review finding, checked at the far end of the pipeline rather than only at the parse. |
| Re-running the same date creates no revision | PASS | Second import of 2026-09-11: `created 0, deduplicated 1379`, evidence `created 0, deduplicated 1379`, one additional `raw_artifact_observations` row. The repeated fetch stays auditable without inventing a revision. |
| Changed content creates a revision | PASS | Correcting one close in the payload produces exactly one new version; the other 1,378 deduplicate. |
| No revision flapping with the Step 9 pilots | PASS | Distinct source codes. A regression asserts the imported rows carry only the whole-market source, and the two histories never share a `(security, trade_date)` row. |
| A date the source has nothing for quarantines | PASS | 2024-07-24 raises `no_data_for_date`; the raw artifact is retained, no business row is written, and `import_quarantine.reason_code` carries the code — which is how 17-c will tell a benign skip from a real failure without re-deriving the calendar. |
| Imported rows resolve under Market PIT | PASS | 2330 on 2026-09-11 resolves by `exchange_daily_settled@1` at 2026-09-11T19:00Z — 03:00 on 09-12, Asia/Taipei. |
| Evidence follows the declared purpose | PASS | All 5,266 versions carry `release_rule` and nothing else: a `gap_fill` proves no first sighting. A `first_capture` run instead claims one `capture_bound` per created version. |
| Expected coverage is declared | PASS | `daily_price/TWSE → twse_mi_index` and `daily_price/TPEx → tpex_otc_quotes`, cadence `trading_day`, window from 2020-01-02, both declaring the TWSE calendar Step 16 measured. |
| The downgrade refuses to orphan history | PASS | With rows imported, `downgrade 4d9f2a6c8b17` raises `P0001` before mutating anything. Verified red by removing the guard and watching the regression fail. |

Reconciliation against legacy `stock_db`, from the stored rows:

```text
2026-09-11 twse_mi_index    stored=1379 legacy=1092 diffs={}
2026-09-11 tpex_otc_quotes  stored=1012 legacy= 862 diffs={}
2020-01-02 twse_mi_index    stored=1114 legacy= 955 diffs={}
2020-01-02 tpex_otc_quotes  stored= 876 legacy= 752 diffs={}
2020-04-30 tpex_otc_quotes  stored= 885 legacy= 762 diffs={}
total legacy rows compared: 4,423
```

## Design decisions

**Set-based writes, per-row rules.** A whole-market file is about 1,300
securities, and the existing per-row path would issue roughly 4,000 statements
per request — enough to make 17-c's ~3,300 requests impractical. Registration,
version writes, evidence planning and evidence writes now take one or two
statements each. What did **not** change is the part that matters: the database
still generates `business_content_hash` and `ingested_at`, an unchanged
observation still reuses its existing version, and the match back to that
version is made on the business values the database hashes, never on a hash
recomputed in Python. One trade date imports in about 1.9 s end to end.

**`ON CONFLICT DO NOTHING ... RETURNING`, not `DO UPDATE`.** The usual
`xmax = 0` upsert trick would tell created from existing in one statement, but
`immutable_daily_price` is a `BEFORE UPDATE OR DELETE` trigger: a no-op
`DO UPDATE` would fire it and be rejected. The insert therefore returns only the
rows it created, and the rest are read back and matched on their business values.

**`plan_many` resolves the rule once.** The release rule and the accepted-type
allowlist are constant for a `(dataset_code, source)`, and the rule instant is
constant for the trade date all 1,300 rows share. Each version's already-proven
capture is read in one grouped query rather than one per row. The decision
itself is still `evidence_plan`, unchanged and shared with the per-row path — the
batch is a different number of round trips, not a second policy.

**The importer declares its own market.** `_source_semantics` records the market,
both traded units and the disclosed-volume unit, so the configuration fingerprint
changes if any of them ever does, and a resumed import with changed semantics is
refused rather than silently mixed.

## What this cost in existing tests

Step 16's coverage tests declared `daily_price/TWSE` coverage themselves, using
the pilot source `twse`, under a comment reading *"Step 17 will ship this
declaration with its adapter; here it is a fixture."* It does now, so those tests
point at the real source codes. Five failed the moment the migration landed,
which is the declaration doing its job: the fixture and the shipped declaration
disagreed, and the shipped one won.

## Verification

Database migrated from zero to `5e3b8d1a9c42`:

```text
421 passed, 3 skipped, 1 warning
```

Baseline before this step: 409 (Step 17-a). The 12 integration tests added here
are the difference — 11 written before the importer existed, and one more from
the review below.

Migration round trip on a clean database: `upgrade head` seeds six rows — two
`dataset_sources`, two `dataset_release_rules`, two `dataset_expected_coverage` —
`downgrade 4d9f2a6c8b17` removes exactly those and leaves the two pilot sources'
allowlists untouched, `upgrade head` re-seeds. With history imported the
downgrade refuses first.

`ruff check` reports nothing new against `main` for every file touched.

## Code-review findings

Four findings, all verified before anything changed; none was a false positive.

| # | Finding | Verified by | Disposition |
| --- | --- | --- | --- |
| 1 | The multi-row observation insert binds ~21 parameters per row, so it exceeds PostgreSQL's 65,535-per-statement limit at roughly 3,100 rows | Compiled the real statement: exactly 21 parameters per row, so the ceiling is 3,120. A regression writing 4,000 observations failed with `number of parameters must be between 0 and 65535`. | **Fixed.** Every multi-row insert is now split by the parameters it actually binds, counted from the row itself, so adding a column cannot quietly move the cliff. The evidence insert had the same shape at 12 parameters per row — a ceiling of 5,461, which a `first_capture` run reaches at about 2,730 securities, since it plans two evidence rows per version. |
| 2 | The manifest omits the `publication_time` key every other importer emits | Read: three importers emit it, nothing reads it — and for `daily_price` the constant `"unknown"` has been **false** since Step 15-c. | Fixed, but not by copying the constant. The manifest now reports the rule that actually decides availability time (`exchange_daily_settled@1`) plus the evidence types the run wrote. The same stale `"unknown"` in the Step 9 pilot importer is corrected with it: same dataset, same rule, and a manifest is an audit record. |
| 3 | The coverage insert uses `ON CONFLICT DO NOTHING` while its sibling deliberately uses `DO UPDATE` to repair a pre-existing row | Read. No such row exists on `main`, but a declaration left pointing at a Step 9 pilot source would make the coverage report read the wrong history — silently. | Fixed. The upsert repairs the row. This migration is the authority for what `daily_price` coverage means. |
| 4 | The migration docstring attributes the change to Step 17-a, which lists storage and evidence as out of scope, and the acceptance report says four rows where it seeds six | Counted: 2 + 2 + 2. | Fixed. Both were written while Step 17 was still one step. |

Finding 1 is the one that matters. It is latent rather than live — today's largest
market-date is 1,379 rows — but it fails the import outright when the listed
universe crosses the line, and 17-c is the step that would walk into it.

## Scope exclusions confirmed

- No backfill: 17-c owns the date-range runner, the 2020-01-02 → 2026-09-11 run,
  the committed reconciliation tool and the no-metadata report. The CLI here
  imports one trade date per invocation.
- The index sections of the TWSE artifact are untouched; Step 18 reuses the same
  raw artifacts.
- The Step 9 per-security pilots are unchanged and stay available for spot
  checks.
- No adjusted prices, and no readiness claim for returns or indicators: the
  §51.4 gate still waits on corporate-action history.
