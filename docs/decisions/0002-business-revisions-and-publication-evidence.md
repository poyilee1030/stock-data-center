# ADR-0002: Business Revisions and Publication Evidence

- Status: Accepted
- Date: 2026-09-11

## Context

A value can remain unchanged while knowledge about when it was published is
corrected. Treating those events as the same version chain creates false
business revisions and makes historical reconstruction depend on mutable rows.

## Decision

Business versions and publication evidence are separate append-only histories.
Every evidence event references exactly one business version and carries:

- a storage-generated evidence identifier;
- an evidence kind (publication assertion, correction, retraction, or unknown);
- `published_at`, nullable when no reliable publication instant is known;
- DB-controlled `recorded_at`;
- evidence source/type and its quality classification;
- provenance; and
- an optional `supersedes_evidence_id` referencing evidence for the same
  business version and dataset source.

Historical evidence rows are never updated or deleted. A correction appends a
new event that supersedes the old event. A retraction appends a retraction event
that supersedes the assertion being withdrawn. Improving evidence alone does
not create a new business version.

### Deterministic evidence resolution

For a business version and `knowledge_as_of`, the resolver:

1. keeps only evidence with `recorded_at <= knowledge_as_of`;
2. validates evidence against the capability policy of that exact
   `(dataset_code, source)`;
3. removes any event superseded by another retained event, leaving chain heads;
4. ranks remaining heads by configured evidence quality, then `recorded_at`,
   then the storage-generated evidence identifier, all descending; and
5. interprets the first head: retraction or unknown means market-invisible;
   otherwise its non-null `published_at` is authoritative.

The quality ordering is explicit dataset-source configuration. API handlers do
not choose evidence. A superseding event replaces its predecessor before
quality ranking, so a correction or retraction cannot lose to the row it
supersedes. Cycles, cross-version supersession, and cross-source supersession
must be rejected by storage constraints or trusted write logic.

If multiple independent chain heads exist, the stable ranking above produces
one result. Phase 2 must implement and test this algorithm without changing it;
changing the algorithm later requires a superseding ADR and resolver/cache
namespace review.

### Duplicate fetches

Business identity is determined from canonical business values. If a fetch has
the same business hash as the existing version for the same logical key and
source, no new business version is created. The new ingest run, raw artifact,
and observation/lineage association are still retained. Evidence discovered by
that fetch is appended independently when it is new.

## Consequences

- Evidence knowledge can improve without fabricating a value revision.
- Historical knowledge cutoffs remain reproducible because evidence is
  append-only and selected by `recorded_at`.
- Retractions and corrections remain auditable.
- Evidence ordering becomes a shared resolver contract rather than route logic.
