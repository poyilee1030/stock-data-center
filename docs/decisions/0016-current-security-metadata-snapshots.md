# ADR-0016: Current Security Metadata Snapshot Semantics

Status: Accepted

Date: 2026-09-13

## Context

TWSE and TPEx publish official current-company OpenAPI snapshots. Each row
contains a report date, company code, current company name, industry code, and
listing date. The resources do not provide a historical stream of name or
industry changes, removed securities, delisting dates, or complete market
transfer events.

Treating `listed_on` as the effective date of every current field would
backdate today's name, industry, and venue to the original listing date.
Treating absence from a later snapshot as delisting would invent an event that
the retained row does not prove. Both choices would make historical answers
depend on unsupported inference.

## Decision

Phase 9 imports the following whole-market current snapshots:

- TWSE `opendata/t187ap03_L` as source `twse` and market `TWSE`;
- TPEx `mopsfin_t187ap03_O` as source `tpex` and market `TPEx`.

The stable `security.security_code` identity is registered independently of
venue. Official four-digit company codes and six-digit TWSE TDR codes are
preserved as received. For each returned company:

```text
effective_from = official snapshot report date
listed_on      = official listing date
effective_to   = NULL
delisted_on    = NULL
published_at   = NULL
```

The snapshot report date establishes only when the imported current state is
asserted by that resource. It does not prove that the name, industry, or venue
was already effective on `listed_on`. The exact original publication instant
is not present, so the import creates `unknown` publication evidence and stays
Market-PIT invisible until reliable evidence is added. Actual PostgreSQL write
time governs System PIT.

A later snapshot with equal market/name/industry/listing/delisting state reuses
the latest business version and adds raw/evidence observation lineage. A new
business revision starts on the later report date only when that source's state
changes. Absence from a snapshot does not close an interval or set a delisting
date.

TWSE and TPEx remain independent source histories. This milestone does not
reconcile them into a synthetic cross-source transfer history. Historical
market transfers require retained authoritative event evidence and a separate
Phase 9 milestone.

Both adapters use the shared raw-first lifecycle extracted from Pilot 1:

```text
fetch -> durable raw artifact -> captured checkpoint -> parse/normalize
      -> versioned canonical write -> unknown publication evidence
      -> succeeded checkpoint
```

Only `SourceDataError` conditions enter quarantine. Infrastructure, database,
and unexpected code failures retain a captured checkpoint for repair and
resume from the same raw bytes.

## Consequences

- Current official security metadata can populate stable identities safely.
- Daily unchanged snapshots do not create fake business revisions.
- Current snapshots cannot answer historical universe or transfer questions
  from before their report dates.
- A missing row is not silently interpreted as delisting.
- A separate historical-transfer adapter/reconciliation policy remains needed.
- Cache impact is none because Phase 9 has no cache.
