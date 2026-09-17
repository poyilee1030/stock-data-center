"""Short-timeout HTTP retrieval for raw-first source imports."""

from __future__ import annotations

import time
from collections.abc import Callable
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


# Not a redirect httpx could follow (no usable `Location`, or a loop back to
# the same URL): TWSE's CDN answers this way intermittently under sustained
# sequential load (Step 19-d: a live 2020-2026 backfill, thousands of
# requests). It is transient — the same URL that 307s once succeeds seconds
# later — and unrelated to which locator was asked for.
_RETRYABLE_STATUSES = frozenset({307, 429, 500, 502, 503, 504})


class RetryingFetcher:
    """Retries a transient HTTP failure with backoff before giving up.

    For a bulk backfill only: a normal single-resource import already
    surfaces an operational failure to its caller, who decides whether to
    rerun (CLAUDE.md §71). This exists because a multi-hour backfill making
    thousands of sequential requests should not lose an hour of progress —
    and a whole calendar year's range, in the corporate-action shape — to one
    transient response the very next attempt would not have hit.
    """

    def __init__(
        self,
        fetcher: SourceFetcher | None = None,
        *,
        attempts: int = 5,
        backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 * 2**attempt,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fetcher = fetcher or HttpSourceFetcher()
        self._attempts = attempts
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep

    def fetch(self, resource: SourceResource) -> FetchedArtifact:
        last_error: Exception | None = None
        for attempt in range(self._attempts):
            if attempt:
                self._sleep(self._backoff_seconds(attempt - 1))
            try:
                return self._fetcher.fetch(resource)
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in _RETRYABLE_STATUSES:
                    raise
                last_error = error
            except (httpx.TimeoutException, httpx.TransportError) as error:
                last_error = error
        raise last_error  # type: ignore[misc]
