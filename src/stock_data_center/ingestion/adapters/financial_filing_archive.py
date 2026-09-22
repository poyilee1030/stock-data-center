"""The legacy iXBRL archive: `data/raw/xbrl/<year>/<year>Q<q>/…` (ROADMAP §14).

45,324 documents, 26 GB, one file per filing, named
`<year>Q<q>_<symbol>_<YYYYMMDD>.html`. The trailing date is a synthetic
deadline the legacy scraper wrote, not a capture and not a publication instant
(CLAUDE.md §32, §75); it is recorded and never used as evidence. The files are
the same MOPS documents re-encoded as UTF-8: for 1101 2025Q1 the official
cp950 response of 2026-09-20 decodes to a string identical, character for
character, to the archived copy (audit §4.8).

Because it is the same endpoint's document, this reads it through the official
adapter's contract and under the official adapter's source code
(CLAUDE.md §30): one `mops_t164sb01` history, in which an archived copy of a
document already fetched officially dedups instead of becoming a second
revision. What differs is the artifact origin — `legacy_archive`, never
`official_fetch` — and that is the importer's to declare.

The archive does not record which `REPORT_ID` fetched a file; the document's
own `ReportCategory` says whether it is 合併 or 個體, so nothing is assumed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar
from zoneinfo import ZoneInfo

from stock_data_center.ingestion.adapters.financial_filing import (
    MOPSFinancialFilingAdapter,
)
from stock_data_center.ingestion.http import resolve_archive_glob
from stock_data_center.ingestion.models import (
    FinancialFilingArchiveRequest,
    ParsedFinancialFiling,
    SourceResource,
)

MARKET_TIMEZONE = ZoneInfo("Asia/Taipei")

# ROADMAP §14: the archive stays where the owner keeps it, outside this repo.
DEFAULT_ARCHIVE_ROOT = Path.home() / "GitHubLL/my_stock_project/data/raw/xbrl"


class LegacyFinancialFilingArchiveAdapter(MOPSFinancialFilingAdapter):
    """One filing's archived document, read as `legacy_archive` bytes."""

    version = "legacy-xbrl-archive:v1"
    variants: ClassVar[dict[str, tuple[str, ...]]] = (
        MOPSFinancialFilingAdapter.variants
    )

    def __init__(self, *, archive_root: Path | None = None) -> None:
        self._root = Path(archive_root) if archive_root else DEFAULT_ARCHIVE_ROOT

    def resource(self, request: FinancialFilingArchiveRequest) -> SourceResource:
        """Where the document is, as a pattern.

        The file name ends in the synthetic deadline the legacy scraper wrote,
        which is not derivable from the request, so the name has to be looked
        up. That lookup belongs to the fetch — `ArchiveGlobFetcher` does it —
        and not here: `resource()` runs before the import manifest exists, so
        a miss raised from here would leave no record at all.
        """

        folder = self._root / str(request.report_year) / request.period_label
        return SourceResource(
            resource_key=(
                f"{self.source}:financial_filing_archive:{request.security_code}:"
                f"{request.period_label}"
            ),
            source_uri=str(
                folder / f"{request.period_label}_{request.security_code}_*.html"
            ),
        )

    def parse(
        self, content: bytes, request: FinancialFilingArchiveRequest
    ) -> ParsedFinancialFiling:
        # The archive does not say which REPORT_ID fetched the file, so the
        # document's own ReportCategory decides and nothing is checked against
        # an expectation that does not exist.
        return self._read(content, request, expected_report_id=None)

    def capture_bound(
        self, request: FinancialFilingArchiveRequest
    ) -> datetime | None:
        """What this request's file proves about when its filing was public.

        Read from the file, not from whatever the fetcher last saw: an import
        resuming from a captured checkpoint reads the bytes back out of the raw
        store and never calls the fetcher, and evidence that quietly weakened
        on a resume would be a resume that changed history (CLAUDE.md §76).
        """

        path = resolve_archive_glob(Path(self.resource(request).source_uri))
        return archive_capture_bound(
            report_year=request.report_year,
            report_quarter=request.report_quarter,
            mtime=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        )


# Step 23-c. The archive was written by two different kinds of run, and only
# the file's own mtime tells them apart (audit §4.8, measured 2026-09-21 over
# all 45,324 files).
#
# From 2025Q4 the legacy daily job fetched filings as they appeared: its mtimes
# spread across the whole filing window, day by day, so the file was written
# when the scraper first saw the filing. That is a first sighting, and a first
# sighting bounds publication from above.
#
# Everything else came from a bulk run. Three dates in February 2026 re-fetched
# 2020Q1–2025Q3 wholesale; 2026-08-01, 08-16 and 08-17 were catch-ups that each
# wrote files for quarters closed months earlier — 08-16 wrote 2020Q1 and
# 2020Q2 alongside two 2026Q2 files, which is what makes it a bulk run and not
# a daily one. A bulk run noticed a filing long after it was published and
# proves nothing about when (CLAUDE.md §32).
LEGACY_CAPTURE_WINDOW_START = (2025, 4)

BACKFILL_RUN_DATES = frozenset(
    {
        date(2026, 2, 21),
        date(2026, 2, 22),
        date(2026, 2, 23),
        date(2026, 2, 24),
        date(2026, 2, 26),
        date(2026, 8, 1),
        date(2026, 8, 16),
        date(2026, 8, 17),
    }
)

# 證券交易法 §36. Registered in migration 3c8e5f1b7a46 with its authority; named
# here rather than declared on the source, for the same reason Step 22-c named
# the monthly-revenue rule in its importer: declaring it on `mops_t164sb01`
# would hand the statutory instant to every version the official importer
# writes, including filings nothing has ever proved were on time.
STATUTORY_RULE = ("financial_statements_general", 1)

EVIDENCE_LABEL = "legacy xbrl archive"


def archive_capture_bound(
    *, report_year: int, report_quarter: int, mtime: datetime
) -> datetime | None:
    """The instant the legacy scraper had this document, or `None`.

    `None` means the file proves nothing about publication and the filing has
    to resolve by the release rule instead. The returned instant is the file's
    own mtime in the market timezone: the scraper had the bytes by then, so
    publication was no later, and unlike the monthly-revenue archive — which
    records dates only — there is no need to round out to the end of the day.
    """
    if mtime.tzinfo is None:
        raise ValueError("mtime must be timezone-aware (CLAUDE.md §34)")
    local = mtime.astimezone(MARKET_TIMEZONE)
    if (report_year, report_quarter) < LEGACY_CAPTURE_WINDOW_START:
        return None
    if local.date() in BACKFILL_RUN_DATES:
        return None
    return local
