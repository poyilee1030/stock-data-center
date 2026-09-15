"""Step 15-c — applying the evidence policy to a dataset.

Which rule a dataset follows is a declaration per `(dataset_code, source)`,
like its accepted evidence types, so opting in is configuration rather than
code.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from itertools import count

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.evidence import (
    EvidencePolicyService,
    UnacceptedEvidenceTypeError,
)
from stock_data_center.provenance import IngestPurpose


pytestmark = pytest.mark.integration

_DIGESTS = count(0x76000)
POLICY = EvidencePolicyService()

# 09:00 Taipei on 07-24, i.e. after the exchange rule's 03:00 instant. Under
# ADR-0020 §10 v1 never captures the unsettled same-day row, so a real forward
# capture is always after the rule — which is exactly why the rule matters only
# for history nobody captured.
CAPTURED_AT = datetime(2024, 7, 24, 1, 0, tzinfo=UTC)
BEFORE_THE_RULE = datetime(2024, 7, 23, 14, 0, tzinfo=UTC)  # 22:00 Taipei on 07-23


def configure(
    db: Connection,
    *,
    dataset: str = "daily_price",
    source: str = "twse",
    accepted: tuple[str, ...] = ("official", "capture_bound", "release_rule"),
) -> None:
    db.execute(
        sa.text(
            "INSERT INTO dataset_catalog (dataset_code, description, schema_version) "
            "VALUES (:dataset, :description, 'v1') "
            "ON CONFLICT (dataset_code) DO NOTHING"
        ),
        {"dataset": dataset, "description": dataset},
    )
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_sources
                (dataset_code, source, supports_market_pit, supports_system_pit,
                 publication_time_quality, evidence_status, accepted_evidence_types,
                 is_canonical)
            VALUES (:dataset, :source, true, true, 0, 'verified', :accepted, false)
            ON CONFLICT (dataset_code, source) DO UPDATE
               SET accepted_evidence_types = EXCLUDED.accepted_evidence_types
            """
        ),
        {"dataset": dataset, "source": source, "accepted": list(accepted)},
    )


def follow(
    db: Connection,
    *,
    dataset: str = "daily_price",
    source: str = "twse",
    rule_id: str = "exchange_daily_settled",
    version: int = 1,
) -> None:
    db.execute(
        sa.text(
            """
            INSERT INTO dataset_release_rules
                (dataset_code, source, rule_id, version, note)
            VALUES (:dataset, :source, :rule_id, :version, 'test')
            ON CONFLICT (dataset_code, source) DO NOTHING
            """
        ),
        {"dataset": dataset, "source": source, "rule_id": rule_id, "version": version},
    )


def plan(db: Connection, **changes):
    arguments = {
        "dataset_code": "daily_price",
        "source": "twse",
        "period": date(2024, 7, 23),
        "purpose": IngestPurpose.FIRST_CAPTURE,
        "version_created": True,
        "captured_at": CAPTURED_AT,
    }
    arguments.update(changes)
    return POLICY.plan(db, **arguments)


def types(items) -> tuple[str, ...]:
    return tuple(item.evidence_type for item in items)


def test_history_nobody_captured_resolves_by_its_rule(db: Connection) -> None:
    """The case the whole policy exists for: imported history, no capture."""
    configure(db)
    follow(db)

    items = plan(db, purpose=IngestPurpose.GAP_FILL, version_created=True)

    assert types(items) == ("release_rule",)
    assert items[0].published_at == datetime(
        2024, 7, 23, 19, 0, tzinfo=UTC
    )  # 07-24 03:00 Taipei
    assert items[0].evidence_source == "exchange_daily_settled@1"


def test_a_forward_capture_supersedes_the_rule_it_beats(db: Connection) -> None:
    """A real forward capture runs after 03:00, so it proves the tighter bound.

    The rule is then falsified for that row and not recorded at all: the capture
    is what actually happened, and the rule would only assert something weaker.
    """
    configure(db)
    follow(db)

    items = plan(db)

    assert types(items) == ("capture_bound",)
    assert items[0].published_at == CAPTURED_AT


def test_a_capture_before_the_rule_instant_records_both(db: Connection) -> None:
    configure(db)
    follow(db)

    items = plan(db, captured_at=BEFORE_THE_RULE)

    assert types(items) == ("capture_bound", "release_rule")
    assert items[0].published_at == BEFORE_THE_RULE
    assert items[1].evidence_source == "exchange_daily_settled@1"


def test_a_dataset_following_no_rule_still_records_what_it_captured(
    db: Connection,
) -> None:
    configure(db, dataset="security_metadata", accepted=("official", "capture_bound"))

    items = plan(db, dataset_code="security_metadata")

    assert types(items) == ("capture_bound",)


def test_a_dataset_with_neither_rule_nor_capture_stays_unknown(
    db: Connection,
) -> None:
    """Exactly the pre-ADR-0020 behaviour, kept for anything not opted in."""
    configure(db, dataset="security_metadata", accepted=("official",))

    items = plan(
        db,
        dataset_code="security_metadata",
        purpose=IngestPurpose.GAP_FILL,
        version_created=False,
    )

    assert types(items) == ("official",)
    assert items[0].published_at is None


def test_planning_a_type_the_source_does_not_accept_fails_loudly(
    db: Connection,
) -> None:
    """A half-configured source would silently produce invisible evidence."""
    configure(db, accepted=("official",))
    follow(db)

    with pytest.raises(UnacceptedEvidenceTypeError) as error:
        plan(db)

    assert "capture_bound" in str(error.value)


def test_a_rule_mapping_must_point_at_a_registered_rule(db: Connection) -> None:
    configure(db, dataset="tdcc_snapshot", source="tdcc")

    with pytest.raises(sa.exc.DBAPIError):
        with db.begin_nested():
            follow(
                db, dataset="tdcc_snapshot", source="tdcc",
                rule_id="invented_rule", version=1,
            )


def test_a_rule_mapping_must_point_at_a_configured_source(db: Connection) -> None:
    with pytest.raises(sa.exc.DBAPIError):
        with db.begin_nested():
            follow(db, dataset="never_configured")


def test_the_mapping_is_queryable_on_its_own(db: Connection) -> None:
    """Which rule a dataset follows is configuration, not buried in code."""
    configure(db)
    follow(db)

    rule = POLICY.rule_for(db, dataset_code="daily_price", source="twse")

    assert rule is not None
    assert rule.evidence_source == "exchange_daily_settled@1"
    assert rule.authority.strip()
    # Both daily-price sources are opted in by the migration; a dataset that
    # declared no rule has none, and is not given one by default.
    assert POLICY.rule_for(db, dataset_code="daily_price", source="tpex") is not None
    configure(db, dataset="security_metadata", source="twse")
    assert (
        POLICY.rule_for(db, dataset_code="security_metadata", source="twse") is None
    )
