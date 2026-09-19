"""Step 20-c — one request budget per host, enforced in one place.

MOPS blocked the legacy scraper on 2026-07-02; the legacy scraper's answer
was a 3-second pause between requests (my_stock_project
scraper/quarterly/fetch_xbrl.py, `FETCH_INTERVAL_SECONDS = 3`). Steps 20-d,
22, 23 and 33 all call the same host. Each sleeping on its own would let two
of them running together hit MOPS twice as fast as either intends, which is
the pattern that got the legacy scraper blocked. The governor sits inside the
fetcher, so no adapter can reach MOPS without passing it.

The budget: requests to a governed host never overlap, and each starts at
least the host's interval after the previous one to that host finished.
"""

from __future__ import annotations

import threading
import time
from itertools import pairwise

import httpx
import pytest

from stock_data_center.ingestion.http import (
    MOPS_HOST,
    MOPS_MIN_INTERVAL_SECONDS,
    HostRateGovernor,
    HttpSourceFetcher,
    RetryingFetcher,
    process_governor,
)
from stock_data_center.ingestion.models import SourceResource


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_the_mops_budget_is_the_legacy_three_second_interval() -> None:
    assert MOPS_HOST == "mopsov.twse.com.tw"
    assert MOPS_MIN_INTERVAL_SECONDS == 3.0
    assert process_governor().interval_for(f"https://{MOPS_HOST}/nas/t21/x.html") == 3.0


def test_an_ungoverned_host_is_never_delayed() -> None:
    clock = FakeClock()
    governor = HostRateGovernor({MOPS_HOST: 3.0}, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        with governor.slot("https://www.twse.com.tw/rwd/zh/fund/T86"):
            pass
    assert clock.sleeps == []


def test_the_next_request_waits_out_the_interval_from_the_last_finish() -> None:
    clock = FakeClock()
    governor = HostRateGovernor({MOPS_HOST: 3.0}, clock=clock, sleep=clock.sleep)
    url = f"https://{MOPS_HOST}/server-java/t13sa150_otc"
    with governor.slot(url):
        clock.now += 1.0  # the request itself took a second
    clock.now += 0.5
    with governor.slot(url):
        pass
    assert clock.sleeps == [pytest.approx(2.5)]


def test_host_matching_ignores_case_and_port_but_not_the_host() -> None:
    governor = HostRateGovernor({MOPS_HOST: 3.0})
    assert governor.interval_for("https://MOPSOV.twse.com.tw:443/x") == 3.0
    assert governor.interval_for("https://mops.twse.com.tw/x") is None
    assert governor.interval_for("https://mopsov.twse.com.tw.evil.test/x") is None


def test_a_negative_or_zero_interval_is_rejected() -> None:
    with pytest.raises(ValueError):
        HostRateGovernor({MOPS_HOST: 0.0})


def test_every_default_fetcher_in_the_process_shares_one_governor() -> None:
    # "Every MOPS request in the process passes through the governor":
    # a fetcher built without an explicit governor, including the one a
    # RetryingFetcher builds for itself, uses the process-wide one.
    assert HttpSourceFetcher().governor is process_governor()
    assert HttpSourceFetcher().governor is HttpSourceFetcher().governor
    assert RetryingFetcher()._fetcher.governor is process_governor()


def test_two_adapters_running_together_cannot_exceed_the_host_budget() -> None:
    """Two importers, each with its own fetcher, on two threads, against the
    same governed host — the shape of Step 22's monthly-revenue job
    overlapping Step 20-d's foreign-holding job. Neither knows about the
    other; the shared governor alone must keep them inside one budget."""
    interval = 0.15
    governor = HostRateGovernor({MOPS_HOST: interval})
    spans: list[tuple[float, float]] = []
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        start = time.monotonic()
        time.sleep(0.02)  # the request is in flight for a while
        with lock:
            spans.append((start, time.monotonic()))
        return httpx.Response(200, content=b"<html></html>",
                              headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    revenue = HttpSourceFetcher(transport=transport, governor=governor)
    foreign_holding = HttpSourceFetcher(transport=transport, governor=governor)

    def run(fetcher: HttpSourceFetcher, resources: list[SourceResource]) -> None:
        for resource in resources:
            fetcher.fetch(resource)

    revenue_resources = [
        SourceResource(
            resource_key=f"t21sc03:{month}",
            source_uri=f"https://{MOPS_HOST}/nas/t21/sii/t21sc03_115_{month}_0.html",
        )
        for month in range(1, 5)
    ]
    holding_resources = [
        SourceResource.form_post(
            resource_key=f"t13sa150_otc:{day}",
            source_uri=f"https://{MOPS_HOST}/server-java/t13sa150_otc",
            fields=(("step", "2"), ("years", "2026"), ("months", "09"),
                    ("days", f"{day:02d}"), ("bcode", "")),
        )
        for day in range(1, 5)
    ]
    threads = [
        threading.Thread(target=run, args=(revenue, revenue_resources)),
        threading.Thread(target=run, args=(foreign_holding, holding_resources)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    spans.sort()
    assert len(spans) == 8
    for (_, previous_end), (next_start, _) in pairwise(spans):
        # never overlapping, and never closer than the interval
        assert next_start - previous_end >= interval - 0.005


def test_a_retry_goes_through_the_governor_too() -> None:
    clock = FakeClock()
    governor = HostRateGovernor({MOPS_HOST: 3.0}, clock=clock, sleep=clock.sleep)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200, content=b"ok")

    fetcher = RetryingFetcher(
        HttpSourceFetcher(transport=httpx.MockTransport(handler), governor=governor),
        backoff_seconds=lambda attempt: 0.0,
        sleep=lambda seconds: None,
    )
    fetcher.fetch(SourceResource(resource_key="k", source_uri=f"https://{MOPS_HOST}/x"))
    assert calls == 2
    # the retry waited out the host interval although its own backoff was zero
    assert clock.sleeps == [pytest.approx(3.0)]


def test_a_failed_request_still_counts_against_the_budget() -> None:
    clock = FakeClock()
    governor = HostRateGovernor({MOPS_HOST: 3.0}, clock=clock, sleep=clock.sleep)
    url = f"https://{MOPS_HOST}/x"
    with pytest.raises(RuntimeError), governor.slot(url):
        raise RuntimeError("connection reset")
    with governor.slot(url):
        pass
    assert clock.sleeps == [pytest.approx(3.0)]
