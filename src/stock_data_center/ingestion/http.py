"""Short-timeout HTTP retrieval for raw-first source imports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

import httpx

from stock_data_center.ingestion.models import FetchedArtifact, SourceResource


class SourceFetcher(Protocol):
    def fetch(self, resource: SourceResource) -> FetchedArtifact: ...


class HttpSourceFetcher:
    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)

    def fetch(self, resource: SourceResource) -> FetchedArtifact:
        with httpx.Client(
            timeout=self._timeout,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "stock-data-center/0.1 raw-first-ingestion",
            },
        ) as client:
            response = client.get(resource.source_uri)
            fetched_at = datetime.now(UTC)
            response.raise_for_status()
        media_type = response.headers.get("content-type", "application/octet-stream")
        media_type = media_type.split(";", 1)[0].strip().lower()
        return FetchedArtifact(
            content=response.content,
            source_uri=str(response.url),
            fetched_at=fetched_at,
            media_type=media_type,
        )
