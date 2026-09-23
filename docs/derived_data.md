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

Every canonical derived dataset has an immutable definition identified by:

- dataset code and explicit `derivation_version`;
- formula/specification;
- implementation version or Git commit;
- required input datasets;
- timezone and calendar convention where relevant;
- price-adjustment convention where relevant; and
- trusted registration timestamp.

A formula or convention change creates a new derivation version. Existing
definitions and results are never silently overwritten.

## PIT inheritance

Derived market visibility is inherited from the PIT-safe inputs selected under
the request's `information_as_of` and `knowledge_as_of`. Derived system
visibility is inherited from inputs selected under `system_as_of`. Calculation
time never widens input visibility.

`computed_at` records when the Data Center performed or materialized a
calculation. It is operational provenance, not a market publication time, and
must never be used as `published_at` or as the market-PIT eligibility cutoff.

## Materialized result lineage

A materialized result preserves its derivation definition/version, input
dataset identities, deterministic input fingerprint, full PIT context,
computation run, `computed_at`, and storage-generated business hash. Repeating
the same definition with the same PIT-safe inputs must have the same semantic
identity.

## Materialized and virtual equivalence

A canonical metric may be materialized in PostgreSQL or computed on demand.
This is a performance choice. Both strategies use the same definition and PIT
rules and return the same business value, derivation identity, and input
provenance.

v1 computes on demand by default (ROADMAP §17). Step 26-a measured the
long-form materialization at about 740 bytes per metric-day — 56 GB for
`technical_indicators:v1` alone, 28 times the daily prices it is a function
of — and nearly all of it was lineage repeated on every row. A metric is
materialized only by its own step, after measurement shows on-demand
computation too slow, and its stored rows must equal the on-demand ones.

## `technical_indicators:v1`

The first canonical derived dataset (Step 26-a), ported from the legacy
calculator so the consumers trained on those values keep reading the same
series. `storage_strategy` is `virtual`: nothing is stored, and
`TechnicalIndicatorService` computes it on demand in two shapes.

```text
compute(context)          the series as one PIT context sees it
rolling(knowledge_as_of)  the rolling as-of series: each date D at D's cutoff
```

Both read the security's whole daily-price history once, through
`PITResolver.market_history`, which settles every version's authoritative
evidence at the knowledge cutoff and then selects visible versions in memory
with the same rule `resolve` uses. The full 2020–2026 rolling series of one
security takes about 0.2 s.

### Metrics

```text
ma5 ma10 ma20 ma60 ma120 ma240      rolling means of raw official close
vma5 vma10 vma20 vma60 vma120 vma240 rolling means of volume
k d                                  nine-day RSV smoothed twice at alpha 1/3
rsi6 rsi12                           adjusted exponential gain/loss, com = w-1
macd_dif macd_dea macd_hist          12/26 difference with a 9-period signal
bb_upper bb_middle bb_lower          20-day mean ± 2 sample standard deviations
```

The legacy table of the same name also holds the three institutional streak
columns. They are a different derivation with different inputs and a different
release instant, so they are `institutional_streaks:v1` in Step 26-b, not part
of this one.

### Conventions the definition records

`price_adjustment_convention` is `raw_official_close`: no corporate-action
adjustment. Legacy computed on raw prices and its consumers were trained that
way. An adjusted variant is a later derivation version (ROADMAP §26.2), never a
silent change to this one.

`calendar_convention` states the rolling cutoff. Observation date D is computed
at the instant D's own prices become public under the `daily_price` release
rule — `exchange_daily_settled@1`, 03:00 on D+1 — which is why D's own close is
part of D's value and D+1's is not. Taking the cutoff at the end of D instead
would leave D's own close invisible and shift the whole series back a day.

### The two PIT axes are not the same axis

`information_as_of` moves with the observation date: it is that cutoff.
`knowledge_as_of` is the series', and is the same for every row, because a
series computed today is computed from the evidence recorded by today. Pinning both to the observation date would claim the Data Center had
recorded, in 2020, evidence it first stored in 2026 — and would resolve to
nothing at all.

### Missing values are returned, not omitted

A metric without enough history yet — `ma240` on a security's fortieth day —
is returned as `None` on that date's row. "Not enough history" is the answer to
that question, and a reader must be able to tell it apart from a date the
series never covered, which has no row at all.

A date whose own price was published after its cutoff also has no row: at that
instant the market could not have computed it.

### Warm-up is part of the value

K, D, RSI and MACD never forget. A caller that trims the beginning of the input
history gets different numbers for the same dates, which is why the series
always warms up from the security's first visible day. Legacy recomputed
incrementally from a 500-calendar-day buffer, so its exponential columns carry
whatever warm-up its last run happened to have; that difference is quantified
in the Step 26-a reconciliation rather than hidden inside a tolerance.

### One series per source

Daily prices keep separate histories per source (CLAUDE.md §30), so the series
is computed per source and a security that transferred market has one series
per side of the transfer rather than one continuous series across both. Every
returned row names its source.

### Input identity

Every returned row carries `input_fingerprint`, the SHA-256 of the
comma-separated daily-price version ids the observation date read, in
trade-date order, and `input_version_count`. Anyone holding the same versions
can recompute it; a changed input anywhere in the history changes it.
