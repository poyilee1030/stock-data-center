# Monthly Revenue

## Phase 4 scope

Phase 4 provides cache-free normalized writes and PIT-safe queries for observed
monthly revenue. It uses the Phase 2 resolver and existing Phase 1 storage
contract; no new schema, REST API, Redis backend, or cache abstraction is added.

`MonthlyRevenueWriter` accepts normalized revenue/currency plus an existing raw
artifact and ingest-run observation. PostgreSQL verifies lineage, generates the
trusted ingestion time, and generates a business hash from revenue and currency.
The logical key is `(security, source, revenue_year, revenue_month)`.

## Business and evidence history

Business revisions and publication evidence remain independent:

- changed revenue or currency creates a new append-only business revision;
- unchanged values reuse the existing business revision even when fetched in a
  new ingest run;
- each fetch remains preserved through raw artifact observations;
- learning or correcting publication time appends evidence targeting the same
  business version; and
- evidence correction/retraction uses the Phase 2 supersession rules.

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

Source selection is exact. Independent source histories are never overwritten,
averaged, or selected by latest ingestion.

## Legacy field disposition

Observed `revenue` and `currency` are returned from
`monthly_revenue_versions`. Publication timestamps remain publication evidence.
Free-form source `comment` and parser coordinates remain raw-artifact-only.

MoM, YoY, cumulative revenue, and cumulative YoY are not duplicated as observed
facts. They remain the virtual canonical-derived contract
`monthly_revenue_growth:v1`, whose actual calculator and PIT-safe input lineage
belong to Phase 9.
