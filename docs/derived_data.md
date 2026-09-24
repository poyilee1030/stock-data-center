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

Every canonical derived dataset has an immutable definition, a code constant
(`stock_data_center.v2.derived`, ADR-0027) identified by:

- dataset code and explicit `derivation_version`;
- formula/specification;
- implementation version: the git commit the result was computed with, returned
  with every result;
- required input datasets;
- timezone and calendar convention where relevant; and
- price-adjustment convention where relevant.

A formula or convention change creates a new derivation version; an existing
definition is never edited. When a definition was introduced is its git
history.

## PIT inheritance

Derived market visibility is inherited from the inputs selected under the
request's `information_as_of` and `knowledge_as_of`; derived system visibility
from inputs selected under `system_as_of`. Calculation time never widens input
visibility: it is provenance, never a publication time or an eligibility
cutoff.

The rolling as-of series computes each observation date at the instant its own
inputs became public, so no value in it could see a later price. A correction to
an earlier input that became available later changes every value after that
instant and nothing before it (`docs/pit_semantics.md`).

## Computed on demand

Derived data has no tables: a result is a deterministic function of stored
inputs, computed when asked for (ROADMAP §17). A metric is materialized only by
its own step, after measurement shows on-demand computation too slow, and the
materialized result must equal the on-demand one for the same PIT context,
definition and inputs. `technical_indicators:v1` (Step 35-c-4) is the first; it
computes one stock's full series in about 0.11 s.
