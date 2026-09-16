# Step 19-c Acceptance Report

Status: IN REVIEW

Scope: the corporate-action import path — set-based event registration and
version writes for a range file, the per-row detail fetches a TWSE range file
needs, retraction of an event whose row disappeared inside the file's
executed coverage, the six source policies and their evidence types, and the
CLI.

Schema impact: migration `02f0a144b1fc` adds `corporate_action_retractions`
(append-only, immutable) and declares `dataset_sources` rows for the six
result-feed sources (`accepted_evidence_types = ['official', 'capture_bound']`,
no release rule — ADR-0022 §4). Migration impact: upgrade/downgrade pass;
the downgrade refuses while any `ingest_runs`/`import_manifests`/retraction
row references a declared source. PIT impact: versions inherit visibility
from their own publication evidence as usual; retraction is a System-PIT-only
fact for now (ADR-0022 §1).

`src/` changed by +622/−1 lines (`corporate_action.py` 329, `lifecycle.py`
+109, `market_reference/ingestion.py` +100, `cli.py` +56, `metadata.py` +22,
six other importers +1 each for a shared hook parameter, `policy.py` +1).

## Design decisions (ADR-0022)

Two things the shared `RawFirstImporter` framework never needed before:

- **Retraction** is its own append-only `corporate_action_retractions` table,
  not a `corporate_action_versions` revision — that table's rows are typed by
  `action_type` and require the terms each type needs, which a retraction has
  none of. Deduplicated on `(event_id, raw_artifact_id)`; resolving whether a
  later reappearance un-retracts an event is deferred until a reader needs
  the answer, since none exists yet.
- **Per-row detail fetches happen before the write transaction opens.**
  `RawFirstImporter` gained `_capture_dependencies` (default: none), called
  after the primary resource parses and before `_write_business`, with the
  same quarantine/resumability handling `parse()` already has.
  `CorporateActionImporter` uses it to fetch TWT49UDetail/TWTAVUDetail pages
  and build every row's finished `CorporateActionObservation`, so
  `_write_business` is a pure write and a `SourceDataError` from either the
  list or a detail page quarantines the whole range the same way.

## Acceptance evidence

All from `tests/integration/test_step19c_corporate_action_ingestion.py`,
using the same TWT49U/detail fixtures 19-b captured on 2026-09-16 (2454 息,
6442 權, 2543 權息).

| Criterion | Result | Evidence |
| --- | --- | --- |
| Correction regression: same locator, changed terms → new revision of the same event | PASS | `test_a_corrected_detail_creates_a_new_revision_of_the_same_event`: a corrected cash amount creates 1 new version; the event still has exactly 2 versions under `TWT49U:20240104`. |
| A removed row produces a retraction, not a deletion | PASS | `test_a_row_dropped_from_the_covered_range_is_retracted_not_deleted`: 6442 drops from a rerun of the same January window; its event/version rows and hashes are byte-identical before and after, and one `corporate_action_retractions` row appears with `reason = 'absent_from_covered_range'`. |
| A row outside the executed coverage is never retracted by its absence | PASS | `test_a_row_outside_the_covered_window_is_never_retracted`: 2543 (May) is absent from a January-only rerun; zero retraction rows are written. |
| Re-running a range creates no revision | PASS | `test_rerunning_the_same_range_creates_no_revision`: 0 created, 3 deduplicated; 0 retractions (the same bytes prove no absence). |
| A quarantined range keeps its raw artifacts and writes no business row | PASS | `test_a_failing_detail_quarantines_the_range_and_keeps_raw_artifacts`: a detail page answering `無相關資料` raises `ResourceQuarantinedError("no_data_for_date")`; 0 events, 0 versions, ≥2 raw artifacts (list + the failing detail) retained. |
| Set-based registration + detail completion end-to-end | PASS | `test_a_range_registers_events_and_completes_them_from_their_details`: 3 rows → 3 events, 3 versions, all three `capture_bound` evidence rows; the 權息 row's stored `cash_dividend_per_share`/`free_share_ratio`/`rights_ratio`/`subscription_price` match the source detail page exactly (`0.4`, `0.14`, `0.20211906001`, `33`). |

## Verification

```text
561 passed, 3 skipped in 101s
```

3 skipped are the pre-existing `RUN_LIVE_SOURCE_TESTS=1` official-endpoint
pilots, unrelated to this step. No test written for this step used a live
network call; all six new integration tests use the fixtures 19-b already
captured.

- `alembic upgrade head` / `downgrade -1` / `upgrade head` / `alembic check`:
  clean, no drift.
- `ruff check` on every file this step touches: zero new findings. The two
  pre-existing unsorted-import findings in `lifecycle.py` and
  `trading_calendar.py`/`daily_market.py` predate this branch (confirmed
  against `main`), matching 19-b's precedent for the `adapters/__init__.py`
  `__all__` ordering.
- `docs/data_domain_inventory.json`: `corporate_action_retractions` classified
  as an excluded (provenance-only) table; the storage-contract coverage test
  passes.

### A pre-existing test this step's migration required adjusting

`tests/integration/test_step19a_corporate_action_terms.py` downgrades from
head to the revision before `8e4b2c7d9a13` in three tests, to exercise that
migration's own ratio-representability refusal in isolation. Once this step's
migration sits on top and declares these sources, `ingest_runs` created by
those tests' `write()` helper (real corporate-action data on
`tpex_exdailyq`, one of the six declared sources) is referenced by a genuine
foreign key from `ingest_runs` to `dataset_sources(dataset_code, source)` —
so a downgrade that tries to pass through this step's migration while that
data exists cannot succeed, by design (append-only source-policy protection,
mirroring `7a2c9e4d1b58`'s own guard and its stated reasoning). The three
tests now pin their schema to `8e4b2c7d9a13` explicitly (`AT_STEP_19A`)
instead of riding `"head"`, so they test exactly what they always tested,
decoupled from whatever migration lands on top of it next. ADR-0022 §5
records the same reasoning for this step's own downgrade guard.

## Scope exclusions confirmed

- No backfill and no fetch of every detail page across 2020-2026; that is
  19-d's work (2020-01-01 → 2026-09-11, ~7,800 `TWT49UDetail` requests).
- No legacy `dividend` reconciliation; 19-d's acceptance criterion.
- No resolver/read service for corporate actions, PIT-aware or otherwise —
  nothing consumes this data yet, and ADR-0022 §1 defers the un-retraction
  question to whichever step adds the first reader.
- No ETF split/reverse-split feeds (19-e).

## Known limitations / deferred work

- Retraction visibility does not yet account for a later reappearance
  un-retracting an event — deliberately deferred (ADR-0022 §1) until a
  consumer needs the answer, rather than guessed now.
