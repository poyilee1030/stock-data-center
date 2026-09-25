# Public REST API v1

The API is how downstream systems read the Data Center (CLAUDE.md §56): they
never connect to PostgreSQL. It answers in dataset concepts, never table names
(§55), and every answer states the PIT context it was given. Which row a
context sees is `stock_data_center.v2.visibility`'s alone (§19,
`docs/pit_semantics.md`); the API parses, validates and renders.

Step 27-b serves the observed datasets; Step 27-c adds financial reports, the
stored derived datasets, `technical_indicators_pit:v1`, the stock list and the
trading calendar. Adjusted prices are Step 36.

## Running it

The API runs as the `api` service of `docker-compose.yml`, next to PostgreSQL:

```text
scripts/api_up.sh          # docker compose up -d --build api, with the commit it serves
```

It reads `stockdc_backfill` (`STOCKDC_API_DATABASE` overrides it) over the
compose network, and takes the key from `STOCKDC_API_KEY` in `.env`; without
one it does not start. PostgreSQL listens on `127.0.0.1:26519` only, so a
client can reach the data through the API alone.

The API listens on every interface, port 28617 (owner, 2026-09-25): the server
is an office desktop, and clients on the LAN or the tailnet call
`http://<its LAN or Tailscale address>:28617`. On the LAN the key travels in
clear HTTP; over Tailscale it is encrypted, so prefer the Tailscale address.
Every read runs in a read-only transaction.

Outside the container, for development:

```text
STOCKDC_API_KEY=<key> DATABASE_URL=<url> python -m stock_data_center.api [--host 0.0.0.0] [--port 28617]
```

`--host 127.0.0.1` keeps it to this machine. The key and the database URL come
from the environment (for instance `set -a && . ./.env && set +a`), never the
command line.

Every `/v1` request needs the key in the `X-API-Key` header; without it the
answer is `401`.

## Swagger UI

`/docs` is Swagger UI and `/openapi.json` its OpenAPI description; both are
served without the key (owner, 2026-09-25), since they describe the API's shape
and hold no data. Press **Authorize** and paste the key to try requests from
the page. The page loads Swagger UI's script and stylesheet from
cdn.jsdelivr.net, so the browser needs internet access.

The handlers read their query parameters themselves, so that an unknown one is
refused; FastAPI cannot see them, and `stock_data_center.api.openapi`
describes them from the same sets the handlers accept. A parameter a handler
accepts without a description stops the app from starting.

## Endpoints

| Request | Answer |
| --- | --- |
| `GET /v1/datasets` | every dataset: name, `kind`, description, key fields, period field, columns; an observed one's sources and the columns each never publishes; a derived one's definition |
| `GET /v1/datasets/{name}` | rows as a PIT context sees them |
| `GET /v1/stocks` | today's stock list (reference data, not PIT) |
| `GET /v1/trading-days` | the trading calendar (reference data, not PIT) |

Observed datasets (`kind: observed`): `daily-prices`, `indices`,
`official-valuations` (source-published PE/PB/yield, §53),
`institutional-flows`, `institutional-market-flows`, `foreign-holdings`,
`margin-trading`, `securities-lending`, `shareholding-distributions`,
`monthly-revenues`, `corporate-actions`, `financial-reports`.

Stored derived datasets (`kind: derived`, Step 26): `technical-indicators`,
`institutional-streaks`, `institutional-cumulative-flows`,
`shareholding-concentrations`, `margin-metrics`, `short-interest-metrics`,
`valuation-metrics` (computed, not `official-valuations`). On demand
(`kind: derived_on_demand`): `technical-indicators-pit`.

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

## Financial reports

`financial-reports` returns, per `(stock_id, report_year, report_quarter)`, the
report version the PIT context sees, with **that version's own facts** in
`facts` (§20): a restatement is a new version with its full set, so a fact it
drops is absent from it. `start`/`end` filter the quarter's last day. A report
without a proven publication (`published_at` NULL) is never market visible
(§31), though system PIT shows it with `available_at: null`.

| Parameter | Meaning |
| --- | --- |
| `statement` | repeatable: `balance_sheet`, `income_statement`, `cash_flow` |
| `account_code` | repeatable, such as `9750` (basic EPS) |

A fact is `{statement, account_code, concept, period_start, period_end, unit,
value}`; `concept` is the namespace-qualified name (Clark notation, §33), and
an instant has `period_start: null`. A report has about 420 facts; an answer
holds at most 200,000, so a whole-market quarter needs `statement` or
`account_code`.

## Stored derived datasets

These tables are computed from the **latest inputs** and overwritten when an
input is corrected (§43); they have no knowledge axis. So:

- Only `information_as_of` filters them: a row is returned once every input it
  was computed from is public. Its `available_at` is its own date's release
  instant (03:00 Asia/Taipei the next day; TDCC's Sunday noon for
  `shareholding-concentrations`), later only where an input it reads has a
  correction available later (`docs/pit_semantics.md`).
- A `knowledge_as_of` or `system_as_of` earlier than the request is refused
  with `400`: the table cannot say what was known before. `latest` works for
  both; `system_as_of=latest` returns every stored row.
- The response says `"inputs": "latest"`, carries the definition in
  `derivation` (dataset code, derivation version, formula, input datasets,
  conventions, §42), and each row its `computed_at` (computation provenance,
  never publication time, §44). Stored rows carry no provenance of their own
  (§45).
- Between an input's correction and the next derived run, a row still holds the
  value computed before it while its `available_at` has already moved to the
  correction: a `computed_at` earlier than that means it is not recomputed yet.

To reproduce what a past run saw, use the observed datasets with explicit
instants, or `technical-indicators-pit`.

## `technical-indicators-pit`

`technical_indicators_pit:v1`, the same formula as `technical-indicators`
computed on demand under full PIT, for **one** `stock_id` per request
(`source` needed only if the stock has two price sources).

| `view` | Meaning |
| --- | --- |
| `as_of` (default) | the series as seen at `information_as_of`, from rows recorded by `knowledge_as_of` |
| `rolling` | each date computed at its own release instant, so no value sees a later price; takes no `information_as_of` |

Each row carries `information_as_of`, `input_count` and `input_fingerprint`
(SHA-256 of the input rows' dates and `recorded_at`); `derivation.git_commit`
names the implementation. System PIT is refused: the on-demand series is a
market-PIT answer.

## Reference data

`GET /v1/stocks` (`market=sii|otc`, repeatable `stock_id`) returns today's
list of listed and OTC common stocks (ADR-0026): no company delisted before
today, so history read over it carries survivorship bias, and the list is
refreshed in place, not point in time. `GET /v1/trading-days?start=&end=`
returns the TWSE trading calendar, corrected in place when the exchange revises
it. Both carry provenance per row and take no PIT parameter.

## Errors

`400` for a parameter the API cannot use, with a `detail` saying which; `401`
without a valid key; `404` for an unknown dataset.
