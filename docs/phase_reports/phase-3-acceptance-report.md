# Phase 3 Acceptance Report

Date: 2026-09-12

Result: PASS

## Scope delivered

Phase 3 delivers cache-free normalized writes and PIT-safe domain access for:

- stable security-code identity across market transfers;
- effective-dated security metadata, including historical market membership,
  names, industries, listing dates, delisting dates, and bounded intervals;
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

Two focused migrations protect stable identity and correct the Phase 1 market
assumption: `d81b5c9a3f20_protect_security_identity.py` makes security-code
identity immutable and trusted, while
`f3a74c12e690_version_security_market.py` moves market into metadata history,
backfills existing rows, and includes market in the business hash. See
[ADR-0011](../decisions/0011-effective-dated-security-market.md). No REST API,
derived calculator, Redis package, or cache abstraction was added.

## Required acceptance criteria

| Criterion | Result | Concrete evidence |
| --- | --- | --- |
| No survivorship-only current security list | PASS | `security_universe` resolves metadata under the requested PIT context and business date before applying listing and market filters. Regressions cover delisted/future securities and prove 5236 belongs only to the TPEx 2025 universe and only to the TWSE post-transfer universe. |
| Historical security state is queryable | PASS | `security_state` resolves effective-dated metadata revisions rather than latest state. Tests cover historical/current names, historical market membership, one stable ID across a venue transfer, listing metadata, provenance, bounded intervals, expired-state prevention, and DB-enforced immutable identity. |
| Daily market data is source/revision aware | PASS | `daily_price` and `daily_price_history` use the Phase 2 resolver for each `(security, source, trade_date)`. Tests prove knowledge-cutoff reconstruction, independent source histories, TPEx/TWSE venue histories sharing one security identity, canonical system-PIT window ordering, and full provenance. |
| Intentionally preserved legacy observable fields are queryable | PASS | A dataset-specific regression writes and resolves OHLC, volume, trade value/count, price change/direction, bid/ask snapshots, and parsed last bid/ask prices and volumes field-for-field. |
| Intentionally dropped fields are documented | PASS | `docs/security_daily_market.md` records `pced_file`, `pced_row`, and `pced_col` as raw-artifact-only parser coordinates and explains the identity/metadata disposition of date, symbol, market, and name. A contract test guards the documentation. |
| Dataset-specific regression tests exist | PASS | Sixteen focused Phase 3 integration/unit tests cover historical universe and metadata, effective-dated market transfer, populated-schema migration/backfill/rehash, listing intervals, all legacy quote fields, market knowledge cutoffs, system PIT, venue/source isolation, idempotent duplicate-fetch/evidence lineage, DB-enforced stable security identity, date validation, documentation contract, and absence of cache/HTTP coupling. |

## Permanent regression matrix

```text
historical non-survivorship universe         PASS
effective-dated metadata history             PASS
TPEx-to-TWSE market transfer                 PASS
cross-venue prices share stable identity     PASS
populated market migration/backfill/rehash   PASS
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
pytest:                     61 passed
Phase 3 focused tests:      16 passed
alembic check:              No new upgrade operations detected
Python compileall:          PASS
git diff --check:           PASS
```

## Phase decision

All required Phase 3 criteria pass. Phase 3 is complete. Phase 4 must not begin
until this phase is reviewed and accepted.
