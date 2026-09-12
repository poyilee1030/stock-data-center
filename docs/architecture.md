# Architecture Contract

## Status

Accepted for Phase 0 on 2026-09-11.

## System boundary

`stock-data-center` is the sole owner of source ingestion, raw provenance,
business revision history, publication evidence, temporal visibility, source
selection, canonical reusable derived definitions/results, PostgreSQL storage,
the public data API, and any optional query cache.

Downstream systems consume resolved responses through the public API or SDK.
They must not connect directly to PostgreSQL or Redis and must not reproduce
PIT, evidence-selection, or source-selection rules.

The following concerns remain outside this repository:

- EPS or stock-selection model training
- feature and label construction owned by those models
- portfolio research, ranking, and backtesting

## Authoritative components

PostgreSQL 18 is the only authoritative store. It contains business history,
publication evidence, ingestion history, and provenance. Raw artifacts are
immutable, content-addressed evidence held behind a storage abstraction.

For securities, `security_code` is stable identity while market/listing venue is
source-observed, effective-dated metadata. Historical universe filtering occurs
after PIT metadata resolution; no current identity-row market is authoritative.

Redis, when configured, is only a disposable cache of already-resolved query
responses. Removing every Redis key, disabling Redis, or losing Redis must not
change the business result or provenance returned for a canonical request.

## Write path

```text
source
  -> immutable raw artifact + ingest run
  -> parser
  -> normalization
  -> PostgreSQL business/evidence history
```

No durable business or evidence write may exist only in Redis.
Repeated identical fetches remain many-to-one provenance observations of the
same business/evidence identity; they do not create fake revisions merely to
retain artifact/run lineage.

## Read path

```text
client
  -> API
  -> application service
  -> optional response cache
  -> PIT resolver on cache miss
  -> PostgreSQL
```

Routes, repositories, and PIT resolvers do not contain Redis-specific policy.
The service depends on a cache abstraction.

## Frozen contracts

- [PIT semantics](pit_semantics.md)
- [ADR-0001: temporal model](decisions/0001-temporal-and-pit-model.md)
- [ADR-0002: business revisions and publication evidence](decisions/0002-business-revisions-and-publication-evidence.md)
- [ADR-0003: immutable aggregates, hashes, and provenance](decisions/0003-immutable-aggregates-hashes-and-provenance.md)
- [ADR-0004: source capability and cross-source policy](decisions/0004-source-capability-and-cross-source-policy.md)
- [ADR-0005: optional cache architecture](decisions/0005-optional-cache-architecture.md)
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
- [Core PIT resolver contract](pit_resolver.md)
- [Security metadata and daily market data](security_daily_market.md)
- [Monthly revenue](monthly_revenue.md)
- [Financial filings and XBRL](financial_xbrl.md)
- [TDCC shareholding distribution](tdcc.md)
- [Institutional flow and securities financing](institutional_financing.md)
- [Market indices, corporate actions, and official valuation](market_reference.md)
- [Cache contract](cache.md)

Changes to an accepted decision require a superseding ADR and, when the change
affects planned behavior, a corresponding `ROADMAP.md` update.
