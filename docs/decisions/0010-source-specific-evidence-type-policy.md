# ADR-0010: Source-specific accepted evidence types

- Status: Accepted
- Date: 2026-09-11
- Clarifies: ADR-0004

## Context

Evidence type is semantically relevant to Market PIT. Ranking all evidence rows
solely by their per-event quality could let a high-ranked but unsupported type,
such as an estimate, override publication evidence that the exact dataset
source policy accepts.

## Decision

`dataset_sources.accepted_evidence_types` is a non-empty source-specific
allowlist keyed by `(dataset_code, source)`. The migration assigns existing
sources the conservative `official`-only policy. An operator must explicitly
add any additional accepted type for each source.

Authoritative evidence resolution applies operations in this order:

1. enforce the exact dataset/source capability and verification policy;
2. retain only evidence recorded by the knowledge cutoff;
3. retain only evidence whose `evidence_type` is in that source's allowlist;
4. remove superseded accepted events, leaving accepted chain heads;
5. rank those heads by `quality_rank`, `recorded_at`, and evidence ID,
   descending; and
6. interpret assertion/correction as affirmative and unknown/retraction as
   invisible.

Filtering precedes supersession. Therefore, an unsupported correction or
retraction cannot suppress an otherwise authoritative accepted assertion. An
accepted correction/retraction retains the existing chain semantics.

## Consequences

- Evidence policy for one source cannot authorize evidence for another source.
- A version with only unsupported evidence is Market-PIT invisible but may
  remain System-PIT visible.
- The allowlist is configuration, not inferred from evidence currently present.
- This adds one Phase 2 schema migration. There is no cache impact because no
  cache implementation or namespace exists yet.
