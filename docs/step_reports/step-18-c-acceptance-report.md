# Step 18-c Acceptance Report

Status: IN REVIEW (#29)

Scope: Official valuation. This step adds the TWSE `BWIBBU_d` and TPEx
`afterTrading/peQryDate` adapters, their importer, the source policy and
coverage declarations, the CLI, and the 2020-01-02 → 2026-09-11 backfill for
both markets.

Schema impact: none. Migration `b3d6f0a2c8e5` only adds rows: the
`official_valuation` catalog entry, two `dataset_sources` rows, their
release-rule mappings, and two expected-coverage declarations.
PIT impact: nothing new. Both sources follow `exchange_daily_settled@1`.
Size: `src/` changed by +764/−1 lines. The step also commits one script and
eight fixtures.

## Source decisions made during this step

Each decision is recorded in ROADMAP 18-c and audit §4.6.

1. **TPEx is read as JSON, not CSV.** ROADMAP said the new TPEx site had no
   JSON for this table and that the CSV states no date. Both statements were
   wrong:
   - The legacy `pera.php` page now redirects (302) to the new page. That page
     loads its table from `www/zh-tw/afterTrading/peQryDate`.
   - The JSON matches the CSV row for row: 772 rows on 2020-01-02 and 885 on
     2026-09-11.
   - The JSON states its own date twice, at the top level and on the table, and
     carries a row count and the formula notes.
   - The CSV is MS950, not big5, and does state `資料日期`.

   The owner chose the JSON. Its source code is `tpex_pe_qry_date`.
2. **Not-computed markers store NULL.** These are TWSE `-`, TPEx `N/A`, and,
   by owner decision, TPEx `"null"` and a ratio of exactly zero on either
   exchange. The zero and `"null"` values have no official explanation; both
   are TPEx's rendering of a security's first listed day. A negative ratio,
   yield or dividend quarantines its own row, and the other rows of that date
   still import.
3. **Report periods and dividend years are normalised.** `財報年/季` is stored
   as `YYYYQn`, parsed from each exchange's own format (`115/2` and `115Q2`).
   A value in the other exchange's format fails the file. `股利年度` is stored
   as the ROC year + 1911.

While this step was open, the same redirect check found GET JSON for TPEx
`3itrade_hedge`, `3itrdsum`, `margin_bal` and `margin_sbl`, and for TPEx's own
foreign-holding table (`insti/qfii`). It also confirmed that the new MOPS site
offers no usable JSON for monthly revenue, iXBRL or dividend declarations.
These findings are recorded in audit §4.3–4.5, §4.7, §4.8 and §4.13, and in
ROADMAP Steps 20 and 21. Those steps decide what to do with them.

## Baseline

Legacy `stock_db.pe_ratio`, 2020-01-02 → 2026-09-11:

| Market | Rows | Dates | Non-NULL PE |
| --- | ---: | ---: | ---: |
| `sii` | 1,611,824 | 1,627 | 1,302,368 |
| `otc` | 1,324,687 | 1,627 | 962,372 |

## The run

```text
                     versions   securities  dates   raw artifacts
twse_bwibbu_d       1,612,498        1,121  1,627           1,654
tpex_pe_qry_date    1,324,687          944  1,627           1,943
evidence  2,937,185  release_rule exchange_daily_settled@1, unknown 0
data/raw  783 MB after this step
```

Coverage from the Step 16 validator for both markets: `expected 1627,
observed 1627, missing [], unexpected [], is_complete true`.

The backfill ran twice, and the manifests record both passes:

1. **v1** covered 2020-01-02 → 2025-03-27 for TPEx and 2020-01-02 → 2024-06-14
   for TWSE. It hit TPEx's `"null"` ratios and quarantined 205 whole dates as
   `unrecognised_value`. It also recorded one TWSE fetch timeout (2024-05-07)
   and one row quarantine (6720 on 2024-12-04). The owner then decided that
   those values mean not computed, and v1 was stopped.
2. **v2** (`twse-bwibbu-d:v2`, `tpex-pe-qry-date:v2`) ran four ranges:
   - TWSE 2024-05-07 → 2026-09-11: 574 dates imported, 0 failed.
   - TPEx 2021-07-26 → 2022-11-02: 315 dates imported, 0 failed. This added
     162,170 versions; 87,701 rows deduplicated against v1.
   - TPEx 2024-12-04: 1 new version, 6720, stored with NULL ratios.
   - TPEx 2025-03-28 → 2026-09-11: 358 dates imported, 0 failed.

Restarting under a new adapter version needs new import ids. The lifecycle
refuses to reuse an import id with a changed configuration fingerprint. The
old failed manifests and the superseded row quarantine stay in place as
history.

v1 and v2 produce identical output on every date that v1 imported
successfully. The only change between them is how `"null"` and zero are
handled. v1 quarantined every such value, and every date where one occurred
was re-imported under v2.

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| Legacy `pe_ratio` reconciles for both markets | PASS | **TPEx:** 1,324,687 rows compared, 1 difference: 6720 on 2024-12-04, where legacy has `0.0` and we store NULL by decision. **TWSE:** 1,597,366 rows compared, 0 differences. The remaining 14,458 legacy rows fall on 15 dates where legacy saved another date's file. Those dates are classified as a whole, below. |
| Every difference is classified | PASS | The reconciliation script's classes are `legacy_captured_another_date` (with the official date the legacy file reproduces), `pe_ratio:*`, `legacy_only` and `legacy_only:we_quarantined_*`. No unclassified row remains, and the script exits 0. |
| `dividend_yield`, `pb_ratio`, `dividend_year`, `report_period` are reported as new data | PASS | Non-NULL rows, TWSE / TPEx: PB 1,612,236 / 1,324,129; yield 1,612,498 / 1,324,687; dividend year 1,612,498 / 1,324,687; report period 1,612,498 / 355,131. |
| The 5-column TWSE file of 2025-06-24 is parsed by an explicit variant or quarantined | PASS | TWSE now serves 2025-06-24 with the 8-field header. The 5-field file exists only in the legacy archive, and it holds another date's data. The adapter parses the real 5-field header explicitly (`bwibbu_5`, tested against TWSE's 2017-01-03 response). Every other header fails the file as `schema_mismatch`. All 1,627 TWSE dates in the window (1,654 imports) were `bwibbu_8`. |
| TPEx `財報年/季` before 2025-01-02 is NULL, not an error; `115/2` and `115Q2` are each parsed explicitly | PASS | `pe_qry_date_7` covers 1,216 dates up to 2024-12-31, and `pe_qry_date_8` covers 411 dates from 2025-01-02. The first stored TPEx report period is on 2025-01-02. Tests cover each exchange rejecting the other's format. |
| `dividend_per_share` is populated for TPEx only | PASS | TPEx 1,324,687 rows, TWSE 0. |
| Coverage is complete | PASS | Both markets: 1,627 of 1,627 dates. |

## What the reconciliation had to learn about legacy

On 15 TWSE dates, legacy saved a file for a different date, and nothing in its
pipeline noticed. The legacy CSV carries no date, and its parser checked
neither the date nor the header. The script first scores how well each legacy
date agrees with our file for the same date. When agreement is below 50%, it
searches the whole window for the official date the legacy file reproduces:

| Legacy date | Agreement with same-date official file | Legacy file is |
| --- | ---: | --- |
| 2020-12-07 | 24.2% | 2020-12-18 (100%) |
| 2022-01-24 | 15.9% | 2022-01-18 (100%) |
| 2023-05-15 | 12.0% | 2024-06-18 (100%) |
| 2024-09-20 | 22.7% | 2024-09-18 (100%) |
| 2024-11-11 | 19.4% | 2024-12-18 (100%) |
| 2025-02-19, 2025-02-20 | 29.9%, 26.3% | 2025-02-18 (100%) |
| 2025-06-04, 2025-06-05 | 22.4%, 22.2% | 2025-06-18 (100%) |
| 2025-07-01 | 22.2% | 2025-07-18 (100%) |
| 2022-02-17, 2023-03-22, 2024-01-08, 2025-08-20 | 7–9% | one byte-identical file of late-2017 data (股利年度 105, 財報年/季 106/3) |
| 2025-06-24 | 5.8% | an older 5-field file, not in the window |

The same 15 dates account for all 970 stored rows that legacy lacks. No other
date has a legacy-only row or a source-only row.

This is the same kind of legacy defect that Step 17-c and Step 18-b found as
short captures. Here the capture has the right size but the wrong date.

## Defects the live run caught

**`"null"` was an unknown token, so it failed whole dates.** The adapter
treated any unrecognised cell as a format change. That is the right default,
and it made the problem visible: 205 TPEx dates quarantined, with every raw
artifact kept. It was the wrong granularity for a value the owner then ruled
on, because 379 cells had cost 162,170 rows. The fix changes the markers, not
the fail-closed default. A new unknown token still fails its file.

**The first stop was blunt.** `pkill -f` matched the shell that issued it, so
the command reported exit 144. The backfill processes did stop. No manifest was
left `running`: the interrupted dates had not opened a write transaction, and
v2 imported them under new ids.

## Verification

Database migrated from zero:

```text
632 passed, 3 skipped, 1 warning
```

The baseline on `main` was 595 collected (592 passed, 3 skipped). The 40 new
tests (29 unit, 11 integration) are the difference.

How each test was seen to fail first:

- Unit tests: 25 against a stub adapter that raised `NotImplementedError`. The
  26th, the source-code constants, failed at import before the stub existed.
- Integration tests: all 10 failed before the importer existed. After
  implementation, 4 failed with the migration removed, and the quarantine test
  failed with the quarantine write removed.
- The zero/null decision tests failed against the code as it stood before each
  change.

`alembic upgrade head` → `alembic check` reports no new operations. `downgrade
9f3d7c2e5a41` removes exactly the rows this migration declared, and a second
upgrade restores them. With a quarantined run present, the downgrade guard
raises `P0001` before any mutation. `ruff check` reports nothing new relative
to `main`.

Reproduce the reconciliation:

```bash
python scripts/reconcile_official_valuation.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \
    --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
```

## Scope exclusions confirmed

- Computed valuation is Step 26's work. This step stores only exchange-published
  values.
- `收盤價` from `BWIBBU_d` and the TPEx company names are not stored. Daily
  prices and security metadata own them.
- Steps 20 and 21 are not changed beyond recording the TPEx JSON findings.
