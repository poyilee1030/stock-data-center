# Phase 5 Acceptance Report

Date: 2026-09-12

Result: PASS

## Scope delivered

Phase 5 delivers cache-free financial filing aggregate ingestion and PIT-safe
financial/XBRL reads:

- draft parent plus XBRL fact and curated-summary children;
- PostgreSQL seal-generated aggregate hash and trusted visibility time;
- canonical namespace-aware concept QNames and full context identity;
- independent append-only publication evidence;
- Q4 visibility governed only by authoritative evidence;
- actual basic EPS from the exact PIT-resolved filing version; and
- many-observation lineage for unchanged filing/evidence fetches.

Primary evidence:

- `src/stock_data_center/financials/`
- `tests/integration/test_phase5_financial_xbrl.py`
- `tests/unit/test_phase5_contract.py`
- `tests/integration/test_phase1_schema.py`
- `migrations/versions/e51d9b7f204a_enforce_phase5_financial_contract.py`
- [Financial/XBRL contract](../financial_xbrl.md)

The repository now contains nine sequential migrations. The Phase 5 migration
adds filing observation lineage, backfills existing filing lineage, requires a
canonical Clark QName, and requires exactly one numeric/text fact value. It has
no cache impact. No REST API, Redis/cache implementation, or canonical-derived
calculator was added.

## Required acceptance criteria

| Criterion | Result | Concrete evidence |
| --- | --- | --- |
| Unsealed filings are invisible | PASS | The dataset service regression creates a committed draft with facts, summary, and evidence; both System PIT and the shared aggregate resolver ignore it until the DB seal exists. |
| Child insert after seal fails | PASS | A focused writer regression attempts a summary insert after sealing and PostgreSQL returns SQLSTATE `55000`. Phase 1 permanent tests also cover fact update/delete/insert and parent/seal mutation. |
| Concurrent seal/child behavior preserves hash correctness | PASS | `test_financial_seal_serializes_with_concurrent_child_mutation` uses separate database connections for both lock orderings: seal-first rejects the child; child-first commits and the seal hash includes it. |
| Dimensional facts coexist correctly | PASS | Five facts with distinct explicit dimensions, typed dimensions, scenario, or segment receive five distinct storage-generated context hashes and coexist under the same QName/unit. |
| Canonical duplicate facts are rejected | PASS | Repeating an equivalent full context under the same filing/QName/unit violates `uq_financial_fact_identity`; DB checks also reject a local-name-only concept and a fact with both value types. |
| Q4 availability is evidence-controlled | PASS | A sealed 2024-Q4 filing published on 2025-03-15 remains invisible at the February cutoff and becomes visible only after the evidence publication instant. No period/calendar shortcut exists in the service. |
| Historical EPS actuals are PIT-safe | PASS | `actual_eps` resolves the sealed filing first. It is absent before public availability and at a knowledge cutoff before evidence recording, then returns `basic_eps` from that exact resolved version. |
| Dataset-specific regression tests exist | PASS | Twelve focused Phase 5 tests cover value contracts, seal visibility/immutability, Q4/two-clock EPS, unknown evidence, full context identity, duplicate/QName constraints, repeated lineage, cross-source rejection, populated migration backfill, scope, and dependency boundaries. |

## Additional invariant evidence

```text
business revision vs publication evidence separation    PASS
unknown publication / Market PIT                        PASS
unknown publication / System PIT                        PASS
knowledge cutoff for later-learned evidence              PASS
source-specific accepted evidence policy                 PASS (shared resolver)
deterministic correction/retraction ranking               PASS (shared resolver)
namespace-aware concept identity                          PASS
full explicit/typed/scenario/segment context identity     PASS
unchanged filing / no fake business revision              PASS
version-linked repeated observations                      PASS
evidence-linked repeated observations                     PASS
cross-source lineage mismatch rejected                    PASS
populated filing-lineage migration backfill               PASS
no TTM EPS / ROE / valuation calculators                  PASS
CACHE_BACKEND=none / no Redis dependency                  PASS
```

## Verification

```text
PostgreSQL:                 18 (postgres:18)
pytest:                     87 passed
Phase 5 focused tests:      12 passed (8 integration, 4 unit)
financial seal concurrency: PASS (real separate connections)
alembic migrations:         9, single head e51d9b7f204a
alembic check:              No new upgrade operations detected
Python compileall:          PASS
git diff --check:           PASS
```

## Phase decision

All required Phase 5 criteria pass. Phase 5 is ready for review. Phase 6 must
not begin until this phase is reviewed and accepted.
