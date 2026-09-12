# ADR-0013: Phase 7 Observed Source Semantics

Status: Accepted

Date: 2026-09-12

## Context

Institutional flow, foreign holding, margin/short, and SBL records share a daily
PIT shape but have different source fields. Their Phase 1 tables existed without
normalized writer/query contracts or repeat-fetch association lineage.

## Decision

Keep the five source datasets independent and expose them through one bounded
Phase 7 writer/service package. Each table retains its explicit dataset-specific
foreign keys and PIT resolver contract.

The logical revision keys are documented in the Phase 7 contract. PostgreSQL
generates ingestion time and a canonical business hash that excludes logical
keys and provenance. Identical refetches reuse a business revision while
append-only dataset-specific observation tables preserve every raw/run lineage.

All per-security stock quantities use canonical shares. Source adapters must
declare `SHARE` or `LOT`; one lot normalizes to 1,000 shares before observation,
storage, and hashing. Canonical observation fields reject untyped numeric
quantities. Gross quantities, balances, holdings, and limits are non-negative
integers. Published net-flow fields and SBL adjustment remain signed source
facts after normalization and are not recomputed. Published ratios are
percentages. Publication evidence cannot predate the local observation date and
remains separate from business history.

No reliable v1 absolute holding baseline exists for trust/dealer holdings.
Daily flows can reproduce only a zero-origin cumulative-net-flow proxy, not an
absolute holding. The Phase 9 definition is therefore frozen as
`institutional_cumulative_flow:v1` with explicit
`*_cumulative_net_shares`/`*_cumulative_net_ratio` metric names. Adding a real
baseline later requires a new observed contract and derivation version.

Derived chip-flow, margin pressure, short-interest, and holding metrics are not
source facts. They remain versioned PIT-safe canonical derivations for Phase 9
or model-specific downstream features according to the inventory.

## Consequences

- Source revisions and source capabilities remain isolated.
- Historical backfills cannot falsify System PIT.
- Late evidence cannot enter an earlier knowledge cutoff.
- Repeat fetch provenance is complete without fake business revisions.
- Equivalent lot/share representations have one canonical business identity.
- The Data Center does not mislabel cumulative flow as absolute ownership.
- Redis/cache impact is none because Phase 7 has no cache implementation.
