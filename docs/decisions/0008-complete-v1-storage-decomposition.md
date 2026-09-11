# ADR-0008: Complete v1 storage decomposition

- Status: Accepted
- Date: 2026-09-11

## Context

The original Phase 1 schema covered core price, revenue, financial, and TDCC
data but did not freeze the storage/ownership decisions for every known legacy
domain. Deferring those choices would allow later ingestion work to redefine
PIT, provenance, or observed-versus-derived boundaries piecemeal.

## Decision

Phase 1 defines explicit observed version tables for institutional flows,
foreign holdings, institutional market summaries, margin/short data, SBL,
market indices, corporate actions, official valuations, security tags, and the
XBRL concept catalog. Daily prices retain all known observable quote fields.

Canonical derived data uses an immutable definition registry, computation-run
provenance, and a generic typed metric version table. The definition contains
an explicit derivation version and semantic conventions; each materialized
result contains its PIT context and deterministic input fingerprint. A generic
metric table is chosen because these domains share temporal, lineage, and
version semantics, while metric-specific wide tables would duplicate them and
force schema changes for each stable metric. Dataset-specific API contracts may
still expose strongly typed responses later.

Publication evidence retains real foreign keys to each observed version type.
No loose polymorphic evidence target is introduced. The complete legacy field
disposition is normative in `docs/data_domain_inventory.md`.

## Consequences

- Later phases can populate empty contracts without discovering a new major v1
  storage domain.
- Observed official valuation remains distinct from computed valuation.
- Legacy trust/dealer holdings and analytics are not mislabeled as source facts.
- Formula changes coexist by derivation version; `computed_at` is never a
  publication time.
- Adding a new observed version domain requires a real evidence foreign key and
  corresponding DB validation.
