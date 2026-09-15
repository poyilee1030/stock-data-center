"""Apply the availability-time policy to one dataset's version (ADR-0020).

Which rule a dataset follows is a declaration per `(dataset_code, source)`, and
so is which evidence types that source accepts. Both live in the database, so
opting a source in is configuration rather than a code change — and a source
that is only half opted in fails loudly instead of quietly writing evidence its
own policy would then ignore.
"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import dataset_release_rules, dataset_sources
from stock_data_center.evidence.models import ReleaseRule
from stock_data_center.evidence.plan import PlannedEvidence, evidence_plan
from stock_data_center.evidence.release_rules import ReleaseRuleService
from stock_data_center.provenance import IngestPurpose


class UnacceptedEvidenceTypeError(RuntimeError):
    """The policy planned evidence this source does not accept.

    Writing it would be worse than useless: the resolver filters it out by
    `accepted_evidence_types` (ADR-0010), so the row would sit in storage
    looking like evidence while proving nothing.
    """


class EvidencePolicyService:
    """Turn one written version into the evidence its source may claim."""

    def __init__(self, rules: ReleaseRuleService | None = None) -> None:
        self._rules = rules or ReleaseRuleService()

    def rule_for(
        self, connection: Connection, *, dataset_code: str, source: str
    ) -> ReleaseRule | None:
        """The release rule this source follows, if it declared one."""
        row = connection.execute(
            sa.select(
                dataset_release_rules.c.rule_id, dataset_release_rules.c.version
            ).where(
                dataset_release_rules.c.dataset_code == dataset_code,
                dataset_release_rules.c.source == source,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return self._rules.rule(
            connection, rule_id=row["rule_id"], version=row["version"]
        )

    def plan(
        self,
        connection: Connection,
        *,
        dataset_code: str,
        source: str,
        period: date,
        purpose: IngestPurpose,
        version_created: bool,
        captured_at: datetime,
    ) -> tuple[PlannedEvidence, ...]:
        rule = self.rule_for(
            connection, dataset_code=dataset_code, source=source
        )
        rule_instant = None
        rule_source = None
        if rule is not None:
            resolved = self._rules.resolve(
                connection,
                rule_id=rule.rule_id,
                version=rule.version,
                period=period,
            )
            rule_instant = resolved.published_at
            rule_source = resolved.evidence_source

        planned = evidence_plan(
            purpose=purpose,
            version_created=version_created,
            captured_at=captured_at,
            rule_instant=rule_instant,
            rule_source=rule_source,
        )
        self._require_accepted(
            connection, dataset_code=dataset_code, source=source, planned=planned
        )
        return planned

    @staticmethod
    def _require_accepted(
        connection: Connection,
        *,
        dataset_code: str,
        source: str,
        planned: tuple[PlannedEvidence, ...],
    ) -> None:
        accepted = connection.scalar(
            sa.select(dataset_sources.c.accepted_evidence_types).where(
                dataset_sources.c.dataset_code == dataset_code,
                dataset_sources.c.source == source,
            )
        )
        if accepted is None:
            raise LookupError(
                f"no source policy for {dataset_code}/{source}"
            )
        unaccepted = sorted(
            {item.evidence_type for item in planned} - set(accepted)
        )
        if unaccepted:
            raise UnacceptedEvidenceTypeError(
                f"{dataset_code}/{source} does not accept {unaccepted}; add them "
                "to accepted_evidence_types in the same migration that maps the "
                "rule, or the evidence would be written and then ignored"
            )
