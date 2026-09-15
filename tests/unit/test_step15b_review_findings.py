"""Step 15-b — regressions for the code-review findings.

Each failed before its fix.
"""

from datetime import UTC, datetime

import pytest

from stock_data_center.evidence import evidence_plan
from stock_data_center.provenance import IngestPurpose


FIRST_SEEN = datetime(2024, 2, 5, 14, 0, tzinfo=UTC)
MUCH_LATER = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
RULE = datetime(2024, 2, 12, 15, 59, 59, tzinfo=UTC)


def test_a_rerun_over_an_existing_version_claims_no_second_capture() -> None:
    """Finding 1. A looser bound recorded later used to win and hide data.

    Two `capture_bound` rows for one version share rank 80, and the resolver
    breaks ties by `recorded_at`, so the rerun's 2026 instant would supersede
    the real 2024 one — turning a row that was visible at an early
    `information_as_of` into one that is not.

    A genuine first sighting creates the version. If the version already
    existed, someone captured it earlier and our instant is only a looser
    upper bound, so it proves nothing worth recording.
    """
    items = evidence_plan(
        purpose=IngestPurpose.FIRST_CAPTURE,
        version_created=False,
        captured_at=MUCH_LATER,
        rule_instant=RULE,
        rule_source="monthly_revenue_statutory@1",
    )

    assert tuple(item.evidence_type for item in items) == ("release_rule",)
    assert items[0].published_at == RULE


def test_a_first_capture_that_creates_the_version_still_claims_it() -> None:
    items = evidence_plan(
        purpose=IngestPurpose.FIRST_CAPTURE,
        version_created=True,
        captured_at=FIRST_SEEN,
        rule_instant=RULE,
        rule_source="monthly_revenue_statutory@1",
    )

    assert tuple(item.evidence_type for item in items) == (
        "capture_bound",
        "release_rule",
    )
    assert items[0].published_at == FIRST_SEEN


def test_a_rerun_cannot_falsify_the_rule_either() -> None:
    """It is not a first sighting, so it proves nothing about lateness."""
    items = evidence_plan(
        purpose=IngestPurpose.FIRST_CAPTURE,
        version_created=False,
        captured_at=MUCH_LATER,
        rule_instant=datetime(2024, 1, 20, 15, 59, 59, tzinfo=UTC),
        rule_source="monthly_revenue_statutory@1",
    )

    assert tuple(item.evidence_type for item in items) == ("release_rule",)


def test_rule_evidence_must_name_the_rule_and_version() -> None:
    """Finding 2. Append-only storage can never correct unattributed evidence."""
    with pytest.raises(TypeError):
        evidence_plan(
            purpose=IngestPurpose.GAP_FILL,
            version_created=False,
            captured_at=FIRST_SEEN,
            rule_instant=RULE,
        )

    items = evidence_plan(
        purpose=IngestPurpose.GAP_FILL,
        version_created=False,
        captured_at=FIRST_SEEN,
        rule_instant=RULE,
        rule_source="monthly_revenue_statutory@1",
    )
    assert items[0].evidence_source == "monthly_revenue_statutory@1"


def test_an_unattributed_rule_source_is_rejected() -> None:
    for bad in ("", "   ", "release_rule", "monthly_revenue_statutory"):
        with pytest.raises(ValueError):
            evidence_plan(
                purpose=IngestPurpose.GAP_FILL,
                version_created=False,
                captured_at=FIRST_SEEN,
                rule_instant=RULE,
                rule_source=bad,
            )


def test_no_rule_needs_no_attribution() -> None:
    items = evidence_plan(
        purpose=IngestPurpose.FIRST_CAPTURE,
        version_created=True,
        captured_at=FIRST_SEEN,
        rule_instant=None,
    )

    assert tuple(item.evidence_type for item in items) == ("capture_bound",)
