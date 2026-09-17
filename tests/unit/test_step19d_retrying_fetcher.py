"""Step 19-d — retrying a transient HTTP failure a bulk backfill hit live.

A real 2020-2026 corporate-action backfill saw TWSE answer a handful of
requests with a 307 that carried no usable redirect, and the very same URL
succeeded seconds later. `RetryingFetcher` is the fix: unrelated to which
resource was asked for, so it belongs at the fetch layer, not in any one
adapter or importer.
"""

from __future__ import annotations

import httpx
import pytest

from stock_data_center.ingestion.http import RetryingFetcher
from stock_data_center.ingestion.models import FetchedArtifact, SourceResource

RESOURCE = SourceResource(resource_key="k", source_uri="https://example/x")


def _artifact() -> FetchedArtifact:
    from datetime import UTC, datetime

    return FetchedArtifact(
        content=b"{}", source_uri=RESOURCE.source_uri,
        fetched_at=datetime.now(UTC), media_type="application/json",
    )


class _StatusError(httpx.HTTPStatusError):
    def __init__(self, status_code: int) -> None:
        request = httpx.Request("GET", RESOURCE.source_uri)
        response = httpx.Response(status_code, request=request)
        super().__init__(f"status {status_code}", request=request, response=response)


class ScriptedFetcher:
    def __init__(self, outcomes: list[Exception | FetchedArtifact]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def fetch(self, resource: SourceResource) -> FetchedArtifact:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_a_retryable_status_succeeds_on_a_later_attempt() -> None:
    sleeps: list[float] = []
    fetcher = ScriptedFetcher([_StatusError(307), _StatusError(307), _artifact()])
    result = RetryingFetcher(fetcher, sleep=sleeps.append).fetch(RESOURCE)
    assert result.content == b"{}"
    assert fetcher.calls == 3
    assert len(sleeps) == 2


def test_it_gives_up_after_its_attempt_budget() -> None:
    fetcher = ScriptedFetcher([_StatusError(307)] * 5)
    with pytest.raises(httpx.HTTPStatusError):
        RetryingFetcher(fetcher, attempts=5, sleep=lambda _s: None).fetch(RESOURCE)
    assert fetcher.calls == 5


def test_a_non_retryable_status_fails_immediately() -> None:
    fetcher = ScriptedFetcher([_StatusError(404), _artifact()])
    with pytest.raises(httpx.HTTPStatusError) as error:
        RetryingFetcher(fetcher, sleep=lambda _s: None).fetch(RESOURCE)
    assert error.value.response.status_code == 404
    assert fetcher.calls == 1


def test_a_timeout_is_retried_too() -> None:
    fetcher = ScriptedFetcher([httpx.ReadTimeout("slow"), _artifact()])
    result = RetryingFetcher(fetcher, sleep=lambda _s: None).fetch(RESOURCE)
    assert result.content == b"{}"
    assert fetcher.calls == 2


def test_the_first_attempt_is_not_delayed() -> None:
    sleeps: list[float] = []
    fetcher = ScriptedFetcher([_artifact()])
    RetryingFetcher(fetcher, sleep=sleeps.append).fetch(RESOURCE)
    assert sleeps == []
