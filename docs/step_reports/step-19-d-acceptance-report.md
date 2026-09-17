# Step 19-d Acceptance Report

Status: IN REVIEW (#28)

Scope: real 2020-01-01 → 2026-09-11 backfill for all six corporate-action
result feeds, and the legacy `dividend` reconciliation report ADR-0022 §6–8
built the framework for.

Schema impact: none new (uses `corporate_action_events`/`_versions`/
`corporate_action_retractions` from Step 19-c). Code impact:
`CorporateActionBackfill` (year-chunked backfill, ADR-0022 §6),
`RetryingFetcher` (transient-HTTP-status retry, ADR-0022 §7),
`scripts/reconcile_corporate_actions.py`, and a real correctness fix to the
shared `RawFirstImporter._capture_dependencies` contract (ADR-0022 §8: a
row whose detail cannot resolve quarantines on its own, not the whole
range).

## What the real backfill found (ADR-0022 §7–8)

Two genuine, live discoveries changed the design after it was written:

1. **A captured-but-unparseable dependency was replayed forever.** TWSE
   served an HTML `網站維護中` maintenance page for one `TWT49UDetail`
   request; its raw bytes got checkpointed like any real response, and
   every retry re-parsed the same garbage. Fixed in
   `RawFirstImporter._capture_and_parse`: a `SourceDataError` whose content
   was never real JSON (`invalid_json`) now discards its own checkpoint;
   a genuine `no_data_for_date` (a stable domain fact) keeps it.
2. **Range-level quarantine cost real, retrievable data.** TWT49U's
   2887-series preferred shares (Taishin Financial's `2887F`/`2887G`/`2887H`/
   `2887I`/`2887Z1`, different codes different years) have never had a
   working detail page — confirmed permanent by repeated live requests, not
   a transient blip. TWT49U ex-dividend dates commonly list dozens of
   securities together, so failing the whole day over one such row was
   silently dropping every other real, legacy-matched row on it. Fixed:
   `_capture_dependencies` now quarantines one row at a time;
   `_write_business` still registers every row's event identity (so an
   unresolved row stays a retraction candidate) but writes a version only
   for rows that resolved.

`scripts/reconcile_corporate_actions.py`'s own `duplicate_check` had the
same identity bug the codebase had just fixed for TWTCAU in Step 19-e: it
grouped by `source_event_key` alone, which double-counted the ordinary case
of two different securities sharing one locator date. Fixed to group by
`(security_id, source_event_key)`, the real identity (CLAUDE.md §51.5).

## Acceptance evidence

Real backfill against a fresh-ish `stockdc_step19d` database, 2026-09-16 →
2026-09-17, `--purpose first_capture`.

| Criterion | Result | Evidence |
| --- | --- | --- |
| Zero duplicate `(feed, code, locator date)` over each feed's stored history | PASS | `(security_id, source_event_key)` grouping returns 0 duplicate groups for all six sources (`twse_twt49u` 7,784 events, `twse_twtauu` 149, `twse_twtb8u` 10, `tpex_exdailyq` 7,318, `tpex_revivt` 107, `tpex_pvchgrslt` 13). |
| Legacy `dividend` reconciles on date, close before, reference price, rights+dividend value, and type, with every difference classified | PASS | `reconcile_corporate_actions.py`: `differences: {}`. All 6,182 legacy rows (2020-01-02 → 2026-09-11) matched a stored `twse_twt49u` row; 0 `legacy_only`, 0 field-level disagreements. |
| Quarantined events, if any, are listed with their reason | PASS | 14 distinct locators, all `no_data_for_date`, all Taishin 2887-series preferred/warrant sub-classes: `2887F` in 2020/2021/2022/2023/2024/2025/2026 (every year), `2887Z1` in 2023/2024/2025/2026, `2887G`/`2887H`/`2887I` newly in 2026. Every other security on each of those dates completed normally. |

No legacy baseline exists for `twse_twtauu`/`twse_twtb8u`/`tpex_exdailyq`/
`tpex_revivt`/`tpex_pvchgrslt` (Step 19-b: legacy `dividend` holds only
TWT49U-shaped rows), so only the duplicate-identity criterion applies to
them; all five have 0 rejected/quarantined and 0 retracted.

## Verification

```text
$ .venv/bin/python3 -m pytest tests/ -q
587 passed, 3 skipped, 1 warning in ~107s
$ .venv/bin/python3 -m ruff check <changed files>
# no new findings
$ .venv/bin/python3 scripts/reconcile_corporate_actions.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_step19d \
    --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db \
    --start 2020-01-02 --end 2026-09-11
# exit 0; differences: {}
```

New regressions in `tests/integration/test_step19c_corporate_action_ingestion.py`:
`test_a_failing_detail_quarantines_only_its_own_row`,
`test_a_garbled_detail_page_quarantines_its_row_and_clears_its_checkpoint`,
`test_a_genuine_no_data_detail_quarantines_its_row_and_keeps_its_checkpoint`
(replacing the old range-level-quarantine fixture, which asserted the
now-corrected behavior).

## Known limitations / deferred work

- TWT49U's 2887-series gap is permanent and will keep appearing every year
  a member of that share family has an ex-dividend date; no further action
  is needed per occurrence — it quarantines on its own automatically now.
- The stray `2887G`/`2887H`/`2887I` discoveries in 2026 (not seen in
  earlier years) suggest Taishin has issued more sub-classes since; nothing
  to do until one appears with a genuine cash dividend and a working detail
  page, which would be a new, different observation.
- One stale `running` manifest remains from a process killed earlier this
  session (2023's original whole-year attempt, before this fix existed);
  harmless — it references no data any current read path uses — and left
  as an accurate record rather than mutated after the fact.
