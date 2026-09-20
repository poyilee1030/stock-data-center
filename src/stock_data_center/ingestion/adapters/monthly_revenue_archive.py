"""The legacy monthly-revenue archive: `data/raw/monthly_revenue/*/market.csv`.

One UTF-8 file per month holding both markets, written by the legacy scraper
and later rewritten one cell per row by `revswarm`, which recovered each
filing's announcement date from news articles (audit §7.4). It is not an
official response, so it is read as `legacy_archive` bytes and what it is worth
depends on the month (ROADMAP §14, CLAUDE.md §75):

* 2020M01–2026M01, `publish_time` is the recovered announcement date, and the
  value beside it is *not* the value published that day — corrections since
  are already in it, and the first-published number is not recoverable;
* 2026M02 onward, both are the legacy 22:45 job's own first capture.

The adapter reads both the same way and says only what the file says. Which
window a month falls in is the importer's decision, because it decides what may
be claimed rather than what was read.

One adapter instance covers one market: the file's `market` column is `SII` or
`OTC`, and each maps to the Step 22-a source that holds that market's history.
`rotc` and `pub` never appear — the legacy scraper requested neither — and a
row that named one would be outside the v1 universe, so it fails the file.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import ClassVar

from stock_data_center.ingestion.models import (
    MonthlyRevenueArchiveRequest,
    MonthlyRevenueArchiveRow,
    ParsedMonthlyRevenueArchive,
    SourceDataError,
    SourceResource,
)
from stock_data_center.monthly_revenue.ingestion import (
    MonthlyRevenueObservation,
    RevenueScale,
    SourceRevenueAmount,
)

# ROADMAP §14: the archive stays where the owner keeps it, outside this repo.
DEFAULT_ARCHIVE_ROOT = Path.home() / "GitHubLL/my_stock_project/data/raw/monthly_revenue"

HEADER = (
    "symbol", "name", "revenue", "revenue_last_month", "revenue_last_year",
    "mom_pct", "yoy_pct", "revenue_acc", "revenue_acc_last_year", "acc_yoy_pct",
    "comment", "market", "publish_time",
)
HEADER_VARIANT = "legacy_market_csv_13"
# The legacy market label, and the Step 22-a source that owns that market.
MARKETS = {"SII": "mops_t21sc03_sii", "OTC": "mops_t21sc03_otc"}
SOURCE_MARKETS = {source: market for market, source in MARKETS.items()}
# Legacy column -> stored column, for the amounts it states in 千元.
AMOUNTS = {
    "revenue": "revenue",
    "revenue_last_month": "revenue_last_month",
    "revenue_last_year": "revenue_last_year_month",
    "revenue_acc": "cumulative_revenue",
    "revenue_acc_last_year": "cumulative_revenue_last_year",
}
PERCENTS = {
    "mom_pct": "mom_pct",
    "yoy_pct": "yoy_pct",
    "acc_yoy_pct": "cumulative_yoy_pct",
}


class LegacyMonthlyRevenueArchiveAdapter:
    """One market's rows out of one month's legacy `market.csv`."""

    dataset_code = "monthly_revenue"
    version = "legacy-market-csv:v1"
    variants: ClassVar[dict[str, tuple[str, ...]]] = {HEADER_VARIANT: HEADER}

    def __init__(
        self, source: str, *, archive_root: Path | None = None
    ) -> None:
        if source not in SOURCE_MARKETS:
            raise ValueError(f"no legacy market maps to source {source!r}")
        self.source = source
        self.legacy_market = SOURCE_MARKETS[source]
        self.market = "sii" if source.endswith("_sii") else "otc"
        self.coverage_market = "TWSE" if self.market == "sii" else "TPEx"
        self._root = Path(archive_root) if archive_root else DEFAULT_ARCHIVE_ROOT

    def resource(self, request: MonthlyRevenueArchiveRequest) -> SourceResource:
        period = request.period
        folder = f"{period.year}M{period.month:02d}"
        path = self._root / str(period.year) / folder / "market.csv"
        return SourceResource(
            resource_key=(
                f"{self.source}:monthly_revenue_archive:"
                f"{period.year:04d}-{period.month:02d}"
            ),
            source_uri=str(path),
        )

    def parse(
        self, content: bytes, request: MonthlyRevenueArchiveRequest
    ) -> ParsedMonthlyRevenueArchive:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise SourceDataError(
                "invalid_encoding", f"{self.source}: {error}"
            ) from error
        reader = csv.DictReader(io.StringIO(text))
        if tuple(reader.fieldnames or ()) != HEADER:
            raise SourceDataError(
                "schema_mismatch",
                f"{self.source} archive header is {reader.fieldnames!r}",
            )
        rows: list[MonthlyRevenueArchiveRow] = []
        seen: set[str] = set()
        total = 0
        for number, row in enumerate(reader, 1):
            total += 1
            market = (row["market"] or "").strip()
            if market not in MARKETS:
                raise SourceDataError(
                    "unrecognised_value",
                    f"{self.source} archive row {number} is market {market!r}, "
                    "which is outside the v1 universe",
                )
            if market != self.legacy_market:
                continue
            code = (row["symbol"] or "").strip()
            if not code:
                raise SourceDataError(
                    "invalid_identity",
                    f"{self.source} archive row {number} has no company code",
                )
            if code in seen:
                raise SourceDataError(
                    "duplicate_security",
                    f"{self.source} archive lists {code} twice for "
                    f"{request.period.year}-{request.period.month:02d}",
                )
            seen.add(code)
            rows.append(
                MonthlyRevenueArchiveRow(
                    security_code=code,
                    observation=self._observation(row, number, request),
                    captured_on=self._captured_on(row["publish_time"], number),
                )
            )
        return ParsedMonthlyRevenueArchive(
            market=self.market,
            period=request.period,
            rows=tuple(rows),
            header_variant=HEADER_VARIANT,
            source_fields=HEADER,
            source_rows=total,
        )

    def _observation(
        self, row: dict, number: int, request: MonthlyRevenueArchiveRequest
    ) -> MonthlyRevenueObservation:
        amounts = {
            stored: self._amount(row[column], number, column)
            for column, stored in AMOUNTS.items()
        }
        revenue = amounts.pop("revenue")
        if revenue is None:
            raise SourceDataError(
                "unrecognised_value",
                f"{self.source} archive row {number} has no 當月營收",
            )
        scaled, currency = SourceRevenueAmount(
            revenue, "TWD", RevenueScale.THOUSAND
        ).to_major_unit()
        note = (row["comment"] or "").strip()
        return MonthlyRevenueObservation(
            period=request.period,
            revenue=scaled,
            currency=currency,
            note=note or None,
            **{
                stored: None if value is None else value * RevenueScale.THOUSAND.multiplier
                for stored, value in amounts.items()
            },
            **{
                stored: self._percent(row[column], number, column)
                for column, stored in PERCENTS.items()
            },
        )

    def _amount(self, text: str, number: int, column: str) -> Decimal | None:
        value = (text or "").strip()
        if not value:
            return None
        try:
            return Decimal(value.replace(",", ""))
        except InvalidOperation as error:
            raise SourceDataError(
                "unrecognised_value",
                f"{self.source} archive row {number} {column} is {text!r}",
            ) from error

    def _percent(self, text: str, number: int, column: str) -> Decimal | None:
        return self._amount(text, number, column)

    def _captured_on(self, text: str, number: int) -> date:
        value = (text or "").strip()
        try:
            return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        except (ValueError, IndexError) as error:
            raise SourceDataError(
                "unrecognised_value",
                f"{self.source} archive row {number} publish_time is {text!r}; "
                "the legacy file writes it as YYYYMMDD",
            ) from error
