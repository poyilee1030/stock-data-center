"""The API's description: Swagger UI at /docs, served without the key (owner, 2026-09-25).

The handlers read their query parameters themselves, so FastAPI cannot see
them; `stock_data_center.api.openapi` describes them from the sets the handlers
accept. These tests keep the description and the parsing in step, and keep the
key on every data request.
"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stock_data_center import api
from stock_data_center.api import openapi
from stock_data_center.api.datasets import DATASETS
from stock_data_center.api.derived import ADJUSTED, DERIVED, PIT_REFERENCE
from stock_data_center.db.base import metadata

KEY = "test-key-0123456789"


def _unreachable():
    raise AssertionError("the description never reads the database")


@pytest.fixture
def client():
    with TestClient(api.create_app(api_key=KEY, connect=_unreachable)) as client:
        yield client


def _documented(schema: dict, path: str) -> dict[str, dict]:
    return {p["name"]: p for p in schema["paths"][path]["get"]["parameters"]
            if p["in"] == "query"}


def test_the_description_is_served_without_the_key(client) -> None:
    assert client.get("/docs").status_code == 200
    assert "swagger-ui" in client.get("/docs").text
    assert client.get("/openapi.json").status_code == 200


@pytest.mark.parametrize("path", ["/v1/datasets", "/v1/datasets/daily-prices", "/v1/stocks",
                                  "/v1/trading-days", "/docs/", "/redoc",
                                  "/docs/oauth2-redirect", "/openapi.json/x"])
def test_everything_else_still_needs_the_key(client, path) -> None:
    assert client.get(path).status_code == 401


def test_swagger_ui_sends_the_key_as_x_api_key(client) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"] == {
        openapi.SCHEME: {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
    assert schema["security"] == [{openapi.SCHEME: []}]


def test_each_endpoint_documents_exactly_the_parameters_it_accepts(client) -> None:
    schema = client.get("/openapi.json").json()
    accepted = {
        "/v1/datasets": set(),
        "/v1/datasets/{name}": api._REPORT_PARAMS | api._REFERENCE_PARAMS,
        "/v1/stocks": api._STOCK_PARAMS,
        "/v1/trading-days": api._CALENDAR_PARAMS,
    }
    assert set(schema["paths"]) == set(accepted)
    for path, names in accepted.items():
        documented = _documented(schema, path)
        assert set(documented) == names, path
        for parameter in documented.values():
            assert parameter["description"], (path, parameter["name"])
            repeatable = parameter["name"] in api._REPEATABLE
            assert (parameter["schema"]["type"] == "array") == repeatable, parameter["name"]
    rows = _documented(schema, "/v1/datasets/{name}")
    assert rows["start"]["required"] and rows["end"]["required"]
    assert not any(p["required"] for n, p in rows.items() if n not in ("start", "end"))


def test_every_dataset_is_offered_by_name(client) -> None:
    schema = client.get("/openapi.json").json()
    (name,) = [p for p in schema["paths"]["/v1/datasets/{name}"]["get"]["parameters"]
               if p["in"] == "path"]
    assert name["schema"]["enum"] == [*DATASETS, *DERIVED, PIT_REFERENCE, ADJUSTED]


def test_choices_are_the_ones_the_handlers_accept(client) -> None:
    schema = client.get("/openapi.json").json()
    rows = _documented(schema, "/v1/datasets/{name}")
    assert rows["statement"]["schema"]["items"]["enum"] == list(api.STATEMENTS)
    assert rows["view"]["schema"]["enum"] == list(api.VIEWS)
    assert _documented(schema, "/v1/stocks")["market"]["schema"]["enum"] == list(api.MARKETS)


def test_no_answer_is_described_as_fastapis_422(client) -> None:
    # The handlers answer a bad request with 400 themselves.
    schema = client.get("/openapi.json").json()
    for item in schema["paths"].values():
        assert "422" not in item["get"]["responses"]
    assert "schemas" not in schema["components"]


def test_an_undescribed_parameter_fails_when_the_app_is_created() -> None:
    app = FastAPI()
    with pytest.raises(ValueError, match="undescribed query parameter: typo"):
        openapi.install(app, {"/x": frozenset({"start", "typo"})}, {}, [])


def test_an_unknown_dataset_is_described_as_404(client) -> None:
    schema = client.get("/openapi.json").json()
    assert "404" in schema["paths"]["/v1/datasets/{name}"]["get"]["responses"]


def test_the_description_names_no_internals(client) -> None:
    # Clients have no copy of the repository, and know datasets, not tables (§55).
    text = client.get("/openapi.json").text
    for internal in ("§", "ADR", "CLAUDE.md", "stock_data_center", "docs/"):
        assert internal not in text, internal
    tables = [name for name in metadata.tables if "_" in name]
    assert "daily_prices" in tables
    assert [name for name in tables if re.search(rf"\b{name}\b", text)] == []


def test_every_stored_derived_dataset_is_explained(client) -> None:
    rows = client.get("/openapi.json").json()["paths"]["/v1/datasets/{name}"]["get"]
    for name in DERIVED:
        assert f"`{name}`" in rows["description"], name
    assert f"### {PIT_REFERENCE}" in rows["description"]
    assert f"### {ADJUSTED}" in rows["description"]
