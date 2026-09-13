"""Value types for Phase 8 observed market-reference data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID


def _decimal(name: str, value: Decimal | None, scale: int, *, positive: bool = False) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal or None")
    if value.as_tuple().exponent < -scale:
        raise ValueError(f"{name} has more than {scale} fractional places")
    if positive and value < 0:
        raise ValueError(f"{name} must not be negative")


class AmountScale(str, Enum):
    MAJOR = "major"
    THOUSAND = "thousand"
    MILLION = "million"

    @property
    def multiplier(self) -> Decimal:
        return {
            AmountScale.MAJOR: Decimal(1),
            AmountScale.THOUSAND: Decimal(1000),
            AmountScale.MILLION: Decimal(1000000),
        }[self]


@dataclass(frozen=True, slots=True)
class TwdAmount:
    """Canonical TWD amount in major units (dollars)."""
    value: Decimal

    def __post_init__(self) -> None:
        _decimal("TWD amount", self.value, 4, positive=True)


@dataclass(frozen=True, slots=True)
class SourceTwdAmount:
    value: Decimal
    scale: AmountScale

    def __post_init__(self) -> None:
        _decimal("source TWD amount", self.value, 4, positive=True)
        if not isinstance(self.scale, AmountScale):
            raise ValueError("scale must be an AmountScale")

    def to_canonical(self) -> TwdAmount:
        return TwdAmount(self.value * self.scale.multiplier)


@dataclass(frozen=True, slots=True)
class Phase8LineageRef:
    raw_artifact_id: UUID
    ingest_run_id: UUID


@dataclass(frozen=True, slots=True)
class Phase8Publication:
    evidence_kind: Literal["assertion", "correction", "retraction", "unknown"]
    published_at: datetime | None
    evidence_source: str
    evidence_type: str
    quality_rank: int
    supersedes_evidence_id: int | None = None

    def __post_init__(self) -> None:
        if self.published_at is not None:
            if self.published_at.tzinfo is None or self.published_at.utcoffset() is None:
                raise ValueError("published_at must be timezone-aware")
            object.__setattr__(self, "published_at", self.published_at.astimezone(UTC))


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
        if self.trade_value is not None and not isinstance(self.trade_value, TwdAmount):
            raise ValueError("trade_value must be a canonical TwdAmount")
        if self.high_value is not None and self.high_value < max(
            value for value in (self.open_value, self.close_value, self.low_value) if value is not None
        ):
            raise ValueError("high_value is inconsistent with OHLC")
        if self.low_value is not None and self.low_value > min(
            value for value in (self.open_value, self.close_value) if value is not None
        ):
            raise ValueError("low_value is inconsistent with OHLC")


@dataclass(frozen=True, slots=True)
class MarketIndexMetadataObservation:
    effective_from: date
    market: str
    name: str
    effective_to: date | None = None

    def __post_init__(self) -> None:
        if not self.market or not self.name:
            raise ValueError("market and name must be nonempty")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")


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
    official_rights_dividend_value: TwdAmount | None = None
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
        money = (
            "cash_dividend_per_share", "capital_reduction_cash_return_per_share",
            "subscription_price", "close_before",
            "official_reference_price", "official_rights_dividend_value",
        )
        for name in money:
            value = getattr(self, name)
            if value is not None and not isinstance(value, TwdAmount):
                raise ValueError(f"{name} must be a canonical TwdAmount")
        ratios = (
            "earnings_stock_ratio", "capital_surplus_stock_ratio",
            "free_share_ratio", "rights_ratio", "old_shares", "new_shares",
        )
        for name in ratios:
            _decimal(name, getattr(self, name), 8, positive=True)
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


@dataclass(frozen=True, slots=True)
class WrittenPhase8Version:
    dataset_code: str
    version_id: int
    business_content_hash: str
    ingested_at: datetime
    created: bool


@dataclass(frozen=True, slots=True)
class Phase8LineageObservation:
    raw_artifact_id: UUID
    raw_artifact_hash: str
    raw_artifact_uri: str
    ingest_run_id: UUID
    ingest_run_status: str
    source_uri: str
    fetched_at: datetime
