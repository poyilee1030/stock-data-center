# Step 20-c Acceptance Report

Status: IN REVIEW

Scope: the fetch layer that Step 20-d needs. There are two parts.

- `SourceResource` now describes a whole request: its method, body and
  headers, serialized in one canonical form.
- A per-host rate governor sits inside `HttpSourceFetcher`, so every request
  to MOPS in the process shares one budget.

Schema impact: none. There is no migration.
PIT impact: none. Publication evidence, release rules and business identity
are untouched.
Size: `src/` changed by +223/−9 lines (`http.py`, `models.py`, and a
6-line change to `lifecycle.py`). That is below the ~800-line split threshold
(`CLAUDE.md` §1).

## Design decisions

1. **What a resource holds.** It has `method` (`GET` or `POST`), `body`
   (bytes, or `None`) and `headers`. Header names are stored lower-cased,
   de-duplicated and sorted. Two resources that send the same request are
   therefore equal and serialize to the same string. `SourceResource.form_post`
   builds an ASCII `application/x-www-form-urlencoded` body from fields in the
   order given, which is the request MOPS `t13sa150_otc` expects.
   The constructor rejects four kinds of request that cannot be sent coherently:
   - a `GET` with a body;
   - a method other than `GET` or `POST`;
   - duplicate headers;
   - a `Host`, `Content-Length` or `Transfer-Encoding` header, because httpx
     derives those from the URL and body, and a stored request could otherwise
     disagree with what was actually sent.
2. **Serialization.** `to_json()` produces canonical JSON with the body in
   base64, so a non-text body survives byte for byte. `from_json()` rejects
   fields it does not know rather than dropping them. The `to_json_object()` /
   `from_json_object()` pair does the same for JSONB. This is the form a job
   will store.
3. **Headers.** The fetcher still sends `Accept: application/json` and its
   `User-Agent` by default. A resource's headers are added to those defaults
   and override them. A plain GET goes out exactly as before.
4. **The budget.** `mopsov.twse.com.tw` gets 3 seconds. That is the legacy
   scraper's `FETCH_INTERVAL_SECONDS`, which `my_stock_project`
   `scraper/quarterly/fetch_xbrl.py` adopted after MOPS blocked it on
   2026-07-02 ("別縮短"). The governor applies two rules to a governed host:
   - requests never overlap;
   - each request starts at least the interval after the previous one
     finished. A failed request counts too, because the host saw it.

   The host must match exactly, ignoring case and port, so
   `mops.twse.com.tw` and look-alike hosts are not governed. No other host is
   governed: the TWSE and TPEx backfills keep their own
   `min_interval_seconds`. Moving those backfills onto the governor is not in
   this step's scope.
5. **One governor per process.** Any `HttpSourceFetcher` built without an
   explicit governor uses `process_governor()`. That includes the fetcher a
   `RetryingFetcher` builds for itself. The governor wraps the request itself,
   so a retry also waits for the host.
6. **Request provenance.** `raw_artifact_observations.source_uri` stores a
   URL, and every `t13sa150_otc` date posts to the same URL. When a resource
   is not a plain GET, the lifecycle adds `resource.request_identity()` to the
   manifest's `source_scope`, which also puts it in the configuration
   fingerprint. A plain GET's identity is `None`, so no existing import's
   scope or fingerprint changes, and a half-finished backfill still resumes
   under its old `import_id`.

## Acceptance criteria

| Criterion | Result | Evidence |
| --- | --- | --- |
| A POST resource round-trips through serialization unchanged | PASS | `test_a_post_resource_round_trips_through_serialization_unchanged` checks both that the restored resource equals the original and that it re-serializes to the same text. `test_a_body_that_is_not_text_round_trips_byte_for_byte` does the same for a big5 body with `\x00\xff`. |
| Two adapters running together cannot exceed the host budget | PASS | `test_two_adapters_running_together_cannot_exceed_the_host_budget`: two fetchers on two threads share one governor and send 4 GETs (revenue) and 4 POSTs (foreign holding) to the MOPS host. The test asserts that no two request spans overlap and that every gap is at least the interval. |
| Every MOPS request in the process passes through the governor | PASS | `test_every_default_fetcher_in_the_process_shares_one_governor` covers default fetchers, including the one inside `RetryingFetcher`. `test_a_retry_goes_through_the_governor_too` shows a retry waiting out 3 s even though its own backoff is zero. |
| A POST's request is in its import's provenance | PASS | `test_a_post_resource_records_its_full_request_in_the_manifest` (integration) restores the manifest's `source_scope.request` to the exact resource that was sent. `test_a_plain_get_resource_scope_is_unchanged` confirms plain-GET scopes are unchanged. |
| The fetcher sends what the resource names | PASS | `test_the_fetcher_sends_the_method_body_and_headers_the_resource_names` and `test_a_plain_get_is_sent_exactly_as_before`, both through `httpx.MockTransport`. |

**The tests failed first.** Both new unit files failed at import before any
implementation existed. The integration test failed on the missing `request`
key. `test_a_plain_get_resource_scope_is_unchanged` guards against a change
rather than asking for one, so it passed from the start. Mutation check: with
the fetcher's `governor.slot(...)` replaced by `if True:`, the concurrency test
and the retry test both fail. They pass again once the mutation is reverted.

**The full suite passes:** 737 passed, 3 skipped. The skipped tests are
opt-in live calls (`RUN_LIVE_SOURCE_TESTS`). The run used a freshly
recreated `stockdc` test database. Ruff reports no errors in the new and
changed code. `models.py` and `lifecycle.py` still report three findings
(two import-sort and one unused import); the same three are reported on
`main`.

## Live check (2026-09-19)

I sent one POST of the legacy scraper's form (`step=2&years=2026&months=09&days=11&bcode=`)
to `https://mopsov.twse.com.tw/server-java/t13sa150_otc` through the new
`HttpSourceFetcher`, using the process governor, then sent the same request
again.

- The response was `text/html`, 548,126 bytes. The ROADMAP estimates about
  550 KB per date. The table is headed `115/09/11 外資及陸資投資持股統計` and
  has the 11 columns 20-d expects. Its first row is `00411A 主動統一前沿科技`.
- The second request's wall time, including the governor's wait, was 3.13 s.
  Both responses had the same content.
- Decoded as strict big5, the page yields 11 replacement characters. Choosing
  the codec (for example big5-hkscs or cp950) is for 20-d's parser to settle.

## Known limitations and deferred work

- **The budget holds within one process.** Two separate processes, such as a
  cron job and a manual backfill, each have their own governor. v1 runs one
  ingestion process (ROADMAP §3.1: no queue, no separate service). If that
  changes, the budget has to move into shared state, for example a
  PostgreSQL advisory lock.
- **Redirects are not re-governed.** A redirect to another host is not
  governed again. No governed source redirects today.
- **TWSE/TPEx pacing stays with each backfill.** Their per-backfill sleeps
  are unchanged. Moving them onto the governor is a behaviour change for the
  Step 17–20 backfills and has no ROADMAP step.
- **A pre-existing ledger error.** ROADMAP Step 19-d's section still reads
  **IN REVIEW** although it is MERGED, both in the §20 table and in `CLAUDE.md`. This
  step leaves it for a ledger-only fix.
