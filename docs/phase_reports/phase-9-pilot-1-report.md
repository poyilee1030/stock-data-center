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
| Idempotent repeat | PASS | Same import ID resumes without refetch, and changed scope cannot reuse that ID. A new import preserves a new run/raw observation while unchanged business and evidence identities dedupe. A quarantined attempt can retry under the same ID; its prior failure remains in the final manifest. |
| Quarantine | PASS | A changed TPEx field contract retains raw bytes, fails before canonical writes, marks the run failed, and appends an immutable quarantine reason. |
| Reconciliation manifest | PASS | PostgreSQL records source, adapter, scope/fingerprint, coverage, raw/business/evidence/dedup/unknown/rejection counts, units, gaps, and warnings. |
| Phase boundary | PASS | No derived calculator and no Redis/cache code was added. |

## Executed checks

```text
pytest tests/unit/test_phase9_daily_market_adapters.py
    5 passed

pytest tests/integration/test_phase9_real_ingestion_framework.py
       -m "integration and not live_source"
    1 passed, 1 deselected

RUN_LIVE_SOURCE_TESTS=1 pytest
       tests/integration/test_phase9_real_ingestion_framework.py
       -m live_source
    1 passed, 1 deselected

full suite on a new database
    170 passed, 1 skipped (live source is opt-in)
```

## Remaining Phase 9 work

The representative pilot still needs security metadata and market-transfer
history, MOPS revenue and financial/XBRL, TDCC, institutional/margin/SBL, and
available Phase 8 domains. Full-market backfill remains blocked until that
expanded pilot and all Phase 9 acceptance criteria pass.
