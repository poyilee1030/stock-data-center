# Phase 9 Pilot 2 Report: TWSE/TPEx Security Metadata Snapshots

## Scope

This is an incremental Phase 9 milestone report, not the Phase 9 acceptance
report. It adds official current security-metadata ingestion and stable
identity registration. Historical market-transfer reconstruction and other
Phase 9 domains remain outside this milestone.

## Results

| Criterion | Result | Evidence |
| --- | --- | --- |
| Shared import lifecycle | PASS | Daily market and security metadata both use `RawFirstImporter` for advisory locking, durable raw capture, captured resume, CAS completion, manifest state, quarantine, and operational-failure handling. Existing Pilot 1 regression tests remain green after extraction. |
| Official source contracts | PASS | `TWSESecurityMetadataAdapter` maps TWSE `t187ap03_L`; `TPExSecurityMetadataAdapter` maps TPEx `t187ap03_O`. Required report date, identity, name, industry, and listing-date fields are validated before canonical construction. |
| Stable identity | PASS | Each company code is registered through `MarketDataWriter.register_security`; venue remains effective-dated metadata rather than immutable identity. |
| Effective-time honesty | PASS | Current name/industry/venue state begins on the official snapshot report date. The listing date is retained separately and is never used to backdate all current fields. |
| Publication-time honesty | PASS | Neither endpoint proves its exact original publication instant. Imports create `unknown` evidence with `published_at=NULL`; controlled PIT tests prove System-PIT visibility and Market-PIT invisibility. |
| No fake revisions | PASS | A later equal snapshot reuses the latest equal business state while retaining a new raw artifact observation and evidence observation. A changed field starts a new report-date version. |
| No inferred delisting | PASS | Omission from a later current snapshot does not set `delisted_on` or close an interval. Historical delisting requires separate evidence. |
| Raw-first restart | PASS | A simulated process crash after committed capture resumes from the same hash-verified bytes and original ingest run with a fetcher that fails if called. |
| Quarantine boundary | PASS | Missing required source fields are quarantined after raw capture; no canonical security metadata is written. Security-specific adapter and writer operational-failure regressions remain `captured`, create no quarantine row, and resume from retained bytes without refetch. |
| Reconciliation | PASS | Manifest records source/adapter, report-date coverage, row count, field contract, effective/listing/publication semantics, and explicitly reports historical coverage as `not_evaluated` and transfer history as `not_in_scope`. |
| Phase boundary | PASS | No historical transfer inference, bulk backfill, derived calculator, calendar, cache, or unrelated dataset was added. |

## Executed checks

```text
pytest tests/unit/test_phase9_daily_market_adapters.py
       tests/unit/test_phase9_security_metadata_adapters.py
       tests/integration/test_phase9_real_ingestion_framework.py
       tests/integration/test_phase9_security_metadata_ingestion.py
       -m "not live_source"
    41 passed, 2 deselected

full suite on a new database
    205 passed, 2 skipped (live source is opt-in)

ruff check (changed ingestion/market-data/test files)
    PASS
```

The live-source regression is opt-in:

```bash
RUN_LIVE_SOURCE_TESTS=1 pytest -m live_source \
  tests/integration/test_phase9_security_metadata_ingestion.py
```

Current official artifacts fetched on 2026-09-13 parsed successfully after the
adapter contract was corrected to preserve TWSE's official six-digit TDR codes:

```text
TWSE report 2026-09-11: 1,094 rows
TPEx report 2026-09-12:   891 rows
```

The in-process live rerun was blocked before a source response by the local
TLS certificate chain, matching the environment limitation already recorded
for Pilot 1. TLS verification was not disabled. The controlled raw-to-Postgres
suite and parsing of the separately fetched exact official bytes both pass.

## Remaining Phase 9 work

Authoritative historical market-transfer/delisting reconstruction remains the
next security-identity milestone. MOPS revenue and financial/XBRL, TDCC,
institutional/margin/SBL, available Phase 8 domains, legacy migration,
expanded pilot reconciliation, and full backfill also remain incomplete.
