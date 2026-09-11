# ADR-0005: Optional Cache Architecture

- Status: Accepted
- Date: 2026-09-11

## Context

Repeated PIT resolution can create PostgreSQL read load, but caching must not
become a second source of truth or allow temporal results to leak across query
cutoffs.

## Decision

Redis is an optional, disposable query-result cache introduced only in the
roadmap's cache phases. PostgreSQL-backed resolution works with
`CACHE_BACKEND=none`, and application services depend on a cache abstraction
with `NullCache` and `RedisCache` implementations.

Caching occurs above PIT resolution and stores complete normalized responses.
Canonical keys include every semantic input and explicit contract/schema/
resolver versions. Redis failure is caught at the cache boundary and falls back
to PostgreSQL; cache SET failure never changes a successful response.

The normative key, payload, failure, and namespace rules are in
[Optional Cache Contract](../cache.md).

## Consequences

- Redis installation, credentials, persistence, and availability are not needed
  for migrations, ingestion, core PIT tests, or no-cache startup.
- Deleting all cached data is semantically safe.
- TTL and eviction are performance policies, not correctness mechanisms.
- Cache phases must prove cold/warm/failure equivalence against `NullCache`.
- A temporal backdating migration cannot ship without a cache-impact plan.
