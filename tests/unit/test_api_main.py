"""Where the API listens (owner, 2026-09-25): every interface of the office
desktop by default, so clients on the LAN or the tailnet can call it."""

from __future__ import annotations

from stock_data_center.api import __main__ as main


def test_the_default_is_every_interface_on_an_uncommon_port() -> None:
    args = main.parse([])
    assert args.host == "0.0.0.0"
    assert args.port == 28617


def test_it_can_be_kept_to_this_machine() -> None:
    assert main.parse(["--host", "127.0.0.1"]).host == "127.0.0.1"


def test_an_image_names_the_commit_it_serves(monkeypatch) -> None:
    # A container has no .git to ask: the image carries STOCKDC_GIT_COMMIT (§42).
    seen = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/x")
    monkeypatch.setenv("STOCKDC_API_KEY", "k")
    monkeypatch.setenv("STOCKDC_GIT_COMMIT", "abc123")
    monkeypatch.setattr(main, "create_app", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setattr(main.uvicorn, "run", lambda *a, **k: None)
    main.main([])
    assert seen["git_commit"] == "abc123"
