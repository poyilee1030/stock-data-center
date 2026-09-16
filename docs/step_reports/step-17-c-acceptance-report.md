# Step 17-c Acceptance Report

Status: IN REVIEW

Scope: Whole-market daily-price history backfill and reconciliation

Schema impact: none. Migration impact: none.
PIT impact: none new — the imported history resolves by `exchange_daily_settled@1`,
the rule Step 15-c applied and 17-b mapped to these sources.
`src/` changed by +408/−14 lines, plus one committed script.

## Baseline

Step 17-b's import path, merged as #20, proved on five market-dates. This step
runs the window: 1,627 trading dates per market, 2020-01-02 → 2026-09-11.

Before the run, `daily_price` held nothing for either whole-market source. The
Step 16 calendar measured the window at **1,627 trading days**, and the legacy
baseline measured at the start of 17-a was **1,631,598 `sii` rows / 1,299,781
`otc` rows**. Both numbers are matched exactly below, which is what makes the
comparison meaningful rather than approximate.

## The run

Both markets, throttled 1.2 s, in parallel (separate hosts, so each host's
interval is preserved):

```text
                  dates   imported  resumed  failed        rows
twse_mi_index     1,627      1,627        0       0   1,969,048
tpex_otc_quotes   1,627      1,627        0       0   1,500,983
                                                      ---------
                                                      3,470,031
raw artifacts 3,338   observations 3,343   evidence 3,470,031 release_rule
data/raw 547 MB       non-trading days not requested: 818 per market
```

TWSE needed a second pass; see *What the run found* below.

## Acceptance evidence

| Criterion | Result | Evidence |
| --- | --- | --- |
| Every trading date is imported or explicitly reported as a gap | PASS | The Step 16 coverage validator, not a second implementation: `expected_dates 1627, observed_dates 1627, missing_dates [], unexpected_dates [], non_trading_days 818, is_complete true` for **both** markets. |
| Row counts and values reconcile against legacy | PASS | TWSE 1,631,598 legacy rows compared, TPEx 1,299,781 — both exactly the baseline measured before any code was written. **Zero `legacy_only` rows in either market across the whole window**: the feed is a strict superset on all 1,627 dates. TPEx: **zero differences**. TWSE: 1,062 rows differ, fully explained below. |
| Every difference is classified | PASS | Five classes, each counted separately so "expected" cannot hide "unexplained": `legacy_only` (0), `legacy_snapshot_differs` (1,062, one date, explained), source-only untraded (17,480 TWSE / 31,201 TPEx), source-only with no published volume (0 / 0), source-only outside the legacy universe (319,970 / 170,001). The script exits 0, which is the same verdict as this table. |
| A resumed run continues rather than restarting | PASS | The TWSE second pass: `resumed 1619, imported 8, failed 0` in **41 s**. The 1,619 finished dates were not re-fetched. |
| Reruns stay idempotent | PASS | `dedup_count 0` across 3,254 manifests, because no date was imported twice — and the 1,619 resumed dates returned their stored results without touching the source. The per-date idempotency contract itself is 17-b's regression set. |
| A report of priced securities with no metadata row | PASS | 366 TWSE and 217 TPEx, broken down below. |

## What the run found

**TWSE served a maintenance page for 18 seconds.** Six dates quarantined as
`invalid_json`. Raw-first meant the bytes were on disk to look at, and all six
were the *same* 611-byte artifact — content-addressed storage deduplicated them
into one row:

```html
<title>網站維護中 - 臺灣證券交易所</title>
```

Fetched between 03:49:30 and 03:49:48 UTC, with successful dates in between, so
TWSE was flapping rather than down. Two further dates hit `ReadTimeout`. All
eight were retried by re-running the same command with the same import id, which
is the resumability criterion demonstrated on real failures rather than
simulated ones. The six quarantine rows remain: the audit trail is append-only,
and the manifests for those dates now read `succeeded`.

**All 1,062 TWSE value differences are on one date, and the archive explains
them.** Every difference is in volume, trade value and trade count; **no OHLC
value differs anywhere in 1.63 M rows**. All 1,062 fall on 2026-03-27, and on
that date every legacy volume is an exact multiple of 1,000 while neighbouring
dates are not:

```text
0051 on 2026-03-27   ours 41,556 shares / 354 trades
                     legacy 31,000 shares / 22 trades
legacy archive file written  2026-03-27 14:10:01 +0800
```

The regular session closes at 13:30 and the odd-lot session settles later, so at
14:10 TWSE was still serving round-lot-only statistics. The legacy scraper skips
any date whose file already exists (`fetch_daily_sii.py`, audit §3), so it never
saw the final figures. Ours are ≥ legacy on all 1,076 compared rows of that date,
which is the direction that hypothesis predicts.

This is a documented, PIT-correct difference, and it is why the classification is
called `legacy_snapshot_differs` rather than naming a field as wrong: nothing
preserved the official bytes as they stood at 14:10, so which snapshot is stale
is established from the capture time, not asserted from the numbers.

**Priced securities with no metadata row**, the report ROADMAP asks for:

| | TWSE | TPEx |
| --- | ---: | ---: |
| ETFs and beneficiary certificates | 293 | 173 |
| Preferred shares | 33 | 3 |
| TDRs / foreign listings | 4 | 0 |
| Ordinary shares, all of them delisted mid-window | 36 | 41 |
| **total** | **366** | **217** |

The first three groups are permanent: the company snapshots Step 10 imports
describe companies, and these are not companies. The fourth is different and
worth stating plainly — every ordinary share in the list stopped being priced
before 2026-09-11, so these are delisted issuers that the *current* snapshot no
longer lists. This run imported only the current snapshots; Step 11's
listing/delisting lifecycle history writes to the same table and would describe
them. The number is therefore a property of what this database holds, not a
permanent gap in the contract.

## Design decisions

**The calendar decides which dates to ask for.** 818 non-trading days per market
were never requested, so a closure can never arrive as a quarantined date and
never looks like a gap. A range the calendar has not imported raises before the
first request rather than after the last, because an unimported month and a month
of closures are indistinguishable in the data.

**Each date carries its own import id**, derived from the run's with `uuid5`, so
a run that dies on date 900 resumes at date 900, and two runs never collide on
one checkpoint.

**One bad date is reported, not fatal.** It is named in the run report with its
reason code, the exit status is non-zero, and the remaining dates still import.
Ending the run on the first failure would have thrown away 1,618 good dates to
report eight bad ones.

**The gap report is the Step 16 coverage validator.** It already answers which
expected dates a dataset holds, which it is missing, and which closures are not
its gaps. A second implementation would have been a second thing to keep true.

## Two defects the live run found that the tests could not

**The runner asked for a `TPEx` calendar.** No official TPEx calendar exists — by
Step 16's measured decision, TPEx datasets declare the TWSE one through
`dataset_expected_coverage.calendar_market`. The first TPEx backfill died on its
first call. Every one of the seven tests written before it was a TWSE test, where
`market` and `calendar_market` are the same string, so the tests had grown the
implementation's blind spot exactly. The runner now reads the declaration, and a
TPEx regression covers the path.

**The throttle slept between resumed dates.** Politeness is owed to the source,
not to the checkpoint table. Retrying eight failed dates would have slept once per
already-finished date: 1,627 × 1.2 s of waiting to make eight requests. The
throttle now follows a real request, which is what made the retry take 41 s.

Both were found by running the thing against reality, which is the argument for
the backfill being its own step rather than a footnote to 17-b.

## What the legacy archive turned out to be

Reading `my_stock_project/scraper/daily/` changed how this report reads its own
baseline, and audit §3 is updated with it:

- The scraper requests `response=csv`, so the JSON representation's `hints`
  (`單位：元、股`), `notes`, `date` and per-table `fields` never existed in the
  archive. The official CSV is first-hand — the archive is not, because the saved
  file is a rewrite of it: big5 decoded with `errors="ignore"`, `="…"` stripped,
  single-cell rows dropped (which is where the report date went), requoted as
  UTF-8-BOM, and skipped entirely below a size floor.
- 92% of the window (1,492 of 1,629 files) was written in one campaign in January
  and February 2026, and `skip if exists` means nothing was refreshed afterwards.
  So the archive carries no first-seen evidence for daily prices, which is why
  this run declares `gap_fill` and claims no capture bound.

Three checks found no damage to the values themselves: 1,627 archive dates
against 1,627 official ones, zero corrupted names in 2,133 code/name pairs, and
the row-level comparison above. The archive is a sound reconciliation baseline.
It is not an official artifact, and the difference is what this section records.

## Source capability recorded

Audit §4.1 now records how far back each endpoint serves, probed at the
boundary: TWSE **2004-02-11** (the endpoint says so itself —
`查詢日期小於93年2月11日，請重新查詢!`) and TPEx **2007-07-02** (it says nothing;
2007-06-29, the previous trading day, simply returns zero rows).

This is recorded because the window starting on 2020-01-02 is a ROADMAP scope
decision, and nothing until now said so — "the endpoints still serve 2020
onward" read like a limit. It also documents what extending the window would
actually take: the Step 16 calendar covers only 2020-01 onward and the runner
fails closed outside it, and the two markets would need different
`window_start` values, since TWSE reaches three years further back than TPEx.

TPEx's out-of-range answer is `stat` `ok` with zero rows — identical to a
closure. That is the reason Step 17-a's reason code is `no_data_for_date` and
not `market_closed`, and it is now written down next to the evidence.

## Code-review findings

Six findings, all verified against the code and the imported data before
anything changed; none was a false positive. Four were latent — the conditions
that trigger them do not occur in this data — and latent is not the same as
harmless, since each would fire on the first correction or the first extended
window.

| # | Finding | Verified by | Disposition |
| --- | --- | --- | --- |
| 1 | The CLI mints a fresh base id per invocation, so a `--through` run restarted without `--import-id` re-fetches everything it already finished | `cli.py:160`. The backfill in this report only resumed because the id was passed by hand. | **Fixed.** The base id is derived from `(source, start, end)`, so resuming is the default; `--import-id` still overrides, and the run prints the id it used. The documented promise no longer depends on an undocumented flag. |
| 2 | The reconciliation collapses several revisions of a security-date in whatever order PostgreSQL returned | The revision constraint includes the business hash, so multiple revisions are legal. Latent: this data has 0 multi-revision keys. | Fixed. `DISTINCT ON ... ORDER BY ingested_at DESC` — the current state, chosen deterministically, as §78 requires. |
| 3 | `import_counts` takes a window and filters only on source, so unscoped totals print under a window header | Read. | Fixed. Scoped by the trade date each manifest recorded. |
| 4 | The exit code gates on `legacy_snapshot_differs`, which the script itself says is reported and not judged — so it returns 1 on the run this report marks PASS | Ran it: exit 1 against accepted differences. | Fixed. It gates on `legacy_only` and null disagreements, the classes that are defects. The script now exits **0**, agreeing with the acceptance table. |
| 5 | `volume == 0` is false for NULL, so a source-only row with no published volume was counted as outside the legacy universe | Latent: 0 NULL volumes in this data. | Fixed, and with a third bucket rather than the other one: an unknown volume is not evidence of anything, and putting it in either explanation would hide an unexplained row inside one. |
| 6 | The runner passes the caller's range straight to the calendar, so a range before `window_start` imports dates the validator reports as `unexpected` for ever | Confirmed `expected_periods` clamps and the runner does not. Latent: the backfill starts exactly at `window_start`. | Fixed. Refused before the first fetch, and before the calendar check, because the declaration is the more specific authority. |

Finding 1 is the one that matters: the resume this step is built on worked in
this run only because the id was passed by hand, while the documentation
promised it without the flag. The other five are the kind that stay invisible
until the day they are not.

## Verification

Database migrated from zero:

```text
432 passed, 3 skipped, 1 warning
```

Baseline before this step: 421 (Step 17-b). The 11 integration tests added here
are the difference, and each was seen to fail first — the two written after the
live run against the code as it then stood, and the two from the review against
the code as it was pushed.

`ruff check` reports nothing new against `main` for every file touched.

## Scope exclusions confirmed

- No adjusted prices, and **no analysis-readiness claim**: CLAUDE.md §51.4 gates
  that on corporate-action history, which is Step 19. Raw OHLC is stored, correct
  and reconciled; that is all this step claims.
- The Step 9 per-security pilots stay out of production and are unchanged.
- The index sections of the TWSE artifacts are untouched. Step 18 reuses these
  same 1,627 raw artifacts rather than re-fetching them.
- `data/raw` holds 547 MB of official response bytes on this machine only; it is
  gitignored, and a rebuild elsewhere is a re-run of the same CLI.
