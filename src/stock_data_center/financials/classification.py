"""Source-aware classification of XBRL duration contexts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from stock_data_center.financials.models import EPSPeriodBasis, XBRLContext


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


def classify_eps_period_basis(
    *,
    context: XBRLContext,
    filing_period_start: date,
    filing_period_end: date,
    report_quarter: int,
    classification: SourceContextClassification,
) -> EPSPeriodBasis:
    """Validate source semantics against the normalized context and filing."""

    if context.period_type != "duration":
        raise ValueError("EPS period classification requires a duration context")
    if (
        context.period_start != classification.expected_start
        or context.period_end != classification.expected_end
    ):
        raise ValueError("source classification does not match the XBRL context")
    if context.period_end != filing_period_end:
        raise ValueError("current EPS context must end at the filing period end")

    assert context.period_start is not None
    assert context.period_end is not None
    duration_days = (context.period_end - context.period_start).days
    role = classification.period_role
    if role is SourcePeriodRole.CURRENT_SINGLE_QUARTER:
        if not 70 <= duration_days <= 100:
            raise ValueError("single-quarter context has an implausible duration")
        if report_quarter > 1 and context.period_start <= filing_period_start:
            raise ValueError("single-quarter context cannot start at fiscal-year start")
        return EPSPeriodBasis.QUARTER
    if role is SourcePeriodRole.CURRENT_YEAR_TO_DATE:
        if context.period_start != filing_period_start:
            raise ValueError("YTD context must start at the filing period start")
        if report_quarter > 1 and duration_days <= 100:
            raise ValueError("YTD context is not longer than one quarter")
        if duration_days >= 300:
            raise ValueError("full-year context must be classified as annual")
        return EPSPeriodBasis.YTD
    if role is SourcePeriodRole.CURRENT_FULL_YEAR:
        if (
            report_quarter != 4
            or context.period_start != filing_period_start
            or duration_days < 300
        ):
            raise ValueError("annual context requires a full-year Q4 duration")
        return EPSPeriodBasis.ANNUAL
    raise ValueError("source context role is not a current EPS period")
