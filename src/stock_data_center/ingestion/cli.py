"""Command-line entry point for bounded Phase 9 source imports."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import sqlalchemy as sa

from stock_data_center.ingestion.adapters import (
    TPExDailyMarketAdapter,
    TPExDelistingHistoryAdapter,
    TPExListingHistoryAdapter,
    TPExSecurityMetadataAdapter,
    TWSEDailyMarketAdapter,
    TWSEDelistingHistoryAdapter,
    TWSEListingHistoryAdapter,
    TWSESecurityMetadataAdapter,
    TWSETradingCalendarAdapter,
)
from stock_data_center.ingestion.daily_market import DailyMarketImporter
from stock_data_center.ingestion.models import (
    DailyMarketRequest,
    SecurityLifecycleRequest,
    SecurityMetadataRequest,
    TradingCalendarRequest,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.ingestion.security_lifecycle import (
    SecurityLifecycleImporter,
    reconcile_security_transfers,
)
from stock_data_center.ingestion.security_metadata import SecurityMetadataImporter
from stock_data_center.ingestion.trading_calendar import TradingCalendarImporter


def _months(first: date, last: date):
    month = first
    while month <= last:
        yield month
        month = (month.replace(day=28) + timedelta(days=7)).replace(day=1)


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
    security_history = subparsers.add_parser("security-history")
    security_history.add_argument("--source", choices=("twse", "tpex"), required=True)
    security_history.add_argument(
        "--event", choices=("listing", "delisting"), required=True
    )
    security_history.add_argument(
        "--year",
        type=int,
        help="required for TPEx; TWSE official resources are whole-history tables",
    )
    security_history.add_argument("--import-id", type=UUID)
    security_history.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    calendar = subparsers.add_parser(
        "trading-calendar",
        help="import one month of actual trading days",
    )
    calendar.add_argument("--source", choices=("twse",), default="twse")
    calendar.add_argument("--month", required=True, help="Gregorian YYYY-MM")
    calendar.add_argument(
        "--through",
        help="optional Gregorian YYYY-MM; import every month from --month to it",
    )
    calendar.add_argument(
        "--min-interval-seconds",
        type=float,
        default=1.5,
        help="throttle between months of a history run",
    )
    calendar.add_argument("--import-id", type=UUID)
    calendar.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    subparsers.add_parser(
        "security-transfer-reconciliation",
        help="recompute final transfer matching from canonical TWSE/TPEx histories",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    engine = sa.create_engine(args.database_url, pool_pre_ping=True)
    try:
        if args.command == "security-transfer-reconciliation":
            with engine.connect() as connection:
                reconciliation = reconcile_security_transfers(connection)
            print(
                json.dumps(
                    {"reconciliation": asdict(reconciliation)},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0

        import_id = args.import_id or uuid4()
        calendar_runs: list = []
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
        elif args.command == "trading-calendar":
            importer = TradingCalendarImporter(
                engine,
                raw_store=LocalRawArtifactStore(args.raw_root),
            )
            first = date.fromisoformat(f"{args.month}-01")
            last = (
                date.fromisoformat(f"{args.through}-01")
                if args.through
                else first
            )
            if last < first:
                parser.error("--through must not be before --month")
            months = list(_months(first, last))
            base_id = import_id
            calendar_runs = []
            for index, month in enumerate(months):
                if index:
                    time.sleep(args.min_interval_seconds)
                # One import id per month, derived from the run id, so each
                # month resumes on its own and a run that fails midway
                # continues with the rest instead of restarting.
                import_id = (
                    base_id if len(months) == 1 else uuid5(base_id, str(month))
                )
                result = importer.run(
                    adapter=TWSETradingCalendarAdapter(),
                    request=TradingCalendarRequest(month),
                    import_id=import_id,
                )
                calendar_runs.append((month, import_id, result))
        elif args.command == "security-metadata":
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
        else:
            adapters = {
                ("twse", "listing"): TWSEListingHistoryAdapter,
                ("twse", "delisting"): TWSEDelistingHistoryAdapter,
                ("tpex", "listing"): TPExListingHistoryAdapter,
                ("tpex", "delisting"): TPExDelistingHistoryAdapter,
            }
            if args.source == "tpex" and args.year is None:
                parser.error("security-history --source tpex requires --year")
            if args.source == "twse" and args.year is not None:
                parser.error("security-history --source twse does not accept --year")
            adapter = adapters[(args.source, args.event)]()
            importer = SecurityLifecycleImporter(
                engine,
                raw_store=LocalRawArtifactStore(args.raw_root),
            )
            result = importer.run(
                adapter=adapter,
                request=SecurityLifecycleRequest(args.year),
                import_id=import_id,
            )
        with engine.connect() as connection:
            manifest = importer.manifest(connection, import_id)
            months_report = [
                {
                    "month": f"{month:%Y-%m}",
                    "import_id": str(month_id),
                    "status": importer.manifest(connection, month_id).status,
                    "trading_days": month_result.normalized_rows,
                    "created": month_result.business_versions_created,
                    "deduplicated": month_result.business_versions_deduplicated,
                    "resumed": month_result.resumed_from_checkpoint,
                }
                for month, month_id, month_result in calendar_runs
            ]
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
                # A history run reports every month it imported, not only the
                # last: an earlier month's warnings are the point of running it.
                **({"months": months_report} if months_report else {}),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
