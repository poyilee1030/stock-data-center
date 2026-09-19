"""Foreign-holding adapters: 外資及陸資投資持股統計.

One whole-market table per trade date, in shares and percent:

- TWSE `rwd/zh/fund/MI_QFIIS`, `selectType=ALLBUT0999`, JSON with 12 fields and
  `hints` `單位:股`. The two share ratios are JSON numbers rather than strings,
  so the payload is decoded with exact decimals: a float would turn 0.3 into
  0.29999….
- TPEx has no exchange JSON with every contract column (audit §4.4): MOPS
  `server-java/t13sa150_otc` is the source, a POST of the legacy scraper's form
  that answers MS950 HTML with 11 columns. It is decoded as cp950, not strict
  big5 — a handful of security names use characters only cp950 maps. Every
  request goes through the per-host governor of Step 20-c.

`與前日異動原因` holds codes 2, 3, 4 and 5, each defined in the page's own
note; blank means an ordinary market-trade change and is stored as NULL. A
cell may carry several codes, stored ascending and comma-separated (`2,4`).
Both sources wrap codes in links to filing pages whose URLs move every month;
the links are presentation and only the codes are stored, so a moved link is
not a revision. Anything else fails the file.

No value is recomputed, and the security name and TWSE's ISIN are not stored:
security metadata owns them.
"""

from __future__ import annotations

import html
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from urllib.parse import urlencode

from stock_data_center.ingestion.models import (
    ForeignHoldingRequest,
    ForeignHoldingRow,
    ParsedForeignHolding,
    SourceDataError,
    SourceResource,
)
from stock_data_center.institutional_financing.models import (
    ForeignHoldingObservation,
    QuantityScale,
    SourceShareQuantity,
)

_ROC_OFFSET = 1911
_SHARES = re.compile(r"\d{1,3}(?:,\d{3})*|\d+")
_RATIO = re.compile(r"\d+(?:\.\d+)?")
_ROC_DATE = re.compile(r"(\d{2,3})/(\d{2})/(\d{2})")
_REASON_CODES = frozenset("2345")
_TAG = re.compile(r"<[^>]*>")

# Observation field -> position among the value cells that follow code and name.
_VALUE_FIELDS = (
    "issued_shares", "investable_shares", "held_shares",
    "investable_ratio", "held_ratio",
    "foreign_legal_limit_ratio", "mainland_legal_limit_ratio",
    "change_reason", "source_last_update_date",
)


class ForeignHoldingAdapter(ABC):
    """One market's per-security foreign holding for one trade date."""

    dataset_code = "foreign_holding"
    source: str
    market: str
    version: str
    endpoint: str
    variants: MappingProxyType

    @abstractmethod
    def resource(self, request: ForeignHoldingRequest) -> SourceResource: ...

    @abstractmethod
    def parse(
        self, content: bytes, request: ForeignHoldingRequest
    ) -> ParsedForeignHolding: ...

    def _variant(self, fields: object) -> str:
        variant = next(
            (
                name
                for name, expected in self.variants.items()
                if isinstance(fields, (list, tuple)) and tuple(fields) == expected
            ),
            None,
        )
        if variant is None:
            raise SourceDataError(
                "schema_mismatch", f"{self.source} published an unknown header {fields!r}"
            )
        return variant

    def _rows(
        self,
        request: ForeignHoldingRequest,
        table: Sequence[tuple[str, Sequence[object]]],
    ) -> tuple[ForeignHoldingRow, ...]:
        """`table` is (code, the nine value cells in `_VALUE_FIELDS` order)."""
        rows: list[ForeignHoldingRow] = []
        seen: set[str] = set()
        for number, (code, cells) in enumerate(table, 1):
            if not code:
                raise SourceDataError(
                    "invalid_identity", f"{self.source} row {number} has no security code"
                )
            if code in seen:
                raise SourceDataError(
                    "duplicate_security",
                    f"{self.source} lists {code} twice for {request.trade_date.isoformat()}",
                )
            seen.add(code)
            values = dict(zip(_VALUE_FIELDS, cells, strict=True))
            # Parse first: a SourceDataError is itself a ValueError, and only
            # the observation's own range checks are `invalid_numeric`.
            parsed = {
                **{name: self._shares(values[name], number, code) for name in _VALUE_FIELDS[:3]},
                **{name: self._ratio(values[name], number, code) for name in _VALUE_FIELDS[3:7]},
                "change_reason": self._reason(values["change_reason"], number, code),
                "source_last_update_date": self._roc_date(
                    values["source_last_update_date"], number, code
                ),
            }
            try:
                observation = ForeignHoldingObservation(
                    trade_date=request.trade_date, **parsed
                )
            except ValueError as error:
                raise SourceDataError(
                    "invalid_numeric", f"{self.source} row {number} ({code}): {error}"
                ) from error
            rows.append(ForeignHoldingRow(code, observation))
        return tuple(rows)

    def _text(self, value: object, number: int, code: str) -> str:
        if not isinstance(value, str):
            raise SourceDataError(
                "schema_mismatch", f"{self.source} row {number} ({code}) {value!r} is not text"
            )
        return value.strip()

    def _shares(self, value: object, number: int, code: str):
        text = self._text(value, number, code)
        if not _SHARES.fullmatch(text):
            raise SourceDataError(
                "unrecognised_value", f"{self.source} row {number} ({code}) shares {value!r}"
            )
        return SourceShareQuantity(
            Decimal(text.replace(",", "")), QuantityScale.SHARE
        ).to_canonical()

    def _ratio(self, value: object, number: int, code: str) -> Decimal:
        if isinstance(value, Decimal) or isinstance(value, int) and not isinstance(value, bool):
            text = str(value)
        else:
            text = self._text(value, number, code)
        if not _RATIO.fullmatch(text):
            raise SourceDataError(
                "unrecognised_value", f"{self.source} row {number} ({code}) ratio {value!r}"
            )
        return Decimal(text)

    def _reason(self, value: object, number: int, code: str) -> str | None:
        """Every published code, ascending and comma-separated: `2`, `2,4`.

        A cell may carry several codes. TWSE puts one link per code on its own
        line (`2<br>4`); MOPS runs them together (`24`). Each code is one digit
        from 2 to 5, so both read the same, and a digit outside that set or a
        repeated one fails the file.
        """
        text = html.unescape(_TAG.sub(" ", self._text(value, number, code)))
        digits = "".join(text.split())
        if not digits:
            return None
        if (
            not digits.isdigit()
            or not set(digits) <= _REASON_CODES
            or len(set(digits)) != len(digits)
        ):
            raise SourceDataError(
                "unrecognised_value",
                f"{self.source} row {number} ({code}) change reason {value!r}",
            )
        return ",".join(sorted(digits))

    def _roc_date(self, value: object, number: int, code: str) -> date | None:
        text = self._text(value, number, code)
        if not text:
            return None
        match = _ROC_DATE.fullmatch(text)
        if match is None:
            raise SourceDataError(
                "unrecognised_value",
                f"{self.source} row {number} ({code}) last-update date {value!r}",
            )
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year + _ROC_OFFSET, month, day)
        except ValueError as error:
            raise SourceDataError(
                "unrecognised_value",
                f"{self.source} row {number} ({code}) last-update date {value!r}",
            ) from error


class TWSEForeignHoldingAdapter(ForeignHoldingAdapter):
    """`fund/MI_QFIIS` — 外資及陸資投資持股統計."""

    source = "twse_mi_qfiis"
    market = "TWSE"
    version = "twse-mi-qfiis:v2"
    endpoint = "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS"
    unit_hint = "單位:股"
    variants = MappingProxyType({
        "mi_qfiis_12": (
            "證券代號", "證券名稱", "國際證券編碼", "發行股數",
            "外資及陸資尚可投資股數", "全體外資及陸資持有股數",
            "外資及陸資尚可投資比率", "全體外資及陸資持股比率",
            "外資及陸資共用法令投資上限比率", "陸資法令投資上限比率",
            "與前日異動原因(註)", "最近一次上市公司申報外資及陸資持股異動日期",
        ),
    })

    def resource(self, request: ForeignHoldingRequest) -> SourceResource:
        query = urlencode(
            {
                "date": request.trade_date.strftime("%Y%m%d"),
                "selectType": "ALLBUT0999",
                "response": "json",
            }
        )
        return SourceResource(
            resource_key=f"{self.source}:foreign_holding:{request.trade_date.isoformat()}",
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: ForeignHoldingRequest
    ) -> ParsedForeignHolding:
        try:
            payload = json.loads(content, parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SourceDataError("invalid_json", f"invalid source JSON: {error}") from error
        if not isinstance(payload, dict):
            raise SourceDataError("schema_mismatch", "source JSON must be an object")
        if payload.get("stat") != "OK":
            raise SourceDataError(
                "source_status", f"TWSE response status: {payload.get('stat')!r}"
            )
        if payload.get("date") != request.trade_date.strftime("%Y%m%d"):
            raise SourceDataError(
                "date_mismatch",
                f"TWSE answered for {payload.get('date')!r}, not "
                f"{request.trade_date.isoformat()}",
            )
        variant = self._variant(payload.get("fields"))
        data = payload.get("data")
        total = payload.get("total")
        # A closed date answers OK with total 0 and no data at all.
        if total == 0 and not data:
            raise SourceDataError(
                "no_data_for_date",
                f"TWSE has no foreign holding for {request.trade_date.isoformat()}",
            )
        if payload.get("hints") != self.unit_hint:
            raise SourceDataError(
                "unit_declaration_changed",
                f"TWSE declares {payload.get('hints')!r}, not {self.unit_hint!r}",
            )
        if not isinstance(data, list) or not data:
            raise SourceDataError("schema_mismatch", "TWSE data is not a non-empty list")
        if not isinstance(total, int) or isinstance(total, bool) or total != len(data):
            raise SourceDataError(
                "schema_mismatch", f"TWSE declares {total!r} rows but lists {len(data)}"
            )
        header = self.variants[variant]
        table: list[tuple[str, Sequence[object]]] = []
        for number, raw in enumerate(data, 1):
            if not isinstance(raw, list) or len(raw) != len(header):
                raise SourceDataError(
                    "schema_mismatch", f"TWSE row {number} has an invalid shape"
                )
            code = self._text(raw[0], number, "?")
            cells = list(raw[3:])
            # A security that has not filed yet gets the integer 0 as its
            # last-update date (4581 on 2020-03-06, found by the backfill);
            # MOPS leaves the same cell blank. Only that exact value is read
            # as "no date": anything else not text still fails the file.
            if cells[-1] == 0 and type(cells[-1]) is int:
                cells[-1] = ""
            # Skip name and ISIN: security metadata owns both.
            table.append((code, cells))
        return ParsedForeignHolding(
            market=self.market,
            trade_date=request.trade_date,
            rows=self._rows(request, table),
            header_variant=variant,
            source_fields=header,
        )


_MOPS_NO_DATA = "查無所需資料"
_TH = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL | re.IGNORECASE)
_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TITLE = "外資及陸資投資持股統計"


class MOPSForeignHoldingAdapter(ForeignHoldingAdapter):
    """MOPS `t13sa150_otc` — 上櫃 外資及陸資投資持股統計."""

    source = "mops_t13sa150_otc"
    market = "TPEx"
    version = "mops-t13sa150-otc:v2"
    endpoint = "https://mopsov.twse.com.tw/server-java/t13sa150_otc"
    encoding = "cp950"
    variants = MappingProxyType({
        "t13sa150_11": (
            "證券代號", "證券名稱", "發行股數",
            "外資及陸資尚可投資股數", "全體外資及陸資持有股數",
            "外資及陸資尚可投資比率", "全體外資及陸資持股比率",
            "外資及陸資共用法令投資上限比率", "陸資法令投資上限比率",
            "與前日異動原因(註)", "最近一次上櫃公司申報外資持股異動日期",
        ),
    })

    def resource(self, request: ForeignHoldingRequest) -> SourceResource:
        day = request.trade_date
        return SourceResource.form_post(
            resource_key=f"{self.source}:foreign_holding:{day.isoformat()}",
            source_uri=self.endpoint,
            # The legacy scraper's form (my_stock_project fetch_daily_otc.py):
            # a Gregorian year, zero-padded month and day, no security filter.
            fields=(
                ("step", "2"),
                ("years", f"{day.year}"),
                ("months", f"{day.month:02d}"),
                ("days", f"{day.day:02d}"),
                ("bcode", ""),
            ),
            headers=(("Accept", "text/html"),),
        )

    def parse(
        self, content: bytes, request: ForeignHoldingRequest
    ) -> ParsedForeignHolding:
        try:
            page = content.decode(self.encoding)
        except UnicodeDecodeError as error:
            raise SourceDataError(
                "unusable_response", f"MOPS page is not {self.encoding}: {error}"
            ) from error
        headings = [_cell(text) for text in _TH.findall(page)]
        if not headings:
            if _MOPS_NO_DATA in page:
                raise SourceDataError(
                    "no_data_for_date",
                    f"MOPS has no foreign holding for {request.trade_date.isoformat()}",
                )
            raise SourceDataError(
                "unusable_response", "MOPS answered a page with no holding table"
            )
        day = request.trade_date
        expected_title = f"{day.year - _ROC_OFFSET}/{day:%m/%d}　{_TITLE}"
        if headings[0] != expected_title:
            raise SourceDataError(
                "date_mismatch", f"MOPS table is titled {headings[0]!r}, not {expected_title!r}"
            )
        variant_fields = headings[1:12]
        variant = self._variant(variant_fields)
        header = self.variants[variant]
        table: list[tuple[str, Sequence[object]]] = []
        for number, row in enumerate(_TR.findall(page), 1):
            cells = [_cell(text) for text in _TD.findall(row)]
            if not cells:
                continue
            if len(cells) != len(header):
                raise SourceDataError(
                    "schema_mismatch", f"MOPS row {number} has {len(cells)} cells"
                )
            # Skip the name: security metadata owns it.
            table.append((cells[0], cells[2:]))
        if not table:
            raise SourceDataError(
                "no_data_for_date",
                f"MOPS lists no security for {request.trade_date.isoformat()}",
            )
        return ParsedForeignHolding(
            market=self.market,
            trade_date=request.trade_date,
            rows=self._rows(request, table),
            header_variant=variant,
            source_fields=header,
        )


def _cell(text: str) -> str:
    """A cell's text: tags and line breaks dropped, entities decoded, trimmed."""
    return html.unescape(_TAG.sub("", _BR.sub("", text))).replace("\xa0", " ").strip()
