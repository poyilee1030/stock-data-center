"""Whole-market daily-price hooks for the shared raw-first import lifecycle."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import Connection, Engine

from stock_data_center.evidence import EvidencePolicyService
from stock_data_center.ingestion.adapters.whole_market_daily import (
    WholeMarketDailyAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    EvidenceContext,
    ParsedWholeMarketDaily,
    SourceResource,
    WholeMarketDailyRequest,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_data import (
    LineageRef,
    MarketDataWriter,
    PublicationObservation,
)


class WholeMarketDailyImporter(
    RawFirstImporter[WholeMarketDailyRequest, ParsedWholeMarketDaily]
):
    """Import one market's published quotes for one trade date."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        writer: MarketDataWriter | None = None,
        policy: EvidencePolicyService | None = None,
    ) -> None:
        super().__init__(engine, raw_store=raw_store, fetcher=fetcher)
        self._writer = writer or MarketDataWriter()
        self._policy = policy or EvidencePolicyService()

    def _source_scope(
        self,
        adapter: RawFirstAdapter[WholeMarketDailyRequest, ParsedWholeMarketDaily],
        request: WholeMarketDailyRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "trade_date": request.trade_date.isoformat(),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(
        self,
        adapter: RawFirstAdapter[WholeMarketDailyRequest, ParsedWholeMarketDaily],
    ) -> Mapping[str, object]:
        if not isinstance(adapter, WholeMarketDailyAdapter):
            raise TypeError(
                "WholeMarketDailyImporter requires a WholeMarketDailyAdapter"
            )
        disclosed = adapter.semantics.disclosed_volume_unit
        return {
            "market": adapter.market,
            "traded_quantity_unit": adapter.semantics.traded_quantity_unit.value,
            "trade_value_unit": adapter.semantics.trade_value_unit.value,
            "disclosed_volume_unit": disclosed.value if disclosed else None,
        }

    def _dataset_description(
        self,
        adapter: RawFirstAdapter[WholeMarketDailyRequest, ParsedWholeMarketDaily],
    ) -> str:
        return "official per-security daily market observations"

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter: RawFirstAdapter[WholeMarketDailyRequest, ParsedWholeMarketDaily],
        request: WholeMarketDailyRequest,
        parsed: ParsedWholeMarketDaily,
        lineage: LineageRef,
        context: EvidenceContext,
    ) -> BusinessWriteResult:
        if not isinstance(adapter, WholeMarketDailyAdapter):
            raise TypeError(
                "WholeMarketDailyImporter requires a WholeMarketDailyAdapter"
            )
        # A whole-market file is about 1,300 securities. Every step below is
        # set-based for that reason, and for that reason only: the identity,
        # revision and evidence rules are the per-row ones, unchanged.
        security_ids = self._writer.register_securities(
            connection,
            security_codes=[row.security_code for row in parsed.rows],
        )
        written = self._writer.append_daily_prices(
            connection,
            source=adapter.source,
            trade_date=parsed.trade_date,
            observations=[
                (security_ids[row.security_code], row.observation)
                for row in parsed.rows
            ],
            lineage=lineage,
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
                PublicationObservation(
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
                lineage=lineage,
            )
        )
        unknown = sum(
            1 for _, observation in planned if observation.published_at is None
        )
        # Every importer reports how availability time was decided. Saying
        # "unknown" here would be false since Step 15-c: these rows resolve by
        # the rule their source declared, and a manifest is an audit record.
        claimed = sorted({observation.evidence_type for _, observation in planned})

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
                "security_count": len({row.security_code for row in parsed.rows}),
                "header_variant": parsed.header_variant,
                "source_fields": list(parsed.source_fields),
                "coverage_start": parsed.trade_date.isoformat(),
                "coverage_end": parsed.trade_date.isoformat(),
                # One file is one trade date: whether the date should have been
                # open at all is the Step 16 calendar's question, answered by
                # the coverage report rather than guessed here.
                "coverage_validation": "not_evaluated",
                "coverage_gaps": None,
                "publication_time": (
                    bound.rule.evidence_source if bound.rule else "unknown"
                ),
                "availability_time_evidence": claimed,
                "source_units": {
                    "traded_quantity": (
                        adapter.semantics.traded_quantity_unit.value
                    ),
                    "trade_value": adapter.semantics.trade_value_unit.value,
                    "disclosed_volume": (
                        adapter.semantics.disclosed_volume_unit.value
                        if adapter.semantics.disclosed_volume_unit
                        else None
                    ),
                },
                "canonical_units": {
                    "volume": "share",
                    "trade_value": "twd",
                    "last_bid_volume": "share",
                    "last_ask_volume": "share",
                },
            },
        )
