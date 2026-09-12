# ADR 0015: Phase 9 raw-first import framework

## Status

Accepted for the first Phase 9 pilot milestone.

## Decision

Real-source imports use a durable two-transaction boundary:

1. fetch bytes and write them to immutable SHA-256-addressed raw storage;
2. commit the raw artifact, fetch observation, ingest run, and import checkpoint;
3. only then parse, normalize, and call the existing trusted domain writer;
4. commit successful business/evidence writes and reconciliation, or append an
   immutable quarantine record while keeping the raw capture.

An explicit `import_id` identifies one restartable source scope. A successful
resource checkpoint is not fetched again when that same import is resumed. A
new import of the same resource creates a new ingest run and raw observation;
storage-generated business and publication-evidence hashes continue to dedupe
unchanged semantic identities.

A `captured` checkpoint is also a complete restart input. Resume reads the
checkpoint's original content-addressed file, verifies its byte size and
SHA-256 against PostgreSQL, and continues with the original ingest run without
calling the external source. The local store canonicalizes its root once,
persists absolute locators, and contributes its backend/root identity to the
configuration fingerprint, so process CWD changes cannot silently redirect a
resume. A missing or corrupt retained artifact is an operational storage
failure: the checkpoint stays captured for repair/retry and is never replaced
silently with newly fetched bytes.

Only explicit `SourceDataError` validation failures enter quarantine.
Infrastructure, database, storage, and unexpected programming errors preserve
the captured checkpoint, raw identity, and original running ingest run so a
retry continues from the retained artifact. The manifest records the
operational failure without increasing the rejected/quarantined count.

One PostgreSQL session advisory lock serializes the full lifecycle of each
`import_id + resource_key`. State transitions additionally compare the
checkpoint's captured status, ingest-run ID, and raw-artifact ID. A completed
checkpoint is reusable only when its referenced ingest run is also successful.

The initial adapters intentionally cover one daily-market security/month from
each exchange:

| Adapter | Source quantity | Source money | Canonical quantity | Canonical money |
| --- | --- | --- | --- | --- |
| `twse-stock-day:v1` | shares | TWD | shares | TWD |
| `tpex-trading-stock:v1` | 1,000-share lots | thousand TWD | shares | TWD |

These unit contracts are adapter constants. Numeric magnitude is never used to
guess a scale. An unexpected field list, identity, month, numeric shape, or row
ordering rejects the resource and records its raw artifact in quarantine.
Negative OHLC/volume/value/count and prices outside the reported low/high range
are explicit source-data failures. Equivalent PostgreSQL checks protect the
canonical daily-price table from bypassing adapter validation.

## PIT consequences

The official historical query responses prove business observations but do not
prove the original historical publication timestamp. This milestone therefore
writes append-only `unknown` evidence with `published_at = NULL`. The versions
are visible under System PIT only after their actual PostgreSQL insertion time
and remain Market-PIT invisible until separate reliable publication evidence is
recorded. Neither trade dates nor fetch times are copied into `ingested_at` or
`published_at`.

Pilot 1 does not yet own a trustworthy expected trading-day calendar. Monthly
coverage therefore records `coverage_validation = "not_evaluated"` and
`coverage_gaps = null`. An empty gap list is reserved for a future check that
actually compares the response with authoritative expected trading dates.

## Scope boundary

This ADR does not declare the full Phase 9 pilot or full-market backfill ready.
Later Phase 9 milestones must add security metadata/transfer history and every
remaining observed v1 domain before the Phase 9 acceptance report can pass.
No Phase 10 derived calculator and no Redis dependency is introduced.
