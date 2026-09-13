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
```

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

Adapters reject negative OHLC, volume, trade value, or trade count, as well as
OHLC values outside the reported low/high range. PostgreSQL independently
enforces the canonical price constraints as a final storage boundary.

Run the permanent real-endpoint pilot regression explicitly:

```bash
RUN_LIVE_SOURCE_TESTS=1 pytest -m live_source \
  tests/integration/test_phase9_real_ingestion_framework.py \
  tests/integration/test_phase9_security_metadata_ingestion.py
```

The default suite skips network access while retaining parser, raw-first,
restart, operational-failure resume, concurrency, deduplication, quarantine,
and PIT regressions.
