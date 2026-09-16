# Step 18-a Acceptance Report

Status: IN REVIEW

Scope: Market-index adapters

Schema impact: none. Migration impact: none. PIT impact: none — this step writes
nothing; it turns official bytes into `MarketIndexObservation` values.
`src/` changed by +573/−0 lines.

## Why Step 18 is split three ways

Five adapters and two importers run well past what one pull request can be
reviewed as (CLAUDE.md §1). The seams are the ones Step 17 proved: 18-a parses,
18-b imports and backfills, 18-c does the second dataset. Indices and valuation
are separate datasets in separate tables, so each part is correct on its own.

## The spike ROADMAP required, resolved

Step 18 was gated on spiking TPEx for an OTC index-OHLC endpoint before
promising anything. The audit's recorded negative result is **wrong, and the
data is still not backfillable**.

`openapi/v1/tpex_index` (`櫃買指數歷史資料`) does publish
`Open/High/Low/Close/Change` for `櫃買指數`, the OTC counterpart of the TAIEX.
It accepts **no parameters** — `d=`, `date=` and `yr=/mn=` are all ignored — and
always returns the current calendar month, 12 rows on 2026-09-16, despite its
name. OTC index OHLC therefore stays NULL for 2020–2026, and Step 27's forward
capture can accumulate it from the day it starts. Audit §4.2 is corrected in
this PR, including the earlier 404 probes that produced the wrong conclusion.

## Baseline, measured before any code was written

Legacy `stock_db`, 2020-01-02 → 2026-09-11:

| Measurement | Result |
| --- | ---: |
| `market_indices` rows | 449,528 |
| distinct TWSE index names | 366 |
| distinct TPEx index names | 43 |
| dates | 1,627 |

Live source shape, verified 2026-09-16:

| Measurement | Result |
| --- | --- |
| `MI_INDEX` index sections | 6 — price and return, each for TWSE, cross-market and TIP |
| TWSE index rows on 2026-09-11 | 273, no repeated name |
| TPEx `indexSummary` sections | 2, stable 2020 → 2026 |
| TPEx index rows | 74 (2026-09-11), 60 (2020-01-02) |
| `MI_5MINS_HIST` for 2026-01 | 21 rows, one index |

## The identity finding

**TPEx repeats one name across its two sections**, so the published name alone
is not an identity — and audit §4.2 said it was.

```text
指數段      櫃買指數   收市 395.52   漲跌  -9.72
報酬指數段  櫃買指數   收市 735.15   漲跌 -18.06
```

32 of TPEx's 34 names appear in both sections. TWSE happens to avoid the
collision by naming its return indices distinctly — `發行量加權股價指數` versus
`發行量加權股價報酬指數` — but the identity has to hold for both feeds, so it is
`(source, section, published name)`. The section is structural rather than a
business value: a price index does not become a return index, which is the test
CLAUDE.md §51.5 applies to any constructed key. The longest resulting
`index_code` is 49 characters, inside the column's 64.

Legacy `market_indices` holds 43 OTC names with `櫃買指數` appearing exactly
once per trade date, so it kept one section and **lost the entire TPEx return
series**. That is new data here, not a reconciliation difference, and 18-b will
report it as such.

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| Every TWSE index section is read | PASS | 273 rows from 6 sections. A regression asserts the section count, so reading only the first would fail. |
| Index identity survives the TPEx collision | PASS | `櫃買指數` parses twice with different `index_code` values and different closes; a repeated name *within* one section still raises `ambiguous_identity`. |
| The TWSE adapter can reuse Step 17-c's artifact | PASS | Its resource key is `twse_mi_index:daily-quotes:<date>` — the price import's — so 18-b's lifecycle finds the stored artifact instead of fetching the file again. No TWSE index fetch is needed for the whole window. |
| The unsigned points are signed by their own column | PASS | 寶島股價指數 parses to `-865.63` from `865.63` plus a `-` marker; 40 rows are positive and every one has a positive percent. A blank sign with a non-zero magnitude raises `ambiguous_direction`, and `X` (不比價) stores no change. |
| OHLC is the TAIEX's alone | PASS | `open/high/low/trade_value` are NULL for every whole-list row; `MI_5MINS_HIST` parses 2026-01-02 to `29016.68 / 29363.43 / 29007.75 / 29349.81` and claims no change columns, because that feed publishes none. |
| Everything else fails closed | PASS | Closed date → `no_data_for_date`; wrong date → `date_mismatch`; changed header → `schema_mismatch`; repeated name in a section → `ambiguous_identity`; TAIEX row outside its month → `date_mismatch`. |

## Verification

```text
192 passed
```

Baseline before this step: 172. The 20 adapter tests added here are the
difference, and each was seen to fail first. They run against captured response
bytes with no database, which is the point of the seam: this step writes
nothing.

`ruff check` reports nothing new against `main`; the two pre-existing findings in
`ingestion/models.py` are unchanged.

## Scope exclusions confirmed

- Nothing is written and no migration ships: the writers, source policy,
  coverage declarations, CLI and backfill are 18-b's.
- Official valuation is 18-c's.
- Index trade value stays NULL: no inspected source publishes it.
- OTC index OHLC stays NULL for past dates, per the spike above.
- No index-rename linking. A renamed index is a new identity until official
  evidence says otherwise, which no feed currently provides.
