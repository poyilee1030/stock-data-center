# ADR-0001: Temporal and PIT Model

- Status: Accepted
- Date: 2026-09-11

## Context

Historical market knowledge and actual Data Center ingestion answer different
questions. A single `as_of` value cannot represent both without permitting
look-ahead or rewriting ingestion history.

## Decision

The system exposes two explicit PIT modes:

- Market PIT requires both `information_as_of` and `knowledge_as_of`.
- System PIT requires `system_as_of`.

Market PIT uses authoritative publication evidence satisfying both
`published_at <= information_as_of` and
`recorded_at <= knowledge_as_of`. Unknown publication time is not market
visible. System PIT uses trusted version `ingested_at`, or trusted seal
`ingested_at` for an aggregate.

All cutoffs are aware instants, stored with PostgreSQL `TIMESTAMPTZ`, compared
inclusively, and normalized to UTC. `Asia/Taipei` is used to interpret source
market times. Normal callers cannot set authoritative ingestion or evidence
recording times.

The complete normative rules are in [Point-in-Time Semantics](../pit_semantics.md).

## Consequences

- Requests cannot mix market and system PIT parameters.
- Market reconstruction can distinguish information availability from when
  the Data Center learned the supporting evidence.
- Backfilled data cannot appear in an earlier system-PIT result.
- Query APIs and cache identities must retain the selected PIT mode and every
  cutoff it requires.
