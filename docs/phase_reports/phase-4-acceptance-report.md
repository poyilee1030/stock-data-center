# Phase 4 Acceptance Report

Date: 2026-09-12

Result: PASS

## Scope delivered

Phase 4 delivers cache-free normalized ingestion and PIT-safe queries for
observed monthly revenue:

- append-only revenue/currency business revisions;
- independent append-only publication evidence;
- exact source identity and raw/ingest provenance;
- revenue normalized to the currency's major unit before storage and hashing;
- many-observation lineage for reused business/evidence identities;
- single-period and inclusive period-history queries;
- delayed-publication and knowledge-cutoff reconstruction; and
- idempotent unchanged-content ingestion without false revisions.

Primary evidence:

- `src/stock_data_center/monthly_revenue/`
- `tests/integration/test_phase4_monthly_revenue.py`
- `tests/unit/test_phase4_contract.py`
- `migrations/versions/c92e81a40d17_link_repeated_observation_lineage.py`
- [Monthly revenue contract](../monthly_revenue.md)

One focused migration adds append-only version/evidence observation links,
exact dataset/source validation, and backfill for existing rows. No REST API,
Redis/cache package, or derived calculator was added. MoM, YoY, cumulative
revenue, and cumulative YoY remain assigned to the Phase 10
`monthly_revenue_growth:v1` derived contract.

## Review blocker closure

| Finding | Result | Concrete evidence |
| --- | --- | --- |
| Canonical revenue unit | PASS | `revenue` is frozen as the currency's major unit. `SourceRevenueAmount` requires an explicit `RevenueScale`; the regression normalizes `410000000` thousand TWD to `410000000000` TWD and proves it hashes/deduplicates identically to the equivalent canonical value. |
| Duplicate-fetch lineage | PASS | `monthly_revenue_version_observations` links every artifact/run to created and reused versions. `publication_evidence_observations` does the same for created and deduplicated evidence. Tests prove two fetches link to one version, reject cross-source links, expose both observations through the service, and backfill populated pre-migration rows. |

## Required acceptance criteria

| Criterion | Result | Concrete evidence |
| --- | --- | --- |
| Unknown publication is market-invisible | PASS | A dataset-specific regression appends explicit unknown evidence and proves Market PIT returns no row while System PIT returns the legitimately ingested business version. |
| Official publication evidence can later improve reconstruction | PASS | An accepted assertion supersedes unknown evidence on the same business version. The earlier knowledge cutoff remains invisible; a later reconstruction becomes visible without creating another revenue revision. |
| Knowledge cutoff prevents using evidence learned later | PASS | The evidence-improvement and competing-business-revision regressions capture a cutoff before later evidence and prove the resolver retains only knowledge available by that cutoff. |
| Same business content with new evidence does not create business revision | PASS | Source-native thousand-TWD and equivalent major-unit values normalize to the same amount/hash/version. Both artifact/run pairs are durably linked to that version; repeated identical evidence is likewise linked without a fake evidence revision. |
| Dataset-specific PIT tests exist | PASS | Fourteen focused integration/unit tests cover unknown publication, System PIT, delayed publication, later evidence, knowledge cutoffs, business revisions, canonical scale normalization, linked repeated-fetch/evidence provenance, cross-source link rejection, populated migration backfill, source isolation, ordered history, period validation, derived-scope separation, and no cache/HTTP coupling. |

## Permanent regression matrix

```text
unknown publication / Market PIT             PASS
unknown publication / System PIT             PASS
delayed publication information cutoff       PASS
later-learned official evidence               PASS
knowledge-cutoff reconstruction               PASS
revenue business correction                   PASS
unchanged fetch / no false revision           PASS
currency-major unit normalization             PASS
version-linked repeated observations          PASS
evidence-linked repeated observations         PASS
cross-source association rejection            PASS
populated association migration backfill      PASS
independent source histories                  PASS
PIT-safe ordered period history               PASS
```

## Verification

```text
PostgreSQL:                 18 (postgres:18)
pytest:                     75 passed
Phase 4 focused tests:      14 passed
alembic check:              No new upgrade operations detected
Python compileall:          PASS
git diff --check:           PASS
```

## Phase decision

All required Phase 4 criteria pass. Phase 4 is complete. Phase 5 must not begin
until this phase is reviewed and accepted.
