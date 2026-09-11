# Phase 0 Acceptance Report

- Phase: 0 — Freeze Contracts and ADRs
- Date: 2026-09-11
- Result: PASS

## Scope delivered

Phase 0 produced documentation and accepted architectural decisions only. It
did not introduce a database schema, application code, Redis dependency, or any
later-phase implementation.

Normative documents:

- [Architecture contract](../architecture.md)
- [Point-in-time semantics](../pit_semantics.md)
- [Optional cache contract](../cache.md)

Accepted decisions:

- [ADR-0001: temporal and PIT model](../decisions/0001-temporal-and-pit-model.md)
- [ADR-0002: business revisions and publication evidence](../decisions/0002-business-revisions-and-publication-evidence.md)
- [ADR-0003: immutable aggregates, hashes, and provenance](../decisions/0003-immutable-aggregates-hashes-and-provenance.md)
- [ADR-0004: source capability and cross-source policy](../decisions/0004-source-capability-and-cross-source-policy.md)
- [ADR-0005: optional cache architecture](../decisions/0005-optional-cache-architecture.md)

## Required acceptance criteria

| Criterion | Result | Concrete evidence |
| --- | --- | --- |
| `information_as_of` and `knowledge_as_of` are defined | PASS | `docs/pit_semantics.md`, “Status and terminology” and “Market PIT”, define both clocks, their inclusive comparisons, required pairing, and reconstruction modes. ADR-0001 accepts them. |
| `system_as_of` is defined | PASS | `docs/pit_semantics.md`, “System PIT”, defines single-version and aggregate eligibility. ADR-0001 accepts the separate system clock. |
| Publication evidence uses `published_at` plus DB-controlled `recorded_at` | PASS | ADR-0002 requires both fields, makes `recorded_at` DB-controlled, and defines deterministic cutoff resolution. `docs/pit_semantics.md`, “Trusted times and backfill”, rejects caller backdating. |
| Business and evidence revisions are separate | PASS | ADR-0002 declares two append-only histories and states that better evidence cannot create a business revision; its duplicate-fetch rule preserves lineage independently. |
| Only sealed complex aggregates are visible | PASS | ADR-0003 defines the draft-to-seal lifecycle, resolver invisibility before seal, trusted seal time, dataset-specific seal FKs, and DB-enforced post-seal immutability. |
| Source-level PIT capability is defined | PASS | ADR-0004 assigns capability to `(dataset_code, source)`, defines owned fields and explicit failure for unsupported combinations, and prohibits capability inheritance. |
| Redis is explicitly optional | PASS | `docs/cache.md`, “Authority and placement”, accepts both `CACHE_BACKEND=none` and `CACHE_BACKEND=redis`; ADR-0005 makes PostgreSQL authoritative and Redis disposable. |
| Cache-on/cache-off equivalence is an invariant | PASS | `docs/cache.md` states equality across NullCache, Redis cold, Redis warm, and Redis failure fallback, including provenance and schema. ADR-0005 accepts the invariant. |
| PIT-aware cache-key fields are documented | PASS | `docs/cache.md`, “Canonical identity”, specifies contract/schema/resolver versions, dataset, all business parameters, resolved source, PIT mode, and the mode-specific cutoff fields, plus deterministic serialization and hashing. |

All nine required criteria pass. Phase 1 may begin only through a separate,
explicit task and must not introduce Redis.

## Additional contract evidence

- Publication correction/retraction and deterministic authoritative-evidence
  ordering: ADR-0002.
- Hash separation, XBRL context identity, duplicate-fetch behavior, and lineage
  constraints: ADR-0003.
- Cross-source ambiguity and canonical-source policy: ADR-0004.
- Cache failure, namespace evolution, payload completeness, alias handling, and
  temporal migration impact: `docs/cache.md` and ADR-0005.
- Ownership and downstream access boundaries: `docs/architecture.md`.

## Verification

- Required Phase 0 documents and all five ADRs were checked for presence.
- Internal Markdown links in Phase 0 documents were checked to resolve to local
  files.
- Whitespace/error checking was run across the delivered Markdown files.
- No executable project or test configuration exists yet; Phase 0 is a
  documentation-only phase, so there was no application test suite to run.
- Repository inspection confirmed no Phase 1 schema or cache implementation was
  added.
