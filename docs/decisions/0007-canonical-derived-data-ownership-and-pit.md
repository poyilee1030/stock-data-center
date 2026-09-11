# ADR-0007: Canonical Derived Data Ownership and PIT

- Status: Accepted
- Date: 2026-09-11

## Context

The earlier boundary placed all derived values downstream, which would make
multiple models independently implement financially standard calculations and
drift in formula and PIT behavior. Conversely, storing model-specific signals
in the Data Center would couple its contract to experiments.

## Decision

The Data Center owns deterministic, financially well-defined, cross-repository
canonical derived datasets. Model-specific features, labels, scores, feature
combinations, training, and ranking remain downstream.

Every canonical definition has an explicit `derivation_version`; semantic
changes create a new version. Materialized results retain input identity and
fingerprint, the complete PIT context, computation provenance, and business
hash.

Derived visibility inherits from PIT-safe inputs. `computed_at` means only when
the calculation ran and is never market publication evidence. Materialized and
virtual execution must remain semantically equivalent.

The normative contract is [Canonical Derived Data](../derived_data.md).

## Consequences

- Shared standard metrics have one definition and one PIT policy.
- A later calculation cannot make future input visible in a historical result.
- Cache identities for derived requests include `derivation_version`.
- Phase 1 must define storage/materialization strategy; calculators remain a
  later phase.
