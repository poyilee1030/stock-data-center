# Phase 4 Acceptance Report

Date: 2026-09-12

Result: PASS

## Scope delivered

Phase 4 delivers cache-free normalized ingestion and PIT-safe queries for
observed monthly revenue:

- append-only revenue/currency business revisions;
- independent append-only publication evidence;
- exact source identity and raw/ingest provenance;
- single-period and inclusive period-history queries;
- delayed-publication and knowledge-cutoff reconstruction; and
- idempotent unchanged-content ingestion without false revisions.

Primary evidence:

- `src/stock_data_center/monthly_revenue/`
- `tests/integration/test_phase4_monthly_revenue.py`
- `tests/unit/test_phase4_contract.py`
- [Monthly revenue contract](../monthly_revenue.md)

No schema migration, REST API, Redis/cache package, or derived calculator was
added. MoM, YoY, cumulative revenue, and cumulative YoY remain assigned to the
Phase 9 `monthly_revenue_growth:v1` derived contract.

## Required acceptance criteria

| Criterion | Result | Concrete evidence |
| --- | --- | --- |
| Unknown publication is market-invisible | PASS | A dataset-specific regression appends explicit unknown evidence and proves Market PIT returns no row while System PIT returns the legitimately ingested business version. |
| Official publication evidence can later improve reconstruction | PASS | An accepted assertion supersedes unknown evidence on the same business version. The earlier knowledge cutoff remains invisible; a later reconstruction becomes visible without creating another revenue revision. |
| Knowledge cutoff prevents using evidence learned later | PASS | The evidence-improvement and competing-business-revision regressions capture a cutoff before later evidence and prove the resolver retains only knowledge available by that cutoff. |
| Same business content with new evidence does not create business revision | PASS | An unchanged fetch from a second raw observation reuses the first version ID while preserving both artifact observations; later official evidence targets that same version. |
| Dataset-specific PIT tests exist | PASS | Ten focused integration/unit tests cover unknown publication, System PIT, delayed publication, later evidence, knowledge cutoffs, business revisions, unchanged fetches, source isolation, ordered history, period validation, derived-scope separation, and no cache/HTTP coupling. |

## Permanent regression matrix

```text
unknown publication / Market PIT             PASS
unknown publication / System PIT             PASS
delayed publication information cutoff       PASS
later-learned official evidence               PASS
knowledge-cutoff reconstruction               PASS
revenue business correction                   PASS
unchanged fetch / no false revision           PASS
raw observation preservation                  PASS
independent source histories                  PASS
PIT-safe ordered period history               PASS
```

## Verification

```text
PostgreSQL:                 18 (postgres:18)
pytest:                     71 passed
Phase 4 focused tests:      10 passed
alembic check:              No new upgrade operations detected
Python compileall:          PASS
git diff --check:           PASS
```

## Phase decision

All required Phase 4 criteria pass. Phase 4 is complete. Phase 5 must not begin
until this phase is reviewed and accepted.
