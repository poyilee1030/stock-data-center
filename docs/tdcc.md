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
identified by the storage-generated aggregate hash of the snapshot date, the
declared distribution profile, and the complete ordered distribution. A
changed distribution for the same key is a new, independent sealed version; the
older version remains queryable under earlier cutoffs. Publication evidence is appended separately, so learning,
correcting, or retracting a publication time never mutates the snapshot or
creates a false business revision.

Each distribution row retains the source-native `bucket_code` verbatim together
with `holder_count`, `shares`, and `ownership_percent`. The writer rejects
values that storage would otherwise silently round: `shares` must be a whole
number and `ownership_percent` must have at most 8 decimal places and lie
between -100 and 100. Bucket codes must be unique.

## Distribution profiles and completeness

Every snapshot declares a `distribution_schema` profile registered in
`tdcc_distribution_schemas` / `tdcc_distribution_schema_buckets`
([ADR-0012](decisions/0012-tdcc-distribution-profiles-and-seal-completeness.md)).
Each profile bucket has one role. The official profile is `tdcc-opendata-v1`:

| Bucket codes | Role | Contract |
| --- | --- | --- |
| `1`-`15` | `holding` | holding range; holder count required; shares and percent non-negative |
| `16` | `adjustment` | difference adjustment; shares and percent may be signed; holder count is `NULL` |
| `17` | `total` | source-published total; holder count required; shares and percent non-negative |

In this profile, level 16 is a signed adjustment. A value such as
`shares = -2000` is stored and hashed exactly as received. It is never clamped
to 0 or dropped.

For an adjustment bucket, holder_count is NULL. Historical files leave the
field blank and open-data forms may write 0. PostgreSQL canonicalizes both to
`NULL`, so the two forms of the same snapshot have the same business content,
and it rejects any other adjustment holder count. Adapters do not decide this.

An incomplete snapshot cannot be sealed. A distribution must contain every
bucket of its profile and no bucket outside it. The canonical writer checks
this before creating a draft, and PostgreSQL enforces the same rules for every
write path: per row on insert/update and for completeness at seal. Sealed
therefore means complete for the declared profile. Arithmetic identities
between holding, adjustment, and total rows are not enforced; the total is
retained as the source-published value.

A profile is frozen once used. Bucket definitions may be added only while no
snapshot references the profile. After the first snapshot (draft or sealed)
uses it, PostgreSQL rejects any new bucket for that profile (`55000`), and
existing definitions can never be updated or deleted. The bucket insert locks
the profile row, which conflicts with the foreign-key lock taken by a snapshot
insert, so a definition change and a first use serialize and whichever
commits first wins. The profile code in the business hash therefore always
names one fixed contract. Changed semantics or a different source layout need a
new profile code, for example `tdcc-opendata-v2`, and never a mutation of an
existing one.

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
payload is the snapshot date, the profile code, and every distribution row
ordered by `bucket_code COLLATE "C"`, so the hash is byte-order deterministic
and does not depend on the database default collation. The Phase
6 migration assigns existing snapshots `tdcc-opendata-v1`, canonicalizes
existing adjustment holder counts of 0 to `NULL`, and rehashes sealed snapshots
under this payload. It aborts instead of guessing if an existing row does not
fit the profile or a sealed snapshot is incomplete.

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
Each returned bucket carries its profile role. Distributions are returned with
numeric bucket codes in numeric order followed by other codes in byte order.
`observations` lists every artifact/run pair linked to a version.
