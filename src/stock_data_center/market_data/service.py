"""Dataset-specific PIT queries for security metadata and daily prices."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import (
    daily_price_versions,
    security,
    security_metadata_versions,
)
from stock_data_center.market_data.models import SecurityState
from stock_data_center.pit import PITResolver, ResolvedRecord
from stock_data_center.pit.models import PITContext
from stock_data_center.pit.source_policy import SourcePolicyResolver


class InvalidDateRangeError(ValueError):
    """Raised when the beginning of a requested history follows its end."""


class MarketDataService:
    """Resolve dataset concepts without exposing storage details to callers."""

    def __init__(
        self,
        *,
        resolver: PITResolver | None = None,
        source_policy: SourcePolicyResolver | None = None,
    ) -> None:
        self._source_policy = source_policy or SourcePolicyResolver()
        self._resolver = resolver or PITResolver(source_policy=self._source_policy)

    def security_state(
        self,
        connection: Connection,
        *,
        security_code: str,
        effective_on: date,
        context: PITContext,
        source: str | None = None,
    ) -> SecurityState | None:
        identity = self._identity(connection, security_code)
        if identity is None:
            return None
        policy = self._source_policy.resolve(
            connection, "security_metadata", context, source
        )
        return self._security_state_for_identity(
            connection,
            identity=identity,
            effective_on=effective_on,
            context=context,
            source=policy.source,
        )

    def security_universe(
        self,
        connection: Connection,
        *,
        effective_on: date,
        context: PITContext,
        source: str | None = None,
        market: str | None = None,
        listed_only: bool = True,
    ) -> tuple[SecurityState, ...]:
        """Return the PIT-visible universe, optionally limited to listed names."""
        policy = self._source_policy.resolve(
            connection, "security_metadata", context, source
        )
        statement = sa.select(
            security.c.id,
            security.c.security_code,
        ).where(
            sa.exists(
                sa.select(1).where(
                    security_metadata_versions.c.security_id == security.c.id,
                    security_metadata_versions.c.source == policy.source,
                    security_metadata_versions.c.effective_from <= effective_on,
                )
            )
        )
        states = []
        for identity in connection.execute(statement).mappings():
            state = self._security_state_for_identity(
                connection,
                identity=identity,
                effective_on=effective_on,
                context=context,
                source=policy.source,
            )
            if (
                state is not None
                and (market is None or state.market == market)
                and (state.is_listed or not listed_only)
            ):
                states.append(state)
        return tuple(sorted(states, key=lambda item: item.security_code))

    def daily_price(
        self,
        connection: Connection,
        *,
        security_code: str,
        trade_date: date,
        context: PITContext,
        source: str | None = None,
    ) -> ResolvedRecord | None:
        identity = self._identity(connection, security_code)
        if identity is None:
            return None
        return self._resolver.resolve(
            connection,
            dataset_code="daily_price",
            logical_key={
                "security_id": identity["id"],
                "trade_date": trade_date,
            },
            context=context,
            source=source,
        )

    def daily_price_history(
        self,
        connection: Connection,
        *,
        security_code: str,
        start_date: date,
        end_date: date,
        context: PITContext,
        source: str | None = None,
    ) -> tuple[ResolvedRecord, ...]:
        if start_date > end_date:
            raise InvalidDateRangeError("start_date must not follow end_date")
        identity = self._identity(connection, security_code)
        if identity is None:
            return ()
        policy = self._source_policy.resolve(
            connection, "daily_price", context, source
        )
        trade_dates = connection.scalars(
            sa.select(daily_price_versions.c.trade_date)
            .where(
                daily_price_versions.c.security_id == identity["id"],
                daily_price_versions.c.source == policy.source,
                daily_price_versions.c.trade_date.between(start_date, end_date),
            )
            .distinct()
            .order_by(daily_price_versions.c.trade_date)
        )
        records = []
        for trade_date in trade_dates:
            record = self._resolver.resolve(
                connection,
                dataset_code="daily_price",
                logical_key={
                    "security_id": identity["id"],
                    "trade_date": trade_date,
                },
                context=context,
                source=policy.source,
            )
            if record is not None:
                records.append(record)
        return tuple(records)

    @staticmethod
    def _identity(connection: Connection, security_code: str):
        return connection.execute(
            sa.select(
                security.c.id,
                security.c.security_code,
            ).where(security.c.security_code == security_code)
        ).mappings().one_or_none()

    def _security_state_for_identity(
        self,
        connection: Connection,
        *,
        identity,
        effective_on: date,
        context: PITContext,
        source: str,
    ) -> SecurityState | None:
        effective_dates = connection.scalars(
            sa.select(security_metadata_versions.c.effective_from)
            .where(
                security_metadata_versions.c.security_id == identity["id"],
                security_metadata_versions.c.source == source,
                security_metadata_versions.c.effective_from <= effective_on,
            )
            .distinct()
            .order_by(security_metadata_versions.c.effective_from.desc())
        )
        for effective_from in effective_dates:
            record = self._resolver.resolve(
                connection,
                dataset_code="security_metadata",
                logical_key={
                    "security_id": identity["id"],
                    "effective_from": effective_from,
                },
                context=context,
                source=source,
            )
            if record is None:
                continue
            effective_to = record.data["effective_to"]
            if effective_to is not None and effective_on > effective_to:
                return None
            return SecurityState(
                security_id=identity["id"],
                security_code=identity["security_code"],
                effective_on=effective_on,
                record=record,
            )
        return None
