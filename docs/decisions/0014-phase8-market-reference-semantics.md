# ADR-0014: Phase 8 Market Reference Semantics

## Status

Accepted for Phase 8 on 2026-09-12.

## Context

Index quotes, corporate actions, and official valuation are reusable observed
data, but their time and unit meanings differ. A future corporate action can be
public before its effective date, while an official PE must not be confused
with a Data Center-computed PE.

## Decision

- Keep independent immutable revisions per dataset, source, and logical key.
- Treat index/valuation `trade_date` as effective observation date.
- Treat corporate `announcement_date` as source announcement date and
  `ex_date` as event effective date; market visibility follows publication
  evidence, not the ex-date.
- Store monetary values in canonical TWD major units after explicit adapter
  scale normalization.
- Define stock/rights ratios as shares per share and published yields as
  percentage points.
- Keep `official_valuation_versions` strictly source-published. Computed
  valuation uses a separately identified derivation version.
- Preserve every repeat-fetch raw/run lineage through append-only,
  dataset-specific association tables.
- Use the shared two-clock Market PIT and trusted-ingestion System PIT rules.

## Consequences

Backfills remain historically honest, source histories cannot overwrite each
other, and downstream consumers can tell observed official values from derived
ones. Future source adapters must supply explicit native monetary scale. New
calculation formulas require new derivation versions rather than mutation of
the observed contract.
