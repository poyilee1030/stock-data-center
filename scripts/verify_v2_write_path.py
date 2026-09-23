#!/usr/bin/env python
"""Step 35-b-1 acceptance: live fetches through the v2 write path add no row.

For a few trade dates, copy what `stockdc_backfill` holds in v2 (the migrated
v1 history) into a scratch database, then fetch the same dates live through
every job with `--refetch` semantics. The write path appends only a value that
differs from the key's latest row, so a write path that maps every column the
way the migration did appends nothing; any appended row is printed, key and
columns, for classification. One kind is expected and reported apart: a TAIEX
request is a whole month, so it also returns trade dates later than the
migrated history, which have no stored row to agree with. Those dates have no
MI_INDEX close in the copy either, so the TAIEX check holds them back and the
report counts them `unverified`.

The backfill database is only read. The scratch database is created from the
migration chain and dropped at the end unless `--keep`.

    .venv/bin/python scripts/verify_v2_write_path.py \\
        --date 2020-01-02 --date 2023-06-15 --date 2026-09-11
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from alembic import command
from alembic.config import Config

from stock_data_center.db import schema_v2 as v2
from stock_data_center.ingestion.http import (
    HostRateGovernor,
    HttpSourceFetcher,
    RetryingFetcher,
)
from stock_data_center.ingestion.lifecycle import current_git_commit
from stock_data_center.v2.backfill import HOST_INTERVALS, run
from stock_data_center.v2.exchange_daily import (
    JOBS,
    TAIEX_LIST_NAME,
    key_columns,
    value_columns,
)

SERVER = "postgresql+psycopg://stockdc:stockdc@localhost:5432"
BACKFILL = f"{SERVER}/stockdc_backfill"
SCRATCH_NAME = "stockdc_v2_verify"

VALUE_TABLES = (
    v2.daily_prices, v2.valuations, v2.institutional_flows, v2.institutional_market_flows,
    v2.foreign_holdings, v2.margin_trading, v2.securities_lending, v2.index_prices,
)


def _recreate_scratch() -> str:
    admin = sa.create_engine(f"{SERVER}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {SCRATCH_NAME}")
        connection.exec_driver_sql(f"CREATE DATABASE {SCRATCH_NAME} OWNER stockdc")
    admin.dispose()
    url = f"{SERVER}/{SCRATCH_NAME}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return url


def _drop_scratch() -> None:
    admin = sa.create_engine(f"{SERVER}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {SCRATCH_NAME}")
    admin.dispose()


def _copy(source, target, dates: list[date]) -> dict[str, int]:
    """Stocks, the dates' trading days and value rows, and every fetch they cite."""
    months = sorted({day.replace(day=1) for day in dates})
    selections = {
        v2.stocks: sa.select(v2.stocks),
        v2.trading_days: sa.select(v2.trading_days).where(v2.trading_days.c.trade_date.in_(dates)),
    }
    for table in VALUE_TABLES:
        condition = table.c.trade_date.in_(dates)
        if table is v2.index_prices:
            # A TAIEX request is a whole month; its other dates must be there too,
            # with the MI_INDEX close each is checked against.
            in_month = sa.or_(*(
                sa.func.date_trunc("month", table.c.trade_date) == month for month in months
            ))
            taiex = sa.or_(
                table.c.source == "twse_mi_5mins_hist",
                sa.and_(table.c.source == "twse_mi_index", table.c.index_name == TAIEX_LIST_NAME),
            )
            condition = sa.or_(condition, sa.and_(taiex, in_month))
        selections[table] = sa.select(table).where(condition)

    copied: dict[str, int] = {}
    with source.connect() as reader, target.begin() as writer:
        rows = {table: [dict(r) for r in reader.execute(q).mappings()]
                for table, q in selections.items()}
        fetch_ids = {row["fetch_id"] for batch in rows.values() for row in batch}
        fetch_rows = [dict(r) for r in reader.execute(
            sa.select(v2.fetches).where(v2.fetches.c.id.in_(fetch_ids))).mappings()]
        writer.execute(sa.insert(v2.fetches), fetch_rows)
        copied["fetches"] = len(fetch_rows)
        for table, batch in rows.items():
            if batch:
                writer.execute(sa.insert(table), batch)
            copied[table.name] = len(batch)
    return copied


def _appended(target, since) -> list[dict]:
    """Every value row written by this run, beside the row it differs from."""
    found = []
    with target.connect() as connection:
        for table in VALUE_TABLES:
            keys = key_columns(table)
            values = value_columns(table)
            new = connection.execute(
                sa.select(table).where(table.c.recorded_at >= since)).mappings().all()
            for row in new:
                old = connection.execute(
                    sa.select(table)
                    .where(*(table.c[k] == row[k] for k in keys), table.c.recorded_at < since)
                    .order_by(table.c.recorded_at.desc()).limit(1)
                ).mappings().first()
                found.append({
                    "table": table.name,
                    "key": {k: str(row[k]) for k in keys},
                    "was": None if old is None else {
                        c: str(old[c]) for c in values if old[c] != row[c]},
                    "now": {c: str(row[c]) for c in values
                            if old is None or old[c] != row[c]},
                })
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", action="append", type=date.fromisoformat, required=True)
    parser.add_argument("--keep", action="store_true", help="keep the scratch database")
    args = parser.parse_args()
    dates = sorted(args.date)

    source = sa.create_engine(BACKFILL)
    target = sa.create_engine(_recreate_scratch())
    try:
        copied = _copy(source, target, dates)
        print(json.dumps({"copied": copied}, indent=2), file=sys.stderr)
        with target.connect() as connection:
            since = connection.scalar(sa.select(sa.func.statement_timestamp()))
        began = time.monotonic()
        fetcher = RetryingFetcher(HttpSourceFetcher(governor=HostRateGovernor(HOST_INTERVALS)))
        report = run(
            target, list(JOBS.values()), dates[0], dates[-1],
            fetcher=fetcher, git_commit=current_git_commit(), purpose="correction_check",
            refetch=True,
            progress=lambda key, period, outcome: print(
                f"{key} {period} {outcome.status} +{outcome.appended} "
                f"={outcome.unchanged} {outcome.reason_code or ''}", file=sys.stderr, flush=True),
        )
        appended = _appended(target, since)
        with source.connect() as reader:
            migrated_through = reader.scalar(sa.select(sa.func.max(v2.index_prices.c.trade_date))
                                             .where(v2.index_prices.c.source == "twse_mi_5mins_hist"))
        beyond = [row for row in appended if row["was"] is None
                  and date.fromisoformat(row["key"]["trade_date"]) > migrated_through]
        differing = [row for row in appended if row not in beyond]
        print(json.dumps({
            "dates": [d.isoformat() for d in dates],
            "seconds": round(time.monotonic() - began, 1),
            "report": report,
            "differing_rows": differing,
            "beyond_migrated_history": beyond,
        }, ensure_ascii=False, indent=2))
        failed = any(counts.get("failed") or counts.get("quarantined") for counts in report.values())
        return 1 if differing or failed else 0
    finally:
        target.dispose()
        source.dispose()
        if not args.keep:
            _drop_scratch()


if __name__ == "__main__":
    raise SystemExit(main())
