# Step 12 Acceptance Report

Status: IN REVIEW

Scope: Harden Taiwan Corporate-Action Contract

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| Explicit stock split and reverse split | PASS | `stock_split` and `reverse_split` require positive `old_shares`/`new_shares` with opposite DB-enforced directions. |
| Distinct 盈餘配股 and 資本公積配股 | PASS | Separate action types and `earnings_stock_ratio` / `capital_surplus_stock_ratio` fields; regression verifies distinct stored rows and hashes. |
| Explicit rights and capital reduction | PASS | `rights_issue` requires `rights_ratio`; capital reduction requires a decreasing old/new share pair plus a typed kind. Cash-refund reductions require positive per-share returned cash, while loss-offset kinds reject it. |
| Safe migration lifecycle | PASS | Representable legacy history round-trips. New unrepresentable history raises SQLSTATE `P0001` before mutation. |
| Impossible values rejected | PASS | Domain and PostgreSQL checks cover missing terms, zero/negative ratios, incomplete share pairs, invalid share direction, and date order. |
| Hash completeness | PASS | The storage trigger hashes canonical revision JSON; none of the new semantic fields are excluded. Regressions distinguish legal stock-dividend categories and changes to capital-reduction kind/returned cash. |
| Raw prices unchanged | PASS | Migration does not alter `daily_price_versions`; guarded-downgrade regression verifies raw OHLC values and hash byte-for-byte after failure. |
| Representative permanent regressions | PASS | Cash dividend, earnings/capital-surplus stock dividend, split, reverse split, rights issue, capital reduction, and combined ex-right/ex-dividend semantics are covered. |

## Migration preservation evidence

The populated downgrade regression creates a cash-refund capital-reduction revision and its
observation provenance, plus a raw daily OHLC revision. Attempting downgrade to
`4d2a6f8c1e30` returns `P0001`. Alembic remains at `7c9e2a4b6d81`; the schema,
event revision, observation row, and raw OHLC revision remain intact.

## Verification

Clean PostgreSQL database:

```text
239 passed, 3 skipped
```

The three skips are opt-in live official-endpoint tests from Step 11 and are
outside this schema/domain-only PR. Alembic metadata drift check passed. One
SQLAlchemy reflection warning is emitted for intentionally `NOT VALID`
constraints that preserve pre-PR legacy rows while enforcing all new writes.

## Scope exclusions confirmed

Step 12 adds no external-source adapter, historical corporate-action backfill,
price-jump inference, adjustment factor, adjusted price, total-return series,
technical indicator, trading-calendar behavior, or Redis/cache work.
