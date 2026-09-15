# Step 15-c Acceptance Report

Status: IN REVIEW

Scope: Applying the availability-time evidence policy to the adapters

Schema impact: `dataset_release_rules`, plus the `daily_price` catalog, source,
and rule-mapping seeds. Migration: `4d9f2a6c8b17`.
**PIT impact: this is the step where imported history becomes Market-PIT
visible.** `src/` changed by +269/−41 lines, inside the review threshold.

## The behaviour change, stated plainly

Before this step, a `daily_price` row imported from the official endpoint was
Market-PIT **invisible**: the endpoint publishes no release instant, so the
import recorded `unknown` with `published_at = NULL`. The existing Step 9
regression asserted exactly that — `assert market is None`.

It now resolves by `exchange_daily_settled@1`: trade date 2025-09-01 becomes
visible at 03:00 on 09-02, Asia/Taipei. That assertion is now the opposite, and
pins the rule, the attribution, and the instant:

```python
assert market is not None
assert market.authoritative_evidence.evidence_type == "release_rule"
assert market.authoritative_evidence.evidence_source == "exchange_daily_settled@1"
assert market.authoritative_evidence.published_at == datetime(2025, 9, 1, 19, 0, tzinfo=UTC)
```

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| The policy is applied through the ingest lifecycle | PASS | `_write_business` receives an `EvidenceContext` carrying the declared purpose and the *recorded* fetch instant, read back from the observation so a resumed import keeps the original instant rather than the moment it resumed. |
| Opting in is configuration, not code | PASS | `dataset_release_rules` maps `(dataset_code, source)` to a versioned rule, with foreign keys to both `dataset_sources` and `release_rules`. Queryable on its own through `rule_for`. |
| A half-configured source fails loudly | PASS | Planning a type absent from `accepted_evidence_types` raises `UnacceptedEvidenceTypeError` rather than writing evidence the resolver would filter out. |
| History nobody captured resolves by its rule | PASS | A `gap_fill` import plans `release_rule` only, at 03:00 the next day. |
| A forward capture supersedes the rule it beats | PASS | A capture after 03:00 falsifies the rule and records the capture alone. |
| Nothing is enabled by default | PASS | A dataset with no mapping gets no rule; with neither rule nor capture it still records `unknown`, exactly as before. |
| CLAUDE.md §31–32 match the behaviour | PASS | Rewritten to the implemented policy, including the purpose table and the falsification rule. |

## What this cost in existing tests

Seeding `daily_price` into `dataset_sources` collided with fixtures that insert
the same row, so six test files now use `ON CONFLICT ... DO UPDATE`. Two
assertions changed because the behaviour changed: the Step 9 Market-PIT
assertion above, and its `unknown_publication_observations` count, which is now
zero.

A first attempt at the fixture change was made with a loose scripted rewrite and
broke 65 tests. It was reverted rather than patched over, and redone against
exact patterns.

## A circular import the wiring exposed

`evidence` imported `IngestPurpose` from `ingestion.models`, while
`ingestion.daily_market` imports the evidence policy — so importing either
package first could leave the other half-initialised. `IngestPurpose` and
`ArtifactOrigin` now live in `stock_data_center/provenance.py`, which neither
package owns: they are the vocabulary the lifecycle *declares* and the policy
*reads*, so belonging to either was the mistake.

## Verification

Database migrated from zero to `4d9f2a6c8b17`:

```text
373 passed, 3 skipped, 1 warning
```

Baseline before this step: 364.

## Scope exclusions confirmed

- Only `daily_price` is opted in, because it is the only dataset with both an
  adapter and a rule today. Steps 17–24 opt their own sources in.
- No `legacy_capture_bound` or `press_report_bound` evidence is written; those
  need the legacy-archive importers of Steps 22 and 23.
- The financial-industry statement rule is still undefined, by decision.
