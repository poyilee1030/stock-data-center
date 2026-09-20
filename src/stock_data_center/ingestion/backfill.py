"""Walk a date range of whole-market daily prices, resumably and politely.

The window is about 1,627 trading dates per market. Three things follow from
that size. The calendar decides which dates to ask for, so a closure is never
requested and never looks like a gap. Each date carries its own import id, so a
run that dies on date 900 resumes at date 900 rather than at the beginning. And
one bad date is reported rather than fatal, because ending the run would throw
away the 899 that worked.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid5

from stock_data_center.coverage import ExpectedCoverageService
from stock_data_center.ingestion.adapters.corporate_action import (
    CorporateActionListAdapter,
)
from stock_data_center.ingestion.adapters.monthly_revenue import (
    MOPSMonthlyRevenueAdapter,
)
from stock_data_center.ingestion.adapters.whole_market_daily import (
    WholeMarketDailyAdapter,
)
from stock_data_center.ingestion.corporate_action import CorporateActionImporter
from stock_data_center.ingestion.models import (
    CorporateActionRangeRequest,
    MonthlyRevenueRequest,
    ResourceQuarantinedError,
    RevenuePage,
    SourceDataError,
    WholeMarketDailyRequest,
)
from stock_data_center.ingestion.monthly_revenue import MonthlyRevenueImporter
from stock_data_center.ingestion.whole_market_daily import WholeMarketDailyImporter
from stock_data_center.market_calendar import TradingCalendarService
from stock_data_center.monthly_revenue.models import RevenuePeriod
from stock_data_center.provenance import IngestPurpose


@dataclass(frozen=True, slots=True)
class BackfillDateResult:
    """What happened to one trade date."""

    trade_date: date
    import_id: UUID
    status: str
    rows: int = 0
    created: int = 0
    deduplicated: int = 0
    reason_code: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class BackfillReport:
    source: str
    market: str
    calendar_market: str
    start: date
    end: date
    requested: tuple[date, ...]
    non_trading_days: tuple[date, ...]
    results: tuple[BackfillDateResult, ...]

    @property
    def imported(self) -> int:
        return sum(1 for item in self.results if item.status == "imported")

    @property
    def resumed(self) -> int:
        return sum(1 for item in self.results if item.status == "resumed")

    @property
    def failed(self) -> int:
        return sum(1 for item in self.results if item.status == "failed")

    @property
    def rows(self) -> int:
        return sum(item.rows for item in self.results)

    @property
    def created(self) -> int:
        return sum(item.created for item in self.results)

    @property
    def deduplicated(self) -> int:
        return sum(item.deduplicated for item in self.results)

    @property
    def is_complete(self) -> bool:
        return self.failed == 0 and len(self.results) == len(self.requested)

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "market": self.market,
            "calendar_market": self.calendar_market,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "requested_dates": len(self.requested),
            "non_trading_days": len(self.non_trading_days),
            "imported": self.imported,
            "resumed": self.resumed,
            "failed": self.failed,
            "rows": self.rows,
            "business_versions_created": self.created,
            "business_versions_deduplicated": self.deduplicated,
            "is_complete": self.is_complete,
            "failures": [
                {
                    "trade_date": item.trade_date.isoformat(),
                    "import_id": str(item.import_id),
                    "reason_code": item.reason_code,
                    "detail": item.detail,
                }
                for item in self.results
                if item.status == "failed"
            ],
        }


class WholeMarketDailyBackfill:
    """Import every published trade date in a range, one request per date."""

    def __init__(
        self,
        importer: WholeMarketDailyImporter,
        *,
        calendar: TradingCalendarService | None = None,
        expected: ExpectedCoverageService | None = None,
        sleep: Callable[[float], None] = time.sleep,
        request_factory: Callable[[date], object] = WholeMarketDailyRequest,
    ) -> None:
        self._importer = importer
        self._calendar = calendar or TradingCalendarService()
        self._expected = expected or ExpectedCoverageService(self._calendar)
        self._sleep = sleep
        # Every trading-date dataset walks the same range the same way; only
        # the request type differs. Step 18-b reuses this for market indices.
        self._request_factory = request_factory

    def run(
        self,
        *,
        adapter: WholeMarketDailyAdapter,
        start: date,
        end: date,
        base_import_id: UUID,
        purpose: IngestPurpose = IngestPurpose.UNSPECIFIED,
        min_interval_seconds: float = 1.5,
        git_commit: str | None = None,
    ) -> BackfillReport:
        if start > end:
            raise ValueError("start must not be after end")
        with self._importer.engine.connect() as connection:
            # Which calendar a market follows is the declaration's to say, not
            # this runner's. No official TPEx calendar exists, so TPEx datasets
            # declare the TWSE one; asking for a `TPEx` calendar finds nothing.
            declaration = self._expected.declaration(
                connection,
                dataset_code=adapter.dataset_code,
                market=adapter.market,
            )
            # Checked before the calendar, because the declaration is the more
            # specific authority on what this dataset covers. Importing outside
            # it writes dates the coverage validator permanently reports as
            # `unexpected`, so `is_complete` could never become true again.
            # Refused rather than silently narrowed: a caller who wants more
            # history should widen the declaration, which is the same change
            # that makes the coverage report agree.
            if start < declaration.window_start or (
                declaration.window_end is not None and end > declaration.window_end
            ):
                raise ValueError(
                    f"{start.isoformat()}..{end.isoformat()} reaches outside the "
                    f"declared coverage window for {adapter.dataset_code}/"
                    f"{adapter.market} "
                    f"({declaration.window_start.isoformat()}.."
                    f"{declaration.window_end.isoformat() if declaration.window_end else 'open'})"
                )
            # Raises rather than answering if the calendar has not imported the
            # range: an unimported month and a month of closures are
            # indistinguishable in the data, and finding that out after 3,300
            # requests is finding it out too late.
            open_days = self._calendar.trading_days(
                connection,
                market=declaration.calendar_market,
                start=start,
                end=end,
            )
        open_set = set(open_days)
        closed = tuple(
            day for day in _days_between(start, end) if day not in open_set
        )

        results: list[BackfillDateResult] = []
        requested_source = False
        for trade_date in open_days:
            # Throttle what the source feels, not what the checkpoint table
            # does. A resumed date makes no request, so waiting after it buys
            # nothing and costs the whole window: retrying a handful of failed
            # dates would otherwise sleep once per finished date.
            if requested_source:
                self._sleep(min_interval_seconds)
            result = self._one_date(
                adapter=adapter,
                trade_date=trade_date,
                base_import_id=base_import_id,
                purpose=purpose,
                git_commit=git_commit,
            )
            requested_source = result.status != "resumed"
            results.append(result)
        return BackfillReport(
            source=adapter.source,
            market=adapter.market,
            calendar_market=declaration.calendar_market,
            start=start,
            end=end,
            requested=open_days,
            non_trading_days=closed,
            results=tuple(results),
        )

    def _one_date(
        self,
        *,
        adapter: WholeMarketDailyAdapter,
        trade_date: date,
        base_import_id: UUID,
        purpose: IngestPurpose,
        git_commit: str | None,
    ) -> BackfillDateResult:
        # Derived, not random: the same run resumes onto the same checkpoints,
        # and two different runs never collide on one.
        import_id = date_import_id(base_import_id, adapter.source, trade_date)
        try:
            result = self._importer.run(
                adapter=adapter,
                request=self._request_factory(trade_date),
                import_id=import_id,
                purpose=purpose,
                git_commit=git_commit,
            )
        except ResourceQuarantinedError as error:
            # The raw artifact is kept and the reason code is on the quarantine
            # row; the run carries on, because one unreadable date says nothing
            # about the next one.
            return BackfillDateResult(
                trade_date=trade_date,
                import_id=import_id,
                status="failed",
                reason_code=_reason_code(error),
                detail=str(error),
            )
        except Exception as error:  # noqa: BLE001 - reported, not swallowed
            return BackfillDateResult(
                trade_date=trade_date,
                import_id=import_id,
                status="failed",
                reason_code="operational_error",
                detail=f"{type(error).__name__}: {error}",
            )
        return BackfillDateResult(
            trade_date=trade_date,
            import_id=import_id,
            status="resumed" if result.resumed_from_checkpoint else "imported",
            rows=result.normalized_rows,
            created=result.business_versions_created,
            deduplicated=result.business_versions_deduplicated,
        )


# A fixed namespace, so a run's identity is a function of what it covers rather
# than of when it was launched.
_BACKFILL_NAMESPACE = UUID("6f9f4e2c-77a1-4b3e-9d51-0c2a8f3b6e41")


def default_base_import_id(source: str, start: date, end: date) -> UUID:
    """The run identity for one (source, range), derived rather than minted.

    Resume must not depend on the caller having kept a random UUID. A minted id
    makes every invocation a different run, so a 1,627-date backfill killed
    partway and restarted plainly would re-fetch everything it had already
    finished and write a second full set of runs, checkpoints and manifests.
    Deriving it from the scope makes resuming what happens by default; a caller
    who genuinely wants a separate run passes its own id.
    """
    return uuid5(_BACKFILL_NAMESPACE, f"{source}:{start.isoformat()}:{end.isoformat()}")


def date_import_id(base: UUID, source: str, trade_date: date) -> UUID:
    return uuid5(base, f"{source}:{trade_date.isoformat()}")


def month_import_id(base: UUID, month: date) -> UUID:
    """The per-month equivalent, for the loops that fetch a month at a time.

    Same reason as `date_import_id`: a run that dies at month 56 of 81 has to
    resume by being run again, which it cannot do if its base id was minted.
    """
    return uuid5(base, month.isoformat())


def year_import_id(base: UUID, source: str, year: int) -> UUID:
    """The per-year equivalent, for a result feed's `(feed, year)` requests
    (ADR-0019: about 7 requests per feed for 2020-2026)."""
    return uuid5(base, f"{source}:{year}")


@dataclass(frozen=True, slots=True)
class BackfillYearResult:
    """What happened to one calendar year's range request."""

    year: int
    start: date
    end: date
    import_id: UUID
    status: str
    rows: int = 0
    created: int = 0
    deduplicated: int = 0
    row_quarantined: int = 0
    reason_code: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CorporateActionBackfillReport:
    source: str
    feed: str
    start: date
    end: date
    results: tuple[BackfillYearResult, ...]

    @property
    def imported(self) -> int:
        return sum(1 for item in self.results if item.status == "imported")

    @property
    def resumed(self) -> int:
        return sum(1 for item in self.results if item.status == "resumed")

    @property
    def failed(self) -> int:
        return sum(1 for item in self.results if item.status == "failed")

    @property
    def rows(self) -> int:
        return sum(item.rows for item in self.results)

    @property
    def created(self) -> int:
        return sum(item.created for item in self.results)

    @property
    def deduplicated(self) -> int:
        return sum(item.deduplicated for item in self.results)

    @property
    def row_quarantined(self) -> int:
        return sum(item.row_quarantined for item in self.results)

    @property
    def is_complete(self) -> bool:
        return self.failed == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "feed": self.feed,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "years_requested": len(self.results),
            "imported": self.imported,
            "resumed": self.resumed,
            "failed": self.failed,
            "rows": self.rows,
            "business_versions_created": self.created,
            "business_versions_deduplicated": self.deduplicated,
            # A quarantined row is not a range failure (ADR-0022 §8) — it
            # can be a stable, expected domain fact (`no_data_for_date`) —
            # so it does not affect `is_complete`. It is still surfaced
            # here, per year, so CLAUDE.md §78/§79's "quarantined records
            # reported" is satisfied at this report's own top level, not
            # only inside each year's own manifest.
            "row_quarantined_count": self.row_quarantined,
            "is_complete": self.is_complete,
            "failures": [
                {
                    "year": item.year,
                    "import_id": str(item.import_id),
                    "reason_code": item.reason_code,
                    "detail": item.detail,
                }
                for item in self.results
                if item.status == "failed"
            ],
            "row_quarantines": [
                {"year": item.year, "row_quarantined_count": item.row_quarantined}
                for item in self.results
                if item.row_quarantined
            ],
        }


class CorporateActionBackfill:
    """Import every calendar year one result feed's range covers.

    Chunked by year, not requested whole: the source answers a whole
    2020-2026 range in one call, but writing 7,800 detail pages' worth of
    events into one transaction, or losing all of it to one crash 6,000
    requests in, is worse than seven smaller ones with their own checkpoints
    and their own visible progress.
    """

    def __init__(
        self,
        importer: CorporateActionImporter,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._importer = importer
        self._sleep = sleep

    def run(
        self,
        *,
        adapter: CorporateActionListAdapter,
        start: date,
        end: date,
        base_import_id: UUID,
        purpose: IngestPurpose = IngestPurpose.UNSPECIFIED,
        min_interval_seconds: float = 1.5,
        git_commit: str | None = None,
    ) -> CorporateActionBackfillReport:
        if start > end:
            raise ValueError("start must not be after end")
        results: list[BackfillYearResult] = []
        requested_source = False
        for year in range(start.year, end.year + 1):
            year_start = max(start, date(year, 1, 1))
            year_end = min(end, date(year, 12, 31))
            if requested_source:
                self._sleep(min_interval_seconds)
            result = self._one_year(
                adapter=adapter,
                year=year,
                year_start=year_start,
                year_end=year_end,
                base_import_id=base_import_id,
                purpose=purpose,
                git_commit=git_commit,
            )
            requested_source = result.status != "resumed"
            results.append(result)
        return CorporateActionBackfillReport(
            source=adapter.source,
            feed=adapter.feed,
            start=start,
            end=end,
            results=tuple(results),
        )

    def _one_year(
        self,
        *,
        adapter: CorporateActionListAdapter,
        year: int,
        year_start: date,
        year_end: date,
        base_import_id: UUID,
        purpose: IngestPurpose,
        git_commit: str | None,
    ) -> BackfillYearResult:
        import_id = year_import_id(base_import_id, adapter.source, year)
        try:
            result = self._importer.run(
                adapter=adapter,
                request=CorporateActionRangeRequest(year_start, year_end, year_end),
                import_id=import_id,
                purpose=purpose,
                git_commit=git_commit,
            )
        except ResourceQuarantinedError as error:
            return BackfillYearResult(
                year=year, start=year_start, end=year_end, import_id=import_id,
                status="failed", reason_code=_reason_code(error), detail=str(error),
            )
        except Exception as error:  # noqa: BLE001 - reported, not swallowed
            return BackfillYearResult(
                year=year, start=year_start, end=year_end, import_id=import_id,
                status="failed", reason_code="operational_error",
                detail=f"{type(error).__name__}: {error}",
            )
        # `ResourceImportResult` carries no reconciliation detail of its
        # own; the per-row quarantine count (ADR-0022 §8) only exists on
        # the manifest `_write_business` wrote it into.
        with self._importer._engine.connect() as connection:
            manifest = self._importer.manifest(connection, import_id)
        return BackfillYearResult(
            year=year, start=year_start, end=year_end, import_id=import_id,
            status="resumed" if result.resumed_from_checkpoint else "imported",
            rows=result.normalized_rows,
            created=result.business_versions_created,
            deduplicated=result.business_versions_deduplicated,
            row_quarantined=manifest.reconciliation.get("row_quarantined_count", 0),
        )


def _reason_code(error: ResourceQuarantinedError) -> str | None:
    cause = error.__cause__
    if isinstance(cause, SourceDataError):
        return cause.reason_code
    return None


def _days_between(start: date, end: date) -> list[date]:
    from datetime import timedelta

    span = (end - start).days
    return [start + timedelta(days=offset) for offset in range(span + 1)]


def revenue_page_import_id(
    base: UUID, source: str, period: RevenuePeriod, page: RevenuePage
) -> UUID:
    """The per-(month, page) equivalent, for the monthly-revenue walk.

    Same reason as `date_import_id`: 80 months × 2 pages is 160 checkpoints
    per market, and a run that dies at month 56 resumes by being run again.
    """
    return uuid5(base, f"{source}:{period.year:04d}-{period.month:02d}:{page.value}")


@dataclass(frozen=True, slots=True)
class RevenuePageResult:
    """What happened to one market-month's page."""

    period: RevenuePeriod
    page: RevenuePage
    import_id: UUID
    status: str
    rows: int = 0
    created: int = 0
    deduplicated: int = 0
    reason_code: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class MonthlyRevenueBackfillReport:
    source: str
    market: str
    start: RevenuePeriod
    end: RevenuePeriod
    pages: tuple[RevenuePage, ...]
    results: tuple[RevenuePageResult, ...]

    @property
    def imported(self) -> int:
        return sum(1 for item in self.results if item.status == "imported")

    @property
    def resumed(self) -> int:
        return sum(1 for item in self.results if item.status == "resumed")

    @property
    def failed(self) -> int:
        return sum(1 for item in self.results if item.status == "failed")

    @property
    def no_data(self) -> int:
        return sum(1 for item in self.results if item.status == "no_data")

    @property
    def rows(self) -> int:
        return sum(item.rows for item in self.results)

    @property
    def created(self) -> int:
        return sum(item.created for item in self.results)

    @property
    def deduplicated(self) -> int:
        return sum(item.deduplicated for item in self.results)

    @property
    def is_complete(self) -> bool:
        """Every page asked for was stored.

        A `no_data` page counts against it as a failure does. The two are
        reported apart because only one of them a rerun can fix, but neither
        is coverage, and a walk that silently called a missing month complete
        would be the coverage gap Step 16 exists to surface.
        """
        return all(item.status in ("imported", "resumed") for item in self.results)

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "market": self.market,
            "start": f"{self.start.year:04d}-{self.start.month:02d}",
            "end": f"{self.end.year:04d}-{self.end.month:02d}",
            "pages": [page.value for page in self.pages],
            "requested_pages": len(self.results),
            "imported": self.imported,
            "resumed": self.resumed,
            "failed": self.failed,
            "no_data": self.no_data,
            "rows": self.rows,
            "business_versions_created": self.created,
            "business_versions_deduplicated": self.deduplicated,
            "is_complete": self.is_complete,
            "failures": [
                {
                    "period": f"{item.period.year:04d}-{item.period.month:02d}",
                    "page": item.page.value,
                    "import_id": str(item.import_id),
                    "reason_code": item.reason_code,
                    "detail": item.detail,
                }
                for item in self.results
                if item.status == "failed"
            ],
            "no_data_pages": [
                {
                    "period": f"{item.period.year:04d}-{item.period.month:02d}",
                    "page": item.page.value,
                    "reason_code": item.reason_code,
                }
                for item in self.results
                if item.status == "no_data"
            ],
        }


class MonthlyRevenueBackfill:
    """Import every month of one market's revenue history, page by page.

    No calendar is consulted: a monthly filing is due on a day of the month,
    not on a trading day, and the declaration says so with
    `cadence = 'calendar_month'`. What the declaration does decide, exactly as
    it does for the daily walks, is the window — importing outside it writes
    months the coverage validator would report as `unexpected` forever.
    """

    def __init__(
        self,
        importer: MonthlyRevenueImporter,
        *,
        expected: ExpectedCoverageService | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._importer = importer
        self._expected = expected or ExpectedCoverageService()
        self._sleep = sleep

    def run(
        self,
        *,
        adapter: MOPSMonthlyRevenueAdapter,
        start: RevenuePeriod,
        end: RevenuePeriod,
        base_import_id: UUID,
        purpose: IngestPurpose = IngestPurpose.UNSPECIFIED,
        min_interval_seconds: float = 3.0,
        pages: tuple[RevenuePage, ...] | None = None,
        git_commit: str | None = None,
    ) -> MonthlyRevenueBackfillReport:
        if start > end:
            raise ValueError("start must not be after end")
        wanted = tuple(pages) if pages else (RevenuePage.DOMESTIC, RevenuePage.FOREIGN)
        with self._importer.engine.connect() as connection:
            declaration = self._expected.declaration(
                connection,
                dataset_code=adapter.dataset_code,
                market=adapter.coverage_market,
            )
        first = date(start.year, start.month, 1)
        last = date(end.year, end.month, 1)
        if first < declaration.window_start or (
            declaration.window_end is not None and last > declaration.window_end
        ):
            raise ValueError(
                f"{first.isoformat()}..{last.isoformat()} reaches outside the "
                f"declared coverage window for {adapter.dataset_code}/"
                f"{adapter.coverage_market} "
                f"({declaration.window_start.isoformat()}.."
                f"{declaration.window_end.isoformat() if declaration.window_end else 'open'})"
            )

        results: list[RevenuePageResult] = []
        requested_source = False
        for period in _periods_between(start, end):
            for page in wanted:
                # Throttled on what the host feels, not on what the
                # checkpoint table does: a resumed page makes no request.
                if requested_source:
                    self._sleep(min_interval_seconds)
                result = self._one_page(
                    adapter=adapter,
                    period=period,
                    page=page,
                    base_import_id=base_import_id,
                    purpose=purpose,
                    git_commit=git_commit,
                )
                requested_source = result.status != "resumed"
                results.append(result)
        return MonthlyRevenueBackfillReport(
            source=adapter.source,
            market=adapter.coverage_market,
            start=start,
            end=end,
            pages=wanted,
            results=tuple(results),
        )

    def _one_page(
        self,
        *,
        adapter: MOPSMonthlyRevenueAdapter,
        period: RevenuePeriod,
        page: RevenuePage,
        base_import_id: UUID,
        purpose: IngestPurpose,
        git_commit: str | None,
    ) -> RevenuePageResult:
        import_id = revenue_page_import_id(base_import_id, adapter.source, period, page)
        try:
            result = self._importer.run(
                adapter=adapter,
                request=MonthlyRevenueRequest(period, page),
                import_id=import_id,
                purpose=purpose,
                git_commit=git_commit,
            )
        except ResourceQuarantinedError as error:
            reason = _reason_code(error)
            # `no_data_for_period` is the source saying it has not published
            # this market-month; every other quarantine is a page this run
            # could not read. Rerunning fixes the second kind and not the
            # first, so they are counted apart — and neither is coverage.
            return RevenuePageResult(
                period=period,
                page=page,
                import_id=import_id,
                status="no_data" if reason == "no_data_for_period" else "failed",
                reason_code=reason,
                detail=str(error),
            )
        except Exception as error:  # noqa: BLE001 - reported, not swallowed
            return RevenuePageResult(
                period=period,
                page=page,
                import_id=import_id,
                status="failed",
                reason_code="operational_error",
                detail=f"{type(error).__name__}: {error}",
            )
        return RevenuePageResult(
            period=period,
            page=page,
            import_id=import_id,
            status="resumed" if result.resumed_from_checkpoint else "imported",
            rows=result.normalized_rows,
            created=result.business_versions_created,
            deduplicated=result.business_versions_deduplicated,
        )


def _periods_between(start: RevenuePeriod, end: RevenuePeriod):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield RevenuePeriod(year, month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
