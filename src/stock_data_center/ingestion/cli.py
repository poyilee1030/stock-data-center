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
    MOPSForeignHoldingAdapter,
    TPExDailyMarketAdapter,
    TPExDelistingHistoryAdapter,
    TPExETFReverseSplitAdapter,
    TPExETFSplitAdapter,
    TPExExRightDailyAdapter,
    TPExInstiQfiiForeignHoldingAdapter,
    TPExInstitutionalInvestorAdapter,
    TPExInstitutionalMarketSummaryAdapter,
    TPExListingHistoryAdapter,
    TPExMarginTradingAdapter,
    TPExMarketIndexAdapter,
    TPExOfficialValuationAdapter,
    TPExParValueChangeAdapter,
    TPExReductionAdapter,
    TPExSecurityMetadataAdapter,
    TPExWholeMarketDailyAdapter,
    TWSEDailyMarketAdapter,
    TWSEDelistingHistoryAdapter,
    TWSEETFSplitAdapter,
    TWSEExRightAdapter,
    TWSEForeignHoldingAdapter,
    TWSEInstitutionalInvestorAdapter,
    TWSEInstitutionalMarketSummaryAdapter,
    TWSEListingHistoryAdapter,
    TWSEMarginTradingAdapter,
    TWSEMarketIndexAdapter,
    TWSEOfficialValuationAdapter,
    TWSEParValueChangeAdapter,
    TWSEReductionAdapter,
    TWSESecurityMetadataAdapter,
    TWSETaiexHistoryAdapter,
    TWSETradingCalendarAdapter,
    TWSEWholeMarketDailyAdapter,
)
from stock_data_center.ingestion.backfill import (
    CorporateActionBackfill,
    WholeMarketDailyBackfill,
    default_base_import_id,
    month_import_id,
)
from stock_data_center.ingestion.corporate_action import CorporateActionImporter
from stock_data_center.ingestion.daily_market import DailyMarketImporter
from stock_data_center.ingestion.foreign_holding import ForeignHoldingImporter
from stock_data_center.ingestion.http import RetryingFetcher
from stock_data_center.ingestion.institutional_investor import (
    InstitutionalInvestorImporter,
)
from stock_data_center.ingestion.institutional_summary import (
    InstitutionalMarketSummaryImporter,
)
from stock_data_center.ingestion.margin_trading import MarginTradingImporter
from stock_data_center.ingestion.market_index import (
    MarketIndexImporter,
    TaiexHistoryImporter,
)
from stock_data_center.ingestion.models import (
    CorporateActionRangeRequest,
    DailyMarketRequest,
    ForeignHoldingRequest,
    IngestPurpose,
    InstitutionalInvestorRequest,
    InstitutionalMarketSummaryRequest,
    MarginTradingRequest,
    MarketIndexRequest,
    OfficialValuationRequest,
    SecurityLifecycleRequest,
    SecurityMetadataRequest,
    TaiexHistoryRequest,
    TradingCalendarRequest,
    WholeMarketDailyRequest,
)
from stock_data_center.ingestion.official_valuation import (
    OfficialValuationImporter,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.ingestion.security_lifecycle import (
    SecurityLifecycleImporter,
    reconcile_security_transfers,
)
from stock_data_center.ingestion.security_metadata import SecurityMetadataImporter
from stock_data_center.ingestion.trading_calendar import TradingCalendarImporter
from stock_data_center.ingestion.whole_market_daily import WholeMarketDailyImporter


def _months(first: date, last: date):
    month = first
    while month <= last:
        yield month
        month = (month.replace(day=28) + timedelta(days=7)).replace(day=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stock-data-center-ingest")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument(
        "--purpose",
        choices=[purpose.value for purpose in IngestPurpose],
        default=IngestPurpose.UNSPECIFIED.value,
        help="why this fetch was requested (ADR-0020); only a first capture "
        "may later claim capture-bound evidence, so this is never inferred. "
        "Re-fetching history published long ago is a gap_fill, not a first "
        "capture",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    daily = subparsers.add_parser("daily-market")
    daily.add_argument("--source", choices=("twse", "tpex"), required=True)
    daily.add_argument("--security-code", required=True)
    daily.add_argument("--month", required=True, help="Gregorian YYYY-MM")
    daily.add_argument("--import-id", type=UUID)
    daily.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    whole_market = subparsers.add_parser(
        "whole-market-daily",
        help="import one market's published quotes for one trade date",
    )
    whole_market.add_argument(
        "--source", choices=("twse_mi_index", "tpex_otc_quotes"), required=True
    )
    whole_market.add_argument(
        "--trade-date", required=True, help="Gregorian YYYY-MM-DD"
    )
    whole_market.add_argument(
        "--through",
        help="optional Gregorian YYYY-MM-DD; import every published trading "
        "date from --trade-date to it, driven by the Step 16 calendar",
    )
    whole_market.add_argument(
        "--min-interval-seconds",
        type=float,
        default=1.5,
        help="throttle between dates of a history run",
    )
    whole_market.add_argument("--import-id", type=UUID)
    whole_market.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    index = subparsers.add_parser(
        "market-index",
        help="import one market's published index closes for one trade date",
    )
    index.add_argument(
        "--source", choices=("twse_mi_index", "tpex_index_summary"), required=True
    )
    index.add_argument("--trade-date", required=True, help="Gregorian YYYY-MM-DD")
    index.add_argument(
        "--through",
        help="optional Gregorian YYYY-MM-DD; import every published trading "
        "date from --trade-date to it, driven by the Step 16 calendar",
    )
    index.add_argument("--min-interval-seconds", type=float, default=1.5)
    index.add_argument("--import-id", type=UUID)
    index.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    valuation = subparsers.add_parser(
        "official-valuation",
        help="import one market's published PE/PB/yield table for one trade date",
    )
    valuation.add_argument(
        "--source", choices=("twse_bwibbu_d", "tpex_pe_qry_date"), required=True
    )
    valuation.add_argument("--trade-date", required=True, help="Gregorian YYYY-MM-DD")
    valuation.add_argument(
        "--through",
        help="optional Gregorian YYYY-MM-DD; import every published trading "
        "date from --trade-date to it, driven by the Step 16 calendar",
    )
    valuation.add_argument("--min-interval-seconds", type=float, default=1.5)
    valuation.add_argument("--import-id", type=UUID)
    valuation.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    flows = subparsers.add_parser(
        "institutional-investor",
        help="import one market's per-security institutional flows for one trade date",
    )
    flows.add_argument(
        "--source", choices=("twse_t86", "tpex_insti_daily_trade"), required=True
    )
    flows.add_argument("--trade-date", required=True, help="Gregorian YYYY-MM-DD")
    flows.add_argument(
        "--through",
        help="optional Gregorian YYYY-MM-DD; import every published trading "
        "date from --trade-date to it, driven by the Step 16 calendar",
    )
    flows.add_argument("--min-interval-seconds", type=float, default=1.5)
    flows.add_argument("--import-id", type=UUID)
    flows.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    summary = subparsers.add_parser(
        "institutional-summary",
        help="import one market's institutional trading-value summary for one trade date",
    )
    summary.add_argument(
        "--source", choices=("twse_bfi82u", "tpex_insti_summary"), required=True
    )
    summary.add_argument("--trade-date", required=True, help="Gregorian YYYY-MM-DD")
    summary.add_argument(
        "--through",
        help="optional Gregorian YYYY-MM-DD; import every published trading "
        "date from --trade-date to it, driven by the Step 16 calendar",
    )
    summary.add_argument("--min-interval-seconds", type=float, default=1.5)
    summary.add_argument("--import-id", type=UUID)
    summary.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    margin = subparsers.add_parser(
        "margin-trading",
        help="import one market's per-security margin trading for one trade date",
    )
    margin.add_argument(
        "--source", choices=("twse_mi_margn", "tpex_margin_balance"), required=True
    )
    margin.add_argument("--trade-date", required=True, help="Gregorian YYYY-MM-DD")
    margin.add_argument(
        "--through",
        help="optional Gregorian YYYY-MM-DD; import every published trading "
        "date from --trade-date to it, driven by the Step 16 calendar",
    )
    margin.add_argument("--min-interval-seconds", type=float, default=1.5)
    margin.add_argument("--import-id", type=UUID)
    margin.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    holding = subparsers.add_parser(
        "foreign-holding",
        help="import one market's per-security foreign holding for one trade date",
    )
    holding.add_argument(
        "--source",
        choices=("twse_mi_qfiis", "mops_t13sa150_otc", "tpex_insti_qfii"),
        required=True,
    )
    holding.add_argument("--trade-date", required=True, help="Gregorian YYYY-MM-DD")
    holding.add_argument(
        "--through",
        help="optional Gregorian YYYY-MM-DD; import every published trading "
        "date from --trade-date to it, driven by the Step 16 calendar",
    )
    # MOPS is paced by the per-host governor (3 s) whatever this says.
    holding.add_argument("--min-interval-seconds", type=float, default=1.5)
    holding.add_argument("--import-id", type=UUID)
    holding.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    taiex = subparsers.add_parser(
        "taiex-history",
        help="import one calendar month of TAIEX open/high/low/close",
    )
    taiex.add_argument("--month", required=True, help="Gregorian YYYY-MM")
    taiex.add_argument(
        "--through", help="optional Gregorian YYYY-MM; import every month to it"
    )
    taiex.add_argument("--min-interval-seconds", type=float, default=1.5)
    taiex.add_argument("--import-id", type=UUID)
    taiex.add_argument("--raw-root", type=Path, default=Path("data/raw"))
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
    corporate_action = subparsers.add_parser(
        "corporate-action",
        help="import one result feed's executed events over a date range",
    )
    corporate_action.add_argument(
        "--feed",
        choices=(
            "TWT49U", "TWTAUU", "TWTB8U", "exDailyQ", "revivt", "pvChgRslt",
            "TWTCAU", "etfSplitRslt", "etfRvsRslt",
        ),
        required=True,
    )
    corporate_action.add_argument(
        "--start", required=True, help="Gregorian YYYY-MM-DD"
    )
    corporate_action.add_argument("--end", required=True, help="Gregorian YYYY-MM-DD")
    corporate_action.add_argument(
        "--executed-through",
        help="optional Gregorian YYYY-MM-DD; rows dated after it are counted, "
        "not turned into events (ADR-0019). Decided when the job is issued, "
        "never from the fetch clock. Defaults to --end. Ignored with "
        "--backfill, where each year's own end is its executed_through.",
    )
    corporate_action.add_argument(
        "--backfill",
        action="store_true",
        help="walk --start..--end one calendar year at a time (ADR-0019: "
        "about 7 requests per feed for 2020-2026), each year its own "
        "checkpoint and import id",
    )
    corporate_action.add_argument(
        "--min-interval-seconds",
        type=float,
        default=1.5,
        help="throttle between years of a --backfill run",
    )
    corporate_action.add_argument(
        "--min-detail-interval-seconds",
        type=float,
        default=1.5,
        help="throttle between a range's per-row detail-page fetches "
        "(TWT49U/TWTAUU); a full-history TWT49U range needs about 7,800",
    )
    corporate_action.add_argument("--import-id", type=UUID)
    corporate_action.add_argument("--raw-root", type=Path, default=Path("data/raw"))
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
        backfill_report = None
        month_failures: list[dict] = []
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
                purpose=IngestPurpose(args.purpose),
            )
        elif args.command == "whole-market-daily":
            importer = WholeMarketDailyImporter(
                engine,
                raw_store=LocalRawArtifactStore(args.raw_root),
            )
            adapter = (
                TWSEWholeMarketDailyAdapter()
                if args.source == "twse_mi_index"
                else TPExWholeMarketDailyAdapter()
            )
            first = date.fromisoformat(args.trade_date)
            if args.through:
                last = date.fromisoformat(args.through)
                if last < first:
                    parser.error("--through must not be before --trade-date")
                # Derived from the scope unless the caller named one, so a run
                # that dies partway resumes by being run again — without the
                # operator having had to keep a UUID from the first attempt.
                base_import_id = args.import_id or default_base_import_id(
                    args.source, first, last
                )
                backfill_report = WholeMarketDailyBackfill(importer).run(
                    adapter=adapter,
                    start=first,
                    end=last,
                    base_import_id=base_import_id,
                    purpose=IngestPurpose(args.purpose),
                    min_interval_seconds=args.min_interval_seconds,
                )
            else:
                result = importer.run(
                    adapter=adapter,
                    request=WholeMarketDailyRequest(first),
                    import_id=import_id,
                    purpose=IngestPurpose(args.purpose),
                )
        elif args.command == "market-index":
            importer = MarketIndexImporter(
                engine, raw_store=LocalRawArtifactStore(args.raw_root)
            )
            adapter = (
                TWSEMarketIndexAdapter()
                if args.source == "twse_mi_index"
                else TPExMarketIndexAdapter()
            )
            first = date.fromisoformat(args.trade_date)
            if args.through:
                last = date.fromisoformat(args.through)
                if last < first:
                    parser.error("--through must not be before --trade-date")
                base_import_id = args.import_id or default_base_import_id(
                    args.source, first, last
                )
                backfill_report = WholeMarketDailyBackfill(
                    importer, request_factory=MarketIndexRequest
                ).run(
                    adapter=adapter,
                    start=first,
                    end=last,
                    base_import_id=base_import_id,
                    purpose=IngestPurpose(args.purpose),
                    min_interval_seconds=args.min_interval_seconds,
                )
            else:
                result = importer.run(
                    adapter=adapter,
                    request=MarketIndexRequest(first),
                    import_id=import_id,
                    purpose=IngestPurpose(args.purpose),
                )
        elif args.command == "official-valuation":
            importer = OfficialValuationImporter(
                engine, raw_store=LocalRawArtifactStore(args.raw_root)
            )
            adapter = (
                TWSEOfficialValuationAdapter()
                if args.source == "twse_bwibbu_d"
                else TPExOfficialValuationAdapter()
            )
            first = date.fromisoformat(args.trade_date)
            if args.through:
                last = date.fromisoformat(args.through)
                if last < first:
                    parser.error("--through must not be before --trade-date")
                base_import_id = args.import_id or default_base_import_id(
                    args.source, first, last
                )
                backfill_report = WholeMarketDailyBackfill(
                    importer, request_factory=OfficialValuationRequest
                ).run(
                    adapter=adapter,
                    start=first,
                    end=last,
                    base_import_id=base_import_id,
                    purpose=IngestPurpose(args.purpose),
                    min_interval_seconds=args.min_interval_seconds,
                )
            else:
                result = importer.run(
                    adapter=adapter,
                    request=OfficialValuationRequest(first),
                    import_id=import_id,
                    purpose=IngestPurpose(args.purpose),
                )
        elif args.command == "institutional-investor":
            importer = InstitutionalInvestorImporter(
                engine, raw_store=LocalRawArtifactStore(args.raw_root)
            )
            adapter = (
                TWSEInstitutionalInvestorAdapter()
                if args.source == "twse_t86"
                else TPExInstitutionalInvestorAdapter()
            )
            first = date.fromisoformat(args.trade_date)
            if args.through:
                last = date.fromisoformat(args.through)
                if last < first:
                    parser.error("--through must not be before --trade-date")
                base_import_id = args.import_id or default_base_import_id(
                    args.source, first, last
                )
                backfill_report = WholeMarketDailyBackfill(
                    importer, request_factory=InstitutionalInvestorRequest
                ).run(
                    adapter=adapter,
                    start=first,
                    end=last,
                    base_import_id=base_import_id,
                    purpose=IngestPurpose(args.purpose),
                    min_interval_seconds=args.min_interval_seconds,
                )
            else:
                result = importer.run(
                    adapter=adapter,
                    request=InstitutionalInvestorRequest(first),
                    import_id=import_id,
                    purpose=IngestPurpose(args.purpose),
                )
        elif args.command == "institutional-summary":
            importer = InstitutionalMarketSummaryImporter(
                engine, raw_store=LocalRawArtifactStore(args.raw_root)
            )
            adapter = (
                TWSEInstitutionalMarketSummaryAdapter()
                if args.source == "twse_bfi82u"
                else TPExInstitutionalMarketSummaryAdapter()
            )
            first = date.fromisoformat(args.trade_date)
            if args.through:
                last = date.fromisoformat(args.through)
                if last < first:
                    parser.error("--through must not be before --trade-date")
                base_import_id = args.import_id or default_base_import_id(
                    args.source, first, last
                )
                backfill_report = WholeMarketDailyBackfill(
                    importer, request_factory=InstitutionalMarketSummaryRequest
                ).run(
                    adapter=adapter,
                    start=first,
                    end=last,
                    base_import_id=base_import_id,
                    purpose=IngestPurpose(args.purpose),
                    min_interval_seconds=args.min_interval_seconds,
                )
            else:
                result = importer.run(
                    adapter=adapter,
                    request=InstitutionalMarketSummaryRequest(first),
                    import_id=import_id,
                    purpose=IngestPurpose(args.purpose),
                )
        elif args.command == "margin-trading":
            importer = MarginTradingImporter(
                engine, raw_store=LocalRawArtifactStore(args.raw_root)
            )
            adapter = (
                TWSEMarginTradingAdapter()
                if args.source == "twse_mi_margn"
                else TPExMarginTradingAdapter()
            )
            first = date.fromisoformat(args.trade_date)
            if args.through:
                last = date.fromisoformat(args.through)
                if last < first:
                    parser.error("--through must not be before --trade-date")
                base_import_id = args.import_id or default_base_import_id(
                    args.source, first, last
                )
                backfill_report = WholeMarketDailyBackfill(
                    importer, request_factory=MarginTradingRequest
                ).run(
                    adapter=adapter,
                    start=first,
                    end=last,
                    base_import_id=base_import_id,
                    purpose=IngestPurpose(args.purpose),
                    min_interval_seconds=args.min_interval_seconds,
                )
            else:
                result = importer.run(
                    adapter=adapter,
                    request=MarginTradingRequest(first),
                    import_id=import_id,
                    purpose=IngestPurpose(args.purpose),
                )
        elif args.command == "foreign-holding":
            importer = ForeignHoldingImporter(
                engine, raw_store=LocalRawArtifactStore(args.raw_root)
            )
            adapter = {
                "twse_mi_qfiis": TWSEForeignHoldingAdapter,
                "mops_t13sa150_otc": MOPSForeignHoldingAdapter,
                "tpex_insti_qfii": TPExInstiQfiiForeignHoldingAdapter,
            }[args.source]()
            first = date.fromisoformat(args.trade_date)
            if args.through:
                last = date.fromisoformat(args.through)
                if last < first:
                    parser.error("--through must not be before --trade-date")
                base_import_id = args.import_id or default_base_import_id(
                    args.source, first, last
                )
                backfill_report = WholeMarketDailyBackfill(
                    importer, request_factory=ForeignHoldingRequest
                ).run(
                    adapter=adapter,
                    start=first,
                    end=last,
                    base_import_id=base_import_id,
                    purpose=IngestPurpose(args.purpose),
                    min_interval_seconds=args.min_interval_seconds,
                )
            else:
                result = importer.run(
                    adapter=adapter,
                    request=ForeignHoldingRequest(first),
                    import_id=import_id,
                    purpose=IngestPurpose(args.purpose),
                )
        elif args.command == "taiex-history":
            importer = TaiexHistoryImporter(
                engine, raw_store=LocalRawArtifactStore(args.raw_root)
            )
            first = date.fromisoformat(f"{args.month}-01")
            last = (
                date.fromisoformat(f"{args.through}-01") if args.through else first
            )
            if last < first:
                parser.error("--through must not be before --month")
            months = list(_months(first, last))
            # Derived, so a run that dies partway resumes by being run again.
            base_id = args.import_id or default_base_import_id(
                "twse_mi_5mins_hist", first, last
            )
            calendar_runs = []
            fetched_last = False
            for month in months:
                if fetched_last:
                    time.sleep(args.min_interval_seconds)
                month_id = month_import_id(base_id, month)
                try:
                    result = importer.run(
                        adapter=TWSETaiexHistoryAdapter(),
                        request=TaiexHistoryRequest(month),
                        import_id=month_id,
                        purpose=IngestPurpose(args.purpose),
                    )
                except Exception as error:  # noqa: BLE001 - reported, not swallowed
                    # One unreachable month says nothing about the next, and
                    # ending the run would discard every month that worked.
                    month_failures.append(
                        {
                            "month": f"{month:%Y-%m}",
                            "import_id": str(month_id),
                            "detail": f"{type(error).__name__}: {error}",
                        }
                    )
                    fetched_last = True
                    continue
                fetched_last = not result.resumed_from_checkpoint
                calendar_runs.append((month, month_id, result))
                import_id = month_id
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
                    purpose=IngestPurpose(args.purpose),
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
                purpose=IngestPurpose(args.purpose),
            )
        elif args.command == "corporate-action":
            feed_adapters = {
                "TWT49U": TWSEExRightAdapter,
                "TWTAUU": TWSEReductionAdapter,
                "TWTB8U": TWSEParValueChangeAdapter,
                "exDailyQ": TPExExRightDailyAdapter,
                "revivt": TPExReductionAdapter,
                "pvChgRslt": TPExParValueChangeAdapter,
                "TWTCAU": TWSEETFSplitAdapter,
                "etfSplitRslt": TPExETFSplitAdapter,
                "etfRvsRslt": TPExETFReverseSplitAdapter,
            }
            importer = CorporateActionImporter(
                engine, raw_store=LocalRawArtifactStore(args.raw_root),
                # A full-history run makes thousands of requests; capped so
                # one transient response costs seconds, not a rerun.
                fetcher=RetryingFetcher(
                    attempts=8, backoff_seconds=lambda attempt: min(2.0 * 2**attempt, 30.0)
                ),
                min_detail_interval_seconds=args.min_detail_interval_seconds,
            )
            start = date.fromisoformat(args.start)
            end = date.fromisoformat(args.end)
            if end < start:
                parser.error("--end must not be before --start")
            if args.backfill:
                adapter = feed_adapters[args.feed]()
                base_import_id = args.import_id or default_base_import_id(
                    adapter.source, start, end
                )
                backfill_report = CorporateActionBackfill(importer).run(
                    adapter=adapter,
                    start=start,
                    end=end,
                    base_import_id=base_import_id,
                    purpose=IngestPurpose(args.purpose),
                    min_interval_seconds=args.min_interval_seconds,
                )
            else:
                executed_through = (
                    date.fromisoformat(args.executed_through)
                    if args.executed_through
                    else end
                )
                result = importer.run(
                    adapter=feed_adapters[args.feed](),
                    request=CorporateActionRangeRequest(start, end, executed_through),
                    import_id=import_id,
                    purpose=IngestPurpose(args.purpose),
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
                purpose=IngestPurpose(args.purpose),
            )
        if backfill_report is not None:
            # A range run has one manifest per date, so the run reports itself:
            # every failure is named, and the exit code follows.
            print(
                json.dumps(
                    {
                        "backfill": {
                            **backfill_report.as_dict(),
                            "base_import_id": str(base_import_id),
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0 if backfill_report.is_complete else 1

        if month_failures and not calendar_runs:
            # Every month failed, so there is no manifest to read and no
            # resource to describe — but this is precisely the run whose report
            # matters. Name the failures and stop.
            print(
                json.dumps(
                    {"month_failures": month_failures},
                    ensure_ascii=False, indent=2, default=str,
                )
            )
            return 1

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

    if month_failures:
        # Non-zero, so a scripted run notices; the successful months still
        # imported and are reported above.
        exit_code = 1
    else:
        exit_code = 0
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
                **({"month_failures": month_failures} if month_failures else {}),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
