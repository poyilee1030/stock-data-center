# Step 14 Acceptance Report

Status: IN REVIEW

Scope: Source-Reality Alignment of inventory and storage contract

Schema impact: none. Migration: none. PIT impact: none. No file under `src/` or
`migrations/` changed.

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| Inventory, audit, and schema agree | PASS | `storage_contract` in `docs/data_domain_inventory.json` classifies 194 non-structural columns across 24 tables and excludes the other 29 by name and reason, so all 53 tables in the metadata are accounted for. `test_every_stored_column_has_a_source_coverage_entry` compares the columns against the live SQLAlchemy metadata; `test_unsourced_and_partial_columns_match_the_audit` compares status *and* effect against audit §5 row for row. |
| The new test fails if a column is added without a source mapping | PASS | Four fault injections, each failing the test that should catch it (runs below): an unmapped column on `daily_price_versions` and on `financial_facts`, a `stays NULL` effect on a `NOT NULL` column, and an `observed` target naming a column that exists in no migration and no planned PR. |
| Unsourced-field claims corrected | PASS | stock-tag effective dates, index trade value, non-TAIEX index OHLC, order-book depth, monthly-revenue currency, and the corporate-action announcement/record/payment dates and earnings/capital-surplus split are all `unsourced` or `partially_sourced` in the registry and in audit §5, each with the effect it has on the stored column. |
| Dropped sourced fields restored | PASS | The eight monthly-revenue published comparatives are `observed` against `monthly_revenue_versions.*` (Step 22 adds the columns); the TAIEX OHLC is `partially_sourced` from `MI_5MINS_HIST` instead of `unsourced`. |
| Not-in-v1 domains marked | PASS | Stock tags, the XBRL codebook, the margin market summary, and `monthly_revenue_growth:v1` are marked **not in v1** in the inventory matrix and ROADMAP §16. |
| New domain added | PASS | `dividend_declaration` is in the v1 storage contract matrix, naming the `dividend_declaration_versions` table Step 33 adds. |

## What changed in the two documents

Corrections where the inventory claimed unsourced fields:

| Claim | Corrected to |
| --- | --- |
| `stock_tags` observed with explicit effective interval | Not in v1: MoneyDJ third-party snapshot, no effective dates, no consumer |
| `market_indices` "available OHLC / change percent / trade value" | Close, change points, change percent only; OHLC for the TAIEX alone via `MI_5MINS_HIST`; `trade_value` unsourced; index metadata effective dates are observation dates |
| `daily_quotes.bid`/`ask` → `bid_snapshot`/`ask_snapshot` | → `last_bid_price`/`last_ask_price`: the files publish one order-book level, and the depth blobs have no source |
| `monthly_revenue_versions.currency` as an observation | Page-level constant (單位：千元, always TWD) |
| `dividend` "all dates … are revision content" | No announcement, record, or payment date, and no earnings / capital-surplus split, in any exchange result feed |

Corrections where it dropped sourced fields consumers read:

| Legacy field | Was | Now |
| --- | --- | --- |
| `revenue_last_month`, `revenue_last_year`, `mom_pct`, `yoy_pct`, `revenue_cumulative`, `revenue_cumulative_last_year`, `cumulative_yoy_pct` | canonical derived (`monthly_revenue_growth:v1`) | observed `monthly_revenue_versions.*` (Step 22) |
| `comment` (備註) | raw-artifact-only | observed `monthly_revenue_versions.note` (Step 22) |
| TAIEX `open_value`/`high_value`/`low_value` | unsourced (audit §5) | partially sourced, `MI_5MINS_HIST` (Step 18) |

Two gaps the audit implied but had not stated, added to §5 by this PR:

- `corporate_action_versions.old_shares` / `new_shares` are **partially sourced**:
  capital reduction only. The TWSE `TWTB8U` par-value detail fields are
  unverified and no TPEx par-value endpoint was found (§4.10).
- `security_metadata_versions.name` / `industry` are **partially sourced**: the
  snapshots publish current values only, so an earlier effective date carries the
  current value (§4.11). ROADMAP §16 already noted this for the domain; it was
  not visible at the column level.

Audit §5 was rewritten from two prose lists into one table with one row per
`table.column`, a status of `unsourced` or `partially sourced`, and an effect. No
fact was removed by the restructuring itself; the row count grew from 8 grouped
entries plus a prose sentence to 32 explicit columns, and it is now parseable,
which is what lets the test compare it with the registry.

## Unsourced does not mean NULL

Both §5 and ROADMAP §2.3 said every unsourced column "stays NULL". Six of them
are `NOT NULL`, and `monthly_revenue_versions.currency` is written today by
shipped code (`src/stock_data_center/monthly_revenue/ingestion.py`) and is part
of the revision-identity comparison. A PR obeying that rule literally would have
violated a NOT NULL constraint. Each unsourced column now records its effect:

| Effect | Columns |
| --- | --- |
| stays NULL | the depth blobs, index `trade_value`, and the five corporate-action columns |
| stores a documented constant | `monthly_revenue_versions.currency` — the page unit 單位：千元, always TWD |
| stores a derived value | `market_index_metadata_versions.effective_from`/`effective_to` — our own first and last observation dates |
| table stays empty | `security_tag_versions` and `xbrl_concept_catalog_versions`, whose domains are out of v1 |

`test_a_column_that_stays_null_is_actually_nullable` checks the first row against
the live schema.

## Verification

Clean PostgreSQL database (`stockdc_pr14_probe`, migrated from zero to
`7c9e2a4b6d81`):

```text
250 passed, 3 skipped, 1 warning in 23.79s
```

Baseline on `main`, same database state: 183 passed. The 11 new tests are the
whole difference; no existing test changed behaviour.

Fault injection, all four at once:

```text
$ # 1. add sa.Column("probe_unmapped", sa.Text()) to financial_facts
$ # 2. set monthly_revenue_versions.currency effect to "stays NULL"
$ # 3. drop planned_pr from the monthly_revenue.mom_pct field
$ pytest tests/unit/test_pr14_storage_contract_source_coverage.py -q
FAILED ...::test_every_stored_column_has_a_source_coverage_entry
FAILED ...::test_a_column_that_stays_null_is_actually_nullable
FAILED ...::test_unsourced_and_partial_columns_match_the_audit
FAILED ...::test_observed_targets_exist_in_the_schema_or_name_the_pr_that_adds_them
4 failed, 7 passed
```

An earlier probe adding a column to `daily_price_versions` failed the same
coverage test. All tests were confirmed red before the documents were written
(TDD): the first nine as `9 failed in 0.14s`, the three added in review as
`3 failed, 8 passed`.

## Review findings addressed

A code review of the first push raised eight findings; all eight were confirmed
against the schema and the shipped code, and all eight are fixed here.

| # | Finding | Fix |
| --- | --- | --- |
| 1 | The new paragraph in `docs/schema.md` sat inside the storage-map table, so its last two rows rendered as literal pipe text | Paragraph moved below the table |
| 2 | "Unsourced ⇒ stays NULL" is false for six `NOT NULL` columns, and `currency` is written today | Effect recorded per column, with a test that *stays NULL* implies nullable; §5 and ROADMAP §2.3 reworded |
| 3 | The report repeated the same wrong claim | Scope-exclusions bullet corrected |
| 4 | Eight `observed` targets name columns that exist in no migration | `planned_pr: 22` added, plus a test that every `observed` target exists or names its PR |
| 5 | The guard covered only `*_versions`, so `financial_facts` and friends could gain unmapped columns silently | Coverage extended to every content-bearing table; every remaining table excluded by name and reason, so the guard is total |
| 6 | The §5 section parser would silently drop rows below a future `### 5.1` | Parser asserts it read every `| \`table.column\`` row in the section |
| 7 | `assert record["audit_section"] in audit_text` is a whole-document substring test, so "4.1" matches inside "4.10" | Removed; `test_referenced_audit_sections_exist` is the real check |
| 8 | The inventory said last bid/ask *volume* is observed from a legacy field that does not exist | Reworded: the source publishes it, Step 17 stores it, the legacy table has no field for it |

## Known environment issue, not caused by this PR

The long-lived local `stockdc` database reports 56 integration failures on this
branch **and on `main`** (identical set). Its `alembic_version` is already at
head, so `command.upgrade(..., "head")` is a no-op, while `market_index` still
carries the `market` and `name` columns a later migration revision removes — the
database predates an amended migration. A database migrated from zero produces
the schema the metadata declares and the whole suite passes. Recreating that
local database resolves it; nothing in the migration chain needs changing.

## Scope exclusions confirmed

- Unsourced columns are not dropped. Most stay NULL; the six that are `NOT NULL`
  hold a documented constant, a value derived from our own observations, or
  nothing at all because their table is out of v1. Audit §5 records which, per
  column, and a test checks that every column marked *stays NULL* is nullable.
- No column was added to any table: the monthly-revenue comparatives are
  recorded as Step 22's schema change, not made here.
- `dividend_declaration_versions` is documented as a planned domain; Step 33
  creates the table.
