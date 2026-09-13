# ADR-0018: Taiwan corporate-action contract

Status: Accepted for PR #12

## Decision

Corporate-action versions preserve source-faithful legal and economic meaning.
Cash dividends, earnings stock dividends, capital-surplus stock dividends,
stock splits, reverse splits, rights issues, capital reductions, ex-dividend,
ex-right, and combined ex-right/ex-dividend events have distinct canonical
types. Split-style events use `old_shares` and `new_shares`.

The contract retains source component ratios, official reference-price values,
the original source event type, and structured source terms. All are business
revision content and therefore enter the PostgreSQL-generated business hash.
An announced event can be stored before its ex-date becomes known.

Legacy ambiguous `stock_dividend` and `rights` values remain representable for
unchanged existing rows but are not accepted by the new domain writer.

The downgrade to revision `4d2a6f8c1e30` runs a preflight before mutation. It
proceeds only if every stored revision is exactly representable by the old
schema; otherwise it raises SQLSTATE `P0001` and leaves Alembic head, schema,
events, and observation provenance unchanged.

## Consequences

Raw official OHLC remains immutable observed data. This PR does not infer an
action from price movement and does not implement ingestion, historical
backfill, adjustment factors, adjusted prices, or total-return calculations.
Those remain gated by later roadmap PRs.
