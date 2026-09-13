"""Historical security listing/delisting hooks for the raw-first lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.postgresql import insert

from stock_data_center.db.metadata import (
    import_manifests,
    publication_evidence,
    security,
    security_metadata_versions,
    security_transfer_event_observations,
    security_transfer_events,
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


@dataclass(frozen=True, slots=True)
class SecurityTransferMatch:
    security_code: str
    effective_on: date
    from_source: str
    from_market: str
    to_source: str
    to_market: str
    source_term: str


@dataclass(frozen=True, slots=True)
class SecurityTransferReconciliation:
    reconciliation_status: str
    reconciliation_rule_version: str
    explicit_transfer_event_count: int
    matched_cross_source_transfer_count: int
    unmatched_cross_source_transfer_count: int
    pending_cross_source_transfer_count: int
    matched_events: tuple[SecurityTransferMatch, ...]
    unmatched_events: tuple[SecurityTransferMatch, ...]
    pending_events: tuple[SecurityTransferMatch, ...]


def reconcile_security_transfers(
    connection: Connection,
) -> SecurityTransferReconciliation:
    """Recompute final transfer truth from complete canonical source histories."""
    completed_delisting_scopes = {
        (source, int(year))
        for source, year in connection.execute(
            sa.select(
                import_manifests.c.source,
                import_manifests.c.source_scope["year"].astext,
            )
            .where(
                import_manifests.c.dataset_code == "security_metadata",
                import_manifests.c.status == "succeeded",
                import_manifests.c.source_scope["history_event"].astext
                == "delisting",
                import_manifests.c.source_scope["year"].astext.is_not(None),
            )
            .distinct()
        )
    }
    entry = security_metadata_versions.alias("transfer_entry")
    exit_state = security_metadata_versions.alias("transfer_exit")
    matching_exit = sa.exists().where(
        exit_state.c.security_id == entry.c.security_id,
        exit_state.c.source == security_transfer_events.c.from_source,
        exit_state.c.market == security_transfer_events.c.from_market,
        exit_state.c.effective_from == entry.c.effective_from,
        exit_state.c.delisted_on == entry.c.effective_from,
    )
    rows = connection.execute(
        sa.select(
            security.c.security_code,
            entry.c.effective_from,
            security_transfer_events.c.from_source,
            security_transfer_events.c.from_market,
            entry.c.source.label("to_source"),
            entry.c.market.label("to_market"),
            security_transfer_events.c.source_term,
            matching_exit.label("matched"),
        )
        .select_from(
            security_transfer_events.join(
                entry, entry.c.id == security_transfer_events.c.entry_version_id
            ).join(security, security.c.id == entry.c.security_id)
        )
        .order_by(
            security.c.security_code,
            entry.c.effective_from,
            security_transfer_events.c.id,
        )
    ).mappings()
    matched: list[SecurityTransferMatch] = []
    unmatched: list[SecurityTransferMatch] = []
    pending: list[SecurityTransferMatch] = []
    for row in rows:
        item = SecurityTransferMatch(
            security_code=row["security_code"],
            effective_on=row["effective_from"],
            from_source=row["from_source"],
            from_market=row["from_market"],
            to_source=row["to_source"],
            to_market=row["to_market"],
            source_term=row["source_term"],
        )
        if row["matched"]:
            matched.append(item)
        elif (item.from_source, item.effective_on.year) in completed_delisting_scopes:
            unmatched.append(item)
        else:
            pending.append(item)
    return SecurityTransferReconciliation(
        reconciliation_status="final" if not pending else "provisional",
        reconciliation_rule_version="security-transfer:v1",
        explicit_transfer_event_count=len(matched) + len(unmatched) + len(pending),
        matched_cross_source_transfer_count=len(matched),
        unmatched_cross_source_transfer_count=len(unmatched),
        pending_cross_source_transfer_count=len(pending),
        matched_events=tuple(matched),
        unmatched_events=tuple(unmatched),
        pending_events=tuple(pending),
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
                self._append_transfer_event(
                    connection,
                    entry_version_id=written.version_id,
                    from_source="tpex",
                    from_market=event.transfer_from_market,
                    source_term="櫃轉市",
                    lineage=lineage,
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
                "transfer_reconciliation_status": "provisional",
                "transfer_reconciliation_next_step": (
                    "run security-transfer-reconciliation after required TWSE "
                    "and TPEx histories are present"
                ),
                "transfer_match_rule": (
                    "TWSE note contains 櫃轉市 and TPEx has same-code same-date "
                    "official delisting state; final result is recomputed from "
                    "canonical histories"
                ),
                "source_fields": list(parsed.source_fields),
            },
        )

    @staticmethod
    def _append_transfer_event(
        connection: Connection,
        *,
        entry_version_id: int,
        from_source: str,
        from_market: str,
        source_term: str,
        lineage: LineageRef,
    ) -> None:
        event_id = connection.execute(
            insert(security_transfer_events)
            .values(
                entry_version_id=entry_version_id,
                from_source=from_source,
                from_market=from_market,
                source_term=source_term,
                recorded_at=sa.func.statement_timestamp(),
                raw_artifact_id=lineage.raw_artifact_id,
                ingest_run_id=lineage.ingest_run_id,
            )
            .on_conflict_do_nothing(constraint="uq_security_transfer_event")
            .returning(security_transfer_events.c.id)
        ).scalar_one_or_none()
        if event_id is None:
            event_id = connection.execute(
                sa.select(security_transfer_events.c.id).where(
                    security_transfer_events.c.entry_version_id == entry_version_id,
                    security_transfer_events.c.from_source == from_source,
                    security_transfer_events.c.from_market == from_market,
                )
            ).scalar_one()
        connection.execute(
            insert(security_transfer_event_observations)
            .values(
                security_transfer_event_id=event_id,
                raw_artifact_id=lineage.raw_artifact_id,
                ingest_run_id=lineage.ingest_run_id,
            )
            .on_conflict_do_nothing()
        )
