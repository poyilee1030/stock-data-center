"""Normalized append-only writes for financial filing aggregates."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Connection, RowMapping
from sqlalchemy.dialects.postgresql import insert

from stock_data_center.db.metadata import (
    financial_facts,
    financial_filing_seals,
    financial_filing_version_observations,
    financial_filing_versions,
    publication_evidence,
    publication_evidence_observations,
    quarterly_financial_summary,
)
from stock_data_center.financials.classification import (
    SourceContextClassification,
    classify_eps_period_basis,
)
from stock_data_center.financials.models import (
    SummaryPeriodBasis,
    XBRLContext,
)


@dataclass(frozen=True, slots=True)
class FilingLineageRef:
    raw_artifact_id: UUID
    ingest_run_id: UUID


@dataclass(frozen=True, slots=True)
class FinancialFilingObservation:
    filing_key: str
    report_year: int
    report_quarter: int
    period_start: date
    period_end: date
    currency: str

    def __post_init__(self) -> None:
        if not self.filing_key:
            raise ValueError("filing_key must not be empty")
        if not 1900 <= self.report_year <= 9999:
            raise ValueError("report_year must be between 1900 and 9999")
        if not 1 <= self.report_quarter <= 4:
            raise ValueError("report_quarter must be between 1 and 4")
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        currency = self.currency.upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter code")
        object.__setattr__(self, "currency", currency)


@dataclass(frozen=True, slots=True)
class FinancialFactObservation:
    concept_qname: str
    context: XBRLContext
    unit_identity: str
    numeric_value: Decimal | None = None
    text_value: str | None = None
    is_nil: bool = False
    decimals: str | None = None

    def __post_init__(self) -> None:
        qname = self.concept_qname
        if not qname.startswith("{") or "}" not in qname[1:]:
            raise ValueError("concept_qname must use canonical {namespace}local form")
        namespace, local = qname[1:].split("}", 1)
        if not namespace or not local or "{" in local or "}" in local:
            raise ValueError("concept_qname must use canonical {namespace}local form")
        value_count = int(self.numeric_value is not None) + int(
            self.text_value is not None
        )
        if self.is_nil and value_count:
            raise ValueError("nil facts cannot contain numeric or text values")
        if not self.is_nil and value_count != 1:
            raise ValueError(
                "non-nil facts require exactly one numeric or text value"
            )


@dataclass(frozen=True, slots=True)
class QuarterlySummaryObservation:
    metric_code: str
    period_basis: SummaryPeriodBasis
    value: Decimal
    unit_identity: str
    source_fact_id: int
    source_context_classification: SourceContextClassification | None = None

    def __post_init__(self) -> None:
        if (
            self.metric_code == "basic_eps"
            and self.source_context_classification is None
        ):
            raise ValueError(
                "basic_eps requires a validated source context classification"
            )


@dataclass(frozen=True, slots=True)
class FinancialPublication:
    evidence_kind: Literal["assertion", "correction", "retraction", "unknown"]
    published_at: datetime | None
    evidence_source: str
    evidence_type: str
    quality_rank: int
    supersedes_evidence_id: int | None = None

    def __post_init__(self) -> None:
        if self.published_at is not None:
            if (
                self.published_at.tzinfo is None
                or self.published_at.utcoffset() is None
            ):
                raise ValueError("published_at must be timezone-aware")
            object.__setattr__(
                self, "published_at", self.published_at.astimezone(UTC)
            )


@dataclass(frozen=True, slots=True)
class WrittenFiling:
    version_id: int
    business_content_hash: str | None
    created: bool


@dataclass(frozen=True, slots=True)
class SealedFiling:
    version_id: int
    business_content_hash: str
    ingested_at: datetime


class FinancialFilingWriter:
    """Build a filing draft, seal it atomically, and append evidence separately."""

    def begin_filing(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        observation: FinancialFilingObservation,
        lineage: FilingLineageRef,
    ) -> WrittenFiling:
        values = {
            **_dataclass_values(observation),
            "security_id": security_id,
            "source": source,
            "business_content_hash": None,
            "raw_artifact_id": lineage.raw_artifact_id,
            "ingest_run_id": lineage.ingest_run_id,
        }
        inserted = connection.execute(
            insert(financial_filing_versions)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_financial_filing_source_key")
            .returning(
                financial_filing_versions.c.id,
                financial_filing_versions.c.business_content_hash,
            )
        ).mappings().one_or_none()
        if inserted is None:
            predicates = [
                financial_filing_versions.c.source == source,
                financial_filing_versions.c.filing_key == observation.filing_key,
            ]
            predicates.extend(
                financial_filing_versions.c[name] == value
                for name, value in _dataclass_values(observation).items()
                if name != "filing_key"
            )
            inserted = connection.execute(
                sa.select(
                    financial_filing_versions.c.id,
                    financial_filing_versions.c.business_content_hash,
                ).where(*predicates)
            ).mappings().one_or_none()
            if inserted is None:
                raise ValueError(
                    "filing_key already identifies different filing metadata"
                )
            created = False
        else:
            created = True
        self._link_observation(
            connection, version_id=inserted["id"], lineage=lineage
        )
        return WrittenFiling(
            version_id=inserted["id"],
            business_content_hash=inserted["business_content_hash"],
            created=created,
        )

    def append_fact(
        self,
        connection: Connection,
        *,
        version_id: int,
        observation: FinancialFactObservation,
    ) -> int:
        context = observation.context
        return connection.execute(
            financial_facts.insert()
            .values(
                filing_version_id=version_id,
                concept_qname=observation.concept_qname,
                context_hash="0" * 64,
                entity_identifier=context.entity_identifier,
                period_type=context.period_type,
                instant_date=context.instant_date,
                period_start=context.period_start,
                period_end=context.period_end,
                explicit_dimensions=dict(context.explicit_dimensions),
                typed_dimensions=dict(context.typed_dimensions),
                scenario=dict(context.scenario),
                segment=dict(context.segment),
                unit_identity=observation.unit_identity,
                numeric_value=observation.numeric_value,
                text_value=observation.text_value,
                is_nil=observation.is_nil,
                decimals=observation.decimals,
            )
            .returning(financial_facts.c.id)
        ).scalar_one()

    def append_summary(
        self,
        connection: Connection,
        *,
        version_id: int,
        observation: QuarterlySummaryObservation,
    ) -> int:
        values = _dataclass_values(observation)
        classification = values.pop("source_context_classification")
        if observation.metric_code == "basic_eps":
            assert isinstance(classification, SourceContextClassification)
            row = connection.execute(
                sa.select(
                    financial_facts.c.entity_identifier,
                    financial_facts.c.period_type,
                    financial_facts.c.instant_date,
                    financial_facts.c.period_start,
                    financial_facts.c.period_end,
                    financial_facts.c.explicit_dimensions,
                    financial_facts.c.typed_dimensions,
                    financial_facts.c.scenario,
                    financial_facts.c.segment,
                    financial_filing_versions.c.period_start.label(
                        "filing_period_start"
                    ),
                    financial_filing_versions.c.period_end.label(
                        "filing_period_end"
                    ),
                    financial_filing_versions.c.report_quarter,
                )
                .select_from(
                    financial_facts.join(
                        financial_filing_versions,
                        financial_filing_versions.c.id
                        == financial_facts.c.filing_version_id,
                    )
                )
                .where(financial_facts.c.id == observation.source_fact_id)
            ).mappings().one_or_none()
            if row is None:
                raise ValueError("source fact does not exist")
            context = XBRLContext(
                entity_identifier=row["entity_identifier"],
                period_type=row["period_type"],
                instant_date=row["instant_date"],
                period_start=row["period_start"],
                period_end=row["period_end"],
                explicit_dimensions=row["explicit_dimensions"],
                typed_dimensions=row["typed_dimensions"],
                scenario=row["scenario"],
                segment=row["segment"],
            )
            classified_basis = classify_eps_period_basis(
                context=context,
                filing_period_start=row["filing_period_start"],
                filing_period_end=row["filing_period_end"],
                report_quarter=row["report_quarter"],
                classification=classification,
            )
            if classified_basis.value != observation.period_basis.value:
                raise ValueError(
                    "summary period_basis does not match source classification"
                )
        return connection.execute(
            quarterly_financial_summary.insert()
            .values(filing_version_id=version_id, **values)
            .returning(quarterly_financial_summary.c.id)
        ).scalar_one()

    def seal(self, connection: Connection, *, version_id: int) -> SealedFiling:
        row = connection.execute(
            financial_filing_seals.insert()
            .values(
                filing_version_id=version_id,
                business_content_hash="0" * 64,
                ingested_at=sa.func.statement_timestamp(),
            )
            .returning(
                financial_filing_seals.c.filing_version_id,
                financial_filing_seals.c.business_content_hash,
                financial_filing_seals.c.ingested_at,
            )
        ).mappings().one()
        return SealedFiling(
            version_id=row["filing_version_id"],
            business_content_hash=row["business_content_hash"],
            ingested_at=row["ingested_at"],
        )

    def append_publication_evidence(
        self,
        connection: Connection,
        *,
        source: str,
        version_id: int,
        observation: FinancialPublication,
        lineage: FilingLineageRef,
    ) -> int:
        evidence_values = _dataclass_values(observation)
        values = {
            **evidence_values,
            "dataset_code": "financial_filing",
            "source": source,
            "financial_filing_version_id": version_id,
            "publication_evidence_hash": "0" * 64,
            "recorded_at": sa.func.statement_timestamp(),
            "raw_artifact_id": lineage.raw_artifact_id,
            "ingest_run_id": lineage.ingest_run_id,
        }
        evidence_id = connection.execute(
            insert(publication_evidence)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[publication_evidence.c.publication_evidence_hash]
            )
            .returning(publication_evidence.c.id)
        ).scalar_one_or_none()
        if evidence_id is None:
            predicates = [
                publication_evidence.c.dataset_code == "financial_filing",
                publication_evidence.c.source == source,
                publication_evidence.c.financial_filing_version_id == version_id,
            ]
            predicates.extend(
                publication_evidence.c[name].is_not_distinct_from(value)
                for name, value in evidence_values.items()
            )
            evidence_id = connection.execute(
                sa.select(publication_evidence.c.id).where(*predicates)
            ).scalar_one()
        connection.execute(
            insert(publication_evidence_observations)
            .values(
                publication_evidence_id=evidence_id,
                raw_artifact_id=lineage.raw_artifact_id,
                ingest_run_id=lineage.ingest_run_id,
            )
            .on_conflict_do_nothing()
        )
        return evidence_id

    @staticmethod
    def _link_observation(
        connection: Connection,
        *,
        version_id: int,
        lineage: FilingLineageRef,
    ) -> None:
        connection.execute(
            insert(financial_filing_version_observations)
            .values(
                filing_version_id=version_id,
                raw_artifact_id=lineage.raw_artifact_id,
                ingest_run_id=lineage.ingest_run_id,
            )
            .on_conflict_do_nothing()
        )


def _dataclass_values(instance: object) -> dict[str, object]:
    return {field.name: getattr(instance, field.name) for field in fields(instance)}
