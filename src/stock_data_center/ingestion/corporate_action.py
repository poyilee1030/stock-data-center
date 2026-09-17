"""Corporate-action result-feed import path (ROADMAP Step 19-c, Invariant G(2)).

A range file lists executed events, and TWSE's dividend and reduction rows
publish their terms only on a per-event detail page (Step 19-b). Fetching
those details is `_capture_dependencies`'s job: it runs before the write
transaction opens, alongside the primary resource, so an HTTP round trip is
never made while a transaction is held open. Its result is the finished
`CorporateActionObservation` for every row, in row order — building it calls
the adapter the same way `_write_business` would have, so a detail that fails
to complete a row (`detail_required`, `invalid_identity`, an inconsistent
term) quarantines the whole range exactly like a parse failure would, keeping
its raw artifacts and writing no business row.

`_write_business` is then a pure write: register events, append versions and
their evidence, and retract whichever previously-registered event in this
range's covered window (Step 19-a's `executed_through` boundary) this run's
rows no longer name.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import date
from types import MappingProxyType
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

from stock_data_center.db import metadata
from stock_data_center.evidence import EvidencePolicyService
from stock_data_center.ingestion.adapters.corporate_action import (
    CorporateActionListAdapter,
    TWSEDividendDetailAdapter,
    TWSEExRightAdapter,
    TWSEReductionAdapter,
    TWSEReductionDetailAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    ArtifactOrigin,
    CorporateActionRangeRequest,
    EvidenceContext,
    IngestPurpose,
    ParsedCorporateActionList,
    SourceDataError,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_data import LineageRef, MarketDataWriter
from stock_data_center.market_reference.ingestion import MarketReferenceWriter
from stock_data_center.market_reference.models import (
    CorporateActionObservation,
    Phase8LineageRef,
    Phase8Publication,
)

# The only two list adapters whose rows publish their terms on a detail page
# (Step 19-b); every other feed's row is complete on its own.
_DETAIL_ADAPTERS: Mapping[str, object] = MappingProxyType(
    {
        TWSEExRightAdapter.source: TWSEDividendDetailAdapter(),
        TWSEReductionAdapter.source: TWSEReductionDetailAdapter(),
    }
)


class CorporateActionImporter(
    RawFirstImporter[CorporateActionRangeRequest, ParsedCorporateActionList]
):
    """Import one result feed's executed events over a requested range."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        security_writer: MarketDataWriter | None = None,
        writer: MarketReferenceWriter | None = None,
        policy: EvidencePolicyService | None = None,
        min_detail_interval_seconds: float = 1.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(engine, raw_store=raw_store, fetcher=fetcher)
        self._security_writer = security_writer or MarketDataWriter()
        self._writer = writer or MarketReferenceWriter()
        self._policy = policy or EvidencePolicyService()
        # TWT49U alone can need thousands of detail pages for one range
        # (ADR-0019: ~7,800 across 2020-2026). Throttled per real fetch, not
        # per row: a resumed detail makes no request, so waiting for it buys
        # the source nothing and costs a rerun dearly.
        self._min_detail_interval_seconds = min_detail_interval_seconds
        self._sleep = sleep

    def _source_scope(
        self,
        adapter: RawFirstAdapter[
            CorporateActionRangeRequest, ParsedCorporateActionList
        ],
        request: CorporateActionRangeRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
            "executed_through": request.executed_through.isoformat(),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(
        self,
        adapter: RawFirstAdapter[
            CorporateActionRangeRequest, ParsedCorporateActionList
        ],
    ) -> Mapping[str, object]:
        if not isinstance(adapter, CorporateActionListAdapter):
            raise TypeError(
                "CorporateActionImporter requires a CorporateActionListAdapter"
            )
        return {
            "market": adapter.market,
            "feed": adapter.feed,
            "event_identity": "feed, locator dates (ADR-0019)",
        }

    def _dataset_description(
        self,
        adapter: RawFirstAdapter[
            CorporateActionRangeRequest, ParsedCorporateActionList
        ],
    ) -> str:
        return "exchange result-feed corporate actions (Invariant G(2))"

    def _capture_dependencies(
        self,
        *,
        adapter: RawFirstAdapter[
            CorporateActionRangeRequest, ParsedCorporateActionList
        ],
        request: CorporateActionRangeRequest,
        parsed: ParsedCorporateActionList,
        import_id: UUID,
        purpose: IngestPurpose,
        artifact_origin: ArtifactOrigin,
    ) -> tuple[CorporateActionObservation, ...]:
        if not isinstance(adapter, CorporateActionListAdapter):
            raise TypeError(
                "CorporateActionImporter requires a CorporateActionListAdapter"
            )
        detail_adapter = _DETAIL_ADAPTERS.get(adapter.source)
        cache: dict[tuple[str, str], object] = {}
        observations: list[CorporateActionObservation] = []
        requested_source = False
        for row in parsed.rows:
            detail = None
            if row.detail_request is not None:
                if detail_adapter is None:
                    raise SourceDataError(
                        "detail_required",
                        f"{adapter.source} rows need a detail page but this "
                        "importer has no detail adapter for it",
                    )
                key = (row.security_code, row.detail_request.locator.source_event_key)
                if key not in cache:
                    if requested_source:
                        self._sleep(self._min_detail_interval_seconds)
                    parsed_detail, fetched = self._capture_and_parse(
                        detail_adapter,
                        row.detail_request,
                        import_id=import_id,
                        purpose=purpose,
                        artifact_origin=artifact_origin,
                    )
                    cache[key] = parsed_detail
                    requested_source = requested_source or fetched
                detail = cache[key]
            observations.append(adapter.observation(row, detail))
        return tuple(observations)

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter: RawFirstAdapter[
            CorporateActionRangeRequest, ParsedCorporateActionList
        ],
        request: CorporateActionRangeRequest,
        parsed: ParsedCorporateActionList,
        lineage: LineageRef,
        context: EvidenceContext,
        dependencies: object | None = None,
    ) -> BusinessWriteResult:
        if not isinstance(adapter, CorporateActionListAdapter):
            raise TypeError(
                "CorporateActionImporter requires a CorporateActionListAdapter"
            )
        if not isinstance(dependencies, tuple) or len(dependencies) != len(parsed.rows):
            raise RuntimeError(
                "corporate-action observations were not captured before the "
                "write transaction opened"
            )
        reference_lineage = Phase8LineageRef(lineage.raw_artifact_id, lineage.ingest_run_id)

        security_ids = self._security_writer.register_securities(
            connection, security_codes=[row.security_code for row in parsed.rows],
        )
        event_keys = [
            (security_ids[row.security_code], adapter.source, row.locator.source_event_key)
            for row in parsed.rows
        ]
        events = self._writer.register_corporate_action_events(
            connection, events=event_keys,
        )
        written = self._writer.append_corporate_actions(
            connection,
            source=adapter.source,
            observations=[
                (events[key], observation)
                for key, observation in zip(event_keys, dependencies, strict=True)
            ],
            lineage=reference_lineage,
        )
        created = sum(1 for item in written if item.created)

        bound = self._policy.bind(
            connection, dataset_code=adapter.dataset_code, source=adapter.source
        )
        plans = bound.plan_many(
            connection,
            # No source in Invariant G(2) declares a release rule (ADR-0019):
            # a result feed publishes whenever the exchange executes the
            # event, not on a fixed schedule. `period` is unused whenever
            # `bound.rule` is None, which every corporate-action source is.
            period=parsed.coverage_end,
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
        unknown = sum(
            1 for _, publication in planned if publication.published_at is None
        )

        present_event_ids = {events[key] for key in event_keys}
        candidates = _retraction_candidates(
            connection,
            source=adapter.source,
            window_start=parsed.coverage_start,
            window_end=parsed.coverage_end,
        )
        missing_event_ids = [
            event_id for event_id in candidates if event_id not in present_event_ids
        ]
        retracted = self._writer.retract_corporate_actions(
            connection,
            events=missing_event_ids,
            reason="absent_from_covered_range",
            lineage=reference_lineage,
        )

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
                "requested_date_start": request.start.isoformat(),
                "requested_date_end": request.end.isoformat(),
                "executed_through": request.executed_through.isoformat(),
                "actual_market": parsed.market,
                "event_count": len(parsed.rows),
                "not_yet_executed": parsed.not_yet_executed,
                "source_fields": list(parsed.source_fields),
                "coverage_start": parsed.coverage_start.isoformat(),
                "coverage_end": parsed.coverage_end.isoformat(),
                "coverage_validation": "not_evaluated",
                "coverage_gaps": None,
                "retraction_candidates": len(candidates),
                "retracted_count": len(retracted),
            },
        )


def _retraction_candidates(
    connection: Connection, *, source: str, window_start: date, window_end: date,
) -> dict[int, str]:
    """Registered events whose latest version's `ex_date` falls inside the
    covered window — candidates for retraction until this run's own rows
    prove otherwise. `ex_date` is every feed's event/executed date (ADR-0019),
    so it stands in for the executed date this source's events are keyed by.
    """
    events = metadata.tables["corporate_action_events"]
    versions = metadata.tables["corporate_action_versions"]
    latest = (
        sa.select(versions.c.event_id, versions.c.ex_date)
        .distinct(versions.c.event_id)
        .where(versions.c.source == source)
        .order_by(
            versions.c.event_id, versions.c.ingested_at.desc(), versions.c.id.desc()
        )
        .subquery()
    )
    rows = connection.execute(
        sa.select(events.c.id, events.c.source_event_key)
        .select_from(latest.join(events, events.c.id == latest.c.event_id))
        .where(latest.c.ex_date.between(window_start, window_end))
    ).all()
    return {event_id: key for event_id, key in rows}
