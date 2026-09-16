# Real-source ingestion

Phase 9 real imports preserve source bytes before normalization and use the
existing trusted domain writers. PostgreSQL holds the import manifest,
checkpoint, quarantine reason, raw provenance, business revision, publication
evidence, and actual ingestion time. Raw bytes are stored under `data/raw/` by
SHA-256 through `LocalRawArtifactStore`.

## Daily-market pilot adapters

The first milestone supports official per-security monthly history:

- [TWSE `exchangeReport/STOCK_DAY`](https://www.twse.com.tw/zh/trading/historical/stock-day.html),
  whose traded volume is shares and traded value is TWD;
- [TPEx `afterTrading/tradingStock`](https://www.tpex.org.tw/zh-tw/mainboard/trading/info/stock-pricing.html),
  whose traded volume is 1,000-share lots and traded value is thousand TWD.

TPEx values are multiplied by 1,000 before a `DailyPriceObservation` is built.
Canonical storage and its business hash therefore contain shares and TWD only.
The TWSE `X` price-change marker is retained as `price_direction = "X"`; its
numeric suffix is parsed separately instead of silently treating the marker as
an ordinary positive/negative change.

The endpoints do not establish the original release time of each historical
row. Imports record `published_at = NULL`; they do not use the trade date,
fetch time, or current time as invented publication evidence.

## Whole-market daily-price adapters

Step 17-a adds one request per (market, trade date), which is what production
will use. They are separate sources from the pilots above, not a faster route to
the same rows: they publish the disclosed bid/ask level the pilots do not, and
one logical key must not alternate revisions between two field sets
(CLAUDE.md §30). Step 17-b gives them their importer, their CLI and their source
policy; this section describes what they parse.

| Source code | Endpoint | Grain |
| --- | --- | --- |
| `twse_mi_index` | `rwd/zh/afterTrading/MI_INDEX?type=ALLBUT0999` | stock section of one trade date |
| `tpex_otc_quotes` | `www/zh-tw/afterTrading/otc?type=EW` | one trade date |

Both feeds publish shares and whole TWD for traded quantity and value. The
disclosed bid/ask level is where they differ, and each feed declares its own
unit: TWSE states `單位：元、股` for the whole table, so that column is already
shares, while TPEx labels the column `最後買量(千股)` / `(張數)` and is
multiplied by 1,000. The TWSE adapter re-checks `hints` on every parse and
fails closed on a restatement; the TPEx response also carries `flagField`
naming its own label, checked against the header variant rather than trusted on
its own.

TPEx has three header variants (audit §4.1). Each is mapped explicitly by its
exact field tuple; an unrecognised header raises `schema_mismatch` so the
lifecycle quarantines the resource with its raw artifact retained, rather than
being read positionally.

A date the source has nothing for raises `no_data_for_date` in both markets —
TWSE answers an apology with no tables, TPEx an empty table — so a caller
walking a date range can tell it from a parse failure. It is deliberately not
called `market_closed`: only the Step 16 calendar can say a date was a
closure. The TWSE stock section is likewise found by its own
header, not by its position among the ten tables — the index sections of the
same artifact belong to Step 18.

The TWSE `X` marker and the TPEx `除息` / `除權` / `除權息` markers are the same
不比價 statement in two spellings, parsed as `price_direction = "X"` with no
price change: TWSE fills the magnitude cell with `0.00` on those rows, and TPEx
prints the word where the number would be.

## Current security-metadata adapters

The second milestone supports official whole-market company snapshots:

- [TWSE `opendata/t187ap03_L`](https://openapi.twse.com.tw/v1/opendata/t187ap03_L);
- [TPEx `mopsfin_t187ap03_O`](https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O).

Both resources contain an official report date and listing date. The report
date becomes `effective_from` for the current name, source industry code, and
market state. `listed_on` retains the separately reported listing date. The
import does not pretend that every current field existed from listing day.

A later equal snapshot reuses the latest equal business version while adding
new raw/evidence observation lineage. A changed state begins a new version on
the later report date. A security missing from a new snapshot is not silently
marked delisted. The resources do not prove complete historical name, industry,
delisting, or market-transfer events; those remain a separate adapter and
reconciliation milestone.

The endpoints also lack an exact original publication instant. Their versions
therefore receive `unknown` evidence with `published_at = NULL` and remain
Market-PIT invisible unless separate reliable publication evidence is added.

## Historical security lifecycle adapters

The third milestone imports authoritative venue events:

- [TWSE listing history](https://www.twse.com.tw/rwd/zh/company/newlisting?response=json);
- [TWSE delisting history](https://www.twse.com.tw/rwd/zh/company/suspendListing?response=json);
- [TPEx yearly listing history](https://www.tpex.org.tw/www/zh-tw/company/latest);
- [TPEx yearly delisting history](https://www.tpex.org.tw/www/zh-tw/company/deListed).

TWSE resources expose their whole available table. TPEx resources require a
Gregorian year and may validly contain zero events. An event's official venue
date becomes effective metadata; it does not become `published_at`. TWSE and
TPEx remain independent source histories. A TWSE `櫃轉市` note is reconciled
only with a same-code, same-date TPEx exit and does not synthesize a merged
business version. Import manifests label this relation provisional. Run the
separate final reconciliation after the relevant source histories are present;
it recomputes the result from canonical history and is independent of import
order.

## Trading-calendar adapter

The fourth milestone imports the market's actual open days:

- [TWSE `afterTrading/FMTQIK`](https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK),
  one request per calendar month.

The report lists only the days the market actually traded, so a closure is an
absence rather than a flag. The importer stores one version per month with the
day list as its business content, and bounds a still-running month with
`coverage_through` so its remaining days stay unknown rather than closed. The
endpoint publishes no release instant, so months are stored with `unknown`
evidence and `published_at = NULL`. See [`trading_calendar.md`](trading_calendar.md).

## Running one resource

Apply migrations, set `DATABASE_URL`, and use an explicit UUID when a job may
need to resume:

```bash
python -m stock_data_center.ingestion.cli daily-market \
  --source twse --security-code 2330 --month 2025-09 \
  --import-id 11111111-1111-4111-8111-111111111111

python -m stock_data_center.ingestion.cli daily-market \
  --source tpex --security-code 6488 --month 2025-09 \
  --import-id 22222222-2222-4222-8222-222222222222

python -m stock_data_center.ingestion.cli security-metadata \
  --source twse --expected-report-date 2026-09-11 \
  --import-id 33333333-3333-4333-8333-333333333333

python -m stock_data_center.ingestion.cli security-metadata \
  --source tpex --expected-report-date 2026-09-12 \
  --import-id 44444444-4444-4444-8444-444444444444

python -m stock_data_center.ingestion.cli security-history \
  --source tpex --event listing --year 2025 \
  --import-id 55555555-5555-4555-8555-555555555555

python -m stock_data_center.ingestion.cli security-history \
  --source tpex --event delisting --year 2025 \
  --import-id 66666666-6666-4666-8666-666666666666

python -m stock_data_center.ingestion.cli security-history \
  --source twse --event listing \
  --import-id 77777777-7777-4777-8777-777777777777

python -m stock_data_center.ingestion.cli security-history \
  --source twse --event delisting \
  --import-id 88888888-8888-4888-8888-888888888888

python -m stock_data_center.ingestion.cli security-transfer-reconciliation

python -m stock_data_center.ingestion.cli trading-calendar \
  --month 2024-07 \
  --import-id 99999999-9999-4999-8999-999999999999

python -m stock_data_center.ingestion.cli trading-calendar \
  --month 2020-01 --through 2026-09 --min-interval-seconds 1.5
```

A history run derives one import id per month from the run id, so a run that
fails midway resumes with the remaining months instead of restarting.

The command prints the persisted manifest/reconciliation without connection
credentials. Reusing the same successful `import_id` and identical scope reads
the checkpoint and does not fetch again. If execution stopped immediately after
raw capture, the same command reads and hash-verifies those retained bytes,
uses the original ingest run, and continues without contacting the source. A
different configuration cannot be silently attached to an existing import ID.
The local raw root is resolved to an absolute location when the store starts;
stored locators therefore do not change meaning with the process working
directory. The raw backend/root identity is part of the import fingerprint.

Workers serialize the complete lifecycle of one `import_id + resource_key`
with a PostgreSQL advisory lock. Successful and quarantine transitions also
compare the checkpoint's run/artifact ownership, and a successful checkpoint
is accepted only when its referenced ingest run is successful.

## Reconciliation fields

Each resource records requested/actual security and month coverage, raw count,
new and deduplicated business/evidence identities, repeated evidence
observations, unknown-publication count, rejected/quarantined count, source and
canonical units, coverage-validation state, and warnings. Pilot 1 has no
authoritative trading calendar, so successful monthly imports state
`coverage_validation = "not_evaluated"` and `coverage_gaps = null`; they do not
claim gap-free coverage. Explicit source/schema/domain violations are
quarantined after raw capture. Infrastructure, database, and unexpected
programming failures are not mislabeled as bad source data: the checkpoint
remains `captured`, retains its original raw artifact and ingest run, and can
resume without a refetch. Network failures before capture are reported as
fetch failures because no source artifact was received.

Security-history import manifests record explicit transfer evidence as
`provisional`; they intentionally omit final matched/unmatched counts. The
`security-transfer-reconciliation` command emits those final counts and the
deterministically ordered matched/unmatched event lists from current canonical
TWSE/TPEx histories. Until the applicable TPEx delisting year has a successful
manifest, absent counterparts are reported as pending rather than genuinely
unmatched. Re-running after that source arrives replaces stale audit
interpretation without mutating an earlier import manifest.

Adapters reject negative OHLC, volume, trade value, or trade count, as well as
OHLC values outside the reported low/high range. PostgreSQL independently
enforces the canonical price constraints as a final storage boundary.

Run the permanent real-endpoint pilot regression explicitly:

```bash
RUN_LIVE_SOURCE_TESTS=1 pytest -m live_source \
  tests/integration/test_phase9_real_ingestion_framework.py \
  tests/integration/test_phase9_security_metadata_ingestion.py \
  tests/integration/test_phase9_security_lifecycle_ingestion.py
```

The default suite skips network access while retaining parser, raw-first,
restart, operational-failure resume, concurrency, deduplication, quarantine,
and PIT regressions.
