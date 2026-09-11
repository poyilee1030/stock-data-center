# Core PIT Resolver

## Scope

Phase 2 implements the reusable PostgreSQL-backed resolver in
`src/stock_data_center/pit/`. It has no cache dependency and does not expose an
HTTP API. Later domain services call this contract with a SQLAlchemy
`Connection`, a registered dataset code, the dataset's exact logical key, an
optional explicit source, and one explicit PIT context.

```python
result = PITResolver().resolve(
    connection,
    dataset_code="daily_price",
    source="twse",
    logical_key={"security_id": 123, "trade_date": trade_date},
    context=MarketPITContext(
        information_as_of=information_cutoff,
        knowledge_as_of=knowledge_cutoff,
    ),
)
```

`None` means no version is visible under that exact request. Invalid logical
keys, naive timestamps, unknown/ambiguous sources, and unsupported source/PIT
combinations raise explicit resolver errors rather than falling back to current
state.

## Source resolution

Source selection follows ADR-0004:

1. use the explicit source when supplied;
2. otherwise use the one configured canonical source;
3. otherwise fail explicitly.

Capability is checked on the exact `(dataset_code, source)` row. Market PIT
requires `supports_market_pit` and verified evidence status. System PIT requires
`supports_system_pit`. No capability is inherited across sources.

## Authoritative evidence

For each candidate business version, the resolver:

1. retains evidence with `recorded_at <= knowledge_as_of`;
2. removes evidence superseded by another retained event;
3. ranks remaining chain heads by `quality_rank`, then `recorded_at`, then
   storage-generated evidence ID, all descending;
4. interprets the first head; `unknown` and `retraction` are invisible, while
   `assertion` and `correction` require
   `published_at <= information_as_of`.

Supersession happens before ranking, so even a lower-ranked correction or
retraction replaces the assertion it supersedes. Independent chain heads still
use the deterministic ranking. Among visible business revisions for one
logical key/source, the resolver chooses greatest authoritative
`published_at`, then greatest storage-generated version ID.

## System resolution and aggregates

Single-row versions require `ingested_at <= system_as_of` and resolve by latest
ingestion time then version ID. Financial and TDCC aggregates are joined to
their dataset-specific seal table; only `seal.ingested_at <= system_as_of` is
eligible for system PIT. Market PIT additionally requires
`seal.ingested_at <= knowledge_as_of`; a seal committed later cannot leak a
formerly incomplete aggregate into a historically reproducible result.
Consequently, a committed draft is invisible in both PIT modes.

Market resolution never substitutes ingestion/fetch time for publication time.
System resolution never consults publication evidence.

## Result and provenance

`ResolvedRecord` includes:

- resolved dataset, source, mode, and every effective cutoff;
- normalized business data separated from infrastructure fields;
- business version ID/hash and trusted ingestion or seal time;
- raw artifact ID/hash/URI/stored time;
- ingest run ID/status/start/completion;
- source URI and fetch time;
- aggregate seal identity where applicable; and
- the authoritative evidence event and its evidence provenance for market PIT.

The result contract contains no Redis key, PostgreSQL table name, or
API-specific representation.

## Registered v1 contracts

The registry covers all Phase 1 observed version domains with real publication
evidence targets, including the financial and TDCC seal rules. New observed
domains extend the registry rather than duplicating PIT logic in handlers.

Canonical derived resolution will compose this input resolver in its later
implementation phase. Its inherited PIT context remains governed by
`docs/derived_data.md`.
