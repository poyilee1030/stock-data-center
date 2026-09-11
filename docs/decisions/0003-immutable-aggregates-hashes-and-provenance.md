# ADR-0003: Immutable Aggregates, Hashes, and Provenance

- Status: Accepted
- Date: 2026-09-11

## Context

Financial filings and TDCC snapshots contain a parent and many child rows. A
partially written aggregate must not become historically visible. Business
identity, publication evidence identity, and raw-byte identity also serve
different audit purposes and cannot share one ambiguous hash.

## Decision

### Aggregate lifecycle

Complex datasets use dataset-specific seal tables with real foreign keys, such
as `financial_filing_seals` and `tdcc_snapshot_seals`.

```text
draft parent -> validated children -> seal -> visible and immutable
```

A draft may be committed but all resolvers ignore it. The trusted DB-generated
seal `ingested_at` is the entire aggregate's system-visible time. After sealing,
PostgreSQL constraints/triggers reject parent update/delete and child
insert/update/delete. Repository call order is not an immutability control.

### Hash boundaries

Three identities remain separate:

- `business_content_hash`: storage-generated from canonical domain values only;
- `publication_evidence_hash`: storage-generated from canonical evidence
  assertions and relationships; and
- `raw_artifact_hash`: SHA-256 of exact raw bytes.

Business hashes exclude publication/recording/ingestion/seal times, raw artifact
and ingest-run identifiers, fetch metadata, and evidence fields. Each dataset
must define its typed canonical business serialization before its table is
introduced. Canonical collections are ordered; timestamps include an offset and
are normalized; hashes never rely on Python object representations or caller
supplied digests.

Financial facts use namespace-aware concept QName plus a non-null canonical
`context_hash`. Context identity includes the entity, period type and bounds,
explicit dimensions, typed dimensions, and scenario/segment where applicable;
dimension collections are canonically ordered. Unit identity also participates
in fact uniqueness.

### Provenance

Raw artifacts are immutable and content-addressed. Existing paths cannot be
overwritten with different bytes. Where a normalized/evidence record stores
both `raw_artifact_id` and `ingest_run_id`, a composite database foreign key (or
an equally strong relational constraint) proves that the artifact belongs to
that run.

Normal write paths use DB-controlled authoritative times. Preserved historical
times are limited to a separately audited migration path.

## Consequences

- A transaction may retain a recoverable draft without leaking partial data.
- Sealed aggregates remain reproducible under every future query.
- Duplicate business content can share identity while every fetch retains its
  own provenance.
- Phase 1 must enforce these rules in PostgreSQL, not merely in Python.
