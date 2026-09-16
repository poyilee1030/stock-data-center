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
from stock_data_center.ingestion.adapters.whole_market_daily import (
    WholeMarketDailyAdapter,
)
from stock_data_center.ingestion.models import (
    ResourceQuarantinedError,
    SourceDataError,
    WholeMarketDailyRequest,
)
from stock_data_center.ingestion.whole_market_daily import WholeMarketDailyImporter
from stock_data_center.market_calendar import TradingCalendarService
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
    ) -> None:
        self._importer = importer
        self._calendar = calendar or TradingCalendarService()
        self._expected = expected or ExpectedCoverageService(self._calendar)
        self._sleep = sleep

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
                request=WholeMarketDailyRequest(trade_date),
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


def _reason_code(error: ResourceQuarantinedError) -> str | None:
    cause = error.__cause__
    if isinstance(cause, SourceDataError):
        return cause.reason_code
    return None


def _days_between(start: date, end: date) -> list[date]:
    from datetime import timedelta

    span = (end - start).days
    return [start + timedelta(days=offset) for offset in range(span + 1)]
