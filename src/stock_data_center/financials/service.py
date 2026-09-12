"""Dataset-specific PIT reads for sealed financial filing aggregates."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import (
    financial_facts,
    financial_filing_version_observations,
    ingest_runs,
    quarterly_financial_summary,
    raw_artifact_observations,
    raw_artifacts,
    security,
)
from stock_data_center.financials.models import (
    ActualEPS,
    FilingLineageObservation,
    FilingPeriod,
    FinancialFact,
    QuarterlyMetric,
    ResolvedFinancialFiling,
    XBRLContext,
)
from stock_data_center.pit import PITResolver
from stock_data_center.pit.models import PITContext


class FinancialFilingService:
    def __init__(self, *, resolver: PITResolver | None = None) -> None:
        self._resolver = resolver or PITResolver()

    def filing(
        self,
        connection: Connection,
        *,
        security_code: str,
        period: FilingPeriod,
        context: PITContext,
        source: str | None = None,
    ) -> ResolvedFinancialFiling | None:
        security_id = connection.scalar(
            sa.select(security.c.id).where(security.c.security_code == security_code)
        )
        if security_id is None:
            return None
        resolved = self._resolver.resolve(
            connection,
            dataset_code="financial_filing",
            logical_key={
                "security_id": security_id,
                "report_year": period.report_year,
                "report_quarter": period.report_quarter,
            },
            context=context,
            source=source,
        )
        if resolved is None:
            return None
        version_id = resolved.provenance.version_id
        facts = connection.execute(
            sa.select(financial_facts)
            .where(financial_facts.c.filing_version_id == version_id)
            .order_by(
                financial_facts.c.concept_qname,
                financial_facts.c.context_hash,
                financial_facts.c.unit_identity,
            )
        ).mappings()
        summaries = connection.execute(
            sa.select(quarterly_financial_summary)
            .where(quarterly_financial_summary.c.filing_version_id == version_id)
            .order_by(quarterly_financial_summary.c.metric_code)
        ).mappings()
        return ResolvedFinancialFiling(
            filing=resolved,
            facts=tuple(
                FinancialFact(
                    fact_id=row["id"],
                    concept_qname=row["concept_qname"],
                    context_hash=row["context_hash"],
                    context=XBRLContext(
                        entity_identifier=row["entity_identifier"],
                        period_type=row["period_type"],
                        instant_date=row["instant_date"],
                        period_start=row["period_start"],
                        period_end=row["period_end"],
                        explicit_dimensions=row["explicit_dimensions"],
                        typed_dimensions=row["typed_dimensions"],
                        scenario=row["scenario"],
                        segment=row["segment"],
                    ),
                    unit_identity=row["unit_identity"],
                    numeric_value=row["numeric_value"],
                    text_value=row["text_value"],
                    decimals=row["decimals"],
                )
                for row in facts
            ),
            summary=tuple(
                QuarterlyMetric(
                    metric_code=row["metric_code"],
                    value=row["value"],
                    unit_identity=row["unit_identity"],
                )
                for row in summaries
            ),
        )

    def actual_eps(
        self,
        connection: Connection,
        *,
        security_code: str,
        period: FilingPeriod,
        context: PITContext,
        source: str | None = None,
    ) -> ActualEPS | None:
        filing = self.filing(
            connection,
            security_code=security_code,
            period=period,
            context=context,
            source=source,
        )
        if filing is None:
            return None
        matches = [
            item for item in filing.summary if item.metric_code == "basic_eps"
        ]
        if not matches:
            return None
        metric = matches[0]
        return ActualEPS(metric.value, metric.unit_identity, filing.filing)

    def observations(
        self, connection: Connection, *, version_id: int
    ) -> tuple[FilingLineageObservation, ...]:
        rows = connection.execute(
            sa.select(
                financial_filing_version_observations.c.raw_artifact_id,
                raw_artifacts.c.raw_artifact_hash,
                raw_artifacts.c.storage_uri,
                financial_filing_version_observations.c.ingest_run_id,
                ingest_runs.c.status,
                raw_artifact_observations.c.source_uri,
                raw_artifact_observations.c.fetched_at,
            )
            .select_from(
                financial_filing_version_observations.join(
                    raw_artifact_observations,
                    sa.and_(
                        raw_artifact_observations.c.raw_artifact_id
                        == financial_filing_version_observations.c.raw_artifact_id,
                        raw_artifact_observations.c.ingest_run_id
                        == financial_filing_version_observations.c.ingest_run_id,
                    ),
                )
                .join(
                    raw_artifacts,
                    raw_artifacts.c.id
                    == financial_filing_version_observations.c.raw_artifact_id,
                )
                .join(
                    ingest_runs,
                    ingest_runs.c.id
                    == financial_filing_version_observations.c.ingest_run_id,
                )
            )
            .where(
                financial_filing_version_observations.c.filing_version_id
                == version_id
            )
            .order_by(
                raw_artifact_observations.c.fetched_at,
                financial_filing_version_observations.c.ingest_run_id,
            )
        ).mappings()
        return tuple(
            FilingLineageObservation(
                raw_artifact_id=row["raw_artifact_id"],
                raw_artifact_hash=row["raw_artifact_hash"],
                raw_artifact_uri=row["storage_uri"],
                ingest_run_id=row["ingest_run_id"],
                ingest_run_status=row["status"],
                source_uri=row["source_uri"],
                fetched_at=row["fetched_at"],
            )
            for row in rows
        )
