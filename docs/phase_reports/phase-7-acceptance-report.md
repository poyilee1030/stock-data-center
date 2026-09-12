# Phase 7 Acceptance Report

Date: 2026-09-12

Result: PASS

## Scope delivered

Phase 7 delivers cache-free normalized ingestion and PIT-safe reads for:

```text
institutional investor security flows
institutional market summaries
foreign holdings
margin and exchange short data
securities borrowing and lending (SBL)
```

The Phase 1 version tables remain the authoritative storage contracts. One
focused migration adds dataset-specific many-observation lineage, DB value
constraints, final storage-generated hash semantics, and an Asia/Taipei lower
bound on publication evidence. The `institutional_financing` package supplies
strict source value types, append-only deduplicating writers, single-record and
inclusive-history PIT reads, and observation-provenance access. Per-security
stock quantities normalize explicit source shares/lots to canonical shares
before storage and hashing.

Primary evidence:

- `src/stock_data_center/institutional_financing/`
- `tests/integration/test_phase7_institutional_financing.py`
- `tests/unit/test_phase7_contract.py`
- `tests/integration/test_phase1_schema.py`
- `migrations/versions/3f7c9a2d6e10_implement_phase7_source_data.py`
- [Phase 7 contract](../institutional_financing.md)
- [ADR-0013](../decisions/0013-phase7-observed-source-semantics.md)

The repository now contains twelve sequential migrations. The Phase 7
migration backfills observation links for populated version tables and
recomputes only business hashes to exclude logical keys consistently. It
preserves trusted ingestion and evidence timestamps. Its downgrade restores
the prior hash contract. It has no cache impact because no cache exists.

No REST API, Redis/cache implementation, source fetch adapter, canonical
derived calculator, or model-specific feature was added.

## Review blocker closure

| Finding | Result | Concrete evidence |
| --- | --- | --- |
| Canonical share-quantity unit | PASS | The Phase 7 contract freezes all per-security stock quantities to shares. `QuantityScale`, `SourceShareQuantity`, and `ShareQuantity` require explicit source scale and reject ambiguous raw numerics in canonical observations. |
| Lot/share equivalence and hash identity | PASS | `test_tpex_lots_and_share_inputs_dedupe_after_share_normalization`: TPEx-style 500 lots stores as 500,000 shares; an equivalent share representation has the same hash, reuses the same revision, and keeps both fetch lineages. Unit coverage independently checks the 500-lot/500,000-share conversion. |
| Trust/dealer absolute holding semantics | PASS | Option B is explicit in the domain inventory, contract, and ADR. With no reliable v1 baseline, legacy zero-origin values are renamed `trust_cumulative_net_*` / `dealer_cumulative_net_*` under `institutional_cumulative_flow:v1`; they are proxies and never claim absolute ownership. |

## Required acceptance criteria

| Criterion | Result | Concrete evidence |
| --- | --- | --- |
| Each source domain has explicit logical/revision keys | PASS | The Phase 7 contract lists each logical key. `test_all_phase7_domains_round_trip_with_source_faithful_fields` writes and resolves all five domains. PostgreSQL hashes only business values and unique constraints combine source, logical key, and hash. |
| Publication/effective-time semantics are documented | PASS | The contract defines `trade_date` as effective observation date, not publication time. `test_publication_before_trade_date_and_wrong_lineage_are_rejected` proves PostgreSQL rejects earlier local publication evidence (`23514`). Shared two-clock resolver behavior is exercised by the backfill test. |
| Historical backfill preserves System PIT | PASS | `test_backfill_uses_actual_ingestion_and_evidence_knowledge_times` writes a 2019 observation now: it is absent before DB-generated ingestion, visible after it, and remains market-invisible under a knowledge cutoff before evidence recording. |
| Source-specific provenance is preserved | PASS | `test_repeat_fetch_reuses_revision_and_preserves_each_observation` proves two unchanged fetches reuse one revision while retaining two artifact/run links. Cross-source association is rejected (`23514`); links reject mutation (`55000`). |
| Old model-selection raw dependencies are reconstructible | PASS | `test_legacy_observed_dependencies_have_explicit_storage_columns` checks every inventoried institutional, foreign-holding, margin/short, and SBL source field. Actual observed values survive write/PIT resolution in canonical units. The legacy zero-origin trust/dealer calculation remains numerically reproducible only under the corrected cumulative-flow proxy names; the contract does not claim absolute holdings. |
| Dataset-specific regression tests exist | PASS | Twenty-two focused tests (13 unit cases, 9 integration tests) cover all five datasets, explicit share/lot normalization and deduplication, cumulative-flow proxy inventory semantics, source-faithful signed values, PIT timing, source isolation, repeat provenance, DB constraints, append-only associations, schema coverage, and populated migration round-trip. |

## Additional invariant evidence

```text
gross buy/sell values non-negative                       PASS
canonical stock quantity unit = share                   PASS
explicit SHARE/LOT source-boundary normalization        PASS
500 lots == 500000 shares in value/hash/revision        PASS
ambiguous untyped canonical quantity rejected           PASS
signed institutional net flows preserved verbatim       PASS
signed SBL adjustment preserved verbatim                 PASS
published ratios use explicit percent semantics          PASS
at least one source value required                       PASS
business hash and ingestion time storage-generated       PASS
unchanged refetch / no fake business revision            PASS
every refetch retains raw artifact and ingest run         PASS
observation links validate exact dataset/source           PASS
independent source histories                              PASS
inclusive history PIT resolution                         PASS
unknown/late evidence rules use shared resolver           PASS
publication cannot predate local observation date         PASS
populated migration lineage backfill                      PASS
migration upgrade/downgrade hash contract round-trip      PASS
no model-specific pressure/ranking logic                  PASS
no absolute trust/dealer holding claim without baseline  PASS
legacy cumulative-flow proxy explicitly renamed          PASS
no Phase 10 canonical calculators                         PASS
CACHE_BACKEND=none / no Redis dependency                  PASS
```

The preserved TDCC boundary remains explicit in the Phase 7 contract:
shareholding concentration must consume PIT-safe sealed TDCC snapshots,
`tdcc-opendata-v1` remains immutable after first use, new formats require new
profile codes, and downstream derived lineage must retain the TDCC
profile/schema identity.

## Verification

```text
PostgreSQL:                 18 (postgres:18)
pytest:                     147 passed (Phase 6 baseline: 125 passed)
Phase 7 focused tests:      22 passed (9 integration, 13 unit cases)
alembic migrations:         12, single head 3f7c9a2d6e10
alembic check:              No new upgrade operations detected
populated migration:        downgrade/upgrade/hash restoration PASS
Python compileall:          PASS
git diff --check:           PASS
```

## Phase decision

All required Phase 7 criteria pass. Phase 7 is ready for review. Phase 8 must
not begin until this phase is reviewed and accepted.
