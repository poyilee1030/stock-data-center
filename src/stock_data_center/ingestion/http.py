"""Short-timeout HTTP retrieval for raw-first source imports."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from pathlib import Path

from stock_data_center.ingestion.models import (
    FetchedArtifact,
    SourceDataError,
    SourceResource,
)


class SourceFetcher(Protocol):
    def fetch(self, resource: SourceResource) -> FetchedArtifact: ...


class LocalArchiveFetcher:
    """Read one archive file, and record what the file itself says.

    `fetched_at` is the Data Center read time, not a source publication time
    (CLAUDE.md §75). The file's own mtime travels separately, in the manifest.
    """

    def __init__(self, *, media_type: str = "application/octet-stream") -> None:
        self.last_mtime: datetime | None = None
        self._media_type = media_type

    def fetch(self, resource: SourceResource) -> FetchedArtifact:
        path = Path(resource.source_uri)
        try:
            content = path.read_bytes()
        except OSError as error:
            raise SourceDataError(
                "archive_unreadable", f"{path}: {error}"
            ) from error
        self.last_mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        return FetchedArtifact(
            content=content,
            source_uri=str(path),
            fetched_at=datetime.now(UTC),
            media_type=self._media_type,
        )


class ArchiveGlobFetcher(LocalArchiveFetcher):
    """Resolve one archive file from a pattern, as part of fetching it.

    An archive file's folder is known but its name carries a date we do not,
    so it has to be looked up. Doing that in an adapter's `resource()` would
    put the lookup before the import manifest exists, outside every block that
    records a quarantine or an operational failure: a missing file would then
    end a 45,000-filing loop with no auditable trace at all (CLAUDE.md §78,
    §79). Here it is a fetch failure, which the lifecycle records.

    `resource.source_uri` is the pattern; the artifact observation records the
    file that actually answered it.
    """

    def fetch(self, resource: SourceResource) -> FetchedArtifact:
        pattern = Path(resource.source_uri)
        matches = sorted(pattern.parent.glob(pattern.name))
        if not matches:
            raise SourceDataError(
                "archive_file_missing", f"no archived file matches {pattern}"
            )
        if len(matches) > 1:
            # One filing, one document. Two would mean the archive holds two
            # answers to the same request and nothing says which is current.
            raise SourceDataError(
                "ambiguous_archive_file",
                f"{len(matches)} archived files match {pattern}: "
                f"{[path.name for path in matches]}",
            )
        return super().fetch(
            SourceResource(
                resource_key=resource.resource_key, source_uri=str(matches[0])
            )
        )


# MOPS blocked the legacy scraper on 2026-07-02. Its answer, kept since, is a
# 3-second pause between requests (my_stock_project
# scraper/quarterly/fetch_xbrl.py, `FETCH_INTERVAL_SECONDS`). Steps 20-d, 22,
# 23 and 33 all call this host, so the pause is one budget for the whole
# process, not one per adapter.
MOPS_HOST = "mopsov.twse.com.tw"
MOPS_MIN_INTERVAL_SECONDS = 3.0


class HostRateGovernor:
    """One request budget per host, shared by every fetcher that holds it.

    For a governed host, requests never overlap, and each starts at least the
    host's interval after the previous one to that host finished — failed
    requests included, since the host saw them too. Hosts with no budget pass
    straight through. Thread-safe: two importers on two threads, each with
    its own fetcher, still share one budget.

    The host is the requested URL's. A redirect to another host is not
    re-governed; no governed source redirects today.
    """

    def __init__(
        self,
        min_interval_seconds: Mapping[str, float],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        for host, interval in min_interval_seconds.items():
            if not interval > 0:
                raise ValueError(f"{host}: interval must be positive, got {interval}")
        self._intervals = {
            host.lower(): float(interval)
            for host, interval in min_interval_seconds.items()
        }
        self._locks = {host: threading.Lock() for host in self._intervals}
        self._last_finished: dict[str, float] = {}
        self._clock = clock
        self._sleep = sleep

    def interval_for(self, url: str) -> float | None:
        host = (urlsplit(url).hostname or "").lower()
        return self._intervals.get(host)

    @contextmanager
    def slot(self, url: str) -> Iterator[None]:
        """Hold the host for one request: wait out its interval, then keep
        every other request to it waiting until this one has finished."""
        host = (urlsplit(url).hostname or "").lower()
        interval = self._intervals.get(host)
        if interval is None:
            yield
            return
        with self._locks[host]:
            last = self._last_finished.get(host)
            if last is not None:
                wait = last + interval - self._clock()
                if wait > 0:
                    self._sleep(wait)
            try:
                yield
            finally:
                self._last_finished[host] = self._clock()


_PROCESS_GOVERNOR = HostRateGovernor({MOPS_HOST: MOPS_MIN_INTERVAL_SECONDS})


def process_governor() -> HostRateGovernor:
    """The governor every fetcher in this process uses unless handed another."""
    return _PROCESS_GOVERNOR


_DEFAULT_HEADERS = {
    "accept": "application/json",
    "user-agent": "stock-data-center/0.1 raw-first-ingestion",
}


class HttpSourceFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        governor: HostRateGovernor | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)
        self._governor = governor or process_governor()
        self._transport = transport

    @property
    def governor(self) -> HostRateGovernor:
        return self._governor

    def fetch(self, resource: SourceResource) -> FetchedArtifact:
        with httpx.Client(
            timeout=self._timeout,
            follow_redirects=True,
            headers={**_DEFAULT_HEADERS, **dict(resource.headers)},
            transport=self._transport,
        ) as client:
            with self._governor.slot(resource.source_uri):
                response = client.request(
                    resource.method, resource.source_uri, content=resource.body
                )
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
            except (
                httpx.TimeoutException,
                httpx.TransportError,
                # A 307 that does carry a `Location` but loops back to the
                # same URL — the other shape this class's own module
                # comment already claims to cover — surfaces here, not as
                # an `HTTPStatusError`: `follow_redirects=True` exhausts
                # httpx's own redirect limit before any status code
                # reaches `raise_for_status()`.
                httpx.TooManyRedirects,
            ) as error:
                last_error = error
        raise last_error  # type: ignore[misc]
