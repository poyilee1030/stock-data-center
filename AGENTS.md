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
canonical reusable derived datasets
public API
optional query-result caching
```

It does NOT own:

```text
EPS model training
stock-selection model training
portfolio research
strategy ranking logic
model-specific experimental features
```

The highest-priority requirement is:

> Historical correctness must not depend on current database state, caller discipline, derivation timing, or cache availability.

---

# 1. Canonical Roadmap

The canonical roadmap is:

```text
ROADMAP.md
```

Do not maintain competing version-suffixed roadmaps in the repository root.

Work one phase at a time.

Do not begin later phases merely because code is convenient to add.

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
which canonical derivation definition applies
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
calculate canonical derived data
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

Cache resolved Data Center responses above the PIT resolver / canonical-derivation service.

Correct:

```text
API
 -> service
 -> cache
 -> PIT resolver / derived service on miss
 -> PostgreSQL
```

Avoid:

```text
API route containing Redis code
SQL repository containing Redis code
PIT resolver changing semantics based on cache
derived calculator changing formulas based on cache
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

For canonical derived queries:

```text
derivation_version
```

Omitting any PIT cutoff or derivation identity from a cached query key is a P0 bug.

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
derivation semantics
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
institutional/margin/SBL query results
canonical derived results
large repeated historical query results
```

The objective is to avoid repeated DB/PIT/derivation work.

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
derivation definitions
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

# 21. Seal Concurrency Rule

Seal and child mutation must serialize on the same aggregate identity.

For concurrent seal vs child mutation, only these outcomes are valid:

```text
child commits first
-> seal sees and hashes the child

or

seal commits first
-> child mutation is rejected
```

This outcome is forbidden:

```text
seal hash excludes child
but child commits afterward
```

Keep real multi-connection concurrency regression tests.

---

# 22. Seal Table Rule

Prefer dataset-specific seal tables with real foreign keys.

Avoid loose polymorphic references such as:

```text
aggregate_type + version_id
```

when they cannot be protected by real referential integrity.

---

# 23. Ingestion-Time Rule

Normal callers must not provide authoritative historical ingestion timestamps.

For complex aggregates, the system-visible time is the server/trusted seal time.

Historical timestamp preservation is allowed only through a dedicated trusted migration path with provenance and tests.

---

# 24. Business Hash Rule

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

# 25. Hash Separation

Keep separate:

```text
business_content_hash
publication_evidence_hash
raw_artifact_hash
```

Do not reuse a generic `content_hash` to mean different identities.

---

# 26. Duplicate Fetch Rule

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

# 27. Provenance Integrity

If a record stores:

```text
raw_artifact_id
ingest_run_id
```

the DB should enforce that the artifact belongs to that ingest run.

Do not allow mismatched lineage IDs.

---

# 28. Raw Artifact Rule

Raw artifacts are immutable and content-addressed.

Do not overwrite existing content-addressed paths with different bytes.

Raw artifacts are evidence/provenance, not a cache.

---

# 29. Source-Level PIT Capability

PIT capability belongs to:

```text
(dataset_code, source)
```

not only `dataset_code`.

Do not let verified evidence rules for one source authorize another source.

---

# 30. Cross-Source Rule

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

# 31. Unknown Publication Rule

If:

```text
published_at IS NULL
```

the row/version is market-PIT invisible by default.

It may still be system-PIT visible if legitimately ingested by the system cutoff.

Do not invent a historical publication timestamp.

---

# 32. Backfill Rule

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

# 33. XBRL Context Rule

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

# 34. Timezone Rule

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

# 35. Data Domain Inventory Is Mandatory

Phase 1 must define the complete known v1 storage contract.

Maintain:

```text
docs/data_domain_inventory.md
```

Every relevant legacy table/domain/field must be mapped to one of:

```text
observed/source dataset
canonical derived dataset
model-specific downstream feature
raw-artifact-only
deprecated / intentionally removed
```

No known v1 legacy domain may remain unmapped when Phase 1 is accepted.

---

# 36. Phase 1 Is Complete v1 Storage Contract

Phase 1 is NOT merely:

```text
create a few core PIT tables
```

Phase 1 must define the complete known v1 storage contract for:

```text
core identity/provenance
daily market data
monthly revenue
financial/XBRL
TDCC

institutional investor data
foreign/trust/dealer source data

margin trading
short selling
SBL

market indices
corporate actions
official valuation source data

canonical derived dataset definitions/storage strategy
```

Later phases may populate and expose these datasets.

Empty-but-correct tables/contracts are acceptable in Phase 1.

---

# 37. Legacy Domain Coverage Rule

Before Phase 1 is accepted, review the old DB inventory field-by-field.

Do not silently omit old observable data.

For each field/domain, explicitly decide:

```text
keep as observed
recompute as canonical derived
leave to downstream model
preserve only in raw artifact
deprecate intentionally
```

Document the decision.

---

# 38. Observed vs Canonical-Derived Boundary

Do not use this oversimplified boundary:

```text
raw -> Data Center
derived -> ML repo
```

Use this boundary instead:

```text
Data Center
=
observable/source facts
+
deterministic reusable canonical derived data

ML repos
=
model-specific transformations/features/labels/training
```

A derived value may belong in Data Center if it has a stable cross-repo financial definition.

---

# 39. Canonical Derived Dataset Rule

Canonical derived datasets are allowed in Data Center when they are:

```text
deterministic
cross-repo reusable
financially well-defined
independent of a specific ML model
reconstructible from PIT-safe Data Center inputs
```

Examples:

```text
technical indicators
shareholding concentration
TTM EPS
canonical ROE/ROA/margins
margin usage ratios
short-interest/SBL ratios
```

---

# 40. Model-Specific Feature Rule

Do not put model-specific features into Data Center.

Forbidden examples:

```text
selection_score_v4
momentum_quality_combo
eps_growth_signal_weighted
experiment-specific interaction terms
```

These belong in downstream ML repositories.

---

# 41. Derivation Version Is Mandatory

Every canonical derived dataset must have an explicit:

```text
derivation_version
```

Formula changes require a new derivation version.

Do not silently overwrite historical derived values with a changed implementation.

Example:

```text
technical_indicators:v1
technical_indicators:v2
```

---

# 42. Derivation Definition Rule

A canonical derivation definition must preserve enough information to identify its semantics.

At minimum:

```text
dataset code
derivation version
formula/specification
implementation version or git commit
required input datasets
calendar/timezone convention where relevant
price-adjustment convention where relevant
registration timestamp
```

Keep this in schema/docs appropriate to the implementation.

---

# 43. Derived PIT Rule

A derived value does not create a new market-publication time merely because Data Center computed it later.

For market PIT:

```text
derived visibility inherits from PIT-safe input visibility
```

For example:

```text
MA20
TTM EPS
large-holder concentration
margin usage ratio
```

must use only inputs visible under the requested PIT context.

Do not set:

```text
published_at = computed_at
```

for a canonical derived metric.

---

# 44. `computed_at` Rule

`computed_at` means:

```text
when Data Center calculated/materialized the derived result
```

It is provenance / operational metadata.

It is NOT:

```text
market publication time
```

Do not use `computed_at` to determine market PIT visibility.

---

# 45. Derived Lineage Rule

Materialized canonical derived results must preserve:

```text
derivation_version
input dataset identity
input lineage / deterministic input fingerprint
PIT context
computation provenance
business-content hash
```

Repeated recomputation with identical inputs and derivation version must not create a semantically different result.

---

# 46. Materialized vs Virtual Derived Data

Canonical derived datasets may be:

```text
materialized in PostgreSQL
or
computed on demand and cached
```

Storage strategy is a performance decision.

The financial definition and PIT semantics must remain identical.

Do not make downstream repos care whether a result is materialized or virtual.

---

# 47. Shared Derived Data Goal

If both:

```text
stock-eps-model
stock-model-selection
```

need the same canonical metric, prefer one Data Center definition rather than independent reimplementations.

This avoids:

```text
formula drift
PIT drift
duplicate computation
inconsistent feature semantics
```

---

# 48. Daily Market Legacy Coverage

The legacy `daily_quotes` domain must be reviewed field-by-field.

At minimum decide the v1 disposition of:

```text
OHLC
volume
trade value
trade count
price change
bid/ask fields
other source-observable values
```

Do not assume OHLCV alone is sufficient without an explicit inventory decision.

---

# 49. Institutional / Chip-Flow Coverage

The v1 storage contract must cover source data required to reproduce legacy institutional/chip-flow metrics, including as applicable:

```text
institutional investor buy/sell/net flow
foreign investor data
investment trust data
dealer data
foreign/trust/dealer holdings
```

Exact table decomposition may differ, but the domain cannot remain undefined.

---

# 50. Margin / SBL Coverage

The v1 storage contract must cover:

```text
margin trading
short selling
securities borrowing and lending
```

Derived pressure/ratio metrics may be canonical derived datasets.

Do not migrate old model-specific interpretations as source facts.

---

# 51. Corporate Action Coverage

The v1 schema must define a corporate-action domain for:

```text
cash dividends
stock dividends
rights
ex-dividend / ex-right events
other supported corporate actions
```

This is required for future adjusted-price and total-return correctness.

---

# 52. Market Index Coverage

The v1 schema must define historical market-index storage used by:

```text
market-regime features
benchmarks
backtests
```

Do not force downstream backtesters to source their own index data.

---

# 53. Official Valuation vs Computed Valuation

Keep these concepts separate:

```text
source-published PE/PB/dividend yield
```

and:

```text
Data Center-computed valuation metrics
```

The former is observed source data.

The latter is canonical derived data.

Do not collapse them into one ambiguous table without clear source/derivation semantics.

---

# 54. Monthly Revenue Derived Fields

Values such as:

```text
MoM
YoY
cumulative revenue
cumulative YoY
```

may be recomputed from PIT-safe monthly revenue history.

Do not duplicate them as source facts unless preserving the source-published value itself is intentional and documented.

---

# 55. API Boundary Rule

Public clients may know:

```text
dataset concepts
PIT context
source selection
derivation version
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

# 56. Downstream Credential Rule

`stock-eps-model` and `stock-model-selection` must not require:

```text
PostgreSQL credentials
Redis credentials
```

They use only Data Center API/SDK credentials/configuration.

---

# 57. Ingestion and Query Separation

Write path:

```text
source -> raw artifact -> parser -> normalization -> PostgreSQL
```

Read path:

```text
client -> API -> optional cache -> PIT resolver / derived service -> PostgreSQL
```

Do not route ingestion correctness through Redis.

---

# 58. No Cache-Only Writes

Never write business/evidence/derivation-definition data only to Redis.

All durable writes go through authoritative storage/provenance paths.

---

# 59. Query Alias Rule

Avoid ambiguous cache behavior for:

```text
latest
now
```

Resolve aliases to explicit semantics/timestamps before canonical cache identity whenever possible.

Use shorter TTLs for intentionally dynamic current queries.

---

# 60. Cache TTL Rule

TTL is a performance policy, not a correctness mechanism.

Never rely on:

```text
the wrong key will expire soon
```

as a substitute for correct cache identity.

---

# 61. Cache Serialization Rule

Cache encoding must be deterministic and versioned.

Cached payloads must retain enough response metadata/provenance to be semantically identical to an uncached response.

Do not return a reduced-information cached response.

---

# 62. Observability Rule

Expose/log enough information to distinguish:

```text
cache hit
cache miss
cache error
cache bypass
PostgreSQL query
resolver latency
derived calculation latency
response size
```

Do not log sensitive credentials or raw connection strings.

---

# 63. SSD Optimization Rule

Redis/materialization are intended primarily as read/query workload optimizations.

Do not claim SSD benefit without measurement.

Use metrics such as:

```text
PostgreSQL blks_read / blks_hit
query timings
iostat
cache hit/miss metrics
derived recomputation counts
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

# 64. Testing Cache Correctness

Every cacheable query family must test at least:

```text
NullCache result
Redis cold result
Redis warm result
Redis unavailable fallback result
```

All must match semantically.

Also test that changing each relevant PIT/source/derivation parameter produces a distinct cache identity.

---

# 65. Testing Derived Correctness

Every canonical derived dataset must have tests for:

```text
deterministic formula output
derivation-version behavior
PIT-safe input selection
no future input leakage
input lineage/fingerprint
materialized vs virtual equivalence where both exist
```

A derivation bug is not fixed until a regression test exists.

---

# 66. PIT Regression Tests Are Permanent

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
seal concurrency
source capability isolation
XBRL dimensions
derived PIT inheritance
cache PIT identity
cache failure fallback
```

---

# 67. Phase Scope Discipline

When implementing one ROADMAP phase:

- implement only that phase
- do not weaken existing acceptance criteria
- do not silently change PIT semantics
- do not silently change derivation semantics
- record architectural changes in ADRs
- keep phase reports current
- avoid unrelated refactors

If a later-phase optimization is needed for correctness, document why.

---

# 68. Phase 1 Special Rule

Phase 1 must establish the complete known v1 storage and ownership contract.

Do not accept Phase 1 merely because the currently implemented core tables are correct.

Before Phase 1 completion:

```text
docs/data_domain_inventory.md must be complete
all known v1 domains must be mapped
all intended v1 observed domains must have storage contracts
all intended v1 canonical-derived domains must have materialized/virtual contracts
```

Phase 1 may leave tables empty.

Phase 1 does not need to implement all ingestion/calculator pipelines.

---

# 69. Phase 1 No-Redis Rule

Do not introduce Redis into Phase 1 schema correctness work.

First complete:

```text
storage contract
constraints
PIT metadata
provenance
domain coverage
derived-data contract
```

Caching remains a later optimization phase.

---

# 70. Derived Implementation Phase Rule

Phase 1 may define canonical derived storage/definition contracts.

Actual calculators belong in the later canonical-derived-data phase unless needed for schema validation.

Do not implement model training logic inside derived calculators.

---

# 71. Migration Rule

A migration that changes historical temporal or derivation meaning must include a cache-impact section.

If it can change prior cached query results:

```text
bump namespace
or
purge affected cache entries
```

before/with rollout.

---

# 72. Redis Configuration Rule

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

# 73. Security Rule

If Redis is remote:

- do not expose it publicly
- restrict network access to trusted hosts
- use authentication/TLS where the network environment requires it
- never embed credentials in source control

Redis is infrastructure, not a public API.

---

# 74. Priority Order

When tradeoffs exist, prioritize:

```text
1. PIT correctness
2. historical auditability
3. complete v1 domain ownership/storage contract
4. provenance integrity
5. deterministic behavior
6. source isolation
7. derivation-version correctness
8. cache/result equivalence
9. maintainability
10. performance
11. SSD/read reduction
12. convenience
```

Never trade the first eight for performance.

---

# 75. Phase Completion Checklist

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

For Phase 1 additionally:

```text
legacy/domain inventory complete
all known v1 domains mapped
observed storage contracts complete
canonical-derived contracts complete
derivation-version semantics documented
```

For cache phases additionally:

```text
CACHE_BACKEND=none passes
Redis cold/warm equivalence passes
Redis failure fallback passes
```

Then publish the phase acceptance report.

---

# 76. Core Boundary

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
    canonical reusable derived data
    optional cache
    API

stock-eps-model
owns:
    EPS-specific features
    EPS labels
    EPS training
    EPS prediction

stock-model-selection
owns:
    model-specific selection features
    forward-return labels
    selection training
    ranking
    backtesting
```

Redis must remain an optional implementation detail of `stock-data-center`.

Canonical derived datasets must remain model-independent, versioned, PIT-safe, and reusable.

Enabling, disabling, restarting, or losing Redis may affect performance, but must never affect PIT correctness or returned data.
