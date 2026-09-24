# Canonical Derived Data Contract

## Ownership boundary

`stock-data-center` owns observed source facts and deterministic canonical
derived datasets that retain a stable financial meaning independent of any
particular model. Downstream ML repositories own experiment-specific features,
interactions, labels, scores, training, and ranking.

A derived metric belongs here only when it is deterministic, reusable across
repositories, financially well-defined, and reconstructible from PIT-safe Data
Center inputs. Examples include standard technical indicators, TTM EPS,
shareholding concentration, and margin/SBL ratios. A weighted model signal does
not qualify.

## Definition identity

Every canonical derived dataset has an immutable definition, a code constant
(`stock_data_center.v2.derived`, `stock_data_center.v2.derived_store`,
ADR-0027) identified by:

- dataset code and explicit `derivation_version`;
- formula/specification;
- implementation version: its git history. Stored rows carry no commit; the
  on-demand `technical_indicators_pit:v1` returns the commit it computed with;
- required input datasets;
- timezone and calendar convention where relevant; and
- price-adjustment convention where relevant.

A formula or convention change creates a new derivation version and recomputes
the whole table; an existing definition is never edited.

The dataset code names the semantics and the number after the colon names only
the formula. A stored dataset follows the latest inputs and has no marker
(`technical_indicators:v1`); the on-demand PIT reference carries `_pit`
(`technical_indicators_pit:v1`). One formula, one version for both.

## Stored datasets (Step 26)

Each dataset is one wide table keyed by `(stock_id, source, trade_date)`, one
column per metric, plus `computed_at` (ROADMAP §17, ADR-0027 revision of
2026-09-24). No per-row version, commit, lineage, hash or PIT context.

| Dataset | Table | Inputs | Series |
| --- | --- | --- | --- |
| `technical_indicators:v1` | `technical_indicators` | `daily_prices` | every trade date of one stock's prices from one source |
| `institutional_streaks:v1` | `institutional_streaks` | `institutional_flows`, `daily_prices` | the days the stock traded (volume above zero) on the market of the institutional source; `twse_t86` counts over `twse_mi_index` days, `tpex_insti_daily_trade` over `tpex_otc_quotes` days |

**Latest inputs.** A value is computed from each input key's latest recorded
row. There is no knowledge-time axis: a corrected input recomputes the dates it
reaches and overwrites them, so the tables are not append-only. On 2020–2026
history no input key has a second row, so the latest values are the PIT ones;
a correction captured later moves the derived value with it. This is a
deliberate simplification to disclose downstream.

**No future data.** The value for date D uses only inputs dated on or before D;
every formula is causal along the trade date. An input published after its data
date is aligned to its publication (`valuation_metrics:v1`, Step 26).

**Incremental runs.** `python -m stock_data_center.v2.derived_store --dataset
<code>` brings one table up to the inputs recorded so far, in one transaction:

1. It takes each input writer's advisory lock shared, which waits for writers
   mid-transaction and holds new ones off until it commits, and fixes
   `computed_at` at that instant. Every input row stamped before it is read;
   every one stamped after it is left to the next run.
2. For each series, the earliest input date recorded after the previous run's
   `computed_at` is where rewriting starts. A new trading day and a corrected
   earlier one are the same case.
3. The series restarts `BUFFER_DAYS` (500) calendar days before that date, like
   legacy, and further back when that span holds fewer rows than the longest
   window (240), and the rows from the start date on are replaced.

`--full` recomputes every series from its first row.

**Accuracy of an incremental run** (owner decision 2026-09-24). The windowed
metrics (MA, VMA, Bollinger) and the streaks are exact. The exponential ones
(K, D, RSI, MACD) never forget where they started, so a restart leaves a
residue: at most 1e-5 of the close for MACD and 1e-6 absolute for K, D and RSI
(`derived_store.within_tolerance`; measured maxima 4.1e-6 and 6.5e-8). A full
run has none, and equals `technical_indicators_pit:v1` bit for bit while no
input has a correction (`scripts/verify_derived_store.py`).

## On-demand PIT reference

`technical_indicators_pit:v1` (Step 35-c-4) computes the same formula from the
inputs resolved under an explicit PIT context and writes nothing. Its market
visibility is inherited from the inputs selected under `information_as_of` and
`knowledge_as_of`. The rolling as-of series computes each observation date at
the instant its own inputs became public, so no value in it could see a later
price, and a correction to an earlier input that became available later changes
every value after that instant and nothing before it
(`docs/pit_semantics.md`). One stock's full series takes about 0.11 s; reading
it from the stored table takes about 0.017 s.

## `computed_at`

Computation provenance, never a publication time or an eligibility cutoff
(CLAUDE.md §44): the instant a run fixed its inputs.
