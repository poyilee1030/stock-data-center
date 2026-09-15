# PR #14 Acceptance Report

Status: IN REVIEW

Scope: Source-Reality Alignment of inventory and storage contract

Schema impact: none. Migration: none. PIT impact: none. No file under `src/` or
`migrations/` changed.

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| Inventory, audit, and schema agree | PASS | `storage_contract` in `docs/data_domain_inventory.json` classifies all 154 non-structural columns of the 16 observed `*_versions` tables. `test_every_stored_column_has_a_source_coverage_entry` compares it against the live SQLAlchemy metadata; `test_unsourced_and_partial_columns_match_the_audit` compares its exception set against audit §5 row for row. |
| The new test fails if a column is added without a source mapping | PASS | Adding `unsourced_probe_column` to `daily_price_versions` in `db/metadata.py` fails `test_every_stored_column_has_a_source_coverage_entry`; removing it restores green (run below). |
| Unsourced-field claims corrected | PASS | stock-tag effective dates, index trade value, non-TAIEX index OHLC, order-book depth, monthly-revenue currency, and the corporate-action announcement/record/payment dates and earnings/capital-surplus split are all `unsourced` or `partially_sourced` in the registry and in audit §5. |
| Dropped sourced fields restored | PASS | The eight monthly-revenue published comparatives are `observed` against `monthly_revenue_versions.*` (PR #22 adds the columns); the TAIEX OHLC is `partially_sourced` from `MI_5MINS_HIST` instead of `unsourced`. |
| Not-in-v1 domains marked | PASS | Stock tags, the XBRL codebook, the margin market summary, and `monthly_revenue_growth:v1` are marked **not in v1** in the inventory matrix and ROADMAP §16. |
| New domain added | PASS | `dividend_declaration` is in the v1 storage contract matrix, naming the `dividend_declaration_versions` table PR #33 adds. |

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
| `revenue_last_month`, `revenue_last_year`, `mom_pct`, `yoy_pct`, `revenue_cumulative`, `revenue_cumulative_last_year`, `cumulative_yoy_pct` | canonical derived (`monthly_revenue_growth:v1`) | observed `monthly_revenue_versions.*` (PR #22) |
| `comment` (備註) | raw-artifact-only | observed `monthly_revenue_versions.note` (PR #22) |
| TAIEX `open_value`/`high_value`/`low_value` | unsourced (audit §5) | partially sourced, `MI_5MINS_HIST` (PR #18) |

Two gaps the audit implied but had not stated, added to §5 by this PR:

- `corporate_action_versions.old_shares` / `new_shares` are **partially sourced**:
  capital reduction only. The TWSE `TWTB8U` par-value detail fields are
  unverified and no TPEx par-value endpoint was found (§4.10).
- `security_metadata_versions.name` / `industry` are **partially sourced**: the
  snapshots publish current values only, so an earlier effective date carries the
  current value (§4.11). ROADMAP §16 already noted this for the domain; it was
  not visible at the column level.

Audit §5 was rewritten from two prose lists into one table with one row per
`table.column` and a status of `unsourced` or `partially sourced`. No fact was
added or removed by the restructuring itself; the row count grew from 8 grouped
entries plus a prose sentence to 32 explicit columns, and it is now parseable,
which is what lets the test compare it with the registry.

## Verification

Clean PostgreSQL database (`stockdc_pr14_probe`, migrated from zero to
`7c9e2a4b6d81`):

```text
248 passed, 3 skipped, 1 warning in 23.77s
```

Baseline on `main`, same database state: 183 passed. The 9 new tests are the
whole difference; no existing test changed behaviour.

Failing-on-purpose evidence for the acceptance criterion:

```text
$ # add sa.Column("unsourced_probe_column", sa.Text()) to daily_price_versions
$ pytest tests/unit/test_pr14_storage_contract_source_coverage.py -q
FAILED ...::test_every_stored_column_has_a_source_coverage_entry
1 failed, 8 passed
```

All 9 tests were confirmed red before the documents were written (TDD):

```text
9 failed in 0.14s
```

## Known environment issue, not caused by this PR

The long-lived local `stockdc` database reports 56 integration failures on this
branch **and on `main`** (identical set). Its `alembic_version` is already at
head, so `command.upgrade(..., "head")` is a no-op, while `market_index` still
carries the `market` and `name` columns a later migration revision removes — the
database predates an amended migration. A database migrated from zero produces
the schema the metadata declares and the whole suite passes. Recreating that
local database resolves it; nothing in the migration chain needs changing.

## Scope exclusions confirmed

- Unsourced columns are not dropped. They stay nullable and unpopulated.
- No column was added to any table: the monthly-revenue comparatives are
  recorded as PR #22's schema change, not made here.
- `dividend_declaration_versions` is documented as a planned domain; PR #33
  creates the table.
