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
| `stock_info` | `symbol`, `name`, `market`, industry/category, listing and delisting dates | Stable `security` identity plus observed `security_metadata_versions`; market, names/categories, and effective dates are revisioned. |
| `stock_tags` | `symbol`, tag/category, effective dates | Observed `security_tag_versions`; source and effective interval are explicit. |
| `daily_quotes` | `date`, `market`, `symbol`, `name`; OHLC; `volume`, `value`, `transactions`; `change`, `direction`; `bid`, `ask`; parsed last bid/ask price and volume; `pced_file`, `pced_row`, `pced_col` | OHLC, volume, trade value/count, change/direction, source bid/ask snapshots, and parsed last bid/ask values are observed in `daily_price_versions`. Symbol resolves through stable `security`; market/name through effective-dated metadata. `pced_*` is raw-only parser provenance tied to `raw_artifacts`/`ingest_runs`, not business content. |
| `monthly_revenue` | year/month, current revenue, currency; MoM, YoY, cumulative revenue, cumulative YoY; comment; publication timestamp; `pced_*` | Current revenue is normalized to the currency's major unit and stored with currency in `monthly_revenue_versions`. Version/evidence observation links preserve every fetch. Source commentary remains raw-only. Ratios and cumulative values are canonical derived (`monthly_revenue_growth:v1`). Publication time belongs only in `publication_evidence`; `pced_*` is raw-only. |
| `income_statement` | All 40 declared legacy identity, statement, quarterly/accumulated metric, and `pced_*` fields | Deprecated pre-XBRL category. Namespace-aware `financial_facts` and sealed summaries are the v1 replacement; the JSON contract gives every field an explicit disposition. |
| `balance_sheet` | All 24 declared legacy identity, statement, balance, ratio, and `pced_*` fields | Deprecated pre-XBRL category; replaced by namespace-aware financial facts and versioned summaries. |
| `cash_flow` | All 20 declared legacy identity, statement, cash-flow, and `pced_*` fields | Deprecated pre-XBRL category; replaced by namespace-aware financial facts and versioned summaries. |
| `quarterly_reports` | All 36 declared legacy identity, financial summary, ratio, and `pced_*` fields | Deprecated pre-XBRL category; replaced by `quarterly_reports_xbrl`, sealed facts, and canonical summaries. |
| `income_statement_xbrl` | entity/period, namespaced account code, value, unit, context/dimensions | Observed `financial_filing_versions` + `financial_facts`; statement presentation summaries may use `quarterly_financial_summary`. |
| `balance_sheet_xbrl` | entity/instant, namespaced account code, value, unit, context/dimensions | Observed `financial_filing_versions` + `financial_facts`. |
| `cash_flow_xbrl` | entity/period, namespaced account code, value, unit, context/dimensions | Observed `financial_filing_versions` + `financial_facts`. |
| `quarterly_reports_xbrl` | year/quarter and normalized quarterly report values | Observed filing identity/facts; reusable normalized metrics use `quarterly_financial_summary`; no nullable-date fact identity survives. |
| `xbrl_codebook` | `statement_type`, `account_code`, Chinese/English account names | Observed `xbrl_concept_catalog_versions`; account code becomes namespace-aware `concept_qname`. |
| `shareholding` | snapshot date, holding-level bucket, holder count, shares, ownership percent | Observed immutable `tdcc_snapshot_versions` aggregate with `tdcc_distribution`; only a seal makes it visible. |
| `institutional_investors` | foreign, foreign-dealer, trust, dealer-self, dealer-hedge buy/sell/net; dealer and total net; date/market/symbol/name; `pced_*` | All published per-security flow columns are normalized to shares and observed in `institutional_investor_versions`; identity/name and raw coordinates map as above. |
| `institutional_summary` | market/date/institution, buy, sell, net | Observed source-published market totals in `institutional_market_summary_versions`; they are not silently recomputed from security rows. |
| `foreign_holding` | issued, investable and held shares; investable/held ratio; foreign/mainland legal-limit ratios; change reason; source update date; `pced_*` | Share counts are normalized to shares and observed in `foreign_holding_versions`; raw coordinates are raw-only. |
| `trust_holding` | date/symbol, legacy cumulative “holding” and ratio | The legacy zero-origin calculation is not an absolute holding. It maps to the canonical proxy `institutional_cumulative_flow:v1` (`trust_cumulative_net_shares` and ratio) from PIT-safe flows and issued shares. |
| `dealer_holding` | date/symbol, legacy cumulative “holding” and ratio | The legacy zero-origin calculation maps to `institutional_cumulative_flow:v1` (`dealer_cumulative_net_shares` and ratio), not actual dealer ownership. |
| `margin_trading` | margin buy/sell/cash repayment/previous balance/balance/next limit/utilization; short buy/sell/stock repayment/previous balance/balance/next limit/utilization; offset balance | Source quantities are normalized from explicit shares/lots to shares and observed in `margin_trading_versions`. Ratios recomputed by Data Center are separately versioned canonical derived metrics. |
| `margin_sbl` | margin-short previous balance/buy/sell/balance; SBL previous balance/borrowed/returned/balance/limit/available/adjustment/note | Margin-short observations map to `margin_trading_versions`; SBL observations map to `securities_lending_versions`; all stock quantities use shares. Source notes remain observed. |
| `margin_summary` | market/date aggregate margin and short balances/changes | Canonical derived `margin_market_summary:v1` when reconstructed; source-published totals, if onboarded later, require a distinct observed definition. |
| `market_indices` | date/market/index symbol/name, close, change points; available OHLC/change percent/trade value; `pced_*` | Stable `market_index.index_code`, observed effective-dated `market_index_metadata_versions`, and observed quotes in `market_index_versions`; raw coordinates are raw-only. |
| `dividend` | date/symbol/name, close before event, reference price, rights/dividend value, action type | Stable source event identity in `corporate_action_events` plus observed `corporate_action_versions`; all dates/type/amounts/terms are revision content. |
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
| `monthly_revenue` | observed | security, source, revenue year/month | revenue/currency revision; evidence + ingestion cutoffs | materialized | EPS and selection |
| `financial_filing` | observed aggregate | security, source, filing key | draft invisible; trusted seal time; evidence targets sealed version | materialized/sealed | EPS, canonical fundamentals |
| `tdcc_snapshot` | observed aggregate | security, source, snapshot date | draft invisible; trusted seal time; evidence targets sealed version | materialized/sealed | concentration metrics |
| `institutional_investor` | observed | security, source, trade date | all published category flows share one revision hash | materialized | holdings/streaks/selection |
| `institutional_market_summary` | observed | market, source, date, institution | independently published market aggregate | materialized | market analysis |
| `foreign_holding` | observed | security, source, trade date | shares/ratios/legal limits revision together | materialized | selection/ownership |
| `margin_trading` | observed | security, source, trade date | margin and exchange short fields revision together | materialized | margin/short metrics |
| `securities_lending` | observed | security, source, trade date | SBL balance/activity revision | materialized | short-interest metrics |
| `market_index` | observed | index, source, trade date | index quote revision | materialized | benchmarks/regime/backtests |
| `market_index_metadata` | observed | index, source, effective-from | market/name/effective-end revision | materialized | historical index identity presentation |
| `corporate_action` | observed | stable event (`security`, source, source event key) | type, dates, amounts, and action terms revision | materialized | adjusted prices/returns |
| `official_valuation` | observed | security, source, trade date | source-published values only | materialized | API/comparison |
| `security_tag` | observed | security, source, tag, effective-from | effective interval revision | materialized | universe construction |
| `xbrl_concept_catalog` | observed | source, concept QName | labels/statement classification revision | materialized | financial parsers/API |
| `technical_indicators:v1` | canonical derived | security/date/metric/PIT/input fingerprint | visibility inherited from PIT-safe prices; never from `computed_at` | materialized | both ML repos/API |
| `shareholding_concentration:v1` | canonical derived | security/date/metric/PIT/input fingerprint | sealed TDCC inputs only | materialized | selection/API |
| `valuation_metrics:v1` | canonical derived | security/date/metric/PIT/input fingerprint | PIT-safe prices and financial inputs | materialized | both ML repos/API |
| `margin_metrics:v1` | canonical derived | security/date/metric/PIT/input fingerprint | PIT-safe margin inputs | materialized | selection/API |
| `short_interest_metrics:v1` | canonical derived | security/date/metric/PIT/input fingerprint | PIT-safe margin/SBL inputs | materialized | selection/API |
| `monthly_revenue_growth:v1` | canonical derived | security/month/metric/PIT/input fingerprint | PIT-safe revenue history | virtual initially | both ML repos/API |
| `institutional_cumulative_flow:v1` | canonical derived proxy | security/date/category/PIT/input fingerprint | zero-origin cumulative PIT-safe net flows, optionally divided by PIT-safe issued shares; not absolute holdings | materialized | selection/API |
| `institutional_streaks:v1` | canonical derived | security/date/category/PIT/input fingerprint | PIT-safe institutional flows | materialized | selection/API |
| `margin_market_summary:v1` | canonical derived | market/date/metric/PIT/input fingerprint | PIT-safe constituent inputs | virtual initially | market analysis |

Every materialized derived row references a `derived_dataset_definitions`
record, a `derived_computation_runs` record, an explicit market/system PIT
context, canonical input dataset identities, and a deterministic input
fingerprint. `computed_at` is operational provenance only. Backfill status for
all Phase 1 contracts is **not started**; population belongs to later phases.
