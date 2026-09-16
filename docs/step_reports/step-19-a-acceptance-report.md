# Step 19-a Acceptance Report

Status: IN REVIEW (#24)

Scope: Result-feed contract, storage precision, and the TPEx adapters

Schema impact: migration `8e4b2c7d9a13` widens the four share-ratio columns of
`corporate_action_versions` to `NUMERIC(28, 12)`, drops
`official_rights_dividend_value` from the non-negative check, and rehashes
existing revisions both ways. Downgrade is guarded.
PIT impact: none. Nothing is written by an adapter; the one PIT-relevant
decision, `executed_through`, is a request field fixed when a job is issued.
`src/` changed by +824/−27 lines (630 of them the new adapter module).

## Why Step 19 is split five ways

Six feeds, two TWSE detail pages, a storage correction, an import with
retraction semantics and a backfill run well past what one pull request can be
reviewed as (CLAUDE.md §1). The first draft of this step held all six adapters
and came to about 1,400 lines under `src/`, so it was cut again along the
exchange seam:

| Part | Scope |
| --- | --- |
| 19-a | the contract, the storage corrections, the three self-contained TPEx feeds |
| 19-b | the three TWSE list feeds and their two detail pages |
| 19-c | the import path, retraction included |
| 19-d | the 2020-2026 backfill and legacy reconciliation |
| 19-e | the ETF split feeds found on the way (below) |

Each part is correct on its own. 19-a's contract already names all six result
feeds, so 19-b adds adapters without changing it.

## Baseline, measured before any code was written

Legacy `stock_db.dividend`, 2020-01-02 → 2026-09-11: 6,182 rows, all TWSE.
Every official feed was fetched per calendar year 2020-2026 on 2026-09-16.

| Measurement | Result |
| --- | ---: |
| legacy rows matched by TWT49U on `(code, date)` | 6,182 of 6,182 |
| value differences (close before, reference, 權值+息值, type) | **0** |
| TWT49U rows the legacy never kept | 1,602 — 1,402 ETFs, 170 preferred shares, 30 TDRs |
| legacy-only rows | 0 |

That baseline is 19-b's acceptance evidence to reproduce through its adapter.
TPEx has no legacy counterpart: the legacy system never fetched a TPEx
corporate-action feed, so every TPEx event here is new data.

## What the live feeds falsified

Four assumptions this step started with were wrong. Each is now a test, and
ADR-0019 records the decision it forced.

1. **Result files list events that have not happened yet.** Fetched on
   2026-09-16, TWT49U listed 35 rows dated 09-16 or later, `revivt` three on
   09-21, TWTAUU up to 10-19 with `-` in every price. Invariant G(2) rests on
   the event being executed. A request now carries `executed_through`; later
   rows are counted and not parsed, and the file claims completeness only
   through that date — which is also the only range 19-c may retract in.
2. **`權值+息值` is signed.** It is defined as close before minus reference
   price, and six rights issues priced above the close publish it negative
   (TPEx 8444 on 2024-12-12: −0.204602). The schema's non-negative check was
   wrong, not the data.
3. **Share ratios need eleven places.** `202.11906001` shares per thousand is
   0.20211906001. `NUMERIC(24, 8)` would have stored 0.20211906 without an
   error; the integration test shows PostgreSQL doing exactly that before the
   migration. TPEx cash dividends carry eight places, which `TwdAmount` capped
   at four.
4. **A TPEx par-value endpoint exists.** Audit §4.10 said none had been found.
   `bulletin/pvChgRslt` publishes the exchange ratio and both par values, and
   all 13 events 2020-2026 satisfy ratio = old par ÷ new par. TWSE's
   `TWTB8UDetail`, verified the same day, publishes no ratio at all.

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| Announcement-feed rejection with the 1591/108/1 fixture | PASS | The fixture holds both TPEx rows (board dates 1080806 and 1090505). `ExchangeLocator` refuses `mopsfin_t187ap39_O`, `t187ap45_L`, `TWT48U` and `t05st09sub` with `announcement_feed`, and any unregistered feed with `unknown_feed`. |
| `source_event_key` is `"<feed>:<locator date>"` with no revision content | PASS | `exDailyQ:20240103`, `revivt:20240205`, `pvChgRslt:20240909`. Changing the name, both prices, the dividend values, the type, the cash and the free shares of one row leaves its locator unchanged; a rename leaves the whole observation unchanged. |
| Separate events have separate keys | PASS | 6629 paid four times in 2024: four keys. A repeated row quarantines the file as `ambiguous_identity`. |
| Zero duplicate keys over the full TPEx history | PASS | 2020-01-01 → 2026-09-15 through the adapters: `exDailyQ` 7,328 rows, `revivt` 108, `pvChgRslt` 13 — zero duplicate `(code, key)`. |
| Rows after `executed_through` are not events | PASS | The 2026 `revivt` file parsed through 2026-09-15: 7 events, 3 counted as not yet executed, coverage ends 2026-09-15. |
| Every TPEx row maps or is quarantined with a reason | PASS | All 7,449 rows above map, **zero quarantined**: 6,464 ex-dividend, 430 ex-right, 434 ex-right-dividend; 87 loss-offset and 21 cash-refund reductions; 13 splits. |
| Stored values round-trip; the downgrade refuses what it cannot hold; history still deduplicates | PASS | Integration tests: an eleven-place ratio and a negative difference are read back exactly; the downgrade raises `P0001` for either, before mutation, with the head and column scale unchanged; a revision written before the migration still deduplicates after it and gets its old hash back on downgrade. Removing the rehash from the migration makes that test fail. |

### Fail-closed paths

Each has a test, and each test was checked by removing its guard:

- range echo or a row outside the requested range: `date_mismatch`
- changed list header or inline-detail labels: `schema_mismatch`
- unknown event type or reduction reason: `unknown_event_type`
- any status but `ok`: `source_status`
- detail for another security: `invalid_identity`
- resumption date the detail contradicts: `date_mismatch`
- detail unit other than the declared one: `unit_mismatch`
- a negative amount other than the signed difference: `invalid_numeric`
- type the terms contradict, or a rights ratio without a price: `inconsistent_terms`
- refund reason with no cash, or offset reason with cash: `inconsistent_terms`
- a par-value ratio the par values contradict: `inconsistent_terms`
- a `revivt` cash increase whose unit has never been seen: `unsupported_terms`

## Verification

```text
510 passed, 3 skipped
```

Baseline before this step: 472 collected. The 41 tests added here (36 unit, 5
integration) are the difference. The first unit suite and all five integration
tests were run red before their code existed. The unit suite was then rewritten
for the split, against adapters that already existed, so a red run proved
nothing for it. Instead it was run against 19 deliberate breaks of the adapter:
removing each guard above, storing the name, skipping the per-thousand
division, letting a negative through, and emitting `1E+1` for a ratio. Every
break made at least one test fail.

- `alembic check`: no new operations. Downgrade to `7a2c9e4d1b58` and upgrade
  back ran clean on the local database.
- `git diff --check`: clean.
- `ruff check`: nothing new against `main`.

## Found on the way, scheduled rather than absorbed

- **ETF splits have their own result feeds**: TWSE `rwd/zh/split/TWTCAU` (it
  lists 0050's split on 2025-06-18), TPEx `bulletin/etfSplitRslt` and
  `bulletin/etfRvsRslt`. None of Step 19's six feeds lists these events, so an
  adjusted 0050 series would be wrong without them. ROADMAP now has Step 19-e,
  and Step 25 depends on it.
- **TWT49U's `最近一次申報*` columns are today's filing**, not the event's:
  every 2024 row carries `115年第2季`. Audit §6 had said to keep them in
  `source_terms`, which would revise every past event each quarter. The audit
  is corrected here; 19-b's adapter excludes them.
- **ROADMAP ledger drift.** Step 18-b merged as #23 while the ledger still said
  `THIS STEP`; corrected here.

## Scope exclusions confirmed

- No TWSE adapter or detail page, no write, no retraction, no source policy,
  no CLI, no backfill.
- `announcement_date`, `record_date`, `payment_date`,
  `earnings_stock_ratio` and `capital_surplus_stock_ratio` stay NULL.
- No ratio is inferred from prices, for TWSE par-value changes or anywhere else.

## Code-review findings

One `/code-review` finding held up after checking. Three other claims in that
review did not.

| # | Finding | Checked by | Disposition |
| --- | --- | --- | --- |
| 1 | `CorporateActionRangeRequest` accepted `executed_through` earlier than `start`. The 2027 file requested on 2027-01-01 would report coverage from 2027-01-01 to 2026-12-31 | Reading `coverage_end` | **Fixed.** The request now refuses it: such a job can only count rows, so the issuer skips it. A regression test failed before the fix. |
| — | `money` and `ratios` in `CorporateActionObservation.__post_init__` are unused | `models.py:290` still reads both in the "at least one term" check | Not a defect. |
| — | revivt 2026: nobody confirmed that the three dropped rows are future-dated | `test_rows_dated_after_executed_through_are_not_events_yet` asserts exactly three rows after 2026-09-15 and `not_yet_executed == 3` | Already covered. |
| — | Ruff: I001 and an unused `ArtifactOrigin` in `ingestion/models.py`, TRY004 in `market_reference/models.py` | Running ruff on `main` | All three already exist on `main`, so they are out of scope. |

