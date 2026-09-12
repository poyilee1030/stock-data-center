"""Value types for institutional-flow and securities-financing source data."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID


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
class SourceLineageRef:
    raw_artifact_id: UUID
    ingest_run_id: UUID


@dataclass(frozen=True, slots=True)
class SourceLineageObservation:
    raw_artifact_id: UUID
    raw_artifact_hash: str
    raw_artifact_uri: str
    ingest_run_id: UUID
    ingest_run_status: str
    source_uri: str
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class SourcePublication:
    evidence_kind: Literal["assertion", "correction", "retraction", "unknown"]
    published_at: datetime | None
    evidence_source: str
    evidence_type: str
    quality_rank: int
    supersedes_evidence_id: int | None = None

    def __post_init__(self) -> None:
        if self.published_at is not None:
            if (
                self.published_at.tzinfo is None
                or self.published_at.utcoffset() is None
            ):
                raise ValueError("published_at must be timezone-aware")
            object.__setattr__(
                self, "published_at", self.published_at.astimezone(UTC)
            )


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
        for name in ("margin_utilization_ratio", "short_utilization_ratio"):
            _validate_decimal(
                name,
                getattr(self, name),
                scale=8,
                nonnegative=True,
                maximum=Decimal("100"),
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


@dataclass(frozen=True, slots=True)
class WrittenSourceVersion:
    dataset_code: str
    version_id: int
    business_content_hash: str
    ingested_at: datetime
    created: bool
