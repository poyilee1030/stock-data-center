"""Daily-market domain hooks for the shared Phase 9 import lifecycle."""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from datetime import date

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

from stock_data_center.db.metadata import publication_evidence
from stock_data_center.evidence import EvidencePolicyService
from stock_data_center.ingestion.adapters import DailyMarketAdapter
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    EvidenceContext,
    DailyMarketRequest,
    ParsedDailyMarket,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_data import (
    LineageRef,
    MarketDataWriter,
    PublicationObservation,
)


class DailyMarketImporter(RawFirstImporter[DailyMarketRequest, ParsedDailyMarket]):
    """Import official per-security daily observations through shared state."""

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
        adapter: RawFirstAdapter[DailyMarketRequest, ParsedDailyMarket],
        request: DailyMarketRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "security_code": request.security_code,
            "month": request.month.isoformat(),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(
        self,
        adapter: RawFirstAdapter[DailyMarketRequest, ParsedDailyMarket],
    ) -> Mapping[str, object]:
        if not isinstance(adapter, DailyMarketAdapter):
            raise TypeError("DailyMarketImporter requires a DailyMarketAdapter")
        return {
            "traded_quantity_unit": adapter.semantics.traded_quantity_unit.value,
            "trade_value_unit": adapter.semantics.trade_value_unit.value,
        }

    def _dataset_description(
        self,
        adapter: RawFirstAdapter[DailyMarketRequest, ParsedDailyMarket],
    ) -> str:
        return "official per-security daily market observations"

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter: RawFirstAdapter[DailyMarketRequest, ParsedDailyMarket],
        request: DailyMarketRequest,
        parsed: ParsedDailyMarket,
        lineage: LineageRef,
        context: EvidenceContext,
    ) -> BusinessWriteResult:
        security_id = self._writer.register_security(
            connection, security_code=parsed.security_code
        )
        created = 0
        deduplicated = 0
        evidence_created = 0
        evidence_deduplicated = 0
        evidence_observations = 0
        unknown_observations = 0
        for observation in parsed.rows:
            written = self._writer.append_daily_price(
                connection,
                security_id=security_id,
                source=adapter.source,
                observation=observation,
                lineage=lineage,
            )
            created += int(written.created)
            deduplicated += int(not written.created)

            # What this version may claim follows from the run that produced it
            # and the rule its source declared, never from a hard-coded type.
            for planned in self._policy.plan(
                connection,
                dataset_code="daily_price",
                source=adapter.source,
                period=observation.trade_date,
                purpose=context.purpose,
                version_created=written.created,
                captured_at=context.captured_at,
            ):
                evidence_existed = connection.scalar(
                    sa.select(
                        sa.exists().where(
                            publication_evidence.c.dataset_code
                            == adapter.dataset_code,
                            publication_evidence.c.source == adapter.source,
                            publication_evidence.c.daily_price_version_id
                            == written.version_id,
                            publication_evidence.c.evidence_type
                            == planned.evidence_type,
                            publication_evidence.c.published_at.is_not_distinct_from(
                                planned.published_at
                            ),
                        )
                    )
                )
                self._writer.append_publication_evidence(
                    connection,
                    dataset_code="daily_price",
                    source=adapter.source,
                    version_id=written.version_id,
                    observation=PublicationObservation(
                        evidence_kind=planned.evidence_kind,
                        published_at=planned.published_at,
                        evidence_source=planned.evidence_source,
                        evidence_type=planned.evidence_type,
                        quality_rank=planned.quality_rank,
                    ),
                    lineage=lineage,
                )
                evidence_created += int(not evidence_existed)
                evidence_deduplicated += int(evidence_existed)
                evidence_observations += 1
                unknown_observations += int(planned.published_at is None)

        return BusinessWriteResult(
            business_versions_created=created,
            business_versions_deduplicated=deduplicated,
            publication_evidence_created=evidence_created,
            publication_evidence_deduplicated=evidence_deduplicated,
            evidence_observations=evidence_observations,
            unknown_publication_observations=unknown_observations,
            normalized_rows=len(parsed.rows),
            coverage_start=parsed.coverage_start,
            coverage_end=parsed.coverage_end,
            reconciliation={
                "requested_date_start": request.month.isoformat(),
                "requested_date_end": date(
                    request.month.year,
                    request.month.month,
                    calendar.monthrange(request.month.year, request.month.month)[1],
                ).isoformat(),
                "requested_security": request.security_code,
                "actual_security": parsed.security_code,
                "actual_security_name": parsed.security_name,
                "actual_row_count": len(parsed.rows),
                "coverage_start": (
                    parsed.coverage_start.isoformat() if parsed.coverage_start else None
                ),
                "coverage_end": (
                    parsed.coverage_end.isoformat() if parsed.coverage_end else None
                ),
                "coverage_validation": "not_evaluated",
                "coverage_gaps": None,
                "source_units": {
                    "traded_quantity": adapter.semantics.traded_quantity_unit.value,
                    "trade_value": adapter.semantics.trade_value_unit.value,
                },
                "canonical_units": {
                    "volume": "share",
                    "trade_value": "twd",
                },
                "publication_time": "unknown",
            },
        )
