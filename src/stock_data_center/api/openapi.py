"""The API's OpenAPI description, which `/docs` renders as Swagger UI.

The handlers read their query parameters themselves, so an unknown one is
refused (`stock_data_center.api._params`); FastAPI therefore cannot see them.
This module describes them instead, from the same sets the handlers accept, and
declares the `X-API-Key` header so Swagger UI's Authorize button sends it. The
description is served without the key (owner, 2026-09-25); every data request
still needs it.

The texts are for the API's clients, who have no copy of this repository: they
say what `docs/api.md` says a client needs, without its section numbers, module
names or table names (CLAUDE.md §55).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

SCHEME = "ApiKey"

DESCRIPTION = """\
Official Taiwan market data (TWSE, TPEx, MOPS, TDCC) with the time each value
became public and the raw file it came from, read point-in-time.

## Access

Every `/v1` request needs the API key in the `X-API-Key` header. On this page,
press **Authorize** and paste the key, then open an endpoint and press
**Try it out**.

## Point in time

Every answer states, in `pit`, the context it was answered in.

- **Market PIT** answers what was public by `information_as_of`, using what the
  Data Center had recorded by `knowledge_as_of`: for each key, the latest row
  with `available_at <= information_as_of` and `recorded_at <= knowledge_as_of`.
- **System PIT** answers what the Data Center had recorded by `system_as_of`,
  public or not. It cannot be combined with the market parameters.
- An instant carries its UTC offset, such as `2024-07-02T12:00:00+08:00`;
  without one the answer is 400. `latest` and `now` mean the moment the request
  arrived, and so does a market parameter left out; `pit.aliases` and
  `pit.defaulted` say which. To get the same answer again later, send both
  market instants explicitly.
- A value is public from its release: the exchanges' daily data from 03:00
  Taipei time the day after its trade date, TDCC's weekly distribution from the
  following Sunday noon, a corporate action from 00:00 on its ex-date, monthly
  revenue and financial reports from when each company published them. A
  correction is public from when it was recorded. A value whose publication
  time is unknown is never returned under market PIT.

## Answers

- Rows are per source: two sources are never merged. `source` selects them;
  `/v1/datasets` lists each dataset's sources.
- `available_at` is when a value became public, `recorded_at` when the Data
  Center recorded it, and `provenance` names the fetch and the SHA-256 of the
  raw file the row came from.
- Decimals are JSON numbers written with their stored digits (`1234.50`), not
  rounded through a binary float; read them as decimals where exactness matters.
- A column a source never publishes is left out of that source's rows and
  listed in `unsourced`. A `null` anywhere else is a value the source did not
  give for that row, such as a price on a day without a trade.
- Errors: 400 for a parameter the API cannot use, with `detail` saying which;
  401 without a valid key; 404 for an unknown dataset.

## Stocks

The stocks are today's listed (`sii`) and OTC (`otc`) common stocks: no ETF,
ETN, preferred share, TDR or warrant, and no company delisted before today.
History read over them therefore carries survivorship bias.
"""

DATASETS = """\
Every dataset: its name, `kind` (`observed`, `derived`, `derived_on_demand`),
description, key fields, date field (`period`) and columns. An observed
dataset also lists its sources and the columns each never publishes; a derived
one its formula, input datasets and conventions.
"""

ROWS = """\
Each key's row as the PIT context sees it. `/v1/datasets` describes every
dataset's keys, date field, columns and sources.

- `start` and `end` bound the key's own date: trade date, snapshot date,
  revenue month, ex-date, or a financial report's quarter end.
- At most 200 `stock_id`; without one a query spans at most 31 days.
  `indices` and `institutional-market-flows` have no stocks and refuse
  `stock_id`.
- A parameter the dataset does not take, or a single-valued one given twice,
  is refused with 400, so a misspelled PIT parameter never silently means
  "latest".

### corporate-actions

Events the exchanges executed: ex-right and ex-dividend, capital reduction,
and par-value change, one per stock, source and ex-date. `event_type` keeps
the source's own wording, such as 除息 or 現金減資. Each source publishes only
its own kind of terms; the others are listed in `unsourced`. An event later
removed from the exchange's results is not returned.

### financial-reports

One row per stock and quarter: the report version the PIT context sees, with
that version's own facts in `facts`. A restatement is a new version with its
full set of facts, so a fact it drops is absent from it.

- A fact is `{statement, account_code, concept, period_start, period_end,
  unit, value}`; `concept` is the namespace-qualified XBRL name, and an instant
  has `period_start: null`.
- `statement` and `account_code` narrow the facts. A report has about 420
  facts and an answer holds at most 200,000, so a whole-market query needs one
  of them.
- There is one source, so it takes no `source`. Financial-industry companies'
  statements are not included.
- A report whose publication time is not proven is never returned under
  market PIT; system PIT shows it with `available_at: null`.

### Stored derived datasets

`technical-indicators`, `institutional-streaks`,
`institutional-cumulative-flows`, `shareholding-concentrations`,
`margin-metrics`, `short-interest-metrics` and `valuation-metrics` are
computed from the latest inputs and recomputed when an input is corrected.

- The value for a date uses only inputs dated on or before it; a financial
  report enters `valuation-metrics` only from the day it was published.
- Prices are the official raw closes, not adjusted for corporate actions.
- Only `information_as_of` filters them: a row is returned once every input it
  was computed from is public. Its `available_at` is its date's release, 03:00
  Taipei time the next day (Sunday noon for `shareholding-concentrations`), or
  later where an input was corrected later.
- An earlier `knowledge_as_of` or `system_as_of` is refused with 400: the
  table cannot say what was known before. Use `latest`.
- The answer says `"inputs": "latest"` and carries the formula in
  `derivation`. Each row carries `computed_at`, when it was computed, which is
  not a publication time; a `computed_at` earlier than the row's
  `available_at` means it has not been recomputed since an input's correction.
- To see what was known at a past instant, read the observed datasets with
  explicit instants, or `technical-indicators-pit`.

### technical-indicators-pit

The same formula as `technical-indicators`, computed on request under full
market PIT, for one `stock_id` at a time.

- `view=as_of` (default): the series as seen at `information_as_of`, from rows
  recorded by `knowledge_as_of`.
- `view=rolling`: each date computed at its own release, so no value sees a
  later price; it takes no `information_as_of`.
- Each row carries `information_as_of`, `input_count` and `input_fingerprint`
  (a SHA-256 of its input rows' dates and recording times);
  `derivation.git_commit` names the implementation. System PIT is refused.
"""

STOCKS = """\
Today's listed (`sii`) and OTC (`otc`) common stocks, with name, market,
industry and listing date. It is refreshed in place from the latest list, so
it is not point-in-time and holds no company delisted before today. `market`
and `stock_id` filter it; it takes no PIT parameter.
"""

CALENDAR = """\
TWSE trading days in [`start`, `end`]. The calendar is corrected in place when
the exchange revises it, so it is not point-in-time; it takes no PIT
parameter.
"""

_DATE = {"type": "string", "format": "date"}
_INSTANT = {"type": "string", "examples": ["2024-07-02T12:00:00+08:00", "latest"]}
_LIST = {"type": "array", "items": {"type": "string"}}


def _described(schema: dict, description: str, *, required: bool = False) -> tuple:
    return schema, required, description


# Every query parameter any endpoint accepts, in the order Swagger UI lists them.
PARAMETERS: dict[str, tuple[dict, bool, str]] = {
    "start": _described(_DATE, "First date, YYYY-MM-DD, of the key's own date: trade date, "
                               "snapshot date, revenue month, ex-date, or a report's quarter "
                               "end.", required=True),
    "end": _described(_DATE, "Last date, YYYY-MM-DD, inclusive.", required=True),
    "stock_id": _described(_LIST, "Stock code, such as 2330; repeatable."),
    "source": _described(_LIST, "Source code; repeatable. Results are always per source, "
                                "never merged; /v1/datasets lists each dataset's sources."),
    "information_as_of": _described(_INSTANT, "Market PIT: what was public by this instant. "
                                               "ISO 8601 with its UTC offset, or latest/now; "
                                               "left out, the instant the request arrived."),
    "knowledge_as_of": _described(_INSTANT, "Market PIT: using what the Data Center had "
                                            "recorded by this instant. ISO 8601 with its UTC "
                                            "offset, or latest/now; left out, the instant the "
                                            "request arrived. Stored derived datasets take "
                                            "only latest."),
    "system_as_of": _described(_INSTANT, "System PIT: what the Data Center had recorded by "
                                         "this instant, public or not. Not combinable with "
                                         "information_as_of or knowledge_as_of."),
    "statement": _described(_LIST, "financial-reports only: the statements whose facts to "
                                   "return; repeatable."),
    "account_code": _described(_LIST, "financial-reports only: the account codes whose facts "
                                      "to return, such as 9750 (basic EPS); repeatable."),
    "view": _described({"type": "string", "default": "as_of"},
                       "technical-indicators-pit only: as_of computes every date as of one PIT "
                       "context; rolling computes each date at its own release instant and "
                       "takes no information_as_of."),
    "market": _described({"type": "string"}, "sii (listed) or otc."),
}


def _parameter(name: str, enums: Mapping[str, Sequence[str]]) -> dict:
    schema, required, description = PARAMETERS[name]
    schema = dict(schema)
    if name in enums:
        values = list(enums[name])
        if schema["type"] == "array":
            schema["items"] = {"type": "string", "enum": values}
        else:
            schema["enum"] = values
    return {"name": name, "in": "query", "required": required, "description": description,
            "schema": schema}


def install(app: FastAPI, accepted: Mapping[str, frozenset[str]],
            enums: Mapping[str, Sequence[str]], datasets: Sequence[str]) -> None:
    """Describe each path's `accepted` query parameters, and `datasets` as `{name}`'s values.

    A parameter an endpoint accepts but PARAMETERS does not describe fails here,
    when the app is created, rather than going missing from the description."""
    undescribed = sorted(set().union(*accepted.values()) - set(PARAMETERS))
    if undescribed:
        raise ValueError(f"undescribed query parameter: {', '.join(undescribed)}")

    def openapi() -> dict:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version,
                             description=app.description, routes=app.routes)
        components = schema.setdefault("components", {})
        components["securitySchemes"] = {
            SCHEME: {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
        schema["security"] = [{SCHEME: []}]
        # The handlers answer a bad request with 400 themselves, never FastAPI's 422.
        for name in ("HTTPValidationError", "ValidationError"):
            components.get("schemas", {}).pop(name, None)
        if not components.get("schemas"):
            components.pop("schemas", None)
        for path, names in accepted.items():
            operation = schema["paths"][path]["get"]
            operation["responses"].pop("422", None)
            if names:
                operation["responses"]["400"] = {
                    "description": "A parameter is missing, unknown, repeated or invalid; "
                                   "`detail` says which."}
            operation["responses"]["401"] = {"description": "X-API-Key is missing or wrong."}
            if "{name}" in path:
                operation["responses"]["404"] = {"description": "No such dataset."}
            path_parameters = [p for p in operation.get("parameters", ()) if p["in"] == "path"]
            for parameter in path_parameters:
                if parameter["name"] == "name":
                    parameter["schema"] = {"type": "string", "enum": list(datasets)}
                    parameter["description"] = "The dataset; /v1/datasets describes each."
            operation["parameters"] = [*path_parameters,
                                       *(_parameter(n, enums) for n in PARAMETERS if n in names)]
        app.openapi_schema = schema
        return schema

    app.openapi = openapi
