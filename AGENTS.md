# AGENTS.md

> Delivery is PR-driven. `ROADMAP.md` is authoritative for PR number, scope, dependencies, acceptance criteria, blockers, and out-of-scope work. Historical Phase terminology may remain in old ADRs/reports but must not drive new implementation planning.

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

Highest-priority requirement:

> Historical correctness must not depend on current database state, caller discipline, derivation timing, source import order, guessed source identity, or cache availability.

---

# 1. Canonical Roadmap and Delivery Unit

The canonical roadmap is:

```text
ROADMAP.md
```

Do not maintain competing version-suffixed roadmaps in the repository root.

The delivery unit is a pull request.

Before implementation, identify the current PR and read its:

```text
goal
dependencies
status / blockers
in-scope work
out-of-scope work
schema/PIT/provenance impact
migration impact
tests
acceptance criteria
```

Work one PR at a time.

If a required correctness criterion fails, stop. Do not reinterpret the requirement simply to keep implementation moving.

Use only:

```text
MERGED
IN REVIEW
PLANNED
BLOCKED
SUPERSEDED
```

For new PR acceptance evidence, prefer:

```text
docs/pr_reports/pr-<N>-acceptance-report.md
```

Each required criterion must be PASS/FAIL with concrete evidence.

---

## Current PR-Based Sequence

ROADMAP §20 is the authoritative ledger. Its current snapshot identifies PRs #1–#12 as MERGED and labels PR #13 as `THIS PR` (a contextual marker, not an additional status value):

```text
PR #11  authoritative security lifecycle history        MERGED
PR #12  Taiwan corporate-action contract               MERGED
PR #13  source-reality rebuild of ROADMAP/AGENTS/audit   THIS PR
PR #14  source-reality alignment                       PLANNED
PR #15  availability-time evidence policy              PLANNED (owner decision / ADR-0020)
PR #16  trading calendar + coverage validator          PLANNED
PR #17  whole-market daily prices                      PLANNED
PR #18  market indices + official valuation            PLANNED
PR #19  exchange corporate-action result feeds         PLANNED
PR #20  institutional flows/summary + foreign holding  PLANNED
PR #21  margin trading + securities lending            PLANNED
PR #22  monthly revenue                               PLANNED
PR #23  financial statements (iXBRL)                   PLANNED
PR #24  TDCC distribution                             PLANNED
PR #25  adjusted prices                               PLANNED
PR #26  canonical derived v1 (legacy calculator ports) PLANNED
PR #27  scheduled forward capture                     PLANNED
PR #28  public REST API v1                            PLANNED
PR #29  Python SDK + downstream integration           PLANNED
PR #30  operations + observability                    PLANNED
PR #31  full correctness CI gate                      PLANNED
PR #32  my_stock_project cutover + v1 release          PLANNED
PR #33  issuer dividend declarations                  PLANNED (depends on #19)
```

`ROADMAP.md` remains authoritative if this snapshot becomes stale.

The dividend-summary pilot that once held the #13 slot was abandoned, and never opened as a pull request: the issuer dividend summary feeds expose no proven correction-stable event identity, and TPEx live data falsified the proposed `(security, dividend_year, period)` identity. Announcement feeds never enter `corporate_action_versions`. Corporate actions come from exchange result feeds under ROADMAP Invariant G(2); the earnings/capital-surplus split comes from the declaration feeds that ROADMAP PR #33 stores as their own domain.

Do not bypass the announcement-feed blocker by inventing another mutable composite key.

## Source-Field Rule

`docs/source_field_audit.md` records which fields the legacy DB, the legacy raw archive, and the official endpoints actually provide.

Do not add, populate, or promise a stored field unless you can name its official endpoint, exact source field label, the date range in which it exists, and its unit conversion. Update the audit in the same PR.

Columns the audit lists as unsourced stay NULL and must not be presented as data.

## v1 Scope and Deferred Work

v1 rebuilds the legacy consumer scope with PIT, normally from 2020-01-02 through cutover. Honor domain-specific history windows in ROADMAP, including PR #33's ROC 107–115 declaration requests.

The security universe is 上市 (`sii`) and 上櫃 (`otc`) only. Where an endpoint offers a market selector, request those two values and reject `rotc` and `pub` at the adapter boundary.

Financial-industry financial statements are excluded from PR #23; those issuers' other datasets remain in scope. Stock tags, the XBRL codebook, and margin market summary are not v1 deliveries. Existing nullable storage contracts are retained, not dropped merely because v1 does not populate them.

ROADMAP §26 distinguishes unavailable source fields, work waiting on a stated trigger, and obtainable work excluded by owner decision. Do not turn these into an unconditional future backlog. In particular, cache abstraction and Redis wait until PR #30 measurements prove API/DB latency insufficient.

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
Redis optional, deferred outside v1 (ROADMAP §26.2)
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

Downstream consumers receive resolved data. Do not move PIT logic into downstream repos.

---

# 4. PostgreSQL Is Authoritative

PostgreSQL is the source of truth. Redis is never authoritative.

Deleting all Redis keys must not change correctness.

---

# 5. Redis Is Optional

Cache implementation is not a v1 requirement. The cache rules in this document apply if the ROADMAP measurement trigger authorizes a later cache PR; they do not authorize adding a cache to current delivery.

When a cache is implemented, both are valid:

```env
CACHE_BACKEND=none
```

and:

```env
CACHE_BACKEND=redis
REDIS_URL=redis://host:6379
```

Do not require Redis to run migrations, ingest data, resolve PIT queries, calculate canonical derived data, or pass core correctness tests.

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

Differences in performance are allowed. Differences in business/PIT output are P0 bugs.

---

# 7. Redis Failure Must Degrade Performance Only

Redis GET failure:

```text
log/metric -> bypass cache -> query PostgreSQL -> return correct result
```

Redis SET failure must not fail a correct response.

Use short connection/socket timeouts. Do not implement long blocking retries in the request path.

---

# 8. Cache Layer Placement

Correct:

```text
API
 -> service
 -> cache
 -> PIT resolver / derived service on miss
 -> PostgreSQL
```

Avoid Redis-specific code inside API routes, SQL repositories, PIT semantics, or derived formulas.

---

# 9. Cache Backends

When cache work is authorized, provide at least:

```text
NullCache
RedisCache
```

Application services depend on the cache interface, not Redis directly.

---

# 10. Cache Key Correctness

Canonical cache identity must include every semantically relevant field:

```text
cache contract version
response schema version
resolver semantics version
dataset/endpoint
business query parameters
source
pit mode
information_as_of
knowledge_as_of
system_as_of
derivation_version where applicable
```

Omitting a relevant PIT/source/derivation parameter is a P0 bug.

Use canonical serialization before hashing.

---

# 11. Cache Namespace Evolution

Semantic changes must not silently reuse old cached results.

Bump relevant namespace/version for changes to:

```text
cache contract
response schema
PIT resolver semantics
source reconciliation semantics
derivation semantics
```

---

# 12. Cache Content

Prefer caching normalized PIT-resolved Data Center results, not PostgreSQL row representations.

---

# 13. Redis Persistence

Redis is disposable RAM cache.

Do not rely on Redis persistence for audit, revision history, evidence, ingestion history, or derivation definitions.

---

# 14. PIT Time Types

Use explicit temporal types:

Market PIT:

```text
information_as_of
knowledge_as_of
```

System PIT:

```text
system_as_of
```

Do not collapse them into ambiguous `date`/`as_of` variables.

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

Do not use `ingested_at` as publication time.

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

---

# 17. Publication Evidence Rule

Business content and publication evidence are separate.

Do not create a business revision merely because publication evidence became known, improved, was corrected, or retracted.

Publication evidence inside Data Center is append-only. Use supersession/retraction records; never update old evidence rows.

---

# 18. Evidence Times

Publication evidence preserves:

```text
published_at
recorded_at
```

`published_at` = when the market could know.

`recorded_at` = when Data Center learned/recorded the evidence.

Normal `recorded_at` is trusted storage/DB generated.

---

# 19. Authoritative Evidence Resolution

Evidence selection must be deterministic and account for:

```text
knowledge cutoff
source capability
evidence type/quality
supersession
retraction
deterministic tie breaker
```

API handlers must not improvise evidence selection.

---

# 20. Immutable Aggregate Rule

Complex parent+children datasets are immutable aggregates.

Only seal makes them PIT-visible. Resolvers ignore drafts.

After seal, DB must reject parent and child mutation.

---

# 21. Seal Concurrency Rule

Seal and child mutation must serialize on the same aggregate identity.

Valid outcomes:

```text
child commits first -> seal sees/hashes child
seal commits first -> child mutation rejected
```

Forbidden:

```text
seal hash excludes child but child commits afterward
```

Keep real multi-connection concurrency regressions.

---

# 22. Seal Table Rule

Prefer dataset-specific seal tables with real foreign keys. Avoid loose polymorphic aggregate references when referential integrity cannot be enforced.

---

# 23. Ingestion-Time Rule

Normal callers must not provide authoritative historical ingestion timestamps.

Historical preservation is allowed only through a dedicated trusted migration path with provenance and tests.

---

# 24. Business Hash Rule

`business_content_hash` is storage-generated from canonical business values.

Do not include publication/provenance/ingest metadata in business content identity.

---

# 25. Hash Separation

Keep separate:

```text
business_content_hash
publication_evidence_hash
raw_artifact_hash
```

---

# 26. Duplicate Fetch Rule

Unchanged business content must not create a fake revision, but repeated fetch provenance must remain auditable.

---

# 27. Provenance Integrity

If a record stores `raw_artifact_id` and `ingest_run_id`, the DB should enforce that the artifact belongs to that run.

---

# 28. Raw Artifact Rule

Raw artifacts are immutable and content-addressed. They are evidence/provenance, not cache.

v1 uses the local `data/raw/` store behind a storage abstraction. It contains only content-addressed artifacts (`<ab>/<sha256>`), never source archives or a `processed/` staging layer. Archives inside the store root could bypass the intended `storage_uri` boundary because reads validate containment under that root.

Under ROADMAP §14, required legacy archives remain at `~/GitHubLL/my_stock_project/data/raw` by owner decision. PR #32 cannot declare cutover complete while a v1 rebuild depends on a path outside this repository. Do not relocate archives as an incidental adapter change.

---

# 29. Source-Level PIT Capability

PIT capability belongs to:

```text
(dataset_code, source)
```

Do not let one source's verified evidence rules authorize another source.

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

New reconciliation policy requires ADR + permanent tests.

Final reconciliation must be deterministic from stored histories and independent of source import order.

If multiple sources exist without a canonical-source policy, require explicit source selection or return separated results. Endpoints with different field coverage must not alternate revisions for one logical key: PR #17 uses distinct source codes or retires the PR #9 per-security pilots from production.

---

# 31. Unknown Publication Rule

If:

```text
published_at IS NULL
```

the row/version is market-PIT invisible by default.

It may still be System-PIT visible if legitimately ingested by the system cutoff.

PR #15 remains PLANNED and requires owner-approved ADR-0020 before implementation. Until it lands, adapter PRs #16–#24 may proceed with `unknown` evidence; they are not blocked by the policy PR. Append approved evidence later without rewriting business versions.

---

# 32. Backfill Rule

If reliable evidence proves historical publication time, preserve it. Otherwise:

```text
published_at = NULL
```

Never use current wall-clock time as fake historical publication metadata.

ROADMAP PR #15 proposes `capture_bound`, `legacy_capture_bound`, `press_report_bound`, and `release_rule`, with per-(dataset, source) acceptance, explicit provenance, quality ordering, and versioned release rules. These proposals are not permission to enable new evidence types before ADR-0020 approval. Implement the exact approved policy and update §§31–32 in that PR.

Real legacy first-seen records exist for monthly revenue from 2026M02 and XBRL from 2025Q4. Older synthetic deadlines and later backfill-run dates are not first-seen evidence. Reconstructed monthly-revenue announcement dates are separate from first-published values; they do not restore pre-correction values. The approved policy must document latest-corrected backfill look-ahead and keep later corrections invisible before their capture.

---

# 33. XBRL Context Rule

Financial facts require a non-null canonical context identity covering entity, period, dimensions, typed dimensions, scenario/segment as applicable.

Preserve namespace-aware concept identity.

---

# 34. Timezone Rule

Use timezone-aware timestamps only.

```text
market timezone: Asia/Taipei
PostgreSQL: TIMESTAMPTZ
```

---

# 35. Data Domain Inventory Is Mandatory

Maintain:

```text
docs/data_domain_inventory.md
docs/data_domain_inventory.json
```

Every relevant legacy table/domain/field has an explicit disposition:

```text
observed/source dataset
canonical derived dataset
model-specific downstream feature
raw-artifact-only
deprecated / intentionally removed
```

---

# 36. Complete v1 Storage Contract Is a Permanent Invariant

Known v1 coverage includes:

```text
core identity/provenance
trading calendar
daily market data
monthly revenue
financial/XBRL
TDCC
institutional investor data
margin trading
short selling
SBL
market indices
corporate actions
issuer dividend declarations (separate domain, PR #33)
official valuation
canonical derived definitions/storage strategy
```

Later adapters/backfills must not weaken established ownership/storage semantics without explicit ROADMAP/ADR changes.

---

# 37. Legacy Domain Coverage Rule

When changing legacy/domain coverage, review old inventory field-by-field and explicitly choose:

```text
keep as observed
recompute as canonical derived
leave downstream
preserve only raw
deprecate intentionally
```

---

# 38. Observed vs Canonical-Derived Boundary

Data Center owns observable/source facts plus deterministic reusable canonical derived data.

ML repositories own model-specific transformations, features, labels, and training.

---

# 39. Canonical Derived Dataset Rule

Canonical derived datasets must be deterministic, cross-repo reusable, financially well-defined, model-independent, and reconstructible from PIT-safe inputs.

---

# 40. Model-Specific Feature Rule

Do not put model-specific features such as selection scores or experiment-specific interactions into Data Center.

---

# 41. Derivation Version Is Mandatory

Every canonical derived dataset has an explicit `derivation_version`. Formula changes require a new version.

---

# 42. Derivation Definition Rule

Preserve enough information to identify derivation semantics:

```text
dataset code
derivation version
formula/specification
implementation version/git commit
required input datasets
calendar/timezone convention
price-adjustment convention
registration timestamp
```

---

# 43. Derived PIT Rule

Derived visibility inherits from PIT-safe input visibility. Do not set `published_at = computed_at`.

---

# 44. `computed_at` Rule

`computed_at` is computation provenance, not market publication time.

---

# 45. Derived Lineage Rule

Materialized canonical results preserve derivation version, input identity/fingerprint, PIT context, computation provenance, and business-content hash.

---

# 46. Materialized vs Virtual Derived Data

Storage strategy is a performance choice. Financial definition and PIT semantics must match.

v1 materializes one rolling as-of series per metric: each observation date uses inputs visible at that date's cutoff. Other PIT contexts are computed on demand. Both paths must agree for the same context (ROADMAP §17).

---

# 47. Shared Derived Data Goal

If multiple downstream repos need the same canonical metric, prefer one Data Center definition.

---

# 48. Daily Market Legacy Coverage

Review legacy daily quote fields explicitly, including OHLC, volume, trade value, trade count, price change, bid/ask, and other source-observable values.

---

# 49. Institutional / Chip-Flow Coverage

The storage contract covers source data needed for institutional flow/holding metrics where applicable.

---

# 50. Margin / SBL Coverage

The storage contract covers margin trading, short selling, and securities borrowing/lending. Derived ratios are separate canonical derived data.

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

Do not collapse legally/source-distinct events merely because adjustment math may be similar.

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

For split-style events prefer `old_shares` / `new_shares` over ambiguous provider-specific ratios.

"Where available" is literal. The exchange result feeds that v1 ingests (`docs/source_field_audit.md` §4.10) do not publish `announcement_date`, `record_date`, `payment_date`, or the split between `earnings_stock_ratio` and `capital_surplus_stock_ratio`. Those columns stay NULL in v1.

## 51.1 Raw vs Adjusted Price Rule

Official daily OHLC is observed source data. Never rewrite raw history to remove a corporate-action discontinuity.

```text
raw official OHLC
+ PIT-safe corporate actions
-> versioned adjustment factors
-> adjusted OHLC / total-return series
```

## 51.2 Corporate-Action Inference Rule

Never infer a corporate action solely from a large price jump.

If an official reference price exists, preserve it as source data and use it for reconciliation.

## 51.3 Adjustment Timing Rule

Do not apply an adjustment before its effective/ex date under the selected PIT context.

## 51.4 Historical Price Readiness Gate

Raw daily-price ingestion may occur before complete corporate-action history because raw official prices are valid facts.

Do not declare historical prices analysis-ready for returns, indicators, backtesting, or ML until:

```text
corporate-action contract is explicit
representative real-source corporate-action source contracts pass
supported corporate-action history is backfilled
large discontinuities are reconciled
```

## 51.5 Corporate-Action Stable Identity Gate

This is a hard correctness gate.

Every normalized corporate action first registers stable identity using:

```text
(security_id, source, source_event_key)
```

A source-native event/document identifier is preferred.

If a source lacks one, a synthetic `source_event_key` is permitted only when official source semantics prove that its components remain invariant across corrections/replacements.

### Announcement feeds vs exchange result feeds

The rest of this section distinguishes two kinds of feed.

**Announcement/plan feeds** publish decisions whose dates and terms can still change. Examples: `t187ap45_L`, `mopsfin_t187ap39_O`, `TWT48U`. The forbidden-field list below applies to them in full. None has a proven link to an executed event, so none may enter `corporate_action_versions` or `security_events`.

They may still be stored as their own domain when the feed has a workable key *within itself*. ROADMAP PR #33 uses MOPS `t05st09sub` as the primary historical and forward source for both markets, in `dividend_declaration_versions`; the two OpenAPI declaration feeds are cross-checks only. Storing declarations this way is not a claim about event identity, and it does not unblock any column in `corporate_action_versions`.

**Exchange result feeds** record an event the exchange executed and priced on a trading date: `TWT49U`, `TWTAUU`, `TWTB8U`, TPEx `exDailyQ`, TPEx `revivt`.

- The executed event date is a completed market fact, not a mutable plan. TWSE's own detail locator for these feeds is `(code, date)`, for example `1101,20240701`.
- For these feeds, `source_event_key = "<feed>:<locator date>"` is permitted.
- Changed terms under the same locator are revisions of the same event. A row removed from the feed is a retraction.
- The adapter must still pass the full-history duplicate scan and the correction regressions (ROADMAP §27.7).

TWSE result feeds must use `response=json`. CSV replaces the detail locator with a link label, so legacy CSV cannot prove event identity. Preserve detail responses and source-native units; PR #19 converts free-share and rights ratios by dividing by 1,000.

The ex_date entry in the forbidden list below refers to announced or planned dates. It does not refer to the executed-date locator of a result feed.

The following fact is permanent:

> Current-snapshot uniqueness is not proof of correction-stable event identity.

Do not use mutable business content merely to remove collisions. Unless an official contract explicitly proves immutability for identity purposes, the following are forbidden as synthetic event-key repair fields:

```text
action_type
board_date
announcement_date
ex_date
record_date
payment_date
cash amount
stock ratio / share ratio
reference price
row ordinal
company name
adapter version
```

The same caution applies to business classification fields such as dividend year/period, accounting quarter/year label, and distribution period: they may be used only if official semantics prove correction stability, not merely because a live snapshot becomes unique.

### Announcement-feed evidence (abandoned pilot, permanent fixture)

The proposed TPEx identity:

```text
security_code + dividend_year + period
```

failed live verification:

```text
TPEx mopsfin_t187ap39_O
2,483 rows
65 duplicate identity groups
```

Representative collision:

```text
1591 / 108 / 1
board_date = 1080806
board_date = 1090505
```

This must remain a permanent example proving that dividend year/period is insufficient.

Re-verified 2026-09-15 on the same feed, now known to be frozen at `出表日期` 1100804: the 65 groups are unchanged, and adding the board-resolution date leaves 2 (`5009/108/1/1080805`, `8426/108/1/1090319`). TWSE `t187ap45_L` needs `股利所屬期間` as well — `(公司代號, 股利年度, 期別)` alone has 30 duplicate groups there. Both keys are within-feed keys for PR #33 only. Neither is an event identity, and the fixture above stands.

### Publication identity is not event identity

MOPS/TWSE/TPEx interfaces may expose publication/filing locators such as a sequence number for an individual announcement.

Do not equate:

```text
publication_id / filing sequence
```

with:

```text
corporate_action event_id
```

unless the official source explicitly defines a shared correction-stable event identity.

A correction/replacement can be a new publication referring to the same business event. Some announcement workflows may withdraw/re-create records. Therefore an announcement locator alone does not satisfy the PR #12 event identity contract.

### Summary-feed / exchange-feed joining

Do not create authoritative event linkage between:

```text
TWSE t187ap45_L
TWSE TWT48U_ALL
TWSE TWT49U
TPEx mopsfin_t187ap39_O
TPEx tpex_exright_prepost
TPEx tpex_exright_daily
```

using only heuristic combinations of security, ex-date, amount, ratio, action type, reference price, or row order.

Such matching may be used only as explicitly labeled reconciliation evidence, never as an official foreign key or stable event identity.

### Fail-closed requirement

If stable identity cannot be proven:

```text
DO NOT call/register corporate_action_event
DO NOT fabricate source_event_key
DO NOT append mutable fields until uniqueness appears
DO NOT weaken PR #12 semantics
```

Depending on current PR scope, allowed behavior is:

```text
retain raw artifact
record source/research evidence
quarantine ambiguous normalization
emit blocker/reconciliation report
stop before business-event registration
```

A blocked source is an acceptable correctness result.

### Future adapter identity tests

Any adapter that claims stable event identity must permanently test:

```text
same executed locator with corrected terms -> same event, new revision
separate real events -> different source_event_key
live/current duplicate scan
full-history duplicate scan -> zero duplicate (feed, code, locator date)
removed result-feed row -> retraction, not deletion
known collision fixture
forbidden mutable fields absent from identity
```

### Issuer declaration contract (PR #33)

Use MOPS `t05st09sub`, `TYPEK=sii|otc`, `qryType=1`, one Big5 HTML table per market-year. Respect the 3-second request interval. TWSE `t187ap45_L` and the TPEx OpenAPI feed frozen at `出表日期` 1100804 only cross-check overlapping years.

Handle both header variants: ROC ≤ 109 has 19 cells and combined legal/capital reserves; ROC ≥ 110 has 21 cells and separate reserves. Earlier rows keep the legal-reserve component NULL and the combined figure in the capital-surplus column with `reserves_combined`; never present it as reserve-pure. The flag also applies to frozen TPEx OpenAPI rows.

Declaration identity is `(security, dividend_year, dividend_period_text, sequence)`, not corporate-action event identity. Duplicate identities quarantine. TPEx OpenAPI's missing period text requires the board-resolution date for within-feed comparison, and its two remaining collisions still quarantine.

Market membership is evaluated at query time. Newly listed issuers can appear in past-year queries as new records; a smaller result set is not a retraction. A decision-progress change creates a version. Do not treat the board decision date as a proven release instant; follow PR #33's capture-based evidence contract and the approved PR #15 policy.

Never write these declarations into `corporate_action_versions` or `security_events`, or claim a verified link to an executed event.

---

# 52. Market Index Coverage

Define historical market-index storage used by market-regime features, benchmarks, and backtests.

PR #18 identifies indices by `(source, published index name)`. Whole-list sources supply close and changes only. TAIEX OHLC comes from `MI_5MINS_HIST`; its close must match `MI_INDEX` on each trading date or quarantine. Other index OHLC and index trade value stay NULL unless new official source evidence satisfies the audit rule.

---

# 53. Official Valuation vs Computed Valuation

Source-published PE/PB/dividend yield is observed source data. Data Center-computed valuation is canonical derived data. Keep them distinct.

---

# 54. Monthly Revenue Derived Fields

PR #22 preserves published comparatives as observed fields: `revenue_last_month`, `revenue_last_year_month`, `mom_pct`, `yoy_pct`, `cumulative_revenue`, `cumulative_revenue_last_year`, `cumulative_yoy_pct`, and `note`. Revenue amounts convert ×1,000 to TWD.

Keep them exactly as published; do not replace or reconcile them against our own series. Preserve the 2026M06/M07 discrepancy fixture (11 of 1,846 companies). `monthly_revenue_growth:v1` is outside v1.

---

# 55. API Boundary Rule

Public clients may know dataset concepts, PIT context, source, derivation version, and provenance.

They must not know PostgreSQL table names, Redis key formats, or Alembic internals.

---

# 56. Downstream Credential Rule

Downstream ML repos must not require PostgreSQL or Redis credentials. They use API/SDK only.

---

# 57. Ingestion and Query Separation

```text
write: source -> raw artifact -> parser -> normalization -> PostgreSQL
read: client -> API -> optional cache -> PIT/derived service -> PostgreSQL
```

Do not route ingestion correctness through Redis.

---

# 58. No Cache-Only Writes

Never write durable business/evidence/derivation-definition data only to Redis.

---

# 59. Query Alias Rule

Resolve `latest`/`now` aliases to explicit semantics/timestamps where possible before cache identity construction.

---

# 60. Cache TTL Rule

TTL is performance policy, never a correctness mechanism.

---

# 61. Cache Serialization Rule

Cache encoding is deterministic and versioned. Cached responses retain the same semantic metadata/provenance as uncached responses.

---

# 62. Observability Rule

Expose enough information to distinguish cache behavior, PostgreSQL queries, resolver latency, derivation latency, response size, ingest status, quarantine, source failures, coverage gaps, and reconciliation status.

Do not log credentials.

---

# 63. SSD Optimization Rule

Do not claim SSD benefit without measurement. Redis does not eliminate PostgreSQL write I/O.

---

# 64. Testing Cache Correctness

Every cacheable query family tests NullCache, Redis cold/warm, and Redis-unavailable fallback equivalence.

---

# 65. Testing Derived Correctness

Every canonical derived dataset tests deterministic formulas, derivation versions, PIT-safe inputs, no future leakage, lineage, and materialized/virtual equivalence where applicable.

---

# 66. PIT Regression Tests Are Permanent

Once a PIT/leakage/correctness bug is found, add a permanent regression.

Permanent categories include:

```text
unknown publication
late publication evidence
knowledge cutoff
system late ingestion
backfill
evidence correction/retraction
sealed aggregate
seal concurrency
source capability isolation
XBRL dimensions
derived PIT inheritance
cache PIT identity
cache failure fallback
corporate-action identity collision
corporate-action correction-stability
publication-vs-event identity
```

---

# 67. PR Scope Discipline

When implementing one ROADMAP PR:

- implement only its dominant delivery goal
- honor dependencies and blockers
- honor explicit out-of-scope work
- do not weaken existing acceptance criteria
- do not silently change PIT/identity/derivation semantics
- record architectural changes in ADRs where appropriate
- keep acceptance/reconciliation evidence current
- avoid unrelated refactors

A `BLOCKED` PR is not permission to invent a workaround outside its contract.

---

# 68. Historical Storage-Contract Preservation Rule

Earlier merged PRs established v1 storage/ownership contracts.

Before changing/removing an existing domain contract:

```text
update ROADMAP
update domain inventory
write/adjust ADR if architectural
provide migration semantics
provide regressions
```

Empty-but-correct contracts do not grant permission to reinterpret semantics during later ingestion work.

---

# 69. Cache Scope Rule

Do not introduce Redis/cache work into source-correctness PRs unless the current ROADMAP PR explicitly owns cache behavior.

No cache PR is authorized before the measured-latency trigger in ROADMAP §26.2.

---

# 70. Real-Data Import PR Boundary

Real adapters and bulk backfills belong to dedicated ROADMAP PRs.

Fixture-backed ingestion is not proof that real source import/backfill is complete.

A bounded pilot is not proof that full historical coverage exists.

---

# 71. Raw-First Adapter Rule

For external sources, preserve raw artifacts before normalization whenever retention is possible.

Required provenance as applicable:

```text
source
resource/request identity
fetch time
raw bytes/export
raw artifact hash
ingest run
adapter/parser version
```

Do not discard source representation after extracting canonical rows.

Use `fetch → durable raw artifact → checkpoint → parse → normalize → canonical write`. One adapter per endpoint serves history and forward capture. History runs are throttled, resumable, and checkpointed. Unknown header variants quarantine; operational failures after raw capture remain resumable.

---

# 72. Source-Native Unit Rule

A real adapter must know source unit/scale before constructing canonical observations.

Do not infer unit from numeric magnitude.

---

# 73. Historical Backfill Publication-Time Rule

Historical backfill never invents `published_at`.

If not provable:

```text
published_at = NULL
```

Do not substitute import time, file mtime, effective date, or legacy row existence.

---

# 74. Historical Backfill System-PIT Rule

Normal backfill records actual Data Center ingestion/seal time. Do not copy historical market/effective dates into `ingested_at`.

---

# 75. Legacy Database Migration Rule

Legacy `stock_db` is migration input, not automatically authoritative truth.

Do not bulk-copy without field mapping, source-semantic validation, unit normalization, PIT validation, provenance capture, and new writer/trusted migration constraints.

Default artifact origin is `official_fetch`, preserving exact response bytes. `legacy_archive` is allowed only for ROADMAP §14 / PRs #22–#24 exceptions:

- Monthly revenue: `market.csv` provides first-captured values from 2026M02 and recovered announcement dates for older rows. `revswarm.db` is not a live import dependency. Apply PR #22's date ambiguity rule and import cross-check under the approved evidence policy.
- XBRL: legacy documents from 2020Q1, with the PR #23 archive-vs-official sample gate and full re-fetch fallback if its mismatch threshold fails. Synthetic filename dates and late backfill files carry no first-seen evidence.
- TDCC: the consolidated `shareholding` archive up to forward capture (375 weeks, 348 inside v1), then official OpenData. Follow PR #24's payload/date validation; reject filename/payload disagreement.

Record archive path, file mtime, and compressed entry name where applicable. `fetched_at` is the Data Center read time. Most legacy files were transformed by scrapers and are reconciliation baselines, not official raw bytes.

---

# 76. Backfill Idempotency and Restart Rule

Bulk import/backfill must be safe to rerun and resume.

```text
same canonical content -> no fake business revision
same evidence identity -> no fake evidence revision
repeated observation -> provenance remains auditable
partial failure -> restart does not corrupt prior work
```

---

# 77. Pilot-Before-Bulk Rule

Before full historical backfill, relevant pilots and dependency PRs must pass.

For daily prices, corporate-action identity/history and trading-calendar dependencies must be satisfied before analysis-readiness claims.

---

# 78. Import Reconciliation Rule

Every real import/backfill reports at least:

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

Cross-source reconciliation must be reproducible from stored histories and independent of incidental import order.

Historical price discontinuities are classified as:

```text
explained_by_corporate_action
explained_by_other_documented_market_event
unexplained_anomaly
```

Do not silently smooth anomalies.

Every adapter and derived PR reconciles against legacy `stock_db` for 2020-01-02 through 2026-09-11, with units normalized first and every difference classified. A documented PIT-correct difference is acceptable. Calendar-based coverage distinguishes closures from gaps and must not depend on today's security universe.

---

# 79. Import Manifest and Quarantine Rule

Every pilot/bulk import emits an auditable manifest with import ID, git commit, adapter version, source/scope, configuration fingerprint, timing, hashes where practical, result counts, warnings/errors, and reconciliation result.

Suspicious or semantically ambiguous records fail loudly or enter quarantine.

Corporate-action identity ambiguity is a quarantine/blocker condition, not a prompt to manufacture an ID.

---

# 80. Derived Implementation PR Rule

Actual calculators belong in dedicated derived PRs unless a minimal implementation is required solely for contract validation.

For adjusted-price calculations (PR #25):

```text
raw official OHLC
+ PIT-safe corporate actions
-> versioned adjustment factors
-> adjusted OHLC / total-return
```

v1 uses `factor(D) = official_reference_price(D) / close_before(D)` for ex-right/ex-dividend and capital-reduction/par-value resumption dates. This includes cash dividends and produces a dividend-reinvested, total-return-style series. Do not apply events before their effective date or outside their PIT visibility.

PR #26 ports legacy calculators, including `technical_indicators:v1` on raw close. It depends on PRs #17–#25 as each metric requires and does not bypass readiness gates. Adjusted-price indicator variants and a price-only adjusted series wait for a consumer need. Institutional cumulative-flow "holding" values are proxies; composite pressure scores remain downstream.

---

# 81. Migration Rule

A migration changing temporal, lineage, identity, or derivation meaning requires explicit migration/downgrade semantics.

Downgrade either safely represents all history or fails before mutation.

Never delete/collapse valid PIT history merely to make downgrade succeed.

---

# 82. Redis Configuration Rule

Redis-specific configuration belongs in deployment/configuration, not domain logic.

---

# 83. Security Rule

If Redis is remote, restrict network access, use auth/TLS as appropriate, and never commit credentials.

---

# 84. Priority Order

When tradeoffs exist, prioritize:

```text
1. PIT correctness
2. historical auditability
3. stable source/business identity correctness
4. complete v1 domain ownership/storage contract
5. provenance integrity
6. real-source semantic correctness and unit normalization
7. deterministic/idempotent import behavior
8. source isolation
9. derivation-version correctness
10. reconciliation/auditability
11. migration/history preservation
12. cache/result equivalence
13. maintainability
14. performance
15. SSD/read reduction
16. convenience
```

Never trade identity/PIT/auditability/provenance/source semantics/import determinism/reconciliation/migration safety/cache correctness for implementation convenience.

---

# 85. PR Completion Checklist

Before declaring any PR complete/mergeable:

```text
current PR and ROADMAP scope identified
declared dependencies satisfied
PR is not BLOCKED by an unresolved correctness gate
tests pass
docs updated
acceptance criteria evaluated
PIT semantics preserved
provenance preserved
source identity semantics preserved
no source-capability leakage
no direct downstream DB/cache dependency introduced
out-of-scope work not silently absorbed
review blockers/majors resolved
```

For schema/migration PRs additionally:

```text
alembic upgrade passes
alembic check/schema drift passes
downgrade passes or is deliberately guarded before mutation
permanent migration regressions pass
```

For real-data ingestion/backfill PRs additionally:

```text
real source adapters use explicit source semantics
raw-first provenance preserved
publication time proven or unknown
normal backfill preserves actual System-PIT ingestion time
import is idempotent/restartable
reconciliation/import manifests exist
quarantined/anomalous records reported
real-data PIT spot checks pass
cross-source reconciliation converges independent of import order where applicable
```

For corporate-action ingestion/backfill PRs additionally:

```text
native or synthetic source_event_key contract is documented
identity survives corrections/replacements
snapshot uniqueness is not used as sole proof
known collision fixtures pass
forbidden mutable business fields are absent from source_event_key
publication identity is not silently treated as event identity
heuristic issuer/exchange joins are not authoritative foreign keys
ambiguous identity fails closed before event registration
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
adjustment convention explicit/versioned
no future corporate action leaks backward
raw vs adjusted distinguishable
derived inputs PIT-safe and lineage-complete
```

For cache PRs additionally:

```text
CACHE_BACKEND=none passes
Redis cold/warm equivalence passes
Redis failure fallback passes
```

Then publish/update PR acceptance evidence.

---

# 85.1 Agent Execution Protocol

At the start of implementation:

```text
1. Read ROADMAP.md.
2. Identify current GitHub PR.
3. Read status, blockers, dependencies, acceptance criteria, out-of-scope work.
4. If status is BLOCKED, do not implement around the blocker.
5. Inspect current main/head.
6. Implement only the current PR when its correctness gates permit implementation.
```

At the end report:

```text
commit SHA
files/schema materially changed
acceptance criteria satisfied
tests/lint/migration checks
known limitations
explicitly deferred work
```

For source research that discovers a failed identity/source-semantic assumption:

```text
stop implementation
record the failing live/source evidence
update ROADMAP/acceptance evidence
preserve a regression fixture when practical
do not patch the failure by adding mutable fields
```

---

# 86. Core Boundary

```text
stock-data-center owns:
    source data
    temporal visibility
    publication evidence
    ingest history
    revisions
    provenance
    PostgreSQL
    stable source/business identity contracts
    canonical reusable derived data
    optional cache
    API

stock-eps-model owns:
    EPS-specific features
    EPS labels
    EPS training
    EPS prediction

stock-model-selection owns:
    model-specific selection features
    forward-return labels
    selection training
    ranking
    backtesting
```

Redis remains optional.

Canonical derived datasets remain model-independent, versioned, PIT-safe, and reusable.

Enabling, disabling, restarting, or losing Redis may affect performance but never correctness.

A source being official does not automatically make every row safely normalizable. If official data lacks a proven stable business identity required by the canonical contract, the correct outcome is to preserve evidence and remain blocked rather than invent semantics.
