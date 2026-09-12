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
    "cash_dividend", "stock_dividend", "rights", "ex_dividend", "ex_right",
    "capital_reduction", "other",
]


@dataclass(frozen=True, slots=True)
class CorporateActionObservation:
    action_type: ActionType
    ex_date: date
    announcement_date: date | None = None
    record_date: date | None = None
    payment_date: date | None = None
    cash_dividend_per_share: TwdAmount | None = None
    stock_dividend_ratio: Decimal | None = None
    rights_ratio: Decimal | None = None
    subscription_price: TwdAmount | None = None
    close_before: TwdAmount | None = None
    reference_price: TwdAmount | None = None
    rights_dividend_value: TwdAmount | None = None
    terms: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.announcement_date is not None and self.announcement_date > self.ex_date:
            raise ValueError("announcement_date must not follow ex_date")
        money = (
            "cash_dividend_per_share", "subscription_price", "close_before",
            "reference_price", "rights_dividend_value",
        )
        for name in money:
            value = getattr(self, name)
            if value is not None and not isinstance(value, TwdAmount):
                raise ValueError(f"{name} must be a canonical TwdAmount")
        for name in ("stock_dividend_ratio", "rights_ratio"):
            _decimal(name, getattr(self, name), 8, positive=True)
        if not any(
            getattr(self, name) is not None
            for name in ("announcement_date", "record_date", "payment_date", *money,
                         "stock_dividend_ratio", "rights_ratio")
        ) and not self.terms:
            raise ValueError("at least one corporate-action term is required")
        object.__setattr__(self, "terms", self.terms or {})


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
