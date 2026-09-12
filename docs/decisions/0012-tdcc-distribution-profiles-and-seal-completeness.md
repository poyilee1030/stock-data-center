# ADR-0012: TDCC distribution profiles and seal completeness

- Status: Accepted
- Date: 2026-09-12
- Clarifies: ADR-0003

## Context

ADR-0003 requires an aggregate to have its required children before it is
sealed. For TDCC snapshots the Phase 1 trigger checked only that at least one
distribution row existed. That let a partially parsed file (for example only
level 15) become an immutable, PIT-visible "complete" snapshot, which could
later feed wrong shareholding-concentration metrics.

The official TDCC distribution also has rows with different semantics:
levels 1-15 are holding ranges, level 16 is a difference adjustment whose share
count can be negative, and level 17 is the source-published total. Historical
files leave the adjustment holder count blank, while open-data forms may
write 0. The Phase 1 global `shares >= 0` check rejected valid adjustment rows.

## Decision

Every TDCC snapshot declares a `distribution_schema` profile.
`tdcc_distribution_schemas` and `tdcc_distribution_schema_buckets` register each
profile and its buckets, each with one role: `holding`, `adjustment`, or
`total`. A profile is frozen on first use: its buckets may be defined only while
no snapshot references it, and existing definitions can never be updated or
deleted. Changed bucket semantics require a new profile code, as a changed
formula requires a new derivation version. Because the aggregate hash contains
only the profile code, not its bucket rows, the code must name one fixed
contract.

`tdcc-opendata-v1` registers levels 1-15 as `holding`, 16 as `adjustment`, and
17 as `total`.

PostgreSQL enforces the profile:

- a bucket definition insert locks the profile row and is rejected (`55000`)
  once any snapshot references the profile; a snapshot insert's foreign-key
  lock on the same row serializes first use with definition changes;
- every distribution row must be a bucket of its snapshot's profile;
- `holding` and `total` rows require a holder count and non-negative shares and
  ownership percent;
- `adjustment` rows may carry signed shares and percent; their holder count is
  canonically `NULL`, and a source blank or 0 is stored as `NULL`, while any
  other value is rejected;
- a seal is rejected unless every bucket of the profile is present.

The canonical writer validates the same profile before creating a draft, so
the normal ingestion path fails without writing. The profile is part of the
aggregate business hash.

## Consequences

- Sealed means complete for the declared profile, for every write path.
- A signed adjustment is stored and hashed verbatim; it is never clamped or
  dropped.
- Historical blank and open-data 0 adjustment holder counts produce the same
  business content, so the same snapshot from either form is not a false
  revision.
- Arithmetic identities between holding, adjustment, and total rows are not
  enforced. The total is retained as a source-published value; the
  canonical-derived phase decides how to use it.
- A third-party or historical layout that differs from the official one needs
  its own registered profile rather than a relaxed global rule.
- The Phase 6 migration assigns existing snapshots `tdcc-opendata-v1` and aborts
  instead of guessing if an existing row does not fit it or a sealed snapshot is
  incomplete. It canonicalizes existing adjustment holder counts of 0 to `NULL`
  and rehashes sealed snapshots. There is no cache impact because no cache
  exists; no PIT timestamp changes.
