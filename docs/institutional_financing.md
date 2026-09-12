# Institutional Flow and Securities Financing

## Phase 7 scope

Phase 7 implements cache-free normalized writes and PIT-safe reads for five
observed source datasets already established by the Phase 1 storage contract:

```text
institutional_investor
institutional_market_summary
foreign_holding
margin_trading
securities_lending
```

These are source facts. Phase 7 does not calculate model-specific
interpretations or canonical ratios from multiple datasets.

## Logical keys and business revisions

| Dataset | Logical key excluding source | Revision content |
| --- | --- | --- |
| `institutional_investor` | `(security, trade_date)` | published foreign/trust/dealer gross and net flows |
| `institutional_market_summary` | `(market, trade_date, institution)` | published market-wide buy/sell/net flow |
| `foreign_holding` | `(security, trade_date)` | issued/investable/held shares, published ratios, update metadata |
| `margin_trading` | `(security, trade_date)` | margin and short transactions, balances, limits, published utilization ratios |
| `securities_lending` | `(security, trade_date)` | previous balance, borrowed, returned, balance, limits, signed adjustment, note |

`source` is part of every revision identity. Independent TWSE, TPEx, or other
approved source histories are never merged, averaged, or selected by latest
ingestion. A changed source value creates a new immutable business revision.
An unchanged refetch reuses the existing revision and adds an append-only link
to the new raw artifact and ingest run.

PostgreSQL generates `business_content_hash` from the revision content and
generates `ingested_at` from trusted server time. Logical keys, publication
times, ingestion times, and raw/ingest identifiers are not business content.

## Canonical stock-quantity unit

Every Phase 7 stock quantity is stored and returned in **shares (`股`)**. Lots
(`張`) are never a canonical storage or API unit. One Taiwan market lot is
normalized as exactly 1,000 shares before the canonical observation is built,
written, or hashed:

```text
500 lots    -> 500000 shares
500000 shares -> 500000 shares
```

Source adapters must represent every stock quantity as `SourceShareQuantity`
with an explicit `QuantityScale.SHARE` or `QuantityScale.LOT`, then call
`to_canonical()`. Canonical observation fields accept only `ShareQuantity`;
passing an ambiguous raw `Decimal` is rejected. This applies to per-security
institutional gross/net flows, foreign holding share counts, all margin/short
quantities, and all SBL quantities including adjustment. PostgreSQL therefore
receives and hashes only canonical shares. Equivalent lot/share source
representations reuse the same business revision.

Market-level institutional summary values are a distinct source-published
aggregate and are not asserted to be per-security share quantities by this
contract.

Gross buy/sell quantities, holdings, balances, and limits are non-negative.
Net-flow values remain signed and are not silently recomputed from buy and
sell. SBL `adjustment` is also signed and remains source-faithful after unit
normalization. Ratios in the v1 tables are source-published percentages in the
inclusive range 0 through 100, with no implicit fraction-to-percent conversion.

Nullable fields express source-format capability: one source may omit a field
that another publishes. Every version must contain at least one domain value.
The raw artifact retains source columns and parser coordinates not represented
as canonical business fields.

## Temporal and evidence semantics

`trade_date` is the effective observation date, not publication time.
`published_at` is independent append-only publication evidence and cannot
precede the observation date in `Asia/Taipei`. `recorded_at` is trusted
DB-generated knowledge time. Unknown publication remains Market-PIT invisible.

System PIT uses DB-generated `ingested_at`. Backfilling a 2019 observation today
does not make it visible to a system cutoff in 2019. Market PIT uses both
`information_as_of` and `knowledge_as_of`, including evidence correction and
retraction under the shared resolver rules.

## Query and provenance contract

`InstitutionalFinancingService` resolves single records and inclusive histories
through the shared PIT resolver. Returned records contain exact source,
business-hash, publication-evidence, raw-artifact, and ingest-run provenance.
`observations` exposes all repeated fetches associated with one business
revision. PostgreSQL validates exact dataset/source lineage on both versions
and observation links, and association rows are append-only.

## Derived-data boundary

Daily trust/dealer net flows do not identify absolute holdings without a real
PIT-safe initial holding baseline. No such reliable baseline source is part of
the known v1 contract. Consequently, the legacy names `trust_held_shares`,
`trust_held_ratio`, `dealer_held_shares`, and `dealer_held_ratio` are not treated
as actual holdings. The reproducible legacy zero-origin calculation is defined
only as a cumulative-flow proxy:

```text
trust_cumulative_net_shares
trust_cumulative_net_ratio
dealer_cumulative_net_shares
dealer_cumulative_net_ratio
```

These belong to the later versioned canonical definition
`institutional_cumulative_flow:v1`. The ratio is cumulative net shares divided
by PIT-safe issued shares; neither value claims absolute ownership. A future
actual holding dataset requires a reliable observed baseline, its own PIT and
provenance contract, and a new derivation definition—it must not silently
change this proxy.

Margin usage recomputation, short-interest ratios, SBL pressure, and
`margin_pressure_score` likewise belong to the canonical-derived or
downstream-model boundary documented in the inventory. Canonical calculations
begin in Phase 9 and must consume PIT-resolved inputs.

shareholding concentration is specifically not computed from these source
tables or from raw/partial TDCC distributions. It must derive from a PIT-safe
sealed TDCC snapshot, retain that snapshot's profile/schema identity in input
lineage, and use a versioned derivation definition. In particular, the
`tdcc-opendata-v1` profile remains immutable after first use; a format change
requires a new profile code such as `tdcc-opendata-v2`.
