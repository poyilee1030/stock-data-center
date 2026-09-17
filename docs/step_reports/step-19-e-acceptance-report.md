# Step 19-e Acceptance Report

Status: IN REVIEW (#27)

Scope: verify TWSE `TWTCAU` and TPEx `etfSplitRslt`/`etfRvsRslt` (found in
19-a, never in Step 19's original six-feed contract) against a live fetch,
add their adapters and dataset sources, and run a real 2020-01-01 →
2026-09-11 backfill for all three.

Schema impact: migration `9f3d7c2e5a41` declares three more
`dataset_sources` rows (`twse_twtcau`, `tpex_etfsplitrslt`,
`tpex_etfrvsrslt`), same shape as `02f0a144b1fc` — `capture_bound`
evidence, no release rule. No new tables. Migration impact: upgrade/
downgrade pass on `stockdc`; the downgrade guard refuses while any
`ingest_runs`/`import_manifests` row references a declared source.

`src/` changed +141/−6 across 4 files (`corporate_action.py` +125:
`TWSEETFSplitAdapter`, `TPExETFSplitAdapter`, `TPExETFReverseSplitAdapter`,
and a `_build_locator` hook on `_TWSEListAdapter`; `adapters/__init__.py`
+6 exports; `cli.py` +11 for the three feed choices; `models.py` +5 to add
the three feeds to `RESULT_FEEDS`, without which `ExchangeLocator` would
refuse every row as `unknown_feed`).

## Design decisions (ADR-0023)

Live verification (`docs/source_field_audit.md`) found two things that
decided the design before any code was written:

- **`TWTCAU` publishes no exchange ratio and no detail page** — only a
  `分割(反分割)` direction label and the same two official prices every other
  feed carries. `TWSEETFSplitAdapter` stores `action_type="other"`
  (TWTB8U's existing no-ratio precedent), not a derived `old_shares`/
  `new_shares` from dividing two prices.
- **TPEx's two feeds have listed zero rows, ever**, over the full
  2020-2026 window. Their `_row()` raises `unverified_schema` for any row
  rather than reusing `pvChgRslt`'s never-verified-against-this-feed detail
  label schema.

## Acceptance evidence

Real live backfill against `stockdc_step19e` (fresh database, migrated to
`9f3d7c2e5a41`), 2026-09-17, `--purpose first_capture`.

| Criterion | Result | Evidence |
| --- | --- | --- |
| Fields, units and history verified against a live fetch, not assumed | PASS | `docs/source_field_audit.md`'s ETF-split section: `TWTCAU` sampled live, 11 real rows across 2020-01-01 → 2026-09-11; both TPEx feeds sampled live, `totalCount: 0`. |
| A real 2020-01-01 → 2026-09-11 backfill exists for all three feeds | PASS | `twse_twtcau`: 7 yearly imports, 2020–2025 succeeded whole; 2026 split at the ambiguous row into `2026-01-01→2026-03-31` (quarantined alone) and `2026-04-01→2026-09-11` (succeeded, 2 events). 10 events stored total. `tpex_etfsplitrslt`/`tpex_etfrvsrslt`: one whole-range import each, succeeded, 0 events (real, not "not yet fetched"). |
| Zero duplicate `(feed, code, locator date)` over each feed's stored history | PASS | `SELECT security_id, source_event_key ... GROUP BY ... HAVING count(*)>1` returns 0 rows for all three sources. `TWTCAU:20251022` is shared by two different ETFs (00673R, 00706L) and is not a duplicate under the real identity `(security_id, source, source_event_key)`. |
| A row whose direction/type cannot be determined quarantines with its reason instead of being guessed | PASS | 00631L (115/03/31) publishes an empty `分割(反分割)` cell; both ranges that included it (`2020-01-01→2026-09-11` and `2026-01-01→2026-03-31`) quarantined with `reason_code = unknown_event_type`, raw artifacts retained, zero business rows written from either. |

No legacy reconciliation criterion: legacy `stock_db` never collected ETF
splits or reverse splits (CLAUDE.md §78 applies only where a legacy baseline
exists).

## Verification

```text
$ .venv/bin/python3 -m pytest tests/ -q
572 passed, 3 skipped, 1 warning in 104.03s
$ .venv/bin/python3 -m alembic upgrade head && alembic downgrade -1 && alembic upgrade head
# clean round-trip, no drift (test_alembic_metadata_has_no_drift passes)
$ .venv/bin/python3 -m ruff check <changed files>
# no new findings; pre-existing repo-wide findings unchanged
```

New tests: `tests/unit/test_step19e_etf_split_adapters.py` (11 tests) using
real fixtures captured live 2026-09-17: `twse_twtcau_2020_2026.json` (11
rows, including the real blank-direction anomaly),
`tpex_etfsplitrslt_2020_2026_empty.json`,
`tpex_etfrvsrslt_2020_2026_empty.json` (both real, `totalCount: 0`).

## Known limitations / deferred work

- TPEx `etfSplitRslt`/`etfRvsRslt` adapters cannot parse a real row yet —
  by design (ADR-0023 §3). The day either feed lists one, its `詳細資料`
  schema must be verified against that real response before `_row()` can
  be implemented; guessing `pvChgRslt`'s schema was rejected.
- `TWTCAU`'s `old_shares`/`new_shares` stay NULL permanently for this
  source; Step 25's adjustment-factor work must derive 0050-style ETF
  split factors from `close_before`/`official_reference_price` the same
  way it already does for cash dividends and ex-rights (CLAUDE.md §80),
  not from a share count this feed never publishes.
- **Any future `TWTCAU` request whose range spans 2026-03-31 fails
  outright**, not intermittently — 00631L's blank direction cell is a
  permanent feature of that date's response, not a transient glitch. This
  PR's backfill worked around it by hand-splitting the range at that date
  (ADR-0023 §4); a future automated job that does not know to do the same
  — a `CorporateActionBackfill`-style year-chunked run, a correction-check
  re-fetch, or Step 27's forward capture, whichever first requests a range
  crossing that date — will quarantine the whole range and silently never
  store 00674R (2026-04-22) or 00685L (2026-07-07) until a human notices
  the quarantine and re-splits it by hand. Flagged here (code review of
  #27) so the next PR that automates a `TWTCAU` range job reads this
  first, rather than rediscovering it live.
