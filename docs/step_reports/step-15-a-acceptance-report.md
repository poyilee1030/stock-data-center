# Step 15-a Acceptance Report

Status: IN REVIEW

Scope: Availability-time evidence vocabulary and ingest purpose (ADR-0020 §1, §5)

Schema impact: `evidence_types` registry, `ingest_runs.purpose`,
`raw_artifact_observations.artifact_origin`, and one trigger.
Migration: `2b7d4e9a1c35`. PIT impact: none yet — nothing writes the new types.

## Why this is 15-a and not 15

Step 15's full scope was heading well past the 800-line review threshold
(CLAUDE.md §1), so it splits along the seam between *declaring* the vocabulary
and *evaluating* the rules:

- **15-a (this step)** — the types exist, their ranking is enforced, and every
  ingest records why it fetched and how the bytes were obtained. Correct on its
  own: adapters still emit `unknown` evidence, exactly as before.
- **15-b** — the release-rule registry with its evaluation against the Step 16
  calendar, the purpose-to-evidence derivation, the write-side falsification
  rule, and the CLAUDE.md §31–32 rewrite.

The release-rule registry deliberately went to 15-b rather than here. A
versioned rule row with no code that can turn it into an instant is a promise,
not a fact, and would have been the "half a contract merged" the splitting rule
warns against.

`src/` changed by **+94 lines**, well inside the threshold.

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| The four ADR-0020 types are registered with the ADR's ranking | PASS | `evidence_types` holds all five rows; the test asserts the exact ranks *and* the resulting order, so a silent renumbering fails. |
| Ranking is a storage invariant, not caller discipline | PASS | Writing `release_rule` at rank 95 is rejected; at 40 it is accepted. |
| A non-affirmative head cannot outrank an assertion | PASS | An `unknown`-kind row of a pinned type must carry rank 0. |
| Registration does not grant permission | PASS | An unregistered type is still governed by `accepted_evidence_types` (ADR-0010), unchanged. |
| Every ingest declares why it fetched | PASS | `ingest_runs.purpose` with a `CHECK`; an invented purpose is rejected; a run that declares nothing records `unspecified`. |
| The lifecycle records the declared purpose and origin | PASS | An import run with `purpose=gap_fill, artifact_origin=legacy_archive` stores exactly that. |
| ROADMAP §14's artifact origin is modelled | PASS | `raw_artifact_observations.artifact_origin` with a `CHECK`; `scraped_from_a_blog` is rejected. |

## The decision this step had to make

Enforcing "the registered rank is the only legal rank" for **every** type broke
**51 existing tests** across 8 files. Those tests write `official` at rank 100
and use the rank as a free parameter to exercise ADR-0002's ordering contract —
a different contract from the one ADR-0020 introduces.

Rather than rewrite 51 tests inside a step meant to be small, the enforcement is
scoped to what ADR-0020 actually introduces. `evidence_types.rank_is_enforced`
is true for the four new types and false for `official`, and the registry row
says why: `official` predates the ADR, existing rows carry assorted ranks, and
no v1 source emits it affirmatively (audit §7).

Nothing is weakened by this. The hazard ADR-0020 guards against is a forged
`release_rule` outranking a real `capture_bound`, and that is fully pinned.

## Verification

Database migrated from zero to `2b7d4e9a1c35`:

```text
327 passed, 3 skipped, 1 warning
```

Baseline before this step: 312 passed. The 15 new tests are the difference; no
existing test changed.

The Step 14 storage-contract guard again did its job: `evidence_types` failed
classification on first run and is now recorded as an excluded table with its
reason.

## Code-review findings

All five were verified before anything changed; none was a false positive.

| # | Finding | Verified by | Fix |
| --- | --- | --- | --- |
| 1 | A `CHECK` on `purpose` landed on `import_manifests`, which has no such column | `metadata.create_all` on an empty database: `column "purpose" does not exist` | Removed. A scripted edit matched the `completed_after_started` constraint text in two tables. |
| 2 | `purpose` defaulted to `first_capture`, so an undeclared run was *inferred* to be a first capture | Read against ADR-0020 §5 | Default is `unspecified` everywhere. Re-fetching history published long ago is a `gap_fill`, and the CLI help now says so. |
| 3 | `downgrade()` dropped `purpose` and `artifact_origin`, silently rereading `gap_fill` as `first_capture` | Read | `P0001` preflight refuses while any declaration exists, with a regression. |
| 4 | `official` is registered at 90 but stored rows carry 0 and 100, and the docs presented 90 as fact | Read | `pit_semantics.md` now says 90 is nominal, that an `official` assertion at 100 can still outrank a pinned `capture_bound`, and that Step 15-b must not assume 90 describes any stored row. |
| 5 | Steps 14 and 16 were still marked `THIS PR`/`THIS STEP` after merging | Read | Both `MERGED`; only 15-a is current, in ROADMAP and CLAUDE.md. |

Finding 1 is the one worth keeping: **325 green tests could not see it**, because
nothing in the suite ever asked the metadata to emit DDL — migrations build the
real schema, and Alembic autogenerate does not compare `CHECK` constraints.
`tests/integration/test_step15a_metadata_ddl.py` closes that hole, and it
immediately found a *second* latent defect that Step 16 had already merged:
`trading_calendar_versions.trading_days_sorted_distinct` still carried the
subquery form in metadata, which PostgreSQL rejects in a `CHECK`, while the
migration had long since moved it into an immutable function.

## Scope exclusions confirmed

- No release rule is defined, registered, or evaluated here.
- No source's `accepted_evidence_types` changes; every adapter still writes
  `unknown` evidence with `published_at = NULL`.
- CLAUDE.md §31–32 still describe the pre-ADR-0020 rules; rewriting them belongs
  with the behaviour that makes them true, in 15-b.
