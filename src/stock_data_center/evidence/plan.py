"""Decide what evidence an import may claim (ADR-0020 §2, §5).

Kept as one pure function because the decision is the policy: a run may claim
only what its declared purpose lets it prove.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from stock_data_center.ingestion.models import IngestPurpose

# ADR-0020 §1. Storage pins these for the four types, so a plan that disagrees
# is rejected rather than silently outranking a capture.
EVIDENCE_RANKS = {
    "capture_bound": 80,
    "legacy_capture_bound": 70,
    "press_report_bound": 60,
    "release_rule": 40,
}


@dataclass(frozen=True, slots=True)
class PlannedEvidence:
    evidence_type: str
    evidence_kind: str
    published_at: datetime | None
    quality_rank: int
    evidence_source: str


def evidence_plan(
    *,
    purpose: IngestPurpose,
    version_created: bool,
    captured_at: datetime,
    rule_instant: datetime | None,
    rule_source: str = "release_rule",
) -> tuple[PlannedEvidence, ...]:
    """Every evidence row this import is entitled to write, in rank order.

    A capture bound is a proven upper bound, so only a run that actually saw the
    row first may claim one: a `first_capture`, or a `correction_check` for the
    revision it newly found. A `gap_fill` noticed the row was missing long after
    it was published and proves nothing about when.

    The write-side rule from §2: if a first sighting happened *after* the rule
    instant, the row is a late filer and the rule is falsified for it, so the
    rule is not recorded at all. A backfill capture cannot falsify anything,
    because it is not a first sighting.
    """
    if captured_at.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware (CLAUDE.md §34)")

    proves_first_sighting = purpose is IngestPurpose.FIRST_CAPTURE or (
        purpose is IngestPurpose.CORRECTION_CHECK and version_created
    )

    items: list[PlannedEvidence] = []
    if proves_first_sighting:
        items.append(
            PlannedEvidence(
                evidence_type="capture_bound",
                evidence_kind="assertion",
                published_at=captured_at,
                quality_rank=EVIDENCE_RANKS["capture_bound"],
                evidence_source="data center first capture",
            )
        )

    if rule_instant is not None:
        falsified = proves_first_sighting and captured_at > rule_instant
        if not falsified:
            items.append(
                PlannedEvidence(
                    evidence_type="release_rule",
                    evidence_kind="assertion",
                    published_at=rule_instant,
                    quality_rank=EVIDENCE_RANKS["release_rule"],
                    evidence_source=rule_source,
                )
            )

    if not items:
        # Nothing is provable, so nothing is claimed. This is the pre-ADR-0020
        # behaviour, kept exactly: market-invisible rather than invented.
        items.append(
            PlannedEvidence(
                evidence_type="official",
                evidence_kind="unknown",
                published_at=None,
                quality_rank=0,
                evidence_source="no publication instant is available",
            )
        )
    return tuple(items)
