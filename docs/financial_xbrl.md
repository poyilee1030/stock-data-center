# Financial Filings and XBRL

## Phase 5 scope

Phase 5 provides cache-free writes and PIT-safe reads for financial filing
aggregates. A filing is a draft parent with XBRL facts and an optional curated
quarterly summary. Only the PostgreSQL-generated seal makes the aggregate
visible. Phase 5 does not add a REST API, cache, TTM EPS, ROE, valuation, or
other derived calculators.

The source `filing_key` identifies one source-native filing revision. A
corrected business filing uses a new source revision key and remains an
independent business version. Publication evidence is appended separately, so
learning, correcting, or retracting a publication time never mutates the
filing or creates a false business revision.

## Aggregate and lineage contract

`FinancialFilingWriter` creates a draft, appends facts and curated summary
metrics, and requests a seal. PostgreSQL serializes sealing and every child
mutation by locking the same parent row. It computes the business hash from
the filing header and ordered children and supplies the trusted seal time.

After sealing, PostgreSQL rejects parent, fact, summary, and seal mutation.
Resolvers ignore drafts. Market PIT additionally requires affirmative accepted
publication evidence; System PIT uses the trusted seal time.

Repeated observations of the same `filing_key` reuse its version while
`financial_filing_version_observations` retains each artifact/run pair. The
generic `publication_evidence_observations` table provides the same guarantee
for deduplicated evidence. Both links are append-only and DB-validated against
the exact `financial_filing` dataset and source. Existing filings are backfilled
with their original lineage by the Phase 5 migration.

## XBRL identity

Concepts use canonical Clark notation:

```text
{namespace-uri}local-name
```

This prevents two taxonomies with the same local concept name from colliding.
Each fact contains exactly one numeric or text value. Its storage-generated
`context_hash` covers:

```text
entity identifier
period type and dates
explicit dimensions
typed dimensions
scenario
segment
```

PostgreSQL JSONB canonicalizes object-key order. Fact identity is filing,
QName, context hash, and unit, so dimensional facts coexist and a semantically
duplicate fact is rejected.

## Publication and Q4 semantics

`published_at` records when the filing became public. DB-generated
`recorded_at` records when the Data Center learned the evidence. Market PIT
requires both cutoffs; unknown publication is invisible. Source capability,
accepted evidence types, correction, retraction, and deterministic ranking use
the shared Phase 2 resolver.

There is no calendar-quarter availability shortcut. In particular, the
existence of a Q4 filing or EPS value in today's database cannot make it visible
in February. A Q4 result becomes visible only at its authoritative publication
instant and only under a knowledge cutoff that includes that evidence.

## Curated actual EPS

`quarterly_financial_summary` exposes stable curated facts from the selected
filing. `basic_eps` is the Phase 5 actual-EPS metric code and retains its unit.
`FinancialFilingService.actual_eps` first resolves the filing through the same
seal, source, evidence, and PIT rules, then reads `basic_eps` from that exact
version. It never searches the current database for an EPS value.

The summary is not a derived-metric engine. Reusable TTM EPS, profitability,
margin, and valuation calculations remain in the later canonical-derived phase
and must inherit PIT visibility from these resolved inputs.
