# Phase 1 Acceptance Report

- Phase: 1 — Versioned PIT Database Schema
- Date: 2026-09-11
- Result: PASS

## Scope delivered

Phase 1 adds the PostgreSQL 18 correctness foundation:

- SQLAlchemy 2.x metadata for dataset/source policy, provenance, versioned
  business data, publication evidence, XBRL facts, and sealed aggregates;
- two fixed Alembic revisions for tables and PostgreSQL integrity logic;
- DB-generated authoritative timestamps and hashes;
- append-only and post-seal immutability triggers;
- composite provenance constraints and dataset/source validation;
- seal-only visibility views; and
- PostgreSQL integration regressions.

No PIT resolver, API route, ingestion adapter, cache abstraction, or Redis
dependency was introduced.

Primary evidence:

- `src/stock_data_center/db/metadata.py`
- `migrations/versions/94060901029e_create_phase_1_schema.py`
- `migrations/versions/b7e1c9a42f10_enforce_phase_1_invariants.py`
- `tests/integration/test_phase1_schema.py`
- [Schema documentation](../schema.md)
- [ADR-0006](../decisions/0006-content-addressed-artifacts-and-fetch-observations.md)

## Required acceptance criteria

| Criterion | Result | Concrete evidence |
| --- | --- | --- |
| Child insert after seal is rejected | PASS | `stockdc_protect_financial_child` and `stockdc_protect_tdcc_child` execute as PostgreSQL triggers. Tests `test_financial_aggregate_visibility_context_and_immutability` and `test_tdcc_seal_controls_visibility_and_immutability` attempt inserts after seal and observe DB rejection. |
| Sealed aggregate cannot be updated/deleted | PASS | Parent, child, and seal triggers reject post-seal mutations. The financial aggregate test exercises parent update/delete, fact update/delete, and seal update/delete; the TDCC test exercises sealed parent update. |
| Committed unsealed aggregate can exist but is resolver-invisible | PASS | No constraint requires a seal at transaction commit, while `visible_financial_filings` and `visible_tdcc_snapshots` require dataset-specific seals. Both aggregate tests create parent/children without a seal and prove the visibility view returns zero before seal and one after seal. |
| Caller cannot forge normal historical ingestion time | PASS | Single-version, evidence, and seal BEFORE INSERT triggers overwrite caller timestamps with `statement_timestamp()`. Tests submit year-2000 values and assert returned values fall within the current operation window. |
| Business hash is storage-generated from canonical business content | PASS | Per-dataset insert/seal triggers replace submitted hashes using SHA-256 over PostgreSQL canonical JSON. Tests cover daily price, security metadata, monthly revenue, financial filing, and TDCC hashes. Duplicate price content is rejected by logical-key/source/hash uniqueness. |
| Evidence update does not create false business revision | PASS | Publication evidence has its own append-only table/hash and target FK. `test_evidence_is_append_only_and_does_not_create_business_revision` appends assertion, correction, and retraction while the business-version count stays one. |
| Correction/retraction can be represented append-only | PASS | Evidence-kind and publication-shape checks support correction and retraction; supersession validation restricts chains to one target/source. The evidence test inserts both events and confirms evidence UPDATE is rejected. |
| XBRL context identity supports dimensions | PASS | `stockdc_prepare_financial_fact` hashes entity, period, explicit/typed dimensions, scenario, and segment. The aggregate test proves reordered equivalent JSON produces a duplicate identity while a distinct dimension produces a distinct non-null hash and coexists. |
| Source A capability does not leak to source B | PASS | `dataset_sources` uses `(dataset_code, source)` as its primary key. `test_source_capabilities_and_lineage_are_isolated` stores opposing market-PIT flags and proves they remain independent; cross-source normalized lineage is rejected. |
| Alembic upgrade/downgrade/upgrade succeeds | PASS | `test_postgresql_18_and_migration_round_trip` verifies PostgreSQL major version 18, downgrades to base, confirms schema removal, upgrades to head, and confirms table/view restoration. The full suite passed this test. |

All ten required criteria pass.

## Additional integrity evidence

- `raw_artifact_observations` preserves repeated-fetch provenance without
  duplicating identical content-addressed artifacts.
- Composite foreign keys reject mismatched artifact/run identifiers, while
  triggers reject dataset/source mismatch.
- Publication evidence requires exactly one real version foreign key target.
- Aggregate seal hashes canonically order facts, summary metrics, and TDCC
  buckets before hashing.
- A partial unique index permits at most one canonical source per dataset.
- `alembic check` reports no metadata drift.

## Verification performed

Environment:

```text
Python      3.12.3
PostgreSQL  18 (postgres:18)
SQLAlchemy  2.0.52
Alembic     1.19.2
psycopg     3.3.5
pytest      8.4.2
```

Results:

```text
pytest:                    8 passed
Alembic round-trip:        PASS (covered by pytest)
alembic check:             No new upgrade operations detected
Python compileall:         PASS
git diff --check:          PASS
Phase scope inspection:    no resolver/API/cache/Redis implementation
```

Phase 2 may begin only as a separate task. Its resolver must operate with cache
disabled and must not alter the Phase 0 semantics.
