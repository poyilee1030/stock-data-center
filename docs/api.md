# Public REST API v1

The API is how downstream systems read the Data Center (CLAUDE.md §56): they
never connect to PostgreSQL. It answers in dataset concepts, never table names
(§55), and every answer states the PIT context it was given. Which row a
context sees is `stock_data_center.v2.visibility`'s alone (§19,
`docs/pit_semantics.md`); the API parses, validates and renders.

Step 27-b serves the observed datasets below. Financial reports, the stored
derived datasets, `technical_indicators_pit:v1`, the stock list and the trading
calendar are Step 27-c; adjusted prices are Step 36.

## Running it

```text
STOCKDC_API_KEY=<key> DATABASE_URL=<url> python -m stock_data_center.api [--port 8000]
```

It listens on `127.0.0.1` only. The key and the database URL come from the
environment (for instance `set -a && . ./.env && set +a`), never the command
line. Every read runs in a read-only transaction.

Every request needs the key in the `X-API-Key` header; without it the answer
is `401`.

## Endpoints

| Request | Answer |
| --- | --- |
| `GET /v1/datasets` | every dataset: name, description, key fields, period field, columns, sources, and the columns each source never publishes |
| `GET /v1/datasets/{name}` | rows as a PIT context sees them |

Datasets: `daily-prices`, `indices`, `official-valuations` (source-published
PE/PB/yield, §53), `institutional-flows`, `institutional-market-flows`,
`foreign-holdings`, `margin-trading`, `securities-lending`,
`shareholding-distributions`, `monthly-revenues`, `corporate-actions`.

### Parameters of `/v1/datasets/{name}`

| Parameter | Meaning |
| --- | --- |
| `start`, `end` | required, `YYYY-MM-DD`: the key's own date (trade date, snapshot date, revenue month, ex-date) is in `[start, end]` |
| `stock_id` | repeatable; a dataset without stocks (indices, market flows) refuses it |
| `source` | repeatable; results are always per source, never merged (§30) |
| `information_as_of`, `knowledge_as_of` | market PIT (§15) |
| `system_as_of` | system PIT (§16); not combinable with the two above |

- An instant is ISO 8601 **with its UTC offset** (`2024-07-02T12:00:00+08:00`);
  without one the answer is `400`.
- `latest` and `now` are aliases for the instant the request arrived. A market
  parameter left out is the same instant (owner, 2026-09-25). The response says
  which instants were aliases and which defaulted; to reproduce history, send
  both instants explicitly.
- Without `stock_id` a query spans at most 31 days; at most 200 `stock_id` per
  request.
- A parameter the endpoint does not know is refused with `400`, so a misspelled
  PIT parameter never silently means "latest".

## Response

```json
{
  "dataset": "daily-prices",
  "pit": {"mode": "market",
          "information_as_of": "2026-09-25T02:00:00+00:00",
          "knowledge_as_of":   "2026-09-25T02:00:00+00:00",
          "defaulted": ["information_as_of", "knowledge_as_of"], "aliases": {}},
  "query": {"start": "2026-09-11", "end": "2026-09-11", "stock_id": ["2330"], "source": null},
  "unsourced": {},
  "rows": [{"stock_id": "2330", "source": "twse_mi_index", "trade_date": "2026-09-11",
            "close_price": 1760.00, "...": "...",
            "recorded_at": "2026-09-19T11:25:33.391821+00:00",
            "available_at": "2026-09-11T19:00:00+00:00",
            "provenance": {"fetch_id": "…", "raw_sha256": "…"}}]
}
```

- A system-PIT answer's `pit` is `{"mode": "system", "system_as_of": …}`.
- `available_at` is when the row's value became public, `recorded_at` when the
  Data Center recorded it; the row is the key's latest one with `available_at <=
  information_as_of` and `recorded_at <= knowledge_as_of`.
- `provenance` names the fetch and the SHA-256 of its raw file
  (`data/raw/<ab>/<sha256>`); a TWSE `TWT49U`/`TWTAUU` corporate action also
  names its detail page's fetch and file (§27).
- Exact decimals are JSON numbers written with their stored digits (`1234.50`),
  never through a binary float.
- A column a source never publishes is **omitted** from that source's rows and
  listed in `unsourced` (`stock_data_center.api.datasets.UNSOURCED`, each entry
  citing the audit): the whole-list index files have no open/high/low,
  `MI_5MINS_HIST` has no change, and each corporate-action result file publishes
  only its own kind of terms. A `null` anywhere else is a value the source did
  not give for that row, such as a price on a day without a trade.
- A retracted corporate action is not returned (`docs/pit_semantics.md`).

## Errors

`400` for a parameter the API cannot use, with a `detail` saying which; `401`
without a valid key; `404` for an unknown dataset.
