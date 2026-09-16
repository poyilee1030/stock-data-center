"""Market-index adapters: whole-list closes, plus the one index with OHLC.

TWSE publishes its index sections in the same `MI_INDEX` file as its stock
section, which Step 17-c already stored for every trade date in the window. That
artifact *could* be reparsed instead of fetched again, but the lifecycle cannot
do it yet: checkpoints are scoped by `import_id`, so borrowing the price
import's resource key would either re-fetch the file under a new id or
short-circuit on the price import's completed checkpoint and write no index row
at all. The adapter therefore has its own key and names the stored resource
separately, through `stored_resource_key`. Building the reprocess path that uses
it is Step 18-b's work.

No feed publishes an index code, so identity has to be built. It is
`(source, section, published name)` — not `(source, name)`, which audit §4.2
assumed and which collides inside a single TPEx file: `櫃買指數` appears in both
the price section and the return section, at 395.52 and 735.15 on 2026-09-11,
and 32 of 34 names are in both. TWSE happens to name its return indices
distinctly, but the identity must hold for both feeds. The section is
structural rather than a business value: a price index does not become a return
index. A renamed index is a new identity until official evidence links them.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from stock_data_center.ingestion.models import (
    MarketIndexRequest,
    MarketIndexRow,
    ParsedMarketIndex,
    ParsedTaiexHistory,
    SourceDataError,
    SourceResource,
    TaiexHistoryRequest,
    TaiexHistoryRow,
)
from stock_data_center.market_reference.models import MarketIndexObservation

_MISSING = frozenset({"", "--", "---", "----", "N/A"})
_TAGS = re.compile(r"<[^>]*>")


class MarketIndexAdapter(ABC):
    """One market's published index closes for one trade date."""

    dataset_code = "market_index"
    source: str
    market: str
    version: str
    endpoint: str

    @abstractmethod
    def resource(self, request: MarketIndexRequest) -> SourceResource: ...

    @abstractmethod
    def parse(
        self, content: bytes, request: MarketIndexRequest
    ) -> ParsedMarketIndex: ...


class TWSEMarketIndexAdapter(MarketIndexAdapter):
    """The `MI_INDEX` index sections — six of them, price and return."""

    source = "twse_mi_index"
    market = "TWSE"
    version = "twse-mi-index-sections:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    # Both spellings are index sections. TWSE separates price indices from
    # return indices by the first column's label and by nothing else; a return
    # index is a published index like any other, not a different dataset.
    section_headers = frozenset({"指數", "報酬指數"})
    fields_tail = (
        "收盤指數",
        "漲跌(+/-)",
        "漲跌點數",
        "漲跌百分比(%)",
        "特殊處理註記",
    )

    def resource(self, request: MarketIndexRequest) -> SourceResource:
        query = urlencode(
            {
                "date": request.trade_date.strftime("%Y%m%d"),
                "type": "ALLBUT0999",
                "response": "json",
            }
        )
        return SourceResource(
            resource_key=(
                f"{self.source}:index-sections:{request.trade_date.isoformat()}"
            ),
            source_uri=f"{self.endpoint}?{query}",
        )

    def stored_resource_key(self, request: MarketIndexRequest) -> str:
        """Where Step 17-c already stored the bytes this adapter parses.

        Stated, not assumed: a reprocess path in 18-b can look the artifact up
        under this key instead of asking TWSE for the same file a second time.
        Nothing in the current lifecycle does that.
        """
        return f"{self.source}:daily-quotes:{request.trade_date.isoformat()}"

    def parse(
        self, content: bytes, request: MarketIndexRequest
    ) -> ParsedMarketIndex:
        payload = _json_object(content)
        if payload.get("stat") != "OK":
            if "tables" not in payload:
                raise SourceDataError(
                    "no_data_for_date",
                    f"TWSE has no data for {request.trade_date.isoformat()}: "
                    f"{payload.get('stat')!r}",
                )
            raise SourceDataError(
                "source_status", f"TWSE response status: {payload.get('stat')!r}"
            )
        if payload.get("date") != request.trade_date.strftime("%Y%m%d"):
            raise SourceDataError(
                "date_mismatch",
                f"TWSE answered for {payload.get('date')!r}, not "
                f"{request.trade_date.isoformat()}",
            )
        tables = payload.get("tables")
        if not isinstance(tables, list):
            raise SourceDataError("schema_mismatch", "TWSE tables is not a list")

        # A section is recognised by its columns, and only then is its label
        # checked. Selecting on the label alone means a rename silently drops
        # 30-50 indices for that date instead of failing.
        sections = []
        for table in tables:
            if not isinstance(table, dict):
                continue
            fields = table.get("fields")
            if not isinstance(fields, list) or len(fields) != 6:
                continue
            if tuple(fields[1:]) != self.fields_tail:
                continue
            if fields[0] not in self.section_headers:
                raise SourceDataError(
                    "schema_mismatch",
                    f"TWSE published an index section labelled {fields[0]!r}, "
                    f"which this adapter does not recognise",
                )
            sections.append(table)
        if not sections:
            raise SourceDataError(
                "schema_mismatch", "TWSE published no index section"
            )

        rows: list[MarketIndexRow] = []
        for table in sections:
            for number, raw in enumerate(
                _data_rows(table.get("data"), self.source), 1
            ):
                values = _row(raw, 6, self.source, number)
                change = _signed_points(
                    marker=values[2], magnitude=values[3], row_number=number
                )
                rows.append(
                    MarketIndexRow(
                        index_name=_index_name(values[0], self.source, number),
                        section=_twse_section(table),
                        observation=MarketIndexObservation(
                            trade_date=request.trade_date,
                            close_value=_required(
                                _decimal(values[1], "close value"),
                                f"{self.source} row {number} close value",
                            ),
                            change_points=change,
                            change_percent=_decimal(values[4], "change percent"),
                        ),
                    )
                )
        _validate_rows(rows, self.source)
        return ParsedMarketIndex(
            market=self.market,
            trade_date=request.trade_date,
            rows=tuple(rows),
            section_count=len(sections),
            source_fields=("指數", *self.fields_tail),
        )


class TPExMarketIndexAdapter(MarketIndexAdapter):
    """`afterTrading/indexSummary` — a price section and a return section."""

    source = "tpex_index_summary"
    market = "TPEx"
    version = "tpex-index-summary:v1"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/afterTrading/indexSummary"
    section_headers = frozenset({"指數", "報酬指數"})
    fields_tail = ("收市指數", "漲跌", "漲跌幅度(%)", "大盤資訊連結")

    def resource(self, request: MarketIndexRequest) -> SourceResource:
        query = urlencode(
            {
                "date": request.trade_date.strftime("%Y/%m/%d"),
                "response": "json",
            }
        )
        return SourceResource(
            resource_key=(
                f"{self.source}:index-summary:{request.trade_date.isoformat()}"
            ),
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: MarketIndexRequest
    ) -> ParsedMarketIndex:
        payload = _json_object(content)
        if payload.get("stat") != "ok":
            raise SourceDataError(
                "source_status", f"TPEx response status: {payload.get('stat')!r}"
            )
        if payload.get("date") != request.trade_date.strftime("%Y%m%d"):
            raise SourceDataError(
                "date_mismatch",
                f"TPEx answered for {payload.get('date')!r}, not "
                f"{request.trade_date.isoformat()}",
            )
        tables = payload.get("tables")
        if not isinstance(tables, list) or not tables:
            raise SourceDataError("schema_mismatch", "TPEx published no table")

        rows: list[MarketIndexRow] = []
        sections = 0
        for table in tables:
            if not isinstance(table, dict):
                raise SourceDataError(
                    "schema_mismatch", "TPEx table is not an object"
                )
            fields = table.get("fields")
            if not isinstance(fields, list) or not fields:
                raise SourceDataError("schema_mismatch", "TPEx table has no header")
            if fields[0] not in self.section_headers:
                raise SourceDataError(
                    "schema_mismatch",
                    f"TPEx published an unknown index section {fields[0]!r}",
                )
            if tuple(fields[1:]) != self.fields_tail:
                raise SourceDataError(
                    "schema_mismatch",
                    f"TPEx index section changed: expected {self.fields_tail!r}, "
                    f"received {tuple(fields[1:])!r}",
                )
            sections += 1
            for number, raw in enumerate(
                _data_rows(table.get("data"), self.source), 1
            ):
                values = _row(raw, 5, self.source, number)
                rows.append(
                    MarketIndexRow(
                        index_name=_index_name(values[0], self.source, number),
                        section=fields[0],
                        observation=MarketIndexObservation(
                            trade_date=request.trade_date,
                            close_value=_required(
                                _decimal(values[1], "close value"),
                                f"{self.source} row {number} close value",
                            ),
                            # TPEx signs the number itself and has no sign
                            # column, so nothing needs combining.
                            change_points=_decimal(values[2], "change points"),
                            change_percent=_decimal(values[3], "change percent"),
                        ),
                    )
                )
        _validate_rows(rows, self.source)
        return ParsedMarketIndex(
            market=self.market,
            trade_date=request.trade_date,
            rows=tuple(rows),
            section_count=sections,
            source_fields=("指數", *self.fields_tail),
        )


class TWSETaiexHistoryAdapter:
    """`MI_5MINS_HIST` — the only index OHLC any endpoint serves for past dates.

    One calendar month per request, and one index: `發行量加權股價指數`. TPEx
    publishes the same shape for `櫃買指數` through `openapi/v1/tpex_index`, but
    that endpoint takes no parameters and always answers the current month, so
    OTC index OHLC cannot be backfilled (audit §4.2).
    """

    dataset_code = "market_index"
    source = "twse_mi_5mins_hist"
    market = "TWSE"
    index_name = "發行量加權股價指數"
    version = "twse-mi-5mins-hist:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST"
    fields = ("日期", "開盤指數", "最高指數", "最低指數", "收盤指數")

    def resource(self, request: TaiexHistoryRequest) -> SourceResource:
        query = urlencode(
            {"date": request.month.strftime("%Y%m%d"), "response": "json"}
        )
        return SourceResource(
            resource_key=f"{self.source}:taiex:{request.month:%Y-%m}",
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: TaiexHistoryRequest
    ) -> ParsedTaiexHistory:
        payload = _json_object(content)
        if payload.get("stat") != "OK":
            # Same structural distinction the index adapter makes: a month TWSE
            # has nothing for answers with no `fields` at all. Mapping every
            # status here would let a maintenance page read as an empty month,
            # and 18-b walks about 80 of them.
            if "fields" not in payload:
                raise SourceDataError(
                    "no_data_for_date",
                    f"TWSE has no TAIEX history for {request.month:%Y-%m}: "
                    f"{payload.get('stat')!r}",
                )
            raise SourceDataError(
                "source_status", f"TWSE response status: {payload.get('stat')!r}"
            )
        fields = payload.get("fields")
        if not isinstance(fields, list) or tuple(fields) != self.fields:
            raise SourceDataError(
                "schema_mismatch",
                f"TAIEX history header changed: expected {self.fields!r}, "
                f"received {fields!r}",
            )

        rows: list[TaiexHistoryRow] = []
        for number, raw in enumerate(
            _data_rows(payload.get("data"), self.source), 1
        ):
            values = _row(raw, 5, self.source, number)
            trade_date = _roc_slashed_date(
                values[0], f"TAIEX row {number} trade date"
            )
            if (trade_date.year, trade_date.month) != (
                request.month.year,
                request.month.month,
            ):
                raise SourceDataError(
                    "date_mismatch",
                    f"TAIEX row {number} is {trade_date.isoformat()}, outside "
                    f"the requested {request.month:%Y-%m}",
                )
            rows.append(
                TaiexHistoryRow(
                    trade_date=trade_date,
                    observation=MarketIndexObservation(
                        trade_date=trade_date,
                        open_value=_decimal(values[1], "open value"),
                        high_value=_decimal(values[2], "high value"),
                        low_value=_decimal(values[3], "low value"),
                        close_value=_required(
                            _decimal(values[4], "close value"),
                            f"TAIEX row {number} close value",
                        ),
                        # This feed publishes no change columns at all.
                    ),
                )
            )
        if not rows:
            raise SourceDataError(
                "no_data_for_date",
                f"TWSE published no TAIEX day for {request.month:%Y-%m}",
            )
        dates = [item.trade_date for item in rows]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise SourceDataError(
                "ambiguous_identity", "TAIEX dates are duplicated or not ordered"
            )
        return ParsedTaiexHistory(
            market=self.market,
            index_name=self.index_name,
            month=request.month,
            rows=tuple(rows),
            source_fields=self.fields,
        )


def _signed_points(
    *, marker: str, magnitude: str, row_number: int
) -> Decimal | None:
    """Combine `漲跌(+/-)` with the unsigned `漲跌點數`.

    Same shape as the stock section, and the same refusal to guess: a sign this
    adapter does not recognise is a change whose direction would have to be
    invented.
    """
    sign = _TAGS.sub("", marker).replace(" ", " ").strip().upper()
    # The marker is validated before the magnitude is even parsed: a missing
    # number must not turn an unreadable sign into a silent None.
    if sign not in {"", "+", "-", "X"}:
        raise SourceDataError(
            "ambiguous_direction",
            f"index row {row_number} publishes an unknown sign {sign!r}",
        )
    if sign == "X":
        # 不比價: the index was not compared, so no change was published.
        return None
    value = _decimal(magnitude, f"index row {row_number} change points")
    if value is None:
        return None
    if sign == "":
        if value != 0:
            raise SourceDataError(
                "ambiguous_direction",
                f"index row {row_number} publishes no sign for a change of "
                f"{magnitude!r}",
            )
        return value
    if value < 0:
        raise SourceDataError(
            "impossible_value",
            f"index row {row_number} signs {sign!r} a negative magnitude",
        )
    return -value if sign == "-" else value


def _json_object(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceDataError(
            "invalid_json", f"invalid source JSON: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise SourceDataError("schema_mismatch", "source JSON must be an object")
    return payload


def _data_rows(value: object, source: str) -> list[object]:
    if not isinstance(value, list):
        raise SourceDataError("schema_mismatch", f"{source} data is not a list")
    return value


def _row(
    value: object, expected_length: int, source: str, row_number: int
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) != expected_length
        or not all(isinstance(item, str) for item in value)
    ):
        raise SourceDataError(
            "schema_mismatch", f"{source} row {row_number} has an invalid shape"
        )
    return tuple(value)


def _twse_section(table: dict) -> str:
    """The section label plus the provider its title names.

    Six sections, three providers: `價格指數(臺灣證券交易所)`,
    `(跨市場)`, `(臺灣指數公司)` and the same three for return indices. The
    label alone conflates them, so one name published by two providers would
    quarantine the whole trade date instead of resolving two indices.
    """
    label = table["fields"][0]
    title = table.get("title")
    provider = ""
    if isinstance(title, str) and "(" in title and title.rstrip().endswith(")"):
        provider = title[title.rindex("(") + 1 : title.rindex(")")].strip()
    return f"{label}/{provider}" if provider else label


def _required(value: Decimal | None, field: str) -> Decimal:
    if value is None:
        raise SourceDataError("missing_value", f"{field} is not published")
    return value


def _index_name(value: str, source: str, row_number: int) -> str:
    name = value.strip()
    if not name:
        raise SourceDataError(
            "invalid_identity", f"{source} row {row_number} has no index name"
        )
    return name


def _decimal(value: str, field: str) -> Decimal | None:
    normalized = _TAGS.sub("", value).replace(",", "").replace("　", "").strip()
    if normalized.upper() in _MISSING:
        return None
    try:
        result = Decimal(normalized)
    except InvalidOperation as error:
        raise SourceDataError(
            "invalid_numeric", f"{field} is not an unambiguous decimal: {value!r}"
        ) from error
    if not result.is_finite():
        raise SourceDataError("invalid_numeric", f"{field} must be finite")
    return result


def _roc_slashed_date(value: str, field: str) -> date:
    parts = value.strip().split("/")
    if len(parts) != 3 or not all(
        part.isascii() and part.isdigit() for part in parts
    ):
        raise SourceDataError(
            "invalid_date", f"{field} is not ROC YYY/MM/DD: {value!r}"
        )
    try:
        return date(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))
    except ValueError as error:
        raise SourceDataError(
            "invalid_date", f"{field} is invalid: {value!r}"
        ) from error


def _validate_rows(rows: list[MarketIndexRow], source: str) -> None:
    if not rows:
        raise SourceDataError(
            "no_data_for_date", f"{source} published no index for the trade date"
        )
    # Identity is (source, section, name): TPEx repeats a name across its two
    # sections by design, so only a collision *within* a section is ambiguous.
    codes = [item.index_code(source) for item in rows]
    if len(codes) != len(set(codes)):
        duplicates = sorted({code for code in codes if codes.count(code) > 1})
        raise SourceDataError(
            "ambiguous_identity",
            f"{source} published one trade date twice for {duplicates!r}",
        )
