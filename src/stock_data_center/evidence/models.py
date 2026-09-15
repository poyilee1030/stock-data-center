"""Contracts for release-rule evaluation (ADR-0020 §3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


class UnknownReleaseRuleError(LookupError):
    """No such rule at that version.

    Rules are versioned and never edited, so an unknown version is a caller
    error rather than something to approximate with the nearest one.
    """


@dataclass(frozen=True, slots=True)
class ReleaseRule:
    rule_id: str
    version: int
    rule_kind: str
    parameters: dict
    timezone: str
    business_day_shift: bool
    authority: str

    @property
    def evidence_source(self) -> str:
        return f"{self.rule_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class ResolvedReleaseInstant:
    """What a rule says about one logical period, ready to become evidence."""

    rule: ReleaseRule
    period: date
    published_at: datetime

    @property
    def evidence_source(self) -> str:
        return self.rule.evidence_source
