# Canonical Derived Data Contract

## Ownership boundary

`stock-data-center` owns observed source facts and deterministic canonical
derived datasets that retain a stable financial meaning independent of any
particular model. Downstream ML repositories own experiment-specific features,
interactions, labels, scores, training, and ranking.

A derived metric belongs here only when it is deterministic, reusable across
repositories, financially well-defined, and reconstructible from PIT-safe Data
Center inputs. Examples include standard technical indicators, TTM EPS,
shareholding concentration, and margin/SBL ratios. A weighted model signal does
not qualify.

## Definition identity

Every canonical derived dataset has an immutable definition identified by:

- dataset code and explicit `derivation_version`;
- formula/specification;
- implementation version or Git commit;
- required input datasets;
- timezone and calendar convention where relevant;
- price-adjustment convention where relevant; and
- trusted registration timestamp.

A formula or convention change creates a new derivation version. Existing
definitions and results are never silently overwritten.

## PIT inheritance

Derived market visibility is inherited from the PIT-safe inputs selected under
the request's `information_as_of` and `knowledge_as_of`. Derived system
visibility is inherited from inputs selected under `system_as_of`. Calculation
time never widens input visibility.

`computed_at` records when the Data Center performed or materialized a
calculation. It is operational provenance, not a market publication time, and
must never be used as `published_at` or as the market-PIT eligibility cutoff.

## Materialized result lineage

A materialized result preserves its derivation definition/version, input
dataset identities, deterministic input fingerprint, full PIT context,
computation run, `computed_at`, and storage-generated business hash. Repeating
the same definition with the same PIT-safe inputs must have the same semantic
identity.

## Materialized and virtual equivalence

A canonical metric may be materialized in PostgreSQL or computed on demand.
This is a performance choice. Both strategies use the same definition and PIT
rules and return the same business value, derivation identity, and input
provenance. Optional caching occurs above the derivation service and does not
change formulas or visibility.
