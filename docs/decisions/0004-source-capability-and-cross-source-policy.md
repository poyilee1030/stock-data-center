# ADR-0004: Source Capability and Cross-Source Policy

- Status: Accepted
- Date: 2026-09-11

## Context

Different sources for one dataset may have different publication guarantees and
revision histories. Dataset-wide capability flags or implicit reconciliation
would allow verified semantics from one source to authorize another.

## Decision

PIT capability is configured for the exact `(dataset_code, source)` pair.
`dataset_catalog` owns dataset/domain metadata. `dataset_sources` owns at least:

- `supports_market_pit`;
- `supports_system_pit`;
- publication-time quality and accepted evidence types;
- evidence verification status; and
- `is_canonical`.

A resolver checks the selected pair before reading versions. Unsupported PIT
mode fails explicitly. Capability is never inherited from another source or
inferred from the presence of similarly shaped rows.

Source histories remain independent. Version 1 does not average, merge,
overwrite, or select the latest-ingested value across sources. Resolution uses:

1. the caller's explicit source; otherwise
2. the sole configured canonical source for the dataset; otherwise
3. an explicit ambiguity error (or a response whose results remain separated
   by source when that endpoint contract specifically defines it).

Configuration with more than one canonical source for a dataset is invalid.
Adding a reconciliation policy requires an ADR and tests before implementation.

## Consequences

- A verified MOPS policy, for example, cannot make another provider market-PIT
  capable.
- Source is a mandatory component of resolver and cache identity even when it
  was filled from canonical-source configuration.
- Provenance always identifies the resolved source.
