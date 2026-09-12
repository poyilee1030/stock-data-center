"""PIT-safe Phase 8 dataset queries."""

from __future__ import annotations

from datetime import date
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db import metadata
from stock_data_center.db.metadata import security
from stock_data_center.market_reference.ingestion import SPECS
from stock_data_center.market_reference.models import Phase8LineageObservation
from stock_data_center.pit import PITResolver, ResolvedRecord
from stock_data_center.pit.models import PITContext
from stock_data_center.pit.source_policy import SourcePolicyResolver


class MarketReferenceService:
    def __init__(self, *, resolver: PITResolver | None = None,
                 source_policy: SourcePolicyResolver | None = None) -> None:
        self._policy = source_policy or SourcePolicyResolver()
        self._resolver = resolver or PITResolver(source_policy=self._policy)

    def index(self, connection: Connection, *, index_code: str, trade_date: date,
              context: PITContext, source: str | None = None) -> ResolvedRecord | None:
        index_id = connection.scalar(sa.select(metadata.tables["market_index"].c.id).where(
            metadata.tables["market_index"].c.index_code == index_code))
        if index_id is None:
            return None
        return self._resolver.resolve(connection, dataset_code="market_index",
            logical_key={"market_index_id": index_id, "trade_date": trade_date},
            context=context, source=source)

    def corporate_action(self, connection: Connection, *, security_code: str,
                         action_type: str, ex_date: date, context: PITContext,
                         source: str | None = None) -> ResolvedRecord | None:
        security_id = self._security_id(connection, security_code)
        if security_id is None:
            return None
        return self._resolver.resolve(connection, dataset_code="corporate_action",
            logical_key={"security_id": security_id, "action_type": action_type, "ex_date": ex_date},
            context=context, source=source)

    def official_valuation(self, connection: Connection, *, security_code: str,
                           trade_date: date, context: PITContext,
                           source: str | None = None) -> ResolvedRecord | None:
        security_id = self._security_id(connection, security_code)
        if security_id is None:
            return None
        return self._resolver.resolve(connection, dataset_code="official_valuation",
            logical_key={"security_id": security_id, "trade_date": trade_date},
            context=context, source=source)

    def history(self, connection: Connection, *, dataset_code: str, start_date: date,
                end_date: date, context: PITContext, source: str | None = None,
                index_code: str | None = None, security_code: str | None = None,
                action_type: str | None = None) -> tuple[ResolvedRecord, ...]:
        if start_date > end_date:
            raise ValueError("start_date must not follow end_date")
        if dataset_code == "market_index":
            index_id = connection.scalar(sa.select(metadata.tables["market_index"].c.id).where(
                metadata.tables["market_index"].c.index_code == index_code))
            if index_id is None:
                return ()
            identity = {"market_index_id": index_id}
        else:
            if dataset_code not in {"corporate_action", "official_valuation"}:
                raise ValueError(f"unsupported Phase 8 dataset {dataset_code!r}")
            security_id = self._security_id(connection, security_code or "")
            if security_id is None:
                return ()
            identity = {"security_id": security_id}
            if dataset_code == "corporate_action":
                if not action_type:
                    raise ValueError("corporate action history requires action_type")
                identity["action_type"] = action_type
        policy = self._policy.resolve(connection, dataset_code, context, source)
        table = SPECS[dataset_code].table
        date_column = table.c.ex_date if dataset_code == "corporate_action" else table.c.trade_date
        statement = sa.select(date_column).where(table.c.source == policy.source,
                                                  date_column.between(start_date, end_date))
        for name, value in identity.items():
            statement = statement.where(table.c[name] == value)
        records = []
        for observed_on in connection.scalars(statement.distinct().order_by(date_column)):
            key = {**identity, date_column.name: observed_on}
            record = self._resolver.resolve(connection, dataset_code=dataset_code,
                                             logical_key=key, context=context, source=policy.source)
            if record is not None:
                records.append(record)
        return tuple(records)

    def observations(self, connection: Connection, *, dataset_code: str,
                     version_id: int) -> tuple[Phase8LineageObservation, ...]:
        """Return every immutable raw/run observation attached to a revision."""
        if dataset_code not in SPECS:
            raise ValueError(f"unsupported Phase 8 dataset {dataset_code!r}")
        spec = SPECS[dataset_code]
        raw_artifacts = metadata.tables["raw_artifacts"]
        ingest_runs = metadata.tables["ingest_runs"]
        raw_observations = metadata.tables["raw_artifact_observations"]
        statement = (
            sa.select(
                spec.link.c.raw_artifact_id,
                spec.link.c.ingest_run_id,
                raw_artifacts.c.raw_artifact_hash,
                raw_artifacts.c.storage_uri,
                ingest_runs.c.status,
                raw_observations.c.source_uri,
                raw_observations.c.fetched_at,
            )
            .select_from(
                spec.link.join(
                    raw_observations,
                    sa.and_(
                        spec.link.c.raw_artifact_id == raw_observations.c.raw_artifact_id,
                        spec.link.c.ingest_run_id == raw_observations.c.ingest_run_id,
                    ),
                ).join(
                    raw_artifacts, spec.link.c.raw_artifact_id == raw_artifacts.c.id,
                ).join(
                    ingest_runs,
                    spec.link.c.ingest_run_id == ingest_runs.c.id,
                )
            )
            .where(spec.link.c[spec.target] == version_id)
            .order_by(raw_observations.c.fetched_at, spec.link.c.ingest_run_id)
        )
        return tuple(
            Phase8LineageObservation(
                raw_artifact_id=row["raw_artifact_id"],
                raw_artifact_hash=row["raw_artifact_hash"],
                raw_artifact_uri=row["storage_uri"],
                ingest_run_id=row["ingest_run_id"],
                ingest_run_status=row["status"],
                source_uri=row["source_uri"],
                fetched_at=row["fetched_at"],
            )
            for row in connection.execute(statement).mappings()
        )

    @staticmethod
    def _security_id(connection: Connection, code: str) -> int | None:
        return connection.scalar(sa.select(security.c.id).where(security.c.security_code == code))
