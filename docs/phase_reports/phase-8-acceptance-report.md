# Phase 8 Acceptance Report

Date: 2026-09-12

Result: PASS

## Scope delivered

Phase 8 delivers cache-free normalized ingestion and PIT-safe reads for market
indices, corporate actions, and official source valuation. The Phase 1 version
tables remain authoritative. A focused migration adds DB value constraints,
final storage-generated business hashes, immutable repeat-observation lineage,
stable market-index identity, and dataset-aware publication-time guards.

Primary evidence:

- `src/stock_data_center/market_reference/`
- `migrations/versions/6b4e8d1f2a73_implement_phase8_market_reference.py`
- `tests/integration/test_phase8_market_reference.py`
- `tests/unit/test_phase8_contract.py`
- [Phase 8 contract](../market_reference.md)
- [ADR-0014](../decisions/0014-phase8-market-reference-semantics.md)

The migration backfills observation links and rehashes populated revisions
without changing ingestion or publication timestamps. Downgrade restores the
previous hash contract. It has no cache impact because no cache exists.

No REST API, Redis/cache implementation, source fetch adapter, canonical
derived calculator, adjusted-price calculation, or model feature was added.

## Required acceptance criteria

| Criterion | Result | Concrete evidence |
| --- | --- | --- |
| Index history is PIT-safe | PASS | `test_index_history_is_pit_safe_and_source_isolated` proves a quote is absent before publication, visible afterward through inclusive history, and independently resolved for two sources. The shared resolver enforces information and knowledge cutoffs. |
| Corporate actions have explicit effective/announcement semantics | PASS | The contract and ADR distinguish source `announcement_date` from effective `ex_date`. `test_corporate_action_is_visible_on_announcement_not_ex_date` proves reliable evidence can expose an announced future action before its ex-date. PostgreSQL rejects an announcement after ex-date and evidence before a known announcement. |
| Source-published valuation is distinguishable from computed valuation | PASS | `official_valuation_versions` is documented and typed as observed source data. Computed values remain separately versioned `valuation_metrics:v1` Phase 9 output; the unit contract test freezes this boundary. |
| Backfill/revision provenance is preserved | PASS | `test_historical_backfill_uses_actual_ingestion_time` proves an old business date is not system-visible before actual trusted ingestion. Equivalent refetches reuse one revision while retaining both raw/run observations. The populated migration test preserves `ingested_at`, backfills lineage, and restores the old hash on downgrade. |
| Dataset-specific regression tests exist | PASS | Thirteen focused tests cover the three datasets, source isolation, Market/System PIT, publication/effective dates, unknown publication, explicit monetary scaling, equivalent-value deduplication, DB guards, observation lineage, and populated migration round-trip. |

## Frozen semantic evidence

```text
index and valuation trade_date are effective dates       PASS
corporate announcement_date differs from ex_date         PASS
market visibility follows publication evidence           PASS
unknown publication remains Market-PIT invisible         PASS
historical backfill uses actual DB ingestion time         PASS
TWD monetary storage uses major units                     PASS
source amount scale is explicit before normalization      PASS
equivalent amount representations deduplicate             PASS
source histories remain independent                       PASS
repeat fetches retain every artifact/run                  PASS
official valuation remains observed source data           PASS
computed valuation remains versioned derived data         PASS
no Redis/cache or Phase 9 calculator introduced           PASS
```

## Verification

```text
PostgreSQL:                 18 (postgres:18)
pytest:                     160 passed (Phase 7 baseline: 147 passed)
Phase 8 focused tests:      13 passed (7 integration, 6 unit)
alembic migrations:         13, single head 6b4e8d1f2a73
alembic check:              No new upgrade operations detected
populated migration:        upgrade/downgrade/hash restoration PASS
Python compileall:          PASS
git diff --check:           PASS
```

## Phase decision

All required Phase 8 criteria pass. Phase 8 is ready for review. Phase 9 must
not begin until this phase is reviewed and accepted.
