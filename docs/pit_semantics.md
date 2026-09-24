# Point-in-Time Semantics

## Status and terminology

This document is normative. It states the rules schema v2 (ADR-0027) applies;
the evidence model that preceded it (ADR-0002, ADR-0010, ADR-0020) is history,
and what it proved survives as stored `published_at` values.

All cutoffs are timezone-aware instants. API timestamps without an explicit
UTC offset are invalid. PostgreSQL stores instants as `TIMESTAMPTZ`; application
code normalizes them to UTC. Comparisons at a cutoff are inclusive (`<=`).
Source-local dates and times are interpreted in `Asia/Taipei` before conversion
to an instant.

The three PIT parameters are deliberately not aliases:

| Parameter | Meaning |
| --- | --- |
| `information_as_of` | Latest instant at which a value may have become public for a market-PIT answer. |
| `knowledge_as_of` | Latest instant at which the Data Center may have recorded a row used in a market-PIT answer. |
| `system_as_of` | Latest instant at which the Data Center may have recorded a row used in a system-PIT answer. |

`date`, `as_of`, `latest`, and `now` must not enter resolution as ambiguous
substitutes. An API that accepts a convenience alias must resolve it to an
explicit PIT mode and concrete instant first.

## What a row is

Every observed table holds one append-only row per key and published value: a
row is added only when a value differs from the key's latest row, and nothing is
updated or deleted (triggers refuse it). Each row carries `recorded_at`, the
database's own statement time when it was written — never a caller's value —
and `fetch_id`, the fetch whose raw file it came from.

A key's rows are its history: the first is what the Data Center first stored,
each later one a change the source published.

## Market PIT

Market PIT answers:

> What was publicly knowable by `information_as_of`, using what the Data Center
> had recorded by `knowledge_as_of`?

A row is eligible when

```text
available_at <= information_as_of
AND
recorded_at  <= knowledge_as_of
```

and the answer for a key is its latest eligible row. `available_at` is when the
row's value became public, and it is computed, not supplied:

| Dataset | A key's first row | A later row |
| --- | --- | --- |
| exchange daily data, indices, valuation, institutional, foreign holding, margin, SBL | release rule `exchange_daily_settled@1` | its own `recorded_at` |
| TDCC distributions | release rule `tdcc_weekly@1` | its own `recorded_at` |
| corporate actions | release rule `corporate_action_ex_date@1` | its own `recorded_at` |
| monthly revenue, financial reports | the stored `published_at`; NULL is never available | its own `recorded_at` |

A later row is a correction the Data Center saw when it recorded it, so it is
never available earlier. A NULL `published_at` means nothing proves when the
value became public, and the row is market-invisible; the Data Center never
substitutes a fetch time, a file date or the period's own date.

**Values recorded before they settled.** The exchange serves a trade date's
rows before they are final (audit §7), so for the rule-based datasets a row
recorded before its rule instant is provisional. It is available from the rule
instant, and the first row recorded at or after the instant — the settled
value — supersedes it there. The settled row is available from the rule instant
however late it was recorded; only rows after it are corrections
(`stock_data_center.v2.exchange_daily.visible`).

Two supported reconstructions are:

```text
historically reproducible:
  information_as_of = historical market cutoff
  knowledge_as_of   = historical Data Center knowledge cutoff

current-best historical:
  information_as_of = historical market cutoff
  knowledge_as_of   = an explicit current cutoff
```

The second may change as later rows are recorded. The first cannot use a row
recorded after its knowledge cutoff.

## Release rules

A release rule is a versioned code constant that cites the schedule, statute or
owner decision it derives from (`stock_data_center.v2.exchange_daily`,
`stock_data_center.v2.release_rules`). A rule with no authority would be an
invented instant. A rule is never edited: correcting one means adding a new
version. Each has a Python form for one date and an SQL form for stored rows,
and the two must agree.

| Rule | Instant | Authority |
| --- | --- | --- |
| `exchange_daily_settled@1` | 03:00 Asia/Taipei on the day after the trade date | ADR-0020 §3: the legacy 23:30 run was incomplete on 5 of 27 observed dates, the 03:00 retry on 1 |
| `tdcc_weekly@1` | 12:00 Asia/Taipei on the first Sunday after the data date | ADR-0020 §3: the legacy Sunday 10:20 job plus margin |
| `corporate_action_ex_date@1` | 00:00 Asia/Taipei on the ex-date | ADR-0027: the current-year result files already list coming ex-dates with their reference prices |

A scheduled instant is not moved off a closure: the exchange file exists at
03:00 whether or not that day is a trading day, and the TDCC rule already names
a Sunday. The statutory deadlines `monthly_revenue_statutory@1` and
`financial_statements_general@1`, which are moved to the next trading day, gave
some historical rows their `published_at` (audit §7.5, §7.6); they are not
applied to anything fetched now, because publication in those two datasets
differs by issuer.

## What a fetch may claim

Every fetch records a `purpose` in `fetches` — `first_capture`, `gap_fill`,
`correction_check`, or `unspecified` — declared when the fetch is requested and
never inferred afterwards. Only a first capture proves anything about
publication: seeing a value first means the Data Center recorded it at that
instant, so it was public by then.

| Purpose | Stores `published_at` on a new key's first row? |
| --- | --- |
| `first_capture` | yes, its own fetch instant |
| `correction_check` | no |
| `gap_fill` | no — it noticed the row was missing long after publication |
| `unspecified` | no |

This matters only where `published_at` is stored. For the rule-based datasets
the purpose changes nothing about visibility: a backfilled row is available at
its rule instant, and a correction at its `recorded_at`.

## System PIT

System PIT answers:

> What had this Data Center actually recorded by `system_as_of`?

```text
recorded_at <= system_as_of
```

Publication does not enter it. A financial report and its facts are written in
one transaction, so a report is never visible without all its facts.

## Trusted times and backfill

`recorded_at` is generated by PostgreSQL. Normal callers cannot provide or
backdate it. For a backfill:

```text
published_at = the instant a first capture or a legacy record proves, else NULL
recorded_at  = the actual time the row is written
```

A historical migration may preserve an old timestamp only through a dedicated,
restricted path with provenance, tests and audit documentation. Steps 35-a and
35-c-1 were that path: they copied v1's `ingested_at` into `recorded_at` and
v1's proven publication evidence into `published_at`.

## Result requirements

A resolved answer identifies, as applicable: the dataset and source, the row's
key and `recorded_at`, its `available_at` and why (the rule, or the stored
`published_at`), the fetch and its raw file, and the PIT mode with every
effective cutoff. An unsupported source or PIT combination fails explicitly; it
never falls back to current state.

## Derived data

Stored derived datasets (Step 26) are computed from the latest inputs and have
no knowledge-time axis: a corrected input recomputes and overwrites the dates it
reaches. The value for date D still uses only inputs dated on or before D, and
an input published late is aligned to its publication. `computed_at` is
provenance only, never a publication or visibility time.

`technical_indicators_pit:v1` is computed on demand under the full PIT context.
`knowledge_as_of` keeps only the input rows recorded by then; `information_as_of`
decides which of them were public. In a rolling as-of series each observation
date is computed at the instant its own inputs became public, so no value can
see a later price. See [Canonical Derived Data](derived_data.md).
