"""Historical security listing/delisting hooks for the raw-first lifecycle."""

from __future__ import annotations

from collections.abc import Mapping

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

from stock_data_center.db.metadata import (
    publication_evidence,
    security,
    security_metadata_versions,
)
from stock_data_center.ingestion.adapters.security_lifecycle import (
    SecurityLifecycleAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    ParsedSecurityLifecycle,
    SecurityLifecycleRequest,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_data import (
    LineageRef,
    MarketDataWriter,
    PublicationObservation,
    SecurityMetadataObservation,
)


class SecurityLifecycleImporter(
    RawFirstImporter[SecurityLifecycleRequest, ParsedSecurityLifecycle]
):
    """Import official venue-entry and venue-exit events without inference."""

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
        adapter: RawFirstAdapter[SecurityLifecycleRequest, ParsedSecurityLifecycle],
        request: SecurityLifecycleRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "history_event": getattr(adapter, "event_kind", None),
            "year": request.year,
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(
        self,
        adapter: RawFirstAdapter[SecurityLifecycleRequest, ParsedSecurityLifecycle],
    ) -> Mapping[str, object]:
        if not isinstance(adapter, SecurityLifecycleAdapter):
            raise TypeError(
                "SecurityLifecycleImporter requires a SecurityLifecycleAdapter"
            )
        return {
            "market": adapter.market,
            "event_kind": adapter.event_kind,
            "effective_from": "official_venue_event_date",
            "listed_on": (
                "official_venue_listing_date"
                if adapter.event_kind == "listing"
                else "unknown_without_listing_event"
            ),
            "delisted_on": (
                "official_venue_delisting_date"
                if adapter.event_kind == "delisting"
                else None
            ),
            "publication_time": "unknown",
            "cross_source_writes": "none",
        }

    def _dataset_description(
        self,
        adapter: RawFirstAdapter[SecurityLifecycleRequest, ParsedSecurityLifecycle],
    ) -> str:
        return "official effective-dated security identity metadata"

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter: RawFirstAdapter[SecurityLifecycleRequest, ParsedSecurityLifecycle],
        request: SecurityLifecycleRequest,
        parsed: ParsedSecurityLifecycle,
        lineage: LineageRef,
    ) -> BusinessWriteResult:
        if not isinstance(adapter, SecurityLifecycleAdapter):
            raise TypeError(
                "SecurityLifecycleImporter requires a SecurityLifecycleAdapter"
            )
        created = 0
        deduplicated = 0
        evidence_created = 0
        evidence_deduplicated = 0
        explicit_transfers = 0
        matched_transfers = 0
        evidence_source = (
            f"{adapter.source} official {adapter.event_kind}-history endpoint"
        )
        for event in parsed.rows:
            security_id = self._writer.register_security(
                connection, security_code=event.security_code
            )
            if event.event_kind == "listing":
                observation = SecurityMetadataObservation(
                    effective_from=event.effective_on,
                    market=event.market,
                    name=event.name,
                    listed_on=event.effective_on,
                )
            else:
                observation = SecurityMetadataObservation(
                    effective_from=event.effective_on,
                    market=event.market,
                    name=event.name,
                    delisted_on=event.effective_on,
                )
            written = self._writer.append_security_metadata(
                connection,
                security_id=security_id,
                source=adapter.source,
                observation=observation,
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

            if event.transfer_from_market is not None:
                explicit_transfers += 1
                matched_transfers += int(
                    self._matching_exit_exists(
                        connection,
                        security_code=event.security_code,
                        source="tpex",
                        market=event.transfer_from_market,
                        effective_on=event.effective_on,
                    )
                )

        coverage_start = parsed.coverage_start
        coverage_end = parsed.coverage_end
        return BusinessWriteResult(
            business_versions_created=created,
            business_versions_deduplicated=deduplicated,
            publication_evidence_created=evidence_created,
            publication_evidence_deduplicated=evidence_deduplicated,
            evidence_observations=len(parsed.rows),
            unknown_publication_observations=len(parsed.rows),
            normalized_rows=len(parsed.rows),
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            reconciliation={
                "requested_year": request.year,
                "event_kind": parsed.event_kind,
                "actual_market": parsed.market,
                "source_row_count": parsed.source_row_count,
                "actual_row_count": len(parsed.rows),
                "coverage_start": (
                    coverage_start.isoformat() if coverage_start else None
                ),
                "coverage_end": coverage_end.isoformat() if coverage_end else None,
                "coverage_validation": "source_event_range_only",
                "coverage_completeness": "not_evaluated",
                "coverage_gaps": None,
                "effective_time": "official_venue_event_date",
                "publication_time": "unknown",
                "system_time": "actual_import_time",
                "cross_source_write_semantics": "independent_source_histories",
                "explicit_transfer_event_count": explicit_transfers,
                "matched_cross_source_transfer_count": matched_transfers,
                "unmatched_cross_source_transfer_count": (
                    explicit_transfers - matched_transfers
                ),
                "transfer_match_rule": (
                    "TWSE note contains 櫃轉市 and TPEx has same-code same-date "
                    "official delisting state; reconciliation only"
                ),
                "source_fields": list(parsed.source_fields),
            },
        )

    @staticmethod
    def _matching_exit_exists(
        connection: Connection,
        *,
        security_code: str,
        source: str,
        market: str,
        effective_on,
    ) -> bool:
        return bool(
            connection.scalar(
                sa.select(
                    sa.exists()
                    .select_from(
                        security_metadata_versions.join(
                            security,
                            security.c.id == security_metadata_versions.c.security_id,
                        )
                    )
                    .where(
                        security.c.security_code == security_code,
                        security_metadata_versions.c.source == source,
                        security_metadata_versions.c.market == market,
                        security_metadata_versions.c.effective_from == effective_on,
                        security_metadata_versions.c.delisted_on == effective_on,
                    )
                )
            )
        )
