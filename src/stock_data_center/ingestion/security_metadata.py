"""Security-metadata hooks for the shared Phase 9 import lifecycle."""

from __future__ import annotations

from collections.abc import Mapping

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

from stock_data_center.db.metadata import publication_evidence
from stock_data_center.ingestion.adapters.security_metadata import (
    SecurityMetadataAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    ParsedSecurityMetadata,
    SecurityMetadataRequest,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_data import (
    LineageRef,
    MarketDataWriter,
    PublicationObservation,
)


class SecurityMetadataImporter(
    RawFirstImporter[SecurityMetadataRequest, ParsedSecurityMetadata]
):
    """Import an official current-company snapshot without backdating state."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        writer: MarketDataWriter | None = None,
    ) -> None:
        super().__init__(engine, raw_store=raw_store, fetcher=fetcher)
        self._writer = writer or MarketDataWriter()

    def _source_scope(
        self,
        adapter: RawFirstAdapter[SecurityMetadataRequest, ParsedSecurityMetadata],
        request: SecurityMetadataRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "snapshot": "current",
            "expected_report_date": (
                request.expected_report_date.isoformat()
                if request.expected_report_date
                else None
            ),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(
        self,
        adapter: RawFirstAdapter[SecurityMetadataRequest, ParsedSecurityMetadata],
    ) -> Mapping[str, object]:
        if not isinstance(adapter, SecurityMetadataAdapter):
            raise TypeError(
                "SecurityMetadataImporter requires a SecurityMetadataAdapter"
            )
        return {
            "market": adapter.market,
            "effective_from": "official_snapshot_report_date",
            "listed_on": "official_listing_date",
            "unchanged_snapshot": "reuse_latest_equal_business_state",
            "omission": "does_not_imply_delisting",
            "publication_time": "unknown",
        }

    def _dataset_description(
        self,
        adapter: RawFirstAdapter[SecurityMetadataRequest, ParsedSecurityMetadata],
    ) -> str:
        return "official effective-dated security identity metadata"

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter: RawFirstAdapter[SecurityMetadataRequest, ParsedSecurityMetadata],
        request: SecurityMetadataRequest,
        parsed: ParsedSecurityMetadata,
        lineage: LineageRef,
    ) -> BusinessWriteResult:
        created = 0
        deduplicated = 0
        evidence_created = 0
        evidence_deduplicated = 0
        evidence_observations = 0
        evidence_source = f"{adapter.source} official current-company endpoint"
        for record in parsed.rows:
            security_id = self._writer.register_security(
                connection, security_code=record.security_code
            )
            written = self._writer.append_security_metadata_snapshot(
                connection,
                security_id=security_id,
                source=adapter.source,
                observation=record.observation,
                lineage=lineage,
            )
            created += int(written.created)
            deduplicated += int(not written.created)
            evidence_existed = connection.scalar(
                sa.select(
                    sa.exists().where(
                        publication_evidence.c.dataset_code == adapter.dataset_code,
                        publication_evidence.c.source == adapter.source,
                        publication_evidence.c.security_metadata_version_id
                        == written.version_id,
                        publication_evidence.c.evidence_kind == "unknown",
                        publication_evidence.c.evidence_source == evidence_source,
                        publication_evidence.c.evidence_type == "official",
                        publication_evidence.c.quality_rank == 0,
                    )
                )
            )
            self._writer.append_publication_evidence(
                connection,
                dataset_code="security_metadata",
                source=adapter.source,
                version_id=written.version_id,
                observation=PublicationObservation(
                    evidence_kind="unknown",
                    published_at=None,
                    evidence_source=evidence_source,
                    evidence_type="official",
                    quality_rank=0,
                ),
                lineage=lineage,
            )
            evidence_created += int(not evidence_existed)
            evidence_deduplicated += int(evidence_existed)
            evidence_observations += 1

        report_date = parsed.report_date.isoformat()
        return BusinessWriteResult(
            business_versions_created=created,
            business_versions_deduplicated=deduplicated,
            publication_evidence_created=evidence_created,
            publication_evidence_deduplicated=evidence_deduplicated,
            evidence_observations=evidence_observations,
            unknown_publication_observations=len(parsed.rows),
            normalized_rows=len(parsed.rows),
            coverage_start=parsed.report_date,
            coverage_end=parsed.report_date,
            reconciliation={
                "requested_snapshot": "current",
                "expected_report_date": (
                    request.expected_report_date.isoformat()
                    if request.expected_report_date
                    else None
                ),
                "actual_report_date": report_date,
                "actual_market": parsed.market,
                "actual_row_count": len(parsed.rows),
                "coverage_start": report_date,
                "coverage_end": report_date,
                "coverage_validation": "snapshot_only",
                "coverage_gaps": None,
                "historical_coverage": "not_evaluated",
                "market_transfer_history": "not_in_scope",
                "effective_time": "official_snapshot_report_date",
                "listing_time": "official_listing_date",
                "omitted_security_semantics": "does_not_imply_delisting",
                "industry_semantics": "source_industry_code",
                "publication_time": "unknown",
                "source_fields": list(parsed.source_fields),
            },
        )
