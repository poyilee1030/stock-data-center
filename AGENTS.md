# AGENTS.md

## Purpose

This repository implements `stock-data-center`.

It owns:

```text
source ingestion
raw provenance
PostgreSQL history
business revisions
publication evidence
PIT visibility
public API
optional query-result caching
```

It does NOT own:

```text
EPS model training
stock-selection model training
portfolio research
strategy ranking logic
```

The highest-priority requirement is:

> Historical correctness must not depend on current database state, caller discipline, or cache availability.

---

# 1. Canonical Roadmap

The canonical roadmap is:

```text
ROADMAP.md
```

Do not maintain competing version-suffixed roadmaps in the repository root.

Work one phase at a time.

Do not begin later phases merely because the code is convenient to add.

At the end of each phase, produce:

```text
docs/phase_reports/phase-N-acceptance-report.md
```

Each required criterion must be marked PASS/FAIL with concrete evidence.

If a required acceptance criterion fails, stop.

---

# 2. Fixed Stack

Unless changed through an ADR and ROADMAP update:

```text
Python 3.12+
FastAPI
Pydantic 2.x
SQLAlchemy 2.x
Alembic
PostgreSQL 18+
pytest
httpx
Docker / Docker Compose
Redis optional
```

Use:

```text
postgres:18
```

Do not use `postgres:latest`.

---

# 3. Architectural Ownership

`stock-data-center` is the only component that decides:

```text
what was publicly knowable
what the Data Center had ingested
which revision was visible
which publication evidence was authoritative
which source policy applies
```

Downstream consumers receive resolved data.

Do not move PIT logic into downstream repos.

---

# 4. PostgreSQL Is Authoritative

PostgreSQL is the source of truth.

Redis is never authoritative.

The following operation must remain semantically safe:

```text
delete all Redis keys
```

After deletion, correct results must still be returned from PostgreSQL-backed resolution.

---

# 5. Redis Is Optional

Both configurations are valid:

```env
CACHE_BACKEND=none
```

and:

```env
CACHE_BACKEND=redis
REDIS_URL=redis://host:6379
```

Do not require Redis to:

```text
run migrations
start in CACHE_BACKEND=none
ingest data
resolve PIT queries
pass core correctness tests
```

---

# 6. Cache Equivalence Is a Hard Invariant

For a canonical request Q:

```text
resolve(Q, NullCache)
==
resolve(Q, Redis cold cache)
==
resolve(Q, Redis warm cache)
==
resolve(Q, Redis unavailable with fallback)
```

Differences in performance are allowed.

Differences in business/PIT output are P0 bugs.

---

# 7. Redis Failure Must Degrade Performance Only

If Redis GET fails:

```text
log/metric
bypass cache
query PostgreSQL
return correct result
```

If Redis SET fails:

```text
log/metric
still return the PostgreSQL result
```

Never fail a correct Data Center query solely because cache infrastructure is unavailable.

Use short Redis connection/socket timeouts.

Do not implement long blocking retries in the request path.

---

# 8. Cache Layer Placement

Cache resolved Data Center responses above the PIT resolver.

Correct:

```text
API
 -> service
 -> cache
 -> PIT resolver on miss
 -> PostgreSQL
```

Avoid:

```text
API route containing Redis code
SQL repository containing Redis code
PIT resolver changing semantics based on cache
```

Use a cache abstraction.

---

# 9. Cache Backends

Provide at least:

```text
NullCache
RedisCache
```

Application services should depend on the cache interface, not Redis directly.

Suggested package:

```text
src/stock_data_center/cache/
```

Do not create a separate `stock-redis-service` repository.

Redis is infrastructure of this repository.

---

# 10. Cache Key Correctness

Cache keys are part of PIT correctness.

Canonical cache identity must include every semantically relevant query field.

At minimum:

```text
cache contract version
response schema version
resolver semantics version
dataset/endpoint
business query parameters
source
pit mode
```

For market PIT:

```text
information_as_of
knowledge_as_of
```

For system PIT:

```text
system_as_of
```

Omitting any PIT cutoff from a cached query identity is a P0 bug.

Use canonical serialization before hashing.

Do not depend on Python dictionary insertion order or unstable object string representations.

---

# 11. Cache Namespace Evolution

Semantic changes must not silently reuse old cached results.

Version/cache namespace changes are required when relevant behavior changes:

```text
cache contract
response schema
PIT resolver semantics
source reconciliation semantics
```

Prefer namespace/version bumps over broad ad hoc deletion.

Trusted migrations that backdate temporal metadata must explicitly purge or namespace-bump affected cache results.

---

# 12. Cache Content

Prefer caching normalized PIT-resolved Data Center results.

Do not make PostgreSQL row/table layout part of the Redis contract.

Good cache candidates:

```text
historical universe
price windows
resolved revenue
resolved financials
resolved TDCC
large repeated historical query results
```

The objective is to avoid repeated DB/PIT work.

---

# 13. Redis Persistence

Treat Redis as disposable RAM cache.

Default dedicated-cache-host recommendation:

```text
AOF off
RDB off
```

Do not rely on Redis persistence for:

```text
audit
reproducibility
business revision history
publication evidence
ingestion history
```

Those belong in PostgreSQL/raw provenance storage.

---

# 14. PIT Time Types

Use explicit temporal types/fields.

Market PIT:

```text
information_as_of
knowledge_as_of
```

System PIT:

```text
system_as_of
```

Do not collapse these into ambiguous `date`/`as_of` variables inside domain logic.

---

# 15. Market PIT Rule

Market PIT answers:

> What was publicly knowable by `information_as_of`, using publication evidence the Data Center had recorded by `knowledge_as_of`?

Conceptually:

```text
published_at <= information_as_of
AND
recorded_at <= knowledge_as_of
```

Then resolve authoritative evidence deterministically.

Do not use `ingested_at` as a substitute for publication time.

---

# 16. System PIT Rule

System PIT answers:

> What complete business data had this Data Center actually ingested by `system_as_of`?

Single-row version:

```text
ingested_at <= system_as_of
```

Complex aggregate:

```text
seal.ingested_at <= system_as_of
```

Do not use historical `published_at` to fake system ingestion history.

---

# 17. Publication Evidence Rule

Business content and publication evidence are separate concepts.

Do not create a business revision merely because:

```text
published_at became known
publication evidence quality improved
publication time was corrected
publication evidence was retracted
```

Publication evidence is append-only.

Use supersession/retraction records.

Never UPDATE old evidence rows.

---

# 18. Evidence Times

Publication evidence must preserve at least:

```text
published_at
recorded_at
```

`published_at`:

```text
when the market could know
```

`recorded_at`:

```text
when Data Center learned/recorded that evidence
```

Normal `recorded_at` must be trusted storage/DB generated.

Callers must not arbitrarily backdate it.

---

# 19. Authoritative Evidence Resolution

Evidence selection must be deterministic and documented.

It must account for:

```text
knowledge cutoff
source capability
evidence type/quality
supersession
retraction
deterministic tie breaker
```

Do not let API handlers improvise evidence selection.

---

# 20. Immutable Aggregate Rule

Complex parent+children datasets are immutable aggregates.

Only seal makes them PIT-visible.

A committed draft may exist.

Resolvers must ignore drafts.

After seal, DB must reject:

```text
parent UPDATE
parent DELETE
child INSERT
child UPDATE
child DELETE
```

Do not rely only on repository call order.

Enforce immutability in PostgreSQL.

---

# 21. Seal Table Rule

Prefer dataset-specific seal tables with real foreign keys.

Avoid loose polymorphic references such as:

```text
aggregate_type + version_id
```

when they cannot be protected by real referential integrity.

---

# 22. Ingestion-Time Rule

Normal callers must not provide authoritative historical ingestion timestamps.

For complex aggregates, the system-visible time is the server/trusted seal time.

Historical timestamp preservation is allowed only through a dedicated trusted migration path with provenance and tests.

---

# 23. Business Hash Rule

`business_content_hash` is storage-generated from canonical business values.

Caller-provided hashes are not authoritative.

Do not include:

```text
published_at
recorded_at
raw artifact ID
ingest run ID
fetch timestamp
seal time
```

in business content identity.

---

# 24. Hash Separation

Keep separate:

```text
business_content_hash
publication_evidence_hash
raw_artifact_hash
```

Do not reuse a generic `content_hash` to mean different identities.

---

# 25. Duplicate Fetch Rule

A repeated fetch with unchanged business content:

```text
must not create a fake business revision
```

but:

```text
must preserve raw artifact / ingest provenance
```

Design lineage accordingly.

---

# 26. Provenance Integrity

If a record stores:

```text
raw_artifact_id
ingest_run_id
```

the DB should enforce that the artifact belongs to that ingest run.

Do not allow mismatched lineage IDs.

---

# 27. Raw Artifact Rule

Raw artifacts are immutable and content-addressed.

Do not overwrite existing content-addressed paths with different bytes.

Raw artifacts are evidence/provenance, not a cache.

---

# 28. Source-Level PIT Capability

PIT capability belongs to:

```text
(dataset_code, source)
```

not only `dataset_code`.

Do not let verified evidence rules for one source authorize another source.

---

# 29. Cross-Source Rule

Preserve independent source histories.

Do not silently:

```text
average
merge
overwrite
choose latest ingest
```

If a canonical source is configured, it must be explicit.

New reconciliation policies require ADR + tests.

---

# 30. Unknown Publication Rule

If:

```text
published_at IS NULL
```

the row/version is market-PIT invisible by default.

It may still be system-PIT visible if legitimately ingested by the system cutoff.

Do not invent a historical publication timestamp.

---

# 31. Backfill Rule

If reliable retained evidence proves a source was public earlier:

```text
published_at = proven historical publication time
recorded_at  = actual time Data Center records the evidence
ingested_at  = actual system ingestion/seal time
```

If historical publication time cannot be proved:

```text
published_at = NULL
```

Do not use current wall-clock time as fake historical publication metadata.

---

# 32. XBRL Context Rule

Financial facts require a non-null canonical context identity.

`context_hash` should represent relevant context including:

```text
entity
period
dimensions
typed dimensions
scenario/segment as applicable
```

Preserve namespace-aware concept identity.

Do not rely on nullable date columns as the fact uniqueness key.

---

# 33. Timezone Rule

Use timezone-aware timestamps only.

Market interpretation:

```text
Asia/Taipei
```

Internal PostgreSQL storage:

```text
TIMESTAMPTZ
```

Never compare naive and aware datetimes silently.

---

# 34. API Boundary Rule

Public clients may know:

```text
dataset concepts
PIT context
source selection
provenance
```

They must not know:

```text
PostgreSQL table names
Redis key formats
Alembic schema internals
```

Do not leak infrastructure details into downstream model repos.

---

# 35. Downstream Credential Rule

`stock-eps-model` and `stock-model-selection` must not require:

```text
PostgreSQL credentials
Redis credentials
```

They use only Data Center API/SDK credentials/configuration.

---

# 36. Ingestion and Query Separation

Write path:

```text
source -> raw artifact -> parser -> normalization -> PostgreSQL
```

Read path:

```text
client -> API -> optional cache -> PIT resolver -> PostgreSQL
```

Do not route ingestion correctness through Redis.

---

# 37. No Cache-Only Writes

Never write business/evidence data only to Redis.

All durable writes go through authoritative storage/provenance paths.

---

# 38. Query Alias Rule

Avoid ambiguous cache behavior for:

```text
latest
now
```

Resolve aliases to explicit semantics/timestamps before canonical cache identity whenever possible.

Use shorter TTLs for intentionally dynamic current queries.

---

# 39. Cache TTL Rule

TTL is a performance policy, not a correctness mechanism.

Never rely on "the wrong key will expire soon" as a substitute for correct cache identity.

---

# 40. Cache Serialization Rule

Cache encoding must be deterministic and versioned.

Cached payloads must retain enough response metadata/provenance to be semantically identical to an uncached response.

Do not return a reduced-information cached response.

---

# 41. Observability Rule

Expose/log enough information to distinguish:

```text
cache hit
cache miss
cache error
cache bypass
PostgreSQL query
resolver latency
response size
```

Do not log sensitive credentials or raw connection strings.

---

# 42. SSD Optimization Rule

Redis is intended primarily as a read/query workload optimization.

Do not claim SSD benefit without measurement.

Use database/host metrics such as:

```text
PostgreSQL blks_read / blks_hit
query timings
iostat
cache hit/miss metrics
```

Redis does not eliminate PostgreSQL write I/O from:

```text
ingestion
WAL
indexes
checkpoints
vacuum
```

---

# 43. Testing Cache Correctness

Every cacheable query family must test at least:

```text
NullCache result
Redis cold result
Redis warm result
Redis unavailable fallback result
```

All must match semantically.

Also test that changing each relevant PIT/source parameter produces a distinct cache identity.

---

# 44. PIT Regression Tests Are Permanent

Once a PIT/leakage bug is found, add a regression test.

Permanent categories include:

```text
unknown publication
late publication evidence
knowledge cutoff
system late ingestion
backfill
evidence correction
evidence retraction
sealed aggregate
source capability isolation
XBRL dimensions
cache PIT identity
cache failure fallback
```

---

# 45. Phase Scope Discipline

When implementing one ROADMAP phase:

- implement only that phase
- do not weaken existing acceptance criteria
- do not silently change PIT semantics
- record architectural changes in ADRs
- keep phase reports current
- avoid unrelated refactors

If a later-phase optimization is needed for correctness, document why.

---

# 46. Phase 1 Special Rule

Phase 1 correctness work takes priority over cache work.

Do not introduce Redis into Phase 1 schema implementation.

First make PostgreSQL/PIT semantics correct with cache disabled.

---

# 47. Cache Phase Special Rule

When adding Redis, do not modify expected resolver output to improve cacheability.

Adapt caching to the resolver contract, not the resolver contract to Redis.

---

# 48. Migration Rule

A migration that changes historical temporal meaning must include a cache-impact section.

If it can change prior cached query results:

```text
bump namespace
or
purge affected cache entries
```

before/with rollout.

---

# 49. Redis Configuration Rule

Redis-specific configuration belongs in deployment/configuration files.

Do not hardcode:

```text
host IP
port
memory size
password
TTL
```

inside domain logic.

---

# 50. Security Rule

If Redis is remote:

- do not expose it publicly
- restrict network access to trusted hosts
- use authentication/TLS where the network environment requires it
- never embed credentials in source control

Redis is infrastructure, not a public API.

---

# 51. Priority Order

When tradeoffs exist, prioritize:

```text
1. PIT correctness
2. historical auditability
3. provenance integrity
4. deterministic behavior
5. source isolation
6. cache/result equivalence
7. maintainability
8. performance
9. SSD/read reduction
10. convenience
```

Never trade the first six for cache performance.

---

# 52. Phase Completion Checklist

Before declaring any phase complete:

```text
tests pass
docs updated
acceptance criteria evaluated
PIT semantics preserved
provenance preserved
no source-capability leakage
no direct downstream DB/cache dependency introduced
```

For cache phases additionally:

```text
CACHE_BACKEND=none passes
Redis cold/warm equivalence passes
Redis failure fallback passes
```

Then publish the phase acceptance report.

---

# 53. Core Boundary

Remember the repository contract:

```text
stock-data-center
owns:
    source data
    temporal visibility
    publication evidence
    ingest history
    revisions
    provenance
    PostgreSQL
    optional cache
    API

stock-eps-model
owns:
    EPS features
    EPS labels
    EPS training
    EPS prediction

stock-model-selection
owns:
    selection features
    forward-return labels
    selection training
    ranking
    backtesting
```

Redis must remain an optional implementation detail of `stock-data-center`.

Enabling, disabling, restarting, or losing Redis may affect performance, but must never affect PIT correctness or returned data.
