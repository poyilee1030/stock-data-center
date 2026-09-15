# Market Indices, Corporate Actions, and Official Valuation

## Phase 8 scope

Phase 8 implements cache-free normalized writes and PIT-safe reads for the
observed `market_index`, `market_index_metadata`, `corporate_action`, and
`official_valuation` datasets.
PostgreSQL remains authoritative. This phase adds no Redis/cache behavior,
derived calculator, source fetch adapter, or public HTTP route.

## Logical keys and revisions

| Dataset | Logical key excluding source | Business revision content |
| --- | --- | --- |
| `market_index` | `(market_index_id, trade_date)` | source-published index OHLC, change, and trade value |
| `market_index_metadata` | `(market_index_id, effective_from)` | market, official name, and effective end |
| `corporate_action` | `(event_id)` | action type, announcement/ex/record/payment dates, amounts, ratios, and source terms |
| `official_valuation` | `(security_id, trade_date)` | source-published PE, PB, dividend yield, dividend year/per-share value, and report period |

`source` keeps histories independent. Changed business content creates an
immutable revision. An equivalent refetch reuses that revision and appends its
raw artifact/ingest-run observation. Logical keys, timestamps, source, and
provenance do not enter `business_content_hash`; PostgreSQL generates the hash
and trusted `ingested_at`.

`market_index.index_code` is the only stable index identity. Market and name
are source-observed, effective-dated metadata. A rename therefore creates a
metadata revision and never mutates or conflicts with the stable index.

Every corporate action first registers a stable event using
`(security_id, source, source_event_key)`. A source document/event identifier
is preferred. If a source lacks one, its adapter must document and test a
source-specific synthetic identity that survives corrections; it must never
silently use `action_type + ex_date` as a universal fallback. `action_type`,
all dates, amounts, ratios, and terms are mutable revision content and enter
the business hash. Two real events can therefore share type and ex-date.

## Canonical units

Monetary values use TWD major units (dollars). An adapter must construct
`SourceTwdAmount` with explicit `MAJOR`, `THOUSAND`, or `MILLION` scale and
normalize it to `TwdAmount` before constructing a canonical observation.
Ambiguous raw decimals are rejected for monetary fields, so equivalent source
representations store and hash identically.

Index levels and changes are points; `change_percent` is percentage points.
Official PE and PB are multiples, and `dividend_yield` is percentage points.
Corporate `earnings_stock_ratio`, `capital_surplus_stock_ratio`,
`free_share_ratio`, and `rights_ratio` are shares per share: `0.1` means 0.1
new/right shares per existing share, not an implicit percent conversion.

## Taiwan corporate-action contract

New observations use explicit action types: `cash_dividend`,
`earnings_stock_dividend`, `capital_surplus_stock_dividend`, `stock_split`,
`reverse_split`, `rights_issue`, `capital_reduction`, `ex_dividend`, `ex_right`,
and `ex_right_dividend`. The predecessor's ambiguous `stock_dividend` and
`rights` values remain readable only for unchanged legacy history; writers
reject them for new observations.

Stock dividends retain their legal source category and component ratios.
Split-style events use explicit `old_shares` and `new_shares`; a stock split
increases shares, while a reverse split and capital reduction decrease them.
These categories never collapse merely because some future adjustment formula
could have similar arithmetic.

Capital reductions additionally require a typed `capital_reduction_kind`:
`cash_refund`, `loss_offset`, `loss_offset_with_cash_increase`, or `other`.
A cash-refund reduction requires a positive canonical-TWD
`capital_reduction_cash_return_per_share`; loss-offset kinds do not return cash.
The combined loss-offset/cash-increase kind may retain the source's applicable
subscription price and allocation ratio without being reclassified as an
ordinary rights issue.

Where the source supplies them, revisions retain `close_before`,
`official_reference_price`, `official_rights_dividend_value`, the original
`source_event_type`, and structured `source_terms`. These fields and all other
economic terms enter the storage-generated business hash. An announced event
may temporarily have no `ex_date`; effective-date history queries exclude it
until an ex-date revision is available.

Corporate actions are observed evidence only. They are never inferred from a
price jump. Official daily OHLC remains immutable source truth and is neither
rewritten nor adjusted by this contract. Adjustment factors, adjusted prices,
total-return series, real-source ingestion, and historical backfill are outside
Step 12.

## Temporal semantics

For index and official valuation observations, `trade_date` is the source's
effective observation date. Publication evidence cannot claim a local
`Asia/Taipei` date earlier than that date.

For corporate actions, `announcement_date` is the source-stated announcement
date and `ex_date` is the effective event date. They are deliberately distinct.
An action may become Market-PIT visible after reliable announcement publication
and before its future effective date. When an announcement date is known,
publication evidence cannot predate it; unknown announcement/publication is
not invented. `record_date` and `payment_date` retain their own source meaning.

Market PIT requires authoritative evidence with both
`published_at <= information_as_of` and `recorded_at <= knowledge_as_of`.
Unknown publication is Market-PIT invisible. System PIT uses DB-generated
`ingested_at`, so a historical backfill does not appear in an earlier system
snapshot merely because its business date is old.

## Official versus computed valuation

`official_valuation_versions` contains only values published by the selected
source. It never stores a Data Center-computed PE, PB, yield, percentile,
TTM EPS, ROE, or other derived value as if it were observed.

Computed valuation belongs to a versioned canonical derived definition such as
`valuation_metrics:v1` in Phase 10. It must consume PIT-safe inputs, preserve
input lineage/fingerprint and PIT context, and remain distinguishable in API
metadata from source-published valuation.

## Query and provenance

`MarketReferenceService` resolves corporate actions primarily by stable
`event_id`; security/type/date history fields are filters over PIT-resolved
event revisions and do not redefine identity. It also provides single-record
and inclusive-history queries
through the shared source policy and PIT resolver. Resolved records retain the
selected source, business hash, evidence, raw artifact, and ingest run.
`observations()` returns every immutable fetch lineage associated with one
revision. PostgreSQL rejects cross-dataset/source links and all mutation of
versions, observation links, and the stable market-index identity.
