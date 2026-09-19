"""Monthly-revenue adapters: MOPS 每月營業收入彙總表 (`t21sc03`).

`mopsov.twse.com.tw/nas/t21/{sii|otc}/t21sc03_{ROC year}_{month}_{0|1}.html`,
a plain GET (audit §4.7). One page per market, month and page: `_0` lists
domestic issuers, `_1` foreign and KY issuers, and the two never share a
company. Legacy fetched only `_0`, so every KY issuer is new coverage.

The page is Big5 as Microsoft writes it: strict big5 rejects it, cp950 reads it
whole. One table per industry, each headed `單位：千元`, then a whole-market
total table. A company row is 11 `<td>` cells; the totals (`合計`,
`全部國內上市公司合計`) start with a `<th>` and are not companies.

Amounts are 千元, stored × 1,000 in TWD. The comparatives (上月營收, 去年當月營收,
the three percentages, the cumulative values and 備註) are stored exactly as
published and never reconciled against our own series (audit §7.3): a
disagreement between this page's 上月營收 and last month's 當月營收 is an
issuer's correction, and it is data. A blank percentage — no base to compare
with — is NULL; 備註 is kept verbatim, `-` included.

The title names the market and the ROC year-month and is checked on every
page. `出表日期` is when the page was generated, not a publication time; it is
recorded, never used as evidence. Values are the latest corrected ones: the
first-published values are not recoverable from this source.

Each market is its own source. A security moving market can appear on both
markets' pages in the same month (legacy 5236, 2026M06), and one source would
then alternate its revisions for one logical key (CLAUDE.md §30).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from types import MappingProxyType

from stock_data_center.ingestion.models import (
    MonthlyRevenueRequest,
    MonthlyRevenueRow,
    ParsedMonthlyRevenue,
    SourceDataError,
    SourceResource,
)
from stock_data_center.monthly_revenue.ingestion import (
    MonthlyRevenueObservation,
    RevenueScale,
    SourceRevenueAmount,
)

_ROC_OFFSET = 1911
_HOST = "https://mopsov.twse.com.tw/nas/t21"
_TITLE = re.compile(r"(上市|上櫃)公司(\d+)年(\d+)月份\(累計與當月\)營業收入統計表")
_UNIT = re.compile(r"單位：([^<\s]+)")
_GENERATED = re.compile(r"出表日期：(\d+/\d+/\d+)")
_AMOUNT = re.compile(r"-?(?:\d{1,3}(?:,\d{3})*|\d+)")
# Percentages carry thousands separators too (4,533.33).
_PERCENT = re.compile(r"-?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?")
_NO_DATA = "查無資料"
_CODE = re.compile(r"[0-9A-Z]+")

# The company-table header, after `<br>` is dropped; 備註 heads the group row.
_HEADER = (
    "公司代號", "公司名稱", "當月營收", "上月營收", "去年當月營收", "上月比較增減(%)",
    "去年同月增減(%)", "當月累計營收", "去年累計營收", "前期比較增減(%)",
)
_AMOUNTS = {
    "revenue": 2,
    "revenue_last_month": 3,
    "revenue_last_year_month": 4,
    "cumulative_revenue": 7,
    "cumulative_revenue_last_year": 8,
}
_PERCENTS = {"mom_pct": 5, "yoy_pct": 6, "cumulative_yoy_pct": 9}
_NOTE = 10
_CELLS = 11


class _Rows(HTMLParser):
    """Every table row as (first cell tag, cell texts)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, list[str]]] = []
        self._row: list[str] | None = None
        self._first: str | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "tr":
            self._close_row()
            self._row, self._first = [], None
        elif tag in ("td", "th") and self._row is not None:
            self._close_cell()
            self._cell = []
            self._first = self._first or tag

    def handle_endtag(self, tag) -> None:
        if tag in ("td", "th"):
            self._close_cell()
        elif tag in ("tr", "table"):
            self._close_row()

    def handle_data(self, data) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def close(self) -> None:
        super().close()
        self._close_row()

    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row:
            self.rows.append((self._first or "", self._row))
        self._row = None


class MOPSMonthlyRevenueAdapter:
    """One market's t21sc03 page for one month."""

    dataset_code = "monthly_revenue"
    source: str
    market: str
    market_title: str
    version: str
    variants = MappingProxyType({"t21sc03_11": _HEADER})

    def resource(self, request: MonthlyRevenueRequest) -> SourceResource:
        period = request.period
        page = request.page.value
        return SourceResource(
            resource_key=(
                f"{self.source}:monthly_revenue:{period.year:04d}-{period.month:02d}:{page}"
            ),
            source_uri=(
                f"{_HOST}/{self.market}/t21sc03_{period.year - _ROC_OFFSET}_"
                f"{period.month}_{page}.html"
            ),
        )

    def parse(self, content: bytes, request: MonthlyRevenueRequest) -> ParsedMonthlyRevenue:
        try:
            text = content.decode("cp950")
        except UnicodeDecodeError as error:
            raise SourceDataError("invalid_encoding", f"{self.source}: {error}") from error
        title = _TITLE.search(text)
        if title is None:
            # Not a MOPS page at all, e.g. the 18-byte `Unreachable Server`
            # the host answers under load; the next request succeeds.
            raise SourceDataError(
                "unusable_response", f"{self.source} answered {text[:80]!r}, not a revenue page"
            )
        market, year, month = title.group(1), int(title.group(2)), int(title.group(3))
        period = request.period
        if market != self.market_title:
            raise SourceDataError(
                "market_mismatch", f"{self.source} page is for {market}, not {self.market_title}"
            )
        if (year + _ROC_OFFSET, month) != (period.year, period.month):
            raise SourceDataError(
                "period_mismatch",
                f"{self.source} page is for ROC {year}/{month}, not {period.year}-{period.month:02d}",
            )
        parser = _Rows()
        parser.feed(text)
        parser.close()
        companies = []
        for first, cells in parser.rows:
            if first != "td" or cells == [""]:
                continue  # a header, a total, or the one-cell layout row
            if len(cells) != _CELLS:
                raise SourceDataError(
                    "schema_mismatch", f"{self.source} row {cells[:2]!r} has {len(cells)} cells"
                )
            if not _CODE.fullmatch(cells[0]):
                raise SourceDataError(
                    "invalid_identity", f"{self.source} row has company code {cells[0]!r}"
                )
            companies.append(cells)
        if not companies:
            if _NO_DATA in text:
                raise SourceDataError(
                    "no_data_for_period",
                    f"{self.source} has no {request.page.name.lower()} page for "
                    f"{period.year}-{period.month:02d}",
                )
            raise SourceDataError("schema_mismatch", f"{self.source} page lists no company")
        units = set(_UNIT.findall(text))
        if units != {"千元"}:
            raise SourceDataError(
                "unit_declaration_changed", f"{self.source} states units {sorted(units)!r}"
            )
        headers = {
            tuple(cells) for first, cells in parser.rows
            if first == "th" and cells and cells[0] == "公司代號"
        }
        if headers != {_HEADER}:
            raise SourceDataError(
                "schema_mismatch", f"{self.source} published headers {sorted(headers)!r}"
            )
        rows: list[MonthlyRevenueRow] = []
        seen: set[str] = set()
        for number, cells in enumerate(companies, 1):
            code = cells[0]
            if code in seen:
                raise SourceDataError(
                    "duplicate_security",
                    f"{self.source} lists {code} twice for {period.year}-{period.month:02d}",
                )
            seen.add(code)
            values: dict[str, object] = {
                name: self._amount(cells[index], number, name, required=name == "revenue")
                for name, index in _AMOUNTS.items()
            }
            values.update(
                {name: self._percent(cells[index], number, name) for name, index in _PERCENTS.items()}
            )
            revenue = values.pop("revenue")
            try:
                observation = MonthlyRevenueObservation(
                    period=period,
                    revenue=revenue,  # type: ignore[arg-type]
                    currency="TWD",
                    note=cells[_NOTE] or None,
                    **values,  # type: ignore[arg-type]
                )
            except ValueError as error:
                raise SourceDataError(
                    "invalid_numeric", f"{self.source} row {number} ({code}): {error}"
                ) from error
            rows.append(MonthlyRevenueRow(code, observation))
        generated = _GENERATED.search(text)
        return ParsedMonthlyRevenue(
            market=self.market,
            period=period,
            page=request.page,
            rows=tuple(rows),
            header_variant="t21sc03_11",
            source_fields=_HEADER + ("備註",),
            generated_on=generated.group(1) if generated else "",
        )

    def _amount(self, text: str, number: int, field: str, *, required: bool) -> Decimal | None:
        if not text and not required:
            return None
        if not _AMOUNT.fullmatch(text):
            raise SourceDataError(
                "unrecognised_value", f"{self.source} row {number} {field} is {text!r}"
            )
        value, _ = SourceRevenueAmount(
            Decimal(text.replace(",", "")), "TWD", RevenueScale.THOUSAND
        ).to_major_unit()
        return value

    def _percent(self, text: str, number: int, field: str) -> Decimal | None:
        if not text:
            return None
        if not _PERCENT.fullmatch(text):
            raise SourceDataError(
                "unrecognised_value", f"{self.source} row {number} {field} is {text!r}"
            )
        try:
            return Decimal(text.replace(",", ""))
        except InvalidOperation as error:  # pragma: no cover - the pattern guards it
            raise SourceDataError(
                "unrecognised_value", f"{self.source} row {number} {field} is {text!r}"
            ) from error


class MOPSSiiMonthlyRevenueAdapter(MOPSMonthlyRevenueAdapter):
    """上市 (`sii`) monthly revenue."""

    source = "mops_t21sc03_sii"
    market = "sii"
    market_title = "上市"
    version = "mops-t21sc03-sii:v1"


class MOPSOtcMonthlyRevenueAdapter(MOPSMonthlyRevenueAdapter):
    """上櫃 (`otc`) monthly revenue."""

    source = "mops_t21sc03_otc"
    market = "otc"
    market_title = "上櫃"
    version = "mops-t21sc03-otc:v1"
