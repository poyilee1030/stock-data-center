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
```

The command prints the persisted manifest/reconciliation without connection
credentials. Reusing the same successful `import_id` and identical scope reads
the checkpoint and does not fetch again. If execution stopped immediately after
raw capture, the same command reads and hash-verifies those retained bytes,
uses the original ingest run, and continues without contacting the source. A
different configuration cannot be silently attached to an existing import ID.

## Reconciliation fields

Each resource records requested/actual security and month coverage, raw count,
new and deduplicated business/evidence identities, repeated evidence
observations, unknown-publication count, rejected/quarantined count, source and
canonical units, coverage-validation state, and warnings. Pilot 1 has no
authoritative trading calendar, so successful monthly imports state
`coverage_validation = "not_evaluated"` and `coverage_gaps = null`; they do not
claim gap-free coverage. Parser or writer ambiguity is quarantined
after raw capture; network failures are reported as fetch failures because no
source artifact was received.

Run the permanent real-endpoint pilot regression explicitly:

```bash
RUN_LIVE_SOURCE_TESTS=1 pytest -m live_source \
  tests/integration/test_phase9_real_ingestion_framework.py
```

The default suite skips network access while retaining parser, raw-first,
restart, deduplication, quarantine, and PIT regressions.
