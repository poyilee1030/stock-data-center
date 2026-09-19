"""Monthly-revenue hooks for the shared raw-first import lifecycle.

One MOPS `t21sc03` page per market, month and page (`_0` domestic, `_1`
foreign/KY). Every row either stores or fails its page. Amounts are 千元
converted to TWD; the published comparatives are stored as published.

Publication evidence is whatever the source's policy allows. In Step 22-a the
two sources accept only `official` and follow no release rule, so every version
records `unknown`: System-PIT visible, Market-PIT invisible until Step 22-c
attaches the evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

from stock_data_center.db.metadata import publication_evidence
from stock_data_center.evidence import EvidencePolicyService
from stock_data_center.ingestion.adapters.monthly_revenue import (
    MOPSMonthlyRevenueAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    EvidenceContext,
    MonthlyRevenueRequest,
    ParsedMonthlyRevenue,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_data import MarketDataWriter
from stock_data_center.monthly_revenue.ingestion import (
    MonthlyRevenuePublication,
    MonthlyRevenueWriter,
    RevenueLineageRef,
)


class MonthlyRevenueImporter(
    RawFirstImporter[MonthlyRevenueRequest, ParsedMonthlyRevenue]
):
    """Import one market's monthly revenue page for one month."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        security_writer: MarketDataWriter | None = None,
        writer: MonthlyRevenueWriter | None = None,
        policy: EvidencePolicyService | None = None,
    ) -> None:
        super().__init__(engine, raw_store=raw_store, fetcher=fetcher)
        self._security_writer = security_writer or MarketDataWriter()
        self._writer = writer or MonthlyRevenueWriter()
        self._policy = policy or EvidencePolicyService()

    def _source_scope(
        self,
        adapter: RawFirstAdapter[MonthlyRevenueRequest, ParsedMonthlyRevenue],
        request: MonthlyRevenueRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "period": f"{request.period.year:04d}-{request.period.month:02d}",
            "page": request.page.value,
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(self, adapter) -> Mapping[str, object]:
        if not isinstance(adapter, MOPSMonthlyRevenueAdapter):
            raise TypeError("MonthlyRevenueImporter requires a MOPSMonthlyRevenueAdapter")
        return {
            "market": adapter.market,
            "amount_unit": "TWD, from 千元 × 1,000",
            "percentages": "as published; blank is NULL",
            "note": "備註 verbatim, '-' included; blank is NULL",
            "encoding": "cp950",
            "not_stored": "names, 合計 and whole-market total rows, 出表日期",
        }

    def _dataset_description(self, adapter) -> str:
        return "per-security monthly revenue"

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter,
        request: MonthlyRevenueRequest,
        parsed: ParsedMonthlyRevenue,
        lineage,
        context: EvidenceContext,
        dependencies: object | None = None,
    ) -> BusinessWriteResult:
        if not isinstance(adapter, MOPSMonthlyRevenueAdapter):
            raise TypeError("MonthlyRevenueImporter requires a MOPSMonthlyRevenueAdapter")
        revenue_lineage = RevenueLineageRef(lineage.raw_artifact_id, lineage.ingest_run_id)
        security_ids = self._security_writer.register_securities(
            connection, security_codes=[row.security_code for row in parsed.rows]
        )
        written = [
            self._writer.append_revenue(
                connection,
                security_id=security_ids[row.security_code],
                source=adapter.source,
                observation=row.observation,
                lineage=revenue_lineage,
            )
            for row in parsed.rows
        ]
        created = sum(1 for item in written if item.created)

        bound = self._policy.bind(
            connection, dataset_code=adapter.dataset_code, source=adapter.source
        )
        plans = bound.plan_many(
            connection,
            period=date(parsed.period.year, parsed.period.month, 1),
            purpose=context.purpose,
            captured_at=context.captured_at,
            versions=[(item.version_id, item.created) for item in written],
        )
        # The writer returns the id whether it inserted the row or found it, so
        # what was already there is read first to count the new ones honestly.
        existing = set(
            connection.scalars(
                sa.select(publication_evidence.c.id).where(
                    publication_evidence.c.dataset_code == adapter.dataset_code,
                    publication_evidence.c.source == adapter.source,
                    publication_evidence.c.monthly_revenue_version_id.in_(
                        sorted({item.version_id for item in written})
                    ),
                )
            )
        )
        evidence_ids: set[int] = set()
        evidence_observations = 0
        unknown = 0
        claimed: set[str] = set()
        for item in written:
            for entry in plans[item.version_id]:
                evidence_ids.add(
                    self._writer.append_publication_evidence(
                        connection,
                        source=adapter.source,
                        version_id=item.version_id,
                        observation=MonthlyRevenuePublication(
                            evidence_kind=entry.evidence_kind,  # type: ignore[arg-type]
                            published_at=entry.published_at,
                            evidence_source=entry.evidence_source,
                            evidence_type=entry.evidence_type,
                            quality_rank=entry.quality_rank,
                        ),
                        lineage=revenue_lineage,
                    )
                )
                evidence_observations += 1
                unknown += entry.published_at is None
                claimed.add(entry.evidence_type)

        period = f"{parsed.period.year:04d}-{parsed.period.month:02d}"
        return BusinessWriteResult(
            business_versions_created=created,
            business_versions_deduplicated=len(written) - created,
            publication_evidence_created=len(evidence_ids - existing),
            publication_evidence_deduplicated=evidence_observations - len(evidence_ids - existing),
            evidence_observations=evidence_observations,
            unknown_publication_observations=unknown,
            normalized_rows=len(parsed.rows),
            coverage_start=parsed.coverage_start,
            coverage_end=parsed.coverage_end,
            reconciliation={
                "requested_period": period,
                "requested_page": request.page.value,
                "actual_market": parsed.market,
                "actual_row_count": len(parsed.rows),
                "stored_row_count": len(parsed.rows),
                "header_variant": parsed.header_variant,
                "source_fields": list(parsed.source_fields),
                "page_generated_on": parsed.generated_on,
                "coverage_validation": "not_evaluated",
                "coverage_gaps": None,
                "publication_time": bound.rule.evidence_source if bound.rule else "unknown",
                "availability_time_evidence": sorted(claimed),
            },
        )
