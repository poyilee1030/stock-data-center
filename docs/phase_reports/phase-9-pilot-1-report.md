# Phase 9 Pilot 1 Report: TWSE/TPEx Daily Market

## Scope

This is an incremental Phase 9 milestone report, not the Phase 9 acceptance
report. It proves two representative real adapters end to end before expanding
to other domains or full-market history.

## Results

| Criterion | Result | Evidence |
| --- | --- | --- |
| Official real source to raw artifact | PASS | The opt-in live regression fetched TWSE `STOCK_DAY` for 2330 and TPEx `tradingStock` for 6488, September 2025, retained each response by SHA-256 before parsing, and normalized 21 source rows from each resource covering 2025-09-01 through 2025-09-30. |
| Explicit source semantics | PASS | `twse-stock-day:v1` declares shares/TWD. `tpex-trading-stock:v1` declares lots/thousand-TWD and normalizes both by 1,000 before constructing canonical observations. |
| Trusted PostgreSQL write | PASS | Both adapters call `MarketDataWriter`; PostgreSQL generates business hashes and actual `ingested_at`. |
| Publication-time honesty | PASS | Historical endpoint responses create `unknown` evidence with `published_at = NULL`. |
| PIT result | PASS | Live and controlled integration regressions resolve imported rows under System PIT. The controlled regression confirms the unknown-publication row is Market-PIT invisible. |
| Idempotent restart | PASS | A successful import resumes without refetch, and changed scope or raw-store identity cannot reuse that ID. The crash-boundary regression also stops after committed raw capture, then resumes from the exact retained/hash-verified bytes with the original ingest run while a fetcher that fails if called remains unused. Absolute raw locators remain valid across CWD changes. No stale running run or fake business revision remains after completion. |
| Failure classification | PASS | Only explicit source/domain validation errors enter immutable quarantine. Adapter/writer programming failures and retained-artifact integrity failures preserve a captured checkpoint and its original raw/run lineage for repair and retry without refetch. |
| Concurrent restart | PASS | A real two-worker PostgreSQL regression proves the full `import_id + resource_key` lifecycle is advisory-locked: only one fetch/run/write occurs, the waiting worker consumes the completed checkpoint, ownership transitions use compare-and-swap predicates, and completed checkpoints require a successful referenced run. |
| Market-value sanity | PASS | Both adapters reject negative prices, volume, value, and count and enforce `low <= open/close <= high`; Phase 9 adds matching PostgreSQL checks at the canonical storage boundary. |
| Quarantine | PASS | A changed TPEx field contract retains raw bytes, fails before canonical writes, marks the run failed, and appends an immutable quarantine reason. |
| Reconciliation manifest | PASS | PostgreSQL records source, adapter, scope/fingerprint, observed coverage, raw/business/evidence/dedup/unknown/rejection counts, units, and warnings. Because Pilot 1 has no authoritative exchange calendar, it truthfully records `coverage_validation=not_evaluated` and `coverage_gaps=null`; the partial-month regression prevents an empty-list false claim. |
| Phase boundary | PASS | No derived calculator and no Redis/cache code was added. |

## Executed checks

```text
pytest tests/unit/test_phase9_daily_market_adapters.py
    16 passed

pytest tests/integration/test_phase9_real_ingestion_framework.py
       -m "integration and not live_source"
    8 passed, 1 deselected

RUN_LIVE_SOURCE_TESTS=1 pytest
       tests/integration/test_phase9_real_ingestion_framework.py
       -m live_source
    previously confirmed: 1 passed
    current environment rerun: blocked during TLS certificate-chain
    verification before any source response reached the adapter

full suite on a new database
    188 passed, 1 skipped (live source is opt-in)

Alembic Phase 9 downgrade then upgrade on that database
    PASS
```

## Remaining Phase 9 work

The representative pilot still needs security metadata and market-transfer
history, MOPS revenue and financial/XBRL, TDCC, institutional/margin/SBL, and
available Phase 8 domains. Full-market backfill remains blocked until that
expanded pilot and all Phase 9 acceptance criteria pass.
