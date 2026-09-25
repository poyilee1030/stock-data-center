#!/usr/bin/env python
"""Steps 27-b and 27-c acceptance: the API against the database behind it.

`unsourced`
    Every column `api.datasets.UNSOURCED` omits for a source holds no value for
    that source: omitting it hides nothing.
`served`
    For every dataset, a query through a running server returns exactly the
    keys and values `visibility.rows` returns for the same PIT context, with
    provenance for every row; the time each request took is reported.
`reports` (27-c)
    A financial-report query returns the versions `visibility.rows` sees, each
    with exactly its own facts from `visibility.report_facts`.
`derived` (27-c)
    For every stored derived dataset, a query returns exactly the stored rows
    whose `available_at` is not after `information_as_of`, at latest and at a
    historical instant; with no corrected input in the database, every row's
    `available_at` must be its own date's release instant.
`pit_reference` (27-c)
    `technical-indicators-pit` rolling equals the stored `technical-indicators`
    within `derived_store.within_tolerance` (exact for the windowed metrics).
`reference_data` (27-c)
    `/v1/stocks` and `/v1/trading-days` return every row of the list and the
    calendar.

Exit 1 if any fails. The server is started by the caller:

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
from stock_data_center.api.derived import DERIVED
from stock_data_center.db import schema_v2 as v2
from stock_data_center.v2 import visibility
from stock_data_center.v2.derived_store import within_tolerance
from stock_data_center.v2.exchange_daily import available_from
from stock_data_center.v2.indicators import METRIC_CODES
from stock_data_center.v2.release_rules import tdcc_available_from

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


# (start, end, stock_id, account_code or None)
REPORT_QUERIES = [
    (date(2020, 1, 1), date(2026, 9, 11), "2330", None),
    (date(2026, 6, 1), date(2026, 6, 30), None, "9750"),
]
# A historical information_as_of for the derived datasets: a row dated after
# it must not be served.
HISTORICAL = datetime.fromisoformat("2024-07-02T12:00:00+08:00")


def _timed(http, path, params):
    began = time.perf_counter()
    response = http.get(path, params=params)
    return response, time.perf_counter() - began


def _reports(c, http, at, failures, report) -> None:
    pit = visibility.MarketPIT(at, at)
    for start, end, stock_id, account_code in REPORT_QUERIES:
        params = {"start": start.isoformat(), "end": end.isoformat(),
                  "information_as_of": at.isoformat(), "knowledge_as_of": at.isoformat()}
        if stock_id:
            params["stock_id"] = stock_id
        if account_code:
            params["account_code"] = account_code
        response, seconds = _timed(http, "/v1/datasets/financial-reports", params)
        if response.status_code != 200:
            failures.append(f"reports: HTTP {response.status_code} {response.text[:200]}")
            continue
        served = json.loads(response.text)["rows"]
        visible = visibility.rows(c, "financial_reports", pit, start=start, end=end,
                                  stock_ids=[stock_id] if stock_id else None)
        facts = visibility.report_facts(c, [r["id"] for r in visible],
                                        account_codes=[account_code] if account_code else None)
        keys = [(r["stock_id"], r["report_year"], r["report_quarter"]) for r in served]
        if keys != [(r["stock_id"], r["report_year"], r["report_quarter"]) for r in visible]:
            failures.append(f"reports {stock_id or account_code}: keys differ")
        n = 0
        for got, want in zip(served, visible, strict=False):
            expected = [(f["statement"], f["concept"], f["period_start"], f["period_end"],
                         f["value"]) for f in facts[want["id"]]]
            actual = [(f["statement"], f["concept"],
                       None if f["period_start"] is None else date.fromisoformat(f["period_start"]),
                       date.fromisoformat(f["period_end"]), Decimal(str(f["value"])))
                      for f in got["facts"]]
            n += len(actual)
            if actual != expected:
                failures.append(f"reports {got['stock_id']} {got['report_year']}Q"
                                f"{got['report_quarter']}: facts differ")
            if got["available_at"] != want["available_at"].isoformat():
                failures.append(f"reports {got['stock_id']}: available_at differs")
        report["reports"].append({"stock_id": stock_id, "account_code": account_code,
                                  "start": start.isoformat(), "end": end.isoformat(),
                                  "reports": len(served), "facts": n,
                                  "seconds": round(seconds, 3), "bytes": len(response.content)})


def _derived(c, http, at, failures, report) -> None:
    for name, dataset in DERIVED.items():
        t = dataset.stored.table
        day_column = t.c[dataset.period]
        released = tdcc_available_from if dataset.period == "snapshot_date" else available_from
        last = c.scalar(sa.select(sa.func.max(day_column)))
        for label, start, end, stock_id, information in (
                ("latest whole market", last, last, None, at),
                ("2330 history, latest", date(2020, 1, 2), last, "2330", at),
                ("2330 history, 2024-07-02 12:00", date(2020, 1, 2), last, "2330", HISTORICAL)):
            params = {"start": start.isoformat(), "end": end.isoformat(),
                      "information_as_of": information.isoformat()}
            if stock_id:
                params["stock_id"] = stock_id
            response, seconds = _timed(http, f"/v1/datasets/{name}", params)
            if response.status_code != 200:
                failures.append(f"{name}: HTTP {response.status_code} {response.text[:200]}")
                continue
            served = json.loads(response.text)["rows"]
            query = sa.select(t).where(day_column.between(start, end)).order_by(
                t.c.stock_id, t.c.source, day_column)
            if stock_id:
                query = query.where(t.c.stock_id == stock_id)
            stored = [r for r in c.execute(query).mappings()
                      if released(r[dataset.period]) <= information]
            if len(served) != len(stored):
                failures.append(f"{name} {label}: {len(served)} served, {len(stored)} expected")
            delayed = 0
            for got, want in zip(served, stored, strict=False):
                if got["available_at"] != released(want[dataset.period]).isoformat():
                    delayed += 1
                for column in dataset.columns:
                    if not _same(got[column], want[column]):
                        failures.append(f"{name} {label}: {column} {got[column]!r} != "
                                        f"{want[column]!r}")
                        break
            if delayed:
                failures.append(f"{name} {label}: {delayed} rows not at their own release, "
                                "though no input has a correction")
            report["derived"].append({"dataset": name, "query": label, "rows": len(served),
                                      "seconds": round(seconds, 3),
                                      "bytes": len(response.content)})


def _pit_reference(c, http, failures, report) -> None:
    t = v2.technical_indicators
    for stock_id in ("2330", "6488"):
        [source] = c.scalars(sa.select(t.c.source).distinct().where(t.c.stock_id == stock_id))
        start, end = date(2020, 1, 2), c.scalar(sa.select(sa.func.max(t.c.trade_date)))
        response, seconds = _timed(http, "/v1/datasets/technical-indicators-pit", {
            "stock_id": stock_id, "start": start.isoformat(), "end": end.isoformat(),
            "view": "rolling"})
        if response.status_code != 200:
            failures.append(f"pit {stock_id}: HTTP {response.status_code} {response.text[:200]}")
            continue
        served = {r["trade_date"]: r for r in json.loads(response.text)["rows"]}
        stored = {r["trade_date"].isoformat(): r for r in c.execute(
            sa.select(t).where(t.c.stock_id == stock_id, t.c.source == source)).mappings()}
        closes = dict(c.execute(sa.select(v2.daily_prices.c.trade_date,
                                          v2.daily_prices.c.close_price)
                                .where(v2.daily_prices.c.stock_id == stock_id,
                                       v2.daily_prices.c.source == source)).all())
        if set(served) != set(stored):
            failures.append(f"pit {stock_id}: {len(served)} dates served, {len(stored)} stored")
        exact = differ = 0
        close = None
        for day in sorted(set(served) & set(stored)):
            close = float(closes[date.fromisoformat(day)] or 0) or close
            for metric in METRIC_CODES:
                got, want = served[day][metric], stored[day][metric]
                if got == want:
                    exact += 1
                elif within_tolerance(metric, want, got, close):
                    differ += 1
                else:
                    failures.append(f"pit {stock_id} {day} {metric}: {got} != {want}")
        report["pit_reference"].append({"stock_id": stock_id, "source": source,
                                        "dates": len(served), "exact": exact,
                                        "within_tolerance": differ,
                                        "seconds": round(seconds, 3)})


def _reference_data(c, http, failures, report) -> None:
    response, seconds = _timed(http, "/v1/stocks", {})
    stocks = json.loads(response.text)["rows"]
    expected = c.scalar(sa.select(sa.func.count()).select_from(v2.stocks))
    if len(stocks) != expected or not all(r["provenance"]["raw_sha256"] for r in stocks):
        failures.append(f"stocks: {len(stocks)} served, {expected} stored")
    first, last = c.execute(sa.select(sa.func.min(v2.trading_days.c.trade_date),
                                      sa.func.max(v2.trading_days.c.trade_date))).one()
    response2, seconds2 = _timed(http, "/v1/trading-days",
                                 {"start": first.isoformat(), "end": last.isoformat()})
    days = json.loads(response2.text)["rows"]
    expected_days = c.scalar(sa.select(sa.func.count()).select_from(v2.trading_days))
    if len(days) != expected_days:
        failures.append(f"trading-days: {len(days)} served, {expected_days} stored")
    report["reference_data"] = {"stocks": len(stocks), "stocks_seconds": round(seconds, 3),
                                "trading_days": len(days), "first": first.isoformat(),
                                "last": last.isoformat(), "days_seconds": round(seconds2, 3)}


def _same(api_value, db_value) -> bool:
    if isinstance(db_value, Decimal):
        return api_value is not None and Decimal(str(api_value)) == db_value
    if isinstance(db_value, datetime | date):
        return api_value == db_value.isoformat()
    if db_value is not None and not isinstance(db_value, int | float | str | bool):
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
    report: dict = {"unsourced": {}, "served": [], "reports": [], "derived": [],
                    "pit_reference": []}
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
            _reports(c, http, at, failures, report)
            _derived(c, http, at, failures, report)
            _pit_reference(c, http, failures, report)
            _reference_data(c, http, failures, report)
    report["failures"] = len(failures)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    for failure in failures[:30]:
        print("FAIL", failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
