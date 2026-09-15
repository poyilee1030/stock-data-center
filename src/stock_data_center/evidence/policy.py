"""Apply the availability-time policy to one dataset's version (ADR-0020).

Which rule a dataset follows is a declaration per `(dataset_code, source)`, and
so is which evidence types that source accepts. Both live in the database, so
opting a source in is configuration rather than a code change — and a source
that is only half opted in fails loudly instead of quietly writing evidence its
own policy would then ignore.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import (
    dataset_release_rules,
    dataset_sources,
    publication_evidence,
)
from stock_data_center.evidence.models import ReleaseRule

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Which publication_evidence column carries each dataset's version id.
DATASET_TARGETS = {
    "daily_price": "daily_price_version_id",
    "security_metadata": "security_metadata_version_id",
    "monthly_revenue": "monthly_revenue_version_id",
    "financial_filing": "financial_filing_version_id",
    "tdcc_snapshot": "tdcc_snapshot_version_id",
    "trading_calendar": "trading_calendar_version_id",
}
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
        version_id: int | None = None,
    ) -> tuple[PlannedEvidence, ...]:
        """Plan one version's evidence. Prefer `bind` when writing many rows."""
        return self.bind(
            connection, dataset_code=dataset_code, source=source
        ).plan(
            connection,
            period=period,
            purpose=purpose,
            version_created=version_created,
            captured_at=captured_at,
            version_id=version_id,
        )

    def bind(
        self, connection: Connection, *, dataset_code: str, source: str
    ) -> BoundEvidencePolicy:
        """Resolve the rule and the allowlist once for a whole write.

        Both are constant for a `(dataset_code, source)`, and a whole-market
        import writes thousands of rows through them.
        """
        accepted = connection.scalar(
            sa.select(dataset_sources.c.accepted_evidence_types).where(
                dataset_sources.c.dataset_code == dataset_code,
                dataset_sources.c.source == source,
            )
        )
        if accepted is None:
            raise LookupError(f"no source policy for {dataset_code}/{source}")
        return BoundEvidencePolicy(
            rules=self._rules,
            dataset_code=dataset_code,
            source=source,
            rule=self.rule_for(
                connection, dataset_code=dataset_code, source=source
            ),
            accepted=frozenset(accepted),
        )


@dataclass(frozen=True, slots=True)
class BoundEvidencePolicy:
    """One dataset source's policy, resolved once."""

    rules: ReleaseRuleService
    dataset_code: str
    source: str
    rule: ReleaseRule | None
    accepted: frozenset[str]

    def plan(
        self,
        connection: Connection,
        *,
        period: date,
        purpose: IngestPurpose,
        version_created: bool,
        captured_at: datetime,
        version_id: int | None = None,
    ) -> tuple[PlannedEvidence, ...]:
        rule_instant = None
        rule_source = None
        if self.rule is not None:
            resolved = self.rules.resolve(
                connection,
                rule_id=self.rule.rule_id,
                version=self.rule.version,
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
            proven_capture_at=self._stored_capture(connection, version_id),
        )
        return self._filter(planned)

    def _stored_capture(
        self, connection: Connection, version_id: int | None
    ) -> datetime | None:
        """The earliest capture already proven for this version.

        Falsification has to follow from what is stored, not from whether *this*
        run created the version: a re-import would otherwise append the very
        rule an earlier run withheld, into storage that cannot take it back.
        """
        if version_id is None:
            return None
        target = DATASET_TARGETS.get(self.dataset_code)
        if target is None:
            return None
        return connection.scalar(
            sa.select(sa.func.min(publication_evidence.c.published_at)).where(
                publication_evidence.c.dataset_code == self.dataset_code,
                publication_evidence.c.source == self.source,
                publication_evidence.c[target] == version_id,
                publication_evidence.c.evidence_type == "capture_bound",
            )
        )

    def _filter(
        self, planned: tuple[PlannedEvidence, ...]
    ) -> tuple[PlannedEvidence, ...]:
        unaccepted = sorted(
            {item.evidence_type for item in planned} - self.accepted
        )
        if not unaccepted:
            return planned
        if self.rule is not None:
            # This source opted into a rule, so an allowlist that cannot carry
            # the result is a half-finished migration, not a default.
            raise UnacceptedEvidenceTypeError(
                f"{self.dataset_code}/{self.source} maps {self.rule.evidence_source} "
                f"but does not accept {unaccepted}; add them to "
                "accepted_evidence_types in the same migration"
            )
        # A source that declared nothing keeps the pre-ADR-0020 behaviour rather
        # than failing an import that was never opted in.
        return evidence_plan(
            purpose=IngestPurpose.UNSPECIFIED,
            version_created=False,
            captured_at=planned[0].published_at or _EPOCH,
            rule_instant=None,
        )
