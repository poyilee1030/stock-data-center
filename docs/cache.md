# Optional Cache Contract

## Authority and placement

Caching is optional and is not implemented before the cache phases in the
roadmap. Both deployment modes are valid:

```env
CACHE_BACKEND=none
```

```env
CACHE_BACKEND=redis
REDIS_URL=redis://host:6379
```

PostgreSQL remains authoritative. Cache access sits in the application service
above the PIT resolver. `NullCache` and `RedisCache` implement one interface;
routes, repositories, and resolvers do not import Redis-specific behavior.

The hard equivalence invariant for every canonical request `Q` is:

```text
resolve(Q, NullCache)
== resolve(Q, Redis cold)
== resolve(Q, Redis warm)
== resolve(Q, Redis unavailable with fallback)
```

Equality includes business data, PIT context, source, response schema, and
provenance. Cache state may change latency only.

## Canonical identity

Before hashing, the service validates the request, resolves aliases and default
source policy, and creates a complete canonical object. It contains at least:

- cache contract version;
- response schema version;
- PIT resolver semantics version;
- dataset/endpoint identity;
- every typed business query parameter;
- resolved source;
- PIT mode;
- `information_as_of` and `knowledge_as_of` for market PIT; or
- `system_as_of` for system PIT.

Canonical JSON uses UTF-8, lexicographically sorted object keys, no insignificant
whitespace, explicit JSON `null` where the schema permits it, and schema-defined
array ordering. Timestamps are aware instants normalized to UTC and serialized
as fixed six-digit microsecond RFC 3339 values ending in `Z`. Enums use their
documented lowercase wire values. Numeric fields use their schema-defined
canonical decimal/string representation; binary floating-point rendering is
not accepted as an implicit contract.

The digest is `SHA256(canonical_json_bytes)`. A key has the form:

```text
stockdc:<cache-contract-version>:<dataset-or-endpoint>:<digest>
```

Dictionary insertion order, `repr`, `str(object)`, and raw caller input are not
canonical encodings. Omitting a PIT cutoff, source, business parameter, or
semantic version is a P0 correctness defect.

## Aliases and defaults

`latest` and `now` are not hashed as aliases. When supported, the service first
resolves them to explicit semantics and concrete cutoffs. A default/canonical
source is likewise resolved to the source identifier before key construction.
Intentionally dynamic current queries may use a shorter TTL, but TTL never
repairs an incomplete key.

## Payload

The cache stores a versioned encoding of the normalized, fully resolved public
response. A hit retains all metadata and provenance returned on a miss. The
payload is not a serialization of PostgreSQL table rows and does not expose a
Redis key through the public API.

## Failure behavior

On cache GET error, the service records a sanitized cache-error/bypass signal,
queries PostgreSQL through the resolver, and returns the correct result. On SET
error it records the failure and still returns the resolved result. Redis uses
short configured connection/socket timeouts and no long request-path retry.
Credentials and connection strings are never logged.

Redis persistence is not required. A dedicated cache may disable AOF and RDB;
eviction, restart, total key deletion, or unavailability must be safe.

## Evolution and migration

A semantic change to the cache contract, response schema, PIT resolution, or
source reconciliation bumps the applicable namespace/version before rollout.
A trusted migration that backdates temporal metadata must include a cache-impact
section and either bump the namespace or purge the precisely affected entries.

## Required later-phase verification

Every cacheable query family compares NullCache, Redis cold, Redis warm, and
Redis-unavailable fallback results. Tests also prove distinct identity for every
business parameter, source, PIT mode/cutoff, and semantic version. Cache metrics
distinguish hit, miss, error, and bypass.
