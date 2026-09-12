"""Command-line entry point for bounded Phase 9 source imports."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa

from stock_data_center.ingestion.adapters import (
    TPExDailyMarketAdapter,
    TPExSecurityMetadataAdapter,
    TWSEDailyMarketAdapter,
    TWSESecurityMetadataAdapter,
)
from stock_data_center.ingestion.daily_market import DailyMarketImporter
from stock_data_center.ingestion.models import (
    DailyMarketRequest,
    SecurityMetadataRequest,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.ingestion.security_metadata import SecurityMetadataImporter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stock-data-center-ingest")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    daily = subparsers.add_parser("daily-market")
    daily.add_argument("--source", choices=("twse", "tpex"), required=True)
    daily.add_argument("--security-code", required=True)
    daily.add_argument("--month", required=True, help="Gregorian YYYY-MM")
    daily.add_argument("--import-id", type=UUID)
    daily.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    security_metadata = subparsers.add_parser("security-metadata")
    security_metadata.add_argument("--source", choices=("twse", "tpex"), required=True)
    security_metadata.add_argument(
        "--expected-report-date",
        type=date.fromisoformat,
        help="optional Gregorian YYYY-MM-DD source-date guard",
    )
    security_metadata.add_argument("--import-id", type=UUID)
    security_metadata.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    import_id = args.import_id or uuid4()
    engine = sa.create_engine(args.database_url, pool_pre_ping=True)
    try:
        if args.command == "daily-market":
            adapter = (
                TWSEDailyMarketAdapter()
                if args.source == "twse"
                else TPExDailyMarketAdapter()
            )
            importer = DailyMarketImporter(
                engine,
                raw_store=LocalRawArtifactStore(args.raw_root),
            )
            result = importer.run(
                adapter=adapter,
                request=DailyMarketRequest(
                    args.security_code, date.fromisoformat(f"{args.month}-01")
                ),
                import_id=import_id,
            )
        else:
            adapter = (
                TWSESecurityMetadataAdapter()
                if args.source == "twse"
                else TPExSecurityMetadataAdapter()
            )
            importer = SecurityMetadataImporter(
                engine,
                raw_store=LocalRawArtifactStore(args.raw_root),
            )
            result = importer.run(
                adapter=adapter,
                request=SecurityMetadataRequest(args.expected_report_date),
                import_id=import_id,
            )
        with engine.connect() as connection:
            manifest = importer.manifest(connection, import_id)
    finally:
        engine.dispose()

    print(
        json.dumps(
            {
                "resource": asdict(result),
                "manifest": {
                    "import_id": manifest.import_id,
                    "status": manifest.status,
                    "result_counts": dict(manifest.result_counts),
                    "reconciliation": dict(manifest.reconciliation),
                },
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
