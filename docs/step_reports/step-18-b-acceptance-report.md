# Step 18-b Acceptance Report

Status: IN REVIEW

Scope: Market-index import path and backfill

Schema impact: none. Migration `7a2c9e4d1b58` adds rows only: three
`dataset_sources`, their release-rule mappings, and two `daily_price`-style
expected-coverage declarations.
PIT impact: none new — all three sources follow `exchange_daily_settled@1`.
`src/` changed by +653/−22 lines, plus one committed script.

## Baseline

Legacy `stock_db.market_indices`, 2020-01-02 → 2026-09-11: 449,528 rows over
1,627 dates, 366 distinct TWSE names and 43 TPEx names.

Step 18-a's parse, merged as #22, with the identity finding it made:
`(source, section, published name)`, because TPEx repeats 32 of its 34 names
across its price and return sections.

## The run

```text
                     indices   dates        rows
twse_mi_index            335   1,627     365,775
tpex_index_summary        95   1,627     111,224
twse_mi_5mins_hist         1   1,630       1,630
                                        ---------
                                          478,629
evidence 478,629 release_rule    artifacts 5,046    data/raw 560 MB
```

Both whole-list backfills: `imported 1627, resumed 0, failed 0, is_complete
true`. The TAIEX run covers 81 months; its 1,630 dates exceed the window by the
three September 2026 dates after the 11th.

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| Legacy `market_indices` reconciles for both markets | PASS | TWSE: 365,342 rows compared, **23 differences**, all on two dates, explained below. TPEx: 51,623 compared, **zero differences**. |
| Every difference is classified | PASS | Five classes, counted separately: `legacy_only` (23), legacy rows that are not indices at all (32,540), source-only return section (220 TWSE / 59,601 TPEx), source-only price section (213 / 0), and zero value disagreements. |
| TAIEX close from `MI_5MINS_HIST` equals the `MI_INDEX` close | PASS | **1,627 dates compared, zero disagreements.** Both TWSE sources publish `發行量加權股價指數`; the cross-check joins them per date and reports rather than averages. |
| TPEx return indices are new data, not a difference | PASS | We hold 95 TPEx indices — 43 price, 52 return — against legacy's 43, which are exactly the price section. The 59,601 source-only rows are the return series legacy never collected. |
| A reprocess path, or the re-fetch stated plainly | PASS, by the second branch | No reprocess path was built, and the TWSE index files were fetched again. The reason is recorded in code and below; content addressing keeps the cost to the requests, not the storage. |
| Coverage is complete | PASS | `expected 1627, observed 1627, missing [], unexpected [], is_complete true` for both markets, from the Step 16 validator. |

## Why the TWSE index files were fetched again

Step 18-a named `stored_resource_key()` so a reprocess path could read the bytes
Step 17-c stored. Building that path was rejected here, for a reason worth
recording: the lineage foreign key requires every version's
`(raw_artifact_id, ingest_run_id)` to exist in `raw_artifact_observations`, and
that table's `fetched_at` is *when we read from the source*. A reprocess run
reads from disk. It would have to either invent a fetch instant or inherit the
original one, and the second feeds ADR-0020's capture decision an instant this
run did not produce. That is the class of change this repository has been bitten
by before, and it is not worth an hour of requests.

Content addressing made the re-fetch nearly free. The re-fetched `MI_INDEX`
bytes hash to the artifact already on disk, so `raw_artifacts` gained nothing for
TWSE; only an observation row per date, which is honest — we did read TWSE
again. TPEx `indexSummary` is a different endpoint, so those artifacts are new.

## Two defects the tests caught

**The batch writer's key was not unique.** It keyed created rows by the link
column, which holds for one security per trade date but not for the TAIEX
import: one index across 21 dates collapsed onto a single key, and each date's
evidence was attached to whichever version came back last. The database caught
it as `publication precedes source date`. The key is now the link column plus
the identity fields.

**The migration's downgrade deleted too much.** It removed every `market_index`
source, including the `market_index/twse` row a Phase 8 fixture creates, which
has ingest runs referencing it — a `RESTRICT` violation. It now deletes only the
three sources it declared. Same shape as Step 17-b's review finding 3, one
migration later.

## A defect the live run caught

**The month loop had the resume defect Step 17-c fixed for the date loop.** The
TAIEX backfill died on a `ReadTimeout` at month 56 of 81, and re-running it
re-fetched all 56 because the loop still minted a fresh `uuid4()` base. That is
the same finding as #21's first, in the sibling loop — the variant that a fix
applied in one place leaves behind. `month_import_id` now derives from a scope
id, and one unreachable month is reported with a non-zero exit rather than
ending the run.

## What the reconciliation had to learn about legacy

Two legacy behaviours had to be classified before the numbers meant anything,
and both were found by looking at what did not match rather than by assuming.

**Legacy stored things that are not indices.** 32,540 rows are the
`漲跌證券數合計` market-breadth table — `1.一般股票`, `12.公司債`, `13.ETN`,
`證券合計(1+6+14+15)`, `持平`, `未成交`. Legacy's parser wrote them into
`market_indices` alongside real indices. They are counted as their own class,
never as a difference.

**Two legacy dates are short.** All 23 remaining `legacy_only` rows fall on
2024-01-25 and 2026-02-09, where legacy holds 95 and 139 index rows against 277
and 287 on the neighbouring dates. Those two legacy captures are incomplete —
the same family as the 2026-03-27 daily-price case in Step 17-c. They are
reported by date rather than explained away, because an unexplained row must not
hide inside an expected class.

The two feeds also need different matching rules, which one attempt got wrong
before the numbers exposed it: TWSE names its return indices distinctly and
legacy collected both sections, so a legacy name matches whichever section holds
it; TPEx repeats one name in both and legacy kept only the price one, so
matching must stay inside the price section. Sharing one rule produced 50,014
false differences on TPEx.

## Scope exclusion recorded in code

`market_index_metadata_versions` is **not** written. The database enforces that
a version's ingest run carries that version's own `dataset_code`, so index
metadata needs its own run, source policy and evidence — a second dataset's
wiring for two columns audit §5 already records as derived values of ours rather
than the source's. The published name lives in `market_index.index_code`, which
is where identity reads it from anyway.

## Verification

Database migrated from zero:

```text
467 passed, 3 skipped, 1 warning
```

Baseline before this step: 457. The 10 integration tests added here are the
difference, and each was seen to fail first.

`ruff check` reports nothing new against `main`.

## Scope exclusions confirmed

- Official valuation is 18-c's.
- Index trade value stays NULL: no inspected source publishes it.
- OTC index OHLC stays NULL for past dates — `openapi/v1/tpex_index` takes no
  parameters and always answers the current month (audit §4.2).
- No index-rename linking. A renamed index is a new identity until official
  evidence says otherwise.
