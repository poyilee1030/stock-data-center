"""Decide what evidence an import may claim (ADR-0020 §2, §5).

Kept as one pure function because the decision is the policy: a run may claim
only what its declared purpose lets it prove.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from stock_data_center.provenance import IngestPurpose

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
    rule_source: str | None = None,
    proven_capture_at: datetime | None = None,
) -> tuple[PlannedEvidence, ...]:
    """Every evidence row this import is entitled to write, in rank order.

    A capture bound is a proven upper bound, so only a run that actually *saw*
    the row first may claim one, and seeing it first means creating its version.
    A run that fetched again and found the version already there was not first:
    someone captured it earlier, and our later instant is a looser bound that
    would supersede the real one, because two capture bounds share a rank and
    the resolver breaks ties by `recorded_at`. A `gap_fill` noticed the row was
    missing long after it was published and proves nothing about when.

    The write-side rule from §2: if a first sighting happened *after* the rule
    instant, the row is a late filer and the rule is falsified for it, so the
    rule is not recorded at all. A backfill capture cannot falsify anything,
    because it is not a first sighting.
    """
    if captured_at.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware (CLAUDE.md §34)")
    if rule_instant is not None:
        _require_rule_attribution(rule_source)

    proves_first_sighting = version_created and purpose in {
        IngestPurpose.FIRST_CAPTURE,
        IngestPurpose.CORRECTION_CHECK,
    }

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
        # Falsification follows from any proven first sighting, whether this run
        # made it or an earlier one already recorded it. Otherwise a re-import
        # would append the rule an earlier run deliberately withheld.
        proven_at = proven_capture_at
        if proves_first_sighting and (proven_at is None or captured_at < proven_at):
            proven_at = captured_at
        falsified = proven_at is not None and proven_at > rule_instant
        if not falsified:
            items.append(
                PlannedEvidence(
                    evidence_type="release_rule",
                    evidence_kind="assertion",
                    published_at=rule_instant,
                    quality_rank=EVIDENCE_RANKS["release_rule"],
                    evidence_source=rule_source,  # type: ignore[arg-type]
                )
            )

    if not items and proven_capture_at is None:
        # Nothing is provable and nothing was ever recorded, so nothing is
        # claimed. This is the pre-ADR-0020 behaviour, kept exactly:
        # market-invisible rather than invented. A version that already carries
        # a proven capture needs no such row — it would only add a rank-0 head
        # asserting ignorance next to evidence.
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


def archive_evidence_plan(
    *,
    bound_at: datetime | None,
    evidence_source: str,
    proves_first_capture: bool,
    rule_instant: datetime | None = None,
    rule_source: str | None = None,
    bound_is_the_rule_day: bool = False,
    proven_capture_at: datetime | None = None,
) -> tuple[PlannedEvidence, ...]:
    """What one legacy-archive row proves about when its month was public.

    The archive holds two different things, and the window decides which
    (audit §7.1, §7.4):

    * **2026M02 onward** the date is the legacy 22:45 job's own first sighting
      and the value beside it is what it saw, so the row proves a
      `legacy_capture_bound` — the same shape as a capture bound, one rank
      lower because the sighting is someone else's and dated to the day.
    * **before that** the date is an announcement date recovered from a news
      article, which proves the filing was public that day: a
      `press_report_bound`. A row still sitting on the statutory day of the
      month cannot be told from the fallback by value alone, so it claims the
      rule instead, which is never earlier than the rule and moves with it
      when the deadline falls on a closed day.

    `bound_at` is what the archive file itself proves: for monthly revenue the
    end of the archive's day in the market timezone, because the archive dates
    rows to the day and the end of it is the earliest instant certainly not
    before the sighting or the article; for an archived iXBRL document (Step
    23-c) the file's own mtime, which is dated to the second. It is `None` only
    when the row claims the rule, which needs no bound of its own.
    """
    if bound_at is not None and bound_at.tzinfo is None:
        raise ValueError("bound_at must be timezone-aware (CLAUDE.md §34)")
    if bound_at is None and not bound_is_the_rule_day:
        raise ValueError(
            "a row with no bound and no rule claims nothing; pass the instant "
            "the archive proves"
        )
    if proves_first_capture:
        return (
            PlannedEvidence(
                evidence_type="legacy_capture_bound",
                evidence_kind="assertion",
                published_at=bound_at,
                quality_rank=EVIDENCE_RANKS["legacy_capture_bound"],
                evidence_source=evidence_source,
            ),
        )
    if bound_is_the_rule_day:
        if rule_instant is None:
            raise ValueError(
                "a row left on the statutory day needs the rule instant it claims"
            )
        _require_rule_attribution(rule_source)
        if proven_capture_at is not None and proven_capture_at > rule_instant:
            # A first sighting later than the deadline falsifies the rule for
            # this row, whoever proved it and whenever (§2, CLAUDE.md §32). The
            # row keeps the capture it already carries and claims nothing here;
            # appending the rule would leave a falsified instant in
            # append-only storage, ready to answer the day the capture above it
            # is superseded.
            return ()
        return (
            PlannedEvidence(
                evidence_type="release_rule",
                evidence_kind="assertion",
                published_at=rule_instant,
                quality_rank=EVIDENCE_RANKS["release_rule"],
                evidence_source=rule_source,  # type: ignore[arg-type]
            ),
        )
    return (
        PlannedEvidence(
            evidence_type="press_report_bound",
            evidence_kind="assertion",
            published_at=bound_at,
            quality_rank=EVIDENCE_RANKS["press_report_bound"],
            evidence_source=evidence_source,
        ),
    )


def _require_rule_attribution(rule_source: str | None) -> None:
    """Rule evidence names `rule_id@version` or it is not written.

    Storage is append-only, so unattributable evidence can never be corrected —
    only superseded by something that does say which rule produced it.
    """
    if rule_source is None:
        raise TypeError(
            "rule_instant requires rule_source; ADR-0020 §3 records the rule as "
            "rule_id@version"
        )
    if "@" not in rule_source or not rule_source.split("@")[0].strip():
        raise ValueError(
            f"rule_source must be rule_id@version, got {rule_source!r}"
        )
