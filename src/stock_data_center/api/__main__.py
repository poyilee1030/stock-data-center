"""Serve the API on the loopback interface.

    STOCKDC_API_KEY=... DATABASE_URL=... python -m stock_data_center.api [--port 8000]

The key and the database URL come from the environment, never the command
line, so they stay out of the process list and shell history.
"""

from __future__ import annotations

import argparse
import os
import sys

import sqlalchemy as sa
import uvicorn

from stock_data_center.api import create_app, read_only


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    url, key = os.getenv("DATABASE_URL"), os.getenv("STOCKDC_API_KEY")
    if not url or not key:
        parser.error("DATABASE_URL and STOCKDC_API_KEY must be set")
    engine = sa.create_engine(url, pool_pre_ping=True)
    app = create_app(api_key=key, connect=lambda: read_only(engine))
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
