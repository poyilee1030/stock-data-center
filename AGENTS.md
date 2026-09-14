# AGENTS.md

> Delivery is PR-driven. `ROADMAP.md` is authoritative for PR number, scope, dependencies, acceptance criteria, and out-of-scope work. Historical Phase terminology may remain in old ADRs/reports but must not drive new implementation planning.

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

# 1. Canonical Roadmap and Delivery Unit

The canonical roadmap is:

```text
ROADMAP.md
```

Do not maintain competing version-suffixed roadmaps in the repository root.

The delivery unit is a **pull request**.

Before implementation, identify the current PR and read its:

```text
goal
dependencies
in-scope work
out-of-scope work
schema/PIT/provenance impact
migration impact
tests
acceptance criteria
```

Work one PR at a time.

Do not implement later planned PRs merely because adjacent code is convenient to add.

If correctness of the current PR requires a change outside its original implementation detail,
make the minimum correctness fix and document why it belongs in the current PR.

If review discovers an independent requirement, create/propose a follow-up PR instead of
silently expanding scope.

New implementation progress is tracked with:

```text
MERGED
IN REVIEW
PLANNED
BLOCKED
SUPERSEDED
```

Do not use "Phase complete" as a progress claim.

Historical artifacts may still contain names such as:

```text
Phase 9
Phase 9 Pilot 1
docs/phase_reports/...
```

Do not rename historical artifacts solely for terminology cleanup.

For new PR acceptance evidence, prefer:

```text
docs/pr_reports/pr-<N>-acceptance-report.md
```

or the repository's current equivalent if a different PR-report convention is already established.

Each required PR criterion must be marked PASS/FAIL with concrete evidence.

If a required correctness criterion fails, stop and keep the PR unmerged.

---


## Current PR-Based Sequence

At the time this file was aligned to the PR-based roadmap:

```text
PR #11  authoritative security lifecycle history        MERGED
PR #12  Taiwan corporate-action contract               IN REVIEW
PR #13  official corporate-action raw-first pilot      PLANNED
PR #14  authoritative Taiwan trading calendar          PLANNED
PR #15  historical corporate-action backfill           PLANNED
PR #16  full daily-market historical backfill          PLANNED
```

This block is only a convenience snapshot.

`ROADMAP.md` remains authoritative if PR numbering/status changes.


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

New reconciliation policies require ADR + permanent regression tests.

Final cross-source reconciliation truth must be deterministic with respect to stored source history.
It must not depend on:

```text
which source imported first
worker scheduling
current process state
```

If reconciliation requires multiple sources, either:

```text
run final reconciliation only after the required histories exist
```

or:

```text
make reconciliation explicitly re-runnable/recomputable from canonical stored histories
```

A per-import result may be provisional only if it is clearly labeled provisional and a deterministic
final reconciliation path exists.

For market-transfer reconciliation, permanent tests must include materially different import orders,
for example:

```text
TPEx exit -> TWSE entry -> reconcile
TWSE entry -> TPEx exit -> reconcile
```

Both must converge to the same final matched/unmatched truth.

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

The v1 inventory/storage ownership contract was established by earlier merged PRs and must remain
complete as the repository evolves.

A new PR that introduces a domain, removes a field, or changes ownership must update the inventory
in the same PR.

No known v1 legacy domain may silently become unmapped.

---

# 36. Complete v1 Storage Contract Is a Permanent Invariant

The repository contract is NOT merely:

```text
create a few core PIT tables
```

The known v1 storage contract covers:

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

Later PRs may populate, backfill, expose, or derive these datasets, but they must not weaken the
already-established ownership/storage contract without an explicit ROADMAP/ADR change.

---

# 37. Legacy Domain Coverage Rule

When changing legacy/domain coverage, review the old DB inventory field-by-field.

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

The schema and real-source adapters must explicitly support, where available:

```text
cash dividends

earnings stock dividends / 盈餘配股
capital-surplus stock dividends / 資本公積配股

rights issues
ex-dividend / ex-right events

stock splits
reverse splits

capital reductions

other explicitly supported corporate actions
```

Do not collapse economically similar but legally/source-distinct events into one ambiguous type
merely because their adjustment math may be similar.

Preserve source terms and, where available:

```text
announcement_date
ex_date
record_date
payment_date

cash_dividend_per_share

earnings_stock_ratio
capital_surplus_stock_ratio
free_share_ratio

old_shares
new_shares

rights_ratio
subscription_price

close_before
official_reference_price
official_rights_dividend_value

original source event type / terms
```

For split-style events, prefer:

```text
old_shares
new_shares
```

over a provider-specific ambiguous `split_ratio`.

This is required for adjusted-price and total-return correctness.

## 51.1 Raw vs Adjusted Price Rule

Official daily OHLC is observed source data.

Never rewrite raw historical OHLC merely to remove a discontinuity caused by:

```text
cash dividend
stock dividend
stock split
rights issue
capital reduction
```

Adjusted price, adjustment factors, and total-return series are canonical derived data with explicit
derivation versions.

Required layering:

```text
raw official OHLC
+ PIT-safe corporate actions
-> versioned adjustment factors
-> adjusted OHLC / total-return series
```

Downstream consumers must be able to distinguish raw and adjusted series.

## 51.2 Corporate-Action Inference Rule

Never infer a corporate action solely from a large price jump.

A large unexplained raw-price move must trigger reconciliation/reporting:

```text
observed corporate action explains it
other documented market event explains it
or
unexplained anomaly
```

If an official ex-right/ex-dividend reference price exists, preserve it as source data and use it to
reconcile the deterministic calculation.

## 51.3 Adjustment Timing Rule

Do not apply an adjustment event before its effective/ex date under the selected PIT context.

Historical returns, MA, volatility, RSI/MACD, and other continuity-sensitive canonical metrics must
declare which price convention they use.

## 51.4 Historical Price Readiness Gate

Raw daily-price ingestion may occur before all corporate-action history is complete because raw
official prices are valid source facts.

However, do not declare historical prices **analysis-ready** for canonical returns, technical
indicators, backtesting, or ML features until:

```text
corporate-action contract is explicit
representative real-source corporate-action pilot passes
supported corporate-action history is backfilled
large price discontinuities are reconciled
```

Under the PR-based roadmap, this means the dependency/acceptance criteria for the relevant
corporate-action and price-backfill PRs must be satisfied before adjusted/derived-price work starts.

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

# 67. PR Scope Discipline

When implementing one ROADMAP PR:

- implement only that PR's dominant delivery goal
- honor declared dependencies
- honor explicit out-of-scope work
- do not weaken existing acceptance criteria
- do not silently change PIT semantics
- do not silently change derivation semantics
- record architectural changes in ADRs
- keep PR acceptance/reconciliation evidence current
- avoid unrelated refactors

If a future-PR optimization is needed for current correctness, document why.

Do not use broad historical labels such as "Phase 9" as permission to implement multiple planned PRs
at once.

---

# 68. Historical Storage-Contract Preservation Rule

Earlier merged PRs established the known v1 storage/ownership contract.

Do not regress that contract while implementing later source adapters, backfills, APIs, caches, or
derived datasets.

Before changing/removing an existing domain contract:

```text
update ROADMAP
update domain inventory
write/adjust ADR if architectural
provide migration semantics
provide regression tests
```

Empty-but-correct contracts established by earlier PRs are not permission to reinterpret their
semantics during later ingestion work.

---

# 69. Cache Scope Rule

Do not introduce Redis/cache work into storage, PIT, ingestion, or source-correctness PRs unless the
current ROADMAP PR explicitly owns cache behavior.

First complete authoritative correctness in PostgreSQL.

Caching remains an optional optimization and must never be required for correctness.

---

# 70. Real-Data Import PR Boundary

Real external-source adapters and bulk historical backfills must be delivered through the dedicated
PRs defined by ROADMAP.md.

Contract PRs may implement:

```text
domain writers
PIT resolvers
normalization types
controlled fixture ingestion
migration tests
```

Fixture-backed ingestion is not proof that real source import/backfill is complete.

A bounded real-source pilot is not proof that full historical coverage/backfill is complete.

Do not begin canonical-derived calculations merely because fixture or bounded pilot data exists.

---

# 71. Raw-First Adapter Rule

For a real external source, preserve the raw artifact before normalization whenever retention is possible.

Required provenance should include as applicable:

```text
source
resource/request identity
fetch time
raw bytes/export
raw artifact hash
ingest run
adapter/parser version
```

Do not discard the source representation after extracting canonical rows.

For legacy PostgreSQL migration, preserve an auditable export/manifest even when original source bytes are unavailable.

---

# 72. Source-Native Unit Rule

A real adapter must know the source unit/scale before constructing a canonical observation.

Examples:

```text
MOPS thousand-TWD -> canonical major currency units
TPEx lots -> canonical shares
TWSE shares -> canonical shares
```

Do not guess a unit from numeric magnitude.

Do not pass ambiguous source-native numbers into canonical writers.

Business hashes use normalized canonical business content.

---

# 73. Historical Backfill Publication-Time Rule

Historical backfill never invents `published_at`.

Allowed:

```text
published_at = historical public time proven by retained/authoritative evidence
```

If not provable:

```text
published_at = NULL
```

Do not substitute:

```text
current import time
filesystem mtime
effective/observation date
legacy row existence
```

for publication time without an explicit source contract.

`recorded_at` is the actual time this Data Center records the evidence.

---

# 74. Historical Backfill System-PIT Rule

Normal backfill records actual Data Center ingestion/seal time.

Do not copy historical market/effective dates into:

```text
ingested_at
seal.ingested_at
```

A trusted migration may preserve old-system ingestion time only if that timestamp genuinely represented completed ingestion and its semantics/provenance are documented and tested.

Otherwise, it is correct for historical Market PIT and current-backfill System PIT to differ.

---

# 75. Legacy Database Migration Rule

The legacy `stock_db` is migration input, not automatically authoritative truth.

Do not bulk-copy old tables into canonical tables without:

```text
legacy field mapping
source-semantic validation
unit normalization
publication/PIT validation
provenance capture
new writer/trusted migration constraints
```

Do not import legacy derived tables as observed truth merely for compatibility.

Prefer authoritative raw-source reconstruction where reliable history exists.

---

# 76. Backfill Idempotency and Restart Rule

Bulk import/backfill must be safe to rerun and resume.

Required:

```text
same canonical content -> no fake business revision
same evidence identity -> no fake evidence revision
repeated observation -> provenance remains auditable
partial failure -> restart does not corrupt prior work
```

Use explicit import IDs, checkpoints, and manifests.

Do not design a one-shot migration that cannot safely resume.

---

# 77. Pilot-Before-Bulk Rule

Before a full historical backfill PR, the relevant real-data pilot and dependency PRs must pass.

Do not use one giant "pilot phase" as a substitute for domain-specific evidence.

Each bulk-import domain should have representative pilot coverage sufficient to exercise its own
semantics.

A real-data pilot must validate, as applicable:

```text
raw artifact
-> parser
-> normalization
-> trusted writer
-> PostgreSQL
-> PIT resolver
-> reconciliation
```

For historical daily prices specifically, do not begin/approve the full backfill merely because
TWSE/TPEx daily-market raw ingestion works.

The ROADMAP dependencies for:

```text
security lifecycle
corporate-action contract/pilot/history
authoritative trading calendar
```

must be satisfied according to the current PR plan.

Do not declare daily-price history analysis-ready until corporate-action discontinuity reconciliation
is available.

---

# 78. Import Reconciliation Rule

Every real-data import/backfill needs reconciliation.

At minimum report:

```text
source/domain
adapter version
requested/actual coverage
raw artifact count
business version count
evidence count
dedup count
unknown-publication count
rejected/quarantined count
coverage gaps
warnings/anomalies
```

For legacy migration, compare old/new samples and counts where meaningful.

Differences caused by corrected revision/evidence modeling are allowed but must be explained.

Never silently ignore discrepancies.

Cross-source reconciliation must be reproducible from canonical stored histories and independent of
incidental import order.

For security market transfers, permanent tests must include both:

```text
exit source imported before entry source
entry source imported before exit source
```

The final matched/unmatched result must converge to the same answer.

If an import-time result can become stale when a counterpart source arrives later, mark it
provisional and provide a deterministic final reconciliation pass.

For historical price backfills, large discontinuities must be classified as:

```text
explained_by_corporate_action
explained_by_other_documented_market_event
unexplained_anomaly
```

Do not silently smooth unexplained anomalies.

---

# 79. Import Manifest and Quarantine Rule

Every pilot/bulk import must emit an auditable manifest containing enough information to identify:

```text
import ID
git commit
adapter/parser version
source/scope
configuration fingerprint
start/end time
input/raw hashes where practical
result counts
warnings/errors
reconciliation result
```

Suspicious or semantically ambiguous records must fail loudly or enter an explicit quarantine path.

Examples:

```text
unknown unit
ambiguous EPS basis
invalid XBRL context
incomplete TDCC distribution
unsupported evidence
unmappable security/market
```

Preserve the raw artifact and failure reason.

---

# 80. Derived Implementation PR Rule

Earlier PRs may define canonical derived storage/definition contracts.

Actual calculators belong in the dedicated canonical-derived PRs defined by ROADMAP.md unless a
minimal implementation is required solely for schema/contract validation.

Do not implement model training logic inside canonical derived calculators.

For price-derived calculations, enforce the dependency chain:

```text
raw official OHLC
+ PIT-safe corporate actions
-> versioned adjustment factors
-> adjusted OHLC / total-return series
-> returns / technical indicators
```

Do not bypass corporate-action readiness by computing indicators directly from mechanically
discontinuous raw prices.

---

# 81. Migration Rule

A migration that changes historical temporal, lineage, or derivation meaning must include explicit
migration/downgrade semantics.

A downgrade must either:

```text
safely represent all stored history
```

or:

```text
fail explicitly before mutation when the previous schema cannot represent it
```

Never delete, merge, or collapse valid append-only PIT history merely to make downgrade succeed.

Permanent regressions must cover any guarded-downgrade condition introduced by the migration.

If a migration can change prior cached query results, include a cache-impact section:

```text
bump namespace
or
purge affected cache entries
```

before/with rollout.

---

# 82. Redis Configuration Rule

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

# 83. Security Rule

If Redis is remote:

- do not expose it publicly
- restrict network access to trusted hosts
- use authentication/TLS where the network environment requires it
- never embed credentials in source control

Redis is infrastructure, not a public API.

---

# 84. Priority Order

When tradeoffs exist, prioritize:

```text
1. PIT correctness
2. historical auditability
3. complete v1 domain ownership/storage contract
4. provenance integrity
5. real-source semantic correctness and unit normalization
6. deterministic/idempotent import behavior
7. source isolation
8. derivation-version correctness
9. import reconciliation/auditability
10. migration/history preservation
11. cache/result equivalence
12. maintainability
13. performance
14. SSD/read reduction
15. convenience
```

Never trade PIT, auditability, provenance, source semantics, import determinism, source isolation,
derivation correctness, reconciliation, migration safety, or cache correctness for performance.

---

# 85. PR Completion Checklist

Before declaring any PR complete/mergeable:

```text
current PR number and ROADMAP scope are identified
declared dependencies are satisfied
tests pass
docs updated
acceptance criteria evaluated
PIT semantics preserved
provenance preserved
no source-capability leakage
no direct downstream DB/cache dependency introduced
out-of-scope work was not silently absorbed
review blockers/majors are resolved
```

For schema/migration PRs additionally:

```text
alembic upgrade passes
alembic check/schema drift passes
downgrade passes
or
downgrade is deliberately guarded before mutation when old schema cannot represent history
permanent migration regressions pass
```

For real-data ingestion/backfill PRs additionally:

```text
real source adapters use explicit source semantics
raw-first provenance is preserved
publication time is proven or unknown
normal backfill preserves actual System-PIT ingestion time
import is idempotent and restartable
reconciliation/import manifests exist
quarantined/anomalous records are reported
real-data PIT spot checks pass
cross-source reconciliation converges independent of import order where applicable
```

For full historical daily-price readiness additionally:

```text
authoritative trading-date coverage exists
corporate-action contract is explicit
supported corporate-action history is available
large discontinuities are reconciled
unexplained anomalies remain visible
raw OHLC remains unchanged
```

For derived-price/indicator PRs additionally:

```text
adjustment convention is explicit/versioned
no future corporate action leaks backward
raw vs adjusted series are distinguishable
derived inputs are PIT-safe and lineage-complete
```

For cache PRs additionally:

```text
CACHE_BACKEND=none passes
Redis cold/warm equivalence passes
Redis failure fallback passes
```

Then publish/update PR acceptance evidence.

Do not publish a new "Phase completion" claim for implementation progress.

---

# 85.1 Agent Execution Protocol

At the start of implementation work:

```text
1. Read ROADMAP.md.
2. Identify the current GitHub PR number.
3. Confirm the PR status is IN REVIEW/PLANNED as appropriate.
4. Read dependencies, acceptance criteria, and out-of-scope work.
5. Inspect current main/head before coding.
6. Implement only the current PR.
```

At the end of implementation work, report:

```text
commit SHA
files/schema materially changed
acceptance criteria satisfied
tests/lint/migration checks
known limitations
explicitly deferred next-PR work
```

If the current GitHub PR number differs from a predicted number in ROADMAP because another PR consumed
the number, update ROADMAP to the actual number instead of forcing the code/history to match the
prediction.

Do not create a new broad "Phase" label to group unfinished work.

---

# 86. Core Boundary

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
