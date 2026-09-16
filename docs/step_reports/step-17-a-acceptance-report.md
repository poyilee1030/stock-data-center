# Step 17-a Acceptance Report

Status: IN REVIEW

Scope: Whole-market daily-price adapters

Schema impact: none. Migration impact: none. PIT impact: none — this step writes
nothing; it turns official bytes into `DailyPriceObservation` values.
`src/` changed by +658/−0 lines.

## Why Step 17 is split three ways

Step 17's acceptance spans a parse, an import path and a ~3,300-request backfill.
Built together it came to about 1,200 lines under `src/`, past what CLAUDE.md §1
treats as reviewable, so it splits along seams that leave each part correct on
its own: 17-a parses, 17-b imports one date end to end, 17-c runs the window and
reconciles. ROADMAP §20 and the Step 17 section are updated accordingly, and
Step 15-c is marked MERGED — it shipped in #18 with the ledger still reading
`THIS STEP`.

## Baseline, measured before any code was written

Legacy `stock_db.daily_quotes`, 2020-01-02 → 2026-09-11:

| Measurement | Result |
| --- | ---: |
| rows | 2,931,379 |
| `sii` | 1,631,598 rows / 1,135 symbols / 1,627 dates |
| `otc` | 1,299,781 rows / 945 symbols / 1,627 dates |
| `bid` / `ask` non-null | 0 |
| `direction` populated | `sii` only (`otc` is NULL in every row) |
| minimum `volume` | 1 — the legacy scraper kept no untraded row |

Live source shape, verified 2026-09-16 before implementation:

| Measurement | Result |
| --- | --- |
| TWSE `MI_INDEX?type=ALLBUT0999` on 2026-09-11 | 10 tables; the stock section is index 8 and must be found by header |
| TWSE stock rows | 1,379 (legacy `sii` holds 1,092) |
| TWSE `漲跌(+/-)` values | `<p style= color:red>+</p>`, `…green>-</p>`, `<p> </p>`, `<p>X</p>` |
| `漲跌價差` on every `X` and blank row | `0.00`, in both the 2020 and the 2026 file |
| TPEx `afterTrading/otc` header variants | three, changing exactly on 2020-04-30 and 2025-01-10 |
| TPEx `漲跌` non-numeric values | `---` (untraded) and `除息` / `除權` (不比價) |
| TWSE closed date (2024-07-24) | `stat` = `很抱歉，沒有符合條件的資料!` |
| TPEx closed date (2024-07-24) | `stat` = `ok`, zero rows |

Both facts the design turns on were measured, not assumed: the whole-market
feeds are a strict **superset** of legacy on every date checked, and TPEx carries
a 不比價 marker the audit's CSV-era header table did not record.

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| Each TPEx header variant parses | PASS | All three parse from the real response bytes captured at their own boundary dates — 2020-01-02 (`prices_only`), 2020-04-30 (`volume_in_thousand_shares`), 2026-09-11 (`volume_in_lots`) — and each file is a permanent fixture. Variant one exposes no disclosed bid/ask volume, so those columns stay NULL rather than defaulting to zero. |
| An unknown header fails closed | PASS | The variant table is keyed on the exact field tuple; an added column raises `schema_mismatch`. A `flagField` that contradicts its header is rejected the same way, and a TWSE header change or a missing stock section is `schema_mismatch` too. |
| Units are declared, not inferred | PASS | Each unit comes from the feed that declares it: TWSE `hints: 單位：元、股` for its whole table, TPEx from its `成交金額(元)` and `最後買量(千股)` / `(張數)` labels. Only TPEx's disclosed bid/ask level is in lots, so only it is converted ×1,000. The TWSE adapter re-checks `hints` on every parse and raises `unit_declaration_changed` on a restatement. A quantity that is not a whole source unit raises `ambiguous_unit`. |
| A closed date fails closed in both markets | PASS | Both raise `no_data_for_date` — TWSE answers an apology with no tables, TPEx an empty table — so 17-b can skip such a date without re-deriving the calendar. A TWSE status this adapter cannot interpret stays `source_status`. Both regressions use the real closed-date responses. |
| Values equal legacy `daily_quotes` | PASS | 4,423 legacy rows across five market-dates covering all three variants: open/high/low/close, volume, trade value and trade count compared, **zero differences**, and no legacy-only row on any date. |
| Extra rows are explained, not silent | PASS | The feeds carry 287 more TWSE and 150 more TPEx rows on 2026-09-11. Classified: untraded securities (9 and 29 — legacy's minimum volume over the whole window is 1) and instrument classes legacy never collected (ETFs, preferred shares, TDRs). The per-security no-metadata report is Step 17-c's. |
| `price_direction` claims only what is published | PASS | TWSE's own column, as `+`/`-`/`flat`/`X`. TPEx publishes no direction column, so an ordinary TPEx row claims none; its `除息` / `除權` / `除權息` marker is the same 不比價 statement as TWSE's `X` and parses to `X`. |
| The stock section is found by its header | PASS | A regression reverses the table order and still reads 1,379 rows; a second asserts that two or zero matching tables is `schema_mismatch`. |
| Identity is unambiguous | PASS | A duplicated security code in one file raises `ambiguous_identity` rather than letting one trade date be written twice. |

Reconciliation against legacy `stock_db`, parsing each fixture through its
adapter and comparing every security legacy carried:

```text
2026-09-11 twse_mi_index:   parsed=1379 legacy=1092 compared=1092 diffs={}
2026-09-11 tpex_otc_quotes: parsed=1012 legacy=862  compared=862  diffs={}
2020-01-02 twse_mi_index:   parsed=1114 legacy=955  compared=955  diffs={}
2020-01-02 tpex_otc_quotes: parsed=876  legacy=752  compared=752  diffs={}
2020-04-30 tpex_otc_quotes: parsed=885  legacy=762  compared=762  diffs={}
```

## Design decisions

**Two new source codes, not more revisions of the pilots.** The whole-market
feeds publish a disclosed bid/ask level the per-security pilots do not. Sharing
a source code would make one `(security, trade_date)` alternate between two field
sets on every import, which CLAUDE.md §30 forbids. ROADMAP left the choice open
between this and retiring the pilots; the pilots are kept, because a
single-security spot check is still worth one request instead of a whole market.
The codes are declared here and become storage policy in 17-b.

**The stock section is found by its header.** It sits at table index 8 today,
after six index sections and two summary sections that are Step 18's. A
positional read would silently start returning index rows the day TWSE adds a
section.

**`X` stores no price change.** TWSE's own note reads `+/-/X表示漲/跌/不比價`,
and every `X` row in every file inspected fills `漲跌價差` with `0.00`. Storing
that zero would claim the price did not move on a day it demonstrably did.
TPEx writes the reason — `除息`, `除權`, `除權息` — where the number would be,
which is the same statement with no number to misread; it maps to the same `X`.
The source's own wording survives in the raw artifact.

**TPEx claims no ordinary direction.** The feed has no `漲跌(+/-)` column: the
sign is inside the number. Deriving a direction from it would be our restatement,
not the source's observation, so `price_direction` stays NULL except for the
不比價 marker. The audit is updated in the same step, as the source-field rule
requires.

**The disclosed bid/ask level is a different unit in each market.** TWSE
declares `單位：元、股` for its whole table and makes no other unit statement
about it, so that column is already shares; TPEx labels the column itself,
`最後買量(千股)` / `(張數)`, so only TPEx is converted. This corrects an earlier
audit line that recorded the TWSE column as lots — see the review section
below.

**A blank TWSE sign must accompany a zero.** Every blank-sign row publishes
`0.00`, so a blank sign next to a non-zero magnitude is a change whose direction
would have to be guessed: it raises `ambiguous_direction`. A row that did not
trade has no close, and therefore no change and no direction.

## Code-review findings

Three findings, all verified before anything changed; none was a false positive,
and the first was larger than reported.

| # | Finding | Verified by | Disposition |
| --- | --- | --- | --- |
| 1 | The blanket ×1,000 on TWSE disclosed bid/ask volume is wrong for the classes note 3 excludes — 00636K traded 200 shares all day yet stored a 11,000-share best bid | The rows are real, but the diagnosis is not the whole story, and both the finding's argument and my original one were inferences from magnitude, which §72 forbids. `TWT53U`, the odd-lot report, settles it structurally: same column labels, same `單位：元、股` hint, and 2330 shows `最後揭示買量 = 200,937` — only shares can be that. | **Fixed, wider than reported.** The column is shares for *every* TWSE row, not just the excluded classes: the conversion is removed entirely. The audit line claiming lots was never sourced and is corrected. Note 3 turns out to be about each security's trading unit, not this column's unit, and no stored column depends on it. |
| 2 | TWSE units were asserted in a comment and never checked at runtime, so a switch to 仟股 under an unchanged header would mis-scale everything by 1,000 | Read: the TPEx path checked `flagField`, the TWSE path checked nothing. | Fixed, and now load-bearing: `hints` is the statement the unit is taken *from*, so a restatement raises `unit_declaration_changed`. |
| 3 | A TPEx closed date and a genuine empty result share `empty_coverage`, so 17-b cannot tell a benign holiday skip from a coverage failure | Read, and confirmed against the real closed-date response. | Fixed. Both markets raise `no_data_for_date`, detected structurally (TWSE returns no `tables` key at all). Not named `market_closed`: only the Step 16 calendar can call a date a closure, and the same answer covers a date the source simply has nothing for. |

Finding 1 is the one that matters, and it is the reason the seam was worth
having: the reconciliation that passes 4,423 rows with zero differences could
never have caught it, because legacy `daily_quotes` has no bid/ask columns at
all. It would have shipped as 1,000×-inflated order-book data behind a green
acceptance table.

## Verification

Database migrated from zero:

```text
409 passed, 3 skipped, 1 warning
```

Baseline before this step: 378 (Step 15-c). The 31 adapter tests added here are
the difference — 27 in the original push, 4 more for the review findings. They run against captured response bytes with no database, which
is the whole point of the seam: this step writes nothing, so it needs no
integration test.

Every one of the 31 was seen to fail first: the original 27 before the adapter existed, and the review's 4 (plus 2 changed assertions) against the adapter as pushed. `ruff check` reports
nothing new: the files this step touches match their state on `main` (the two
pre-existing findings in `ingestion/models.py` are unchanged).

The shared `stockdc` test database was stale from earlier work and could not be
migrated to head at all, which made 188 tests error before anything was run. It
was recreated from zero; that is environment, not code.

## Scope exclusions confirmed

- Nothing is written and no migration ships: the source policy, the release-rule
  mapping, the expected-coverage declarations and the importer are 17-b's.
- The index sections of the TWSE artifact are untouched; Step 18 reuses the same
  raw artifacts.
- `bid_snapshot` / `ask_snapshot` stay NULL: no daily whole-market endpoint
  publishes order-book depth.
- TPEx `發行股數` and next-day limit prices stay unstored, as audit §6 records.
- No adjusted prices, and no readiness claim for returns or indicators: the
  §51.4 gate still waits on corporate-action history.
