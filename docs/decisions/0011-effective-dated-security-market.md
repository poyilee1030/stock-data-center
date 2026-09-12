# ADR-0011: Effective-Dated Security Market Membership

Status: Accepted

Date: 2026-09-12

## Context

Taiwan securities can retain one stock code while transferring listing venue,
for example from TPEx to TWSE. Treating market as an immutable attribute of the
stable security row makes the transfer unrepresentable and causes historical
universe queries to apply a current venue to every past date.

## Decision

`security.security_code` is the stable identity and remains globally unique.
The `security` row contains no authoritative market membership.

`security_metadata_versions.market` is observed, source-specific,
effective-dated business state. It participates in the storage-generated
business hash and resolves through the same Market/System PIT and publication
evidence rules as name, industry, and listing dates.

`SecurityState.market` is read from the resolved metadata record. Historical
universe queries resolve metadata first and only then apply an optional market
filter. They must not filter an identity/current-state table by market.

One `security_id` therefore remains stable across venue transfers. Daily price
histories from old and new venue sources may both reference that identity while
remaining source-isolated.

## Consequences

- Market transfers do not create a new security identity.
- Historical venue membership is reproducible under PIT cutoffs.
- The normalized registration operation needs only `security_code`.
- Migrating existing data copies the former identity market into every existing
  metadata revision and recomputes its business hash to include market.
- Redis/cache impact is none because no cache exists in Phase 3.
