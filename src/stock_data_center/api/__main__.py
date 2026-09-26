"""Serve the API.

    STOCKDC_API_KEY=... DATABASE_URL=... python -m stock_data_center.api [--host ...] [--port 28617]

It listens on every interface by default (owner, 2026-09-25): clients on the
office LAN or the tailnet call it by this machine's address, an office desktop.
`--host 127.0.0.1` keeps it to this machine. Plain HTTP carries the key in
clear on the LAN; Tailscale encrypts it.

The key and the database URL come from the environment, never the command
line, so they stay out of the process list and shell history. In a container
(`docker compose up -d`, `scripts/api_up.sh`) `STOCKDC_GIT_COMMIT` names the
commit the image was built from, and `STOCKDC_WEB_DIR` the built web
dashboard it serves at `/` (ADR-0029); without it no page is served.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import sqlalchemy as sa
import uvicorn

from stock_data_center.api import create_app, read_only

# An uncommon port below Linux's ephemeral range (32768-60999), so an outgoing
# connection never holds it (owner, 2026-09-25: not 8000).
PORT = 28617


def parse(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")  # owner, 2026-09-25
    parser.add_argument("--port", type=int, default=PORT)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse(argv)
    url, key = os.getenv("DATABASE_URL"), os.getenv("STOCKDC_API_KEY")
    if not url or not key:
        sys.exit("DATABASE_URL and STOCKDC_API_KEY must be set")
    engine = sa.create_engine(url, pool_pre_ping=True)
    web = os.getenv("STOCKDC_WEB_DIR")
    app = create_app(api_key=key, connect=lambda: read_only(engine),
                     git_commit=os.getenv("STOCKDC_GIT_COMMIT") or None,
                     web_dir=Path(web) if web else None)
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
