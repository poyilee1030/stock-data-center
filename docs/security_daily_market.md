# Security Metadata and Daily Market Data

## Phase 3 scope

Phase 3 exposes a cache-free Python domain contract for normalized writes and
PIT-safe reads. REST/OpenAPI remains Phase 10 work. Source downloading and raw
artifact storage are separate from normalization: `MarketDataWriter` accepts an
existing `(raw_artifact_id, ingest_run_id)` observation, and PostgreSQL verifies
that lineage belongs to the exact dataset and source.

The service always delegates business revision and publication-evidence
selection to the Phase 2 resolver. It never reads Redis and never substitutes a
current-state security list for a historical universe.

## Security identity and history

`security.security_code` is stable identity. PostgreSQL rejects identity UPDATE,
DELETE, and TRUNCATE, and overwrites caller-provided `created_at` with trusted
statement time. Registering an already-known code returns the same `security_id`.
Market is not stored on this identity row.

Observed market membership, names, industry, listing dates, delisting dates, and
effective ranges live in append-only `security_metadata_versions`. Market is
included in the metadata business hash. A security state on business date `D`
is selected as follows:

1. resolve each source-specific metadata logical key with `effective_from <= D`
   under the requested market or system PIT context;
2. reject a resolved revision whose `effective_to < D`;
3. choose the greatest remaining `effective_from` deterministically; and
4. retain the full resolver provenance and authoritative evidence.

The historical listed universe applies the resolved state on `D`, not the
security's latest metadata or an identity-row market. An optional market filter
is applied only after metadata resolution. Listing membership uses the half-open interval
`listed_on <= D < delisted_on`. A missing `listed_on` means the source did not
provide a start bound; visible metadata remains eligible until `delisted_on`.
Callers can request `listed_only=False` to inspect PIT-visible delisted states.

## Daily market-data contract

The query contract supports one trade date or an inclusive date window. Every
logical key is `(security, source, trade_date)`. Each date is independently
resolved under the explicit PIT context, so corrections do not overwrite old
business revisions and evidence learned after `knowledge_as_of` cannot leak
into historically reproducible results. System PIT uses trusted `ingested_at`.

The normalized writer is append-only. PostgreSQL generates `ingested_at` and
`business_content_hash`; caller values are never authoritative. A repeated
fetch with identical business values reuses the existing business version,
while its separate raw-artifact observation and ingest run remain durable.

## Legacy `daily_quotes` field disposition

The following source-observable values are queryable in every resolved daily
record:

| Legacy concept | Phase 3 result field |
| --- | --- |
| OHLC | `open_price`, `high_price`, `low_price`, `close_price` |
| volume | `volume` |
| trade value | `trade_value` |
| trade count / transactions | `trade_count` |
| price change | `price_change` |
| price direction | `price_direction` |
| source bid snapshot | `bid_snapshot` |
| source ask snapshot | `ask_snapshot` |
| parsed last bid/ask price and volume | `last_bid_price`, `last_ask_price`, `last_bid_volume`, `last_ask_volume` |

`date`, `symbol`, `market`, and `name` are not dropped: they resolve to the
trade-date logical key, stable security identity, and effective-dated metadata.
The same `security_id` can therefore carry TPEx prices before a transfer and
TWSE prices afterward without merging the two source histories.

The only intentionally non-queryable normalized columns from legacy
`daily_quotes` are `pced_file`, `pced_row`, and `pced_col`. They are parser/source
coordinates, not business values. They remain preserved in the immutable raw
artifact and ingest provenance rather than being duplicated into every daily
business revision.

## Public contract

`MarketDataService` provides:

- `security_state` for a security on one historical effective date;
- `security_universe` for a PIT-safe historical listed universe;
- `daily_price` for one source-aware daily observation; and
- `daily_price_history` for an inclusive PIT-resolved date window.

Results return Phase 2 `ResolvedRecord` provenance. Callers see dataset concepts,
PIT cutoffs, source, business revision, publication evidence, and raw lineage;
they do not need table names, migration internals, or cache keys.
