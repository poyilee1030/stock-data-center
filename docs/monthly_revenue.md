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
legacy consumers (audit §4.7, §6). Step 22 therefore stores them as observed
published comparatives, kept exactly as published and never reconciled against
our own series; the 2026M06/M07 pair, where 11 of 1,846 companies disagree, is
the regression fixture (audit §7.3). The virtual canonical-derived contract
`monthly_revenue_growth:v1` that would have recomputed them, and whose
calculator and PIT-safe input lineage would have belonged to Phase 10, leaves v1
with that decision (ROADMAP §16, Step 22).

## Coverage and history (Step 22-b)

Coverage is declared per market, one row each for `TWSE` (`mops_t21sc03_sii`)
and `TPEx` (`mops_t21sc03_otc`), with `calendar_month` cadence from 2020-01 and
no end. The period column is `revenue_period`, generated from `revenue_year`
and `revenue_month` rather than written beside them, so nothing can disagree
with the month it describes. `calendar_market` is required by the declaration
table and is not consulted for a monthly cadence: revenue is filed on a day of
the month, not on a trading day.

A market-month is two pages, `_0` domestic and `_1` foreign/KY, and both belong
to one source and one month. Coverage is therefore a property of the month, not
of the page: a month either page answered is covered, and the page-level result
is in the backfill report and the per-page manifests. The two pages never list
the same company; every stored month is scanned for it
(`scripts/reconcile_monthly_revenue.py`), because one company on both pages
would give one logical key two alternating revisions from one source
(CLAUDE.md §30).

`t21sc03` is regenerated by issuer status, so the source rewrites its own
history: a month's page lists the issuers that hold that status now. An issuer
that has since left both markets is missing from the 2020 page too, even though
legacy recorded it then. Those rows are outside the v1 universe (`pub` and
`rotc` are rejected at the adapter boundary), not a gap in what v1 covers; the
reconciliation names them rather than hiding them.


## Publication evidence (Step 22-c)

The official pages carry no publication instant, so Steps 22-a and 22-b wrote
`unknown` on every version and Market PIT saw nothing. The evidence comes from
the legacy `market.csv`, imported as `legacy_archive` bytes in two windows
(audit §7.1, §7.4, §7.5):

| Window | What the file holds | What is claimed |
| --- | --- | --- |
| 2020M01–2026M01 | a recovered announcement date beside today's corrected value | `press_report_bound` at the end of that day; the statutory rule where the date is still the 10th |
| 2026M02 onward | the legacy 22:45 job's own first-seen date and the value it saw | `legacy_capture_bound` at the end of that day, on a version of that value |

The bound is the end of the archive's day in market time: the file dates rows
to the day, and a bound that is slightly late is safe where an early one is
not.

The statutory rule (`monthly_revenue_statutory@1`) is named by the archive
importer for the rows whose recovered date is the unshifted 10th. It is
deliberately not declared on the source: a declaration would hand the same
instant to every version the official importer writes, including the `_1`
foreign and KY issuers the archive never held. Those keep `unknown` until
Step 27's forward capture proves a real one.

A correction the issuer made after legacy captured the row is a separate
version, and it stays Market-PIT invisible until a run that is the first to
see it records a `capture_bound`. Before that, Market PIT answers the value
that was public at the time, which is the point of importing the first
captures at all.
