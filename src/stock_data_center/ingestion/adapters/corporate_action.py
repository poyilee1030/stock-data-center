"""Exchange corporate-action result feeds (ROADMAP Step 19, Invariant G(2)).

Each list row records an event the exchange executed and priced, so the
exchange's own locator is the event's identity — `"<feed>:<locator dates>"` —
and nothing from the row's terms. TPEx publishes no locator, so its executed
date stands in. The three TPEx feeds are self-contained. TWSE's list rows
publish their amounts only on per-event detail pages, which `TWT49UDetail`
and `TWTAVUDetail` serve; `TWTB8UDetail` repeats the list row and publishes no
exchange ratio, so TWSE par-value changes are stored as `other`.

A current-year file lists results for dates that have not come yet: fetched on
2026-09-16, `revivt` listed 2026-09-21, and TWSE's TWTAUU 2026-10-19. Those are
not executed events, so a request says through which date rows count, and later
rows are only counted. The security name is dropped as well: a rename would
otherwise revise every past event. So are TWT49U's `最近一次申報*` columns, the
issuer's latest filing at fetch time — every 2024 row carries 115年第2季.
"""

from __future__ import annotations

import html
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from urllib.parse import urlencode

from stock_data_center.ingestion.models import (
    CorporateActionDetailRequest,
    CorporateActionRangeRequest,
    CorporateActionRow,
    ExchangeLocator,
    ParsedCorporateActionDetail,
    ParsedCorporateActionList,
    SourceDataError,
    SourceResource,
)
from stock_data_center.market_reference.models import (
    CorporateActionObservation,
    SignedTwdAmount,
    TwdAmount,
)

_THOUSAND = Decimal(1000)
_TWSE_EMPTY = "很抱歉，沒有符合條件的資料!"
_TAGS = re.compile(r"<[^>]*>")
_CELLS = re.compile(r"<th>(.*?)</th>\s*<td>(.*?)</td>", re.DOTALL)

# 權/息 → canonical type. TWSE drops the 除 prefix TPEx spells out.
_EX_TYPES = MappingProxyType({
    "息": "ex_dividend", "權": "ex_right", "權息": "ex_right_dividend",
    "除息": "ex_dividend", "除權": "ex_right", "除權息": "ex_right_dividend",
})
_PAR_VALUE_CHANGE = "變更股票面額"


class CorporateActionListAdapter(ABC):
    """One result feed's executed events over a requested range."""

    dataset_code = "corporate_action"
    source: str
    feed: str
    market: str
    version: str
    endpoint: str
    fields: tuple[str, ...]

    @abstractmethod
    def resource(self, request: CorporateActionRangeRequest) -> SourceResource: ...

    @abstractmethod
    def parse(
        self, content: bytes, request: CorporateActionRangeRequest
    ) -> ParsedCorporateActionList: ...

    def observation(
        self,
        row: CorporateActionRow,
        detail: ParsedCorporateActionDetail | None = None,
    ) -> CorporateActionObservation:
        """The row as a storable observation, with its detail where it has one."""
        if row.detail_request is not None and detail is None:
            raise SourceDataError(
                "detail_required",
                f"{row.locator.source_event_key} for {row.security_code} publishes "
                f"its terms only on its detail page",
            )
        if row.detail_request is None and detail is not None:
            raise ValueError(f"{self.feed} rows take no detail")
        if detail is not None and detail.security_code != row.security_code:
            raise SourceDataError(
                "invalid_identity",
                f"detail for {detail.security_code} cannot complete "
                f"{row.security_code}",
            )
        return self._observation(row, detail)

    @abstractmethod
    def _observation(
        self, row: CorporateActionRow, detail: ParsedCorporateActionDetail | None
    ) -> CorporateActionObservation: ...

    def _resource_key(self, request: CorporateActionRangeRequest) -> str:
        return f"{self.source}:{request.start.isoformat()}:{request.end.isoformat()}"

    def _parsed(
        self,
        request: CorporateActionRangeRequest,
        rows: list[CorporateActionRow],
        skipped: int,
    ) -> ParsedCorporateActionList:
        # One event per (security, locator) and per (security, executed date):
        # a repeat would make the key name two rows.
        for key in (
            lambda item: (item.security_code, item.locator.source_event_key),
            lambda item: (item.security_code, item.event_date),
        ):
            seen = [key(item) for item in rows]
            if len(seen) != len(set(seen)):
                repeated = sorted({item for item in seen if seen.count(item) > 1})
                raise SourceDataError(
                    "ambiguous_identity",
                    f"{self.feed} publishes {repeated[:5]!r} more than once",
                )
        return ParsedCorporateActionList(
            feed=self.feed,
            market=self.market,
            start=request.start,
            end=request.end,
            executed_through=request.executed_through,
            rows=tuple(rows),
            not_yet_executed=skipped,
            source_fields=self.fields,
        )


# --- TWSE ----------------------------------------------------------------------


class _TWSEListAdapter(CorporateActionListAdapter):
    market = "TWSE"
    locator_dates: int

    def resource(self, request: CorporateActionRangeRequest) -> SourceResource:
        query = urlencode(
            {
                "startDate": request.start.strftime("%Y%m%d"),
                "endDate": request.end.strftime("%Y%m%d"),
                "response": "json",
            }
        )
        return SourceResource(
            resource_key=self._resource_key(request),
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: CorporateActionRangeRequest
    ) -> ParsedCorporateActionList:
        payload = _json_object(content)
        if payload.get("stat") != "OK":
            # TWSE answers a range with no event by this exact status and no
            # table at all. Anything else is not an empty range.
            if payload.get("stat") == _TWSE_EMPTY and "fields" not in payload:
                return self._parsed(request, [], 0)
            raise SourceDataError(
                "source_status", f"TWSE response status: {payload.get('stat')!r}"
            )
        _require_range(
            request,
            payload.get("strDate") or _dict(payload.get("params")).get("startDate"),
            payload.get("endDate") or _dict(payload.get("params")).get("endDate"),
            self.feed,
        )
        _require_fields(payload.get("fields"), self.fields, self.feed)

        rows: list[CorporateActionRow] = []
        skipped = 0
        for number, raw in enumerate(_list(payload.get("data"), self.feed), 1):
            values = _row(raw, len(self.fields), self.feed, number)
            event_date = self._event_date(values[0], number)
            _require_in_range(request, event_date, self.feed, number)
            if event_date > request.executed_through:
                skipped += 1
                continue
            code = _code(values[1], self.feed, number)
            locator = self._locator(
                values[self.fields.index("詳細資料")], code, event_date, number
            )
            rows.append(self._row(values, code, event_date, locator, number))
        return self._parsed(request, rows, skipped)

    def _locator(
        self, value: str, code: str, event_date: date, row_number: int
    ) -> ExchangeLocator:
        # TWSE's own page strips everything but word characters and commas
        # before using it, which is what turns `3432  ,20240110` into a locator.
        parts = re.sub(r"[^\w,.\-]", "", value).split(",")
        if len(parts) != 1 + self.locator_dates or parts[0] != code:
            raise SourceDataError(
                "invalid_identity",
                f"{self.feed} row {row_number} locator {value!r} does not name "
                f"{code}",
            )
        dates = tuple(
            _compact_date(part, f"{self.feed} row {row_number} locator")
            for part in parts[1:]
        )
        if list(dates) != sorted(dates) or dates[0] > event_date:
            raise SourceDataError(
                "invalid_identity",
                f"{self.feed} row {row_number} locator {value!r} does not lead "
                f"to {event_date.isoformat()}",
            )
        self._check_locator(dates, event_date, value, row_number)
        return ExchangeLocator(self.feed, code, dates)

    def _check_locator(
        self, dates: tuple[date, ...], event_date: date, value: str, row_number: int
    ) -> None:
        if dates[-1] != event_date:
            raise SourceDataError(
                "invalid_identity",
                f"{self.feed} row {row_number} locator {value!r} is not dated "
                f"{event_date.isoformat()}",
            )

    @abstractmethod
    def _event_date(self, value: str, row_number: int) -> date: ...

    @abstractmethod
    def _row(
        self,
        values: tuple[str, ...],
        code: str,
        event_date: date,
        locator: ExchangeLocator,
        row_number: int,
    ) -> CorporateActionRow: ...


class TWSEExRightAdapter(_TWSEListAdapter):
    """`TWT49U` — 除權除息計算結果表. Amounts come from `TWT49UDetail`."""

    source = "twse_twt49u"
    feed = "TWT49U"
    version = "twse-twt49u:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
    locator_dates = 1
    fields = (
        "資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價",
        "權值+息值", "權/息", "漲停價格", "跌停價格", "開盤競價基準",
        "減除股利參考價", "詳細資料", "最近一次申報資料 季別/日期",
        "最近一次申報每股 (單位)淨值", "最近一次申報每股 (單位)盈餘",
    )

    def _event_date(self, value: str, row_number: int) -> date:
        return _roc_date(value, f"TWT49U row {row_number} date")

    def _row(self, values, code, event_date, locator, row_number):
        label = values[6].strip()
        return CorporateActionRow(
            security_code=code,
            event_date=event_date,
            locator=locator,
            action_type=_ex_type(label, self.feed, row_number),
            source_event_type=label,
            fields={
                "ex_date": event_date,
                "close_before": _twd(values[3], "除權息前收盤價"),
                "official_reference_price": _twd(values[4], "除權息參考價"),
                "official_rights_dividend_value": _signed(values[5], "權值+息值"),
            },
            # Name (2), locator (11) and the latest-filing snapshot (12-14) are
            # not the event's terms.
            source_terms=_terms(
                zip(self.fields[7:11], values[7:11], strict=True)
            ),
            detail_request=CorporateActionDetailRequest(locator),
        )

    def _observation(self, row, detail):
        values = detail.values
        cash = values.get("cash_dividend")
        free = values.get("free_per_thousand")
        rights = values.get("rights_per_thousand")
        subscription = values.get("subscription_price")
        _check_ex_terms(row, cash=cash, stock=free or rights)
        _check_rights(row, rights=rights, subscription=subscription)
        return CorporateActionObservation(
            action_type=row.action_type,
            source_event_type=row.source_event_type,
            cash_dividend_per_share=_amount(cash),
            free_share_ratio=_per_thousand(free),
            rights_ratio=_per_thousand(rights),
            subscription_price=_amount(subscription),
            source_terms={**row.source_terms, **detail.source_terms},
            **row.fields,
        )


class TWSEReductionAdapter(_TWSEListAdapter):
    """`TWTAUU` — 減資恢復買賣參考價格. Terms come from `TWTAVUDetail`.

    The locator's date is TWSE's file date, one trading day before the halt in
    every sampled detail, not the resumption date the row is keyed by.
    """

    source = "twse_twtauu"
    feed = "TWTAUU"
    version = "twse-twtauu:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/reducation/TWTAUU"
    locator_dates = 1
    fields = (
        "恢復買賣日期", "股票代號", "名稱", "停止買賣前收盤價格", "恢復買賣參考價",
        "漲停價格", "跌停價格", "開盤競價基準", "除權參考價", "減資原因", "詳細資料",
    )
    reasons = frozenset({"退還股款", "彌補虧損"})

    def _event_date(self, value: str, row_number: int) -> date:
        return _roc_slashed(value, f"TWTAUU row {row_number} date")

    def _check_locator(self, dates, event_date, value, row_number):
        if dates[0] >= event_date:
            raise SourceDataError(
                "invalid_identity",
                f"TWTAUU row {row_number} locator {value!r} does not precede "
                f"resumption on {event_date.isoformat()}",
            )

    def _row(self, values, code, event_date, locator, row_number):
        reason = values[9].strip()
        if reason not in self.reasons:
            raise SourceDataError(
                "unknown_event_type",
                f"TWTAUU row {row_number} publishes reduction reason {reason!r}",
            )
        return CorporateActionRow(
            security_code=code,
            event_date=event_date,
            locator=locator,
            action_type="capital_reduction",
            source_event_type=reason,
            fields={
                "ex_date": event_date,
                "close_before": _twd(values[3], "停止買賣前收盤價格"),
                "official_reference_price": _twd(values[4], "恢復買賣參考價"),
            },
            source_terms=_terms(zip(self.fields[5:9], values[5:9], strict=True)),
            detail_request=CorporateActionDetailRequest(locator),
        )

    def _observation(self, row, detail):
        values = detail.values
        halt = values["halt_date"]
        if not row.locator.dates[0] <= halt < row.event_date:
            raise SourceDataError(
                "inconsistent_terms",
                f"{row.security_code} halts {halt.isoformat()}, outside "
                f"{row.locator.source_event_key} → {row.event_date.isoformat()}",
            )
        cash_return = values.get("cash_return")
        increase = values.get("cash_increase_shares")
        rights = values.get("rights_per_thousand")
        subscription = values.get("subscription_price")
        if row.source_event_type == "退還股款":
            if cash_return is None or increase is not None:
                raise SourceDataError(
                    "inconsistent_terms",
                    f"{row.security_code} refunds capital but publishes cash "
                    f"{cash_return!r} and a cash increase {increase!r}",
                )
            kind = "cash_refund"
        else:
            if cash_return is not None:
                raise SourceDataError(
                    "inconsistent_terms",
                    f"{row.security_code} offsets losses but returns {cash_return}",
                )
            kind = "loss_offset_with_cash_increase" if increase else "loss_offset"
        if (increase is None) != (rights is None):
            raise SourceDataError(
                "inconsistent_terms",
                f"{row.security_code} publishes a cash increase of {increase!r} "
                f"with {rights!r} per thousand",
            )
        _check_rights(row, rights=rights, subscription=subscription)
        return CorporateActionObservation(
            action_type="capital_reduction",
            capital_reduction_kind=kind,
            source_event_type=row.source_event_type,
            old_shares=_THOUSAND,
            new_shares=_required(values.get("new_shares_per_thousand"), "每壹仟股換發新股票"),
            capital_reduction_cash_return_per_share=_amount(cash_return),
            cash_dividend_per_share=_amount(values.get("cash_dividend")),
            rights_ratio=_per_thousand(rights),
            subscription_price=_amount(subscription),
            source_terms={
                **row.source_terms,
                **detail.source_terms,
                "停止買賣日期": halt.isoformat(),
            },
            **row.fields,
        )


class TWSEParValueChangeAdapter(_TWSEListAdapter):
    """`TWTB8U` — 變更股票面額恢復買賣參考價格.

    `TWTB8UDetail?STK_NO=&STOP_DATE=&RESUME_DATE=` was verified on 2026-09-16
    for 8070 and 2327: it repeats the list row and publishes no exchange ratio,
    so no share terms can be stored and no detail is fetched.
    """

    source = "twse_twtb8u"
    feed = "TWTB8U"
    version = "twse-twtb8u:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/change/TWTB8U"
    locator_dates = 2
    fields = (
        "恢復買賣日期", "股票代號", "名稱", "停止買賣前收盤價格", "恢復買賣參考價",
        "漲停價格", "跌停價格", "開盤競價基準", "詳細資料",
    )

    def _event_date(self, value: str, row_number: int) -> date:
        return _roc_slashed(value, f"TWTB8U row {row_number} date")

    def _row(self, values, code, event_date, locator, row_number):
        return CorporateActionRow(
            security_code=code,
            event_date=event_date,
            locator=locator,
            action_type="other",
            source_event_type=_PAR_VALUE_CHANGE,
            fields={
                "ex_date": event_date,
                "close_before": _twd(values[3], "停止買賣前收盤價格"),
                "official_reference_price": _twd(values[4], "恢復買賣參考價"),
            },
            source_terms={
                **_terms(zip(self.fields[5:8], values[5:8], strict=True)),
                "停止買賣日期": locator.dates[0].isoformat(),
            },
        )

    def _observation(self, row, detail):
        return CorporateActionObservation(
            action_type="other",
            source_event_type=row.source_event_type,
            source_terms=dict(row.source_terms),
            **row.fields,
        )


class _TWSEDetailAdapter(ABC):
    dataset_code = "corporate_action"
    source: str
    feed: str
    version: str
    endpoint: str
    date_parameter: str

    def resource(self, request: CorporateActionDetailRequest) -> SourceResource:
        locator = request.locator
        if locator.feed != self.feed:
            raise ValueError(f"{type(self).__name__} completes {self.feed} rows only")
        query = urlencode(
            {
                "STK_NO": locator.security_code,
                self.date_parameter: locator.dates[0].strftime("%Y%m%d"),
                "response": "json",
            }
        )
        return SourceResource(
            resource_key=(
                f"{self.source}:detail:{locator.security_code}:"
                f"{locator.source_event_key}"
            ),
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: CorporateActionDetailRequest
    ) -> ParsedCorporateActionDetail:
        payload = _json_object(content)
        stat = payload.get("stat")
        if not isinstance(stat, str) or stat.upper() != "OK":
            # The detail answers `無相關資料` for a locator it does not know. That
            # is an event without terms, which must not read as "no dividend".
            if "fields" not in payload:
                raise SourceDataError(
                    "no_data_for_date",
                    f"{self.feed} detail has nothing for "
                    f"{request.locator.source_event_key}: {stat!r}",
                )
            raise SourceDataError("source_status", f"detail status: {stat!r}")
        fields = payload.get("fields")
        data = _list(payload.get("data"), self.feed)
        if len(data) != 1:
            raise SourceDataError(
                "schema_mismatch", f"{self.feed} detail has {len(data)} rows"
            )
        variant, labels = self._variant(fields)
        values = _row(data[0], len(labels), f"{self.feed} detail", 1)
        code = _code(values[0], f"{self.feed} detail", 1)
        if code != request.locator.security_code:
            raise SourceDataError(
                "invalid_identity",
                f"{self.feed} detail is for {code}, not "
                f"{request.locator.security_code}",
            )
        return self._parse(variant, labels, values, code)

    def _variant(self, fields: object) -> tuple[str, tuple[str, ...]]:
        for variant, labels in self.variants.items():
            if isinstance(fields, list) and tuple(fields) == labels:
                return variant, labels
        raise SourceDataError(
            "schema_mismatch", f"{self.feed} detail header changed: {fields!r}"
        )

    variants: Mapping[str, tuple[str, ...]]

    @abstractmethod
    def _parse(
        self, variant: str, labels: tuple[str, ...], values: tuple[str, ...], code: str
    ) -> ParsedCorporateActionDetail: ...


class TWSEDividendDetailAdapter(_TWSEDetailAdapter):
    """`TWT49UDetail` — two headers, for common and for preferred shares.

    Sampled for 53 events across 2020-2026, ETFs, TDRs and preferred shares
    included. `(增資配股) 除權` was empty in every one; if it is not, its text is
    kept as a source term rather than interpreted.
    """

    source = TWSEExRightAdapter.source
    feed = "TWT49U"
    version = "twse-twt49u-detail:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail"
    date_parameter = "T1"
    variants = MappingProxyType({
        "common": (
            "股票代號", "股票名稱", "(每股配發現金股利)除息", "(增資配股) 除權",
            "A. 按普通股股東持股比例每千股無償配股", "B. 員工紅利轉增資",
            "C. (有償) 現金增資", "每股認購金額", "a. 公開承銷", "b. 員工認購",
            " c. 原股東認購", "按股東持股比例每千股認購",
        ),
        "preferred": (
            "股票代號", "股票名稱", "(每股配發現金股利)除息", "(增資配股) 除權",
            "F. 按特別股股東持股比例每千股無償配股",
            "G. 按特別股股東持股比例每千股有償認股", "每股認購金額",
        ),
    })

    def _parse(self, variant, labels, values, code):
        cash = _unit(values[2], "元／股", labels[2])
        terms: dict[str, str] = {}
        if values[3].strip():
            terms[labels[3]] = values[3].strip()
        if variant == "common":
            detail = {
                "cash_dividend": cash,
                "free_per_thousand": _unit(values[4], "股", labels[4]),
                "subscription_price": _unit(values[7], "元／股", labels[7]),
                "rights_per_thousand": _unit(values[11], "股", labels[11]),
            }
            for index in (5, 6, 8, 9, 10):
                amount = _unit(values[index], "股", labels[index])
                if amount is not None:
                    terms[labels[index].strip()] = str(amount)
        else:
            detail = {
                "cash_dividend": cash,
                "free_per_thousand": _unit(values[4], "股", labels[4]),
                "rights_per_thousand": _unit(values[5], "股", labels[5]),
                "subscription_price": _unit(values[6], "元／股", labels[6]),
            }
        return ParsedCorporateActionDetail(
            security_code=code,
            variant=variant,
            values=detail,
            source_terms=terms,
            source_fields=labels,
        )


class TWSEReductionDetailAdapter(_TWSEDetailAdapter):
    """`TWTAVUDetail` — the reduction's share exchange and cash terms."""

    source = TWSEReductionAdapter.source
    feed = "TWTAUU"
    version = "twse-twtavu-detail:v1"
    endpoint = "https://www.twse.com.tw/rwd/zh/reducation/TWTAVUDetail"
    date_parameter = "FILE_DATE"
    variants = MappingProxyType({
        "reduction": (
            "股票代號：", "股票名稱：", "停止買賣日期：", "每壹仟股換發新股票：",
            "每股退還股款：", "原股每股配發現金股利：", "減資並(有償)現金增資：",
            "每股認購金額：", "a. 公開承銷：", "b. 員工認購：", "c. 原股東認購：",
            "按股東持股比例每千股認購：",
        ),
    })

    def _parse(self, variant, labels, values, code):
        terms = {}
        for index in (8, 9, 10):
            amount = _unit(values[index], "股", labels[index])
            if amount is not None:
                terms[labels[index].rstrip("：")] = str(amount)
        return ParsedCorporateActionDetail(
            security_code=code,
            variant=variant,
            values={
                "halt_date": _roc_slashed(values[2], labels[2]),
                "new_shares_per_thousand": _unit(values[3], "股", labels[3]),
                "cash_return": _unit(values[4], "元/股", labels[4]),
                "cash_dividend": _unit(values[5], "元/股", labels[5]),
                "cash_increase_shares": _unit(values[6], "股", labels[6]),
                "subscription_price": _unit(values[7], "元/股", labels[7]),
                "rights_per_thousand": _unit(values[11], "股", labels[11]),
            },
            source_terms=terms,
            source_fields=labels,
        )


# --- TPEx ------------------------------------------------------------------------


class _TPExListAdapter(CorporateActionListAdapter):
    market = "TPEx"

    def resource(self, request: CorporateActionRangeRequest) -> SourceResource:
        query = urlencode(
            {
                "startDate": request.start.strftime("%Y/%m/%d"),
                "endDate": request.end.strftime("%Y/%m/%d"),
                "response": "json",
            }
        )
        return SourceResource(
            resource_key=self._resource_key(request),
            source_uri=f"{self.endpoint}?{query}",
        )

    def parse(
        self, content: bytes, request: CorporateActionRangeRequest
    ) -> ParsedCorporateActionList:
        payload = _json_object(content)
        if payload.get("stat") != "ok":
            raise SourceDataError(
                "source_status", f"TPEx response status: {payload.get('stat')!r}"
            )
        echoed = payload.get("date")
        start, _, end = echoed.partition("~") if isinstance(echoed, str) else ("", "", "")
        _require_range(request, start, end, self.feed)
        tables = payload.get("tables")
        if not isinstance(tables, list) or len(tables) != 1 or not isinstance(
            tables[0], dict
        ):
            raise SourceDataError("schema_mismatch", f"{self.feed} has no single table")
        _require_fields(tables[0].get("fields"), self.fields, self.feed)

        rows: list[CorporateActionRow] = []
        skipped = 0
        for number, raw in enumerate(_list(tables[0].get("data"), self.feed), 1):
            values = _row(raw, len(self.fields), self.feed, number)
            event_date = self._event_date(values[0], number)
            _require_in_range(request, event_date, self.feed, number)
            if event_date > request.executed_through:
                skipped += 1
                continue
            code = _code(values[1], self.feed, number)
            locator = ExchangeLocator(self.feed, code, (event_date,))
            rows.append(self._row(values, code, event_date, locator, number))
        return self._parsed(request, rows, skipped)

    @abstractmethod
    def _event_date(self, value: str, row_number: int) -> date: ...

    @abstractmethod
    def _row(self, values, code, event_date, locator, row_number) -> CorporateActionRow: ...

    def _observation(self, row, detail):
        return CorporateActionObservation(
            source_event_type=row.source_event_type,
            source_terms=dict(row.source_terms),
            **row.fields,
        )


class TPExExRightDailyAdapter(_TPExListAdapter):
    """`bulletin/exDailyQ` — 除權除息計算結果表, amounts included."""

    source = "tpex_exdailyq"
    feed = "exDailyQ"
    version = "tpex-exdailyq:v1"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ"
    fields = (
        "除權息日期", "代號", "名稱", "除權息前收盤價", "除權息參考價", "權值", "息值",
        "權值+息值", "權/息", "漲停價", "跌停價", "開始交易基準價", "減除股利參考價",
        "現金股利", "每仟股無償配股", "現金增資股數", "現金增資認購價", "公開承銷股數",
        "員工認購股數", "原股東認購股數", "按持股比例仟股認購",
    )

    def _event_date(self, value: str, row_number: int) -> date:
        return _roc_slashed(value, f"exDailyQ row {row_number} date")

    def _row(self, values, code, event_date, locator, row_number):
        label = values[8].strip()
        cash = _positive(values[13], "現金股利")
        free = _positive(values[14], "每仟股無償配股")
        subscription = _positive(values[16], "現金增資認購價")
        rights = _positive(values[20], "按持股比例仟股認購")
        return CorporateActionRow(
            security_code=code,
            event_date=event_date,
            locator=locator,
            action_type=_ex_type(label, self.feed, row_number),
            source_event_type=label,
            fields={
                "action_type": _ex_type(label, self.feed, row_number),
                "ex_date": event_date,
                "close_before": _twd(values[3], "除權息前收盤價"),
                "official_reference_price": _twd(values[4], "除權息參考價"),
                "official_rights_dividend_value": _signed(values[7], "權值+息值"),
                "cash_dividend_per_share": _amount(cash),
                "free_share_ratio": _per_thousand(free),
                "rights_ratio": _per_thousand(rights),
                "subscription_price": _amount(subscription),
            },
            source_terms=_terms(
                (self.fields[index], values[index])
                for index in (5, 6, 9, 10, 11, 12, 15, 17, 18, 19)
            ),
        )

    def _observation(self, row, detail):
        fields = row.fields
        _check_ex_terms(
            row,
            cash=fields["cash_dividend_per_share"],
            stock=fields["free_share_ratio"] or fields["rights_ratio"],
        )
        _check_rights(
            row, rights=fields["rights_ratio"], subscription=fields["subscription_price"]
        )
        return super()._observation(row, detail)


class TPExReductionAdapter(_TPExListAdapter):
    """`bulletin/revivt` — 減資恢復交易參考價, its detail inline as HTML."""

    source = "tpex_revivt"
    feed = "revivt"
    version = "tpex-revivt:v1"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/bulletin/revivt"
    fields = (
        "恢復買賣日期", "股票代號", "名稱", "最後交易日之收盤價格",
        "減資恢復買賣開始日參考價格", "漲停價格", "跌停價格", "開始交易基準價",
        "除權參考價", "減資原因", "詳細資料",
    )
    detail_labels = (
        "股票代號/股票名稱:", "停止買賣日期:", "恢復買賣日期:", "每壹仟股換發新股票:",
        "每股退還股款:", "現金增資總股數:", "現金增資認購價:", "現金增資配股率:",
    )
    kinds = MappingProxyType({"現金減資": "cash_refund", "彌補虧損": "loss_offset"})

    def _event_date(self, value: str, row_number: int) -> date:
        return _roc_compact(value, f"revivt row {row_number} date")

    def _row(self, values, code, event_date, locator, row_number):
        reason = values[9].strip()
        if reason not in self.kinds:
            raise SourceDataError(
                "unknown_event_type",
                f"revivt row {row_number} publishes reduction reason {reason!r}",
            )
        detail = _html_cells(values[10], self.detail_labels, self.feed, row_number)
        _require_detail_code(detail[0], code, self.feed, row_number)
        halt = _roc_slashed(detail[1], f"revivt row {row_number} halt date")
        if _roc_slashed(detail[2], f"revivt row {row_number} resumption") != event_date:
            raise SourceDataError(
                "date_mismatch",
                f"revivt row {row_number} detail resumes on {detail[2]!r}, not "
                f"{event_date.isoformat()}",
            )
        return CorporateActionRow(
            security_code=code,
            event_date=event_date,
            locator=locator,
            action_type="capital_reduction",
            source_event_type=reason,
            fields={
                "ex_date": event_date,
                "close_before": _twd(values[3], "最後交易日之收盤價格"),
                "official_reference_price": _twd(values[4], "減資恢復買賣開始日參考價格"),
                "new_shares": _unit(detail[3], "股", "每壹仟股換發新股票"),
                "cash_return": _unit(detail[4], "元/股", "每股退還股款"),
                "halt_date": halt,
                "cash_increase": tuple(detail[5:8]),
            },
            source_terms={
                **_terms(zip(self.fields[5:9], values[5:9], strict=True)),
                "停止買賣日期": halt.isoformat(),
            },
        )

    def _observation(self, row, detail):
        fields = row.fields
        if any(value != "NA" for value in fields["cash_increase"]):
            # Every row 2020-2026 publishes NA here, so the ratio's unit has
            # never been seen. Guessing it would invent the rights ratio.
            raise SourceDataError(
                "unsupported_terms",
                f"{row.security_code} publishes a cash increase "
                f"{fields['cash_increase']!r} this adapter has not verified",
            )
        kind = self.kinds[row.source_event_type]
        cash_return = fields["cash_return"]
        if (kind == "cash_refund") != (cash_return is not None):
            raise SourceDataError(
                "inconsistent_terms",
                f"{row.security_code} {row.source_event_type} returns {cash_return!r}",
            )
        return CorporateActionObservation(
            action_type="capital_reduction",
            capital_reduction_kind=kind,
            source_event_type=row.source_event_type,
            ex_date=fields["ex_date"],
            close_before=fields["close_before"],
            official_reference_price=fields["official_reference_price"],
            old_shares=_THOUSAND,
            new_shares=_required(fields["new_shares"], "每壹仟股換發新股票"),
            capital_reduction_cash_return_per_share=_amount(cash_return),
            source_terms=dict(row.source_terms),
        )


class TPExParValueChangeAdapter(_TPExListAdapter):
    """`bulletin/pvChgRslt` — 變更股票面額恢復交易參考價.

    Unlike TWSE, TPEx publishes the exchange ratio and both par values, so the
    event is a split or a reverse split with its shares stated, checked against
    the par values it follows from.
    """

    source = "tpex_pvchgrslt"
    feed = "pvChgRslt"
    version = "tpex-pvchgrslt:v1"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/bulletin/pvChgRslt"
    fields = (
        "恢復買賣日期", "證券代號", "證券名稱", "最後交易日之收盤價格",
        "恢復買賣開始參考價", "漲停價格", "跌停價格", "開始交易基準價", "詳細資料",
    )
    detail_labels = (
        "證券代號/證券名稱:", "停止買賣日期:", "恢復買賣日期:", "變更股票面額換股率:",
        "變更前股票面額:", "變更後股票面額:",
    )

    def _event_date(self, value: str, row_number: int) -> date:
        return _roc_compact(value, f"pvChgRslt row {row_number} date")

    def _row(self, values, code, event_date, locator, row_number):
        detail = _html_cells(values[8], self.detail_labels, self.feed, row_number)
        _require_detail_code(detail[0], code, self.feed, row_number)
        halt = _roc_slashed(detail[1], f"pvChgRslt row {row_number} halt date")
        if _roc_slashed(detail[2], f"pvChgRslt row {row_number} resumption") != event_date:
            raise SourceDataError(
                "date_mismatch",
                f"pvChgRslt row {row_number} detail resumes on {detail[2]!r}, "
                f"not {event_date.isoformat()}",
            )
        return CorporateActionRow(
            security_code=code,
            event_date=event_date,
            locator=locator,
            action_type="stock_split",
            source_event_type=_PAR_VALUE_CHANGE,
            fields={
                "ex_date": event_date,
                "close_before": _twd(values[3], "最後交易日之收盤價格"),
                "official_reference_price": _twd(values[4], "恢復買賣開始參考價"),
                "ratio": _decimal(detail[3], "變更股票面額換股率"),
                "par_before": _decimal(detail[4], "變更前股票面額"),
                "par_after": _decimal(detail[5], "變更後股票面額"),
            },
            source_terms={
                **_terms(zip(self.fields[5:8], values[5:8], strict=True)),
                "停止買賣日期": halt.isoformat(),
                "變更前股票面額": detail[4].strip(),
                "變更後股票面額": detail[5].strip(),
            },
        )

    def _observation(self, row, detail):
        fields = row.fields
        ratio, before, after = fields["ratio"], fields["par_before"], fields["par_after"]
        if not ratio or not before or not after or before / after != ratio or ratio == 1:
            raise SourceDataError(
                "inconsistent_terms",
                f"{row.security_code} exchanges {ratio} for par {before} → {after}",
            )
        return CorporateActionObservation(
            action_type="stock_split" if ratio > 1 else "reverse_split",
            source_event_type=row.source_event_type,
            ex_date=fields["ex_date"],
            close_before=fields["close_before"],
            official_reference_price=fields["official_reference_price"],
            old_shares=Decimal(1),
            new_shares=_plain(ratio),
            source_terms=dict(row.source_terms),
        )


# --- shared checks ---------------------------------------------------------------


def _check_ex_terms(
    row: CorporateActionRow, *, cash: object, stock: object
) -> None:
    """The published type must be what the terms say it is.

    Held on every one of 7,359 TPEx rows 2020-2026 and 53 sampled TWSE details.
    """
    expected = {
        "ex_dividend": (True, False),
        "ex_right": (False, True),
        "ex_right_dividend": (True, True),
    }[row.action_type]
    if (cash is not None, stock is not None) != expected:
        raise SourceDataError(
            "inconsistent_terms",
            f"{row.security_code} {row.locator.source_event_key} is "
            f"{row.source_event_type} but publishes cash {cash!r} and shares "
            f"{stock!r}",
        )


def _check_rights(row: CorporateActionRow, *, rights: object, subscription: object) -> None:
    if (rights is None) != (subscription is None):
        raise SourceDataError(
            "inconsistent_terms",
            f"{row.security_code} {row.locator.source_event_key} publishes rights "
            f"{rights!r} at price {subscription!r}",
        )


def _ex_type(label: str, feed: str, row_number: int) -> str:
    try:
        return _EX_TYPES[label]
    except KeyError:
        raise SourceDataError(
            "unknown_event_type", f"{feed} row {row_number} publishes type {label!r}"
        ) from None


def _require_range(
    request: CorporateActionRangeRequest, start: object, end: object, feed: str
) -> None:
    wanted = (request.start.strftime("%Y%m%d"), request.end.strftime("%Y%m%d"))
    if (start, end) != wanted:
        raise SourceDataError(
            "date_mismatch",
            f"{feed} answered for {start!r}~{end!r}, not {wanted[0]}~{wanted[1]}",
        )


def _require_in_range(
    request: CorporateActionRangeRequest, value: date, feed: str, row_number: int
) -> None:
    if not request.start <= value <= request.end:
        raise SourceDataError(
            "date_mismatch",
            f"{feed} row {row_number} is dated {value.isoformat()}, outside the "
            f"requested range",
        )


def _require_fields(fields: object, expected: tuple[str, ...], feed: str) -> None:
    if not isinstance(fields, list) or tuple(fields) != expected:
        raise SourceDataError(
            "schema_mismatch",
            f"{feed} header changed: expected {expected!r}, received {fields!r}",
        )


def _require_detail_code(cell: str, code: str, feed: str, row_number: int) -> None:
    published = cell.split("/", 1)[0].strip()
    if published != code:
        raise SourceDataError(
            "invalid_identity",
            f"{feed} row {row_number} detail is for {published!r}, not {code}",
        )


def _html_cells(
    value: str, labels: tuple[str, ...], feed: str, row_number: int
) -> tuple[str, ...]:
    cells = [
        (html.unescape(label).strip(), html.unescape(content).replace("\xa0", " ").strip())
        for label, content in _CELLS.findall(value)
    ]
    if tuple(label for label, _ in cells) != labels:
        raise SourceDataError(
            "schema_mismatch",
            f"{feed} row {row_number} detail labels changed: "
            f"{[label for label, _ in cells]!r}",
        )
    return tuple(content for _, content in cells)


def _json_object(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceDataError("invalid_json", f"invalid source JSON: {error}") from error
    if not isinstance(payload, dict):
        raise SourceDataError("schema_mismatch", "source JSON must be an object")
    return payload



def _list(value: object, feed: str) -> list[object]:
    if not isinstance(value, list):
        raise SourceDataError("schema_mismatch", f"{feed} data is not a list")
    return value


def _row(value: object, length: int, feed: str, row_number: int) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) != length
        or not all(isinstance(item, str) for item in value)
    ):
        raise SourceDataError(
            "schema_mismatch", f"{feed} row {row_number} has an invalid shape"
        )
    return tuple(value)


def _code(value: str, feed: str, row_number: int) -> str:
    code = value.strip()
    if not code or not code.isalnum():
        raise SourceDataError(
            "invalid_identity", f"{feed} row {row_number} code {value!r} is invalid"
        )
    return code


def _decimal(value: str, field: str, *, signed: bool = False) -> Decimal | None:
    normalized = _TAGS.sub("", value).replace(",", "").strip()
    if normalized in {"", "--", "N/A", "NA"}:
        return None
    try:
        result = Decimal(normalized)
    except InvalidOperation as error:
        raise SourceDataError(
            "invalid_numeric", f"{field} is not an unambiguous decimal: {value!r}"
        ) from error
    if not result.is_finite() or (result < 0 and not signed):
        raise SourceDataError("invalid_numeric", f"{field} is not a finite amount")
    return result


def _positive(value: str, field: str) -> Decimal | None:
    """A published zero means the item does not apply (TWT49U notes)."""
    result = _decimal(value, field)
    return result if result else None


def _unit(value: str, unit: str, field: str) -> Decimal | None:
    """A detail amount with its unit, which must be the declared one."""
    text = html.unescape(value).replace("\xa0", " ").strip()
    match = re.fullmatch(r"([\d,.]+)\s*" + re.escape(unit), text)
    if not match:
        raise SourceDataError(
            "unit_mismatch", f"{field.strip()} is {value!r}, not in {unit}"
        )
    return _positive(match[1], field.strip())


def _twd(value: str, field: str) -> TwdAmount:
    result = _decimal(value, field)
    if result is None:
        raise SourceDataError("missing_value", f"{field} is not published")
    return TwdAmount(result)


def _signed(value: str, field: str) -> SignedTwdAmount:
    result = _decimal(value, field, signed=True)
    if result is None:
        raise SourceDataError("missing_value", f"{field} is not published")
    return SignedTwdAmount(result)


def _amount(value: object) -> TwdAmount | None:
    return None if value is None else TwdAmount(value)


def _plain(value: Decimal) -> Decimal:
    """Trailing zeros dropped, without the exponent `normalize` gives 10."""
    value = value.normalize()
    return value.quantize(Decimal(1)) if value.as_tuple().exponent > 0 else value


def _per_thousand(value: object) -> Decimal | None:
    return None if value is None else _plain(value / _THOUSAND)


def _required(value: object, field: str) -> Decimal:
    if value is None:
        raise SourceDataError("missing_value", f"{field} is not published")
    return _plain(value)


def _terms(pairs) -> dict[str, str]:
    """Source columns kept verbatim, with only the digit grouping removed."""
    terms = {}
    for label, value in pairs:
        text = value.replace(",", "").strip()
        if text and text != "--":
            terms[label] = text
    return terms



def _roc_slashed(value: str, field: str) -> date:
    match = re.fullmatch(r"(\d{2,3})/(\d{2})/(\d{2})", value.strip())
    if not match:
        raise SourceDataError("invalid_date", f"{field} is not ROC YYY/MM/DD: {value!r}")
    return _date(int(match[1]) + 1911, int(match[2]), int(match[3]), value, field)


def _roc_compact(value: str, field: str) -> date:
    match = re.fullmatch(r"(\d{3})(\d{2})(\d{2})", value.strip())
    if not match:
        raise SourceDataError("invalid_date", f"{field} is not ROC YYYMMDD: {value!r}")
    return _date(int(match[1]) + 1911, int(match[2]), int(match[3]), value, field)



def _date(year: int, month: int, day: int, value: str, field: str) -> date:
    try:
        return date(year, month, day)
    except ValueError as error:
        raise SourceDataError("invalid_date", f"{field} is invalid: {value!r}") from error


def _dict(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _roc_date(value: str, field: str) -> date:
    match = re.fullmatch(r"(\d{2,3})年(\d{2})月(\d{2})日", value.strip())
    if not match:
        raise SourceDataError("invalid_date", f"{field} is not ROC 年月日: {value!r}")
    return _date(int(match[1]) + 1911, int(match[2]), int(match[3]), value, field)


def _compact_date(value: str, field: str) -> date:
    if not re.fullmatch(r"\d{8}", value):
        raise SourceDataError("invalid_identity", f"{field} date {value!r} is invalid")
    return _date(int(value[:4]), int(value[4:6]), int(value[6:]), value, field)
