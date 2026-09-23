"""A minimal FinMind API v4 client for exploration.

Not part of `stock_data_center`: FinMind is a third-party redistributor, and
nothing here writes to the Data Center. Every response is kept verbatim under
`third-party/finmind/raw/` so a comparison can be rerun without calling the API again.

The token is read from `FINMIND_TOKEN` in the environment or the repo-root
`.env`, and is only ever sent in the `Authorization` header — never printed,
logged, or written into a saved file name or body.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

API_URL = "https://api.finmindtrade.com/api/v4/data"
ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "raw"
ENV_FILE = ROOT.parents[1] / ".env"


class FinMindError(RuntimeError):
    """Raised when FinMind answers with anything but a successful payload."""


def _token() -> str:
    token = os.getenv("FINMIND_TOKEN")
    if token:
        return token
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "FINMIND_TOKEN" and value.strip():
                return value.strip().strip("'\"")
    raise FinMindError(
        "FINMIND_TOKEN is not set; add FINMIND_TOKEN=... to the repo-root .env"
    )


def fetch(
    dataset: str,
    *,
    data_id: str | None = None,
    start_date: str,
    end_date: str | None = None,
    timeout: float = 30.0,
) -> list[dict]:
    """One dataset request; returns the `data` rows and saves the raw body."""
    params = {"dataset": dataset, "start_date": start_date}
    if data_id is not None:
        params["data_id"] = data_id
    if end_date is not None:
        params["end_date"] = end_date

    fetched_at = datetime.now(UTC)
    response = httpx.get(
        API_URL,
        params=params,
        headers={"Authorization": f"Bearer {_token()}"},
        timeout=timeout,
    )
    _save(params, fetched_at, response)

    if response.status_code != 200:
        raise FinMindError(f"{dataset}: HTTP {response.status_code} {response.text[:200]}")
    body = response.json()
    if body.get("status") != 200:
        raise FinMindError(f"{dataset}: {body.get('status')} {body.get('msg')}")
    return body.get("data", [])


def _save(params: dict, fetched_at: datetime, response: httpx.Response) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    name = "_".join(
        str(params[key])
        for key in ("dataset", "data_id", "start_date", "end_date")
        if key in params
    )
    path = RAW_DIR / f"{name}_{fetched_at:%Y%m%dT%H%M%SZ}.json"
    path.write_text(
        json.dumps(
            {
                "request": params,
                "fetched_at": fetched_at.isoformat(),
                "http_status": response.status_code,
                "body": response.text,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
