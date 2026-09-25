"""The API's OpenAPI description, which `/docs` renders as Swagger UI.

The handlers read their query parameters themselves, so an unknown one is
refused (`stock_data_center.api._params`); FastAPI therefore cannot see them.
This module describes them instead, from the same sets the handlers accept, and
declares the `X-API-Key` header so Swagger UI's Authorize button sends it. The
description is served without the key (owner, 2026-09-25); every data request
still needs it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

SCHEME = "ApiKey"

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
