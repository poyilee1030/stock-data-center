# ADR-0017: Official Security Lifecycle History

Status: Accepted

Date: 2026-09-13

## Context

Current-company snapshots cannot prove when a security entered or left a
market. TWSE and TPEx separately publish official historical listing and
delisting tables. The tables identify venue-event dates and names, but do not
carry the original publication timestamp of each historical event.

TWSE's OpenAPI `company/newlisting` representation leaves the listing date
blank for 377 older rows as observed on 2026-09-13. The official TWSE RWD
representation of the same table retains all 792 listing dates, including the
oldest currently exposed 2001-01-03 event. The RWD table is therefore the
contract used for historical reconstruction.

## Decision

Phase 9 imports four official resource families:

- TWSE RWD `company/newlisting`, whole exposed listing history;
- TWSE RWD `company/suspendListing`, whole exposed delisting history;
- TPEx `company/latest`, one Gregorian year of listing events; and
- TPEx `company/deListed`, one Gregorian year of delisting events.

Each event registers the stable `security_code` identity and writes only to its
own source history (`twse` or `tpex`). A listing event produces:

```text
effective_from = official venue listing date
market         = the publishing venue
listed_on      = official venue listing date
delisted_on    = NULL
```

A delisting event produces:

```text
effective_from = official venue delisting date
market         = the publishing venue
listed_on      = NULL unless separately established by a listing event
delisted_on    = official venue delisting date
```

The delisting resource is normalized without reading current database state or
copying fields from a previously imported row. This keeps business output
deterministic regardless of import order. Before the exit date, an earlier
listing event remains the resolved state; on and after the exit date, the
terminal state is not listed.

Names are event-row names. They are not projected backward across an interval,
and industry is not invented because these event resources do not report it.
Absence from any yearly resource still does not prove a delisting.

Every event receives `unknown` official publication evidence with
`published_at = NULL`. The event date is not treated as an announcement time.
Actual insertion time controls System PIT; these backfilled rows remain Market
PIT invisible until separate retained evidence proves historical publication.

TWSE listing notes containing `櫃轉市` are explicit transfer-in evidence. The
normalized relation is retained append-only in `security_transfer_events`, with
repeated raw observations linked separately. Import manifests mark their
transfer result provisional and do not permanently stamp matched/unmatched
truth based on the database state at import time.

Final reconciliation is a separate, deterministic, re-runnable query over the
stored transfer entry and TPEx same-code, same-date delisting histories. Before
the required TPEx delisting year has a successful import manifest, a missing
counterpart is `pending`, not `unmatched`. After that year is present, the same
absence is genuinely unmatched. The result therefore converges after both
histories arrive regardless of import order. The match is audit metadata only:
it does not copy, merge, overwrite, or synthesize a cross-source business
version. Independent source histories and one stable security identity are
preserved.

## Coverage and restart semantics

Coverage is the minimum and maximum event date actually present in each raw
resource, not a claim of all-time exchange completeness. As observed on
2026-09-13, TWSE exposes listing events from 2001-01-03 and delisting events
from 2001-01-20. TPEx is year-scoped; its listing endpoint first returned a
non-empty year at 2005 and its delisting endpoint at 1995, with zero-event
years possible. Each manifest records its requested year, actual event range,
row count, exact source fields, and provisional transfer-event counts. It records
`coverage_completeness = not_evaluated` and does not turn
`coverage_gaps = null` into a completeness claim.

All four adapters reuse `RawFirstImporter`:

```text
fetch -> durable raw artifact -> captured checkpoint -> parse / normalize
      -> versioned canonical write -> unknown publication evidence
      -> succeeded checkpoint
```

Source/schema/domain violations are quarantined. Fetch, storage, database, and
unexpected programming failures remain resumable from the retained raw
artifact. Repeated source observations retain raw and evidence-observation
lineage without creating fake business or evidence revisions.

## Consequences

- Historical venue entry and exit states no longer depend on current snapshots.
- TPEx-to-TWSE transfer 5236 can be verified as one stable identity with
  independent official venue histories.
- Transfer matching is explicit reconciliation, not an implicit canonical
  source-selection policy.
- Reverse source-import order produces the same final matched/unmatched report;
  genuinely unmatched official histories remain visible.
- A later source-policy hook, broader performance work, the Taiwan trading
  calendar, and other Phase 9 datasets remain outside this change.
- Cache impact is none because Phase 9 has no cache.
