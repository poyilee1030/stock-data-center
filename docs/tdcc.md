# TDCC Shareholding Distribution

## Phase 6 scope

Phase 6 provides cache-free writes and PIT-safe reads for TDCC shareholding
distribution snapshots. A snapshot is a draft parent
(`tdcc_snapshot_versions`) with one `tdcc_distribution` child per source
holding level. Only the PostgreSQL-generated seal makes the aggregate visible.
Phase 6 does not add a REST API, a cache, a real TDCC fetch adapter, or
shareholding-concentration calculations.

The raw distribution is the only observed input for the later canonical
`shareholding_concentration:v1` dataset. Large/mid/small-holder ratios, counts,
spreads, and week-over-week changes are computed in the
later canonical-derived phase and must inherit PIT visibility from snapshots
resolved here.

## Logical key and revisions

The logical key is `(security, source, snapshot_date)`. A business revision is
identified by the storage-generated aggregate hash of the snapshot date and the
complete ordered distribution. A changed distribution for the same key is a
new, independent sealed version; the older version remains queryable under
earlier cutoffs. Publication evidence is appended separately, so learning,
correcting, or retracting a publication time never mutates the snapshot or
creates a false business revision.

Each distribution row retains the source-native `bucket_code` verbatim
(including source aggregate rows such as adjustment or total levels) together
with `holder_count`, `shares`, and `ownership_percent`. Phase 6 does not
interpret bucket ranges; that belongs to the derivation definition. The writer
rejects values that storage would otherwise silently round: `shares` must be a
whole number and `ownership_percent` must have at most 8 decimal places and lie
between 0 and 100. A snapshot needs at least one row and unique bucket codes.

## Aggregate and lineage contract

`TDCCSnapshotWriter.write_snapshot` performs one atomic, savepoint-protected
write: it creates the draft, appends every bucket, asks PostgreSQL for the
draft's canonical hash, and either seals it or reuses the existing identical
sealed revision. When reused, the draft is discarded and only the new
artifact/run pair is linked through `tdcc_snapshot_version_observations`, so a
repeated fetch never creates a fake revision but keeps its provenance. If a
concurrent writer seals the identical revision between the lookup and the
seal, the business-revision unique index rejects the second seal and the writer
falls back to the same reuse path. The lower-level `begin_snapshot`,
`append_bucket`, and `seal` operations remain available.

The canonical hash is computed only in PostgreSQL by
`stockdc_tdcc_snapshot_business_hash`, which the seal trigger also uses. Its
payload is the snapshot date plus every distribution row ordered by
`bucket_code COLLATE "C"`, so the hash is byte-order deterministic and does
not depend on the database default collation. For numeric TDCC level codes
this is the same order as the Phase 1 seal, and existing seal hashes are
unchanged.

Seal and child mutation serialize on the same parent row lock: a child that
locks first is included in the seal hash, and a seal that locks first causes
the child mutation to be rejected. After sealing, PostgreSQL rejects parent
update/delete, distribution insert/update/delete, and seal update/delete. The
observation links are append-only and DB-validated against the exact
`tdcc_snapshot` dataset and source. Existing snapshots are backfilled with
their original lineage by the Phase 6 migration.

## Publication and effective-time semantics

`snapshot_date` is the effective (data) date: the holdings the distribution
describes. snapshot_date is not a publication time and never grants market
visibility. `published_at` records when the distribution became public, and
DB-generated `recorded_at` records when the Data Center learned that evidence.
Market PIT requires affirmative accepted evidence with
`published_at <= information_as_of` and `recorded_at <= knowledge_as_of`, plus
`seal.ingested_at <= knowledge_as_of` (ADR-0009). Unknown publication is
market-invisible. Source capability, accepted evidence types, correction,
retraction, and deterministic ranking use the shared Phase 2 resolver.

A distribution cannot be public before the day it describes. PostgreSQL
therefore rejects TDCC evidence whose `published_at` precedes the start of
`snapshot_date` in Asia/Taipei. This is only a lower bound; the actual
publication instant must come from evidence.

System PIT uses only the trusted seal time. `seal.ingested_at` is overwritten
with `statement_timestamp()`, so a caller cannot backdate it.

## Backfill

Backfilled history follows the global backfill rule:

```text
published_at = proven historical publication time, else published_at = NULL
recorded_at  = actual time the Data Center records the evidence
ingested_at  = actual seal time
```

A snapshot for a 2019 date sealed today is invisible to a System PIT query
before today, and invisible to Market PIT unless evidence proves its
publication. Even with proven historical publication, a Market PIT query whose
`knowledge_as_of` precedes the evidence `recorded_at` does not see it. The
snapshot date and the fetch time are never substituted for publication time.

## Reads

`TDCCSnapshotService.snapshot` maps the security code, resolves
`(security_id, snapshot_date)` through the shared PIT resolver, and only then
reads the distribution of that exact sealed version. `history` resolves each
snapshot date in an inclusive range independently and omits dates with no
visible version; it never returns a draft or a version outside the context.
Distributions are returned with numeric bucket codes in numeric order followed
by other codes in byte order. `observations` lists every artifact/run pair
linked to a version.
