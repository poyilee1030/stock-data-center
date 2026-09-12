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

# 23. Data Domain Ownership and v1 Storage Coverage

`stock-data-center` owns two kinds of reusable data:

```text
A. observed/source datasets
B. canonical reusable derived datasets
```

It does NOT own model-specific experimental features.

The boundary is:

```text
stock-data-center
=
historically observable source data
+
deterministic, cross-repo, canonical derived data

stock-eps-model / stock-model-selection
=
model-specific transformations
+
feature combinations
+
labels
+
training logic
```

A useful ownership test is:

> If all current ML models disappeared tomorrow, would this value still have a stable and independently meaningful financial definition?

If yes, it may belong in Data Center.

Examples that may belong in Data Center:

```text
MA20
20-day historical volatility
TTM EPS
ROE
foreign holding ratio
large-holder concentration
margin usage ratio
official PE/PB/dividend yield
```

Examples that do NOT belong in Data Center:

```text
selection_score_v4
quality_momentum_combo
eps_growth_signal_weighted
model-specific interaction terms
```

---

# 24. v1 Data Domain Inventory

Phase 1 must define the complete storage contract for every known Data Center v1 domain, even if later phases populate the tables.

The authoritative inventory document is:

```text
docs/data_domain_inventory.md
```

It must map every relevant legacy table/domain to one of:

```text
observed/source dataset
canonical derived dataset
model-specific feature
raw-artifact-only field
deprecated / intentionally removed
```

The inventory must also document:

```text
legacy table / field
new dataset/domain
new table(s)
storage class
PIT semantics
source
materialized or virtual
migration/backfill status
downstream consumers
```

No known v1 legacy domain may remain unmapped when Phase 1 is accepted.

## 24.1 Observed / Source Data Domains

The Phase 1 schema contract must include storage for the following known v1 domains.

### Core identity / provenance

```text
security
security metadata history
dataset catalog
dataset-source capabilities
ingest runs
raw artifacts
raw artifact observations
publication evidence
```

### Market data

```text
daily stock trading/price data
market indices
official daily valuation data where source-provided
corporate actions
```

The legacy `daily_quotes` inventory must be reviewed field-by-field.

At minimum decide the v1 disposition of:

```text
OHLC
volume
trade value
trade count
price change
bid/ask fields
other source-observable fields
```

Do not silently drop a legacy observable field without recording its disposition in `docs/data_domain_inventory.md`.

### Revenue

```text
monthly revenue
```

Derived values such as:

```text
MoM
YoY
cumulative revenue
cumulative YoY
```

do not need to be duplicated as observed source values when they can be deterministically derived from PIT-safe revenue history, unless the source-published value itself is intentionally preserved as a distinct source fact.

### Financial / XBRL

```text
financial filing versions
financial facts
curated quarterly summary
historical actual EPS
filing publication evidence
```

### TDCC / ownership distribution

```text
TDCC snapshots
TDCC distribution buckets
```

### Institutional / chip-flow source data

The v1 storage contract must cover the source data required to reproduce legacy institutional/chip-flow features, including as applicable:

```text
institutional investor buy/sell/net flow
foreign investor data
investment trust data
dealer data
foreign holding
trust holding
dealer holding
```

The exact table decomposition may be unified by investor category or separated by source/domain, but it must be explicitly decided in Phase 1.

### Margin / securities lending

The v1 storage contract must include:

```text
margin trading
short selling / margin-short data
securities borrowing and lending (SBL)
```

These are source datasets.

Derived pressure/ratio signals are separate canonical derived datasets.

### Corporate actions

The v1 schema must define a domain for:

```text
cash dividends
stock dividends
rights
ex-dividend / ex-right events
other supported corporate actions
```

This domain is required for future adjusted-price and total-return correctness.

### Market indices

The v1 schema must define index history used by:

```text
market-regime features
benchmarks
backtests
```

### Official source valuation

If the upstream source publishes values such as:

```text
PE
PB
dividend yield
```

those source-published values may be stored as observed source data.

Do not confuse source-published valuation with Data Center-computed valuation metrics.

---

# 25. Canonical Derived Dataset Contract

Data Center may materialize or virtually calculate deterministic derived datasets that are reused across repositories.

Initial v1 canonical derived domains should include, where formulas are frozen:

```text
technical indicators
shareholding concentration
valuation metrics
margin metrics
short-interest / SBL metrics
```

Potential materialized storage concepts include:

```text
derived_dataset_definitions

technical_indicator_versions
shareholding_concentration_versions
valuation_metric_versions
margin_metric_versions
short_interest_metric_versions
```

Exact table decomposition must be decided in Phase 1 and documented in `docs/schema.md` and `docs/data_domain_inventory.md`.

## 25.1 Derivation Version

Every canonical derived dataset must have an explicit:

```text
derivation_version
```

Example:

```text
technical_indicators:v1
```

A formula change must create a new derivation version.

Do not silently overwrite historical values with a new implementation.

## 25.2 Derivation Definition

The system must preserve enough information to identify the formula semantics.

At minimum:

```text
dataset code
derivation version
formula/specification
implementation version or git commit
input dataset requirements
timezone/calendar convention where relevant
adjustment convention where relevant
created/registered time
```

## 25.3 Derived PIT Semantics

A derived value is not automatically a new market publication event.

For market PIT:

> Visibility of a derived metric is inherited from the PIT-safe inputs and formula version used to compute it.

For example:

```text
MA20 at historical date T
```

must be computed only from price inputs visible under that query's PIT context.

Similarly:

```text
TTM EPS
shareholding concentration
margin usage ratio
```

must use only input versions available under the requested PIT context.

`computed_at` means:

```text
when Data Center calculated/materialized the result
```

It does NOT mean:

```text
when the market could know the underlying information
```

Do not use `computed_at` as `published_at`.

## 25.4 Derived Lineage

A materialized derived result must preserve:

```text
derivation_version
input dataset identity
input/query lineage or deterministic input fingerprint
PIT context used
computation provenance
business-content hash
```

Repeated recomputation with identical inputs and derivation version must not create a semantically different result.

## 25.5 Materialized vs Virtual

Canonical derived datasets may be:

```text
materialized in PostgreSQL
or
computed on demand and cached
```

The storage policy is a performance decision.

The financial definition and PIT result must be the same.

---

# 26. Phase 0 — Freeze Contracts and ADRs

## Goal

Freeze temporal, versioning, provenance, data-domain ownership, derived-data, and cache semantics before implementation continues.

Required ADRs/documents should cover:

```text
market vs system PIT
two-clock market PIT
publication evidence supersession/retraction
immutable aggregate sealing
hash boundaries
source-level PIT capability
cross-source policy
observed vs canonical-derived ownership
derived PIT semantics
optional cache architecture
cache-key correctness
```

Acceptance criteria:

- [ ] `information_as_of` and `knowledge_as_of` are defined
- [ ] `system_as_of` is defined
- [ ] publication evidence uses `published_at` + DB-controlled `recorded_at`
- [ ] business and evidence revisions are separate
- [ ] only sealed complex aggregates are visible
- [ ] source-level PIT capability is defined
- [ ] observed vs canonical-derived ownership is defined
- [ ] derived `computed_at` is explicitly not market publication time
- [ ] Redis is explicitly optional
- [ ] cache-on/cache-off equivalence is an invariant
- [ ] PIT-aware cache-key fields are documented

---

# 27. Phase 1 — Complete v1 Storage Contract and Versioned PIT Schema

## Goal

Establish the complete PostgreSQL 18 storage contract for all known Data Center v1 observed and canonical-derived domains.

Phase 1 is complete only when the v1 storage model is intentionally frozen enough that later domain phases can focus on ingestion, calculation, PIT querying, and backfill rather than discovering missing major data domains.

Phase 1 does NOT need to:

```text
download all source data
backfill all tables
implement every calculator
expose every REST endpoint
implement Redis
```

Empty-but-correct v1 tables are acceptable.

## Required Inventory

Create and maintain:

```text
docs/data_domain_inventory.md
```

Every relevant legacy table/field must be mapped to:

```text
new table/domain
or
canonical derived dataset
or
model-specific downstream feature
or
raw-artifact-only
or
deprecated
```

## Core Infrastructure Tables / Concepts

```text
security
security_metadata_versions

dataset_catalog
dataset_sources

ingest_runs
raw_artifacts
raw_artifact_observations

publication_evidence
```

## Observed Dataset Storage Contract

The v1 schema must define storage for all known first-class observed domains, including:

```text
daily stock trading / price history
monthly revenue
financial / XBRL
TDCC distribution

institutional investor flow / holdings
foreign / trust / dealer source data

margin trading
short selling
securities lending / SBL

market indices
corporate actions
official source valuation data
```

Suggested concepts may include:

```text
daily_price_versions
monthly_revenue_versions

financial_filing_versions
financial_filing_seals
financial_facts
quarterly_financial_summary

tdcc_snapshot_versions
tdcc_snapshot_seals
tdcc_distribution

institutional_investor_versions
institutional_holding_versions

margin_trading_versions
securities_lending_versions

market_index_versions
corporate_action_versions
official_valuation_versions
```

Exact decomposition may differ if an ADR documents a better normalized design.

## Canonical Derived Storage Contract

Phase 1 must also define the v1 storage/definition contract for cross-repo canonical derived datasets.

At minimum decide the schema strategy for:

```text
technical indicators
shareholding concentration
valuation metrics
margin metrics
short-interest / SBL metrics
```

Suggested concepts may include:

```text
derived_dataset_definitions
technical_indicator_versions
shareholding_concentration_versions
valuation_metric_versions
margin_metric_versions
short_interest_metric_versions
```

A domain may be declared virtual/on-demand rather than materialized, but that decision must be explicit in `docs/data_domain_inventory.md`.

## Required Versioning / Provenance Semantics

All applicable observed/materialized-derived datasets must define:

```text
logical key
business revision semantics
business_content_hash
source
ingested/system visibility time
publication evidence relationship where applicable
raw/ingest provenance where applicable
indexes for expected PIT resolution
```

Canonical derived datasets must additionally define:

```text
derivation_version
input lineage/fingerprint
computation provenance
PIT inheritance semantics
```

## Requirements

- append-only business histories
- append-only evidence histories
- DB-enforced sealed aggregate immutability
- concurrency-safe seal/child serialization
- server/trusted timestamps
- source-aware logical keys
- canonical hash generation
- lineage constraints
- derivation-version semantics
- complete legacy-domain mapping
- PostgreSQL 18 migration round-trip

## Acceptance Criteria

- [ ] `docs/data_domain_inventory.md` exists and covers all known legacy tables/domains
- [ ] every legacy field/domain is intentionally mapped, deprecated, derived, raw-only, or downstream-owned
- [ ] all known v1 observed data domains have a defined storage contract
- [ ] all known v1 canonical-derived domains have a defined materialized/virtual contract
- [ ] child insert after seal is rejected
- [ ] sealed aggregate cannot be updated/deleted
- [ ] seal and child mutation serialize on the same aggregate identity
- [ ] committed unsealed aggregate can exist but is resolver-invisible
- [ ] caller cannot forge normal historical ingestion time
- [ ] business hash is storage-generated from canonical business content
- [ ] evidence update does not create false business revision
- [ ] correction/retraction can be represented append-only
- [ ] XBRL context identity supports dimensions
- [ ] source A capability does not leak to source B
- [ ] canonical derived tables/definitions include `derivation_version`
- [ ] derived `computed_at` is not used as market publication time
- [ ] migration tests cover every v1 table
- [ ] Alembic upgrade/downgrade/upgrade succeeds
- [ ] `alembic check` passes

---

# 28. Phase 2 — Core PIT Resolver

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

The resolver contract must be reusable by later observed and derived dataset phases.

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

# 29. Phase 3 — Security Metadata and Daily Market Data

## Goal

Deliver PIT-safe security identity/history and daily market-data access.

Include:

```text
listed/delisted history
security metadata versions
daily price/trading versions
source provenance
PIT queries
```

The implementation must follow the Phase 1 disposition of legacy `daily_quotes` fields.

Acceptance criteria:

- [ ] no survivorship-only current security list
- [ ] historical security state is queryable
- [ ] daily market data is source/revision aware
- [ ] intentionally preserved legacy observable fields are queryable
- [ ] intentionally dropped fields are documented
- [ ] dataset-specific regression tests exist

---

# 30. Phase 4 — Monthly Revenue

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

Canonical revenue-derived metrics such as MoM/YoY may be implemented here or in Phase 10, but must follow the Phase 1 derivation contract.

Acceptance criteria:

- [ ] unknown publication is market-invisible
- [ ] official publication evidence can later improve reconstruction
- [ ] knowledge cutoff prevents using evidence learned later
- [ ] same business content with new evidence does not create business revision
- [ ] dataset-specific PIT tests exist

---

# 31. Phase 5 — Financial / XBRL

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

Historical actual EPS and other canonical financial facts must become available for downstream derived metrics.

Acceptance criteria:

- [ ] unsealed filings are invisible
- [ ] child insert after seal fails
- [ ] concurrent seal/child behavior preserves aggregate hash correctness
- [ ] dimensional facts can coexist correctly
- [ ] canonical duplicate facts are rejected
- [ ] Q4 availability is controlled by evidence, not calendar shortcuts
- [ ] historical EPS actuals are PIT-safe
- [ ] dataset-specific regression tests exist

---

# 32. Phase 6 — TDCC

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

TDCC raw distribution is the source dataset for later canonical shareholding-concentration calculations.

Acceptance criteria:

- [ ] snapshot is invisible before seal
- [ ] distribution cannot mutate after seal
- [ ] backfilled historical data does not falsify system PIT
- [ ] dataset-specific regression tests exist

---

# 33. Phase 7 — Institutional, Margin, Short-Selling, and SBL Source Data

## Goal

Implement PIT-safe source data required by reusable chip-flow and financing metrics.

Domains include, as applicable:

```text
institutional investor buy/sell/net flow
foreign investor data
investment trust data
dealer data

foreign/trust/dealer holdings

margin balances
short balances
margin utilization source data

securities borrowing and lending / SBL
```

Do not store model-specific interpretations here.

Acceptance criteria:

- [ ] each source domain has explicit logical/revision keys
- [ ] publication/effective-time semantics are documented
- [ ] historical backfill preserves system PIT
- [ ] source-specific provenance is preserved
- [ ] old model-selection raw dependencies can be reconstructed from Data Center source data
- [ ] dataset-specific regression tests exist

---

# 34. Phase 8 — Market Indices, Corporate Actions, and Official Valuation

## Goal

Implement the remaining reusable observed market domains.

### Market indices

Provide PIT-safe history for:

```text
market-regime features
benchmarks
backtesting
```

### Corporate actions

Implement:

```text
cash dividends
stock dividends
rights
ex-dividend / ex-right events
other supported corporate actions
```

This is the foundation for future adjusted prices and total-return calculations.

### Official valuation

Where a source publishes:

```text
PE
PB
dividend yield
```

preserve them as observed source data with source/revision semantics.

Acceptance criteria:

- [x] index history is PIT-safe
- [x] corporate actions have explicit effective/announcement semantics
- [x] source-published valuation is distinguishable from computed valuation
- [x] backfill/revision provenance is preserved
- [x] dataset-specific regression tests exist

---

# 35. Phase 9 — Real Source Adapters and Historical Backfill

## Goal

Populate PostgreSQL with real historical source data only after the observed-domain storage and PIT contracts are complete.

Earlier phases establish:

```text
schema
domain models
trusted writers
PIT resolvers
normalization contracts
controlled fixture-based regressions
```

Phase 9 is where the project begins production-like ingestion of:

```text
real external-source data
legacy stock_db migration inputs
historical backfill
large-scale reconciliation
```

The required path is:

```text
authoritative source / retained legacy source
        |
        v
raw artifact captured first
        |
        v
source-specific adapter/parser
        |
        v
explicit unit/time/identity normalization
        |
        v
trusted domain writer
        |
        v
PostgreSQL business versions + evidence + provenance
        |
        v
PIT resolver
        |
        v
reconciliation / audit report
```

Canonical derived datasets must not be used to compensate for missing or incorrect observed-source imports.

## 35.1 Real Source Adapter Scope

Implement real adapters for the supported v1 observed domains, including as applicable:

```text
TWSE / TPEx security metadata
TWSE / TPEx daily market data

MOPS monthly revenue
MOPS financial / XBRL

TDCC snapshots / distributions

institutional investor flows
foreign holdings
margin / short selling
securities lending / SBL

market indices
corporate actions
official source valuation
```

An adapter must use the trusted domain contract.

Do not write ad hoc rows directly into canonical version tables merely because bulk loading is faster.

## 35.2 Raw-First Rule

For external sources:

> Preserve the raw source artifact before normalization whenever the source can be retained.

Preserve enough provenance to audit parsing:

```text
source
resource/request identity
fetch time
raw bytes or faithful source export
raw artifact hash
adapter/parser version
ingest run
```

Do not retain only parsed rows when the underlying source representation is available.

For legacy PostgreSQL migration, preserve an auditable hashed export/manifest or equivalent migration artifact before transforming rows.

## 35.3 Canonical Normalization Boundary

Source-native semantics must be explicit at the adapter boundary.

Examples already frozen by earlier phases:

```text
monthly revenue:
    source thousand-TWD
    -> canonical currency major units

stock quantities:
    source lots / shares
    -> canonical shares

TDCC:
    declared distribution profile
    signed level-16 adjustment
    canonical holder_count semantics

security:
    stable security_code identity
    effective-dated market membership

financial/XBRL:
    QName/context identity
    EPS period basis
    explicit nil semantics
```

Do not pass ambiguous bare numeric values into canonical observations when source unit/scale varies by source.

Business hashes use normalized canonical business content.

## 35.4 Publication-Evidence Backfill Rule

Historical backfill must not invent market publication time.

If retained evidence proves historical publication:

```text
published_at = proven historical public time
recorded_at  = actual time this Data Center records the evidence
```

If historical publication time cannot be proved:

```text
published_at = NULL
```

Do not substitute these values for publication time without an explicit source contract:

```text
current import time
filesystem modification time
observation/effective date
legacy row existence
```

A legacy `publish_time` may be migrated only when its original semantics and source are understood and documented.

## 35.5 System-PIT Backfill Rule

Normal backfill records the actual ingestion history of the new Data Center.

Therefore:

```text
ingested_at / seal.ingested_at
=
actual current Data Center insert/seal time
```

Do not copy market/effective dates into ingestion time.

A trusted migration may preserve an old-system ingestion timestamp only when:

```text
the old timestamp truly meant complete ingestion
its semantics are documented
the trusted migration path is explicit
the original provenance is retained
regression tests prove the intended reconstruction
```

Otherwise it is correct for:

```text
Market PIT
    -> historical source information

System PIT
    -> information arrived during the new backfill
```

to show different histories.

## 35.6 Legacy `stock_db` Migration

The old database is migration input, not automatically authoritative truth.

Every migrated domain must pass through:

```text
legacy-field inventory mapping
source-semantic validation
unit normalization
publication/PIT validation
provenance capture
new domain writer or trusted migration constraints
```

Do not bulk-copy old tables into new canonical tables.

Special rules:

```text
old derived tables
    -> do not import as observed truth merely for compatibility

old current-state rows
    -> row existence today is not historical visibility

old trust/dealer "holding"
    -> preserve corrected cumulative-flow proxy semantics
       unless a reliable absolute baseline is introduced

old publication timestamps
    -> preserve only when semantics/source evidence are validated
```

Prefer authoritative source reconstruction over legacy derived values when reliable raw history is available.

## 35.7 Idempotent and Restartable Backfill

Backfill jobs must be safe to rerun and resume.

Required behavior:

```text
same canonical business content
    -> no fake business revision

same evidence identity
    -> no fake evidence revision

repeated fetch/import observation
    -> auditable provenance remains

partial job failure
    -> restart without corrupting imported history
```

Use explicit import IDs, checkpoints, and manifests.

Do not design full historical migration as a one-shot process that cannot be resumed safely.

## 35.8 Pilot Before Full Backfill

Do not begin full-market historical backfill immediately.

First run a representative pilot.

The pilot should include, where supported:

```text
at least one TWSE security
at least one TPEx security
at least one security with market-transfer history
at least one security with revenue + financial/XBRL + TDCC coverage
representative institutional / margin / SBL records
representative index / corporate-action / valuation records
```

The pilot must exercise:

```text
raw capture
parser
normalization
trusted writer
PostgreSQL
publication evidence
PIT resolver
history query
repeat-fetch behavior
reconciliation
```

Full historical backfill starts only after the pilot acceptance report passes.

## 35.9 Reconciliation

Every imported domain requires a reconciliation report.

At minimum record:

```text
source
adapter/version
requested date range
actual coverage range
raw artifact count
normalized version count
dedup count
publication evidence count
unknown-publication count
rejected/quarantined count
coverage gaps
source-specific anomalies
```

For legacy migration also compare, where meaningful:

```text
legacy row counts
new canonical version counts
sampled security/date/value pairs
known historical edge cases
intentional semantic differences
```

Exact count equality is not required when the new design intentionally separates:

```text
business revisions
publication evidence
raw observations
```

but every material discrepancy must be explained.

## 35.10 Real-Data PIT Spot Checks

Use imported real records for permanent high-risk PIT regressions.

Include cases such as:

```text
late monthly-revenue publication
Q4 financial filing before/after publication
financial revision across knowledge cutoffs
security market transfer
TDCC signed adjustment/profile completeness
institutional/margin lot-to-share normalization
late historical backfill under System PIT
```

These complement synthetic fixture-based tests.

## 35.11 Import Manifest

Every pilot or bulk import must emit an auditable manifest/report.

Recommended fields:

```text
import ID
git commit
adapter/parser versions
source identifiers
security/date scope
start/end time
configuration fingerprint
input/raw hashes where practical
business/evidence/observation counts
warning/error/quarantine counts
reconciliation result
```

Do not include credentials or secrets.

## 35.12 Quarantine / Fail-Loudly Rule

Do not coerce suspicious source data merely to finish a backfill.

Examples requiring rejection, quarantine, or an explicit policy:

```text
unknown unit
invalid security identity
unmappable market
invalid XBRL context
ambiguous EPS basis
incomplete TDCC distribution
unsupported publication evidence
unexplained impossible negative value
```

Preserve the raw artifact and failure reason.

## 35.13 Recommended Import Order

Prefer dependency-aware import ordering:

```text
1. security identity / historical metadata
2. daily market data
3. monthly revenue
4. financial / XBRL
5. TDCC
6. institutional / margin / short / SBL
7. market indices
8. corporate actions
9. official valuation
```

A domain may be imported earlier when its dependencies are already satisfied, but do not create placeholder identities that later need silent reinterpretation.

## 35.14 Phase Boundary

Phase 9 imports observed/source data.

Do not implement canonical derived calculations here:

```text
MA / RSI / MACD
shareholding concentration
TTM EPS
ROE / ROA
computed PE / PB
margin pressure
short-interest score
```

Those belong to Phase 10.

Redis/cache is not a prerequisite for import or backfill.

## Acceptance Criteria

- [ ] real adapters exist for every supported v1 observed domain/source
- [ ] every adapter uses explicit source-unit/time/identity semantics
- [ ] external raw artifacts are retained before normalization where available
- [ ] legacy migration has an auditable export/manifest
- [ ] historical publication times are proven or stored as unknown
- [ ] normal backfill does not backdate System-PIT ingestion time
- [ ] repeated imports are idempotent at business/evidence identity
- [ ] repeated observations preserve provenance
- [ ] representative pilot import passes end to end
- [ ] real-data PIT spot checks pass
- [ ] per-domain reconciliation reports exist
- [ ] unexplained anomalies are quarantined/reported
- [ ] historical coverage is documented per source/domain
- [ ] restart/resume behavior is tested
- [ ] no canonical derived calculator is implemented in this phase
- [ ] Redis is not required
- [ ] Phase 9 acceptance report records imported coverage and reconciliation results

---

# 36. Phase 10 — Canonical Derived Datasets

## Goal

Implement deterministic reusable calculations shared by downstream repositories.

Initial canonical domains:

```text
technical indicators
shareholding concentration
valuation metrics
margin metrics
short-interest / SBL metrics
```

Examples:

```text
MA5 / MA20 / MA60
historical returns
historical volatility
RSI / MACD if standardized

large/middle/small holder concentration

TTM EPS
canonical ROE/ROA/margins
computed PE/PB where formula is standardized

margin usage ratios
short-interest ratios
SBL pressure metrics
```

Only formulas with stable cross-repo meaning belong here.

Model-specific combinations remain downstream.

## Required Rules

- explicit `derivation_version`
- deterministic formula specification
- PIT-safe input resolution
- input lineage/fingerprint
- no use of future inputs
- `computed_at` is provenance, not market publication time
- materialized and virtual computation must return equivalent semantics

Acceptance criteria:

- [ ] each canonical metric has documented formula/version
- [ ] changing a formula requires a new derivation version
- [ ] identical PIT inputs + derivation version produce identical results
- [ ] derived market visibility inherits only from valid PIT inputs
- [ ] both downstream repos can consume the same canonical definition
- [ ] model-specific features are excluded from this layer
- [ ] legacy calculator outputs selected for v1 can be reproduced or intentionally superseded

---

# 37. Phase 11 — Standardize Public REST Contract

## Goal

Expose already-correct observed and canonical-derived dataset capabilities through a stable API.

Standardize:

```text
routing
request schemas
PIT context schemas
source selection
derivation-version selection where applicable
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
derivation_version
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
- [ ] derived responses identify derivation version
- [ ] invalid PIT combinations fail loudly
- [ ] API works with `CACHE_BACKEND=none`

---

# 38. Phase 12 — Optional Cache Abstraction

## Goal

Add caching without changing resolver or derivation semantics.

Implement:

```text
Cache protocol/interface
NullCache
cache key canonicalizer
codec
cache policy
```

Start with `NullCache`.

Then add integration points above resolved observed/derived query execution.

Acceptance criteria:

- [ ] default/no-cache mode remains fully functional
- [ ] cache layer contains no PIT business rules
- [ ] cache key includes all temporal/source/derivation semantics
- [ ] cache hit returns the same response schema/provenance as a miss

---

# 39. Phase 13 — Redis Backend

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

Required cache identity tests:

```text
different information_as_of -> different identity
different knowledge_as_of -> different identity
different system_as_of -> different identity
different source -> different identity
different derivation_version -> different identity
resolver/response version bump -> different namespace
```

Acceptance criteria:

- [ ] Redis is not required to start Data Center in none mode
- [ ] Redis outage does not fail correct PostgreSQL-backed queries
- [ ] cache-on/cache-off result equality is tested
- [ ] no cache key omits PIT context
- [ ] no derived cache key omits derivation version
- [ ] Redis persistence is not required
- [ ] cache metrics expose hit/miss/error behavior

---

# 40. Phase 14 — Operational Tooling and Observability

## Goal

Make ingestion, derivation, PIT reads, PostgreSQL behavior, and cache behavior observable.

Include metrics/logging for:

```text
ingest runs
derived calculation runs
PIT query latency
PostgreSQL query latency
cache hits
cache misses
cache errors
cache bypasses
result sizes
source failures
seal failures
derivation failures
```

Operational diagnostics should make it possible to determine whether Redis/materialization is actually reducing PostgreSQL workload / SSD reads.

Useful host/database observation may include:

```text
PostgreSQL blks_read / blks_hit
iostat
query timing
cache hit ratio
```

Do not make performance assumptions without measurement.

---

# 41. Phase 15 — Full PIT Regression and CI Gate

## Goal

Consolidate all permanent correctness tests.

CI must include PostgreSQL 18.

Core test families:

```text
schema/migration tests
legacy-domain inventory/schema coverage tests
append-only tests
seal/concurrency tests
business revision tests
publication evidence tests
two-clock market PIT tests
system PIT tests
source-level capability tests

daily market data tests
revenue tests
XBRL tests
TDCC tests
institutional/margin/SBL tests
index/corporate-action/valuation tests
derived-dataset tests

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
- [ ] schema/domain coverage regressions block merge
- [ ] derived-version regressions block merge
- [ ] cache regressions block merge
- [ ] PostgreSQL 18 is verified
- [ ] all dataset-specific suites are included
- [ ] cache disabled mode is always tested

---

# 42. Phase 16 — Downstream Readiness

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
daily market data
monthly revenue
financials
actual EPS history
TDCC
institutional/chip-flow source data
margin/SBL
market indices
corporate actions
official valuation

canonical technical indicators
canonical concentration metrics
canonical valuation metrics
canonical margin/short-interest metrics
```

Downstream systems must not need to know whether:

```text
a derived value was materialized or computed on demand
Redis exists
```

Acceptance criteria:

- [ ] downstream code uses only API/SDK
- [ ] no downstream DB credentials are required
- [ ] no downstream Redis credentials are required
- [ ] both downstream repos can share canonical derived definitions
- [ ] changing CACHE_BACKEND requires no downstream code change
- [ ] model-specific features remain downstream-owned

---

# 43. Suggested Source Layout

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
│   ├── data_domain_inventory.md
│   ├── derived_data.md
│   ├── cache.md
│   ├── phase_reports/
│   └── decisions/
├── src/
│   └── stock_data_center/
│       ├── api/
│       ├── services/
│       ├── pit/
│       ├── derived/
│       │   ├── technical/
│       │   ├── ownership/
│       │   ├── valuation/
│       │   └── margin/
│       ├── cache/
│       │   ├── base.py
│       │   ├── null_cache.py
│       │   ├── redis_cache.py
│       │   ├── keys.py
│       │   ├── codec.py
│       │   └── policy.py
│       ├── storage/
│       ├── ingestion/
│       │   ├── adapters/
│       │   ├── backfill/
│       │   ├── reconciliation/
│       │   └── manifests/
│       └── domain/
└── tests/
    ├── unit/
    ├── integration/
    ├── pit/
    └── regression/
```

---

# 44. Definition of Done for v1

Version 1 is complete when:

- [ ] PostgreSQL 18 is the sole authoritative data store
- [ ] market PIT supports `information_as_of` + `knowledge_as_of`
- [ ] system PIT supports exact ingestion reconstruction
- [ ] business revisions and publication-evidence revisions are separate
- [ ] complex aggregates are concurrency-safe and seal-protected
- [ ] source-level PIT capability is enforced
- [ ] XBRL full context identity is supported
- [ ] raw provenance is auditable

- [ ] all known v1 legacy/source domains have an explicit storage/ownership mapping
- [ ] all required observed domains have PIT-safe storage/query support
- [ ] real source adapters exist for supported v1 observed domains
- [ ] representative pilot and historical backfill have auditable manifests/reconciliation
- [ ] source-native units are normalized before canonical storage
- [ ] backfill does not invent publication time or fake System-PIT history
- [ ] imported raw artifacts/provenance are auditable
- [ ] reusable canonical derived datasets have versioned derivation semantics
- [ ] model-specific features remain outside Data Center

- [ ] REST API is stable
- [ ] Redis is optional
- [ ] Data Center works without Redis
- [ ] Redis failure falls back safely
- [ ] cache keys are PIT-aware
- [ ] derived cache keys include derivation identity
- [ ] cache-on/cache-off results are identical
- [ ] full PIT/domain/derived/cache regression suite runs in CI
- [ ] downstream ML repos need neither DB nor Redis access

---

# 45. Core Design Principles

1. Correct historical visibility before convenience.
2. Phase 1 defines the complete known v1 storage contract, not only the first few datasets.
3. PostgreSQL is truth; Redis is disposable optimization.
4. Redis may improve performance but may never change semantics.
5. Market time and Data Center knowledge time are separate clocks.
6. System PIT means actual complete ingestion history.
7. Business revisions and evidence revisions are separate.
8. Complex aggregates become visible only when sealed and remain correct under concurrency.
9. Sources remain separate unless an explicit reconciliation policy exists.
10. Data Center may own deterministic cross-repo canonical derived datasets.
11. Every canonical derived dataset must have a derivation version and PIT-safe input lineage.
12. `computed_at` is derivation provenance, not market publication time.
13. Model-specific features remain in ML repositories.
14. Cache resolved results, not database implementation details.
15. Every cache identity must include all PIT/source/derivation semantics.
16. Downstream ML systems never reproduce PIT rules.
17. Real-data import is raw-first, source-explicit, idempotent, and reconciliation-driven.
18. Historical backfill never invents publication time or System-PIT history.
19. Full-market backfill follows a successful representative pilot.
20. Measure SSD/database behavior before claiming cache benefit.
