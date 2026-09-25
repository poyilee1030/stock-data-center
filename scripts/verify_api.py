#!/usr/bin/env python
"""Step 27-b acceptance: the API against the database behind it.

`unsourced`
    Every column `api.datasets.UNSOURCED` omits for a source holds no value for
    that source: omitting it hides nothing.
`served`
    For every dataset, a query through a running server returns exactly the
    keys and values `visibility.rows` returns for the same PIT context, with
    provenance for every row; the time each request took is reported.

Exit 1 if either fails. The server is started by the caller:

    STOCKDC_API_KEY=... DATABASE_URL=... python -m stock_data_center.api &
    STOCKDC_API_KEY=... python scripts/verify_api.py \\
        --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \\
        --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.api.datasets import DATASETS, UNSOURCED
from stock_data_center.v2 import visibility

# (dataset, start, end, stock_id or None): a recent whole-market day where the
# dataset has one, else one stock over its history.
QUERIES = [
    ("daily-prices", date(2026, 9, 11), date(2026, 9, 11), None),
    ("daily-prices", date(2020, 1, 2), date(2026, 9, 11), "2330"),
    ("indices", date(2026, 9, 1), date(2026, 9, 11), None),
    ("official-valuations", date(2026, 9, 11), date(2026, 9, 11), None),
    ("institutional-flows", date(2026, 9, 11), date(2026, 9, 11), None),
    ("institutional-market-flows", date(2026, 8, 1), date(2026, 8, 31), None),
    ("foreign-holdings", date(2026, 9, 11), date(2026, 9, 11), None),
    ("margin-trading", date(2026, 9, 11), date(2026, 9, 11), None),
    ("securities-lending", date(2026, 9, 11), date(2026, 9, 11), None),
    ("shareholding-distributions", date(2026, 9, 11), date(2026, 9, 11), None),
    ("monthly-revenues", date(2020, 1, 1), date(2026, 8, 1), "2330"),
    ("corporate-actions", date(2020, 1, 1), date(2026, 9, 11), "2330"),
]


def _same(api_value, db_value) -> bool:
    if isinstance(db_value, Decimal):
        return api_value is not None and Decimal(str(api_value)) == db_value
    if isinstance(db_value, datetime | date):
        return api_value == db_value.isoformat()
    if db_value is not None and not isinstance(db_value, int | str | bool):
        return api_value == str(db_value)
    return api_value == db_value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    key = os.getenv("STOCKDC_API_KEY")
    if not key:
        parser.error("STOCKDC_API_KEY must be set")
    failures: list[str] = []
    report: dict = {"unsourced": {}, "served": []}
    engine = sa.create_engine(args.database_url)
    with engine.connect() as c:
        for name, by_source in UNSOURCED.items():
            table = DATASETS[name].table
            for source, columns in sorted(by_source.items()):
                counts = c.execute(sa.select(
                    sa.func.count(), *(sa.func.count(table.c[col]) for col in sorted(columns))
                ).where(table.c.source == source)).one()
                rows, values = counts[0], dict(zip(sorted(columns), counts[1:], strict=True))
                report["unsourced"][f"{name}/{source}"] = {"rows": rows, "values": values}
                for col, n in values.items():
                    if n:
                        failures.append(f"unsourced {name}/{source}.{col} has {n} values")

        at = datetime.now(UTC)
        pit = visibility.MarketPIT(at, at)
        with httpx.Client(base_url=args.base_url, headers={"X-API-Key": key},
                          timeout=120) as http:
            for name, start, end, stock_id in QUERIES:
                dataset = DATASETS[name]
                params = {"start": start.isoformat(), "end": end.isoformat(),
                          "information_as_of": at.isoformat(), "knowledge_as_of": at.isoformat()}
                if stock_id:
                    params["stock_id"] = stock_id
                began = time.perf_counter()
                response = http.get(f"/v1/datasets/{name}", params=params)
                seconds = time.perf_counter() - began
                if response.status_code != 200:
                    failures.append(f"{name}: HTTP {response.status_code} {response.text[:200]}")
                    continue
                served = json.loads(response.text)["rows"]
                expected = visibility.rows(c, dataset.table.name, pit, start=start, end=end,
                                           stock_ids=[stock_id] if stock_id else None)
                if len(served) != len(expected):
                    failures.append(f"{name}: {len(served)} rows served, {len(expected)} visible")
                for got, want in zip(served, expected, strict=False):
                    for column, value in got.items():
                        if column == "provenance":
                            if value["fetch_id"] != str(want["fetch_id"]) or not value["raw_sha256"]:
                                failures.append(f"{name}: provenance {value}")
                        elif not _same(value, want[column]):
                            failures.append(f"{name}: {column} {value!r} != {want[column]!r}")
                report["served"].append({
                    "dataset": name, "start": start.isoformat(), "end": end.isoformat(),
                    "stock_id": stock_id, "rows": len(served), "seconds": round(seconds, 3),
                    "bytes": len(response.content)})
    report["failures"] = len(failures)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    for failure in failures[:30]:
        print("FAIL", failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
