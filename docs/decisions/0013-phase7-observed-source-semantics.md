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

Gross quantities, balances, holdings, and limits are non-negative integers.
Published net-flow fields and SBL adjustment are signed source facts and are not
recomputed. Published ratios are percentages. Publication evidence cannot
predate the local observation date and remains separate from business history.

Derived chip-flow, margin pressure, short-interest, and holding metrics are not
source facts. They remain versioned PIT-safe canonical derivations for Phase 9
or model-specific downstream features according to the inventory.

## Consequences

- Source revisions and source capabilities remain isolated.
- Historical backfills cannot falsify System PIT.
- Late evidence cannot enter an earlier knowledge cutoff.
- Repeat fetch provenance is complete without fake business revisions.
- Redis/cache impact is none because Phase 7 has no cache implementation.
