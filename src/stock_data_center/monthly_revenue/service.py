"""Dataset-specific PIT queries for monthly revenue."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import monthly_revenue_versions, security
from stock_data_center.monthly_revenue.models import RevenuePeriod
from stock_data_center.pit import PITResolver, ResolvedRecord
from stock_data_center.pit.models import PITContext
from stock_data_center.pit.source_policy import SourcePolicyResolver


class MonthlyRevenueService:
    def __init__(
        self,
        *,
        resolver: PITResolver | None = None,
        source_policy: SourcePolicyResolver | None = None,
    ) -> None:
        self._source_policy = source_policy or SourcePolicyResolver()
        self._resolver = resolver or PITResolver(source_policy=self._source_policy)

    def revenue(
        self,
        connection: Connection,
        *,
        security_code: str,
        period: RevenuePeriod,
        context: PITContext,
        source: str | None = None,
    ) -> ResolvedRecord | None:
        security_id = self._security_id(connection, security_code)
        if security_id is None:
            return None
        return self._resolver.resolve(
            connection,
            dataset_code="monthly_revenue",
            logical_key={
                "security_id": security_id,
                "revenue_year": period.year,
                "revenue_month": period.month,
            },
            context=context,
            source=source,
        )

    def history(
        self,
        connection: Connection,
        *,
        security_code: str,
        start_period: RevenuePeriod,
        end_period: RevenuePeriod,
        context: PITContext,
        source: str | None = None,
    ) -> tuple[ResolvedRecord, ...]:
        if start_period > end_period:
            raise ValueError("start_period must not follow end_period")
        security_id = self._security_id(connection, security_code)
        if security_id is None:
            return ()
        policy = self._source_policy.resolve(
            connection, "monthly_revenue", context, source
        )
        ordinal = (
            monthly_revenue_versions.c.revenue_year * 12
            + monthly_revenue_versions.c.revenue_month
        )
        start_ordinal = start_period.year * 12 + start_period.month
        end_ordinal = end_period.year * 12 + end_period.month
        periods = connection.execute(
            sa.select(
                monthly_revenue_versions.c.revenue_year,
                monthly_revenue_versions.c.revenue_month,
            )
            .where(
                monthly_revenue_versions.c.security_id == security_id,
                monthly_revenue_versions.c.source == policy.source,
                ordinal.between(start_ordinal, end_ordinal),
            )
            .distinct()
            .order_by(
                monthly_revenue_versions.c.revenue_year,
                monthly_revenue_versions.c.revenue_month,
            )
        )
        records = []
        for year, month in periods:
            record = self._resolver.resolve(
                connection,
                dataset_code="monthly_revenue",
                logical_key={
                    "security_id": security_id,
                    "revenue_year": year,
                    "revenue_month": month,
                },
                context=context,
                source=policy.source,
            )
            if record is not None:
                records.append(record)
        return tuple(records)

    @staticmethod
    def _security_id(connection: Connection, security_code: str) -> int | None:
        return connection.scalar(
            sa.select(security.c.id).where(
                security.c.security_code == security_code
            )
        )
