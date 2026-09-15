# stock-data-center ROADMAP

> Delivery is tracked by pull request. Historical phase names are retained only as legacy references.
>
> Status date: 2026-09-14.
>
> Source-reality baseline: [`docs/source_field_audit.md`](docs/source_field_audit.md). Every planned PR in this roadmap is scoped to fields that the audit shows actually exist.

## 1. Project Goal

`stock-data-center` replaces the database part of `my_stock_project` (`stock_db`) with a PIT-controlled, provenance-preserving store for Taiwan stock data used by downstream research and ML systems.

It must answer:

> What data was valid, publicly knowable, and actually ingested at a given historical time?

Primary downstream consumers:

```text
stock-eps-model
stock-model-selection
(today: my_stock_project train_eps/, strategies/, backtester/)
```

Downstream systems must never query the Data Center database directly.

## 1.1 v1 scope is the legacy scope, rebuilt with PIT

v1 covers what `stock_db` covers and what legacy consumers actually read, from 2020-01-02 onward, re-ingested from official sources with PIT metadata and provenance.

v1 is not a superset of every field a data vendor might offer. A field enters v1 only when a verified source field populates it (§2.4).

**The v1 security universe is 上市 (`sii`) and 上櫃 (`otc`) only.** 興櫃 (`rotc`) and 公開發行 companies that trade on no board (`pub`) are excluded. This is an owner decision, not an availability limit: MOPS serves all four, and the exchanges publish no daily quote file for 興櫃 at all. Where a source offers a market selector, an adapter requests `sii` and `otc` and rejects the other values at its boundary rather than filtering them out after ingestion.

---

# 2. Source Reality Baseline

Full evidence is in [`docs/source_field_audit.md`](docs/source_field_audit.md). This section lists the facts that constrain every later PR.

## 2.1 Legacy scope

| Domain | Legacy tables | Coverage | Read by legacy consumers |
|---|---|---|---|
| Daily market | `daily_quotes` | 2020-01-02 → 2026-09-11 | yes |
| Market indices | `market_indices` | same | yes (`index_close` only) |
| Official valuation | `pe_ratio` | same | yes |
| Institutional flows | `institutional_investors`, `institutional_summary` | same | per-security yes; summary no |
| Foreign holding | `foreign_holding` | same | yes (`issued_shares`, `foreign_held_ratio`) |
| Margin / SBL | `margin_trading`, `margin_sbl`, `margin_summary` | same | per-security yes; summary no |
| Monthly revenue | `monthly_revenue` | 2020M01 → 2026M08 | yes (published MoM/YoY/cumulative YoY) |
| Financial statements | `*_xbrl`, `quarterly_reports_xbrl`, `xbrl_codebook` | 2020Q1 → 2026Q2 | facts yes; codebook no |
| TDCC | `shareholding` | 2020-01-03 → 2026-09-11 | yes |
| Security metadata | `stock_info`, `stock_tags` | current snapshots | `stock_info` yes; tags (MoneyDJ, third party) no |
| Corporate actions | `dividend` | TWSE only, 2020-02-13 → 2026-09-10 | no |
| Derived | `technical_indicators`, `trust_holding`, `dealer_holding`, `shareholding_concentration`, `valuation_daily`, `margin_pressure_analysis`, `short_interest_analysis` | — | yes |

Legacy technical indicators and backtests use raw, unadjusted prices. Nothing in the legacy stack consumes corporate-action data.

## 2.2 Source facts that constrain design

1. **No inspected source publishes a per-row publication instant.** Under the current evidence rules, all imported history has `published_at = NULL` and is Market-PIT invisible. PR #15 is the decision point.
   - The legacy system did record real first-seen capture dates, at date precision: monthly revenue from 2026M02 (daily 22:45 run, days 1–15) and XBRL from 2025Q4 (daily 23:50 run).
   - Earlier legacy `publish_time` values are statutory deadlines assigned after the fact: the 10th of the next month for 2020M01–2026M01, and the filing deadline for 2020Q1–2025Q3.
   - Details are in audit §7.1.
2. **Official endpoints still serve 2020 onward for every v1 domain except TDCC.** Re-fetching produces byte-faithful raw artifacts through the PR #9 raw-first lifecycle.
3. **The legacy raw archive is not official source bytes.** The scrapers decoded, re-encoded, stripped title rows (the report date survives only in the directory path), and parsed some domains into CSV. The only exception is the TDCC OpenData files. The archive therefore serves three purposes, and is not a general import source:
   - TDCC history
   - the legacy first-seen records (monthly revenue from 2026M02, XBRL from 2025Q4)
   - a reconciliation baseline
4. **Official TDCC history is not available.** OpenData serves only the latest week, and the portal about one year (51 weeks on 2026-09-14). Weeks before 2025-09-19 exist only in the archive, 375 weeks back to 2019-06-28 (audit §4.9).
5. **MOPS monthly revenue and iXBRL return the latest corrected or amended values.** First-published *values* survive only where some capture recorded them: the legacy first-seen records (monthly revenue from 2026M02, XBRL from 2025Q4), then Data Center forward capture. Publication *dates* are a separate matter: for monthly revenue, `revswarm` reconstructs them from dated news reports for 89.7% of 2020M01-2026M01 (audit §7.4), so most of that history is Market-PIT visible at its real announcement date rather than at a statutory deadline.
6. **The issuer dividend declarations are announcement feeds with no link to an executed event.** They carry no ex-date, record date, payment date, or locator; the MOPS page footnote says so itself. Within a feed `(公司代號, 股利年度, 股利所屬期間, 期別)` is a workable key, so PR #33 stores them as their own domain; they never enter `corporate_action_versions`. MOPS `t05st09sub` serves the full history one market-year per request, but the legal-reserve / capital-surplus split only exists from 民國110; before that the two reserves are published as one figure (audit §4.13).
7. **Exchange result feeds** (`TWT49U`, `TWTAUU`, `TWTB8U`, TPEx `exDailyQ`, TPEx `revivt`) carry one row per security per executed event date, and TWSE's own detail locator is `(code, date)`.
   - They provide: close-before and reference prices, cash dividend, combined free shares, rights terms, capital-reduction share exchange, and cash return.
   - They do not provide: announcement, record, or payment dates, or the earnings/capital-surplus stock-dividend split.
8. **The whole-list market-index sources carry close, change points, and change percent only.** There is no index code, open, high, low, or trade value. Index OHLC exists for the TAIEX alone, in `MI_5MINS_HIST` (`發行量加權股價指數歷史資料`, one calendar month per request, verified live), which the legacy system never fetched. No TPEx equivalent was found (audit 4.2).
9. **The PR #9 per-security daily adapters need one request per security-month**, about 2,200 requests per trade date for daily capture. Production needs whole-market daily endpoints: one request per trade date per market.

## 2.3 Unsourced columns

These existing columns have no source field. They stay NULL, and no v1 PR may promise them:

```text
daily_price_versions.bid_snapshot / ask_snapshot                (depth blobs; the one published
                                                                level is stored in last_bid_price /
                                                                last_ask_price / last_bid_volume /
                                                                last_ask_volume)
market_index_versions.open_value / high_value / low_value      (TAIEX only, via MI_5MINS_HIST; see PR #18)
market_index_versions.trade_value
market_index_metadata_versions.effective_from / effective_to   (as official dates)
security_tag_versions                                          (whole table; third-party only)
corporate_action_versions.announcement_date / record_date / payment_date
corporate_action_versions.earnings_stock_ratio / capital_surplus_stock_ratio
                                                               (split exists only in the announcement
                                                                feeds; PR #33 stores it separately)
monthly_revenue_versions.currency                              (page-level constant TWD)
publication_evidence.published_at from an official release     (no source publishes it)
```

Partially sourced columns (for example, `official_valuation_versions.dividend_per_share` is TPEx only) are listed in the audit §5.

## 2.4 No-unsourced-field rule

A PR may add or promise a stored field only if it names:

```text
official endpoint
exact source field label(s)
date range in which the field exists (header variants included)
unit and conversion
```

The PR adds that evidence to `docs/source_field_audit.md`. A column in §2.3 stays NULL. The API reports it as unavailable, not as missing data.

---

# 3. High-Level Architecture

```text
External Sources
TWSE / TPEx / MOPS / TDCC
                  |
                  v
        +----------------------+
        |    Ingestion Layer   |
        | adapters / parsers   |
        | normalization        |
        +----------+-----------+
                   |
          raw artifacts + metadata
                   |
                   v
        +----------------------+
        |     PostgreSQL 18    |
        |    Source of Truth   |
        |                      |
        | version history      |
        | publication evidence |
        | ingest history       |
        | PIT metadata         |
        +----------+-----------+
                   ^
                   |
        +----------+-----------+
        |   PIT Resolver       |
        | market / system PIT  |
        +----------+-----------+
                   ^
                   |
        +----------+-----------+
        | FastAPI / Public API |
        +----------+-----------+
                   ^
             HTTP / SDK
                   |
          +--------+---------+
          |                  |
          v                  v
 stock-eps-model    stock-model-selection
```

PostgreSQL is authoritative. A query-result cache (NullCache/Redis) may sit between the API and the resolver later, but it is not part of v1 delivery (§26.2).

---

# 4. Core Architectural Invariants

## Invariant A — PostgreSQL is the source of truth

Any cache is never authoritative. Deleting a cache must not change correctness.

## Invariant B — A cache is optional

The application must work correctly with:

```text
CACHE_BACKEND=none
```

## Invariant C — A cache affects performance only

If a cache is ever added, then for the same canonical query:

```text
result(cache=none) == result(cache=redis)
```

## Invariant D — Downstream systems never access PostgreSQL or a cache directly

Only the Data Center API / SDK is public.

## Invariant E — PIT semantics are enforced by Data Center

Downstream code must not recreate publication-time, revision, or ingestion-time rules.

## Invariant F — Current DB state is not historical truth

Historical visibility is determined from temporal metadata and evidence, never from "row exists today".

## Invariant G — Corporate-action identity must survive corrections

A normalized corporate-action event has identity `(security_id, source, source_event_key)`, and that identity survives corrections to the event's terms.

There are two kinds of source.

**1. Announcement/plan feeds**

Examples: issuer dividend summaries `t187ap45_L` and `mopsfin_t187ap39_O`, and exchange forecast tables such as `TWT48U`. Their dates and terms are plans that can change.

The following are revision content and must never form identity:

```text
action_type, board/announcement/ex/record/payment dates, cash amount,
stock or share ratio, reference price, dividend year/period, row ordinal,
company name, adapter version
```

No announcement feed has a proven stable identity, so announcement feeds are not normalized in v1. PR #13 falsified `(security, dividend_year, period)` on live TPEx data.

**2. Exchange result feeds**

`TWT49U`, `TWTAUU`, `TWTB8U`, TPEx `exDailyQ`, and TPEx `revivt`. Each row records an event the exchange executed and priced on a trading date.

- The executed event date, or the exchange's own detail locator where one exists, is the event's identity, not revision content:

  ```text
  source_event_key = "<feed>:<locator date>"
  ```

- Changed terms under the same locator are revisions of the same event.
- A row that disappears from the feed is recorded as a retraction.

Current-snapshot uniqueness is required evidence, not proof. A result-feed adapter must also pass the full-history duplicate scan and the correction regressions in §27.7.

---

# 5. Fixed Technology Stack

Unless changed through an ADR and ROADMAP amendment:

```text
Python                 3.12+
FastAPI
Pydantic               2.x
SQLAlchemy             2.x
Alembic
PostgreSQL             18+
pytest
httpx
Docker / Docker Compose
Redis                  optional, deferred (§26.2)
```

Docker PostgreSQL image: `postgres:18`. Do not use `postgres:latest`.

Raw artifact storage v1 is the local filesystem under `data/raw/`, behind an abstraction so S3, MinIO, or NAS can be added later.

---

# 6. Time Model

All externally meaningful timestamps are timezone-aware. The source-market timezone is `Asia/Taipei`. Internally, prefer UTC instants.

- PostgreSQL: `TIMESTAMPTZ`
- SQLAlchemy: `DateTime(timezone=True)`

Never silently mix naive and timezone-aware datetimes.

---

# 7. PIT Semantics

## 7.1 Market PIT

> What was publicly knowable at market-information time T, using only publication evidence known to the Data Center by knowledge time K?

```text
published_at <= information_as_of
AND
publication_evidence.recorded_at <= knowledge_as_of
```

Then resolve the authoritative evidence available under that knowledge cutoff.

## 7.2 System PIT

> What had this Data Center actually and completely ingested by time T?

```text
ingested_at <= system_as_of           (single-row immutable version)
seal.ingested_at <= system_as_of      (parent+children aggregate)
```

Only sealed aggregates are visible.

## 7.3 What these modes can answer for backfilled history

- System PIT for a backfilled row starts at its actual import time (2026 onward). It cannot answer "what was known on 2023-05-15?". By design, it never will.
- Market PIT can answer that question only when a version has accepted evidence with a non-null `published_at`. No source publishes one (§2.2, fact 1).
- Until PR #15 is decided, backfilled history is Market-PIT invisible. That is a correct result, but the ML consumers cannot use it.

---

# 8. Publication Evidence Semantics

Business content and publication evidence are separate version chains.

Publication evidence is append-only. Improved or corrected evidence creates superseding evidence; old evidence rows are not updated.

Never invent historical publication time. If it is unknown:

```text
published_at = NULL
```

A rule-derived bound is not an invented time only if it is recorded as its own evidence type, with a versioned rule id, a lower quality rank than official evidence, and explicit per-source acceptance. PR #15 decides whether v1 accepts such bounds.

Because evidence is a separate chain, evidence approved later can be appended to versions that are already stored, without rewriting business history.

A source-system publication locator is not automatically a business-event identity (see Invariant G).

---

# 9. Immutable Aggregate Semantics

Complex datasets such as XBRL filings and TDCC snapshots are immutable aggregates:

```text
draft → children written / validated → seal → visible + immutable
```

Only sealing makes a complex aggregate visible. After sealing, DB constraints and triggers must reject parent/child mutation. Prefer dataset-specific seal tables with real foreign keys.

---

# 10. Ingestion-Time Semantics

Normal callers must not provide an authoritative historical `ingested_at`.

- For single-row immutable versions, trusted storage/DB logic generates system ingestion time.
- For complex aggregates, the authoritative system visibility time is generated when the aggregate is sealed.

A historical migration that must preserve timestamps needs a separate trusted migration path with explicit provenance and tests.

---

# 11. Hash Boundaries

Keep these separate:

```text
business_content_hash
publication_evidence_hash
raw_artifact_hash
```

- `business_content_hash` covers canonical business values only.
- `raw_artifact_hash` is SHA-256 over the raw bytes.

Repeated fetches with identical business content must preserve ingest/raw lineage without creating false business revisions.

---

# 12. Source-Level Capability

PIT capability belongs to a dataset+source combination, recorded in `dataset_sources`. One source's verified semantics must never authorize another source.

Accepted evidence types are declared per `(dataset_code, source)` (ADR-0010). Today the raw-first lifecycle hard-codes `official`. PR #15 moves that declaration into each adapter's source policy.

---

# 13. Cross-Source Policy

Version 1 preserves source histories separately. Do not silently average, merge, overwrite, or pick the latest-ingested source.

If no canonical-source policy exists and multiple sources are possible, require explicit source selection or return separated source results. Any reconciliation policy requires an ADR and permanent regression tests.

Two endpoints of the same source must not write alternating revisions of the same logical key. If they cover different fields, give them distinct source codes or retire one of them from production (PR #17).

Issuer/MOPS summary feeds and exchange result feeds must not be joined into an authoritative event link using security/date/amount/ratio heuristics.

---

# 14. Raw Artifact and Provenance Rules

Every normalized version and evidence record must be traceable to ingestion provenance:

```text
ingest_runs → raw_artifacts → normalized business versions → publication evidence
```

Raw artifacts are content-addressed and immutable. Never overwrite an existing raw artifact with different bytes.

Artifact origin:

```text
official_fetch   default; bytes exactly as returned by the official endpoint
legacy_archive   only where an archive holds something no official re-fetch
                 can provide:
                   - TDCC weeks before forward capture, from the archive
                     named in PR #24
                   - legacy first-seen monthly-revenue rows (2026M02 onward)
                   - XBRL documents for 2020Q1 onward (PR #23)
                 The ingest run records the archive path and file mtime, and
                 for a compressed member the archive entry name as well.
                 fetched_at is the Data Center's read time.
                 Where the archive's own filename disagrees with the payload,
                 the payload wins and the file is rejected (PR #24).

v1 depends on archives that no official endpoint can reproduce: the TDCC
`shareholding` archive, the legacy XBRL documents, and the legacy monthly-revenue
`market.csv`, which now carries both the 2026M02-onward first-seen rows and the
`revswarm` announcement dates written back into it. `revswarm.db` itself is not
a dependency; its result is in the CSV.

By owner decision all of them stay under `~/GitHubLL/my_stock_project/data/raw`
for now, the TDCC archive included. PR #32 must not declare cutover complete
while a v1 rebuild still depends on a path outside this repository.

`stock-data-center/data/raw` is therefore the content-addressed artifact store
and nothing else. Keeping source archives out of it matters beyond tidiness:
`LocalRawArtifactStore.read` validates a `storage_uri` only by checking that it
resolves under the store root, so an archive nested inside that root would let
a `storage_uri` pointing straight at an archive file pass the integrity check.
With the archives outside, that is structurally impossible.
```

---

# 15. XBRL Context Identity

XBRL fact identity must not rely only on concept + dates. Use a non-null canonical `context_hash` covering entity, period, dimensions, and scenario/segment as applicable. Prefer full QName / namespace-aware concept identity.

The MOPS iXBRL documents provide contexts with explicit dimensions, units, and decimals (audit §4.8), so this model is sourced.

---

# 16. Data Domain Ownership and v1 Coverage

`stock-data-center` owns observed source datasets and canonical reusable derived datasets. It does not own model-specific experimental features.

| Domain | v1 | Official source (audit section) | Notes |
|---|---|---|---|
| Security identity / metadata / lifecycle | MERGED (#10, #11) | `t187ap03_L`, `mopsfin_t187ap03_O`, listing/delisting history | current name/industry only; no historical industry changes |
| Trading calendar | PR #16 | TWSE `FMTQIK` (§4.12) | |
| Daily prices | PR #17 | TWSE `MI_INDEX`, TPEx `stk_wn1430` (§4.1) | bid/ask snapshots unsourced |
| Market indices | PR #18 | `MI_INDEX` index sections, TPEx `indexSummary` (§4.2); `MI_5MINS_HIST` for TAIEX OHLC | close / change for all; OHLC for the TAIEX only |
| Official valuation | PR #18 | `BWIBBU_d`, TPEx `pera` (§4.6) | |
| Corporate actions (exchange results) | PR #19 | `TWT49U`, `TWTAUU`, `TWTB8U`, TPEx `exDailyQ`, `revivt` (§4.10) | no announcement/record/payment dates |
| Institutional flows / summary, foreign holding | PR #20 | `T86`, `BFI82U`, `MI_QFIIS`, TPEx `3itrade_hedge`, `3itrdsum`, MOPS `t13sa150_otc` (§4.3–4.4) | |
| Margin / SBL | PR #21 | `MI_MARGN`, `TWT93U`, TPEx `margin_bal`, `margin_sbl` (§4.5) | |
| Monthly revenue | PR #22 | MOPS `t21sc03` `_0`/`_1` (§4.7) | adds KY issuers missing from legacy |
| Financial statements | PR #23 | MOPS `t164sb01` iXBRL (§4.8) | financial industry excluded, as in legacy |
| Issuer dividend declarations | PR #33 | MOPS `t05st09sub` per market-year; OpenAPI `t187ap45_L` / `mopsfin_t187ap39_O` as cross-checks (§4.13) | new domain; reserve split only from ROC 110 |
| TDCC | PR #24 | OpenData + the consolidated archive, 375 weeks (§4.9) | |
| Adjusted prices | PR #25 | derived from exchange reference prices | |
| Canonical derived metrics | PR #26 | derived | ports of legacy calculators |
| Stock tags, XBRL codebook, margin market summary | not in v1 | — | no official source or no consumer |

The field-level inventory is `docs/data_domain_inventory.md`/`.json`. PR #14 aligns it with the audit. No known v1 domain may silently become unmapped.

---

# 17. Canonical Derived Dataset Contract

Every canonical derived dataset requires:

```text
derivation_version
formula/specification
implementation version or git commit
input requirements
PIT-safe input lineage
calendar/timezone convention where relevant
adjustment convention where relevant
```

`computed_at` is computation provenance, not market publication time.

v1 derived datasets are ports of what legacy consumers read (PR #26). Composite legacy "pressure scores" stay downstream.

Materialization in v1 stores one rolling as-of series per metric: each observation date is computed from inputs visible at that date's cutoff. Other PIT contexts are computed on demand, not materialized. Materialized and on-demand results must match for the same PIT context.

---

# 18. Corporate-Action / Price Contract

Official daily OHLC is observed source data. Never rewrite raw historical OHLC to remove a mechanical discontinuity. Never infer a corporate action solely from a large price jump.

```text
raw official OHLC
+ exchange result-feed events (PR #19)
-> versioned adjustment factors
-> adjusted OHLC
```

The v1 adjustment convention is the exchange reference-price ratio:

```text
factor(D) = official_reference_price(D) / close_before(D)
```

It applies to each ex-right/ex-dividend date and each capital-reduction or par-value resumption date D. Because the reference price already removes cash dividends, this convention yields a dividend-reinvested (total-return-style) adjusted series. A price-only series that excludes cash dividends is not in v1.

An event affects an adjusted series only under a PIT context in which that event's evidence is visible.

---

# 19. Delivery Model — Pull-Request-Based Roadmap

A PR is the unit of planning, implementation, review, correctness approval, merge, rollback reasoning, and historical traceability.

Each PR defines:

```text
goal
dependencies
source/data contract (endpoints and exact fields, per §2.4)
schema impact
PIT / evidence impact
provenance impact
migration impact
test plan
acceptance criteria
explicit out-of-scope work
```

Status values: `MERGED`, `IN REVIEW`, `PLANNED`, `BLOCKED`, `SUPERSEDED`.

If a required correctness criterion fails, stop and keep the PR unmerged.

---

# 20. PR Ledger

Status date: 2026-09-14.

| PR | Status | Delivery |
|---|---|---|
| #1 | MERGED | Versioned PostgreSQL PIT schema and v1 storage contract |
| #2 | MERGED | Core Market/System PIT resolver |
| #3 | MERGED | Security metadata + daily-market writer/service contracts |
| #4 | MERGED | PIT-safe monthly revenue writer/service contract |
| #5 | MERGED | Financial/XBRL sealed aggregate + EPS contract |
| #6 | MERGED | TDCC snapshot/distribution contract |
| #7 | MERGED | Institutional, margin, short-selling, and SBL source-data contracts |
| #8 | MERGED | Market indices, corporate actions, and official valuation contracts |
| #9 | MERGED | Raw-first TWSE/TPEx daily-market ingestion pilot (per-security) |
| #10 | MERGED | Current TWSE/TPEx security metadata ingestion |
| #11 | MERGED | Authoritative security listing/delisting/venue lifecycle history |
| #12 | MERGED | Hardened Taiwan corporate-action contract |
| #13 | SUPERSEDED | Official dividend summary pilot; identity unprovable; replaced by PR #19 |
| #14 | PLANNED | Source-reality alignment of inventory and storage contract |
| #15 | PLANNED | Availability-time evidence policy (owner decision) |
| #16 | PLANNED | Trading calendar and coverage validator |
| #17 | PLANNED | Whole-market daily prices |
| #18 | PLANNED | Market indices and official valuation |
| #19 | PLANNED | Exchange corporate-action result feeds |
| #20 | PLANNED | Institutional flows, institutional summary, foreign holding |
| #21 | PLANNED | Margin trading and securities lending |
| #22 | PLANNED | Monthly revenue |
| #23 | PLANNED | Financial statements (iXBRL) |
| #24 | PLANNED | TDCC distribution |
| #25 | PLANNED | Adjusted prices |
| #26 | PLANNED | Canonical derived v1 (legacy calculator ports) |
| #27 | PLANNED | Scheduled forward capture |
| #28 | PLANNED | Public REST API v1 |
| #29 | PLANNED | Python SDK and downstream integration |
| #30 | PLANNED | Operations and observability |
| #31 | PLANNED | Full correctness CI gate |
| #32 | PLANNED | `my_stock_project` cutover and v1 release |
| #33 | PLANNED | Issuer dividend declarations (MOPS OpenAPI), stored as their own domain |

PRs #1–#12 established the storage, PIT, and raw-first foundations. Their writer contracts include some columns that no source populates (§2.3). Those columns stay nullable and unpopulated. They are not dropped, because dropping them brings no correctness gain.

The former planned PRs #14–#33 are renumbered into #14–#32 above; #33 is a new PR, not a survivor of the old numbering. The former "Corporate-Action Identity Research Track", the "Official Reference-Price / Share-Count Pilot", and the "Historical Corporate-Action Backfill" are replaced by PR #19. The former "Source Capability Hook" is absorbed into PR #15. The former "Legacy Migration and Reconciliation" is split into the per-domain reconciliation acceptance of PRs #17–#26 and cutover PR #32. The former cache PRs are deferred (§26.2).

---

# 21. Merged PR Notes That Constrain Later Work

## 21.1 PR #9 — per-security daily pilot

The `STOCK_DAY` / `tradingStock` adapters prove the raw-first lifecycle. They do not suit production capture (§2.2, fact 9). PR #17 decides how they coexist with whole-market adapters without revision flapping.

## 21.2 PR #12 — corporate-action contract

`corporate_action_events` holds the stable `(security, source, source_event_key)`. `corporate_action_versions` holds the action type, dates, amounts, ratios, reference terms, and source terms. PR #19 populates it from exchange result feeds under Invariant G(2). The columns in §2.3 stay NULL.

## 21.3 PR #13 — official dividend summary pilot (SUPERSEDED)

The proposed TPEx identity `(security_code, dividend_year, period)` failed live verification on `mopsfin_t187ap39_O`: 2,483 rows, 65 duplicate groups. Representative collision:

```text
security_code 1591, dividend_year 108, period 1
board_date 1080806  and  board_date 1090505
```

No official summary or announcement feed exposes a correction-stable event ID. The 1591/108/1 collision stays as a permanent regression fixture showing that announcement feeds are rejected by the adapter identity policy.

The information PR #13 wanted (cash dividend, stock distribution, reference prices) is available from exchange result feeds, which PR #19 ingests.

---

# 22. Planned PRs — Alignment and Policy

## PR #14 — Source-Reality Alignment

Status: **PLANNED**. Depends on: none.

Goal: make the storage contract and inventory agree with `docs/source_field_audit.md` before more adapters are written.

Scope:

- Correct `docs/data_domain_inventory.md` and `.json` where they claim unsourced fields:
  - stock-tag effective dates
  - index trade value, and index OHLC for anything other than the TAIEX (§2.3)
  - daily order-book depth (`bid_snapshot`/`ask_snapshot`); the one published level is sourced and belongs in `last_bid_*`/`last_ask_*`
  - monthly-revenue currency as an observation
  - corporate-action announcement/record/payment dates and the earnings/capital-surplus split
- Correct them where they drop sourced fields that consumers read: the monthly-revenue published comparatives, and the TAIEX OHLC that PR #18 adds.
- Mark stock tags, the XBRL codebook, and the margin market summary as not in v1, and add the new domain `dividend_declaration_versions` (PR #33).
- Add a contract test: every column of every observed `*_versions` table maps to an audited source field or is listed as unsourced or partially sourced.

Schema impact: none. Migration: none. PIT impact: none.

Acceptance:

- inventory, audit, and schema agree
- the new test fails if a column is added without a source mapping

Out of scope: dropping unsourced columns.

## PR #15 — Availability-Time Evidence Policy

Status: **PLANNED**. It requires ADR-0020, approved by the owner, before implementation. Depends on: PR #14.

Problem: No source provides a per-row publication instant (§2.2, fact 1). Under official-only evidence, imported history is Market-PIT invisible, and System PIT starts at import time (§7.3). Neither can answer the consumers' question ("what was knowable on date D?").

The legacy system answers it in two ways:

- real first-seen capture dates for monthly revenue from 2026M02 and XBRL from 2025Q4
- statutory deadlines assigned after the fact for older periods (audit §7.1)

Recommended decision: add three evidence types that each `(dataset, source)` opts into.

```text
release_rule    versioned, documented no-later-than instant derived from the
                source's publication schedule or statutory deadline.
                Rules (end of day, Asia/Taipei; exact wording fixed by the ADR):
                  monthly revenue          -> the 10th of the next month
                  financial statements,    -> Q4 03/31, Q1 05/15, Q2 08/15, Q3 11/15
                    general industry          (legacy window ends and train_eps
                                               cutoffs; Q2/Q3 are one day after
                                               the 08/14, 11/14 statutory dates)
                  financial statements,    -> out of v1 scope (PR #23)
                    financial industry
                  exchange daily datasets  -> 03:00 the next calendar day
                                              (owner decision; the exchange
                                               publishes same-day rows before
                                               they settle, audit §7, and the
                                               legacy 23:30 run needed its 03:00
                                               retry on 5 of 27 observed trade
                                               days, audit §7.2)
                  TDCC weekly              -> 12:00 on the first Sunday after the
                                              data date (owner decision; the
                                               legacy weekly job ran Sunday 10:20)
                Every rule is at least the statutory deadline moved to the next
                business day (PR #16 calendar). For example, 2021Q2 resolves to
                2021-08-16, not 08-15, and 2026M04 revenue to 2026-05-11 (audit §7.1).
                evidence_source records rule id + version.

capture_bound   the Data Center's first successful fetch instant of an
                artifact containing the version (a proven upper bound).

legacy_capture_bound
                the legacy scraper's recorded first-seen date, converted to a
                conservative instant: the end of the scheduled run that first
                contained the row (22:45 monthly-revenue run; 23:50 XBRL run,
                tightened by file mtime). Applies only to the legacy daily
                job's captures in periods where the record is real (audit
                §7.1). It never applies to the synthetic deadline values, and
                never to backfill-run dates after the capture window closed
                (for example the XBRL 2026-08-01 and 2026-08-17 files).
                evidence_source names the legacy file.

press_report_bound
                a publication date reconstructed from a dated secondary record
                that reports the filing, resolved at the end of that day,
                Asia/Taipei. A news article dated D proves the value was public
                on D, so this is a real publication bound, not a schedule
                estimate. It ranks below the two capture types because it is
                day-precision and reconstructed, with a measurable residual
                error rate.
                v1 source: the `revswarm` monthly-revenue dataset, 114,910 of
                the 128,063 rows in 2020M01-2026M01 (89.7%), verified by its
                three contamination guards and cross-checked at 99.10%
                agreement against today's MOPS values (audit §7.4).
                evidence_source names the dataset, the engine, and the
                verifier recorded per row.
```

Rules:

- Precedence for a version: `capture_bound`, then `legacy_capture_bound`, then `press_report_bound`, then `release_rule`. A `release_rule` never makes a version visible earlier than a capture or a press report proves.
  - Forward-captured data uses the Data Center's capture time.
  - 2026M02 onward monthly revenue and 2025Q4 onward XBRL use the legacy first-seen date.
  - Older history uses the rule.
- If a daily-job capture is later than the rule instant (late filer), the rule is falsified for that row, and only the capture evidence is recorded.
- A backfill-run capture is not first-seen evidence, so it cannot falsify the rule. Such rows resolve like uncaptured history.
- For monthly revenue from 2026M02, a `_0` row absent from the legacy record was not public at the last legacy run (the 15th). It gets no rule evidence and resolves at its Data Center capture.
- A revision first captured after the rule instant receives only `capture_bound`. A correction is never visible before it was actually seen.
- Documented limitation: history before any capture record stores latest-corrected values, made visible at rule instants. This allows correction look-ahead, which the legacy system also has. It affects monthly revenue before 2026M02, XBRL before 2025Q4, and all exchange daily data before forward capture. PR #27 reports forward-capture revision rates so the size of this effect is measured.
- Move source policy (capability and accepted evidence types) from generic raw-first orchestration into each adapter's source declaration. This absorbs the former "source capability hook" PR.

Alternative: keep official-only evidence. v1 then exposes history through System PIT only, and historical Market-PIT queries return nothing.

Schema impact: none expected (`evidence_type` and the allowlists exist). Migration: allowlist data only.

Acceptance:

- ADR-0020 approved. AGENTS.md §31–32 (unknown publication and backfill rules) updated to match.
- Each rule has an id, a version, and a cited official schedule or statute. Permanent tests cover weekends, holidays, year boundaries, and deadlines that fall on non-business days.
- Revision-after-rule regression.
- Exchange daily rules are validated against forward captures: the data for trade date D is fetchable at the rule instant.

Out of scope: inventing instants for sources without a documented schedule or statute.

Coupling note: adapter PRs #16–#24 are not blocked by this PR. Until #15 lands they emit `unknown` evidence. Approved evidence is appended later without touching business versions (§8).

---

# 23. Planned PRs — Official Source Adapters and History (2020-01-02 onward)

Common rules for PRs #16–#24:

- One adapter per official endpoint, used both for history re-fetch and for daily operations.
- Throttled, resumable, checkpointed history runs (the PR #9 lifecycle). Rough request budgets are listed per PR.
- Header variants from the audit are explicit, tested parser cases. An unknown header quarantines the artifact.
- Acceptance includes a reconciliation report against the legacy `stock_db` for 2020-01-02 → 2026-09-11, with every difference classified. Unit normalization (lots → shares, thousand TWD → TWD) is applied before comparison.

## PR #16 — Trading Calendar and Coverage Validator

Status: **PLANNED**. Depends on: PR #14.

Source contract: TWSE `FMTQIK` (one request per month, listing every actual trading day), cross-checked with TWSE `holidaySchedule` where available and with the dates of whole-market daily files. TPEx trading days must equal TWSE's for 2020 onward, or a difference must come from an official TPEx source.

Schema impact: new observed calendar table (market, trading date, source, lineage).

Acceptance:

- the 2020-01-02 → 2026-09-11 calendar matches the trade dates in the legacy archive, or each difference is explained
- typhoon closures (for example, 2024-07-24/25) appear as closures
- the coverage report separates non-trading days from missing data and does not rely on today's security universe

## PR #17 — Whole-Market Daily Prices

Status: **PLANNED**. Depends on: PR #9 lifecycle, PR #11, PR #16.

Source contract (audit §4.1):

- TWSE `MI_INDEX?type=ALLBUT0999`: stock section only. Index sections are handled in PR #18, reusing the same artifact.
- TPEx `stk_wn1430`, including its three header variants.
- One resource per (market, trade date).

History: about 3,300 requests.

Schema impact: none expected. `daily_price_versions` covers the sourced fields, and bid/ask snapshots stay NULL.

Acceptance:

- per-date row counts and OHLC, volume, trade value, and trade count equal legacy `daily_quotes`
- no revision flapping between these adapters and the PR #9 per-security adapters for the same key (distinct source codes or pilot retired from production)
- idempotent re-runs; a changed file creates a revision
- a report of priced securities that have no metadata row (ETFs, TDRs, preferred shares)

Out of scope: adjusted prices; per-security pilots in production.

## PR #18 — Market Indices and Official Valuation

Status: **PLANNED**. Depends on: PR #16, PR #17.

Source contract (audit §4.2, §4.6):

- `MI_INDEX` index sections and TPEx `indexSummary`. Index identity is `(source, published index name)`: close, change points, and change percent only.
- TWSE `MI_5MINS_HIST` (`發行量加權股價指數歷史資料`), one calendar month per request, for TAIEX `open_value`/`high_value`/`low_value`. This is a source the legacy system never fetched, so it is new data, not a legacy port.
- TWSE `BWIBBU_d` and TPEx `pera`.

History: about 4,900 requests (TWSE indices reuse the `MI_INDEX` artifacts), plus about 80 `MI_5MINS_HIST` month requests.

The PR first spikes TPEx for an OTC index-OHLC endpoint. If none exists, OTC index OHLC stays NULL and the audit records the negative result.

Acceptance:

- legacy `market_indices` (close, change points) and `pe_ratio` reconcile
- TAIEX close from `MI_5MINS_HIST` equals the `MI_INDEX` close on every trade date, or the row is quarantined
- the 5-column TWSE `BWIBBU_d` file of 2025-06-24 is parsed by an explicit variant or quarantined
- TPEx `財報年/季` before 2025-01-02 becomes NULL, not an error

Out of scope: index trade value; OHLC for any index other than the TAIEX; index-rename linking beyond explicit official evidence.

## PR #19 — Exchange Corporate-Action Result Feeds

Status: **PLANNED**. Depends on: PR #9, PR #12, PR #17.

Supersedes: PR #13, the former reference-price pilot, and the former corporate-action backfill.

Source contract (audit §4.10):

- TWSE `TWT49U` + `TWT49UDetail`
- TWSE `TWTAUU` + `TWTAVUDetail`
- TWSE `TWTB8U` (detail fields verified in this PR)
- TPEx `exDailyQ`
- TPEx `revivt`
- The TPEx par-value-change endpoint is verified in this PR. Otherwise that family stays unsupported for TPEx.

All TWSE result feeds are requested with `response=json`, never `response=csv`. The CSV rendering flattens 詳細資料 to the link label `除權息資料`, which destroys the `"{code},{yyyymmdd}"` locator that Invariant G(2) depends on (audit §4.10). This is also why the legacy CSV archive is a value-reconciliation baseline only and can never establish event identity.

History: the TWSE feeds accept a whole-year date range, so about 7 requests per feed for 2020-2026, plus one Detail request per event.

Identity: Invariant G(2). ADR-0019 records the result-feed identity rule.

Mapping:

```text
息 -> ex_dividend        權 -> ex_right        權息 -> ex_right_dividend
capital reduction -> capital_reduction; kind from 減資原因;
                     old/new shares from 每壹仟股換發新股票; cash from 每股退還股款
par-value change  -> stock_split / reverse_split when share exchange terms are
                     published; otherwise other, with reference prices kept
cash_dividend_per_share, free_share_ratio (÷1,000), rights_ratio (÷1,000),
subscription_price, close_before, official_reference_price,
official_rights_dividend_value; remaining source columns -> source_terms
```

The fields in §2.3 stay NULL.

History: 2020 onward. There are about 6,200 TWSE detail requests. TPEx rows are self-contained.

Acceptance:

- zero duplicate `(feed, code, locator date)` over the full history of each feed
- correction regression: same locator with changed terms produces a new revision of the same event; a removed row produces a retraction
- legacy `dividend` (6,182 TWSE rows) reconciles on date, close before, reference price, rights+dividend value, and type
- announcement-feed rejection test using the 1591/108/1 fixture

Out of scope: MOPS summary normalization; adjustment factors (PR #25).

## PR #20 — Institutional Flows, Institutional Summary, Foreign Holding

Status: **PLANNED**. Depends on: PR #16, PR #17.

Source contract (audit §4.3–4.4): `T86`, `BFI82U`, `MI_QFIIS`; TPEx `3itrade_hedge`, `3itrdsum`, MOPS `t13sa150_otc`. History: about 9,800 requests.

Acceptance: legacy `institutional_investors`, `institutional_summary`, and `foreign_holding` reconcile. The broken TPEx summary artifact of 2026-07-10 is re-fetched or quarantined.

## PR #21 — Margin Trading and Securities Lending

Status: **PLANNED**. Depends on: PR #16, PR #17.

Source contract (audit §4.5): `MI_MARGN`, `TWT93U`; TPEx `margin_bal`, `margin_sbl`. TWSE utilization ratios stay NULL. History: about 6,500 requests.

Acceptance: legacy `margin_trading` and `margin_sbl` reconcile after lots → shares.

Out of scope: the market summary block (`margin_summary`, which no consumer reads).

## PR #22 — Monthly Revenue

Status: **PLANNED**. Depends on: PR #4 contract, PR #11.

Source contract (audit §4.7): MOPS `t21sc03` pages for `sii`/`otc` × `_0`/`_1` × month, from 2020M01. History: about 330 requests. Revenue is converted ×1,000 to TWD.

Schema impact: add nullable published comparatives to `monthly_revenue_versions`. They are in the same source row and read by consumers:

```text
revenue_last_month, revenue_last_year_month, mom_pct, yoy_pct,
cumulative_revenue, cumulative_revenue_last_year, cumulative_yoy_pct, note
```

The canonical derived `monthly_revenue_growth:v1` leaves v1.

Recovered publication dates (audit §7.4): for 2020M01-2026M01, read them from the legacy `market.csv`, which `revswarm` has already written back — 114,910 of 128,063 rows (89.7%) carry a real announcement date. The CSV is the interface; `revswarm.db` is not a live dependency of this PR.

The CSV keeps only the date, so a row dated on the 10th cannot be told apart from a row that kept the statutory fallback. The rule that follows from that:

```text
publish_time != the 10th of the next month  ->  press_report_bound at end of that day
publish_time == the 10th of the next month  ->  release_rule
```

This costs almost nothing and can never create look-ahead. 40,188 rows sit on the 10th; for the 36,492 where the 10th is a business day the release rule resolves to that same day, so the timestamp is identical. Only the 3,696 rows (2.9%) whose 10th falls on a weekend resolve 1-2 days later than the recovered date says, which is late, not early.

If the per-row provenance is wanted later — `engine`, `verified`, `raw_title`, `url` — the upgrade is to export a provenance column from `revswarm.db` alongside the date, not to read the database at ingest time.

Legacy first-seen import (audit §7.1): for 2026M02 onward, import the legacy `market.csv` rows as `legacy_archive` observations of the first-captured values, with `legacy_capture_bound` evidence from their `publish_time` dates. When the official re-fetch differs, it becomes a later revision whose evidence is the Data Center's own capture time. The synthetic `publish_time` values before 2026M02 are not imported as evidence.

Acceptance:

- legacy `monthly_revenue` reconciles
- KY issuers from `_1` pages appear as new coverage
- a correction between two fetches creates a revision
- published comparatives are stored exactly as published and never reconciled against our own series; the 2026M06/M07 pair, where 11 of 1,846 companies disagree, is a regression fixture (audit §7.3)
- for 2026M02 onward, each first-seen row resolves under Market PIT no earlier than the end of its legacy 22:45 run; rows the re-fetch shows as corrected resolve to the first-captured value before the correction's capture
- for 2020M01-2026M01, a row whose `publish_time` differs from the 10th of the next month resolves at the end of that day; a row on the 10th resolves at the release rule
- no row in that window ever resolves earlier than the release rule would place it
- the `revswarm` announced-revenue cross-check runs at import and its agreement rate is recorded; a drop below the 99.10% measured on 2026-09-15 fails the import

Out of scope: recovering first-published values before 2026M02.

## PR #23 — Financial Statements (iXBRL)

Status: **PLANNED**. Depends on: PR #5 contract, PR #11.

Source contract (audit §4.8): MOPS `t164sb01`, one document per (security, year, quarter, report type), from 2020Q1.

**Financial-industry issuers are out of scope**, matching the legacy system. v1 is the legacy scope (§1.1), and excluding them keeps the financial-industry account taxonomy and its separate statutory deadlines out of v1. Their daily prices, monthly revenue, and every other domain are unaffected; only their financial statements are not ingested. §26.3 records the exclusion.

History source (owner decision; audit §7.1):

- 2020Q1–2025Q3: import the legacy documents as `legacy_archive` versions. They are a February 2026 re-fetch, so their content equals what a fresh fetch returns today, and their filename dates are synthetic deadlines carrying no capture evidence — those resolve by the release rule. A sample is re-fetched officially and compared; a mismatch rate above the threshold set in the PR fails the import and falls back to a full re-fetch.
- 2025Q4 onward: import the legacy documents as `legacy_archive` versions.
  - Documents the daily job captured by the window end (03/31, 05/15, 08/15, 11/15) get `legacy_capture_bound` evidence from the run date and file mtime.
  - Documents dated by later backfill runs (2026-08-01, 2026-08-17) get no capture evidence and resolve by the release rule.
- An official re-fetch that differs becomes a later revision (an amendment) with the Data Center's own capture evidence.

This replaces the full 45,000-request re-fetch, which at the 3-second interval forced by the 2026-07-02 MOPS block would take about 38 hours and risk another block.

Acceptance:

- legacy `*_xbrl` and `quarterly_reports_xbrl` values reconcile through account code ↔ concept QName
- report category (consolidated or individual) is preserved
- the PR #5 EPS contract holds
- the archive-vs-official sample comparison runs and its mismatch rate is recorded in the audit
- for 2025Q4 onward, daily-job captures resolve under Market PIT no earlier than their legacy capture bound
- no financial-industry issuer has a financial-statement version after the import

Out of scope: financial-industry issuers; recovering original pre-amendment filings before 2025Q4.

## PR #24 — TDCC Distribution

Status: **PLANNED**. Depends on: PR #6 contract, PR #16.

Source contract (audit §4.9):

- OpenData `id=1-5` weekly, from the first forward capture onward
- `legacy_archive` artifacts up to the first forward capture, from the single consolidated archive `my_stock_project/data/raw/shareholding`: 426 files, **375 weeks** from 2019-06-28 to 2026-09-11, of which 348 fall inside the v1 window. Consolidated 2026-09-15 from three directories (audit §4.9); `shareholding.bak` is the old filtered copy and is not a source.
- every file carries the same six-column OpenData header, so one parser handles all of them
- the portal per-security query only for repairs inside its roughly one-year window

New runtime dependency: a 7z reader (`py7zr`), since 2021 onward is stored as `.7z`. PR #24 adds it to `pyproject.toml`; it is not declared today.

The importer keys on the 資料日期 column, never on the filename: `20200619.CSV` and `20200619.zip` both contain 20200612 data, and trusting the name would invent a week and drop the real one. It must also handle the slash date format in `20190628.zip`, the ten double-BOM files, mixed extension case, and the 51 content dates that have duplicate copies (every pair agrees exactly, so either may be kept).

Acceptance:

- all 375 weeks import, each keyed by its content date
- a file whose name disagrees with its content date is rejected with that fact named, not silently renamed
- every remaining interval of 10 or more days resolves to a Lunar New Year closure; any other gap fails the import
- 2026-07-09 imports as the complete 4,003-security file, not as a 1,849-security reconstruction
- nothing is read from `shareholding.bak`, whose 155 post-2023-09-15 files are filtered to about 1,767 securities
- legacy `shareholding` reconciles on the 340 weeks it holds

---

# 24. Planned PRs — Derived Data

## PR #25 — Adjusted Prices

Status: **PLANNED**. Depends on: PR #16, PR #17, PR #19.

Method: §18 reference-price ratio. Backward cumulative factors are computed per security. Raw OHLC is untouched.

Acceptance:

- every daily close-to-close gap above a documented threshold (2020 onward) is classified as explained by an exchange result-feed event, explained by another documented market event, or unexplained; the unexplained list is reviewed
- continuity at event dates
- no event affects the series before its evidence is visible in the requested PIT context

Out of scope: price-only (cash-excluded) series; pre-2020 history.

## PR #26 — Canonical Derived v1 (Legacy Calculator Ports)

Status: **PLANNED**. Depends on: PRs #17–#25 as each metric requires.

Definitions, each ported from the legacy calculator and reconciled to its legacy table:

```text
technical_indicators:v1          MA/VMA 5-240, KD, RSI 6/12, MACD, Bollinger
                                 (raw close, as legacy consumers were trained)
institutional_streaks:v1         foreign/trust/dealer streak days
institutional_cumulative_flow:v1 legacy trust/dealer "holding" proxies
shareholding_concentration:v1    large/mid/small holder ratios and WoW
valuation_metrics:v1             TTM EPS, PE, PE percentile, ROE
margin_metrics:v1                utilization and WoW changes
short_interest_metrics:v1        SBL/short ratios and WoW changes
```

Materialization follows §17: the rolling as-of series only.

Acceptance: reconciliation to legacy tables. Intentional differences caused by PIT-correct inputs are listed and explained.

Out of scope: composite pressure scores (downstream); adjusted-price variants of the indicators (a later derivation version).

---

## PR #33 — Issuer Dividend Declarations

Status: **PLANNED**. Depends on: PR #19.

Why: the earnings / legal-reserve / capital-surplus split exists in no exchange result feed. MOPS is its only public source.

Source contract (audit §4.13, verified live 2026-09-15):

```text
primary   POST mopsov.twse.com.tw/server-java/t05st09sub
          encodeURIComponent=1&step=1&firstin=1&off=1
          TYPEK={sii|otc}  YEAR={ROC year}  qryType=1
          one whole-market big5 HTML table per request

cross-check  openapi.twse.com.tw/v1/opendata/t187ap45_L      JSON, 股利年度 114-115
             www.tpex.org.tw/openapi/v1/mopsfin_t187ap39_O   JSON, frozen at 出表日期 1100804
```

`TYPEK` has four values — `sii` 上市, `otc` 上櫃, `rotc` 興櫃, `pub` 公開發行 with no board. The four sets are disjoint, and membership follows the company's status *at query time*, not at the dividend year: 58 of the 792 companies returned by `otc`/民國109 were listed on TPEx only after 2021. So `sii` + `otc` covers the whole v1 universe including its members' pre-listing years, and `rotc`/`pub` add only companies outside it (audit §4.13).

History: 2 markets × ROC years 107-115 = 18 requests, then 2 per day. `t05st09sub` is on the host that blocked the legacy scraper on 2026-07-02, so the 3-second interval applies; at 18 requests that is under a minute.

The TPEx OpenAPI dataset stopped updating at the 民國110 header change. That is TPEx's republication going stale, not a source being withdrawn: MOPS serves `TYPEK=otc` for 110-115 normally. The PR uses MOPS for both markets and keeps the two OpenAPI feeds only as an independent cross-check of overlapping years.

Header variant, mandatory to handle:

```text
ROC <= 109   6 sub-columns under 股東配發內容 (19 cells/row)
             法定盈餘公積 and 資本公積 published as ONE combined figure,
             for cash and for stock dividends alike
ROC >= 110   8 sub-columns (21 cells/row), the two reserves split apart
```

`盈餘轉增資配股` is therefore available for every year. Only the reserve split is unavailable before 民國110. A row from those years sets the legal-reserve component to NULL and records the combined figure in the capital-surplus column with an explicit `reserves_combined` flag. No PR may present a pre-110 capital-surplus figure as reserve-pure.

Storage: a new `dividend_declaration_versions` table. `corporate_action_versions` is untouched and its split columns stay NULL (§2.3); this domain is published as its own series.

Identity: `(security, dividend_year, dividend_period_text, sequence)`. TPEx OpenAPI rows, which lack 股利所屬期間, additionally need the board-resolution date, and two groups still collide and quarantine (audit §4.13).

Time model:

```text
information_as_of  董事會決議（擬議）股利分派日
published_at       capture_bound from forward capture; for the years imported
                   once at backfill, the release rule is the capture date, since
                   no feed publishes a per-row release instant
```

Acceptance:

- both header variants parse; a 19-cell row and a 21-cell row from the same import round-trip correctly
- the `reserves_combined` flag is set for every ROC ≤ 109 row and for every row taken from the frozen TPEx OpenAPI feed
- only `TYPEK=sii` and `TYPEK=otc` are requested; `rotc` and `pub` are rejected at the adapter boundary (§1.1)
- identity is unique within each (market, year) import; any duplicate quarantines
- re-importing a past year after a new listing adds the newly visible rows as new records for that period; it must not diff them against existing rows as corrections, and a smaller row set must not be read as retraction
- a 決議進度 change on re-capture produces a new version under the same identity, not an in-place update
- overlapping years reconcile against the TWSE OpenAPI feed (114-115) and the frozen TPEx feed (107-110); differences are listed
- `corporate_action_versions` and `security_events` are unchanged by this PR
- the API marks the series as declaration data with no verified link to an executed event

Out of scope: linking a declaration to an executed ex-dividend event; the `rotc` and `pub` markets, excluded from the v1 universe by §1.1; the TPEx-only 董監酬勞 and 員工紅利 columns of the OpenAPI feed; `qryType=2` (股利所屬年度) as a second axis.

---

# 25. Planned PRs — Operations, API, Cutover

## PR #27 — Scheduled Forward Capture

Status: **PLANNED**. Depends on: PRs #16–#24.

Daily, weekly, monthly, and quarterly jobs run the adapters. They include retries, calendar-based missing-data alerts, and correction detection by re-fetching recent periods. The report on revision rates per dataset quantifies the backfill limitation described in PR #15.

Acceptance: two weeks of unattended runs with complete coverage and resumable failures.

## PR #28 — Public REST API v1

Status: **PLANNED**. Depends on: PR #15 decision, data PRs.

Expose correct Data Center semantics without exposing tables. The endpoints cover the queries legacy consumers issue: daily panels, indices, valuation, chip data, monthly revenue, financial facts and summaries, TDCC, corporate actions, adjusted prices, and derived metrics. Every response carries its PIT context and provenance. Unsourced columns are omitted or explicitly flagged as unavailable.

## PR #29 — Python SDK and Downstream Integration Contract

Status: **PLANNED**. Downstream repositories need no PostgreSQL credentials.

## PR #30 — Operations and Observability

Status: **PLANNED**. Covers ingest progress, quarantine, coverage and reconciliation status, and PIT/DB latency.

## PR #31 — Full Correctness CI Gate

Status: **PLANNED**. CI covers PostgreSQL 18, Alembic, PIT, publication evidence, source capability, raw-first restart, corporate-action identity and revisions, adjusted prices, derived datasets, and the API. Corporate-action CI includes the result-feed duplicate scan, the correction regressions, and the announcement-feed rejection fixture.

## PR #32 — `my_stock_project` Cutover and v1 Release

Status: **PLANNED**. Depends on: PRs #25–#31.

`my_stock_project` consumes the API/SDK and stops maintaining a competing authoritative database. The final reconciliation compares every legacy table that consumers read with the API results, and every difference is classified.

---

# 26. Not in v1

Three different things were being kept in one list. They are separated here
because they need different treatment: the first group must never be planned
again, the second waits on a stated trigger, and only the third could become a
later version.

## 26.1 Not obtainable — no source exists

No future version can deliver these without a source that does not exist today.
A PR that proposes one must first produce the endpoint and field label, or be
rejected.

| Item | Evidence |
|---|---|
| Corporate-action announcement, record, and payment dates | Published by no inspected feed, the announcement feeds included (audit §4.10, §4.13) |
| Linking a dividend declaration to its executed ex-dividend event | Neither declaration feed carries an ex-date or a locator; the MOPS page says so itself (audit §4.13) |
| Legal-reserve vs capital-surplus split before 民國110 | MOPS published the two reserves as one combined figure until the 民國110 header change (audit §4.13) |
| Index trade value; OHLC for any index other than the TAIEX | Not in `MI_INDEX` or `indexSummary`; no TPEx equivalent of `MI_5MINS_HIST` found (audit §4.2) |
| Historical security name and industry changes | No official history source identified; `t187ap03_*` is a current snapshot |
| Order-book depth (`bid_snapshot`, `ask_snapshot`) | The daily files publish one level only, already stored in `last_bid_*`/`last_ask_*` (audit §4.1) |
| First-published *values*: monthly revenue before 2026M02, iXBRL before 2025Q4, exchange daily data before forward capture | MOPS serves the latest corrected values, and no capture recorded the earlier ones (audit §7.1). For monthly revenue the `revswarm` headline figures are rounded to 0.01億 — enough to detect that a correction happened, not to restore the original 千元 number (audit §7.4). |
| Publication *dates* for the 10.3% of 2020M01-2026M01 monthly revenue with no verified report | `revswarm` found no dated report that passed its guards. These fall through to the release rule. Re-running its later engines could reduce the gap, so this is a coverage limit rather than a hard one. |
| TDCC history before the portal window, if the archive were lost | The portal serves about 51 weeks and OpenData only the latest. The 375 archived weeks cannot be re-fetched from any official endpoint, so the archive is the only copy (audit §4.9). This is a preservation constraint, not a missing dataset: v1 has every week it needs. |

## 26.2 Waiting on a stated trigger

Obtainable, but deliberately not built until the trigger fires. No trigger, no PR.

| Item | Trigger |
|---|---|
| Cache abstraction, Redis backend | The API/DB latency measured in PR #30 proves insufficient. Invariants A–C apply if one is ever added. |
| Stock tags | An official source with effective dates appears. A third-party current snapshot is not one. |
| XBRL codebook, margin market summary | A consumer reads them. Neither has one today. |
| Price-only adjusted series; adjusted-price indicator variants | A consumer needs them. Not required to reproduce the legacy consumers. |

## 26.3 Obtainable, out of v1 by decision

These have a source and could be built. They are out of v1 to keep it to the
legacy scope. This is the only group a later version would draw from.

| Item | What it would take |
|---|---|
| Financial-industry financial statements | A financial-industry account taxonomy and its separate statutory deadlines (audit §7.1). MOPS serves the filings today. |
| Pre-2020 history | The exchange feeds accept earlier date ranges and could be re-fetched. TDCC cannot, except for the 27 weeks the `TDCC` archive holds back to 2019-06-28; anything earlier is gone. |
| `rotc` and `pub` markets | MOPS serves all four `TYPEK` values. Excluded by §1.1, not by availability. |
| `qryType=2` (股利所屬年度) as a second dividend-declaration axis | One more request per market-year against `t05st09sub` (audit §4.13). |
| TPEx-only declaration columns: 董監酬勞, 員工紅利 | Present in the frozen TPEx OpenAPI feed; no TWSE counterpart, so the series would be one-sided. |

---

# 27. Cross-PR Acceptance Rules

## 27.1 Temporal correctness

Never invent historical `published_at`, `ingested_at`, or knowledge. Rule-derived bounds exist only as PR #15 defines them.

## 27.2 Raw-first ingestion

```text
fetch → durable raw artifact → checkpoint → parse → normalize → canonical write
```

The artifact origin is `official_fetch` unless §14 allows `legacy_archive`.

## 27.3 Failure classification

Quarantine invalid or ambiguous source data, unknown header variants, and domain validation failures. Operational failures stay resumable once raw bytes are captured.

## 27.4 Cross-source reconciliation

Final truth must be reproducible from stored source histories and independent of import order.

## 27.5 Migration safety

A downgrade must either safely represent stored history or fail explicitly before mutation.

## 27.6 Corporate-action / price rule

Raw prices are source facts. Continuity belongs in adjustment factors and adjusted series.

## 27.7 Corporate-action identity gate

Before a real-source adapter calls `register_corporate_action_event()`, it must prove:

```text
1. the source is an exchange result feed (Invariant G(2)); announcement feeds are rejected;
2. source_event_key is "<feed>:<locator date>" and contains no revision content;
3. a full-history duplicate scan finds zero duplicate keys;
4. same locator with changed terms -> same event, new revision;
5. separate executed events -> distinct keys;
6. a removed row -> retraction, not deletion.
```

If any item cannot be established, stop before normalized event registration.

## 27.8 Source-field rule

Every stored or promised field satisfies §2.4. Every adapter PR updates `docs/source_field_audit.md`.

## 27.9 Legacy reconciliation

Every adapter and derived PR reconciles against the legacy `stock_db` over 2020-01-02 → 2026-09-11 and classifies every difference. Matching the legacy data is evidence, not the goal: a PIT-correct difference is acceptable when it is explained.

## 27.10 Scope discipline

Each PR states what it intentionally does not do. Do not absorb unrelated refactors or optimizations.

## 27.11 Review / merge evidence

Before merge, provide as applicable:

```text
focused regression suite
full test suite
Ruff/lint
git diff --check
Alembic check
migration round-trip or guarded downgrade test
opt-in live-source verification
legacy reconciliation report
acceptance report (docs/pr_reports/pr-<N>-acceptance-report.md)
```

---

# 28. Suggested Source Layout

```text
stock-data-center/
├── README.md
├── ROADMAP.md
├── AGENTS.md
├── pyproject.toml
├── docker-compose.yml
├── alembic.ini
├── migrations/
├── data/
│   └── raw/                    content-addressed artifact store only
│       └── <ab>/<sha256>       written by LocalRawArtifactStore; no source
│                               archive and no processed/ staging layer
├── docs/
│   ├── source_field_audit.md
│   ├── data_domain_inventory.md
│   ├── pit_semantics.md
│   ├── schema.md
│   ├── real_source_ingestion.md
│   ├── derived_data.md
│   ├── phase_reports/
│   ├── pr_reports/
│   └── decisions/
├── src/
│   └── stock_data_center/
│       ├── api/
│       ├── pit/
│       ├── derived/
│       ├── ingestion/
│       │   ├── adapters/
│       │   └── reconciliation/
│       └── <domain packages>/
└── tests/
    ├── unit/
    └── integration/
```

---

# 29. Definition of Done for v1

Version 1 is complete when:

- PostgreSQL 18 is the sole authoritative data store.
- Every domain in §16 marked for v1 covers 2020-01-02 through cutover. Data comes from official endpoints, except TDCC weeks that predate forward capture, which come from the two documented archives (§14).
- Each domain has a legacy reconciliation report with every difference classified.
- Forward capture runs unattended on the trading calendar.
- Market PIT is usable for history under the approved PR #15 policy. If the owner rejects rule-derived evidence, the API documents that history is System-PIT only.
- System PIT reconstructs actual ingestion exactly.
- Business revisions and publication-evidence revisions are separate.
- Complex aggregates are concurrency-safe and seal-protected.
- XBRL full context identity is supported.
- Corporate actions come only from exchange result feeds that pass the identity gate. Announcement feeds remain un-normalized.
- Adjusted prices use the reference-price convention and have reviewed discontinuity reports.
- Canonical derived v1 metrics reproduce the legacy calculators, with explained PIT differences.
- No API field is presented as data unless a verified source field populates it.
- Downstream ML repos use only the API/SDK. Redis is not required.

---

# 30. Core Design Principles

1. Correct historical visibility before convenience.
2. PostgreSQL is truth; any cache is a disposable optimization.
3. Market time and Data Center knowledge time are separate clocks.
4. System PIT means actual complete ingestion history.
5. Business revisions and publication evidence are separate.
6. Complex aggregates become visible only when sealed and remain correct under concurrency.
7. Sources remain separate unless an explicit reconciliation policy exists.
8. Data Center may own deterministic cross-repository canonical derived datasets.
9. Every canonical derived dataset has a derivation version and PIT-safe lineage.
10. `computed_at` is derivation provenance, not market publication time.
11. Model-specific features remain in ML repositories.
12. Downstream systems never reproduce PIT rules.
13. Real-data import is raw-first, source-explicit, idempotent, and reconciliation-driven.
14. Historical backfill never invents publication time or System-PIT history. Rule-derived bounds are labeled evidence, never silent defaults.
15. Raw official prices are never rewritten to hide discontinuities.
16. Corporate-action identity must survive corrections. Announcement feeds lack it; exchange result feeds carry it through the executed event date or locator.
17. Current-snapshot uniqueness is evidence to test, not proof of identity.
18. If stable source identity is not available, fail closed rather than manufacture a synthetic event.
19. No stored or promised field without a named source field: design follows what sources actually publish.
20. v1 is the legacy scope rebuilt with PIT, not a superset of every conceivable field.
21. Measure before optimizing: no cache until latency is measured.
