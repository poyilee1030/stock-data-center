# Phase 1 Database Schema

## Scope

Phase 1 establishes PostgreSQL 18 storage and integrity rules. It does not
implement PIT resolver queries, ingestion adapters, the public API, or caching.

SQLAlchemy metadata lives in `src/stock_data_center/db/metadata.py`. Alembic
migrations are the deployment record in `migrations/versions/`.

## Core tables

| Area | Tables |
| --- | --- |
| Dataset policy | `dataset_catalog`, `dataset_sources` |
| Security identity/history | `security`, `security_metadata_versions` |
| Provenance | `ingest_runs`, `raw_artifacts`, `raw_artifact_observations`, dataset-specific `*_version_observations`, `publication_evidence_observations` |
| Market/revenue versions | `daily_price_versions`, `monthly_revenue_versions` |
| Financial aggregate | `financial_filing_versions`, `financial_facts`, `quarterly_financial_summary`, `financial_filing_seals` |
| TDCC aggregate | `tdcc_snapshot_versions`, `tdcc_distribution`, `tdcc_snapshot_seals`, `tdcc_distribution_schemas`, `tdcc_distribution_schema_buckets` |
| Institutional data | `institutional_investor_versions`, `institutional_market_summary_versions`, `foreign_holding_versions` |
| Credit/short data | `margin_trading_versions`, `securities_lending_versions` |
| Reference/event data | `market_index`, `market_index_versions`, `corporate_action_versions`, `security_tag_versions` |
| Official source metrics | `official_valuation_versions`, `xbrl_concept_catalog_versions` |
| Canonical derived data | `derived_dataset_definitions`, `derived_computation_runs`, `derived_metric_versions` |
| Publication knowledge | `publication_evidence` |

All externally meaningful timestamps use `TIMESTAMPTZ`.

## Provenance model

`raw_artifacts` is the immutable content-addressed identity of exact bytes. Its
lowercase SHA-256 digest and storage URI are unique, and the URI must contain the
digest. `raw_artifact_observations` records every ingest run that fetched an
artifact, including repeat fetches of identical bytes.

Every normalized version and publication-evidence row has a composite foreign
key to `(raw_artifact_id, ingest_run_id)` in the observation table. Insert
triggers additionally ensure the run's dataset and source match the normalized
row. This prevents both mismatched IDs and cross-source lineage.

See [ADR-0006](decisions/0006-content-addressed-artifacts-and-fetch-observations.md).

## Version and hash rules

Single-row version insert triggers overwrite caller values for `ingested_at`
and `business_content_hash`. Hashes are SHA-256 over PostgreSQL-built canonical
JSON containing only the table's business values. Logical identity, source,
publication evidence, timestamps, and provenance identifiers are outside the
business hash. A unique constraint over logical key, source, and business hash
prevents an unchanged fetch from creating a false revision.

The three hash domains remain separate:

- `business_content_hash` for normalized business values;
- `publication_evidence_hash` for an evidence assertion and its target; and
- `raw_artifact_hash` for exact raw bytes.

Normal application SQL cannot preserve a caller-provided authoritative time or
business/evidence hash. A future historical import requiring timestamp
preservation must add a separately restricted and audited migration mechanism;
none is exposed in Phase 1.

Append-only tables reject `UPDATE`, `DELETE`, and `TRUNCATE`; immutability does
not depend on application repository behavior.

The same rules apply to every v1 observed version table. Daily prices retain
the complete known legacy observable contract: OHLC, volume, trade value,
trade count, price change/direction, source bid/ask snapshots, and parsed last
bid/ask price and volume. Institutional flow, holdings, margin, exchange short,
SBL, index, corporate-action, official-valuation, tag, and concept-catalog
domains each retain source identity, raw/ingest lineage, trusted ingestion
time, and a storage-generated business hash. The field-by-field disposition is
maintained in [the data-domain inventory](data_domain_inventory.md).

## Publication evidence

`publication_evidence` has real nullable foreign keys to each supported version
table and a check requiring exactly one target. Its insert trigger verifies that
the declared dataset/source matches that target and that supersession stays on
the same target and source.

`recorded_at` and `publication_evidence_hash` are overwritten by trusted DB
logic. Updates and deletes are rejected. Assertions/corrections require a
non-null publication instant; unknown/retraction events require a null instant.
Authoritative evidence selection remains Phase 2 work.

Every observed version type has a dedicated nullable foreign key from
`publication_evidence`; the exactly-one-target constraint, dataset/source
validation, supersession validation, and independent evidence hash cover all
v1 targets.

## Canonical derived contract

`derived_dataset_definitions` registers an immutable `(dataset_code,
derivation_version)` semantic definition, including formula/specification,
implementation identity, required inputs, calendar/timezone convention, and
price-adjustment convention. The DB generates its definition hash and trusted
registration time.

`derived_computation_runs` records operational computation provenance.
`derived_metric_versions` stores materialized typed metric values with the
definition, security/date/metric identity, explicit market or system PIT
context, input dataset identity, deterministic input fingerprint, computation
run, DB-generated business hash, and trusted `computed_at`. The DB rejects a
result whose run belongs to a different definition. Definitions and results
are append-only.

`computed_at` never grants market visibility. A market-PIT result carries both
`information_as_of` and `knowledge_as_of`; a system-PIT result carries only
`system_as_of`. Visibility is inherited from the inputs selected under that
context. The same definition contract applies to virtual derived datasets, so
storage strategy cannot change financial meaning. See
[derived-data semantics](derived_data.md) and [ADR-0007](decisions/0007-canonical-derived-data-ownership-and-pit.md).

## Immutable aggregates

Financial filings and TDCC snapshots can be committed as drafts. Their official
visibility surfaces are the `visible_financial_filings` and
`visible_tdcc_snapshots` views, both of which inner-join the dataset-specific
seal table.

On seal insertion, PostgreSQL locks the parent, validates that required children
exist, computes a canonical aggregate business hash from the parent and ordered
children, overwrites the seal time with `statement_timestamp()`, and copies the
hash to the parent. After the seal exists, triggers reject:

- parent update/delete;
- child insert/update/delete; and
- seal update/delete.

Every child insert/update/delete obtains `FOR UPDATE` on that same parent before
checking for a seal. This is the aggregate serialization point: a child mutation
that locks first commits before the seal can scan children, while a seal that
locks first commits before the child checks seal state and causes that child to
be rejected. Moving an existing child to another parent is forbidden.

The seal tables use direct foreign keys rather than polymorphic references.

## XBRL context identity

Every financial fact has a non-null, storage-generated `context_hash`. The hash
includes entity, period shape, explicit and typed dimensions, scenario, and
segment. PostgreSQL `jsonb` provides canonical object-key ordering. Fact
uniqueness includes filing, namespace-aware concept QName, context hash, and
unit identity, so dimensionally distinct facts coexist while a canonical
duplicate is rejected.

## Source capability

`dataset_sources` has a composite primary key of `(dataset_code, source)` and
stores market/system PIT capability, evidence status/quality, and canonical
source status independently. Phase 2 adds a non-empty
`accepted_evidence_types` array to the same exact source policy; existing rows
migrate to conservative `official`-only acceptance. A partial unique index
allows at most one canonical source per dataset. The resolver enforces these
flags and filters evidence types before supersession/ranking.

## Phase 3 domain access

Phase 3 uses the existing `security_metadata_versions` and
`daily_price_versions` foundation. The Phase 3 migrations make stable
`security_code` identity immutable and DB-timestamped, then move market into
`security_metadata_versions` as effective-dated, business-hashed state. Existing
metadata rows are backfilled from their former identity market and rehashed.
The `market_data` package supplies normalized append-only
writers and cache-free domain queries for historical security state, historical
listed universes, single-date daily prices, and inclusive daily-price windows.
Every business record still resolves through the Phase 2 source, evidence,
revision, and provenance rules. See
[the Phase 3 contract](security_daily_market.md) and
[ADR-0011](decisions/0011-effective-dated-security-market.md).

## Phase 4 domain access

Phase 4 retains the existing `monthly_revenue_versions` shape and freezes
`revenue` as currency-major units. One focused migration adds append-only
many-observation lineage for monthly revenue versions and publication evidence.
The association triggers validate that the observation's ingest run has the
same dataset/source as its version or evidence target. Existing rows are
backfilled with their original artifact/run association.

The `monthly_revenue` package provides scale-explicit normalization,
append-only writes, single-period and inclusive-history PIT queries, and linked
observation access. Revenue/currency business revisions remain independent from
evidence corrections, and all results retain Phase 2 provenance. See
[the Phase 4 contract](monthly_revenue.md).

## Phase 5 domain access

Phase 5 adds the `financials` package over the existing sealed filing tables.
Facts require canonical Clark-notation QNames and exactly one numeric/text
value, unless `is_nil` explicitly represents an XBRL nil fact. The
storage-generated context hash continues to cover full period and dimensional
context. Focused migrations add append-only, source-validated many-observation
lineage for filing versions, direct same-filing fact lineage for summaries, and
an explicit `quarter`/`ytd`/`annual` summary basis.

The service returns children only after the shared PIT resolver selects a
sealed filing. Curated `basic_eps` requires an explicit requested period basis,
retains its source fact, and therefore inherits the selected filing's
Market/System PIT, publication-evidence, source, and provenance semantics. The
canonical writer additionally requires a versioned source/context
classification; DB duration checks are defensive and never act as the source
classifier. See [the Phase 5 contract](financial_xbrl.md).

## Phase 6 domain access

Phase 6 adds the `tdcc` package over the existing sealed snapshot tables. A
focused migration adds append-only, source-validated many-observation lineage
for snapshot versions (backfilled from existing parent lineage), exposes the
seal's canonical aggregate hash as `stockdc_tdcc_snapshot_business_hash` with
byte-order (`COLLATE "C"`) bucket ordering, and rejects TDCC publication
evidence whose `published_at` precedes the snapshot date in Asia/Taipei.

Each snapshot declares a registered, append-only distribution profile. Row
triggers enforce the profile's bucket roles (a signed level-16 adjustment with a
`NULL` holder count in `tdcc-opendata-v1`), and the seal rejects a snapshot that
lacks any profile bucket. A profile's bucket definitions freeze once any
snapshot references it. Existing snapshots are assigned the official profile
and rehashed; the migration aborts if they do not fit it. See
[ADR-0012](decisions/0012-tdcc-distribution-profiles-and-seal-completeness.md).

The writer reuses an identical sealed revision instead of creating a fake one,
and the service reads a distribution only after the shared PIT resolver selects
a sealed version. See [the Phase 6 contract](tdcc.md).

## Phase 7 domain access

Phase 7 adds normalized writers and PIT-safe reads for institutional investor
flows, market-level institutional summaries, foreign holdings, margin/short
trading, and securities lending. The five existing source-version tables gain
append-only, source-validated observation associations so unchanged refetches
retain every artifact/run without creating fake revisions.

PostgreSQL enforces non-negative gross quantities, balances, holdings, and
limits while retaining signed published net-flow values and signed SBL
adjustments. Published ratios use percent units. Business hashes exclude
logical keys and provenance; ingestion time remains trusted server time.
Publication evidence cannot predate the trade date in Asia/Taipei.

No pressure score or cross-dataset ratio is materialized in this phase.
Canonical metrics remain Phase 9 work over PIT-resolved inputs. See
[the Phase 7 contract](institutional_financing.md) and
[ADR-0013](decisions/0013-phase7-observed-source-semantics.md).

## Migration operation

Local PostgreSQL 18 startup:

```bash
docker compose up -d postgres
```

Apply, verify, and reverse migrations:

```bash
DATABASE_URL=postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc \
  .venv/bin/alembic upgrade head
DATABASE_URL=postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc \
  .venv/bin/alembic check
DATABASE_URL=postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc \
  .venv/bin/alembic downgrade base
```

The initial migration has no cache impact: Phase 1 has no cache implementation
or historical rows. Any later migration that changes temporal meaning must
include the cache-impact handling required by the roadmap.
