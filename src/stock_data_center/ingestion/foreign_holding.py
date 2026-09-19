"""Foreign-holding hooks for the shared raw-first import lifecycle.

One table per market and trade date, like the per-security flows. Every row
either stores or fails its file. The TPEx table comes from MOPS as a POST; the
lifecycle records that request on the run and in the manifest scope (20-c).
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import Connection, Engine

from stock_data_center.evidence import EvidencePolicyService
from stock_data_center.ingestion.adapters.foreign_holding import (
    ForeignHoldingAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    EvidenceContext,
    ForeignHoldingRequest,
    ParsedForeignHolding,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.institutional_financing import (
    InstitutionalFinancingWriter,
    SourceLineageRef,
    SourcePublication,
)
from stock_data_center.market_data import MarketDataWriter


class ForeignHoldingImporter(
    RawFirstImporter[ForeignHoldingRequest, ParsedForeignHolding]
):
    """Import one market's per-security foreign holding for one trade date."""

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
            ForeignHoldingRequest, ParsedForeignHolding
        ],
        request: ForeignHoldingRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "trade_date": request.trade_date.isoformat(),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(self, adapter) -> Mapping[str, object]:
        if not isinstance(adapter, ForeignHoldingAdapter):
            raise TypeError(
                "ForeignHoldingImporter requires an ForeignHoldingAdapter"
            )
        return {
            "market": adapter.market,
            "encoding": getattr(adapter, "encoding", "utf-8"),
            "quantity_unit": "shares",
            "ratio_unit": "percent, as published",
            "change_reason": "published code 2-5; blank stored as NULL; link markup dropped",
            "not_stored": "security name, ISIN",
        }

    def _dataset_description(self, adapter) -> str:
        return "per-security foreign and mainland investor holding"

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter,
        request: ForeignHoldingRequest,
        parsed: ParsedForeignHolding,
        lineage,
        context: EvidenceContext,
        dependencies: object | None = None,
    ) -> BusinessWriteResult:
        if not isinstance(adapter, ForeignHoldingAdapter):
            raise TypeError(
                "ForeignHoldingImporter requires an ForeignHoldingAdapter"
            )
        source_lineage = SourceLineageRef(lineage.raw_artifact_id, lineage.ingest_run_id)
        security_ids = self._security_writer.register_securities(
            connection, security_codes=[row.security_code for row in parsed.rows]
        )
        written = self._writer.append_foreign_holdings(
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
