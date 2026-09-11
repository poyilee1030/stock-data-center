"""Exact dataset/source selection and PIT capability enforcement."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import dataset_sources
from stock_data_center.pit.errors import (
    AmbiguousSourceError,
    SourceNotFoundError,
    UnsupportedPITModeError,
)
from stock_data_center.pit.models import PITContext, SourcePolicy


class SourcePolicyResolver:
    def resolve(
        self,
        connection: Connection,
        dataset_code: str,
        context: PITContext,
        source: str | None = None,
    ) -> SourcePolicy:
        statement = sa.select(dataset_sources).where(
            dataset_sources.c.dataset_code == dataset_code
        )
        if source is not None:
            statement = statement.where(dataset_sources.c.source == source)
        else:
            statement = statement.where(dataset_sources.c.is_canonical.is_(True))

        rows = connection.execute(statement).mappings().all()
        if source is not None and not rows:
            raise SourceNotFoundError(
                f"source {source!r} is not configured for dataset {dataset_code!r}"
            )
        if source is None and not rows:
            source_count = connection.scalar(
                sa.select(sa.func.count()).select_from(dataset_sources).where(
                    dataset_sources.c.dataset_code == dataset_code
                )
            )
            if source_count == 0:
                raise SourceNotFoundError(
                    f"no source is configured for dataset {dataset_code!r}"
                )
            raise AmbiguousSourceError(
                f"dataset {dataset_code!r} requires an explicit source; "
                "no canonical source is configured"
            )

        row = rows[0]
        policy = SourcePolicy(
            dataset_code=row["dataset_code"],
            source=row["source"],
            supports_market_pit=row["supports_market_pit"],
            supports_system_pit=row["supports_system_pit"],
            publication_time_quality=row["publication_time_quality"],
            evidence_status=row["evidence_status"],
            is_canonical=row["is_canonical"],
        )
        if context.mode == "market" and (
            not policy.supports_market_pit or policy.evidence_status != "verified"
        ):
            raise UnsupportedPITModeError(
                f"{dataset_code}/{policy.source} does not support verified market PIT"
            )
        if context.mode == "system" and not policy.supports_system_pit:
            raise UnsupportedPITModeError(
                f"{dataset_code}/{policy.source} does not support system PIT"
            )
        return policy
