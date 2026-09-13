# Phase 9 Pilot 3 Report: Security Lifecycle History

## Scope

This is an incremental Phase 9 milestone report, not the Phase 9 acceptance
report. It adds authoritative TWSE/TPEx venue listing and delisting history,
including explicit TPEx-to-TWSE transfer reconciliation. Other Phase 9 domains
remain outside this milestone.

## Results

| Criterion | Result | Evidence |
| --- | --- | --- |
| Official historical contracts | PASS | `TWSEListingHistoryAdapter`, `TWSEDelistingHistoryAdapter`, `TPExListingHistoryAdapter`, and `TPExDelistingHistoryAdapter` pin the official table fields, validate ROC dates and stable identities, and reject changed or ambiguous source shapes. |
| Complete TWSE representation | PASS | The adapter uses official TWSE RWD because its 2026-09-13 artifact retains all 792 listing dates; the alternate official OpenAPI representation omitted 377 older dates. |
| Raw-first lifecycle | PASS | Every resource uses `RawFirstImporter`: immutable content-addressed raw capture precedes parsing and canonical writes; succeeded checkpoints, advisory locking, ownership/CAS checks, and raw-store fingerprinting remain shared infrastructure. |
| Deterministic normalization | PASS | Listing and delisting observations are constructed solely from the captured event row. Delisting normalization does not copy current DB state, so import order cannot change business content. |
| Stable identity/source isolation | PASS | Venue stays effective-dated metadata on one stable security-code identity. TWSE and TPEx writes remain independent source histories; no synthetic cross-source version is created. |
| Listing/delisting semantics | PASS | Listing date is both the venue-state `effective_from` and `listed_on`; delisting date is both terminal `effective_from` and `delisted_on`. Absence never implies delisting. |
| Transfer reconciliation | PASS | A TWSE note containing `櫃轉市` is retained as append-only explicit transfer evidence. Final reconciliation is re-runnable from canonical histories, matches only a same-code/same-date TPEx terminal state, and never merges source histories. Permanent regressions cover both source-import orders plus a genuinely unmatched transfer and prove convergence. |
| PIT honesty | PASS | Event dates are not announcement times. Every imported event has `published_at=NULL`; real-event regressions prove post-import System PIT visibility and Market PIT invisibility. |
| Idempotency/provenance | PASS | Repeated identical imports deduplicate business/evidence identity while adding raw-artifact and publication-evidence observation lineage. |
| Restart behavior | PASS | A simulated crash after durable capture resumes from hash-verified retained bytes with a fetcher that fails if called. |
| Quarantine boundary | PASS | Invalid table width/shape is quarantined after raw capture. Shared operational-failure regressions prove infrastructure/programming failures remain captured and resumable rather than being mislabeled as source data. |
| Reconciliation/coverage | PASS | Import manifests record requested year, event kind, actual event range, source/normalized counts, exact fields, unknown-publication count, coverage semantics, and provisional explicit-transfer counts. The separate final report classifies each event as matched, genuinely unmatched, or pending required source history. Zero-event TPEx years are valid imported coverage, not fabricated event rows. |
| Phase boundary | PASS | No source-policy hook, trading calendar, bulk optimization, derived calculation, cache, other dataset, or unrelated refactor was added. |

## Verified source coverage

Official artifacts inspected on 2026-09-13:

```text
TWSE listing:    792 events, 2001-01-03 through 2026-08-11
TWSE delisting:  265 events, 2001-01-20 through 2026-09-01
TWSE 櫃轉市:     240 explicit listing-note events

TPEx listing:    year-scoped; first observed non-empty year 2005
TPEx delisting:  year-scoped; first observed non-empty year 1995
```

These are exposed-resource ranges, not a claim of all-time completeness. TPEx
can legitimately return zero rows for a requested year. Each retained artifact
and manifest records the actual result.

## Executed checks

```text
focused adapter and integration tests
    16 passed, 1 live-source test skipped

full suite on a new PostgreSQL database
    224 passed, 3 skipped (live network tests are opt-in)

isolated Alembic upgrade/check/downgrade/upgrade/check
    PASS

ruff check on changed ingestion and test files
    PASS
```

Permanent coverage includes exact source-field/date validation, incorrect year,
duplicate event identity, empty TPEx year, raw-first quarantine, captured resume,
business/evidence deduplication, transfer source isolation, stable identity,
System PIT, and unknown-publication Market PIT behavior.

The opt-in live-source test imports all four resources. Local TPEx TLS chain
validation was intermittently unavailable during development; TLS verification
was not disabled. Exact official payloads retrieved with valid verification
were parsed by the pinned adapters.

## Remaining Phase 9 work

MOPS revenue and financial/XBRL, TDCC, institutional/margin/SBL, available
Phase 8 observed domains, legacy migration, the broader representative pilot,
and full historical backfill remain incomplete. Source-policy hooks and
performance optimization remain deferred until broader reuse and measurement.
