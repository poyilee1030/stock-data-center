"""A minimal Fubon Neo market-data client for exploration.

Not part of `stock_data_center`, and nothing here writes to the Data Center.
The `fubon_neo` SDK is not on PyPI; it lives in the trade project's venv
together with its `.env` and `.pfx` certificate, so these scripts run with that
interpreter:

    ~/GitHubLL/my_trade_project/venv/bin/python third-party/fubon/fetch_technical.py

Credentials are read from the trade project's `.env` (`PERSONAL_ID`,
`API_KEY`, `CERT_PATH`, optional `CERT_PASS`) and are never printed or saved.
Only market-data endpoints are used — no account, order or trading call.

Every response is kept verbatim under `third-party/fubon/raw/`, so the comparison scripts
can run later, from this repo's own venv, without logging in again.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "raw"
TRADE_PROJECT = Path(
    os.getenv("FUBON_PROJECT_DIR", Path.home() / "GitHubLL" / "my_trade_project")
)


class FubonError(RuntimeError):
    """Raised when login fails or the trade project is not where expected."""


def _env() -> dict[str, str]:
    path = TRADE_PROJECT / ".env"
    if not path.is_file():
        raise FubonError(f"no .env in {TRADE_PROJECT}; set FUBON_PROJECT_DIR")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            values[key.strip()] = value.strip().strip("'\"")
    return values


def login():
    """Log in and return the SDK's market-data REST client."""
    from fubon_neo.sdk import FubonSDK  # only in the trade project's venv

    env = _env()
    cert = Path(env["CERT_PATH"])
    if not cert.is_absolute():
        # The trade project's CERT_PATH is relative to its own root.
        cert = TRADE_PROJECT / cert
    sdk = FubonSDK()
    result = sdk.apikey_login(
        personal_id=env["PERSONAL_ID"],
        api_key=env["API_KEY"],
        cert_path=str(cert),
        cert_pass=env.get("CERT_PASS") or None,
    )
    if not result.is_success:
        raise FubonError(f"login failed: {result.message}")
    sdk.init_realtime()
    return sdk.marketdata.rest_client.stock


def save(name: str, request: dict, body) -> Path:
    """Keep one response verbatim, with the request and the fetch instant."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(UTC)
    path = RAW_DIR / f"{name}_{fetched_at:%Y%m%dT%H%M%SZ}.json"
    path.write_text(
        json.dumps(
            {"request": request, "fetched_at": fetched_at.isoformat(), "body": body},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def latest(name: str) -> dict:
    """The most recently saved response for `name`."""
    paths = sorted(RAW_DIR.glob(f"{name}_*.json"))
    if not paths:
        raise FubonError(f"no saved response for {name!r}; run the fetch script first")
    return json.loads(paths[-1].read_text(encoding="utf-8"))


def finish(code: int = 0) -> None:
    """Exit without interpreter teardown.

    The native `fubon_neo` wheel can segfault while Python shuts down; every
    result is already on disk by the time this is called.
    """
    sys.stdout.flush()
    os._exit(code)
