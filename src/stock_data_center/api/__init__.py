"""The public REST API v1 (Step 27).

    GET /v1/datasets                      what can be asked, and each dataset's shape
    GET /v1/datasets/{name}?start=&end=   rows as a PIT context sees them

The API parses and renders; which row a PIT context sees is
`stock_data_center.v2.visibility`'s alone (CLAUDE.md §19). Every request needs
the API key in `X-API-Key` (owner, 2026-09-25), and every read runs in a
read-only transaction. A query parameter the endpoint does not know is refused:
a misspelled PIT parameter must never silently mean "latest".

    STOCKDC_API_KEY=... DATABASE_URL=... python -m stock_data_center.api
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, date, datetime

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import Connection, Engine

from stock_data_center.api import pit as pit_context
from stock_data_center.api.datasets import DATASETS, Dataset
from stock_data_center.api.render import dumps
from stock_data_center.db import schema_v2 as v2
from stock_data_center.v2 import visibility

# A query without a stock spans at most this many days: the whole market over a
# month is about 40,000 daily rows.
WHOLE_MARKET_DAYS = 31
MAX_STOCKS = 200
_ROW_PARAMS = frozenset({"start", "end", "stock_id", "source", *pit_context.MARKET,
                         pit_context.SYSTEM})


@contextmanager
def read_only(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as connection:
        connection.begin()
        connection.execute(sa.text("SET TRANSACTION READ ONLY"))
        try:
            yield connection
        finally:
            connection.rollback()


def _json(body, status_code: int = 200) -> Response:
    return Response(dumps(body), status_code=status_code, media_type="application/json")


def _date(params, name: str) -> date:
    if name not in params:
        raise HTTPException(400, f"{name} is required (YYYY-MM-DD)")
    try:
        return date.fromisoformat(params[name])
    except ValueError:
        raise HTTPException(400, f"{name} is not a date (YYYY-MM-DD): {params[name]!r}") from None


def _describe(dataset: Dataset) -> dict:
    return {
        "name": dataset.name,
        "description": dataset.description,
        "keys": list(dataset.keys),
        "period": dataset.period,
        "columns": dataset.columns,
        "sources": dataset.sources,
        "unsourced": {source: sorted(dataset.unsourced(source)) for source in dataset.sources
                      if dataset.unsourced(source)},
    }


def _provenance(connection: Connection, rows) -> dict:
    ids = {row[c] for row in rows for c in ("fetch_id", "detail_fetch_id")
           if c in row and row[c] is not None}
    if not ids:
        return {}
    f = v2.fetches
    return {fetch_id: None if sha256 is None else sha256.hex()
            for fetch_id, sha256 in connection.execute(
                sa.select(f.c.id, f.c.sha256).where(f.c.id.in_(ids)))}


def _render_row(dataset: Dataset, row, sha: dict) -> dict:
    hidden = dataset.unsourced(row["source"]) if "source" in row else frozenset()
    out = {name: row[name] for name in dataset.columns if name not in hidden}
    out["available_at"] = row["available_at"]
    provenance = {"fetch_id": row["fetch_id"], "raw_sha256": sha.get(row["fetch_id"])}
    if row.get("detail_fetch_id") is not None:
        provenance["detail_fetch_id"] = row["detail_fetch_id"]
        provenance["detail_raw_sha256"] = sha.get(row["detail_fetch_id"])
    out["provenance"] = provenance
    return out


def create_app(*, api_key: str,
               connect: Callable[[], AbstractContextManager[Connection]]) -> FastAPI:
    """The API; `connect` opens the connection one request reads through."""
    if not api_key:
        raise ValueError("an API key is required: set STOCKDC_API_KEY")
    app = FastAPI(title="stock-data-center", version="1")
    expected = api_key.encode()

    @app.middleware("http")
    async def require_key(request: Request, call_next):
        given = request.headers.get("x-api-key", "").encode()
        if not hmac.compare_digest(given, expected):
            return _json({"detail": "a valid X-API-Key header is required"}, 401)
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return _json({"detail": error.detail}, error.status_code)

    @app.get("/v1/datasets")
    def list_datasets() -> Response:
        return _json({"datasets": [_describe(d) for d in DATASETS.values()]})

    @app.get("/v1/datasets/{name}")
    def dataset_rows(name: str, request: Request) -> Response:
        arrived = datetime.now(UTC)
        dataset = DATASETS.get(name)
        if dataset is None:
            raise HTTPException(404, f"no dataset {name!r}; see /v1/datasets")
        query = request.query_params
        unknown = sorted(set(query) - _ROW_PARAMS)
        if unknown:
            raise HTTPException(400, f"unknown parameter: {', '.join(unknown)}")
        # Only stock_id and source repeat; any other key given twice is ambiguous,
        # and Starlette would keep its last value (code review of #61).
        repeated = sorted(k for k in set(query) if k not in ("stock_id", "source")
                          and len(query.getlist(k)) > 1)
        if repeated:
            raise HTTPException(400, f"repeated parameter: {', '.join(repeated)}")
        params = {k: v for k, v in query.items() if k not in ("stock_id", "source")}
        start, end = _date(params, "start"), _date(params, "end")
        if start > end:
            raise HTTPException(400, "start is after end")
        stock_ids = query.getlist("stock_id") or None
        sources = query.getlist("source") or None
        if stock_ids is not None and not dataset.has_stock:
            raise HTTPException(400, f"{name} has no stock: it takes no stock_id")
        if stock_ids is not None and len(stock_ids) > MAX_STOCKS:
            raise HTTPException(400, f"at most {MAX_STOCKS} stock_id per request")
        if stock_ids is None and dataset.has_stock and (end - start).days >= WHOLE_MARKET_DAYS:
            raise HTTPException(400, f"a query without stock_id spans at most "
                                     f"{WHOLE_MARKET_DAYS} days")
        try:
            resolved = pit_context.resolve(params, arrived)
        except pit_context.PITError as error:
            raise HTTPException(400, str(error)) from None
        with connect() as connection:
            rows = visibility.rows(connection, dataset.table.name, resolved.pit, start=start,
                                   end=end, stock_ids=stock_ids, sources=sources)
            sha = _provenance(connection, rows)
        return _json({
            "dataset": name,
            "pit": resolved.describe(),
            "query": {"start": start, "end": end, "stock_id": stock_ids, "source": sources},
            "unsourced": {source: sorted(dataset.unsourced(source))
                          for source in sorted({r["source"] for r in rows if "source" in r})
                          if dataset.unsourced(source)},
            "rows": [_render_row(dataset, row, sha) for row in rows],
        })

    return app
