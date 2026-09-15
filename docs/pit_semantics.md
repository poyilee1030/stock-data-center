# Point-in-Time Semantics

## Status and terminology

This document is normative for Phase 0 and later implementation.

All cutoffs are timezone-aware instants. API timestamps without an explicit
UTC offset are invalid. PostgreSQL stores instants as `TIMESTAMPTZ`; application
code normalizes them to UTC. Comparisons at a cutoff are inclusive (`<=`).
Source-local dates and times are interpreted in `Asia/Taipei` before conversion
to an instant.

The three PIT parameters are deliberately not aliases:

| Parameter | Meaning |
| --- | --- |
| `information_as_of` | Latest market-publication instant allowed in a market-PIT answer. |
| `knowledge_as_of` | Latest instant at which publication evidence may have been recorded by the Data Center. |
| `system_as_of` | Latest trusted ingestion/seal instant allowed in a system-PIT answer. |

`date`, `as_of`, `latest`, and `now` must not enter domain resolution as
ambiguous substitutes. An API that accepts a convenience alias must resolve it
to an explicit PIT mode and concrete instant before resolution or cache-key
construction.

## Market PIT

Market PIT answers:

> What was publicly knowable by `information_as_of`, using publication evidence
> the Data Center had recorded by `knowledge_as_of`?

A request supplies both clocks and a source. A business version is eligible
only when the authoritative evidence resolved under the knowledge cutoff:

1. has `recorded_at <= knowledge_as_of`;
2. has an `evidence_type` accepted by the exact `(dataset_code, source)` policy;
3. is an affirmative, non-retracted publication assertion;
4. has non-null `published_at`; and
5. has `published_at <= information_as_of`.

Neither a raw fetch time nor `ingested_at` substitutes for `published_at`.
Evidence with `published_at = NULL` is market-invisible. Current wall-clock time
must never be invented as a historical publication time.

## Evidence types and their ranking

`evidence_types` registers every type ADR-0020 fixed, with the rank that
expresses its precedence. Precedence therefore needs no special case in the
resolver: ADR-0002 already ranks heads by `quality_rank`, descending.

| Type | Rank | Meaning |
| --- | ---: | --- |
| `official` | 90 | a per-row release instant published by the source; no v1 source provides one |
| `capture_bound` | 80 | our own first successful fetch of an artifact containing the version — a proven upper bound |
| `legacy_capture_bound` | 70 | the legacy scraper's recorded first-seen date, as the end of the run that first held the row |
| `press_report_bound` | 60 | a publication date reconstructed from a dated secondary record, at end of that day, Asia/Taipei |
| `release_rule` | 40 | a versioned no-later-than instant derived from a published schedule or statute |

For the four types ADR-0020 introduces, **the rank is a storage invariant**: a
row whose rank disagrees with the registry is rejected, and a non-affirmative
row of such a type must carry rank 0 so an `unknown` head can never outrank a
real assertion. Ranking is not left to caller discipline.

`official` is registered but **not pinned**, and its registered 90 is nominal:
it predates ADR-0020 and stored rows carry other values — the current adapters
write rank 0 with `unknown` kind, and the domain fixtures write 100 for
assertions. Pinning it would rewrite history to no purpose, but it does mean an
`official` assertion at 100 can still outrank a pinned `capture_bound` at 80.
That is tolerable only because no v1 source emits `official` affirmatively
(audit §7); Step 15-b must not assume 90 describes any stored row.

Registration is about ranking, never permission: which types a source accepts
remains `dataset_sources.accepted_evidence_types` (ADR-0010).

## Why a fetch happened

Every ingest run records a `purpose` — `first_capture`, `gap_fill`,
`correction_check`, or `unspecified` — declared when the fetch is requested and
never inferred afterwards. A row fetched years later because a query noticed it
was missing is a `gap_fill`, and must not be able to claim a capture bound at
that later instant. `unspecified` covers runs that predate the policy.

Each fetch observation also records its `artifact_origin`, `official_fetch` or
`legacy_archive` (ROADMAP §14), which until now existed only in prose.

## Release rules

`release_rules` registers each versioned rule with the schedule or statute it
derives from. A rule with no cited authority would be an invented instant, which
ROADMAP §2.4 forbids, so `authority` cannot be empty. Rules are never edited in
place: correcting one means publishing a new version, and the table rejects
`UPDATE` and `DELETE`.

Two shapes, and the difference is the part that is easy to get wrong:

| Kind | Resolves at | Moves off a closure? |
| --- | --- | --- |
| statutory deadline (`monthly_revenue_statutory`, `financial_statements_general`) | end of its day | **yes**, to the next trading day |
| scheduled instant (`exchange_daily_settled`, `tdcc_weekly`) | its stated time | **no** |

A filing due on a closed day is filed on the next open one, so 2021Q2 resolves
to 2021-08-16 rather than 08-15, and 2026M04 revenue to 2026-05-11 rather than
05-10. A scheduled instant is different: the exchange file exists at 03:00
whether or not that day is a trading day, and the TDCC rule already names a
Sunday, which is never a business day — shifting it would push the rule a whole
week.

The shift consults the Step 16 calendar, which refuses outside its imported
coverage rather than guessing. A rule that guessed its own deadline would be the
invented instant the policy exists to prevent.

## What an import may claim

`evidence_plan` turns the declared purpose into the evidence a run is entitled
to write:

| Purpose | May claim a capture bound? |
| --- | --- |
| `first_capture` | yes |
| `correction_check` | only for a revision it newly found |
| `gap_fill` | no — it noticed the row was missing long after publication |
| `unspecified` | no |

The write-side rule from ADR-0020 §2: if a first sighting happened *after* the
rule instant, that row is a late filer, the rule is falsified for it, and no
rule evidence is written at all. A backfill capture cannot falsify anything,
because it is not a first sighting.

When nothing is provable, nothing is claimed: the plan falls back to `unknown`
evidence with `published_at = NULL`, exactly as before ADR-0020.

Step 15-c applies this to the adapters and opts each source into the new types;
until it lands they still write `unknown` evidence.

Two supported reconstructions are:

```text
historically reproducible:
  information_as_of = historical market cutoff
  knowledge_as_of   = historical Data Center knowledge cutoff

current-best historical:
  information_as_of = historical market cutoff
  knowledge_as_of   = an explicit current cutoff
```

The second form may change when newly recorded, reliable evidence proves an
older publication time. The first cannot use evidence recorded after its
knowledge cutoff.

After evidence eligibility is established, the resolver selects the applicable
business revision for the source and logical key using the revision's resolved
publication instant and a stable storage-generated revision identifier as the
final tie-breaker. Exact dataset logical keys are defined with that dataset;
handlers may not improvise the ordering.

For an immutable aggregate, market eligibility additionally requires:

```text
seal.ingested_at <= knowledge_as_of
```

This does not redefine market publication time. It proves that the Data Center
had completed the aggregate by the historical knowledge cutoff and prevents a
later seal from making an earlier committed draft retroactively visible.

## System PIT

System PIT answers:

> What complete business data had this Data Center actually ingested by
> `system_as_of`?

Publication time and publication-evidence quality do not control system PIT.
For an immutable single-row version, eligibility is:

```text
version.ingested_at <= system_as_of
```

For a parent-and-children aggregate, eligibility is:

```text
seal.ingested_at <= system_as_of
```

An unsealed aggregate is invisible even if the parent and all expected children
are committed. The trusted seal timestamp is the aggregate's system-visible
time.

## Trusted times and backfill

Normal callers cannot provide or backdate `recorded_at`, version `ingested_at`,
or seal `ingested_at`. These are generated by trusted storage/database logic.

A historical migration may preserve an old timestamp only through a dedicated,
restricted path with provenance, tests, and audit documentation. A migration
that changes historical temporal meaning must also document whether cached
answers require a namespace bump or targeted purge.

For source backfill:

```text
published_at = proven historical publication instant, or NULL if unproved
recorded_at  = actual trusted time the evidence is recorded
ingested_at  = actual trusted insertion/seal time
```

## Result requirements

Resolved responses include enough provenance to identify, as applicable:

- dataset and source
- business version and business hash
- authoritative publication evidence and its published/recorded times
- ingest run and raw artifact
- aggregate seal
- PIT mode and all effective cutoffs

An unsupported source/PIT combination or invalid mixture of market and system
parameters fails explicitly; it never falls back to current state.

The Phase 2 implementation contract and deterministic ordering are documented
in [Core PIT Resolver](pit_resolver.md).

## Derived PIT inheritance

Canonical derived results use inputs resolved under the same explicit PIT
context. Market-derived results inherit `information_as_of` and
`knowledge_as_of`; system-derived results inherit `system_as_of`. A materialized
result's `computed_at` is calculation provenance only and is never substituted
for input publication or ingestion visibility. See
[Canonical Derived Data](derived_data.md).
