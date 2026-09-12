"""Stable contracts shared by real-source ingestion components."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from types import MappingProxyType

from stock_data_center.market_data import DailyPriceObservation


class SourceQuantityUnit(str, Enum):
    SHARE = "share"
    LOT_1000_SHARES = "lot_1000_shares"


class SourceMoneyUnit(str, Enum):
    TWD = "twd"
    THOUSAND_TWD = "thousand_twd"


@dataclass(frozen=True, slots=True)
class DailyMarketSourceSemantics:
    traded_quantity_unit: SourceQuantityUnit
    trade_value_unit: SourceMoneyUnit


@dataclass(frozen=True, slots=True)
class DailyMarketRequest:
    security_code: str
    month: date

    def __post_init__(self) -> None:
        if not self.security_code or self.security_code.strip() != self.security_code:
            raise ValueError("security_code must be nonempty and already trimmed")
        if self.month.day != 1:
            raise ValueError("month must be the first day of the requested month")


@dataclass(frozen=True, slots=True)
class SourceResource:
    resource_key: str
    source_uri: str


@dataclass(frozen=True, slots=True)
class FetchedArtifact:
    content: bytes
    source_uri: str
    fetched_at: datetime
    media_type: str

    def __post_init__(self) -> None:
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        object.__setattr__(self, "fetched_at", self.fetched_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class ParsedDailyMarket:
    security_code: str
    security_name: str
    requested_month: date
    rows: tuple[DailyPriceObservation, ...]
    source_fields: tuple[str, ...]

    @property
    def coverage_start(self) -> date | None:
        return self.rows[0].trade_date if self.rows else None

    @property
    def coverage_end(self) -> date | None:
        return self.rows[-1].trade_date if self.rows else None


@dataclass(frozen=True, slots=True)
class ResourceImportResult:
    resource_key: str
    source: str
    raw_artifact_hash: str | None
    raw_artifact_created: bool
    business_versions_created: int
    business_versions_deduplicated: int
    publication_evidence_created: int
    publication_evidence_deduplicated: int
    evidence_observations: int
    unknown_publication_observations: int
    normalized_rows: int
    coverage_start: date | None
    coverage_end: date | None
    resumed_from_checkpoint: bool = False


@dataclass(frozen=True, slots=True)
class ImportManifestResult:
    import_id: str
    status: str
    result_counts: Mapping[str, int]
    reconciliation: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "result_counts", MappingProxyType(dict(self.result_counts))
        )
        object.__setattr__(
            self, "reconciliation", MappingProxyType(dict(self.reconciliation))
        )


class SourceDataError(ValueError):
    """A source artifact cannot be mapped unambiguously to the contract."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code


class ResourceQuarantinedError(RuntimeError):
    """The raw artifact was retained but its normalized writes were rejected."""
