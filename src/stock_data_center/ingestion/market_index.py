"""Market-index hooks for the shared raw-first import lifecycle.

Two importers, because the two grains are different: the whole-list feeds
publish one file per trade date, and `MI_5MINS_HIST` publishes one calendar
month of OHLC for a single index.

`market_index_metadata_versions` is **not** written here. The database enforces
that a version's ingest run carries that version's own `dataset_code`, so index
metadata needs its own run, source policy and evidence — a second dataset's
worth of wiring for two columns audit §5 already records as derived values of
ours rather than the source's. Until a step declares it, the published name
lives in `market_index.index_code`, which is where identity reads it from
anyway.

TWSE's index sections live in the `MI_INDEX` file Step 17-c already stored, and
those bytes are fetched again here rather than reparsed. Reuse would need a
reprocess path the lifecycle does not have — checkpoints are scoped by
`import_id`, and the lineage foreign key requires every run to own an
observation row, so a reprocess run would have to record a fetch it did not
perform. Content addressing keeps the cost to the requests alone: the re-fetched
bytes hash to the artifact that is already stored, so no artifact is duplicated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import Connection, Engine

from stock_data_center.evidence import EvidencePolicyService
from stock_data_center.ingestion.adapters.market_index import (
    MarketIndexAdapter,
    TWSETaiexHistoryAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    EvidenceContext,
    MarketIndexRequest,
    ParsedMarketIndex,
    ParsedTaiexHistory,
    SourceResource,
    TaiexHistoryRequest,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_reference.ingestion import MarketReferenceWriter
from stock_data_center.market_reference.models import (
    Phase8LineageRef,
    Phase8Publication,
)


class _IndexImporterBase(RawFirstImporter):
    """Shared wiring for the two index grains."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        writer: MarketReferenceWriter | None = None,
        policy: EvidencePolicyService | None = None,
    ) -> None:
        super().__init__(engine, raw_store=raw_store, fetcher=fetcher)
        self._writer = writer or MarketReferenceWriter()
        self._policy = policy or EvidencePolicyService()

    def _dataset_description(self, adapter) -> str:
        return "official market index closes and levels"

    def _write_evidence(
        self,
        connection: Connection,
        *,
        adapter,
        period,
        context: EvidenceContext,
        written: Sequence,
        lineage: Phase8LineageRef,
    ) -> tuple[int, int, int, int]:
        """Plan and write one period's evidence; returns the four counts."""
        bound = self._policy.bind(
            connection, dataset_code=adapter.dataset_code, source=adapter.source
        )
        plans = bound.plan_many(
            connection,
            period=period,
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
        created, deduplicated = self._writer.append_publication_evidence_batch(
            connection,
            dataset_code=adapter.dataset_code,
            source=adapter.source,
            planned=planned,
            lineage=lineage,
        )
        unknown = sum(
            1 for _, publication in planned if publication.published_at is None
        )
        return created, deduplicated, len(planned), unknown


class MarketIndexImporter(_IndexImporterBase):
    """Import one market's published index closes for one trade date."""

    def _source_scope(
        self,
        adapter: RawFirstAdapter[MarketIndexRequest, ParsedMarketIndex],
        request: MarketIndexRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "trade_date": request.trade_date.isoformat(),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(self, adapter) -> Mapping[str, object]:
        if not isinstance(adapter, MarketIndexAdapter):
            raise TypeError("MarketIndexImporter requires a MarketIndexAdapter")
        return {
            "market": adapter.market,
            "index_identity": "source, section, published name",
            "index_level_unit": "index_points",
            "ohlc": "not published by a whole-list index source",
        }

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter,
        request: MarketIndexRequest,
        parsed: ParsedMarketIndex,
        lineage,
        context: EvidenceContext,
        dependencies: object | None = None,
    ) -> BusinessWriteResult:
        reference_lineage = Phase8LineageRef(
            lineage.raw_artifact_id, lineage.ingest_run_id
        )
        index_ids = self._writer.register_indices(
            connection,
            index_codes=[row.index_code(adapter.source) for row in parsed.rows],
        )
        written = self._writer.append_indices(
            connection,
            source=adapter.source,
            observations=[
                (index_ids[row.index_code(adapter.source)], row.observation)
                for row in parsed.rows
            ],
            lineage=reference_lineage,
        )
        created = sum(1 for item in written if item.created)
        evidence = self._write_evidence(
            connection,
            adapter=adapter,
            period=parsed.trade_date,
            context=context,
            written=written,
            lineage=reference_lineage,
        )
        return BusinessWriteResult(
            business_versions_created=created,
            business_versions_deduplicated=len(written) - created,
            publication_evidence_created=evidence[0],
            publication_evidence_deduplicated=evidence[1],
            evidence_observations=evidence[2],
            unknown_publication_observations=evidence[3],
            normalized_rows=len(parsed.rows),
            coverage_start=parsed.coverage_start,
            coverage_end=parsed.coverage_end,
            reconciliation={
                "requested_date_start": request.trade_date.isoformat(),
                "requested_date_end": request.trade_date.isoformat(),
                "actual_market": parsed.market,
                "index_count": len(parsed.rows),
                "section_count": parsed.section_count,
                "source_fields": list(parsed.source_fields),
                "coverage_start": parsed.trade_date.isoformat(),
                "coverage_end": parsed.trade_date.isoformat(),
                "coverage_validation": "not_evaluated",
                "coverage_gaps": None,
                "index_identity": "source, section, published name",
                "ohlc": "not published by this source",
            },
        )


class TaiexHistoryImporter(_IndexImporterBase):
    """Import one calendar month of TAIEX open/high/low/close."""

    def _source_scope(
        self,
        adapter: RawFirstAdapter[TaiexHistoryRequest, ParsedTaiexHistory],
        request: TaiexHistoryRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "month": request.month.isoformat(),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(self, adapter) -> Mapping[str, object]:
        if not isinstance(adapter, TWSETaiexHistoryAdapter):
            raise TypeError("TaiexHistoryImporter requires the TAIEX adapter")
        return {
            "market": adapter.market,
            "index_name": adapter.index_name,
            "index_level_unit": "index_points",
            "change_columns": "not published by this source",
        }

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter,
        request: TaiexHistoryRequest,
        parsed: ParsedTaiexHistory,
        lineage,
        context: EvidenceContext,
        dependencies: object | None = None,
    ) -> BusinessWriteResult:
        reference_lineage = Phase8LineageRef(
            lineage.raw_artifact_id, lineage.ingest_run_id
        )
        # One index, so one identity for the whole month. Its section is the
        # endpoint itself: this feed publishes no section label, and its series
        # is a price index.
        index_code = f"{adapter.source}:指數:{adapter.index_name}"
        index_id = self._writer.register_indices(
            connection, index_codes=[index_code]
        )[index_code]
        written = self._writer.append_indices(
            connection,
            source=adapter.source,
            observations=[(index_id, row.observation) for row in parsed.rows],
            lineage=reference_lineage,
        )
        created = sum(1 for item in written if item.created)
        # Each row is its own trade date, so evidence is planned per date
        # rather than once for the month: the release rule resolves a trade
        # date, not a file.
        evidence_created = evidence_dedup = evidence_rows = evidence_unknown = 0
        by_date: dict = {}
        for row, item in zip(parsed.rows, written, strict=True):
            by_date.setdefault(row.trade_date, []).append(item)
        for trade_date, items in by_date.items():
            counts = self._write_evidence(
                connection,
                adapter=adapter,
                period=trade_date,
                context=context,
                written=items,
                lineage=reference_lineage,
            )
            evidence_created += counts[0]
            evidence_dedup += counts[1]
            evidence_rows += counts[2]
            evidence_unknown += counts[3]
        return BusinessWriteResult(
            business_versions_created=created,
            business_versions_deduplicated=len(written) - created,
            publication_evidence_created=evidence_created,
            publication_evidence_deduplicated=evidence_dedup,
            evidence_observations=evidence_rows,
            unknown_publication_observations=evidence_unknown,
            normalized_rows=len(parsed.rows),
            coverage_start=parsed.coverage_start,
            coverage_end=parsed.coverage_end,
            reconciliation={
                "requested_month": request.month.isoformat(),
                "actual_market": parsed.market,
                "index_name": parsed.index_name,
                "trade_day_count": len(parsed.rows),
                "coverage_start": parsed.coverage_start.isoformat(),
                "coverage_end": parsed.coverage_end.isoformat(),
                "coverage_validation": "not_evaluated",
                "coverage_gaps": None,
                "source_fields": list(parsed.source_fields),
            },
        )
