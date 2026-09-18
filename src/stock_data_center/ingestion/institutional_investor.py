"""Per-security institutional-flow hooks for the shared raw-first import lifecycle.

One file per market and trade date, like the daily prices and the valuation
ratios. Every row either stores or fails its file: no value of this dataset
has an owner-approved "not published" marker to store as NULL.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import Connection, Engine

from stock_data_center.evidence import EvidencePolicyService
from stock_data_center.ingestion.adapters.institutional_investor import (
    InstitutionalInvestorAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    EvidenceContext,
    InstitutionalInvestorRequest,
    ParsedInstitutionalInvestor,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.institutional_financing import (
    InstitutionalFinancingWriter,
    SourceLineageRef,
    SourcePublication,
)
from stock_data_center.market_data import MarketDataWriter


class InstitutionalInvestorImporter(
    RawFirstImporter[InstitutionalInvestorRequest, ParsedInstitutionalInvestor]
):
    """Import one market's per-security institutional flows for one trade date."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        security_writer: MarketDataWriter | None = None,
        writer: InstitutionalFinancingWriter | None = None,
        policy: EvidencePolicyService | None = None,
    ) -> None:
        super().__init__(engine, raw_store=raw_store, fetcher=fetcher)
        self._security_writer = security_writer or MarketDataWriter()
        self._writer = writer or InstitutionalFinancingWriter()
        self._policy = policy or EvidencePolicyService()

    def _source_scope(
        self,
        adapter: RawFirstAdapter[
            InstitutionalInvestorRequest, ParsedInstitutionalInvestor
        ],
        request: InstitutionalInvestorRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "trade_date": request.trade_date.isoformat(),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(self, adapter) -> Mapping[str, object]:
        if not isinstance(adapter, InstitutionalInvestorAdapter):
            raise TypeError(
                "InstitutionalInvestorImporter requires an InstitutionalInvestorAdapter"
            )
        return {
            "market": adapter.market,
            "quantity_unit": "shares",
            "columns": dict(adapter.columns),
            "net": "published, signed, never recomputed",
            "negative_gross": "file fails",
        }

    def _dataset_description(self, adapter) -> str:
        return "per-security institutional investor flows"

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter,
        request: InstitutionalInvestorRequest,
        parsed: ParsedInstitutionalInvestor,
        lineage,
        context: EvidenceContext,
        dependencies: object | None = None,
    ) -> BusinessWriteResult:
        if not isinstance(adapter, InstitutionalInvestorAdapter):
            raise TypeError(
                "InstitutionalInvestorImporter requires an InstitutionalInvestorAdapter"
            )
        source_lineage = SourceLineageRef(lineage.raw_artifact_id, lineage.ingest_run_id)
        security_ids = self._security_writer.register_securities(
            connection, security_codes=[row.security_code for row in parsed.rows]
        )
        written = self._writer.append_institutional_investors(
            connection,
            source=adapter.source,
            observations=[
                (security_ids[row.security_code], row.observation) for row in parsed.rows
            ],
            lineage=source_lineage,
        )
        created = sum(1 for item in written if item.created)

        bound = self._policy.bind(
            connection, dataset_code=adapter.dataset_code, source=adapter.source
        )
        plans = bound.plan_many(
            connection,
            period=parsed.trade_date,
            purpose=context.purpose,
            captured_at=context.captured_at,
            versions=[(item.version_id, item.created) for item in written],
        )
        planned = [
            (
                item.version_id,
                SourcePublication(
                    evidence_kind=entry.evidence_kind,
                    published_at=entry.published_at,
                    evidence_source=entry.evidence_source,
                    evidence_type=entry.evidence_type,
                    quality_rank=entry.quality_rank,
                ),
            )
            for item in written
            for entry in plans[item.version_id]
        ]
        evidence_created, evidence_deduplicated = (
            self._writer.append_publication_evidence_batch(
                connection,
                dataset_code=adapter.dataset_code,
                source=adapter.source,
                planned=planned,
                lineage=source_lineage,
            )
        )
        unknown = sum(1 for _, publication in planned if publication.published_at is None)
        claimed = sorted({publication.evidence_type for _, publication in planned})

        return BusinessWriteResult(
            business_versions_created=created,
            business_versions_deduplicated=len(written) - created,
            publication_evidence_created=evidence_created,
            publication_evidence_deduplicated=evidence_deduplicated,
            evidence_observations=len(planned),
            unknown_publication_observations=unknown,
            normalized_rows=len(parsed.rows),
            coverage_start=parsed.coverage_start,
            coverage_end=parsed.coverage_end,
            reconciliation={
                "requested_date_start": request.trade_date.isoformat(),
                "requested_date_end": request.trade_date.isoformat(),
                "actual_market": parsed.market,
                "actual_row_count": len(parsed.rows),
                "stored_row_count": len(parsed.rows),
                "header_variant": parsed.header_variant,
                "source_fields": list(parsed.source_fields),
                "coverage_start": parsed.trade_date.isoformat(),
                "coverage_end": parsed.trade_date.isoformat(),
                "coverage_validation": "not_evaluated",
                "coverage_gaps": None,
                "publication_time": bound.rule.evidence_source if bound.rule else "unknown",
                "availability_time_evidence": claimed,
            },
        )
