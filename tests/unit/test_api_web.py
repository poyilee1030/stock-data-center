"""The web dashboard is served by the API itself, same origin (ADR-0029, Step 37-a).

The page and its hashed assets hold no data, so they are served without the key,
like `/docs`; every `/v1` request still needs it, and so does any other path.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stock_data_center import api
from stock_data_center.api import __main__ as main

KEY = "test-key-0123456789"


def _unreachable():
    raise AssertionError("serving the page never reads the database")


@pytest.fixture
def web(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>dashboard</title>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    return tmp_path


@pytest.fixture
def client(web):
    with TestClient(api.create_app(api_key=KEY, connect=_unreachable, web_dir=web)) as client:
        yield client


def test_the_page_is_served_without_the_key(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "dashboard" in response.text
    assert response.headers["content-type"].startswith("text/html")
    # A redeploy must reach the browser: the page is revalidated every time.
    assert response.headers["cache-control"] == "no-cache"


def test_its_assets_are_served_without_the_key(client) -> None:
    response = client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert response.text == "console.log(1)"


def test_a_missing_asset_is_404_not_a_page(client) -> None:
    assert client.get("/assets/missing.js").status_code == 404


@pytest.mark.parametrize("path", ["/v1/datasets", "/v1/stocks", "/index.html", "/redoc",
                                  "/assets", "/web/", "/stock/2330"])
def test_everything_else_still_needs_the_key(client, path) -> None:
    assert client.get(path).status_code == 401


def test_an_asset_path_cannot_climb_out_of_the_assets(client, web) -> None:
    (web.parent / "secret.txt").write_text("secret")
    for path in ("/assets/../index.html", "/assets/%2e%2e/%2e%2e/secret.txt"):
        assert "secret" not in client.get(path).text


def test_without_a_web_directory_there_is_no_page() -> None:
    with TestClient(api.create_app(api_key=KEY, connect=_unreachable)) as client:
        assert client.get("/").status_code == 401
        assert client.get("/assets/index-abc123.js").status_code == 401


def test_a_web_directory_without_its_page_refuses_to_start(tmp_path) -> None:
    with pytest.raises(ValueError, match="index.html"):
        api.create_app(api_key=KEY, connect=_unreachable, web_dir=tmp_path)


def test_the_server_takes_the_web_directory_from_the_environment(monkeypatch, web) -> None:
    seen = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/x")
    monkeypatch.setenv("STOCKDC_API_KEY", "k")
    monkeypatch.setenv("STOCKDC_WEB_DIR", str(web))
    monkeypatch.setattr(main, "create_app", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setattr(main.uvicorn, "run", lambda *a, **k: None)
    main.main([])
    assert str(seen["web_dir"]) == str(web)


def test_the_server_serves_no_page_unless_told(monkeypatch) -> None:
    seen = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/x")
    monkeypatch.setenv("STOCKDC_API_KEY", "k")
    monkeypatch.delenv("STOCKDC_WEB_DIR", raising=False)
    monkeypatch.setattr(main, "create_app", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setattr(main.uvicorn, "run", lambda *a, **k: None)
    main.main([])
    assert seen["web_dir"] is None
