# stock-data-center ROADMAP

## 1. Project Goal

`stock-data-center` is the single source of truth for historical Taiwan stock data used by downstream research and ML systems.

Its primary responsibility is not merely to store values. It must answer:

> What data was valid / publicly knowable / actually ingested at a given historical time?

The system must provide PIT-safe (point-in-time-safe), revision-aware, provenance-preserving access to stock data.

Primary downstream consumers:

```text
stock-eps-model
stock-model-selection
```

Downstream systems must never query the Data Center database directly.

---

# 2. High-Level Architecture

```text
External Sources
TWSE / TPEx / MOPS / TDCC / other approved sources
                  |
                  v
        +----------------------+
        |    Ingestion Layer   |
        | adapters / parsers   |
        | normalization        |
        +----------+-----------+
                   |
          raw artifacts + metadata
                   |
                   v
        +----------------------+
        |     PostgreSQL 18    |
        |    Source of Truth   |
        |                      |
        | version history      |
        | publication evidence |
        | ingest history       |
        | PIT metadata         |
        +----------+-----------+
                   ^
                   |
             cache miss only
                   |
        +----------+-----------+
        |   PIT Resolver       |
        | market / system PIT  |
        +----------+-----------+
                   ^
                   |
        +----------+-----------+
        | Optional Cache Layer |
        | NullCache / Redis    |
        +----------+-----------+
                   ^
                   |
        +----------+-----------+
        | FastAPI / Public API |
        +----------+-----------+
                   ^
             HTTP / SDK
                   |
          +--------+---------+
          |                  |
          v                  v
 stock-eps-model    stock-model-selection
```

Redis is optional infrastructure.

PostgreSQL remains authoritative in every deployment mode.

---

# 3. Deployment Modes

Both modes are first-class supported deployments.

## 3.1 Minimal Mode

```text
CACHE_BACKEND=none

Client
  |
  v
stock-data-center
  |
  v
PostgreSQL
```

No Redis installation is required.

## 3.2 Cached Mode

```text
CACHE_BACKEND=redis
REDIS_URL=redis://<redis-host>:6379

Client
  |
  v
stock-data-center
  |
  v
Redis query-result cache
  |
 miss
  v
PostgreSQL
```

Redis may run on a separate high-memory machine.

Recommended initial Redis deployment for a dedicated 64 GB RAM machine:

```text
RAM-only cache
maxmemory ≈ 40–48 GB
eviction policy: allkeys-lfu
AOF: off
RDB persistence: off
```

Exact resource settings are operational configuration, not application semantics.

---

# 4. Core Architectural Invariants

## Invariant A — PostgreSQL is the source of truth

Redis is never authoritative.

Deleting the entire Redis dataset must not change correctness.

## Invariant B — Redis is optional

The application must work correctly with:

```text
CACHE_BACKEND=none
```

## Invariant C — Cache affects performance only

For the same canonical query:

```text
result(cache=none) == result(cache=redis)
```

Redis availability, failure, eviction, restart, or absence must never change the returned PIT result.

## Invariant D — Downstream systems never access PostgreSQL or Redis directly

Only the Data Center API / SDK is public.

## Invariant E — PIT semantics are enforced by Data Center

Downstream code must not recreate publication-time, revision, or ingestion-time rules.

## Invariant F — Current DB state is not historical truth

Historical visibility is determined from temporal metadata and evidence, never from "row exists today".

---

# 5. Fixed Technology Stack

Unless changed through an ADR and ROADMAP amendment:

```text
Python                 3.12+
FastAPI
Pydantic               2.x
SQLAlchemy             2.x
Alembic
PostgreSQL             18+
pytest
httpx
Docker / Docker Compose
Redis                  optional
redis-py               optional runtime dependency
```

Docker PostgreSQL image:

```text
postgres:18
```

Do not use `postgres:latest`.

Raw artifact storage v1:

```text
local filesystem
data/raw/
```

Access must be behind an abstraction so S3 / MinIO / NAS can be added later.

---

# 6. Time Model

All externally meaningful timestamps are timezone-aware.

Source-market timezone:

```text
Asia/Taipei
```

Internally prefer UTC instants.

PostgreSQL:

```text
TIMESTAMPTZ
```

SQLAlchemy:

```python
DateTime(timezone=True)
```

Never silently mix naive and timezone-aware datetimes.

---

# 7. PIT Semantics

The system supports two distinct PIT questions.

## 7.1 Market PIT

Question:

> What was publicly knowable at market-information time T, using only publication evidence known to the Data Center by knowledge time K?

Market PIT uses two clocks:

```text
information_as_of
knowledge_as_of
```

Core semantics:

```text
published_at <= information_as_of

AND

publication_evidence.recorded_at <= knowledge_as_of
```

Then resolve the authoritative evidence available under that knowledge cutoff.

This permits two valid reconstruction modes.

### Historically reproducible reconstruction

```text
information_as_of = historical market cutoff
knowledge_as_of   = historical Data Center knowledge cutoff
```

### Current-best market reconstruction

```text
information_as_of = historical market cutoff
knowledge_as_of   = current explicit timestamp
```

The second may improve as new historical publication evidence is discovered.

## 7.2 System PIT

Question:

> What had this Data Center actually and completely ingested by time T?

System PIT uses:

```text
system_as_of
```

For a single-row immutable version:

```text
ingested_at <= system_as_of
```

For a parent+children aggregate:

```text
seal.ingested_at <= system_as_of
```

Only sealed aggregates are visible.

---

# 8. Publication Evidence Semantics

Business content and publication evidence are separate version chains.

Example:

```text
business version A
    revenue = 100

publication evidence #1
    published_at = NULL
    recorded_at = T1
    type = unknown

publication evidence #2
    published_at = 2026-07-10
    recorded_at = T2
    type = official
    supersedes #1
```

The discovery of better publication evidence must not create a fake business revision.

Publication evidence is append-only.

Correction:

```text
new evidence supersedes old evidence
```

Retraction:

```text
new retraction evidence supersedes prior evidence
```

Never UPDATE historical evidence rows.

---

# 9. Immutable Aggregate Semantics

Complex datasets such as XBRL filings and TDCC snapshots are immutable aggregates.

Examples:

```text
financial_filing_versions
  + financial_facts
  + quarterly_financial_summary

tdcc_snapshot_versions
  + tdcc_distribution
```

Lifecycle:

```text
draft
  |
  v
children written / validated
  |
  v
seal
  |
  v
visible + immutable
```

Hard rule:

> Only sealing makes a complex aggregate visible.

A committed draft may exist.

It must remain invisible to PIT resolvers.

After sealing, DB constraints/triggers must reject:

```text
parent UPDATE
parent DELETE
child INSERT
child UPDATE
child DELETE
```

Prefer dataset-specific seal tables with real foreign keys rather than loose polymorphic `(aggregate_type, version_id)` references.

Example:

```text
financial_filing_seals
tdcc_snapshot_seals
```

---

# 10. Ingestion-Time Semantics

Normal callers must not provide authoritative historical `ingested_at`.

For single-row immutable versions, system ingestion time should be generated by trusted storage / DB logic when the complete version is inserted.

For complex aggregates, authoritative system visibility time is generated when the aggregate is sealed.

Historical migration requiring preserved timestamps must use a separate trusted migration path with:

```text
explicit provenance
restricted code path
tests
audit documentation
```

---

# 11. Hash Boundaries

Do not use one ambiguous `content_hash`.

Maintain separate identities.

## 11.1 `business_content_hash`

Contains canonical business values only.

Must not include:

```text
published_at
recorded_at
raw_artifact_id
ingest_run_id
fetch time
seal time
```

## 11.2 `publication_evidence_hash`

Represents canonical publication evidence.

## 11.3 `raw_artifact_hash`

```text
SHA256(raw bytes)
```

Repeated fetches with identical business content must preserve ingest/raw lineage without creating false business revisions.

---

# 12. Source-Level Capability

PIT capability belongs to a dataset+source combination, not merely a dataset.

Use concepts such as:

```text
dataset_catalog
dataset_sources
```

`dataset_catalog` owns schema/domain metadata.

`dataset_sources` owns:

```text
dataset_code
source
supports_market_pit
supports_system_pit
publication_time_quality
evidence_status
is_canonical
```

Do not allow one source's verified publication semantics to leak into another source.

---

# 13. Cross-Source Policy

Version 1 preserves source histories separately.

Do not silently:

```text
average
merge
overwrite
pick the latest-ingested source
```

If a canonical source exists, configure it explicitly.

If no canonical-source policy exists and multiple sources are possible, require the caller to select a source or return clearly separated source results.

Any reconciliation policy requires an ADR and tests.

---

# 14. Raw Artifact and Provenance Rules

Every normalized version/evidence record must be traceable to ingestion provenance where applicable.

Core concepts:

```text
ingest_runs
raw_artifacts
normalized business versions
publication evidence
```

Where both `raw_artifact_id` and `ingest_run_id` are stored, use DB constraints/composite foreign keys to ensure the artifact truly belongs to that run.

Raw artifacts are content-addressed and immutable.

Do not overwrite an existing raw artifact with different bytes.

---

# 15. XBRL Context Identity

XBRL fact identity must not rely only on concept + dates.

Use a non-null canonical:

```text
context_hash
```

Canonical context should represent, as applicable:

```text
entity
period type
instant / start / end
explicit dimensions
typed dimensions
scenario
segment
```

Dimension collections must be canonically ordered.

Prefer full concept QName / namespace-aware identity.

A robust fact identity should include at least:

```text
filing version
concept QName
context_hash
unit identity
```

---

# 16. Optional Cache Architecture

The cache sits above the PIT resolver.

```text
API
 |
 v
Application Service
 |
 v
Cache Backend
 |       |
 HIT    MISS
 |       |
 |       v
 |   PIT Resolver
 |       |
 |       v
 |   PostgreSQL
 |       |
 |    normalized
 |     result
 |       |
 +<-- cache SET
 |
 v
response
```

Do not put Redis-specific code inside:

```text
API routes
SQL repositories
PIT resolver logic
```

Use a cache abstraction.

Suggested package:

```text
src/stock_data_center/cache/
    base.py
    null_cache.py
    redis_cache.py
    keys.py
    codec.py
    policy.py
```

Suggested interface:

```python
class Cache:
    def get(self, key: str): ...
    def set(self, key: str, value, ttl_seconds: int): ...
```

Implementations:

```text
NullCache
RedisCache
```

---

# 17. Cache Granularity

Prefer caching normalized, PIT-resolved query results rather than individual PostgreSQL rows.

Good cache targets:

```text
historical universe result
resolved financial query
resolved revenue query
resolved TDCC snapshot query
price-window query
large cohort-level Data Center response
```

The goal is to avoid repeating:

```text
disk/page reads
SQL scans
joins
revision resolution
publication evidence resolution
normalization
```

Do not make PostgreSQL table-row layout part of the Redis public contract.

---

# 18. PIT-Aware Cache Keys

A cache key is part of correctness.

A cache key must be derived from a canonical request.

At minimum include all semantically relevant fields:

```text
cache contract version
response schema version
resolver semantics version
dataset / endpoint
all business query parameters
source
pit mode

market PIT:
    information_as_of
    knowledge_as_of

system PIT:
    system_as_of
```

Example canonical payload:

```json
{
  "cache_contract_version": "v1",
  "response_schema_version": "v1",
  "resolver_version": "v1",
  "dataset": "monthly_revenue",
  "security_id": "2330",
  "period": "2026-06",
  "source": "mops",
  "pit_mode": "market",
  "information_as_of": "2026-07-10T15:59:59Z",
  "knowledge_as_of": "2026-07-10T15:59:59Z"
}
```

Then:

```text
SHA256(canonical_json)
```

Example key:

```text
stockdc:cache-v1:monthly_revenue:<digest>
```

Omitting PIT cutoffs from the cache identity is a P0 correctness bug.

---

# 19. Cache Policy

Version 1 may use simple TTL-based caching.

Suggested starting policy:

```text
explicit historical system PIT     long TTL
explicit historical market PIT     long/moderate TTL
current/latest aliases              short TTL
security metadata                   moderate TTL
```

Important:

Before constructing a cache key, aliases such as:

```text
now
latest
```

should be resolved to explicit canonical timestamps/policies when possible.

Exact TTL values are configuration.

Do not encode correctness in TTL alone.

---

# 20. Cache Invalidation / Namespace Rules

Prefer immutable-key evolution over broad manual deletion.

Use versions such as:

```text
CACHE_CONTRACT_VERSION
RESPONSE_SCHEMA_VERSION
PIT_RESOLVER_VERSION
```

When semantics change, bump the relevant namespace/version.

Trusted migrations that intentionally backdate temporal metadata can invalidate previously cached historical results. Such migrations must therefore:

```text
purge affected cache namespace/range
or
bump resolver/cache namespace
```

This requirement must be documented in the migration plan.

---

# 21. Redis Failure Semantics

Redis failure must degrade performance only.

If:

```text
CACHE_BACKEND=redis
```

but Redis is unavailable:

```text
cache GET error
  |
  v
log / metric
  |
  v
bypass cache
  |
  v
PostgreSQL
```

Cache SET failure must not fail a correct Data Center response.

Use short network timeouts.

A later phase may add a circuit breaker / temporary unhealthy state to prevent repeated connection delays.

---

# 22. Redis Persistence

Redis is a disposable cache.

Default recommendation for the dedicated cache host:

```text
appendonly no
save ""
```

Redis restart may empty the cache.

The Data Center must recover automatically through cache misses.

Do not depend on Redis persistence for correctness or audit history.

---

# 23. Phase 0 — Freeze Contracts and ADRs

## Goal

Freeze temporal, versioning, provenance, and cache semantics before implementation continues.

Required ADRs/documents should cover:

```text
market vs system PIT
two-clock market PIT
publication evidence supersession/retraction
immutable aggregate sealing
hash boundaries
source-level PIT capability
cross-source policy
optional cache architecture
cache-key correctness
```

Acceptance criteria:

- [x] `information_as_of` and `knowledge_as_of` are defined
- [x] `system_as_of` is defined
- [x] publication evidence uses `published_at` + DB-controlled `recorded_at`
- [x] business and evidence revisions are separate
- [x] only sealed complex aggregates are visible
- [x] source-level PIT capability is defined
- [x] Redis is explicitly optional
- [x] cache-on/cache-off equivalence is an invariant
- [x] PIT-aware cache-key fields are documented

---

# 24. Phase 1 — Versioned PIT Database Schema

## Goal

Implement the correctness foundation in PostgreSQL 18.

Core tables/concepts include:

```text
security
security_metadata_versions
ingest_runs
raw_artifacts
dataset_catalog
dataset_sources

daily_price_versions
monthly_revenue_versions

financial_filing_versions
financial_filing_seals
financial_facts
quarterly_financial_summary

tdcc_snapshot_versions
tdcc_snapshot_seals
tdcc_distribution

publication_evidence
```

Exact table decomposition may evolve through ADRs.

Requirements:

- append-only business histories
- append-only evidence histories
- DB-enforced sealed aggregate immutability
- server/trusted timestamps
- source-aware logical keys
- canonical hash generation
- lineage constraints
- PostgreSQL 18 migration round-trip

Acceptance criteria:

- [ ] child insert after seal is rejected
- [ ] sealed aggregate cannot be updated/deleted
- [ ] committed unsealed aggregate can exist but is resolver-invisible
- [ ] caller cannot forge normal historical ingestion time
- [ ] business hash is storage-generated from canonical business content
- [ ] evidence update does not create false business revision
- [ ] correction/retraction can be represented append-only
- [ ] XBRL context identity supports dimensions
- [ ] source A capability does not leak to source B
- [ ] Alembic upgrade/downgrade/upgrade succeeds

---

# 25. Phase 2 — Core PIT Resolver

## Goal

Implement correct read semantics without any cache.

Important:

> Phase 2 must be correct with `CACHE_BACKEND=none`.

Implement:

```text
market PIT resolver
system PIT resolver
publication evidence resolver
source policy resolver
```

Required tests include:

```text
market current-best reconstruction
market historically reproducible reconstruction
system PIT late ingestion
publication evidence correction
publication evidence retraction
unknown publication
backfill
source-level capability
unsealed aggregate invisibility
```

Acceptance criteria:

- [ ] resolver correctness does not depend on Redis
- [ ] deterministic authoritative-evidence selection exists
- [ ] market/system semantics match Phase 0
- [ ] queries return provenance

---

# 26. Phase 3 — Security Metadata and Daily Price

## Goal

Deliver PIT-safe security identity/history and daily price access.

Include:

```text
listed/delisted history
security metadata versions
daily price versions/revisions
source provenance
PIT queries
```

Acceptance criteria:

- [ ] no survivorship-only current security list
- [ ] historical security state is queryable
- [ ] daily prices are source/revision aware
- [ ] dataset-specific regression tests exist

---

# 27. Phase 4 — Monthly Revenue

## Goal

Implement PIT-safe monthly revenue ingestion and queries.

Must preserve:

```text
business revision
publication evidence
source
raw provenance
```

Required historical regression cases include delayed publication around known dates.

Acceptance criteria:

- [ ] unknown publication is market-invisible
- [ ] official publication evidence can later improve reconstruction
- [ ] knowledge cutoff prevents using evidence learned later
- [ ] same business content with new evidence does not create business revision
- [ ] dataset-specific PIT tests exist

---

# 28. Phase 5 — Financial / XBRL

## Goal

Implement PIT-safe financial filing ingestion and financial facts.

Requirements:

```text
parent+children immutable aggregate
seal-based visibility
full context_hash
QName-aware concepts
publication evidence
curated quarterly summary
```

Acceptance criteria:

- [ ] unsealed filings are invisible
- [ ] child insert after seal fails
- [ ] dimensional facts can coexist correctly
- [ ] canonical duplicate facts are rejected
- [ ] Q4 availability is controlled by evidence, not calendar shortcuts
- [ ] historical EPS actuals are PIT-safe
- [ ] dataset-specific regression tests exist

---

# 29. Phase 6 — TDCC

## Goal

Implement PIT-safe TDCC snapshot/distribution history.

Requirements:

```text
snapshot parent
distribution children
seal-based visibility
source/provenance
publication/effective-time semantics
```

Acceptance criteria:

- [ ] snapshot is invisible before seal
- [ ] distribution cannot mutate after seal
- [ ] backfilled historical data does not falsify system PIT
- [ ] dataset-specific regression tests exist

---

# 30. Phase 7 — Standardize Public REST Contract

## Goal

Expose already-correct dataset/resolver capabilities through a stable API.

Phase 7 standardizes:

```text
routing
request schemas
PIT context schemas
source selection
errors
pagination
provenance
OpenAPI
```

It must not reimplement dataset logic.

Example explicit parameters:

```text
information_as_of
knowledge_as_of
system_as_of
source
```

Avoid ambiguous:

```text
date
as_of
```

unless an endpoint's contract makes the meaning unambiguous.

Acceptance criteria:

- [ ] public endpoints do not expose DB tables
- [ ] provenance is available in responses
- [ ] invalid PIT combinations fail loudly
- [ ] API works with `CACHE_BACKEND=none`

---

# 31. Phase 8 — Optional Cache Abstraction

## Goal

Add caching without changing resolver semantics.

Implement:

```text
Cache protocol/interface
NullCache
cache key canonicalizer
codec
cache policy
```

Start with `NullCache`.

Then add integration points above PIT resolution.

Acceptance criteria:

- [ ] default/no-cache mode remains fully functional
- [ ] cache layer contains no PIT business rules
- [ ] cache key includes all temporal/source semantics
- [ ] cache hit returns the same response schema/provenance as a miss

---

# 32. Phase 9 — Redis Backend

## Goal

Add optional remote Redis query-result caching.

Implement:

```text
RedisCache
short connect/socket timeouts
TTL policy
serialization
metrics
graceful bypass on failures
```

Redis configuration must be optional.

Environment examples:

```env
CACHE_BACKEND=none
```

or:

```env
CACHE_BACKEND=redis
REDIS_URL=redis://192.168.x.x:6379
```

Required equivalence tests:

```text
same query, NullCache -> result A
same query, Redis cold -> result A
same query, Redis warm -> result A
Redis failure fallback -> result A
```

Required PIT cache regression tests:

```text
different information_as_of -> different cache identity
different knowledge_as_of -> different cache identity
different system_as_of -> different cache identity
different source -> different cache identity
resolver version bump -> different namespace
```

Acceptance criteria:

- [ ] Redis is not required to start Data Center in none mode
- [ ] Redis outage does not fail correct PostgreSQL-backed queries
- [ ] cache-on/cache-off result equality is tested
- [ ] no cache key omits PIT context
- [ ] Redis persistence is not required
- [ ] cache metrics expose hit/miss/error behavior

---

# 33. Phase 10 — Operational Tooling and Observability

## Goal

Make ingestion, PIT reads, PostgreSQL behavior, and cache behavior observable.

Include metrics/logging for:

```text
ingest runs
PIT query latency
PostgreSQL query latency
cache hits
cache misses
cache errors
cache bypasses
result sizes
source failures
seal failures
```

Operational diagnostics should make it possible to determine whether Redis is actually reducing PostgreSQL workload / SSD reads.

Useful host/database observation may include:

```text
PostgreSQL blks_read / blks_hit
iostat
query timing
cache hit ratio
```

Do not make performance assumptions without measurement.

---

# 34. Phase 11 — Full PIT Regression and CI Gate

## Goal

Consolidate all permanent correctness tests.

CI must include PostgreSQL 18.

Core test families:

```text
schema/migration tests
append-only tests
seal tests
business revision tests
publication evidence tests
two-clock market PIT tests
system PIT tests
source-level capability tests
daily price tests
revenue tests
XBRL tests
TDCC tests
API tests
cache equivalence tests
Redis failure tests where practical
```

CI should run:

```text
pytest
Alembic upgrade
Alembic downgrade/upgrade round-trip
alembic check
git diff --check
```

Acceptance criteria:

- [ ] PIT regressions block merge
- [ ] cache regressions block merge
- [ ] PostgreSQL 18 is verified
- [ ] all dataset-specific suites are included
- [ ] cache disabled mode is always tested

---

# 35. Phase 12 — Downstream Readiness

## Goal

Make the Data Center safe and convenient for:

```text
stock-eps-model
stock-model-selection
```

Provide stable client/API examples for:

```text
historically reproducible market query
current-best market reconstruction
system PIT query
historical universe
daily prices
monthly revenue
financials
actual EPS history
TDCC
```

Downstream systems must not need to know whether Redis exists.

Acceptance criteria:

- [ ] downstream code uses only API/SDK
- [ ] no downstream DB credentials are required
- [ ] no downstream Redis credentials are required
- [ ] changing CACHE_BACKEND requires no downstream code change

---

# 36. Suggested Source Layout

```text
stock-data-center/
├── README.md
├── ROADMAP.md
├── AGENTS.md
├── pyproject.toml
├── docker-compose.yml
├── .env.example
├── alembic.ini
├── migrations/
├── data/
│   └── raw/
├── docs/
│   ├── architecture.md
│   ├── pit_semantics.md
│   ├── schema.md
│   ├── data_sources.md
│   ├── cache.md
│   ├── phase_reports/
│   └── decisions/
├── src/
│   └── stock_data_center/
│       ├── api/
│       ├── services/
│       ├── pit/
│       ├── cache/
│       │   ├── base.py
│       │   ├── null_cache.py
│       │   ├── redis_cache.py
│       │   ├── keys.py
│       │   ├── codec.py
│       │   └── policy.py
│       ├── storage/
│       ├── ingestion/
│       └── domain/
└── tests/
    ├── unit/
    ├── integration/
    ├── pit/
    └── regression/
```

---

# 37. Definition of Done for v1

Version 1 is complete when:

- [ ] PostgreSQL 18 is the sole authoritative data store
- [ ] market PIT supports `information_as_of` + `knowledge_as_of`
- [ ] system PIT supports exact ingestion reconstruction
- [ ] business revisions and publication-evidence revisions are separate
- [ ] complex aggregates are seal-protected
- [ ] source-level PIT capability is enforced
- [ ] XBRL full context identity is supported
- [ ] raw provenance is auditable
- [ ] REST API is stable
- [ ] Redis is optional
- [ ] Data Center works without Redis
- [ ] Redis failure falls back safely
- [ ] cache keys are PIT-aware
- [ ] cache-on/cache-off results are identical
- [ ] full PIT/cache regression suite runs in CI
- [ ] downstream ML repos need neither DB nor Redis access

---

# 38. Core Design Principles

1. Correct historical visibility before convenience.
2. PostgreSQL is truth; Redis is disposable optimization.
3. Redis may improve performance but may never change semantics.
4. Market time and Data Center knowledge time are separate clocks.
5. System PIT means actual complete ingestion history.
6. Business revisions and evidence revisions are separate.
7. Complex aggregates become visible only when sealed.
8. Sources remain separate unless an explicit reconciliation policy exists.
9. Cache resolved results, not database implementation details.
10. Every cache identity must include all PIT-relevant semantics.
11. Downstream ML systems never reproduce PIT rules.
12. Measure SSD/database behavior before claiming cache benefit.
