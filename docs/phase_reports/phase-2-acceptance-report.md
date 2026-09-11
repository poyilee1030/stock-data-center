# Phase 2 Acceptance Report

- Phase: 2 — Core PIT Resolver
- Date: 2026-09-11
- Result: PASS

## Scope delivered

Phase 2 adds one reusable, cache-free resolver contract for all Phase 1
observed datasets. It implements explicit market/system contexts, exact
dataset-source policy, deterministic authoritative-evidence selection,
single-row revision selection, aggregate seal visibility, and immutable
provenance-bearing results.

Primary evidence:

- `src/stock_data_center/pit/models.py`
- `src/stock_data_center/pit/contracts.py`
- `src/stock_data_center/pit/source_policy.py`
- `src/stock_data_center/pit/evidence.py`
- `src/stock_data_center/pit/resolver.py`
- `tests/integration/test_phase2_pit_resolver.py`
- [Core PIT resolver contract](../pit_resolver.md)
- [ADR-0009](../decisions/0009-aggregate-market-pit-seal-cutoff.md)

No schema migration, ingestion pipeline, dataset API, derived calculator,
Redis package, or cache abstraction was added.

## Required acceptance criteria

| Criterion | Result | Concrete evidence |
| --- | --- | --- |
| Resolver correctness does not depend on Redis | PASS | The resolver takes only a SQLAlchemy connection and PostgreSQL-backed inputs. A permanent test scans the PIT package for Redis dependencies; the complete suite runs without Redis configuration or service. |
| Deterministic authoritative-evidence selection exists | PASS | `PublicationEvidenceResolver` removes superseded events before ranking chain heads by quality, trusted recording time, and evidence ID. Tests cover correction/retraction precedence (including lower-quality superseding events), independent-head quality ordering, and business revision tie-breaking by publication time/version ID. |
| Market/system semantics match Phase 0 | PASS | Tests cover historically reproducible vs current-best market reconstruction, two cutoffs, unknown publication, late backfill, system ingestion cutoffs, source-level capability, and unsealed aggregate invisibility/seal timing. A later seal is also prevented from leaking into an earlier market knowledge cutoff. Aware context types reject naive timestamps. |
| Queries return provenance | PASS | Market and system results include business version/hash, all PIT cutoffs, artifact ID/hash/URI, fetch observation, ingest-run status/times, authoritative evidence when applicable, and aggregate seal identity/time. Integration assertions inspect these fields. |

All four required criteria pass.

## Permanent regression coverage

```text
market current-best reconstruction          PASS
market historically reproducible            PASS
system PIT late ingestion/backfill           PASS
publication evidence correction              PASS
publication evidence retraction              PASS
unknown publication                          PASS
source-level capability isolation            PASS
unsealed aggregate invisibility               PASS
deterministic evidence/revision tie-breakers  PASS
timezone/logical-key validation               PASS
```

## Verification performed

```text
PostgreSQL:                 18 (postgres:18)
pytest:                     39 passed
Phase 2 focused tests:      10 passed
alembic check:              No new upgrade operations detected
Python compileall:          PASS
git diff --check:           PASS
Redis required/running:     no
```

Phase 3 may begin only as a separate task.
