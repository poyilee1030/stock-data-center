"""Official-valuation hooks for the shared raw-first import lifecycle.

One file per market and trade date, like the daily prices. A row the contract
cannot store — TPEx's first-day `"0"` ratios — is quarantined on its own,
against this file's own provenance, and the other rows of the date import.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

from stock_data_center.db import metadata
from stock_data_center.evidence import EvidencePolicyService
from stock_data_center.ingestion.adapters.official_valuation import (
    OfficialValuationAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    ArtifactOrigin,
    EvidenceContext,
    IngestPurpose,
    OfficialValuationRequest,
    ParsedOfficialValuation,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_data import MarketDataWriter
from stock_data_center.market_reference.ingestion import MarketReferenceWriter
from stock_data_center.market_reference.models import (
    Phase8LineageRef,
    Phase8Publication,
)


class OfficialValuationImporter(
    RawFirstImporter[OfficialValuationRequest, ParsedOfficialValuation]
):
    """Import one market's published valuation ratios for one trade date."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        security_writer: MarketDataWriter | None = None,
        writer: MarketReferenceWriter | None = None,
        policy: EvidencePolicyService | None = None,
    ) -> None:
        super().__init__(engine, raw_store=raw_store, fetcher=fetcher)
        self._security_writer = security_writer or MarketDataWriter()
        self._writer = writer or MarketReferenceWriter()
        self._policy = policy or EvidencePolicyService()

    def _source_scope(
        self,
        adapter: RawFirstAdapter[OfficialValuationRequest, ParsedOfficialValuation],
        request: OfficialValuationRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "trade_date": request.trade_date.isoformat(),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(self, adapter) -> Mapping[str, object]:
        if not isinstance(adapter, OfficialValuationAdapter):
            raise TypeError("OfficialValuationImporter requires an OfficialValuationAdapter")
        return {
            "market": adapter.market,
            "not_computed_markers": sorted(adapter.not_computed),
            "zero_ratio_not_computed": adapter.zero_ratio_not_computed,
            "ratio_unit": "multiple",
            "dividend_yield_unit": "percentage_points",
            "dividend_per_share_unit": (
                "twd_per_share" if adapter.market == "TPEx" else "not published"
            ),
            "dividend_year": "ROC year + 1911",
            "report_period": "Gregorian YYYYQn",
            "other_nonpositive_ratio": "row quarantined",
        }

    def _dataset_description(self, adapter) -> str:
        return "official exchange-published valuation ratios"

    def _capture_dependencies(
        self,
        *,
        adapter,
        request: OfficialValuationRequest,
        parsed: ParsedOfficialValuation,
        import_id: UUID,
        purpose: IngestPurpose,
        artifact_origin: ArtifactOrigin,
    ) -> UUID:
        # Nothing else to fetch. The write hook needs the import id to file
        # a row's quarantine under this import, and this is where it is known.
        return import_id

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter,
        request: OfficialValuationRequest,
        parsed: ParsedOfficialValuation,
        lineage,
        context: EvidenceContext,
        dependencies: object | None = None,
    ) -> BusinessWriteResult:
        if not isinstance(adapter, OfficialValuationAdapter):
            raise TypeError("OfficialValuationImporter requires an OfficialValuationAdapter")
        if not isinstance(dependencies, UUID):
            raise TypeError("the import id was not captured before the write")
        reference_lineage = Phase8LineageRef(lineage.raw_artifact_id, lineage.ingest_run_id)

        security_ids = self._security_writer.register_securities(
            connection, security_codes=[row.security_code for row in parsed.rows]
        )
        written = self._writer.append_official_valuations(
            connection,
            source=adapter.source,
            observations=[
                (security_ids[row.security_code], row.observation) for row in parsed.rows
            ],
            lineage=reference_lineage,
        )
        created = sum(1 for item in written if item.created)

        quarantine = metadata.tables["import_quarantine"]
        for rejected in parsed.rejected:
            connection.execute(
                sa.insert(quarantine).values(
                    import_id=dependencies,
                    resource_key=adapter.resource(request).resource_key,
                    ingest_run_id=lineage.ingest_run_id,
                    raw_artifact_id=lineage.raw_artifact_id,
                    reason_code=rejected.reason_code,
                    reason_detail=rejected.detail,
                )
            )

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
                Phase8Publication(
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
                lineage=reference_lineage,
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
                "actual_row_count": len(parsed.rows) + len(parsed.rejected),
                "stored_row_count": len(parsed.rows),
                "row_quarantine": {
                    rejected.security_code: rejected.reason_code
                    for rejected in parsed.rejected
                },
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
