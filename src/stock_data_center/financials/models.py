"""Financial filing and XBRL domain value types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from stock_data_center.ingestion.observations import (  # noqa: F401 - moved, Step 35-d-1
    EPSPeriodBasis,
    SummaryPeriodBasis,
    XBRLContext,
    _frozen_mapping,
)
from stock_data_center.pit import ResolvedRecord


@dataclass(frozen=True, slots=True)
class FilingPeriod:
    report_year: int
    report_quarter: int

    def __post_init__(self) -> None:
        if not 1900 <= self.report_year <= 9999:
            raise ValueError("report_year must be between 1900 and 9999")
        if not 1 <= self.report_quarter <= 4:
            raise ValueError("report_quarter must be between 1 and 4")


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
    #: The statement that printed the row, and its 會計科目代碼.
    statement: str | None = None
    account_code: str | None = None


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
