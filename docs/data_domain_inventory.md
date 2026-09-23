# Data Domain Inventory

## Audit scope

This is the complete known v1 disposition of the legacy PostgreSQL schema in
`my_stock_project`, reviewed on 2026-09-11 from `common/schemas.py::SCHEMA_COLS`
and the retained database schema dump. The retained DB audit found 26 tables;
`SCHEMA_COLS` contains 27 categories and 416 declared fields, including four
deprecated pre-XBRL categories. No retained table or declared field is left
unmapped.

The normative, field-by-field contract is
[`data_domain_inventory.json`](data_domain_inventory.json). Each of its 416
records names the legacy table and field, disposition, target domain/field, and
reason. It also records the canonical SHA-256 fingerprint of the audited
`SCHEMA_COLS` `(table, field)` pairs. Automated tests compare the complete
contract against that frozen legacy fingerprint, counts, uniqueness, and the
allowed dispositions.

Disposition terms:

- **observed**: source-published data stored as an append-only Data Center version;
- **canonical derived**: deterministic, model-independent data registered by
  derivation version and represented by `derived_metric_versions`, or computed
  virtually under the same definition contract;
- **downstream**: model/experiment-specific logic owned by an ML repository;
- **raw-only**: parser coordinates retained with the immutable raw artifact;
- **deprecated**: intentionally excluded from v1.

## Per-column source coverage

The legacy mapping above answers "where did this legacy field go?". It does not
answer the opposite question, which is the one a step planning an adapter needs:
*does an official source actually publish this stored column?* That is
`storage_contract` in
[`data_domain_inventory.json`](data_domain_inventory.json). Every column of
every table that holds observed content is classified there — which is not only
the `*_versions` tables, since `financial_facts`, `tdcc_distribution`, its
codebook tables, `corporate_action_events`, and `security_transfer_events` hold
source values too:

| Coverage | Meaning |
| --- | --- |
| `sourced` | an inspected official endpoint publishes the value for the whole v1 window and both markets |
| `partially_sourced` | published for only some dates, markets, or securities |
| `unsourced` | no inspected source publishes it; the column stays NULL and no v1 step may promise it |
| `internal` | identity, interval boundary, or provenance linkage not expected from a source field |

Seven structural columns (`id`, `source`, `business_content_hash`,
`ingested_at`, `raw_artifact_id`, `ingest_run_id`, `predecessor_version_id`) are
declared once for all tables instead of per table. Every remaining table in the
schema — provenance links, policy registries, seal state, import bookkeeping,
and canonical derived data — is listed in `excluded_tables` with its reason, so
a new table cannot appear in neither list unnoticed.

An `unsourced` column also records its **effect**, because unsourced does not
mean NULL: six of them are `NOT NULL`. The effect is *stays NULL*, *stores a
documented constant* (monthly-revenue `currency`, always TWD), *stores a derived
value* (index metadata observation dates), or *table stays empty* (a domain out
of v1). A column marked *stays NULL* must be nullable in the live schema, and a
test checks it.

A legacy field whose `target` column does not exist yet carries `planned_pr`,
naming the step that adds it — the eight monthly-revenue comparatives point at
Step 22. A target that exists in neither the schema nor a planned step fails the
test.

`sourced` and `partially_sourced` entries name the published field labels, or
the endpoint where the audit certifies a whole table (§4.3, §4.4, §4.5), and
cite the audit section. The `partially_sourced` and `unsourced` entries must
match [`source_field_audit.md`](source_field_audit.md) §5 exactly, one row per
column.

`tests/unit/test_pr14_storage_contract_source_coverage.py` holds all three
artifacts to each other: the registry, audit §5, and the live SQLAlchemy
metadata. Adding a column to an observed table without a source mapping fails
that test.

## Global legacy-field rules

These rules apply wherever the field occurs; table-specific entries in the
JSON contract resolve the exact target and rationale:

| Legacy field | Disposition |
| --- | --- |
| `symbol` | Stable `security.security_code`, except index symbols which map to `market_index.index_code`. |
| `market` | Effective-dated `security_metadata_versions.market` for securities, or the applicable market-level domain identity. |
| `name` | Observed, effective-dated `security_metadata_versions.name`. |
| `date` | The applicable observation, report, snapshot, or event logical date—not publication or ingestion time. |
| `pced_file`, `pced_row`, `pced_col` | Raw-artifact-only parser/source coordinates. |
| `publish_time` | `publication_evidence.published_at`; never folded into business content. |

All Phase 7 per-security stock quantities use canonical shares. A source value
in lots is explicitly scaled by 1,000 before canonical observation, storage,
and business hashing; an untyped quantity is rejected at the source boundary.

## Complete legacy table and field mapping

| Legacy table | Legacy fields reviewed | v1 disposition |
| --- | --- | --- |
| `stock_info` | `symbol`, `name`, `market`, industry/category, listing and delisting dates | Stable `security` identity plus observed `security_metadata_versions`; market, names/categories, and effective dates are revisioned. Explicit official cross-market transfer terms are retained in `security_transfer_events` for re-runnable reconciliation without merging source histories. |
| `stock_tags` | `symbol`, tag/category, effective dates | **Not in v1** (ROADMAP §16). The only known source is a MoneyDJ current snapshot — a third party, with no effective dates — and no legacy consumer reads the table (audit §4.11, §5). `security_tag_versions` exists in the schema and stays empty. |
| `daily_quotes` | `date`, `market`, `symbol`, `name`; OHLC; `volume`, `value`, `transactions`; `change`, `direction`; `bid`, `ask`; parsed last bid/ask price and volume; `pced_file`, `pced_row`, `pced_col` | OHLC, volume, trade value/count, change, and the single published last bid/ask price are observed in `daily_price_versions`; legacy `bid`/`ask`, NULL in every row, map to `last_bid_price`/`last_ask_price`. The source also publishes the matching last bid/ask *volume*, which Step 17-a stores in `last_bid_volume`/`last_ask_volume`; the legacy table has no field for it. `price_direction` is TWSE-only except for the TPEx 不比價 marker (除息 / 除權 / 除權息), which is stored as `X`; an ordinary TPEx row publishes a signed 漲跌 and no direction. The multi-level `bid_snapshot`/`ask_snapshot` columns have no source and stay NULL: the daily whole-market files publish one order-book level, and it is already stored in `last_bid_*`/`last_ask_*` (audit §4.1, §5). Symbol resolves through stable `security`; market/name through effective-dated metadata. `pced_*` is raw-only parser provenance tied to `raw_artifacts`/`ingest_runs`, not business content. |
| `monthly_revenue` | year/month, current revenue, currency; MoM, YoY, cumulative revenue, cumulative YoY; comment; publication timestamp; `pced_*` | Current revenue is normalized to the currency's major unit and stored in `monthly_revenue_versions`. Version/evidence observation links preserve every fetch. The published comparatives — 上月營收, 去年當月營收, the three percentages, the cumulative values, and 備註 — are in the same MOPS row and read by legacy consumers, so Step 22-a stores them as observed and never reconciles them against our own series (audit §4.7, §6, §7.3); `monthly_revenue_growth:v1` leaves v1 with them. `currency` is not an observation: the page states 單位：千元 as a page-level constant (audit §5). Publication time belongs only in `publication_evidence`; `pced_*` is raw-only. |
| `income_statement` | All 40 declared legacy identity, statement, quarterly/accumulated metric, and `pced_*` fields | Deprecated pre-XBRL category. Namespace-aware `financial_facts` and sealed summaries are the v1 replacement; the JSON contract gives every field an explicit disposition. |
| `balance_sheet` | All 24 declared legacy identity, statement, balance, ratio, and `pced_*` fields | Deprecated pre-XBRL category; replaced by namespace-aware financial facts and versioned summaries. |
| `cash_flow` | All 20 declared legacy identity, statement, cash-flow, and `pced_*` fields | Deprecated pre-XBRL category; replaced by namespace-aware financial facts and versioned summaries. |
| `quarterly_reports` | All 36 declared legacy identity, financial summary, ratio, and `pced_*` fields | Deprecated pre-XBRL category; replaced by `quarterly_reports_xbrl`, sealed facts, and canonical summaries. |
| `income_statement_xbrl` | entity/period, namespaced account code, value, unit, context/dimensions | Observed `financial_filing_versions` + `financial_facts`; statement presentation summaries may use `quarterly_financial_summary`. Step 23-b stores exactly the three statements this and its two siblings held, bounded by the document's own `id="BalanceSheet"` / `id="StatementOfComprehensiveIncome"` / `id="StatementsOfCashFlows"` anchors, and each fact keeps the `statement` it was printed in and its `account_code`. Legacy stored the current period only; the documents' prior-year comparative columns are stored too and filtered at reconciliation (audit §4.8). |
| `balance_sheet_xbrl` | entity/instant, namespaced account code, value, unit, context/dimensions | Observed `financial_filing_versions` + `financial_facts`. |
| `cash_flow_xbrl` | entity/period, namespaced account code, value, unit, context/dimensions | Observed `financial_filing_versions` + `financial_facts`. 權益變動表, the notes, the 附表 and the `escape="true"` narrative blocks are outside this scope: they are counted in the import manifest and stored nowhere, and whether they are ever stored is decided at the end of the ROADMAP (owner decision, 2026-09-21). |
| `quarterly_reports_xbrl` | year/quarter and normalized quarterly report values | Observed filing identity/facts; reusable normalized metrics use `quarterly_financial_summary`; no nullable-date fact identity survives. |
| `xbrl_codebook` | `statement_type`, `account_code`, Chinese/English account names | **Not in v1** (ROADMAP §16). No legacy consumer reads it, and this audit inspected no official concept-catalogue endpoint; the iXBRL documents carry the namespace-aware `concept_qname` that `financial_facts` needs (audit §4.8, §5). `xbrl_concept_catalog_versions` exists in the schema and stays empty. Step 23-a's parser keeps each fact's printed 會計科目代碼 and both labels with the fact, and Step 23-b stores the code on `financial_facts`, which is what Step 23-c's code ↔ QName reconciliation needs, so no codebook table is required. The codebook is also not the statement universe: its 1,748 codes miss 1,040 real cash-flow subtotal rows (`AA0000`, `AB0000`, `AC0100`–`AC0500`) in 332 documents, so the statements are bounded by the document's anchors instead (audit §4.8). |
| `shareholding` | snapshot date, holding-level bucket, holder count, shares, ownership percent | Observed immutable `tdcc_snapshot_versions` aggregate with `tdcc_distribution`; only a seal makes it visible. Step 24-a keys each week on the file's own 資料日期, stores 差異數調整 negative with no holder count (說明4: a difference has no holders) and the published 合計 as-is, including the 158 archived rows above 100% (audit §4.9, ADR-0024). The bulk file's undefined 人數 on the adjustment row is counted in the manifest and stays raw-only. |
| `institutional_investors` | foreign, foreign-dealer, trust, dealer-self, dealer-hedge buy/sell/net; dealer and total net; date/market/symbol/name; `pced_*` | All published per-security flow columns are normalized to shares and observed in `institutional_investor_versions`; identity/name and raw coordinates map as above. |
| `institutional_summary` | market/date/institution, buy, sell, net | Observed source-published market totals in `institutional_market_summary_versions`; they are not silently recomputed from security rows. |
| `foreign_holding` | issued, investable and held shares; investable/held ratio; foreign/mainland legal-limit ratios; change reason; source update date; `pced_*` | Share counts are normalized to shares and observed in `foreign_holding_versions`; raw coordinates are raw-only. |
| `trust_holding` | date/symbol, legacy cumulative “holding” and ratio | The legacy zero-origin calculation is not an absolute holding. It maps to the canonical proxy `institutional_cumulative_flow:v1` (`trust_cumulative_net_shares` and ratio) from PIT-safe flows and issued shares. |
| `dealer_holding` | date/symbol, legacy cumulative “holding” and ratio | The legacy zero-origin calculation maps to `institutional_cumulative_flow:v1` (`dealer_cumulative_net_shares` and ratio), not actual dealer ownership. |
| `margin_trading` | margin buy/sell/cash repayment/previous balance/balance/next limit/utilization; short buy/sell/stock repayment/previous balance/balance/next limit/utilization; offset balance | Source quantities are normalized from explicit shares/lots to shares and observed in `margin_trading_versions`. Ratios recomputed by Data Center are separately versioned canonical derived metrics. |
| `margin_sbl` | margin-short previous balance/buy/sell/balance; SBL previous balance/borrowed/returned/balance/limit/available/adjustment/note | Margin-short observations map to `margin_trading_versions`, where Step 21-a stores them from MI_MARGN / `margin/balance`; the TWT93U / `margin/sbl` 融券 group repeats them and is reconciled, not stored. SBL observations map to `securities_lending_versions` (Step 21-b); `next_limit` is the 融券 group's exact-share limit. Both tables publish shares. Source notes remain observed. |
| `margin_summary` | market/date aggregate margin and short balances/changes | **Not in v1** (ROADMAP §16): no legacy consumer reads it. `margin_market_summary:v1` stays defined as the canonical derived reconstruction for a later version; source-published totals, if onboarded later, require a distinct observed definition. |
| `market_indices` | date/market/index symbol/name, close, change points; `pced_*` | Stable `market_index.index_code`, observed `market_index_metadata_versions`, and observed quotes in `market_index_versions`; raw coordinates are raw-only. The whole-list sources publish close, change points, and change percent only. `open_value`/`high_value`/`low_value` exist for the TAIEX alone, from `MI_5MINS_HIST`, which Step 18 adds; `trade_value` has no source at all, and neither does an official index effective date — `market_index_metadata_versions.effective_from`/`effective_to` record observation dates (audit §4.2, §5). |
| `dividend` | date/symbol/name, close before event, reference price, rights/dividend value, action type | Stable source event identity in `corporate_action_events` plus observed `corporate_action_versions`. The exchange result feeds publish the ex/resumption date, close-before and reference prices, cash dividend, the combined free-share figure, rights terms, capital-reduction kind and returned cash, and source terms. They publish no `announcement_date`, `record_date`, or `payment_date`, and no earnings / capital-surplus split — that split exists only in the MOPS issuer declaration feed, which Step 33 stores as its own `dividend_declaration_versions` domain (audit §4.10, §4.13, §5). |
| `pe_ratio` | date/symbol and source-published PE | Observed `official_valuation_versions`. Official PB/dividend yield and associated source period fields share this observed contract. |
| `valuation_daily` | close, official TTM EPS, official PE, official PE percentile, official ROE | Legacy `pe_official` is observed and maps to `official_valuation_versions.pe_ratio`. Data Center-calculated TTM EPS/PE percentile/ROE and other computed valuations are canonical derived `valuation_metrics:v1`; close is referenced from PIT-safe daily prices, not duplicated. |
| `technical_indicators` | MA 5/10/20/60/120/240; volume MA 5/10/20; K/D; RSI 6/12; MACD DIF/DEA/histogram; Bollinger upper/middle/lower; foreign/trust/dealer streak days | Canonical derived `technical_indicators:v1` and `institutional_streaks:v1`, materialized through `derived_metric_versions`. Formula, calendar, and adjustment conventions are definition-versioned. |
| `shareholding_concentration` | large/mid/small-holder ratios and counts, spread and week-over-week changes | Canonical derived `shareholding_concentration:v1` from sealed TDCC inputs; materialized. |
| `margin_pressure_analysis` | margin utilization/balance ratios, changes, week-over-week metrics, pressure score | Stable ratios/changes are canonical derived `margin_metrics:v1`. The composite pressure score has no stable source-independent legacy specification and is downstream-owned; it is not persisted as a source fact. |
| `short_interest_analysis` | short/SBL balances and ratios, changes, week-over-week metrics, pressure score | Stable ratios/changes are canonical derived `short_interest_metrics:v1`. The composite pressure score is downstream-owned. |

## v1 storage contract matrix

| Domain / dataset code | Kind | Logical identity | Revision / PIT contract | Storage strategy | Planned consumers |
| --- | --- | --- | --- | --- | --- |
| `security_metadata` | observed | security, source, effective-from | market/name/listing business hash revisions; evidence governs market PIT, ingest time system PIT | materialized | both ML repos/API |
| `daily_price` | observed | security, source, trade date | complete quote business hash; evidence + ingestion cutoffs | materialized | indicators, valuation, backtests |
| `monthly_revenue` | observed | security, source, revenue year/month | revenue/currency and published-comparative revision; evidence + ingestion cutoffs | materialized | EPS and selection |
| `financial_filing` | observed aggregate | security, source, filing key | draft invisible; trusted seal time; evidence targets sealed version | materialized/sealed | EPS, canonical fundamentals |
| `tdcc_snapshot` | observed aggregate | security, source, snapshot date | draft invisible; trusted seal time; evidence targets sealed version | materialized/sealed | concentration metrics |
| `institutional_investor` | observed | security, source, trade date | all published category flows share one revision hash | materialized | holdings/streaks/selection |
| `institutional_market_summary` | observed | market, source, date, institution | independently published market aggregate | materialized | market analysis |
| `foreign_holding` | observed | security, source, trade date | shares/ratios/legal limits revision together | materialized | selection/ownership |
| `margin_trading` | observed | security, source, trade date | margin and exchange short fields revision together | materialized | margin/short metrics |
| `securities_lending` | observed | security, source, trade date | SBL balance/activity revision | materialized | short-interest metrics |
| `market_index` | observed | index, source, trade date | index quote revision | materialized | benchmarks/regime/backtests |
| `market_index_metadata` | observed | index, source, effective-from | market/name/effective-end revision | materialized | historical index identity presentation |
| `corporate_action` | observed | stable event (`security`, source, source event key) | source-faithful type, dates, dividend components, old/new shares, capital-reduction kind/returned cash, rights terms, official reference values, and source terms revision | materialized | adjusted prices/returns |
| `official_valuation` | observed | security, source, trade date | source-published values only | materialized | API/comparison |
| `dividend_declaration` | observed | security, dividend year, dividend period text, sequence | issuer board-resolution declarations; new `dividend_declaration_versions` table added by Step 33, never folded into `corporate_action_versions` | materialized | dividend research/API |
| `security_tag` | **not in v1** | security, source, tag, effective-from | — | table exists, stays empty | — |
| `xbrl_concept_catalog` | **not in v1** | source, concept QName | — | table exists, stays empty | — |
| `technical_indicators:v1` | canonical derived | security/date/metric/PIT/input fingerprint | visibility inherited from PIT-safe prices; never from `computed_at` | virtual (ROADMAP §17) | both ML repos/API |
| `shareholding_concentration:v1` | canonical derived | security/date/metric/PIT/input fingerprint | sealed TDCC inputs only | virtual (ROADMAP §17) | selection/API |
| `valuation_metrics:v1` | canonical derived | security/date/metric/PIT/input fingerprint | PIT-safe prices and financial inputs | virtual (ROADMAP §17) | both ML repos/API |
| `margin_metrics:v1` | canonical derived | security/date/metric/PIT/input fingerprint | PIT-safe margin inputs | virtual (ROADMAP §17) | selection/API |
| `short_interest_metrics:v1` | canonical derived | security/date/metric/PIT/input fingerprint | PIT-safe margin/SBL inputs | virtual (ROADMAP §17) | selection/API |
| `monthly_revenue_growth:v1` | **not in v1** | security/month/metric/PIT/input fingerprint | — | — | superseded by the observed published comparatives (Step 22) |
| `institutional_cumulative_flow:v1` | canonical derived proxy | security/date/category/PIT/input fingerprint | zero-origin cumulative PIT-safe net flows, optionally divided by PIT-safe issued shares; not absolute holdings | virtual (ROADMAP §17) | selection/API |
| `institutional_streaks:v1` | canonical derived | security/date/category/PIT/input fingerprint | PIT-safe institutional flows | virtual (ROADMAP §17) | selection/API |
| `margin_market_summary:v1` | **not in v1** | market/date/metric/PIT/input fingerprint | — | — | no legacy consumer (ROADMAP §16) |

v1 computes canonical derived data on demand (ROADMAP §17); a metric is
materialized only by its own step after measurement. Every materialized derived
row, when one exists, references a `derived_dataset_definitions`
record, a `derived_computation_runs` record, an explicit market/system PIT
context, canonical input dataset identities, and a deterministic input
fingerprint. `computed_at` is operational provenance only. Backfill status for
all Phase 1 contracts is **not started**; population belongs to later phases.
