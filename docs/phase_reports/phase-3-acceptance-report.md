# Phase 3 Acceptance Report

Date: 2026-09-11

Result: PASS

## Scope delivered

Phase 3 delivers cache-free normalized writes and PIT-safe domain access for:

- stable security identity and market conflict detection;
- effective-dated security metadata, including names, industries, listing
  dates, delisting dates, and bounded effective intervals;
- historical security state and historical listed-universe queries;
- source- and revision-aware daily price/trading queries for one date or an
  inclusive date range;
- every intentionally preserved legacy `daily_quotes` observable; and
- business revision, publication evidence, raw artifact, and ingest-run
  provenance inherited from the Phase 2 resolver.

Primary evidence:

- `src/stock_data_center/market_data/`
- `tests/integration/test_phase3_security_daily_market.py`
- `tests/unit/test_phase3_contract_docs.py`
- [Security metadata and daily market-data contract](../security_daily_market.md)

One focused migration, `d81b5c9a3f20_protect_security_identity.py`, prevents
current-state mutation of stable identity and makes identity creation time
trusted. The Phase 1 version-table shapes remain unchanged. No REST API,
derived calculator, Redis package, or cache abstraction was added.

## Required acceptance criteria

| Criterion | Result | Concrete evidence |
| --- | --- | --- |
| No survivorship-only current security list | PASS | `security_universe` resolves metadata under the requested PIT context and business date, then applies that date's listing interval. A regression proves a subsequently delisted security appears historically while a future listing does not, and the later universe reverses appropriately. |
| Historical security state is queryable | PASS | `security_state` resolves effective-dated metadata revisions rather than latest state. Tests cover historical/current names, listing metadata, provenance, bounded intervals, prevention of expired-state fallback, and DB-enforced immutable identity. |
| Daily market data is source/revision aware | PASS | `daily_price` and `daily_price_history` use the Phase 2 resolver for each `(security, source, trade_date)`. Tests prove knowledge-cutoff revision reconstruction, independent source histories, canonical system-PIT window ordering, and full provenance. |
| Intentionally preserved legacy observable fields are queryable | PASS | A dataset-specific regression writes and resolves OHLC, volume, trade value/count, price change/direction, bid/ask snapshots, and parsed last bid/ask prices and volumes field-for-field. |
| Intentionally dropped fields are documented | PASS | `docs/security_daily_market.md` records `pced_file`, `pced_row`, and `pced_col` as raw-artifact-only parser coordinates and explains the identity/metadata disposition of date, symbol, market, and name. A contract test guards the documentation. |
| Dataset-specific regression tests exist | PASS | Twelve focused Phase 3 integration/unit tests cover historical universe and metadata, listing intervals, all legacy quote fields, market knowledge cutoffs, system PIT, source isolation, idempotent duplicate-fetch/evidence lineage, DB-enforced stable security identity, date validation, and absence of cache/HTTP coupling. |

## Permanent regression matrix

```text
historical non-survivorship universe         PASS
effective-dated metadata history             PASS
expired metadata interval                    PASS
complete legacy daily observable fields      PASS
daily business revision / knowledge cutoff   PASS
daily independent source histories           PASS
duplicate fetch / no false revision           PASS
raw and ingest provenance                     PASS
system-PIT daily history                      PASS
trusted, immutable security identity          PASS
no Redis or HTTP dependency                   PASS
```

## Verification

```text
PostgreSQL:                 18 (postgres:18)
pytest:                     57 passed
Phase 3 focused tests:      12 passed
alembic check:              No new upgrade operations detected
Python compileall:          PASS
git diff --check:           PASS
```

## Phase decision

All required Phase 3 criteria pass. Phase 3 is complete. Phase 4 must not begin
until this phase is reviewed and accepted.
