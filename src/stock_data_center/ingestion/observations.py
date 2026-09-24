"""What the source adapters produce: observation and value types.

Moved here from the v1 domain packages in Step 35-d-1 so the adapters, and the
schema v2 jobs that read what they produce, no longer load the v1 writers,
services and resolvers those packages also hold. Definitions are unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class SecurityMetadataObservation:
    effective_from: date
    market: str
    name: str
    effective_to: date | None = None
    industry: str | None = None
    listed_on: date | None = None
    delisted_on: date | None = None

@dataclass(frozen=True, slots=True)
class DailyPriceObservation:
    trade_date: date
    open_price: Decimal | None = None
    high_price: Decimal | None = None
    low_price: Decimal | None = None
    close_price: Decimal | None = None
    volume: Decimal | None = None
    trade_value: Decimal | None = None
    trade_count: int | None = None
    price_change: Decimal | None = None
    price_direction: str | None = None
    bid_snapshot: str | None = None
    ask_snapshot: str | None = None
    last_bid_price: Decimal | None = None
    last_ask_price: Decimal | None = None
    last_bid_volume: Decimal | None = None
    last_ask_volume: Decimal | None = None

def _validate_decimal(
    name: str,
    value: Decimal | None,
    *,
    scale: int,
    nonnegative: bool = False,
    maximum: Decimal | None = None,
) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal or None")
    if value.as_tuple().exponent < -scale:
        raise ValueError(f"{name} has more than {scale} fractional places")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must not be negative")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must not exceed {maximum}")

_RATIO_COLUMN_MAXIMUM = Decimal("9999.99999999")

class QuantityScale(str, Enum):
    """Source scale for a stock quantity before canonical normalization."""

    SHARE = "share"
    LOT = "lot"

    @property
    def shares_per_unit(self) -> Decimal:
        return Decimal(1) if self is QuantityScale.SHARE else Decimal(1000)

@dataclass(frozen=True, slots=True)
class ShareQuantity:
    """A canonical whole quantity whose unit is exactly one share."""

    value: Decimal

    def __post_init__(self) -> None:
        _validate_decimal("share quantity", self.value, scale=0)

@dataclass(frozen=True, slots=True)
class SourceShareQuantity:
    """An explicitly scaled source value that can normalize to shares."""

    value: Decimal
    scale: QuantityScale

    def __post_init__(self) -> None:
        _validate_decimal("source share quantity", self.value, scale=0)
        if not isinstance(self.scale, QuantityScale):
            raise ValueError("scale must be QuantityScale.SHARE or QuantityScale.LOT")

    def to_canonical(self) -> ShareQuantity:
        return ShareQuantity(self.value * self.scale.shares_per_unit)

def _require_value(instance: object, names: tuple[str, ...]) -> None:
    if all(getattr(instance, name) is None for name in names):
        raise ValueError("at least one source value is required")

def _validate_quantities(
    instance: object,
    *,
    signed: frozenset[str] = frozenset(),
    exclude: frozenset[str] = frozenset({"trade_date"}),
) -> None:
    names = tuple(field.name for field in fields(instance) if field.name not in exclude)
    _require_value(instance, names)
    for name in names:
        value = getattr(instance, name)
        if value is not None and not isinstance(value, ShareQuantity):
            raise ValueError(
                f"{name} must be an explicitly canonical ShareQuantity"
            )
        if value is not None and name not in signed and value.value < 0:
            raise ValueError(f"{name} must not be negative")

@dataclass(frozen=True, slots=True)
class InstitutionalInvestorObservation:
    trade_date: date
    foreign_buy: ShareQuantity | None = None
    foreign_sell: ShareQuantity | None = None
    foreign_net: ShareQuantity | None = None
    foreign_dealer_buy: ShareQuantity | None = None
    foreign_dealer_sell: ShareQuantity | None = None
    foreign_dealer_net: ShareQuantity | None = None
    trust_buy: ShareQuantity | None = None
    trust_sell: ShareQuantity | None = None
    trust_net: ShareQuantity | None = None
    dealer_self_buy: ShareQuantity | None = None
    dealer_self_sell: ShareQuantity | None = None
    dealer_self_net: ShareQuantity | None = None
    dealer_hedge_buy: ShareQuantity | None = None
    dealer_hedge_sell: ShareQuantity | None = None
    dealer_hedge_net: ShareQuantity | None = None
    dealer_net: ShareQuantity | None = None
    total_net: ShareQuantity | None = None

    def __post_init__(self) -> None:
        _validate_quantities(
            self,
            signed=frozenset(
                {
                    "foreign_net",
                    "foreign_dealer_net",
                    "trust_net",
                    "dealer_self_net",
                    "dealer_hedge_net",
                    "dealer_net",
                    "total_net",
                }
            ),
        )

@dataclass(frozen=True, slots=True)
class ForeignHoldingObservation:
    trade_date: date
    issued_shares: ShareQuantity | None = None
    investable_shares: ShareQuantity | None = None
    held_shares: ShareQuantity | None = None
    investable_ratio: Decimal | None = None
    held_ratio: Decimal | None = None
    foreign_legal_limit_ratio: Decimal | None = None
    mainland_legal_limit_ratio: Decimal | None = None
    change_reason: str | None = None
    source_last_update_date: date | None = None

    def __post_init__(self) -> None:
        numeric_names = (
            "issued_shares",
            "investable_shares",
            "held_shares",
            "investable_ratio",
            "held_ratio",
            "foreign_legal_limit_ratio",
            "mainland_legal_limit_ratio",
        )
        if all(getattr(self, name) is None for name in numeric_names):
            raise ValueError("at least one holding value is required")
        for name in ("issued_shares", "investable_shares", "held_shares"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, ShareQuantity):
                raise ValueError(
                    f"{name} must be an explicitly canonical ShareQuantity"
                )
            if value is not None and value.value < 0:
                raise ValueError(f"{name} must not be negative")
        for name in (
            "investable_ratio",
            "held_ratio",
            "foreign_legal_limit_ratio",
            "mainland_legal_limit_ratio",
        ):
            _validate_decimal(
                name,
                getattr(self, name),
                scale=8,
                nonnegative=True,
                maximum=Decimal("100"),
            )
        if self.change_reason == "":
            raise ValueError("change_reason must be nonempty when supplied")

@dataclass(frozen=True, slots=True)
class InstitutionalMarketSummaryObservation:
    trade_date: date
    market: str
    institution: str
    buy: Decimal | None = None
    sell: Decimal | None = None
    net: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.market or not self.institution:
            raise ValueError("market and institution must be nonempty")
        _require_value(self, ("buy", "sell", "net"))
        _validate_decimal("buy", self.buy, scale=0, nonnegative=True)
        _validate_decimal("sell", self.sell, scale=0, nonnegative=True)
        _validate_decimal("net", self.net, scale=0)

@dataclass(frozen=True, slots=True)
class MarginTradingObservation:
    trade_date: date
    margin_buy: ShareQuantity | None = None
    margin_sell: ShareQuantity | None = None
    margin_cash_repayment: ShareQuantity | None = None
    margin_previous_balance: ShareQuantity | None = None
    margin_balance: ShareQuantity | None = None
    margin_next_limit: ShareQuantity | None = None
    margin_utilization_ratio: Decimal | None = None
    short_buy: ShareQuantity | None = None
    short_sell: ShareQuantity | None = None
    short_stock_repayment: ShareQuantity | None = None
    short_previous_balance: ShareQuantity | None = None
    short_balance: ShareQuantity | None = None
    short_next_limit: ShareQuantity | None = None
    short_utilization_ratio: Decimal | None = None
    offset_balance: ShareQuantity | None = None

    def __post_init__(self) -> None:
        quantity_names = tuple(
            field.name
            for field in fields(self)
            if field.name
            not in {
                "trade_date",
                "margin_utilization_ratio",
                "short_utilization_ratio",
            }
        )
        if (
            all(getattr(self, name) is None for name in quantity_names)
            and self.margin_utilization_ratio is None
            and self.short_utilization_ratio is None
        ):
            raise ValueError("at least one source value is required")
        for name in quantity_names:
            value = getattr(self, name)
            if value is not None and not isinstance(value, ShareQuantity):
                raise ValueError(
                    f"{name} must be an explicitly canonical ShareQuantity"
                )
            if value is not None and value.value < 0:
                raise ValueError(f"{name} must not be negative")
        # No business upper bound: the stop applies from the next business day,
        # so one day's buying can overshoot the limit (TPEx 00989B, 2026-07-14:
        # 103.1%). The only bound is what NUMERIC(12,8) can store, checked here
        # so that it fails as data rather than on every write (review of #34).
        for name in ("margin_utilization_ratio", "short_utilization_ratio"):
            _validate_decimal(
                name, getattr(self, name), scale=8, nonnegative=True,
                maximum=_RATIO_COLUMN_MAXIMUM,
            )

@dataclass(frozen=True, slots=True)
class SecuritiesLendingObservation:
    trade_date: date
    previous_balance: ShareQuantity | None = None
    borrowed: ShareQuantity | None = None
    returned: ShareQuantity | None = None
    balance: ShareQuantity | None = None
    next_limit: ShareQuantity | None = None
    next_available_limit: ShareQuantity | None = None
    adjustment: ShareQuantity | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        numeric_names = (
            "previous_balance",
            "borrowed",
            "returned",
            "balance",
            "next_limit",
            "next_available_limit",
            "adjustment",
        )
        _require_value(self, numeric_names)
        for name in numeric_names:
            value = getattr(self, name)
            if value is not None and not isinstance(value, ShareQuantity):
                raise ValueError(
                    f"{name} must be an explicitly canonical ShareQuantity"
                )
            if value is not None and name != "adjustment" and value.value < 0:
                raise ValueError(f"{name} must not be negative")
        if self.note == "":
            raise ValueError("note must be nonempty when supplied")

def _decimal(name: str, value: Decimal | None, scale: int, *, positive: bool = False) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal or None")
    if value.as_tuple().exponent < -scale:
        raise ValueError(f"{name} has more than {scale} fractional places")
    if positive and value < 0:
        raise ValueError(f"{name} must not be negative")

TWD_SCALE = 8

@dataclass(frozen=True, slots=True)
class TwdAmount:
    """Canonical TWD amount in major units (dollars)."""
    value: Decimal

    def __post_init__(self) -> None:
        _decimal("TWD amount", self.value, TWD_SCALE, positive=True)

@dataclass(frozen=True, slots=True)
class SignedTwdAmount:
    """A published TWD difference, which the source signs itself.

    `權值+息值` is 除權息前收盤價 − 除權息參考價. A rights issue priced above the
    close raises the reference, so the exchanges publish it negative — 3563 on
    2020-03-27 at −0.616165, TPEx 8444 on 2024-12-12 at −0.204602.
    """
    value: Decimal

    def __post_init__(self) -> None:
        _decimal("signed TWD amount", self.value, TWD_SCALE)

@dataclass(frozen=True, slots=True)
class MarketIndexObservation:
    trade_date: date
    close_value: Decimal
    open_value: Decimal | None = None
    high_value: Decimal | None = None
    low_value: Decimal | None = None
    change_points: Decimal | None = None
    change_percent: Decimal | None = None
    trade_value: TwdAmount | None = None

    def __post_init__(self) -> None:
        for name in ("open_value", "high_value", "low_value", "close_value"):
            _decimal(name, getattr(self, name), 8, positive=True)
        _decimal("change_points", self.change_points, 8)
        _decimal("change_percent", self.change_percent, 8)
        if self.trade_value is not None:
            if not isinstance(self.trade_value, TwdAmount):
                raise ValueError("trade_value must be a canonical TwdAmount")
            _decimal("trade_value", self.trade_value.value, 4)
        if self.high_value is not None and self.high_value < max(
            value for value in (self.open_value, self.close_value, self.low_value) if value is not None
        ):
            raise ValueError("high_value is inconsistent with OHLC")
        if self.low_value is not None and self.low_value > min(
            value for value in (self.open_value, self.close_value) if value is not None
        ):
            raise ValueError("low_value is inconsistent with OHLC")

ActionType = Literal[
    "cash_dividend", "earnings_stock_dividend", "capital_surplus_stock_dividend",
    "stock_split", "reverse_split", "rights_issue", "capital_reduction",
    "ex_dividend", "ex_right", "ex_right_dividend", "other",
]

ACTION_TYPES = frozenset(ActionType.__args__)

CapitalReductionKind = Literal[
    "cash_refund", "loss_offset", "loss_offset_with_cash_increase", "other",
]

CAPITAL_REDUCTION_KINDS = frozenset(CapitalReductionKind.__args__)

_MONEY_SCALES = {
    "cash_dividend_per_share": 8,
    "capital_reduction_cash_return_per_share": 8,
    "subscription_price": 6,
    "close_before": 6,
    "official_reference_price": 6,
    "official_rights_dividend_value": 6,
}

_SIGNED = frozenset({"official_rights_dividend_value"})

_SIGNED_MONEY = (TwdAmount, SignedTwdAmount)

_RATIO_SCALES = {
    "earnings_stock_ratio": 12,
    "capital_surplus_stock_ratio": 12,
    "free_share_ratio": 12,
    "rights_ratio": 12,
    "old_shares": 8,
    "new_shares": 8,
}

@dataclass(frozen=True, slots=True)
class CorporateActionObservation:
    action_type: ActionType
    capital_reduction_kind: CapitalReductionKind | None = None
    ex_date: date | None = None
    announcement_date: date | None = None
    record_date: date | None = None
    payment_date: date | None = None
    cash_dividend_per_share: TwdAmount | None = None
    capital_reduction_cash_return_per_share: TwdAmount | None = None
    earnings_stock_ratio: Decimal | None = None
    capital_surplus_stock_ratio: Decimal | None = None
    free_share_ratio: Decimal | None = None
    old_shares: Decimal | None = None
    new_shares: Decimal | None = None
    rights_ratio: Decimal | None = None
    subscription_price: TwdAmount | None = None
    close_before: TwdAmount | None = None
    official_reference_price: TwdAmount | None = None
    official_rights_dividend_value: TwdAmount | SignedTwdAmount | None = None
    source_event_type: str | None = None
    source_terms: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.action_type not in ACTION_TYPES:
            raise ValueError(f"unsupported corporate action type {self.action_type!r}")
        if (self.announcement_date is not None and self.ex_date is not None
                and self.announcement_date > self.ex_date):
            raise ValueError("announcement_date must not follow ex_date")
        if (self.ex_date is not None and self.record_date is not None
                and self.ex_date > self.record_date):
            raise ValueError("ex_date must not follow record_date")
        if (self.record_date is not None and self.payment_date is not None
                and self.record_date > self.payment_date):
            raise ValueError("record_date must not follow payment_date")
        money = tuple(_MONEY_SCALES)
        for name, scale in _MONEY_SCALES.items():
            value = getattr(self, name)
            if value is not None:
                allowed = _SIGNED_MONEY if name in _SIGNED else TwdAmount
                if not isinstance(value, allowed):
                    raise ValueError(f"{name} must be a canonical TwdAmount")
                _decimal(name, value.value, scale)
        ratios = tuple(_RATIO_SCALES)
        for name, scale in _RATIO_SCALES.items():
            _decimal(name, getattr(self, name), scale, positive=True)
            if getattr(self, name) == 0:
                raise ValueError(f"{name} must be positive")
        if (self.old_shares is None) != (self.new_shares is None):
            raise ValueError("old_shares and new_shares must be supplied together")
        component_total = sum(
            value or Decimal(0)
            for value in (self.earnings_stock_ratio, self.capital_surplus_stock_ratio)
        )
        if self.free_share_ratio is not None and self.free_share_ratio < component_total:
            raise ValueError("free_share_ratio must include its declared components")
        required = {
            "cash_dividend": self.cash_dividend_per_share,
            "earnings_stock_dividend": self.earnings_stock_ratio,
            "capital_surplus_stock_dividend": self.capital_surplus_stock_ratio,
            "rights_issue": self.rights_ratio,
        }
        if self.action_type in required and required[self.action_type] is None:
            raise ValueError(f"{self.action_type} requires its explicit economic term")
        if (self.action_type == "cash_dividend"
                and self.cash_dividend_per_share is not None
                and self.cash_dividend_per_share.value == 0):
            raise ValueError("cash_dividend_per_share must be positive")
        if (self.capital_reduction_kind is not None
                and self.capital_reduction_kind not in CAPITAL_REDUCTION_KINDS):
            raise ValueError(
                f"unsupported capital reduction kind {self.capital_reduction_kind!r}"
            )
        if self.action_type == "capital_reduction":
            if self.capital_reduction_kind is None:
                raise ValueError("capital_reduction requires capital_reduction_kind")
        elif (self.capital_reduction_kind is not None
                or self.capital_reduction_cash_return_per_share is not None):
            raise ValueError("capital-reduction terms require action_type capital_reduction")
        cash_return = self.capital_reduction_cash_return_per_share
        if cash_return is not None and cash_return.value <= 0:
            raise ValueError("capital_reduction_cash_return_per_share must be positive")
        if self.capital_reduction_kind == "cash_refund" and cash_return is None:
            raise ValueError("cash_refund requires cash return per share")
        if (self.capital_reduction_kind in {
                "loss_offset", "loss_offset_with_cash_increase"
        } and cash_return is not None):
            raise ValueError("loss-offset capital reduction must not return cash")
        if self.action_type in {"stock_split", "reverse_split", "capital_reduction"}:
            if self.old_shares is None or self.new_shares is None:
                raise ValueError(f"{self.action_type} requires old_shares and new_shares")
            increasing = self.new_shares > self.old_shares
            if self.action_type == "stock_split" and not increasing:
                raise ValueError("stock_split must increase shares")
            if self.action_type in {"reverse_split", "capital_reduction"} and increasing:
                raise ValueError(f"{self.action_type} must not increase shares")
            if self.new_shares == self.old_shares:
                raise ValueError(f"{self.action_type} must change shares")
        if (self.action_type in {"ex_dividend", "ex_right", "ex_right_dividend"}
                and self.ex_date is None):
            raise ValueError(f"{self.action_type} requires ex_date")
        if self.source_event_type == "":
            raise ValueError("source_event_type must be nonempty when supplied")
        if not any(
            getattr(self, name) is not None
            for name in ("announcement_date", "record_date", "payment_date", *money,
                         *ratios, "source_event_type")
        ) and self.ex_date is None and not self.source_terms:
            raise ValueError("at least one corporate-action term is required")
        object.__setattr__(self, "source_terms", self.source_terms or {})

@dataclass(frozen=True, slots=True)
class OfficialValuationObservation:
    trade_date: date
    pe_ratio: Decimal | None = None
    pb_ratio: Decimal | None = None
    dividend_yield: Decimal | None = None
    dividend_year: int | None = None
    dividend_per_share: TwdAmount | None = None
    report_period: str | None = None

    def __post_init__(self) -> None:
        if all(value is None for value in (
            self.pe_ratio, self.pb_ratio, self.dividend_yield, self.dividend_per_share
        )):
            raise ValueError("at least one source-published valuation is required")
        for name in ("pe_ratio", "pb_ratio"):
            _decimal(name, getattr(self, name), 8, positive=True)
            value = getattr(self, name)
            if value is not None and value == 0:
                raise ValueError(f"{name} must be positive")
        _decimal("dividend_yield", self.dividend_yield, 8, positive=True)
        if self.dividend_per_share is not None and not isinstance(self.dividend_per_share, TwdAmount):
            raise ValueError("dividend_per_share must be a canonical TwdAmount")
        if self.dividend_year is not None and not 1900 <= self.dividend_year <= 9999:
            raise ValueError("dividend_year is invalid")
        if self.report_period == "":
            raise ValueError("report_period must be nonempty")

@dataclass(frozen=True, order=True, slots=True)
class RevenuePeriod:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1900 <= self.year <= 9999:
            raise ValueError("year must be between 1900 and 9999")
        if not 1 <= self.month <= 12:
            raise ValueError("month must be between 1 and 12")

class RevenueScale(str, Enum):
    """Source-native amount scale relative to the currency's major unit."""

    MAJOR = "major"
    THOUSAND = "thousand"

    @property
    def multiplier(self) -> Decimal:
        return Decimal(1) if self is RevenueScale.MAJOR else Decimal(1000)

@dataclass(frozen=True, slots=True)
class SourceRevenueAmount:
    value: Decimal
    currency: str
    scale: RevenueScale

    def to_major_unit(self) -> tuple[Decimal, str]:
        return self.value * self.scale.multiplier, self.currency.upper()

@dataclass(frozen=True, slots=True)
class MonthlyRevenueObservation:
    period: RevenuePeriod
    revenue: Decimal
    currency: str
    # The comparatives MOPS publishes in the same row (Step 22), stored exactly
    # as published and never reconciled against our own series (audit §7.3).
    # Amounts are in the currency's major unit, percentages as printed.
    revenue_last_month: Decimal | None = None
    revenue_last_year_month: Decimal | None = None
    mom_pct: Decimal | None = None
    yoy_pct: Decimal | None = None
    cumulative_revenue: Decimal | None = None
    cumulative_revenue_last_year: Decimal | None = None
    cumulative_yoy_pct: Decimal | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if self.note == "":
            raise ValueError("note must be nonempty when supplied")

    @classmethod
    def from_source(
        cls,
        *,
        period: RevenuePeriod,
        amount: SourceRevenueAmount,
    ) -> MonthlyRevenueObservation:
        revenue, currency = amount.to_major_unit()
        return cls(period=period, revenue=revenue, currency=currency)

_PERCENT_SCALE = 8

TDCC_OPENDATA_V1 = "tdcc-opendata-v1"

@dataclass(frozen=True, slots=True)
class TDCCBucketObservation:
    """One source-native holding-level row, retained verbatim.

    Sign and holder-count rules depend on the bucket's role in the declared
    distribution profile and are checked by the writer and PostgreSQL.
    """

    bucket_code: str
    holder_count: int | None
    shares: Decimal
    ownership_percent: Decimal

    def __post_init__(self) -> None:
        if not self.bucket_code or self.bucket_code != self.bucket_code.strip():
            raise ValueError(
                "bucket_code must be non-empty without surrounding whitespace"
            )
        if self.holder_count is not None and self.holder_count < 0:
            raise ValueError("holder_count must not be negative")
        shares = Decimal(self.shares)
        if shares != shares.to_integral_value():
            raise ValueError("shares must be a whole number")
        object.__setattr__(self, "shares", shares.quantize(Decimal(1)))
        percent = Decimal(self.ownership_percent)
        # No upper bound: TDCC publishes 合計 above 100 — 158 rows over 74 data
        # dates, up to 135.00 (audit §4.9) — and refusing them would drop
        # published security-weeks whose counts are unremarkable. The role
        # bounds that do hold are checked where the role is known: the writer's
        # profile validation and the row trigger cap a holding level at 100.
        if percent < Decimal(-100):
            raise ValueError("ownership_percent must not be below -100")
        if percent != percent.quantize(Decimal(1).scaleb(-_PERCENT_SCALE)):
            raise ValueError(
                f"ownership_percent must have at most {_PERCENT_SCALE} decimal places"
            )
        object.__setattr__(self, "ownership_percent", percent)

@dataclass(frozen=True, slots=True)
class TDCCSnapshotObservation:
    """A complete distribution for one security and snapshot (data) date."""

    snapshot_date: date
    distribution_schema: str
    distribution: tuple[TDCCBucketObservation, ...]

    def __post_init__(self) -> None:
        distribution = tuple(self.distribution)
        if not distribution:
            raise ValueError("a TDCC snapshot requires at least one distribution row")
        codes = [item.bucket_code for item in distribution]
        if len(set(codes)) != len(codes):
            raise ValueError("a TDCC snapshot cannot contain duplicate bucket codes")
        object.__setattr__(self, "distribution", distribution)

def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


class SummaryPeriodBasis(str, Enum):
    QUARTER = "quarter"
    YTD = "ytd"
    ANNUAL = "annual"
    INSTANT = "instant"

@dataclass(frozen=True, slots=True)
class XBRLContext:
    entity_identifier: str
    period_type: Literal["instant", "duration", "forever"]
    instant_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    explicit_dimensions: Mapping[str, Any] = MappingProxyType({})
    typed_dimensions: Mapping[str, Any] = MappingProxyType({})
    scenario: Mapping[str, Any] = MappingProxyType({})
    segment: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.entity_identifier:
            raise ValueError("entity_identifier must not be empty")
        valid_shape = (
            self.period_type == "instant"
            and self.instant_date is not None
            and self.period_start is None
            and self.period_end is None
        ) or (
            self.period_type == "duration"
            and self.instant_date is None
            and self.period_start is not None
            and self.period_end is not None
            and self.period_end >= self.period_start
        ) or (
            self.period_type == "forever"
            and self.instant_date is None
            and self.period_start is None
            and self.period_end is None
        )
        if not valid_shape:
            raise ValueError("XBRL context has an invalid period shape")
        for name in (
            "explicit_dimensions",
            "typed_dimensions",
            "scenario",
            "segment",
        ):
            object.__setattr__(self, name, _frozen_mapping(getattr(self, name)))

class SourcePeriodRole(str, Enum):
    """Meaning established by a source-specific parser rule, not date math."""

    CURRENT_SINGLE_QUARTER = "current_single_quarter"
    CURRENT_YEAR_TO_DATE = "current_year_to_date"
    CURRENT_FULL_YEAR = "current_full_year"
    OTHER = "other"

@dataclass(frozen=True, slots=True)
class SourceContextClassification:
    """Auditable result produced by a source adapter's context classifier."""

    source_context_ref: str
    classifier_rule: str
    period_role: SourcePeriodRole
    expected_start: date
    expected_end: date

    def __post_init__(self) -> None:
        if not self.source_context_ref:
            raise ValueError("source_context_ref must not be empty")
        if not self.classifier_rule:
            raise ValueError("classifier_rule must not be empty")
        if self.expected_end < self.expected_start:
            raise ValueError("classified context end must not precede start")

@dataclass(frozen=True, slots=True)
class FinancialFactObservation:
    concept_qname: str
    context: XBRLContext
    unit_identity: str
    numeric_value: Decimal | None = None
    text_value: str | None = None
    is_nil: bool = False
    decimals: str | None = None
    #: The statement whose table printed the row, and the 會計科目代碼 in its
    #: first cell. The statement is part of fact identity, because one number
    #: can be a row of two statements; the code is business content beside it.
    statement: str | None = None
    account_code: str | None = None

    def __post_init__(self) -> None:
        if self.statement not in (
            None, "balance_sheet", "income_statement", "cash_flow"
        ):
            raise ValueError("statement must be one of the three MOPS statements")
        qname = self.concept_qname
        if not qname.startswith("{") or "}" not in qname[1:]:
            raise ValueError("concept_qname must use canonical {namespace}local form")
        namespace, local = qname[1:].split("}", 1)
        if not namespace or not local or "{" in local or "}" in local:
            raise ValueError("concept_qname must use canonical {namespace}local form")
        value_count = int(self.numeric_value is not None) + int(
            self.text_value is not None
        )
        if self.is_nil and value_count:
            raise ValueError("nil facts cannot contain numeric or text values")
        if not self.is_nil and value_count != 1:
            raise ValueError(
                "non-nil facts require exactly one numeric or text value"
            )
