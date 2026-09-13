# stock-data-center ROADMAP

> Delivery is now tracked by pull request. Historical phase names are retained only as legacy references.

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

# 26. Delivery Model — Pull-Request-Based Roadmap

The project is delivered and tracked by **pull request**, not by implementation phase.

The earlier phase model was useful while defining architecture, but it became misleading once
real-source ingestion required multiple independently reviewable PRs. From this point forward:

```text
architecture / invariants / domain contracts
                ↓
        stable long-lived rules

PR #N
    one dominant delivery goal
    explicit dependencies
    explicit in-scope / out-of-scope
    permanent regression tests
    acceptance report / evidence
                ↓
PR #N+1
```

A PR is the unit of:

```text
planning
implementation
review
correctness approval
merge
rollback reasoning
historical traceability
```

Do not claim that a broad "phase" is complete merely because one PR under that topic merged.

Historical ADRs, reports, branch names, or comments may retain old `Phase N` terminology.
They are historical artifacts and do not need to be renamed solely to match this roadmap.

## 26.1 PR Planning Rules

Every planned implementation PR must define:

```text
goal
dependencies
source/data contract
schema impact
PIT impact
provenance impact
migration impact
test plan
acceptance criteria
explicit out-of-scope work
```

A PR should have one dominant reason to exist.

If review reveals a second independent architectural problem, prefer either:

```text
fix it inside the current PR when it is required for that PR's correctness
or
create a dedicated follow-up PR when it is independently reviewable
```

Do not expand a PR merely because adjacent work is convenient.

## 26.2 PR Status Vocabulary

Use only:

```text
MERGED
IN REVIEW
PLANNED
BLOCKED
SUPERSEDED
```

`MERGED` means the PR's acceptance criteria passed and the result is on `main`.

`IN REVIEW` means implementation exists but merge is not yet approved.

`PLANNED` means scope is defined but implementation has not been accepted.

`BLOCKED` means a named dependency or correctness issue prevents implementation/merge.

`SUPERSEDED` means another PR intentionally replaced the planned work.

## 26.3 Planned PR Numbers

The numbers below are the intended next GitHub PR numbers.

If an emergency/hotfix PR consumes a number, do not force history to match this document.
Instead:

```text
preserve the actual GitHub PR number
update this ledger immediately
keep dependency ordering explicit
```

The semantic order matters more than preserving a predicted number.

---

# 27. PR Ledger — Completed and Current Work

Status date: 2026-09-13.

| PR | Status | Delivery |
|---|---|---|
| #1 | MERGED | Versioned PostgreSQL PIT schema and v1 storage contract |
| #2 | MERGED | Core Market/System PIT resolver |
| #3 | MERGED | Security metadata + daily-market writer/service contracts |
| #4 | MERGED | PIT-safe monthly revenue writer/service contract |
| #5 | MERGED | Financial/XBRL sealed aggregate + EPS contract |
| #6 | MERGED | TDCC snapshot/distribution contract |
| #7 | MERGED | Institutional, margin, short-selling, and SBL source-data contracts |
| #8 | MERGED | Market indices, corporate actions, and official valuation contracts |
| #9 | MERGED | Raw-first TWSE/TPEx daily-market ingestion pilot |
| #10 | MERGED | Current TWSE/TPEx security metadata ingestion |
| #11 | MERGED | Authoritative security listing/delisting/venue lifecycle history |

The merged PRs establish the storage/PIT foundation and the first production-like raw-first
ingestion lifecycle. They do **not** mean that all historical source datasets are already
backfilled or analysis-ready.

## 27.1 PR #1 — Versioned PIT Database Schema

Delivered:

```text
append-only observed histories
publication-evidence history
raw / ingest provenance
sealed aggregate infrastructure
source-level capability storage
XBRL dimensional identity
canonical derived-data definition contract
```

## 27.2 PR #2 — Core PIT Resolver

Delivered cache-free:

```text
Market PIT
System PIT
publication-evidence resolution
source/capability selection
sealed aggregate visibility
provenance-aware results
```

## 27.3 PR #3 — Security Metadata and Daily Market Contracts

Delivered:

```text
stable security_code identity
effective-dated venue/security metadata
daily market append-only writer
source/revision-aware daily reads
PIT-safe historical universe service contract
```

This PR defined the domain contract; it did not complete real historical source ingestion.

## 27.4 PR #4 — Monthly Revenue Contract

Delivered:

```text
canonical revenue units
business revision vs evidence separation
repeated-fetch lineage
PIT-safe period/history reads
```

Real MOPS historical adapter/backfill remains future work.

## 27.5 PR #5 — Financial / XBRL Contract

Delivered:

```text
sealed filing aggregate
QName-aware facts
full context identity
explicit EPS basis
curated summary linkage
PIT-safe filing/EPS reads
```

Real MOPS/XBRL historical source backfill remains future work.

## 27.6 PR #6 — TDCC Contract

Delivered:

```text
snapshot parent
distribution children
seal-based visibility
repeat-fetch lineage
PIT-safe snapshot/history reads
```

Real TDCC external adapter/backfill remains future work.

## 27.7 PR #7 — Institutional / Margin / Short / SBL Contracts

Delivered normalized source-data contracts for:

```text
institutional investor flow
institutional holding/proxy data
margin trading
short selling
securities lending / SBL
```

Real historical external adapters/backfill remain future work.

## 27.8 PR #8 — Indices / Corporate Actions / Official Valuation Contracts

Delivered first-class storage and PIT-safe service contracts for:

```text
market indices
corporate actions
official valuation
```

The corporate-action contract is intentionally revisited below because explicit Taiwan
stock splits, reverse splits, 盈餘配股, 資本公積配股, and adjusted-price semantics require
a stronger contract before historical prices are analysis-ready.

## 27.9 PR #9 — Raw-First Daily-Market Pilot

Delivered the shared production-like import lifecycle foundation:

```text
fetch
→ durable content-addressed raw artifact
→ captured checkpoint
→ hash-verified offline resume
→ normalize
→ trusted writer
→ manifest/reconciliation
→ succeeded checkpoint
```

Also delivered:

```text
TWSE STOCK_DAY pilot
TPEx tradingStock pilot
quarantine
operational-failure resume semantics
same-resource concurrency protection
raw-store identity/fingerprint
```

This was a bounded pilot, not full historical daily-market backfill.

## 27.10 PR #10 — Current Security Metadata Ingestion

Delivered:

```text
TWSE current-company snapshot
TPEx current-company snapshot
stable identity registration
current observed venue/name/industry state
same-date A→B→A System-PIT reassertion correctness
shared RawFirstImporter reuse
```

Important invariant:

```text
current snapshot absence ≠ delisting
current observed metadata is not backdated to original listing date
```

## 27.11 PR #11 — Authoritative Security Lifecycle History

Status: **MERGED**

Goal:

```text
official TWSE listing history
official TWSE delisting history
official TPEx listing history
official TPEx delisting history
same security identity across venue transfer
```

Required before merge:

- independent TWSE and TPEx histories remain authoritative
- no historical `published_at` is invented
- same-code/same-date transfer evidence can reconcile across sources
- final transfer reconciliation is deterministic and does not depend on import order
- both `TPEx exit → TWSE entry` and reverse ingestion order converge to the same final result
- raw-first / checkpoint / quarantine / idempotency contracts remain intact

Out of scope:

```text
corporate-action expansion
trading calendar
bulk daily-price backfill
other domain adapters
canonical derived calculations
Redis/cache
```

---

# 28. Next PRs — Historical Price Correctness Gate

The next group exists to prevent raw historical prices from being misinterpreted as real
economic gains/losses when a discontinuity is caused by a corporate action.

The key rule is:

> Raw official OHLC may be stored early, but historical price data is not
> **analysis-ready** for returns, technical indicators, or ML features until
> corporate-action reconciliation exists.

## PR #12 — Harden Taiwan Corporate-Action Contract

Status: **PLANNED**

Depends on:

```text
PR #8 corporate-action base contract
PR #11 stable historical security lifecycle
```

Goal:

Make Taiwan corporate actions explicit enough to explain mechanical price/share changes.

In scope:

```text
cash dividend

earnings stock dividend / 盈餘配股
capital-surplus stock dividend / 資本公積配股

stock split
reverse split

rights issue / cash capital increase
capital reduction

ex-right
ex-dividend
combined ex-right/ex-dividend
```

Required quantities, where officially available:

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

Rules:

```text
stock dividend ≠ stock split
stock split ≠ capital reduction
observed event type must remain source-faithful
raw OHLC must never be rewritten
```

For split-style events, prefer:

```text
old_shares
new_shares
```

over a provider-dependent ambiguous `split_ratio`.

Acceptance criteria:

- [ ] schema can represent stock split and reverse split explicitly
- [ ] 盈餘配股 and 資本公積配股 are distinguishable observed events
- [ ] rights/capital-reduction semantics are explicit
- [ ] migration round-trip is safe or explicitly guarded when old schema cannot represent new history
- [ ] DB constraints reject impossible event values
- [ ] source/business hashes include all semantic quantities
- [ ] raw price tables remain unchanged
- [ ] permanent regressions cover representative event types

Out of scope:

```text
external source backfill
adjusted-price calculation
technical indicators
```

## PR #13 — Official Corporate-Action Raw-First Pilot

Status: **PLANNED**

Depends on:

```text
PR #12
PR #9 shared raw-first lifecycle
```

Goal:

Prove that real official Taiwan corporate-action evidence can be captured and normalized
without inventing event semantics.

In scope:

```text
official TWSE/TPEx source adapters where available
durable raw artifacts
checkpoint/resume
quarantine
source-unit normalization
publication evidence
official reference-price capture
representative real events:
    cash dividend
    stock dividend
    stock split or equivalent share-count event
    capital reduction / rights where source coverage allows
```

Acceptance criteria:

- [ ] representative live official artifacts parse successfully
- [ ] exact source terms are retained
- [ ] unknown historical publication time remains unknown
- [ ] refetch is idempotent at business identity while preserving observation lineage
- [ ] official reference price is preserved as observed data when available
- [ ] no adjusted price is generated in this PR

## PR #14 — Authoritative Taiwan Trading Calendar and Coverage Validator

Status: **PLANNED**

Depends on:

```text
PR #9
PR #11
```

Goal:

Replace:

```text
coverage_validation = not_evaluated
coverage_gaps = null
```

with authoritative market-calendar-aware coverage evaluation.

In scope:

```text
TWSE/TPEx trading dates
holiday/non-trading-day handling
market/source coverage expectations
date-range completeness evaluator
coverage manifest/report
```

Acceptance criteria:

- [ ] expected trading dates come from an explicit authoritative contract
- [ ] weekend/holiday absence is not reported as a data gap
- [ ] actual missing trading dates are reported
- [ ] current and historical coverage can be compared deterministically
- [ ] coverage does not rely on today's security universe

## PR #15 — Historical Corporate-Action Backfill

Status: **PLANNED**

Depends on:

```text
PR #12
PR #13
PR #14 where calendar context is required
```

Goal:

Backfill the supported corporate-action history before raw price history is declared
analysis-ready.

In scope:

```text
historical corporate-action import
resume/idempotency
coverage report
event reconciliation
official reference-price reconciliation
unsupported/ambiguous-event quarantine
```

Acceptance criteria:

- [ ] supported action history has documented source/date coverage
- [ ] unexplained source ambiguity is quarantined, not guessed
- [ ] known stock dividend/split/capital-reduction examples reconcile
- [ ] event history remains append-only and source-aware
- [ ] historical System PIT uses actual trusted ingestion time

## PR #16 — Full Daily-Market Historical Backfill and Discontinuity Reconciliation

Status: **PLANNED**

Depends on:

```text
PR #11
PR #14
PR #15
```

Goal:

Run production-scale TWSE/TPEx historical daily-market backfill and prove that major
price discontinuities are either explained or explicitly reported.

In scope:

```text
full supported historical range
restartable/backpressure-safe execution
coverage validation
TWSE/TPEx source reconciliation
large raw-return anomaly scan
corporate-action lookup around discontinuities
per-security/per-period reconciliation report
```

Required classification:

```text
explained_by_corporate_action
explained_by_other_documented_market_event
unexplained_anomaly
```

Acceptance criteria:

- [ ] historical source/date coverage is documented
- [ ] restart/resume is tested on long-running backfill
- [ ] missing trading dates are explicit
- [ ] large discontinuities are not silently smoothed
- [ ] raw prices remain exactly source-faithful
- [ ] unexplained anomalies remain visible
- [ ] price history may now be declared raw-history complete for supported coverage

This PR still does **not** create adjusted OHLC.

---

# 29. Planned PRs — Remaining Observed-Source Production Ingestion

These PRs turn the already-defined storage/service contracts from PRs #4–#8 into real
official-source ingestion and historical coverage.

## PR #17 — Source Capability Hook and Adapter Policy Cleanup

Status: **PLANNED**

Depends on:

```text
PR #9 shared lifecycle
```

Goal:

Move source/domain capability policy out of generic raw-first orchestration before many
heterogeneous adapters reuse it.

In scope:

```text
domain-specific dataset/source registration hook
supports_market_pit
supports_system_pit
publication_time_quality
accepted evidence types
source capability validation
```

Acceptance criteria:

- [ ] generic lifecycle owns orchestration, not domain truth
- [ ] source A capability cannot leak to source B
- [ ] existing daily/security adapters retain identical behavior
- [ ] regression suite proves no PIT semantic change

## PR #18 — MOPS Monthly Revenue Adapter and Historical Backfill

Status: **PLANNED**

Depends on:

```text
PR #17
PR #4
PR #11
```

Goal:

Ingest real MOPS monthly revenue with publication evidence and historical revisions.

Acceptance criteria:

- [ ] real official adapter exists
- [ ] canonical amount units are preserved
- [ ] revisions/evidence are separated
- [ ] delayed publication and knowledge-cutoff cases are tested
- [ ] historical coverage/reconciliation report exists

## PR #19 — MOPS Financial/XBRL Adapter and Historical Backfill

Status: **PLANNED**

Depends on:

```text
PR #17
PR #5
PR #11
```

Goal:

Ingest real historical financial filings/XBRL while preserving filing aggregate identity,
dimensions, revisions, and publication evidence.

Acceptance criteria:

- [ ] real official filing/XBRL adapter exists
- [ ] sealed aggregate is built only after complete normalization
- [ ] annual/YTD/quarter EPS basis remains explicit
- [ ] dimensional facts retain correct context identity
- [ ] Q4 visibility follows evidence, not calendar assumptions
- [ ] historical filing coverage/reconciliation report exists

## PR #20 — TDCC Official Adapter and Historical Backfill

Status: **PLANNED**

Depends on:

```text
PR #17
PR #6
PR #11
```

Goal:

Ingest real TDCC distribution snapshots and historical coverage.

Acceptance criteria:

- [ ] official source adapter exists
- [ ] snapshot/bucket aggregate is sealed atomically
- [ ] snapshot effective date does not grant false publication visibility
- [ ] backfill preserves actual System-PIT ingestion time
- [ ] coverage/reconciliation report exists

## PR #21 — Institutional Investor Official Adapters and Backfill

Status: **PLANNED**

Depends on:

```text
PR #17
PR #7
PR #11
```

Goal:

Ingest official TWSE/TPEx institutional flow datasets.

Acceptance criteria:

- [ ] TWSE/TPEx quantities normalize to canonical units
- [ ] source histories remain independent
- [ ] cross-source differences are reported, not averaged
- [ ] historical coverage/reconciliation report exists

## PR #22 — Margin / Short-Selling / SBL Official Adapters and Backfill

Status: **PLANNED**

Depends on:

```text
PR #17
PR #7
PR #11
```

Goal:

Ingest official financing and securities-lending histories.

Acceptance criteria:

- [ ] unit conversion is explicit
- [ ] stock/flow semantics are explicit per source field
- [ ] impossible negative/balance values fail loudly
- [ ] historical coverage/reconciliation reports exist

## PR #23 — Market Index and Official Valuation Adapters / Backfill

Status: **PLANNED**

Depends on:

```text
PR #17
PR #8
```

Goal:

Complete real-source ingestion for:

```text
market indices
official PE/PB/dividend-yield style source metrics
```

Acceptance criteria:

- [ ] index identity/history remains PIT-safe
- [ ] index metadata revisions do not change stable index identity
- [ ] official valuation is stored as observed source data
- [ ] computed valuation remains a later derived domain
- [ ] coverage/reconciliation report exists

## PR #24 — Legacy `my_stock_project` / `stock_db` Migration and Reconciliation

Status: **PLANNED**

Depends on:

```text
PR #16
PR #18–#23 for the domains being compared
```

Goal:

Migrate useful legacy history without turning legacy current state into authoritative
historical truth.

In scope:

```text
read-only legacy export
manifest/hash
mapping to new identities
documented transforms
reconciliation against official-source backfills
quarantine for ambiguous legacy rows
```

Rules:

```text
legacy data may fill documented source gaps
legacy data must not silently override better official history
legacy migration must preserve provenance
```

Acceptance criteria:

- [ ] every migrated legacy domain has an explicit disposition
- [ ] migration is restartable/idempotent
- [ ] differences are classified/explained
- [ ] no legacy current row is backdated into fake historical knowledge

---

# 30. Planned PRs — Canonical Derived Data

Observed/source data and canonical derived data remain separate.

## PR #25 — Adjustment Factors, Adjusted Price, and Total Return

Status: **PLANNED**

Depends on:

```text
PR #15
PR #16
```

Goal:

Create deterministic versioned price-continuity datasets without modifying raw prices.

Required layering:

```text
raw official OHLC
+ PIT-safe corporate actions
→ versioned share/price adjustment factors
→ adjusted OHLC
→ total-return series
```

In scope:

```text
share adjustment factor
price adjustment factor
adjusted OHLC
total-return factor/series
derivation version
input lineage/fingerprint
raw vs adjusted query contract
```

Acceptance criteria:

- [ ] raw OHLC is unchanged
- [ ] adjustment convention is documented/versioned
- [ ] same PIT inputs + derivation version produce identical outputs
- [ ] no future corporate action leaks backward
- [ ] official ex-right/ex-dividend reference prices reconcile with derived results where available
- [ ] representative split and 盈餘配股 examples produce economically continuous adjusted returns

## PR #26 — Canonical Reusable Derived Metrics

Status: **PLANNED**

Depends on:

```text
PR #18–#25 as required by each metric
```

Goal:

Implement only stable cross-repository derived definitions.

Initial domains:

```text
historical returns
MA / volatility
RSI / MACD if standardized

shareholding concentration

TTM EPS
canonical ROE/ROA/margins
computed PE/PB where formula is standardized

margin usage ratios
short-interest / SBL pressure metrics
```

Rules:

```text
explicit derivation_version
PIT-safe input resolution
input lineage/fingerprint
computed_at is provenance, not publication time
materialized and virtual semantics must match
```

Model-specific feature engineering remains downstream.

---

# 31. Planned PRs — API, SDK, Cache, Operations, and Cutover

## PR #27 — Public REST API v1

Status: **PLANNED**

Depends on:

```text
stable observed-source service contracts
PR #25/#26 for derived endpoints included in v1
```

Goal:

Expose correct Data Center semantics without exposing tables.

Standardize:

```text
information_as_of
knowledge_as_of
system_as_of
source
derivation_version

errors
pagination
provenance
OpenAPI
```

Acceptance criteria:

- [ ] API works with cache disabled
- [ ] provenance is returned
- [ ] invalid PIT combinations fail loudly
- [ ] endpoints call domain/PIT services rather than reimplementing SQL semantics

## PR #28 — Python SDK and Downstream Integration Contract

Status: **PLANNED**

Depends on:

```text
PR #27
```

Goal:

Give downstream repositories a stable client contract.

Target consumers:

```text
my_stock_project
stock-eps-model
stock-model-selection
future backtest/screener/AI consumers
```

Acceptance criteria:

- [ ] no downstream PostgreSQL credentials are required
- [ ] no downstream Redis credentials are required
- [ ] SDK exposes explicit PIT context
- [ ] representative downstream query integration tests pass

## PR #29 — Optional Cache Abstraction

Status: **PLANNED**

Depends on:

```text
PR #27
```

Goal:

Add caching without changing resolver semantics.

Implement:

```text
Cache protocol
NullCache
cache-key canonicalizer
codec/policy
```

Acceptance criteria:

- [ ] `CACHE_BACKEND=none` is fully functional
- [ ] cache keys include all PIT/source/derivation identity
- [ ] cold/no-cache results are equivalent

## PR #30 — Redis Backend

Status: **PLANNED**

Depends on:

```text
PR #29
```

Goal:

Add optional Redis query-result caching.

Acceptance criteria:

- [ ] Redis outage falls back to authoritative PostgreSQL execution
- [ ] cache-on/cache-off results are identical
- [ ] short connection/socket timeouts exist
- [ ] Redis persistence is not required for correctness
- [ ] cache hit/miss/error/bypass metrics exist

## PR #31 — Operational Tooling and Observability

Status: **PLANNED**

Depends on:

```text
production ingestion and API paths
```

Goal:

Make import/query/derivation/cache behavior operationally diagnosable.

Include:

```text
ingest run metrics
backfill progress
quarantine reporting
PIT query latency
PostgreSQL latency
derivation metrics
cache metrics
source failures
coverage gaps
reconciliation status
```

Do not optimize based on assumptions; measure PostgreSQL/SSD/cache behavior.

## PR #32 — Full Correctness CI Gate

Status: **PLANNED**

Depends on:

```text
all v1 critical domain implementations
```

Goal:

Make permanent correctness regression a merge gate.

CI must cover:

```text
PostgreSQL 18
Alembic upgrade
Alembic downgrade/guarded downgrade semantics
alembic check

append-only rules
seal/concurrency rules
Market/System PIT
publication evidence
source capability
cross-source reconciliation
raw-first restart
corporate actions
adjusted price
derived datasets
API
cache equivalence
```

Acceptance criteria:

- [ ] critical PIT regression blocks merge
- [ ] schema/domain drift blocks merge
- [ ] source-import-order reconciliation regression blocks merge
- [ ] adjusted-price correctness regression blocks merge
- [ ] cache-disabled mode is always tested

## PR #33 — `my_stock_project` Cutover and Data Center v1 Release

Status: **PLANNED**

Depends on:

```text
PR #24
PR #27
PR #28
PR #32
```

Goal:

Make `stock-data-center` the authoritative data provider for the original project and
declare Data Center v1 ready.

Cutover rule:

```text
my_stock_project
    must consume API/SDK

my_stock_project
    must not maintain a competing authoritative stock database
```

Acceptance criteria:

- [ ] required `my_stock_project` reads work through API/SDK
- [ ] direct legacy DB dependencies are removed or explicitly transitional
- [ ] old/new result reconciliation is documented
- [ ] historical PIT examples are validated end to end
- [ ] raw vs adjusted price usage is explicit in consumers
- [ ] v1 release notes document supported source/date coverage
- [ ] unresolved source gaps/anomalies are documented rather than hidden

---

# 32. Cross-PR Acceptance Rules

These rules apply to every PR above.

## 32.1 Temporal Correctness

Never invent:

```text
historical published_at
historical ingested_at
historical knowledge
```

If publication time is unknown:

```text
published_at = NULL
```

Market PIT must not silently treat unknown evidence as known.

## 32.2 Raw-First Ingestion

For external sources where bytes/artifacts are available:

```text
fetch
→ durable raw artifact
→ checkpoint
→ parse
→ normalize
→ canonical write
```

Do not normalize first and hope to reconstruct provenance later.

## 32.3 Failure Classification

Use quarantine for:

```text
invalid source data
ambiguous source semantics
domain validation failure
unexplained impossible values
```

Do not quarantine ordinary:

```text
database outage
network infrastructure failure after durable capture
programming bug
writer operational failure
```

Operational failures should remain resumable from retained raw bytes when possible.

## 32.4 Cross-Source Reconciliation

Final reconciliation truth must be reproducible from stored source histories.

It must not depend on:

```text
which source imported first
worker scheduling
current process state
```

If a result is provisional, label it provisional and provide a deterministic final
reconciliation pass.

## 32.5 Migration Safety

A downgrade must either:

```text
safely represent all stored history
or
fail explicitly before mutation when the previous schema cannot represent it
```

Never delete/collapse valid append-only PIT history merely to make downgrade succeed.

## 32.6 Corporate-Action / Price Rule

Raw prices are source facts.

Never "fix" a historical chart by rewriting raw OHLC.

Continuity belongs in:

```text
corporate-action adjustment factors
adjusted OHLC
total-return series
```

A large price jump alone is not evidence of a corporate action.

## 32.7 Scope Discipline

Each PR must state what it intentionally does not do.

Examples:

```text
no Redis in an ingestion PR
no derived calculator in a raw-source PR
no unrelated performance refactor in a correctness fix
no silent schema expansion for convenience
```

## 32.8 Review / Merge Evidence

Before merge, each PR should provide as applicable:

```text
focused regression suite
full test suite
Ruff/lint
git diff --check
Alembic check
migration round-trip or guarded downgrade test
opt-in live-source verification
acceptance/reconciliation report
```

# 33. Suggested Source Layout

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

# 34. Definition of Done for v1

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

# 35. Core Design Principles

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
