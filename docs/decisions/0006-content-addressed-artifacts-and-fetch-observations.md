# ADR-0006: Separate Content-Addressed Artifacts from Fetch Observations

- Status: Accepted
- Date: 2026-09-11

## Context

A raw artifact is identified by exact bytes, while an ingest run represents a
particular fetch attempt. Storing one `ingest_run_id` directly on a deduplicated
artifact either loses later duplicate-fetch provenance or duplicates the same
content-addressed object.

## Decision

Use two immutable relations:

- `raw_artifacts` stores one content identity per SHA-256 and storage URI; and
- `raw_artifact_observations` links that artifact to every ingest run that
  fetched it and records fetch metadata.

Normalized business/evidence rows reference an observation using the composite
key `(raw_artifact_id, ingest_run_id)`. Database foreign keys prove that those
two identifiers belong together. Trusted insert triggers also verify the
observation's dataset/source against the normalized row.

The raw storage abstraction computes SHA-256 from exact bytes before registering
the immutable artifact. PostgreSQL validates digest form and content-addressed
URI structure; it does not claim to re-read external filesystem bytes.

## Consequences

- Identical bytes are stored once but every fetch remains auditable.
- Duplicate business content can avoid a false version while retaining its new
  ingest-run observation.
- Mismatched lineage identifiers and cross-source reuse are rejected by the
  database.
- Deleting or mutating artifact history is forbidden.
