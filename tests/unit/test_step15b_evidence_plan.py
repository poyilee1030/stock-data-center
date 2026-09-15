"""Step 15-b — which evidence an import may write (ADR-0020 §2, §5).

A pure decision, kept out of the database so it can be read in one sitting:
what a run is allowed to claim follows from the purpose it declared, the instant
it fetched, and what the rule says.
"""

from datetime import UTC, datetime

import pytest

from stock_data_center.evidence import evidence_plan
from stock_data_center.ingestion.models import IngestPurpose


CAPTURED_AT = datetime(2024, 2, 5, 14, 30, tzinfo=UTC)
RULE_LATER = datetime(2024, 2, 12, 15, 59, 59, tzinfo=UTC)
RULE_EARLIER = datetime(2024, 1, 20, 15, 59, 59, tzinfo=UTC)


def plan(**changes):
    arguments = {
        "purpose": IngestPurpose.FIRST_CAPTURE,
        "version_created": True,
        "captured_at": CAPTURED_AT,
        "rule_instant": RULE_LATER,
        "rule_source": "monthly_revenue_statutory@1",
    }
    arguments.update(changes)
    return evidence_plan(**arguments)


def types(items) -> tuple[str, ...]:
    return tuple(item.evidence_type for item in items)


def test_a_first_capture_claims_a_capture_bound() -> None:
    items = plan()

    assert types(items) == ("capture_bound", "release_rule")
    assert items[0].published_at == CAPTURED_AT
    assert items[1].published_at == RULE_LATER


def test_a_capture_later_than_the_rule_falsifies_it() -> None:
    """ADR-0020 §2, the write-side rule: a late filer's rule is not recorded."""
    items = plan(rule_instant=RULE_EARLIER)

    assert types(items) == ("capture_bound",)
    assert items[0].published_at == CAPTURED_AT


def test_a_gap_fill_never_claims_a_capture() -> None:
    """The row was public long before we noticed it was missing."""
    items = plan(purpose=IngestPurpose.GAP_FILL)

    assert types(items) == ("release_rule",)
    assert items[0].published_at == RULE_LATER


def test_a_gap_fill_cannot_falsify_the_rule_either() -> None:
    """A backfill capture is not first-seen evidence, so it proves nothing."""
    items = plan(purpose=IngestPurpose.GAP_FILL, rule_instant=RULE_EARLIER)

    assert types(items) == ("release_rule",)
    assert items[0].published_at == RULE_EARLIER


def test_an_unspecified_run_claims_nothing_it_cannot_prove() -> None:
    items = plan(purpose=IngestPurpose.UNSPECIFIED)

    assert types(items) == ("release_rule",)


def test_a_correction_check_claims_a_capture_only_for_what_it_newly_found() -> None:
    """A correction is never visible before it was actually seen."""
    found = plan(purpose=IngestPurpose.CORRECTION_CHECK, version_created=True)
    unchanged = plan(purpose=IngestPurpose.CORRECTION_CHECK, version_created=False)

    assert types(found) == ("capture_bound", "release_rule")
    assert found[0].published_at == CAPTURED_AT
    assert types(unchanged) == ("release_rule",)


def test_a_correction_found_after_the_rule_keeps_only_its_capture() -> None:
    items = plan(
        purpose=IngestPurpose.CORRECTION_CHECK,
        version_created=True,
        rule_instant=RULE_EARLIER,
    )

    assert types(items) == ("capture_bound",)


def test_without_a_rule_a_first_capture_still_records_what_it_saw() -> None:
    items = plan(rule_instant=None)

    assert types(items) == ("capture_bound",)


def test_with_neither_a_rule_nor_a_capture_the_result_is_unknown() -> None:
    """Exactly today's behaviour: market-invisible rather than invented."""
    items = plan(purpose=IngestPurpose.GAP_FILL, rule_instant=None)

    assert types(items) == ("official",)
    assert items[0].published_at is None
    assert items[0].evidence_kind == "unknown"


def test_every_affirmative_item_carries_the_rank_adr_0020_fixed() -> None:
    items = plan()

    ranks = {item.evidence_type: item.quality_rank for item in items}
    assert ranks == {"capture_bound": 80, "release_rule": 40}
    assert all(item.evidence_kind == "assertion" for item in items)


def test_a_naive_capture_instant_is_refused() -> None:
    """Timezone-aware only (CLAUDE.md §34); a naive instant is ambiguous."""
    with pytest.raises(ValueError):
        plan(captured_at=datetime(2024, 2, 5, 14, 30))
