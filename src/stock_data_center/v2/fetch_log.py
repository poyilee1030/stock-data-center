"""Record one fetch: keep the raw bytes, then log the attempt in `fetches`.

Raw-first (CLAUDE.md §71): the bytes are stored content-addressed before anything
parses them, and the fetch row carries their SHA-256 and size so the file can be
found again at data/raw/<ab>/<hex>.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.schema_v2 import fetches
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore


@dataclass(frozen=True, slots=True)
class FetchRecord:
    dataset: str
    source: str
    resource_key: str
    source_uri: str | None
    purpose: str
    adapter_version: str
    git_commit: str
    fetched_at: datetime


def record_fetch(
    connection: Connection,
    record: FetchRecord,
    *,
    content: bytes | None,
    status: str,
    store: LocalRawArtifactStore | None = None,
    reason_code: str | None = None,
    reason_detail: str | None = None,
    attempt: int = 1,
) -> UUID:
    digest: bytes | None = None
    if content is not None:
        (store or LocalRawArtifactStore()).put(content)
        digest = sha256(content).digest()
    return connection.execute(
        sa.insert(fetches)
        .values(
            dataset=record.dataset,
            source=record.source,
            resource_key=record.resource_key,
            source_uri=record.source_uri,
            purpose=record.purpose,
            adapter_version=record.adapter_version,
            git_commit=record.git_commit,
            fetched_at=record.fetched_at,
            status=status,
            reason_code=reason_code,
            reason_detail=reason_detail,
            attempt=attempt,
            sha256=digest,
            byte_size=None if content is None else len(content),
        )
        .returning(fetches.c.id)
    ).scalar_one()
