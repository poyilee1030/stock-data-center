# Database Schema (v2)

Schema v2 is ADR-0027's design, declared in `src/stock_data_center/db/schema_v2.py`
and built by one Alembic migration, the baseline `a273160c0288` (Step 35-d-3).
Every table's per-column source coverage is `storage_contract` in
[`data_domain_inventory.json`](data_domain_inventory.json), checked against this
schema and audit §5 by `tests/unit/test_pr14_storage_contract_source_coverage.py`.

## Tables

| Table | Key | Content |
| --- | --- | --- |
| `fetches` | `id` | one row per fetch attempt: dataset, source, resource key, purpose, adapter version, git commit, status and reason, and the raw file's SHA-256 and size |
| `stocks` | `stock_id` | today's ISIN list of listed and OTC common stocks (ADR-0026) |
| `trading_days` | `trade_date` | one row per TWSE trading date |
| `daily_prices` | `stock_id, source, trade_date, recorded_at` | OHLC, volume, value, count, change, direction, last bid/ask |
| `index_prices` | `source, index_name, trade_date, recorded_at` | the 126 kept indices: OHLC (TAIEX only), close, change |
| `valuations` | `stock_id, source, trade_date, recorded_at` | official PE, PB, dividend yield, dividend year, report period |
| `institutional_flows` | `stock_id, source, trade_date, recorded_at` | per-stock institutional buy, sell, net |
| `institutional_market_flows` | `source, trade_date, institution, recorded_at` | market-wide institutional buy, sell, net |
| `foreign_holdings` | `stock_id, source, trade_date, recorded_at` | issued, investable, held shares and ratios |
| `margin_trading` | `stock_id, source, trade_date, recorded_at` | margin and short activity, balances, limits |
| `securities_lending` | `stock_id, source, trade_date, recorded_at` | SBL activity, balance, limits |
| `monthly_revenues` | `stock_id, source, revenue_month, recorded_at` | revenue, published comparatives, note, `published_at` |
| `financial_reports` | `id`; unique `stock_id, report_year, report_quarter, recorded_at` | one version of one quarterly report, `published_at` |
| `financial_report_facts` | unique `report_id, statement, concept, period_start, period_end` | the numeric facts of the three statements |
| `shareholding_distributions` | `stock_id, source, snapshot_date, recorded_at` | TDCC levels 1–17 as one wide row |
| `corporate_actions` | `stock_id, source, ex_date, recorded_at` | executed events from the six result feeds |
| `technical_indicators` | `stock_id, source, trade_date` | `technical_indicators:v1`: MA/VMA 5–240, K, D, RSI 6/12, MACD, Bollinger; `computed_at` |
| `institutional_streaks` | `stock_id, source, trade_date` | `institutional_streaks:v1`: foreign/trust/dealer streak days; `computed_at` |
| `institutional_cumulative_flow` | `stock_id, source, trade_date` | `institutional_cumulative_flow:v1`: trust/dealer cumulative net shares and ratio; `computed_at` |

The last three are derived (Steps 26-b, 26-c): computed from the latest inputs and
overwritten in place, so they are not append-only (`docs/derived_data.md`).
Release rules, dataset declarations, the index list and the TDCC level profile
are code constants, not tables.

## Rules every table follows

- **Identity is the official code.** Every per-stock table references
  `stocks.stock_id`; listed and OTC common stocks keep one code for life.
- **Append-only.** A value table gains a row only when a published value
  changes; `recorded_at` is `statement_timestamp()`, never caller-supplied.
  `stockdc_reject_mutation()` is the one trigger function: on every table except
  `stocks`, `trading_days` and the derived tables it refuses `UPDATE` and `DELETE` per row and
  `TRUNCATE` per statement.
- **Provenance is one hop.** Every row's `fetch_id` names the fetch it came
  from, and the fetch names its raw file by SHA-256, stored at
  `data/raw/<ab>/<hex>`. A successful fetch must name a file. A TWSE
  `TWT49U`/`TWTAUU` corporate action also names its detail page's fetch in
  `detail_fetch_id`, and only those two feeds may.
- **Exact decimals.** Decimal columns are unconstrained `numeric` with a CHECK on
  significant decimal places and integer digits, because a typed `numeric(p, s)`
  would round a published 30.555 to 30.56 before any CHECK ran. Quantities and
  amounts are `bigint` shares and TWD.
- **No evidence rows, no hashes, no seals.** When a value became public is
  computed from its dataset's release rule, or stored as `published_at` where
  publication differs per issuer (`docs/pit_semantics.md`). A financial report
  and its facts are written in one transaction, which is what a seal used to
  guarantee.

## Migrations

The chain restarts at the baseline, which builds exactly the observed tables
above, the trigger function and the triggers, and nothing else: no extension,
view or sequence of its own. Its downgrade refuses before any change while a
table holds a row, and on an empty database drops everything it built.

`6ecc3eefb103` (Step 26-b) adds the first two derived tables and `0f6386ea08db`
(Step 26-c) the third. Their downgrades drop them without a guard: every derived
row is recomputed from stored inputs, so nothing they discard is history.

A database built by the old 45-migration chain sits at its head `5c1e8d2a7b90`,
with the v2 tables beside the v1 ones. `scripts/rebase_to_baseline.py` moves it
onto the baseline in one transaction: it drops every object a fresh baseline
does not build — v1 tables, views, functions, sequences and the pgcrypto
extension — without CASCADE, records the baseline revision, and commits only if
the result equals a fresh baseline object by object and every v2 table holds the
rows it held. Without `--execute` it only reports what it would drop. Raw files
are never touched. `stockdc_backfill` was moved this way on 2026-09-24.
