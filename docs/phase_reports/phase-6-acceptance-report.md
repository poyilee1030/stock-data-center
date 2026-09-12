# Phase 6 Acceptance Report

Date: 2026-09-12

Result: PASS

## Scope delivered

Phase 6 delivers cache-free TDCC shareholding-distribution snapshot ingestion
and PIT-safe reads:

- draft snapshot parent plus source-native distribution children;
- PostgreSQL seal-generated aggregate hash and trusted visibility time;
- a reusable DB hash function with byte-order (`COLLATE "C"`) bucket ordering;
- atomic `write_snapshot` that reuses an identical sealed revision, including
  when a concurrent writer seals it first;
- append-only, source-validated many-observation lineage for snapshot versions;
- independent append-only publication evidence;
- a DB lower bound that rejects publication before the snapshot date
  (Asia/Taipei); and
- single-date and inclusive-history PIT reads.

Primary evidence:

- `src/stock_data_center/tdcc/`
- `tests/integration/test_phase6_tdcc.py`
- `tests/unit/test_phase6_contract.py`
- `tests/integration/test_phase1_schema.py` (TDCC seal visibility and
  real multi-connection seal concurrency)
- `migrations/versions/a4c7e2d91b36_implement_phase6_tdcc.py`
- [TDCC contract](../tdcc.md)

The repository now contains eleven sequential migrations. The Phase 6
migration has no cache impact: no cache exists, no TDCC writer existed before
this phase, the hash payload is unchanged, and no PIT timestamp is backdated.
No REST API, Redis/cache implementation, TDCC fetch adapter, or
shareholding-concentration calculator was added.

## Required acceptance criteria

| Criterion | Result | Concrete evidence |
| --- | --- | --- |
| Snapshot is invisible before seal | PASS | `test_draft_snapshot_is_invisible_until_seal` builds a draft with all buckets and affirmative evidence; System PIT, Market PIT, and `history` all return nothing until `seal`, then return the exact version, seal hash, and distribution. |
| Distribution cannot mutate after seal | PASS | `test_distribution_parent_and_seal_cannot_mutate_after_seal`: writer bucket insert, raw child update/delete, parent update/delete, and seal update fail with SQLSTATE `55000`; a second seal fails with `23505`. `test_tdcc_seal_serializes_with_concurrent_child_mutation` (Phase 1, real separate connections) covers both lock orderings. |
| Backfilled historical data does not falsify system PIT | PASS | `test_backfilled_history_does_not_falsify_system_pit`: a 2019 snapshot sealed now is absent at a System PIT cutoff before the seal, visible afterwards with `ingested_at` equal to the trusted seal time, and market-invisible without evidence; a raw seal insert with a backdated `ingested_at` and fake hash is overwritten by PostgreSQL. `test_backfill_with_proven_publication_respects_knowledge_cutoff`: proven 2019 publication evidence remains invisible under a knowledge cutoff before its DB-generated `recorded_at`. |
| Dataset-specific regression tests exist | PASS | Twenty focused Phase 6 tests (13 integration, 7 unit) listed below. |

## Additional invariant evidence

```text
snapshot_date is effective time, not publication time        PASS
publication before snapshot date rejected (23514)             PASS
unknown publication / Market PIT invisible                    PASS
unknown publication / System PIT visible after seal           PASS
knowledge cutoff for later-recorded backfill evidence         PASS
evidence retraction without a new business revision           PASS
changed distribution = independent revision (system/market)   PASS
identical re-fetch reuses version, keeps both lineages        PASS
bucket order does not change business identity                PASS
concurrent identical seal falls back to reuse                 PASS
hash function == seal hash, byte-order bucket ordering        PASS
pre-Phase-6 seal hash preserved by migration                  PASS
populated snapshot-lineage migration backfill                 PASS
cross-source observation lineage rejected (23514)             PASS
observation links append-only (55000)                         PASS
non-canonical source isolated unless explicitly requested     PASS
history omits drafts and PIT-invisible dates                  PASS
values storage would round are rejected by the writer         PASS
no concentration calculator / CACHE_BACKEND=none              PASS
```

## Test discrimination

The first red run failed at collection because `stock_data_center.tdcc` did not
exist, so key guards were additionally mutation-checked against the finished
implementation:

| Mutation | Failing test |
| --- | --- |
| hash ordered by default collation (no `COLLATE "C"`) | `test_database_hash_function_matches_seal_and_orders_buckets_bytewise` |
| publication-time trigger removed | `test_publication_cannot_precede_the_snapshot_date` |
| concurrent-seal fallback disabled | `test_identical_revision_sealed_concurrently_is_reused_not_duplicated` |
| writer rounding guards removed | `test_bucket_values_are_never_silently_rounded_by_storage` |

Disabling only the pre-seal lookup did not fail any test, because the
unique-index fallback alone also reuses the identical revision. That fallback
path now has its own dedicated test (the third row).

## Verification

```text
PostgreSQL:                 18 (postgres:18)
pytest:                     116 passed (Phase 5 baseline: 96 passed)
Phase 6 focused tests:      20 passed (13 integration, 7 unit)
TDCC seal concurrency:      PASS (real separate connections)
alembic migrations:         11, single head a4c7e2d91b36
alembic check:              No new upgrade operations detected
alembic round-trip:         downgrade base / upgrade head PASS
Python compileall:          PASS
git diff --check:           PASS
```

## Phase decision

All required Phase 6 criteria pass. Phase 6 is ready for review. Phase 7 must
not begin until this phase is reviewed and accepted.
