# Architecture Contract

## Status

Accepted for Phase 0 on 2026-09-11; storage revised by ADR-0027 (schema v2).

## System boundary

`stock-data-center` is the sole owner of source ingestion, raw provenance,
published-value history, publication time, temporal visibility, source
selection, canonical reusable derived definitions, PostgreSQL storage, and the
public data API.

Downstream systems consume resolved responses through the public API.
They must not connect directly to PostgreSQL and must not reproduce PIT or
source-selection rules.

The following concerns remain outside this repository:

- EPS or stock-selection model training
- feature and label construction owned by those models
- portfolio research, ranking, and backtesting

## Authoritative components

PostgreSQL 18 is the only authoritative store and there is no cache. It holds
every published value as append-only rows, the fetch log, and the publication
time where one is stored. Raw files are immutable and content-addressed under
`data/raw/<ab>/<sha256>`, behind a storage abstraction; each fetch names its
file by SHA-256.

A stock is its official code. The universe is listed and OTC common stocks
(ADR-0026): `stocks` holds every one on today's ISIN list and every one delisted
since 2020-01-02 that a source proves common, and `listings` each one's listing
spans (ADR-0028). The datasets are fetched only for stocks listed today; that
survivorship bias is accepted and disclosed until Step 38-b.

## Write path

```text
source
  -> raw file (data/raw) + fetches row
  -> parser
  -> keep today's common stocks
  -> compare with each key's latest row
  -> append only what changed
```

A repeated identical fetch logs its own `fetches` row and appends nothing, so it
never creates a fake revision and stays auditable.

## Read path

```text
client
  -> API
  -> application service
  -> PIT visibility and derived computation
  -> PostgreSQL
```

## Frozen contracts

Where an earlier ADR below conflicts with ADR-0026 or ADR-0027 — the
business/evidence split, seals, business hashes, the effective-dated security
market, the TDCC profile tables, the Phase 9 import framework — those two win
(CLAUDE.md §0).

- [PIT semantics](pit_semantics.md)
- [ADR-0001: temporal model](decisions/0001-temporal-and-pit-model.md)
- [ADR-0002: business revisions and publication evidence](decisions/0002-business-revisions-and-publication-evidence.md)
- [ADR-0003: immutable aggregates, hashes, and provenance](decisions/0003-immutable-aggregates-hashes-and-provenance.md)
- [ADR-0004: source capability and cross-source policy](decisions/0004-source-capability-and-cross-source-policy.md)
- [ADR-0007: canonical derived ownership and PIT](decisions/0007-canonical-derived-data-ownership-and-pit.md)
- [ADR-0009: aggregate market PIT seal cutoff](decisions/0009-aggregate-market-pit-seal-cutoff.md)
- [ADR-0010: source-specific evidence types](decisions/0010-source-specific-evidence-type-policy.md)
- [ADR-0011: effective-dated security market](decisions/0011-effective-dated-security-market.md)
- [ADR-0012: TDCC distribution profiles and seal completeness](decisions/0012-tdcc-distribution-profiles-and-seal-completeness.md)
- [ADR-0013: Phase 7 observed source semantics](decisions/0013-phase7-observed-source-semantics.md)
- [ADR-0014: Phase 8 market reference semantics](decisions/0014-phase8-market-reference-semantics.md)
- [ADR-0015: Phase 9 raw-first import framework](decisions/0015-phase9-raw-first-import-framework.md)
- [ADR-0016: current security metadata snapshots](decisions/0016-current-security-metadata-snapshots.md)
- [Canonical derived data contract](derived_data.md)
- [Schema v2](schema.md)
- [ADR-0026: v1 universe is common stocks only](decisions/0026-v1-universe-common-stocks-only.md)
- [ADR-0027: schema v2](decisions/0027-schema-v2.md)
- [ADR-0028: listing spans](decisions/0028-listing-spans.md)
- [Stock universe and daily market data](security_daily_market.md)
- [Monthly revenue](monthly_revenue.md)
- [Financial statements (iXBRL)](financial_xbrl.md)
- [TDCC shareholding distribution](tdcc.md)
- [Institutional flow and securities financing](institutional_financing.md)
- [Market indices, corporate actions, and official valuation](market_reference.md)
- [Trading calendar](trading_calendar.md)

Changes to an accepted decision require a superseding ADR and, when the change
affects planned behavior, a corresponding `ROADMAP.md` update.
