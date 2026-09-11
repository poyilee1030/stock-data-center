# ADR-0009: Aggregate market PIT uses the seal knowledge cutoff

- Status: Accepted
- Date: 2026-09-11

## Context

Financial and TDCC aggregates can be committed as drafts, while only their
trusted seal makes the parent and children complete and PIT-visible. Publication
evidence may technically be appended after the parent exists but before its
seal. If market resolution checked only the current existence of a seal, a seal
created later could make that incomplete aggregate appear in a reconstruction
whose `knowledge_as_of` predates the seal.

## Decision

Market-PIT aggregate candidates must satisfy both the ordinary publication
evidence rules and:

```text
seal.ingested_at <= knowledge_as_of
```

System PIT continues to use `seal.ingested_at <= system_as_of`. The seal remains
operational completeness metadata, not market publication time; market
eligibility still uses authoritative `published_at <= information_as_of`.

## Consequences

- A later seal cannot leak a draft into an earlier knowledge reconstruction.
- Current-best reconstruction sees the aggregate once its explicit knowledge
  cutoff includes both the evidence and seal.
- No schema migration is required.
- Cache namespaces do not yet exist in Phase 2, so there is no cache impact.
