"""Value types for institutional-flow and securities-financing source data."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from decimal import Decimal
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


def _require_value(instance: object, names: tuple[str, ...]) -> None:
    if all(getattr(instance, name) is None for name in names):
        raise ValueError("at least one source value is required")


def _validate_quantities(
    instance: object,
    *,
    signed: frozenset[str] = frozenset(),
    ratios: frozenset[str] = frozenset(),
    exclude: frozenset[str] = frozenset({"trade_date"}),
) -> None:
    names = tuple(field.name for field in fields(instance) if field.name not in exclude)
    _require_value(instance, names)
    for name in names:
        value = getattr(instance, name)
        if name in ratios:
            _validate_decimal(
                name,
                value,
                scale=8,
                nonnegative=True,
                maximum=Decimal("100"),
            )
        else:
            _validate_decimal(
                name,
                value,
                scale=0,
                nonnegative=name not in signed,
            )


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
    foreign_buy: Decimal | None = None
    foreign_sell: Decimal | None = None
    foreign_net: Decimal | None = None
    foreign_dealer_buy: Decimal | None = None
    foreign_dealer_sell: Decimal | None = None
    foreign_dealer_net: Decimal | None = None
    trust_buy: Decimal | None = None
    trust_sell: Decimal | None = None
    trust_net: Decimal | None = None
    dealer_self_buy: Decimal | None = None
    dealer_self_sell: Decimal | None = None
    dealer_self_net: Decimal | None = None
    dealer_hedge_buy: Decimal | None = None
    dealer_hedge_sell: Decimal | None = None
    dealer_hedge_net: Decimal | None = None
    dealer_net: Decimal | None = None
    total_net: Decimal | None = None

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
    issued_shares: Decimal | None = None
    investable_shares: Decimal | None = None
    held_shares: Decimal | None = None
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
            _validate_decimal(name, getattr(self, name), scale=0, nonnegative=True)
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
        _validate_quantities(
            self,
            signed=frozenset({"net"}),
            exclude=frozenset({"trade_date", "market", "institution"}),
        )


@dataclass(frozen=True, slots=True)
class MarginTradingObservation:
    trade_date: date
    margin_buy: Decimal | None = None
    margin_sell: Decimal | None = None
    margin_cash_repayment: Decimal | None = None
    margin_previous_balance: Decimal | None = None
    margin_balance: Decimal | None = None
    margin_next_limit: Decimal | None = None
    margin_utilization_ratio: Decimal | None = None
    short_buy: Decimal | None = None
    short_sell: Decimal | None = None
    short_stock_repayment: Decimal | None = None
    short_previous_balance: Decimal | None = None
    short_balance: Decimal | None = None
    short_next_limit: Decimal | None = None
    short_utilization_ratio: Decimal | None = None
    offset_balance: Decimal | None = None

    def __post_init__(self) -> None:
        _validate_quantities(
            self,
            ratios=frozenset(
                {"margin_utilization_ratio", "short_utilization_ratio"}
            ),
        )


@dataclass(frozen=True, slots=True)
class SecuritiesLendingObservation:
    trade_date: date
    previous_balance: Decimal | None = None
    borrowed: Decimal | None = None
    returned: Decimal | None = None
    balance: Decimal | None = None
    next_limit: Decimal | None = None
    next_available_limit: Decimal | None = None
    adjustment: Decimal | None = None
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
            _validate_decimal(
                name,
                getattr(self, name),
                scale=0,
                nonnegative=name != "adjustment",
            )
        if self.note == "":
            raise ValueError("note must be nonempty when supplied")


@dataclass(frozen=True, slots=True)
class WrittenSourceVersion:
    dataset_code: str
    version_id: int
    business_content_hash: str
    ingested_at: datetime
    created: bool
