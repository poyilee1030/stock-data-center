"""Trading-calendar hooks for the shared raw-first import lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine

from stock_data_center.ingestion.adapters.trading_calendar import (
    TradingCalendarAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    ParsedTradingCalendar,
    SourceResource,
    TradingCalendarRequest,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_calendar import (
    TradingCalendarObservation,
    TradingCalendarWriter,
)
from stock_data_center.market_calendar.models import CalendarLineageRef


# CLAUDE.md §34: the market timezone is Asia/Taipei. Whether a month is over
# is a fact about the market, never about the host running the import.
MARKET_TIMEZONE = ZoneInfo("Asia/Taipei")


class TradingCalendarImporter(
    RawFirstImporter[TradingCalendarRequest, ParsedTradingCalendar]
):
    """Import one published month of actual trading days."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        writer: TradingCalendarWriter | None = None,
        today: date | None = None,
    ) -> None:
        super().__init__(engine, raw_store=raw_store, fetcher=fetcher)
        self._writer = writer or TradingCalendarWriter()
        self._today = today

    def _source_scope(
        self,
        adapter: RawFirstAdapter[TradingCalendarRequest, ParsedTradingCalendar],
        request: TradingCalendarRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "month": request.month.isoformat(),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(
        self,
        adapter: RawFirstAdapter[TradingCalendarRequest, ParsedTradingCalendar],
    ) -> Mapping[str, object]:
        if not isinstance(adapter, TradingCalendarAdapter):
            raise TypeError("TradingCalendarImporter requires a TradingCalendarAdapter")
        return {
            "market": adapter.market,
            "trading_days": "actual_open_days_published_for_the_month",
            "closure": "absence_from_the_published_list",
            "partial_month": "coverage_through_bounds_what_the_version_can_answer",
            "publication_time": "unknown",
        }

    def _dataset_description(
        self,
        adapter: RawFirstAdapter[TradingCalendarRequest, ParsedTradingCalendar],
    ) -> str:
        return "official market trading calendar of actual open days"

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter: RawFirstAdapter[TradingCalendarRequest, ParsedTradingCalendar],
        request: TradingCalendarRequest,
        parsed: ParsedTradingCalendar,
        lineage,
    ) -> BusinessWriteResult:
        calendar_lineage = CalendarLineageRef(
            lineage.raw_artifact_id, lineage.ingest_run_id
        )
        coverage_through = self._coverage_through(parsed)
        written = self._writer.append_month(
            connection,
            source=adapter.source,
            observation=TradingCalendarObservation(
                market=parsed.market,
                calendar_month=parsed.month,
                trading_days=parsed.trading_days,
                coverage_through=coverage_through,
            ),
            lineage=calendar_lineage,
        )
        evidence_source = f"{adapter.source} official monthly trading-day report"
        self._writer.append_unknown_publication_evidence(
            connection,
            source=adapter.source,
            version_id=written.version_id,
            evidence_source=evidence_source,
            lineage=calendar_lineage,
        )
        month_end = _month_end(parsed.month)
        return BusinessWriteResult(
            business_versions_created=int(written.created),
            business_versions_deduplicated=int(not written.created),
            publication_evidence_created=int(written.created),
            publication_evidence_deduplicated=int(not written.created),
            evidence_observations=1,
            unknown_publication_observations=1,
            normalized_rows=len(parsed.trading_days),
            coverage_start=parsed.coverage_start,
            coverage_end=parsed.coverage_end,
            reconciliation={
                "requested_month": parsed.month.isoformat(),
                "actual_market": parsed.market,
                "trading_day_count": len(parsed.trading_days),
                "coverage_start": parsed.coverage_start.isoformat(),
                "coverage_end": parsed.coverage_end.isoformat(),
                "coverage_through": coverage_through.isoformat(),
                "month_complete": coverage_through == month_end,
                "coverage_validation": (
                    "complete_month" if coverage_through == month_end
                    else "partial_month"
                ),
                "coverage_gaps": None,
                "closure_semantics": "absence_from_the_published_list",
                "publication_time": "unknown",
                "source_fields": list(parsed.source_fields),
            },
        )

    def _coverage_through(self, parsed: ParsedTradingCalendar) -> date:
        """How far this version can speak for its month.

        A month fetched before it ends publishes a partial list, and the days
        after the last published one are unknown rather than closed. Only a
        month that has already ended may claim its whole month.
        """
        month_end = _month_end(parsed.month)
        today = self._today or datetime.now(MARKET_TIMEZONE).date()
        if today > month_end:
            return month_end
        # Still inside the month: claim only what the source actually showed.
        # Claiming today would read an unpublished day as a closure.
        return parsed.trading_days[-1]


def _month_end(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=7)).replace(day=1) - timedelta(days=1)
