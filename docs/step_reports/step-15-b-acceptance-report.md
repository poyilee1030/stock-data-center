# Step 15-b Acceptance Report

Status: IN REVIEW

Scope: Release-rule registry and evaluation (ADR-0020 §2, §3)

Schema impact: `release_rules`, seeded with the four rules the ADR fixed, plus
an immutability trigger. Migration: `3c8e5f1b7a46`. PIT impact: none —
`evidence_plan` computes, and no adapter writes the new types yet.

`src/` changed by **+333 lines**, well inside the 800-line review threshold, so
this step was not split further. Step 15-c is a seam decision, not a size one:
see below.

## A defect in ADR-0020, found while implementing

ADR-0020 §3 stated that **all** rule instants resolve at end of day Asia/Taipei
and **all** move to the next business day. Its own table contradicts that twice:
`exchange_daily_settled` is 03:00 the next calendar day and `tdcc_weekly` is
Sunday 12:00, neither of which is end of day, and neither can take a business-day
shift — 03:00 is when the file exists whether or not the market opens, and
Sunday is never a business day, so shifting would push the rule a whole week.

The table is what the owner decided (decisions 1 and 2 named those instants
explicitly); the blanket sentence was a faulty generalization in the prose. The
ADR now distinguishes statutory deadlines from scheduled instants. **No rule
changed.**

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| Each rule has an id, a version, and a cited schedule or statute | PASS | `release_rules` holds the four rules; `authority` cannot be empty, and each cites its statute, owner decision, or the audit section behind it. |
| Rules are versioned and never edited | PASS | `UPDATE` and `DELETE` are rejected by trigger; an unknown version raises rather than falling back to the nearest one. |
| Weekends, holidays, year boundaries, and non-business-day deadlines | PASS | Both ADR worked examples are regressions: 2021Q2 → 2021-08-16 (08-15 is a Sunday) and 2026M04 → 2026-05-11 (05-10 is a Sunday). Q4 2023 → 2024-04-01 crosses the year boundary onto a Sunday. A typhoon closure on 2024-07-10 moves the revenue deadline to 07-11, so the shift follows the real calendar rather than a weekday rule. |
| A scheduled instant does not shift | PASS | `exchange_daily_settled` for 2024-07-23 resolves to 07-24 03:00 even though 07-24 was a closure, and answers for a year the calendar has never imported. |
| The evidence source names the rule and its version | PASS | `monthly_revenue_statutory@1`. |
| The write-side falsification rule | PASS | A first sighting later than the rule instant writes only the capture; a `gap_fill` capture later than the rule cannot falsify it, because it is not a first sighting. |
| A `gap_fill` produces no capture bound | PASS | It plans rule evidence only; with no rule it falls back to `unknown`. |

## What `evidence_plan` decides

| Purpose | May claim a capture bound? |
| --- | --- |
| `first_capture` | yes |
| `correction_check` | only for a revision it newly found |
| `gap_fill` | no |
| `unspecified` | no |

When nothing is provable, nothing is claimed: the plan falls back to `unknown`
with `published_at = NULL`, exactly the pre-ADR-0020 behaviour.

## Why the wiring is Step 15-c

The 800-line threshold is not reached, so this split is a seam decision rather
than a size one. Applying the policy changes what **every** adapter writes, from
`unknown` to real publication evidence, which makes history Market-PIT visible
for the first time. It needs per-source `accepted_evidence_types` opt-in, a
per-dataset reconciliation of what changed, and the CLAUDE.md §31–32 rewrite —
and §31–32 describe behaviour, so rewriting them before the behaviour changes
would make the documentation wrong in the other direction.

This step is correct on its own: the rules resolve, the policy computes, and no
adapter's behaviour changes.

## Verification

Database migrated from zero to `3c8e5f1b7a46`:

```text
355 passed, 3 skipped, 1 warning
```

Baseline before this step: 327. The 17 release-rule regressions and 11
evidence-plan unit tests are the difference; no existing test changed.

The Step 14 storage-contract guard again required the new table to be
classified, and the Step 15-a metadata-DDL guard kept the new constraints
honest.

## Scope exclusions confirmed

- No adapter writes the new evidence types; every one still emits `unknown`.
- No source's `accepted_evidence_types` changes.
- No rule is defined for financial-industry statements: their deadlines differ
  and Step 23 excludes them from v1. A later step must add its own rule rather
  than reuse the general-industry one.
