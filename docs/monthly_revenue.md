# Monthly Revenue

## Phase 4 scope

Phase 4 provides cache-free normalized writes and PIT-safe queries for observed
monthly revenue. It uses the Phase 2 resolver and existing Phase 1 storage
contract; no new schema, REST API, Redis backend, or cache abstraction is added.

`MonthlyRevenueWriter` accepts normalized revenue/currency plus an existing raw
artifact and ingest-run observation. PostgreSQL verifies lineage, generates the
trusted ingestion time, and generates a business hash from revenue and currency.
The logical key is `(security, source, revenue_year, revenue_month)`.

## Canonical amount unit

`monthly_revenue_versions.revenue` is always an amount in the currency's major
unit: TWD means dollars, USD means dollars, and JPY means yen. It never stores an
implicit source-native thousands scale.

Source adapters must construct `SourceRevenueAmount` with an explicit
`RevenueScale` and call `MonthlyRevenueObservation.from_source` before the
writer. For example, a MOPS value of `410000000` expressed in thousand TWD is
normalized to canonical `410000000000` TWD. The normalized amount is what enters
PostgreSQL and the business-content hash. A source value in thousands and an
equivalent major-unit value therefore resolve to the same business revision.

## Business and evidence history

Business revisions and publication evidence remain independent:

- changed revenue or currency creates a new append-only business revision;
- unchanged values reuse the existing business revision even when fetched in a
  new ingest run;
- each fetch remains linked to that version through
  `monthly_revenue_version_observations`;
- learning or correcting publication time appends evidence targeting the same
  business version; and
- evidence correction/retraction uses the Phase 2 supersession rules.

Deduplicated evidence identity has the same guarantee:
`publication_evidence_observations` links every artifact/run that observed the
evidence row without creating fake evidence revisions. Both association tables
are append-only, enforce artifact/run membership, and validate exact
dataset/source identity in PostgreSQL.

`published_at` is the time the market could know the revenue. `recorded_at` is
DB-generated knowledge time. A delayed filing is invisible before its actual
publication instant. Evidence learned later cannot enter a reconstruction whose
`knowledge_as_of` predates that evidence. Unknown publication remains Market-PIT
invisible while legitimately ingested data can remain System-PIT visible.

## Query contract

`MonthlyRevenueService.revenue` resolves one period. `history` resolves an
inclusive year/month window and independently applies the requested PIT context
to every period. Results retain business-version, evidence, raw-artifact, and
ingest-run provenance through `ResolvedRecord`.
`MonthlyRevenueService.observations` returns every raw/ingest observation linked
to a resolved business version, including unchanged repeat fetches.

Source selection is exact. Independent source histories are never overwritten,
averaged, or selected by latest ingestion.

## Legacy field disposition

Observed `revenue` in currency-major units is returned from
`monthly_revenue_versions`. `currency` is stored there too, but it is not an
observation: the MOPS page states 單位：千元 as a page-level constant, so the
value is always TWD (audit §5). Publication timestamps remain publication
evidence, and parser coordinates remain raw-artifact-only.

MoM, YoY, cumulative revenue, cumulative YoY, the two comparative revenue
figures, and 備註 are published in the same MOPS row as 當月營收 and are read by
legacy consumers (audit §4.7, §6). PR #22 therefore stores them as observed
published comparatives, kept exactly as published and never reconciled against
our own series; the 2026M06/M07 pair, where 11 of 1,846 companies disagree, is
the regression fixture (audit §7.3). The virtual canonical-derived contract
`monthly_revenue_growth:v1` that would have recomputed them, and whose
calculator and PIT-safe input lineage would have belonged to Phase 10, leaves v1
with that decision (ROADMAP §16, PR #22). Until PR #22 lands,
`monthly_revenue_versions` still stores `revenue` and `currency` only.
