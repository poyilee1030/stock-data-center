# Phase 1 Acceptance Report

- Phase: 1 — Complete v1 Storage Contract and Versioned PIT Schema
- Date: 2026-09-11
- Result: PASS

## Scope delivered

Phase 1 freezes the complete known v1 PostgreSQL 18 storage and ownership
contract. It includes core identity/provenance, every known observed domain,
publication evidence, immutable financial/TDCC aggregates, canonical-derived
definitions/results, DB-generated hashes and timestamps, source-aware lineage,
and PIT-oriented indexes.

The legacy audit is in
[`docs/data_domain_inventory.md`](../data_domain_inventory.md), with all 416
field dispositions in the normative
[`docs/data_domain_inventory.json`](../data_domain_inventory.json). Schema
details are in [`docs/schema.md`](../schema.md), with architectural decisions in
[ADR-0007](../decisions/0007-canonical-derived-data-ownership-and-pit.md) and
[ADR-0008](../decisions/0008-complete-v1-storage-decomposition.md).

No ingestion pipeline, calculator, resolver, public API, cache implementation,
or Redis dependency was added.

## Migration evidence

Phase 1 is represented by four ordered Alembic revisions:

1. `94060901029e_create_phase_1_schema.py` — core tables and visibility views;
2. `b7e1c9a42f10_enforce_phase_1_invariants.py` — trusted hashes/times,
   append-only histories, lineage, sealing, and concurrency serialization;
3. `58124040faa4_complete_v1_storage_contract.py` — remaining observed and
   canonical-derived v1 storage contracts; and
4. `ae58b8fa158d_enforce_complete_v1_invariants.py` — invariants and PIT indexes
   for the completed v1 contract.

The migration round-trip test applies all four revisions after a downgrade to
base and verifies all 31 v1 tables.

## Required acceptance criteria

| Criterion | Result | Concrete evidence |
| --- | --- | --- |
| `docs/data_domain_inventory.md` covers all known legacy tables/domains | PASS | The inventory records the 2026-09-11 audit: 26 retained DB tables plus all 27 `SCHEMA_COLS` categories, including four deprecated pre-XBRL categories. `test_phase1_contract_docs.py` enforces both the retained table set and the field contract. |
| Every legacy field/domain has an intentional disposition | PASS | `docs/data_domain_inventory.json` contains one explicit record for each of the 416 audited `SCHEMA_COLS` fields, with disposition, target, and reason. Tests enforce uniqueness, 27/416 counts, allowed dispositions, non-empty targets/reasons, and the frozen canonical pair SHA-256 extracted from the old source. A targeted regression also requires `valuation_daily.pe_official` to remain observed as `official_valuation_versions.pe_ratio` in both JSON and Markdown. |
| All known v1 observed domains have a storage contract | PASS | SQLAlchemy metadata and migrations define 31 tables covering core, daily/revenue, XBRL, TDCC, institutional/holding, margin/short/SBL, indices, actions, official valuation, tags, and concept catalog. |
| All known v1 canonical-derived domains have a materialized/virtual contract | PASS | `derived_dataset_definitions`, `derived_computation_runs`, and `derived_metric_versions` implement the common contract; the inventory explicitly selects a strategy for technical, concentration, valuation, margin, short/SBL and additional reusable datasets. |
| Child insert after seal is rejected | PASS | Financial and TDCC child protection triggers lock the parent and reject post-seal mutation; sequential and real multi-connection tests cover this. |
| Sealed aggregate cannot be updated/deleted | PASS | Parent, child, and seal triggers reject post-seal update/delete; integration tests exercise both aggregate families. |
| Seal and child mutation serialize on the same aggregate identity | PASS | Both paths take `FOR UPDATE` on the parent. Two-connection tests prove seal-first rejects the waiting child and child-first makes the waiting seal hash the child. |
| Committed unsealed aggregate can exist but is resolver-invisible | PASS | `visible_financial_filings` and `visible_tdcc_snapshots` inner-join their dataset-specific seal tables; tests see zero before seal and one after. |
| Caller cannot forge normal historical ingestion time | PASS | BEFORE INSERT triggers overwrite observed `ingested_at`, seal time, evidence `recorded_at`, derived `registered_at`, and derived `computed_at` with trusted DB time. Tests submit year-2000 values. |
| Business hash is storage-generated from canonical business content | PASS | PostgreSQL SHA-256 trigger functions overwrite submitted hashes. Tests cover prior and every newly added observed table; a regression proves an extended daily quote field changes the hash. |
| Evidence update does not create false business revision | PASS | Evidence is a separate append-only identity/hash. Assertion, correction, and retraction tests leave the business-version count unchanged. |
| Correction/retraction is append-only | PASS | Evidence checks and same-target/source supersession validation allow appended correction/retraction rows while UPDATE/DELETE/TRUNCATE is rejected. |
| XBRL context identity supports dimensions | PASS | The generated context hash includes entity, period, explicit/typed dimensions, scenario, and segment. Tests prove canonical JSON ordering and dimensional distinction. |
| Source A capability does not leak to source B | PASS | `dataset_sources` keys policy by `(dataset_code, source)`; integration tests preserve opposing source capabilities and reject cross-source lineage. |
| Canonical derived definitions include `derivation_version` | PASS | An immutable unique `(dataset_code, derivation_version)` definition is required. Tests register and retain semantically distinct v1/v2 definitions. |
| Derived `computed_at` is not market publication time | PASS | Derived rows contain explicit market/system PIT context and no `published_at` column; docs define inherited input visibility. The DB overwrites `computed_at` as operational provenance only. |
| Migration tests cover every v1 table | PASS | `V1_TABLES` explicitly lists all 31 tables and the round-trip test checks the complete set after upgrade. New observed-domain tests exercise trusted lineage/hash/time/immutability. |
| Alembic upgrade/downgrade/upgrade succeeds | PASS | `test_postgresql_18_and_migration_round_trip` performs the full cycle against PostgreSQL 18. |
| `alembic check` passes | PASS | Both the dedicated test and the final command report no metadata drift. |

All 19 required criteria pass.

## Verification performed

```text
PostgreSQL:                 18 (postgres:18)
pytest:                     28 passed
Alembic round-trip:         PASS (inside pytest)
alembic check:              No new upgrade operations detected
Python compileall:          PASS
Phase scope:                no resolver/API/cache/Redis implementation
```

Phase 2 may begin only as a separate task and must remain correct with caching
disabled.
