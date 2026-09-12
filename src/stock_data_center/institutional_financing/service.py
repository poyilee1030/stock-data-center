"""Dataset-specific PIT reads for Phase 7 observed source data."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import (
    ingest_runs,
    raw_artifact_observations,
    raw_artifacts,
    security,
)
from stock_data_center.institutional_financing.ingestion import _SPECS
from stock_data_center.institutional_financing.models import SourceLineageObservation
from stock_data_center.pit import PITResolver, ResolvedRecord
from stock_data_center.pit.models import PITContext
from stock_data_center.pit.source_policy import SourcePolicyResolver


class InstitutionalFinancingService:
    def __init__(
        self,
        *,
        resolver: PITResolver | None = None,
        source_policy: SourcePolicyResolver | None = None,
    ) -> None:
        self._source_policy = source_policy or SourcePolicyResolver()
        self._resolver = resolver or PITResolver(source_policy=self._source_policy)

    def institutional_investor(
        self,
        connection: Connection,
        *,
        security_code: str,
        trade_date: date,
        context: PITContext,
        source: str | None = None,
    ) -> ResolvedRecord | None:
        return self._security_record(
            connection,
            "institutional_investor",
            security_code=security_code,
            trade_date=trade_date,
            context=context,
            source=source,
        )

    def foreign_holding(
        self,
        connection: Connection,
        *,
        security_code: str,
        trade_date: date,
        context: PITContext,
        source: str | None = None,
    ) -> ResolvedRecord | None:
        return self._security_record(
            connection,
            "foreign_holding",
            security_code=security_code,
            trade_date=trade_date,
            context=context,
            source=source,
        )

    def margin_trading(
        self,
        connection: Connection,
        *,
        security_code: str,
        trade_date: date,
        context: PITContext,
        source: str | None = None,
    ) -> ResolvedRecord | None:
        return self._security_record(
            connection,
            "margin_trading",
            security_code=security_code,
            trade_date=trade_date,
            context=context,
            source=source,
        )

    def securities_lending(
        self,
        connection: Connection,
        *,
        security_code: str,
        trade_date: date,
        context: PITContext,
        source: str | None = None,
    ) -> ResolvedRecord | None:
        return self._security_record(
            connection,
            "securities_lending",
            security_code=security_code,
            trade_date=trade_date,
            context=context,
            source=source,
        )

    def market_summary(
        self,
        connection: Connection,
        *,
        market: str,
        trade_date: date,
        institution: str,
        context: PITContext,
        source: str | None = None,
    ) -> ResolvedRecord | None:
        return self._resolver.resolve(
            connection,
            dataset_code="institutional_market_summary",
            logical_key={
                "market": market,
                "trade_date": trade_date,
                "institution": institution,
            },
            context=context,
            source=source,
        )

    def history(
        self,
        connection: Connection,
        *,
        dataset_code: str,
        start_date: date,
        end_date: date,
        context: PITContext,
        source: str | None = None,
        security_code: str | None = None,
        market: str | None = None,
        institution: str | None = None,
    ) -> tuple[ResolvedRecord, ...]:
        if start_date > end_date:
            raise ValueError("start_date must not follow end_date")
        if dataset_code not in _SPECS:
            raise ValueError(f"unsupported Phase 7 dataset {dataset_code!r}")
        if dataset_code == "institutional_market_summary":
            if not market or not institution or security_code is not None:
                raise ValueError("market summary history requires market and institution")
            logical_identity = {"market": market, "institution": institution}
        else:
            if not security_code or market is not None or institution is not None:
                raise ValueError("security history requires security_code only")
            security_id = self._security_id(connection, security_code)
            if security_id is None:
                return ()
            logical_identity = {"security_id": security_id}
        policy = self._source_policy.resolve(connection, dataset_code, context, source)
        table = _SPECS[dataset_code].table
        statement = sa.select(table.c.trade_date).where(
            table.c.source == policy.source,
            table.c.trade_date.between(start_date, end_date),
        )
        for name, value in logical_identity.items():
            statement = statement.where(table.c[name] == value)
        dates = connection.scalars(statement.distinct().order_by(table.c.trade_date))
        records = []
        for trade_date in dates:
            record = self._resolver.resolve(
                connection,
                dataset_code=dataset_code,
                logical_key={**logical_identity, "trade_date": trade_date},
                context=context,
                source=policy.source,
            )
            if record is not None:
                records.append(record)
        return tuple(records)

    def observations(
        self,
        connection: Connection,
        *,
        dataset_code: str,
        version_id: int,
    ) -> tuple[SourceLineageObservation, ...]:
        try:
            spec = _SPECS[dataset_code]
        except KeyError as error:
            raise ValueError(f"unsupported Phase 7 dataset {dataset_code!r}") from error
        link = spec.observation_table
        rows = connection.execute(
            sa.select(
                link.c.raw_artifact_id,
                raw_artifacts.c.raw_artifact_hash,
                raw_artifacts.c.storage_uri,
                link.c.ingest_run_id,
                ingest_runs.c.status,
                raw_artifact_observations.c.source_uri,
                raw_artifact_observations.c.fetched_at,
            )
            .select_from(
                link.join(
                    raw_artifact_observations,
                    sa.and_(
                        raw_artifact_observations.c.raw_artifact_id
                        == link.c.raw_artifact_id,
                        raw_artifact_observations.c.ingest_run_id
                        == link.c.ingest_run_id,
                    ),
                )
                .join(raw_artifacts, raw_artifacts.c.id == link.c.raw_artifact_id)
                .join(ingest_runs, ingest_runs.c.id == link.c.ingest_run_id)
            )
            .where(link.c[spec.version_column] == version_id)
            .order_by(raw_artifact_observations.c.fetched_at, link.c.ingest_run_id)
        ).mappings()
        return tuple(
            SourceLineageObservation(
                raw_artifact_id=row["raw_artifact_id"],
                raw_artifact_hash=row["raw_artifact_hash"],
                raw_artifact_uri=row["storage_uri"],
                ingest_run_id=row["ingest_run_id"],
                ingest_run_status=row["status"],
                source_uri=row["source_uri"],
                fetched_at=row["fetched_at"],
            )
            for row in rows
        )

    def _security_record(
        self,
        connection: Connection,
        dataset_code: str,
        *,
        security_code: str,
        trade_date: date,
        context: PITContext,
        source: str | None = None,
    ) -> ResolvedRecord | None:
        security_id = self._security_id(connection, security_code)
        if security_id is None:
            return None
        return self._resolver.resolve(
            connection,
            dataset_code=dataset_code,
            logical_key={"security_id": security_id, "trade_date": trade_date},
            context=context,
            source=source,
        )

    @staticmethod
    def _security_id(connection: Connection, security_code: str) -> int | None:
        return connection.scalar(
            sa.select(security.c.id).where(security.c.security_code == security_code)
        )
