"""Step 20-c — a `SourceResource` is a complete, serializable request.

MOPS `t13sa150_otc` (Step 20-d) is a POST with a form body that answers big5
HTML. Before this step a resource was only a key and a URL, and the fetcher
always sent `GET` with `Accept: application/json`, so that request could not
be described at all. A resource now carries its method, body and headers, and
serializes to one canonical form a job can store and replay.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from stock_data_center.ingestion.http import HostRateGovernor, HttpSourceFetcher
from stock_data_center.ingestion.models import SourceResource

MOPS_URL = "https://mopsov.twse.com.tw/server-java/t13sa150_otc"

# The legacy scraper's form for 2026-09-11 (my_stock_project
# scraper/daily/fetch_daily_otc.py): Gregorian year, zero-padded month/day,
# empty security filter.
MOPS_FORM = (
    ("step", "2"),
    ("years", "2026"),
    ("months", "09"),
    ("days", "11"),
    ("bcode", ""),
)


def mops_resource() -> SourceResource:
    return SourceResource.form_post(
        resource_key="mops_t13sa150_otc:2026-09-11",
        source_uri=MOPS_URL,
        fields=MOPS_FORM,
        headers=(("Accept", "text/html"),),
    )


def test_a_plain_resource_is_still_a_get_with_no_body() -> None:
    resource = SourceResource(resource_key="k", source_uri="https://example/x")
    assert resource.method == "GET"
    assert resource.body is None
    assert resource.headers == ()


def test_a_form_post_encodes_its_fields_in_order() -> None:
    resource = mops_resource()
    assert resource.method == "POST"
    assert resource.body == b"step=2&years=2026&months=09&days=11&bcode="
    assert dict(resource.headers) == {
        "accept": "text/html",
        "content-type": "application/x-www-form-urlencoded",
    }


def test_a_post_resource_round_trips_through_serialization_unchanged() -> None:
    resource = mops_resource()
    text = resource.to_json()
    restored = SourceResource.from_json(text)
    assert restored == resource
    assert restored.to_json() == text


def test_a_body_that_is_not_text_round_trips_byte_for_byte() -> None:
    body = "代號=5009".encode("big5") + b"\x00\xff"
    resource = SourceResource(
        resource_key="k", source_uri="https://example/x", method="POST", body=body
    )
    assert SourceResource.from_json(resource.to_json()).body == body


def test_serialization_is_canonical_whatever_order_headers_are_given_in() -> None:
    a = SourceResource(
        resource_key="k", source_uri="https://example/x",
        headers=(("Accept", "text/html"), ("X-Trace", "1")),
    )
    b = SourceResource(
        resource_key="k", source_uri="https://example/x",
        headers=(("x-trace", "1"), ("accept", "text/html")),
    )
    assert a == b
    assert a.to_json() == b.to_json()


def test_serialization_rejects_an_unknown_field_rather_than_dropping_it() -> None:
    data = json.loads(mops_resource().to_json())
    data["timeout"] = 5
    with pytest.raises(ValueError, match="timeout"):
        SourceResource.from_json(json.dumps(data))


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"method": "PUT"}, "method"),
        ({"method": "post"}, "method"),
        ({"method": "GET", "body": b"x"}, "body"),
        ({"headers": (("Accept", "a"), ("accept", "b"))}, "duplicate"),
        ({"headers": (("Host", "evil"),)}, "host"),
        ({"headers": (("Content-Length", "3"),)}, "content-length"),
    ],
)
def test_an_incoherent_request_is_rejected(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        SourceResource(resource_key="k", source_uri="https://example/x", **kwargs)


def test_a_plain_get_has_no_request_identity_beyond_its_url() -> None:
    # Existing importers' manifests keep their configuration fingerprints:
    # a GET with no body and no headers is fully identified by its URL.
    plain = SourceResource(resource_key="k", source_uri="https://example/x")
    assert plain.request_identity() is None
    assert mops_resource().request_identity() == json.loads(mops_resource().to_json())


class _Recorder:
    def __init__(self, content: bytes = b"<html></html>",
                 media_type: str = "text/html; charset=big5") -> None:
        self.requests: list[httpx.Request] = []
        self._content = content
        self._media_type = media_type

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            200, content=self._content, headers={"content-type": self._media_type}
        )


def _fetcher(recorder: _Recorder) -> HttpSourceFetcher:
    return HttpSourceFetcher(
        transport=httpx.MockTransport(recorder), governor=HostRateGovernor({})
    )


def test_the_fetcher_sends_the_method_body_and_headers_the_resource_names() -> None:
    recorder = _Recorder(content="外資".encode("big5"))
    fetched = _fetcher(recorder).fetch(mops_resource())
    (request,) = recorder.requests
    assert request.method == "POST"
    assert request.content == b"step=2&years=2026&months=09&days=11&bcode="
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    # The resource's Accept replaces the fetcher's JSON default.
    assert request.headers["accept"] == "text/html"
    assert fetched.content == "外資".encode("big5")
    assert fetched.media_type == "text/html"
    assert fetched.fetched_at.tzinfo is not None
    assert fetched.fetched_at <= datetime.now(UTC)


def test_a_plain_get_is_sent_exactly_as_before() -> None:
    recorder = _Recorder(content=b"{}", media_type="application/json")
    _fetcher(recorder).fetch(
        SourceResource(resource_key="k", source_uri="https://example.test/x")
    )
    (request,) = recorder.requests
    assert request.method == "GET"
    assert request.content == b""
    assert request.headers["accept"] == "application/json"
    assert request.headers["user-agent"] == "stock-data-center/0.1 raw-first-ingestion"
