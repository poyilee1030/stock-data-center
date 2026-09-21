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

from pathlib import Path
from typing import ClassVar

from stock_data_center.ingestion.adapters.financial_filing import (
    MOPSFinancialFilingAdapter,
)
from stock_data_center.ingestion.models import (
    FinancialFilingArchiveRequest,
    ParsedFinancialFiling,
    SourceDataError,
    SourceResource,
)

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
        folder = self._root / str(request.report_year) / request.period_label
        matches = sorted(folder.glob(f"{request.period_label}_{request.security_code}_*.html"))
        if not matches:
            raise SourceDataError(
                "archive_file_missing",
                f"{self.source}: no archived document under {folder} for "
                f"{request.security_code} {request.period_label}",
            )
        if len(matches) > 1:
            # One filing, one document. Two would mean the archive holds two
            # answers to the same request and nothing says which is current.
            raise SourceDataError(
                "ambiguous_archive_file",
                f"{self.source}: {len(matches)} archived documents for "
                f"{request.security_code} {request.period_label}: "
                f"{[path.name for path in matches]}",
            )
        return SourceResource(
            resource_key=(
                f"{self.source}:financial_filing_archive:{request.security_code}:"
                f"{request.period_label}"
            ),
            source_uri=str(matches[0]),
        )

    def parse(
        self, content: bytes, request: FinancialFilingArchiveRequest
    ) -> ParsedFinancialFiling:
        # The archive does not say which REPORT_ID fetched the file, so the
        # document's own ReportCategory decides and nothing is checked against
        # an expectation that does not exist.
        return self._read(content, request, expected_report_id=None)
