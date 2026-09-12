"""Financial filing and XBRL domain value types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, Mapping
from uuid import UUID

from stock_data_center.pit import ResolvedRecord


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class FilingPeriod:
    report_year: int
    report_quarter: int

    def __post_init__(self) -> None:
        if not 1900 <= self.report_year <= 9999:
            raise ValueError("report_year must be between 1900 and 9999")
        if not 1 <= self.report_quarter <= 4:
            raise ValueError("report_quarter must be between 1 and 4")


class EPSPeriodBasis(str, Enum):
    QUARTER = "quarter"
    YTD = "ytd"
    ANNUAL = "annual"


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


@dataclass(frozen=True, slots=True)
class FinancialFact:
    fact_id: int
    concept_qname: str
    context_hash: str
    context: XBRLContext
    unit_identity: str
    numeric_value: Decimal | None
    text_value: str | None
    is_nil: bool
    decimals: str | None


@dataclass(frozen=True, slots=True)
class QuarterlyMetric:
    metric_code: str
    period_basis: SummaryPeriodBasis
    value: Decimal
    unit_identity: str
    source_fact: FinancialFact


@dataclass(frozen=True, slots=True)
class ResolvedFinancialFiling:
    filing: ResolvedRecord
    facts: tuple[FinancialFact, ...]
    summary: tuple[QuarterlyMetric, ...]


@dataclass(frozen=True, slots=True)
class ActualEPS:
    value: Decimal
    unit_identity: str
    period_basis: EPSPeriodBasis
    source_fact: FinancialFact
    filing: ResolvedRecord


@dataclass(frozen=True, slots=True)
class FilingLineageObservation:
    raw_artifact_id: UUID
    raw_artifact_hash: str
    raw_artifact_uri: str
    ingest_run_id: UUID
    ingest_run_status: str
    source_uri: str
    fetched_at: datetime
