"""The public REST API v1 (Step 27).

    GET /v1/datasets                      what can be asked, and each dataset's shape
    GET /v1/datasets/{name}?start=&end=   rows as a PIT context sees them
    GET /v1/stocks                        today's stock list (reference data, not PIT)
    GET /v1/trading-days?start=&end=      the trading calendar (reference data, not PIT)

The API parses and renders; which row a PIT context sees is
`stock_data_center.v2.visibility`'s alone (CLAUDE.md §19). Every data request
needs the API key in `X-API-Key` (owner, 2026-09-25); only the description,
`/docs` (Swagger UI) and `/openapi.json`, and the web dashboard's page and assets
(`/`, `/assets/*`, ADR-0029) are served without it: none of them holds data. Every read runs
in a read-only transaction. A query parameter the endpoint does not know is refused:
a misspelled PIT parameter must never silently mean "latest".

    STOCKDC_API_KEY=... DATABASE_URL=... python -m stock_data_center.api
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Connection, Engine

from stock_data_center.api import derived, openapi
from stock_data_center.api import pit as pit_context
from stock_data_center.api.datasets import DATASETS, Dataset
from stock_data_center.api.derived import ADJUSTED, DERIVED, PIT_REFERENCE, Derived
from stock_data_center.api.render import dumps
from stock_data_center.db import schema_v2 as v2
from stock_data_center.v2 import listings, visibility
from stock_data_center.v2.adjusted_prices import AdjustedPrices
from stock_data_center.v2.derived import TechnicalIndicators, UnknownStockError

# A query without a stock spans at most this many days: the whole market over a
# month is about 40,000 daily rows.
WHOLE_MARKET_DAYS = 31
MAX_STOCKS = 200
# A report has about 420 facts: this is a whole market's four statements for
# about a quarter of its stocks, or one account for every report.
MAX_FACTS = 200_000
_PIT_PARAMS = frozenset({*pit_context.MARKET, pit_context.SYSTEM})
_ROW_PARAMS = frozenset({"start", "end", "stock_id", "source", *_PIT_PARAMS})
_REPORT_PARAMS = _ROW_PARAMS | {"statement", "account_code"}
_REFERENCE_PARAMS = _ROW_PARAMS | {"view"}
_STOCK_PARAMS = frozenset({"market", "stock_id", "date"})
_CALENDAR_PARAMS = frozenset({"start", "end"})
_REPEATABLE = frozenset({"stock_id", "source", "statement", "account_code"})
STATEMENTS = ("balance_sheet", "income_statement", "cash_flow")
MARKETS = ("sii", "otc")
VIEWS = ("as_of", "rolling")
UNIVERSE = (
    "Listed (sii) and OTC (otc) common stocks: every one on today's TWSE ISIN list, and "
    "every one delisted since 2020-01-02 that an official source shows to be a common "
    "stock, each with its listing spans. No ETF, preferred share, TDR, warrant or "
    "innovation-board stock. The list is refreshed in place, not point-in-time. The "
    "datasets are still collected only for stocks listed today, so history read through "
    "them carries survivorship bias."
)


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


def _params(request: Request, allowed: frozenset[str],
            repeatable: frozenset[str] = _REPEATABLE) -> tuple[dict[str, str], dict]:
    """The single-valued parameters, and the repeatable ones as lists (None if absent).

    A parameter the endpoint does not know is refused, and so is a single-valued
    one given twice: Starlette would keep its last value (code review of #61)."""
    query = request.query_params
    unknown = sorted(set(query) - allowed)
    if unknown:
        raise HTTPException(400, f"unknown parameter: {', '.join(unknown)}")
    repeated = sorted(k for k in set(query) if k not in repeatable
                      and len(query.getlist(k)) > 1)
    if repeated:
        raise HTTPException(400, f"repeated parameter: {', '.join(repeated)}")
    return ({k: v for k, v in query.items() if k not in repeatable},
            {k: query.getlist(k) or None for k in repeatable & allowed})


def _range(params: dict[str, str]) -> tuple[date, date]:
    start, end = _date(params, "start"), _date(params, "end")
    if start > end:
        raise HTTPException(400, "start is after end")
    return start, end


def _bounded(name: str, start: date, end: date, stock_ids, has_stock: bool) -> None:
    if stock_ids is not None and not has_stock:
        raise HTTPException(400, f"{name} has no stock: it takes no stock_id")
    if stock_ids is not None and len(stock_ids) > MAX_STOCKS:
        raise HTTPException(400, f"at most {MAX_STOCKS} stock_id per request")
    if stock_ids is None and has_stock and (end - start).days >= WHOLE_MARKET_DAYS:
        raise HTTPException(400, f"a query without stock_id spans at most "
                                 f"{WHOLE_MARKET_DAYS} days")


def _resolve(params: dict[str, str], arrived: datetime) -> pit_context.Resolved:
    try:
        return pit_context.resolve(params, arrived)
    except pit_context.PITError as error:
        raise HTTPException(400, str(error)) from None


def _describe(dataset: Dataset) -> dict:
    return {
        "name": dataset.name,
        "kind": "observed",
        "description": dataset.description,
        "keys": list(dataset.keys),
        "period": dataset.period,
        "columns": dataset.columns,
        "sources": dataset.sources,
        "unsourced": {source: sorted(dataset.unsourced(source)) for source in dataset.sources
                      if dataset.unsourced(source)},
    }


def _provenance(connection: Connection, rows) -> dict:
    ids = {row[c] for row in rows
           for c in ("fetch_id", "detail_fetch_id", "listed_fetch_id", "delisted_fetch_id")
           if c in row and row[c] is not None}
    if not ids:
        return {}
    f = v2.fetches
    return {fetch_id: None if sha256 is None else sha256.hex()
            for fetch_id, sha256 in connection.execute(
                sa.select(f.c.id, f.c.sha256).where(f.c.id.in_(ids)))}


def _describe_derived(dataset: Derived) -> dict:
    return {
        "name": dataset.name,
        "kind": "derived",
        "description": dataset.description,
        "keys": ["stock_id", "source", dataset.period],
        "period": dataset.period,
        "columns": dataset.columns,
        "derivation": derived.describe(dataset.stored.definition),
    }


def _describe_reference() -> dict:
    definition = derived.PIT_REFERENCE_DEFINITION
    return {
        "name": PIT_REFERENCE,
        "kind": "derived_on_demand",
        "description": "technical-indicators computed on demand under full PIT, one stock "
                       "at a time; view=rolling gives each date as of its own release.",
        "keys": ["stock_id", "source", "trade_date"],
        "period": "trade_date",
        "derivation": derived.describe(definition),
    }


def _describe_adjusted() -> dict:
    return {
        "name": ADJUSTED,
        "kind": "derived_on_demand",
        "description": "Daily prices adjusted for corporate actions, total-return style, "
                       "computed on demand under PIT, one stock at a time.",
        "keys": ["stock_id", "source", "trade_date"],
        "period": "trade_date",
        "derivation": derived.describe(derived.ADJUSTED_DEFINITION),
    }


def _provenance_of(row, sha: dict) -> dict:
    return {"fetch_id": row["fetch_id"], "raw_sha256": sha.get(row["fetch_id"])}


def _stock(row, spans, sha: dict) -> dict:
    """A company with every listing span; `market` and `listed_on` are its open span's."""
    current = next((span for span in spans if span["delisted_on"] is None), None)
    return {
        "stock_id": row["stock_id"], "name": row["name"], "industry": row["industry"],
        "market": current["market"] if current else None,
        "listed_on": current["listed_on"] if current else None,
        "listings": [{
            "market": span["market"], "listed_on": span["listed_on"],
            "delisted_on": span["delisted_on"],
            "provenance": {
                **_provenance_of(span, sha),
                **{f"{end}_fetch_id": span[f"{end}_fetch_id"] for end in ("listed", "delisted")},
                **{f"{end}_raw_sha256": sha.get(span[f"{end}_fetch_id"])
                   for end in ("listed", "delisted")},
            }} for span in spans],
        "provenance": _provenance_of(row, sha),
    }


def _render_row(dataset: Dataset, row, sha: dict) -> dict:
    hidden = dataset.unsourced(row["source"]) if "source" in row else frozenset()
    out = {name: row[name] for name in dataset.columns if name not in hidden}
    out["available_at"] = row["available_at"]
    provenance = _provenance_of(row, sha)
    if row.get("detail_fetch_id") is not None:
        provenance["detail_fetch_id"] = row["detail_fetch_id"]
        provenance["detail_raw_sha256"] = sha.get(row["detail_fetch_id"])
    out["provenance"] = provenance
    return out


def _fact(fact) -> dict:
    return {name: fact[name] for name in ("statement", "account_code", "concept",
                                          "period_start", "period_end", "unit", "value")}


def create_app(*, api_key: str,
               connect: Callable[[], AbstractContextManager[Connection]],
               git_commit: str | None = None, web_dir: Path | None = None) -> FastAPI:
    """The API; `connect` opens the connection one request reads through.

    `git_commit` is the implementation `technical-indicators-pit` reports;
    None asks git, which a container image cannot. `web_dir` is the built web
    dashboard (`web/dist`, ADR-0029): its `index.html` at `/` and its hashed
    files at `/assets/*`; None serves no page."""
    if not api_key:
        raise ValueError("an API key is required: set STOCKDC_API_KEY")
    if web_dir is not None and not (Path(web_dir) / "index.html").is_file():
        raise ValueError(f"the web directory has no index.html: {web_dir}")
    app = FastAPI(title="stock-data-center", version="1", description=openapi.DESCRIPTION,
                  redoc_url=None, swagger_ui_oauth2_redirect_url=None)
    expected = api_key.encode()
    reference = TechnicalIndicators(git_commit=git_commit)  # takes the commit once
    adjusted = AdjustedPrices(git_commit=reference.git_commit)
    # The description only: it names no data (owner, 2026-09-25).
    public = frozenset({app.docs_url, app.openapi_url, *(("/",) if web_dir else ())})

    @app.middleware("http")
    async def require_key(request: Request, call_next):
        path = request.url.path
        if path in public or (web_dir is not None and path.startswith("/assets/")):
            return await call_next(request)
        given = request.headers.get("x-api-key", "").encode()
        if not hmac.compare_digest(given, expected):
            return _json({"detail": "a valid X-API-Key header is required"}, 401)
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return _json({"detail": error.detail}, error.status_code)

    @app.get("/v1/datasets", summary="Every dataset and its shape",
             description=openapi.DATASETS)
    def list_datasets() -> Response:
        return _json({"datasets": [*(_describe(d) for d in DATASETS.values()),
                                   *(_describe_derived(d) for d in DERIVED.values()),
                                   _describe_reference(), _describe_adjusted()]})

    @app.get("/v1/datasets/{name}", summary="Rows as a PIT context sees them",
             description=openapi.ROWS)
    def dataset_rows(name: str, request: Request) -> Response:
        arrived = datetime.now(UTC)
        if name in DERIVED:
            return _derived_rows(DERIVED[name], request, arrived)
        if name == PIT_REFERENCE:
            return _reference_rows(request, arrived)
        if name == ADJUSTED:
            return _adjusted_rows(request, arrived)
        dataset = DATASETS.get(name)
        if dataset is None:
            raise HTTPException(404, f"no dataset {name!r}; see /v1/datasets")
        reports = dataset.table is v2.financial_reports
        params, lists = _params(request, _REPORT_PARAMS if reports else _ROW_PARAMS)
        start, end = _range(params)
        stock_ids, sources = lists["stock_id"], lists["source"]
        _bounded(name, start, end, stock_ids, dataset.has_stock)
        if reports:
            if sources is not None:
                raise HTTPException(400, f"{name} has one source: it takes no source")
            for statement in lists["statement"] or ():
                if statement not in STATEMENTS:
                    raise HTTPException(400, f"statement is one of {', '.join(STATEMENTS)}: "
                                             f"{statement!r}")
        resolved = _resolve(params, arrived)
        with connect() as connection:
            rows = visibility.rows(connection, dataset.table.name, resolved.pit, start=start,
                                   end=end, stock_ids=stock_ids, sources=sources)
            sha = _provenance(connection, rows)
            facts = None
            if reports:
                facts = visibility.report_facts(
                    connection, [row["id"] for row in rows], statements=lists["statement"],
                    account_codes=lists["account_code"], limit=MAX_FACTS + 1)
                if sum(map(len, facts.values())) > MAX_FACTS:
                    raise HTTPException(400, f"more than {MAX_FACTS} facts: narrow the query "
                                             "with stock_id, statement or account_code")
        rendered = [_render_row(dataset, row, sha) for row in rows]
        if facts is not None:
            for out, row in zip(rendered, rows, strict=True):
                out["facts"] = [_fact(f) for f in facts[row["id"]]]
        query = {"start": start, "end": end, "stock_id": stock_ids, "source": sources}
        if reports:
            query.update(statement=lists["statement"], account_code=lists["account_code"])
        return _json({
            "dataset": name,
            "pit": resolved.describe(),
            "query": query,
            "unsourced": {source: sorted(dataset.unsourced(source))
                          for source in sorted({r["source"] for r in rows if "source" in r})
                          if dataset.unsourced(source)},
            "rows": rendered,
        })

    def _derived_rows(dataset: Derived, request: Request, arrived: datetime) -> Response:
        params, lists = _params(request, _ROW_PARAMS)
        start, end = _range(params)
        stock_ids, sources = lists["stock_id"], lists["source"]
        _bounded(dataset.name, start, end, stock_ids, True)
        resolved = _resolve(params, arrived)
        # The table follows the latest inputs and is overwritten: it cannot say
        # what was recorded by an earlier instant (owner, 2026-09-25).
        pit = resolved.pit
        cutoff = (pit.system_as_of if isinstance(pit, visibility.SystemPIT)
                  else pit.knowledge_as_of)
        if cutoff < arrived:
            raise HTTPException(400, f"{dataset.name} is computed from the latest inputs and "
                                     "cannot answer an earlier knowledge_as_of or "
                                     "system_as_of: use latest")
        information = (None if isinstance(pit, visibility.SystemPIT)
                       else pit.information_as_of)
        with connect() as connection:
            rows = visibility.derived_rows(connection, dataset.stored,
                                           information_as_of=information, start=start, end=end,
                                           stock_ids=stock_ids, sources=sources)
        return _json({
            "dataset": dataset.name,
            "derivation": derived.describe(dataset.stored.definition),
            "pit": resolved.describe(),
            "inputs": "latest",
            "query": {"start": start, "end": end, "stock_id": stock_ids, "source": sources},
            "rows": rows,
        })

    def _reference_rows(request: Request, arrived: datetime) -> Response:
        params, _ = _params(request, _REFERENCE_PARAMS, frozenset())
        start, end = _range(params)
        if "stock_id" not in params:
            raise HTTPException(400, f"{PIT_REFERENCE} is computed for one stock_id at a time")
        view = params.get("view", "as_of")
        if view not in VIEWS:
            raise HTTPException(400, f"view is as_of or rolling: {view!r}")
        if view == "rolling" and "information_as_of" in params:
            raise HTTPException(400, "view=rolling computes each date at its own release "
                                     "instant: it takes no information_as_of")
        resolved = _resolve(params, arrived)
        if isinstance(resolved.pit, visibility.SystemPIT):
            raise HTTPException(400, f"{PIT_REFERENCE} answers market PIT: "
                                     "information_as_of and knowledge_as_of")
        pit = resolved.pit
        with connect() as connection:
            try:
                if view == "rolling":
                    rows = reference.rolling(
                        connection, stock_id=params["stock_id"], start_date=start, end_date=end,
                        source=params.get("source"), knowledge_as_of=pit.knowledge_as_of)
                else:
                    rows = reference.compute(
                        connection, stock_id=params["stock_id"], start_date=start, end_date=end,
                        information_as_of=pit.information_as_of,
                        knowledge_as_of=pit.knowledge_as_of, source=params.get("source"))
            except (UnknownStockError, ValueError) as error:
                raise HTTPException(400, str(error)) from None
        described = resolved.describe()
        if view == "rolling":
            described["information_as_of"] = "each date's own release instant"
        return _json({
            "dataset": PIT_REFERENCE,
            "derivation": {**derived.describe(derived.PIT_REFERENCE_DEFINITION),
                           "git_commit": rows[0].git_commit if rows else reference.git_commit},
            "pit": described,
            "view": view,
            "query": {"start": start, "end": end, "stock_id": params["stock_id"],
                      "source": params.get("source")},
            "rows": [{"stock_id": r.stock_id, "source": r.source,
                      "trade_date": r.observation_date,
                      "information_as_of": r.information_as_of,
                      "input_count": r.input_count, "input_fingerprint": r.input_fingerprint,
                      **r.metrics} for r in rows],
        })

    def _adjusted_rows(request: Request, arrived: datetime) -> Response:
        params, _ = _params(request, _ROW_PARAMS, frozenset())
        start, end = _range(params)
        if "stock_id" not in params:
            raise HTTPException(400, f"{ADJUSTED} is computed for one stock_id at a time")
        resolved = _resolve(params, arrived)
        with connect() as connection:
            try:
                series = adjusted.compute(connection, stock_id=params["stock_id"],
                                          start_date=start, end_date=end, pit=resolved.pit,
                                          source=params.get("source"))
            except (UnknownStockError, ValueError) as error:
                raise HTTPException(400, str(error)) from None
            sha = _provenance(connection, [e.row for e in series.events])
        return _json({
            "dataset": ADJUSTED,
            "derivation": {**derived.describe(derived.ADJUSTED_DEFINITION),
                           "git_commit": series.git_commit},
            "pit": resolved.describe(),
            "query": {"start": start, "end": end, "stock_id": params["stock_id"],
                      "source": params.get("source")},
            "rows": [{"stock_id": series.stock_id, "source": series.source,
                      "trade_date": r.bar.trade_date,
                      "open_price": r.bar.open, "high_price": r.bar.high,
                      "low_price": r.bar.low, "close_price": r.bar.close,
                      "adjustment_factor": r.factor,
                      "adjusted_open_price": r.adjusted_open,
                      "adjusted_high_price": r.adjusted_high,
                      "adjusted_low_price": r.adjusted_low,
                      "adjusted_close_price": r.adjusted_close} for r in series.rows],
            "events": [{**_render_row(DATASETS["corporate-actions"], e.row, sha),
                        "factor": e.factor} for e in series.events],
        })

    @app.get("/v1/stocks", summary="Today's stock list", description=openapi.STOCKS)
    def stock_list(request: Request) -> Response:
        params, lists = _params(request, _STOCK_PARAMS, frozenset({"stock_id"}))
        s, spans = v2.stocks, v2.listings
        chosen = sa.select(spans.c.stock_id)
        if "market" in params:
            if params["market"] not in MARKETS:
                raise HTTPException(400, f"market is one of {', '.join(MARKETS)}: "
                                         f"{params['market']!r}")
            chosen = chosen.where(spans.c.market == params["market"])
        if "date" in params:
            day = _date(params, "date")
            if day < listings.WINDOW_START:
                raise HTTPException(400, f"date starts at {listings.WINDOW_START}: listing "
                                         "spans that closed before it are not kept")
            chosen = chosen.where(
                sa.or_(spans.c.listed_on.is_(None), spans.c.listed_on <= day),
                sa.or_(spans.c.delisted_on.is_(None), spans.c.delisted_on > day))
        query = sa.select(s).order_by(s.c.stock_id)
        if "market" in params or "date" in params:
            query = query.where(s.c.stock_id.in_(chosen))
        if lists["stock_id"] is not None:
            query = query.where(s.c.stock_id.in_(lists["stock_id"]))
        with connect() as connection:
            rows = connection.execute(query).mappings().all()
            held = connection.execute(
                sa.select(spans).where(spans.c.stock_id.in_([r["stock_id"] for r in rows]))
                .order_by(spans.c.stock_id, spans.c.listed_on.asc().nulls_first(),
                          spans.c.delisted_on.asc().nulls_last())).mappings().all()
            sha = _provenance(connection, [*rows, *held])
        by_stock: dict[str, list] = {}
        for span in held:
            by_stock.setdefault(span["stock_id"], []).append(span)
        return _json({
            "universe": UNIVERSE,
            "rows": [_stock(row, by_stock.get(row["stock_id"], []), sha) for row in rows],
        })

    @app.get("/v1/trading-days", summary="The trading calendar",
             description=openapi.CALENDAR)
    def trading_calendar(request: Request) -> Response:
        params, _ = _params(request, _CALENDAR_PARAMS, frozenset())
        start, end = _range(params)
        t = v2.trading_days
        with connect() as connection:
            rows = connection.execute(sa.select(t).where(t.c.trade_date.between(start, end))
                                      .order_by(t.c.trade_date)).mappings().all()
            sha = _provenance(connection, rows)
        return _json({
            "calendar": "TWSE trading days, corrected in place when the exchange revises "
                        "its calendar: reference data, not point-in-time.",
            "query": {"start": start, "end": end},
            "rows": [{"trade_date": row["trade_date"], "provenance": _provenance_of(row, sha)}
                     for row in rows],
        })

    openapi.install(
        app,
        {"/v1/datasets": frozenset(), "/v1/datasets/{name}": _REPORT_PARAMS | _REFERENCE_PARAMS,
         "/v1/stocks": _STOCK_PARAMS, "/v1/trading-days": _CALENDAR_PARAMS},
        {"statement": STATEMENTS, "market": MARKETS, "view": VIEWS},
        [*DATASETS, *DERIVED, PIT_REFERENCE, ADJUSTED])
    if web_dir is not None:
        page = Path(web_dir) / "index.html"

        # The page is revalidated on every visit, so a redeploy reaches the
        # browser; its assets are named by their content hash.
        @app.get("/", include_in_schema=False)
        def dashboard() -> FileResponse:
            return FileResponse(page, headers={"Cache-Control": "no-cache"})

        app.mount("/assets", StaticFiles(directory=Path(web_dir) / "assets"), name="assets")
    return app
