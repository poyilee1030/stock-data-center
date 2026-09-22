"""Materialise a canonical derived dataset's rolling as-of series.

Deliberately not part of the ingestion CLI. Ingestion fetches, stores raw bytes
and normalises; this reads what is already stored and writes a derived result.
Sharing a command would suggest a derived run could claim ingest provenance,
which it never can — `computed_at` is computation provenance, not a publication
or a fetch.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from time import monotonic

import sqlalchemy as sa

from stock_data_center.db.metadata import daily_price_versions, security
from stock_data_center.derived.definitions import (
    TECHNICAL_INDICATORS_V1,
    DerivationRegistry,
)
from stock_data_center.derived.service import INPUT_DATASET, TechnicalIndicatorService


def _securities(connection, *, source: str, start: date, end: date) -> list[str]:
    """Every security with a price in the window, by code.

    Taken from the stored prices rather than from today's universe: a security
    that left the market inside the window still has a series inside it.
    """
    return list(
        connection.scalars(
            sa.select(security.c.security_code)
            .where(
                sa.exists(
                    sa.select(1).where(
                        daily_price_versions.c.security_id == security.c.id,
                        daily_price_versions.c.source == source,
                        daily_price_versions.c.trade_date.between(start, end),
                    )
                )
            )
            .order_by(security.c.security_code)
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stock-data-center-derive")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    materialize = subparsers.add_parser(
        "technical-indicators",
        help="write the rolling as-of series for technical_indicators:v1",
    )
    materialize.add_argument("--source", required=True)
    materialize.add_argument("--start", required=True, help="Gregorian YYYY-MM-DD")
    materialize.add_argument("--end", required=True, help="Gregorian YYYY-MM-DD")
    materialize.add_argument(
        "--security-code",
        action="append",
        help="limit the run to these securities; repeat the flag. The default "
        "is every security with a price in the window",
    )
    materialize.add_argument(
        "--knowledge-as-of",
        help="the evidence cutoff this run reads at, as an ISO instant with an "
        "offset. Defaults to now, which is what a fresh run knows",
    )
    materialize.add_argument(
        "--shard",
        type=int,
        help="process only shard K of --shards, counting from 0. The split is "
        "by position in the sorted security list, so shards are disjoint and "
        "cover everything; running them concurrently is safe because no two "
        "shards write the same row",
    )
    materialize.add_argument("--shards", type=int, help="how many shards in total")
    materialize.add_argument(
        "--progress",
        action="store_true",
        help="one line per security on stderr; a whole-universe run that only "
        "reports at the end reports nothing when it is killed",
    )

    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        parser.error("--end must not be before --start")
    knowledge_as_of = (
        datetime.fromisoformat(args.knowledge_as_of)
        if args.knowledge_as_of
        else None
    )
    if knowledge_as_of is not None and knowledge_as_of.tzinfo is None:
        parser.error("--knowledge-as-of must carry an offset")

    if (args.shard is None) != (args.shards is None):
        parser.error("--shard and --shards are given together or not at all")
    if args.shards is not None and not 0 <= args.shard < args.shards:
        parser.error("--shard must be between 0 and --shards - 1")

    engine = sa.create_engine(args.database_url, pool_pre_ping=True)
    service = TechnicalIndicatorService()
    began = monotonic()
    try:
        with engine.connect() as connection:
            DerivationRegistry().register(connection, TECHNICAL_INDICATORS_V1)
            connection.commit()
            codes = args.security_code or _securities(
                connection, source=args.source, start=start, end=end
            )
        if args.shards is not None:
            codes = codes[args.shard :: args.shards]

        written = 0
        failures: list[dict] = []
        for index, code in enumerate(codes, start=1):
            with engine.connect() as connection:
                try:
                    rows = service.materialize(
                        connection,
                        security_code=code,
                        start_date=start,
                        end_date=end,
                        source=args.source,
                        knowledge_as_of=knowledge_as_of,
                    )
                    connection.commit()
                except Exception as error:  # one security must not end the run
                    connection.rollback()
                    rows = 0
                    failures.append({"security_code": code, "error": str(error)})
            written += rows
            if args.progress:
                print(
                    f"[{index}/{len(codes)}] {code} rows={rows}",
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        engine.dispose()

    print(
        json.dumps(
            {
                "dataset_code": TECHNICAL_INDICATORS_V1.dataset_code,
                "derivation_version": TECHNICAL_INDICATORS_V1.derivation_version,
                "input_dataset": INPUT_DATASET,
                "source": args.source,
                "window": [start.isoformat(), end.isoformat()],
                "shard": (
                    None if args.shards is None else [args.shard, args.shards]
                ),
                "securities": len(codes),
                "metric_rows_written": written,
                "failures": failures,
                "elapsed_seconds": round(monotonic() - began, 1),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
