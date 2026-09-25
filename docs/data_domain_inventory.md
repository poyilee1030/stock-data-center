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
records names the legacy table and field, disposition, target, and reason. It
also records the canonical SHA-256 fingerprint of the audited `SCHEMA_COLS`
`(table, field)` pairs. Automated tests compare the complete contract against
that frozen legacy fingerprint, counts, uniqueness, and the allowed
dispositions.

Targets name schema v2 (ADR-0027). Step 35-d-3 moved them from the v1 tables,
which no longer exist; a legacy field whose v1 column v2 did not keep says so
in its target and reason.

Disposition terms:

- **observed**: source-published data stored in a schema v2 table, one
  append-only row per published value;
- **identity**: the field is part of a row's key — a stock code, a date, a
  report period;
- **publication_time**: stored as `published_at` on a key's first row (monthly
  revenue, financial reports);
- **canonical derived**: deterministic, model-independent data defined by
  derivation version, stored as one wide table per dataset and computed from the
  latest inputs, never from one dated or published after the value's date
  (ROADMAP §17);
- **downstream**: model/experiment-specific logic owned by an ML repository;
- **raw-only**: parser coordinates and labels that stay in the raw file
  (`fetches` → `data/raw/`);
- **deprecated**: intentionally excluded from v1.

## Per-column source coverage

The legacy mapping above answers "where did this legacy field go?". It does not
answer the opposite question, which is the one a step planning an adapter needs:
*does an official source actually publish this stored column?* That is
`storage_contract` in [`data_domain_inventory.json`](data_domain_inventory.json).
Every column of every schema v2 table is classified there:

| Coverage | Meaning |
| --- | --- |
| `sourced` | an inspected official endpoint publishes the value for the whole v1 window and both markets |
| `partially_sourced` | published for only some dates, markets, or securities |
| `unsourced` | no inspected source publishes it; no v1 step may promise it |
| `internal` | identity, the fetch a row came from, or the time the Data Center recorded it |

Two structural columns (`id`, `source`) are declared once for all tables
instead of per table. `fetches`, the fetch log, is listed in `excluded_tables`
with its reason: it holds provenance, not source content. A new table must
appear in one list or the other.

Schema v2 stores no `unsourced` column: the v1 schema kept such columns NULL or
filled them with a constant or a value of its own, and v2 dropped them
(audit §5). The category stays so that a future column with no source is
recorded rather than stored.

A legacy field whose `target` column does not exist yet carries `planned_pr`,
naming the step that adds it. A target that exists in neither the schema nor a
planned step fails the test.

`sourced` and `partially_sourced` entries name the published field labels, or
the endpoint where the audit certifies a whole table (§4.3, §4.4, §4.5), and
cite the audit section. The `partially_sourced` entries must match
[`source_field_audit.md`](source_field_audit.md) §5 exactly, one row per column.

`tests/unit/test_pr14_storage_contract_source_coverage.py` holds all three
artifacts to each other: the registry, audit §5, and the SQLAlchemy metadata of
schema v2. Adding a column to a v2 table without a source mapping fails that
test.

## Global legacy-field rules

These rules apply wherever the field occurs; table-specific entries in the
JSON contract resolve the exact target and rationale:

| Legacy field | Disposition |
| --- | --- |
| `symbol` | The official stock code, `stocks.stock_id` and every table's `stock_id`; an index is identified by its source and published name, `index_prices.index_name`. |
| `market` | Today's market from the ISIN list, `stocks.market` (ADR-0026); for a market-level dataset, the publishing source. |
| `name` | Today's name, `stocks.name`; no name history is kept. |
| `date` | The applicable observation, report, snapshot, or event date — not publication or ingestion time. |
| `pced_file`, `pced_row`, `pced_col` | Raw-only parser coordinates. |
| `publish_time` | `monthly_revenues.published_at` or `financial_reports.published_at`; every other dataset computes it from its release rule. |

All per-security stock quantities are shares. A source value in lots is scaled
by 1,000 at the adapter, and an untyped quantity is rejected at the source
boundary.

## Complete legacy table and field mapping

| Legacy table | Legacy fields reviewed | v1 disposition |
| --- | --- | --- |
| `stock_info` | `symbol`, `name`, `market`, industry/category, listing and delisting dates | `stocks`: today's ISIN list of listed and OTC common stocks (ADR-0026), with today's name, market, industry and listing date. No name, market or lifecycle history is kept, and stocks delisted before today are not in the universe; the survivorship bias is accepted and disclosed. |
| `stock_tags` | `symbol`, tag/category, effective dates | **Not in v1** (ROADMAP §16). The only known source is a MoneyDJ current snapshot — a third party, with no effective dates — and no legacy consumer reads the table (audit §4.11). No tag table exists. |
| `daily_quotes` | `date`, `market`, `symbol`, `name`; OHLC; `volume`, `value`, `transactions`; `change`, `direction`; `bid`, `ask`; parsed last bid/ask price and volume; `pced_file`, `pced_row`, `pced_col` | OHLC, volume, trade value/count, change, and the single published last bid/ask price are observed in `daily_prices`; legacy `bid`/`ask`, NULL in every row, map to `last_bid_price`/`last_ask_price`. The source also publishes the matching last bid/ask *volume*, stored in `last_bid_volume`/`last_ask_volume`; the legacy table has no field for it. `price_direction` is TWSE-only except for the TPEx 不比價 marker (除息 / 除權 / 除權息), which is stored as `X`; an ordinary TPEx row publishes a signed 漲跌 and no direction. No order-book depth is stored: the daily whole-market files publish one level (audit §4.1). `pced_*` is raw-only. |
| `monthly_revenue` | year/month, current revenue, currency; MoM, YoY, cumulative revenue, cumulative YoY; comment; publication timestamp; `pced_*` | Current revenue, converted ×1,000 from 千元 to TWD, is observed in `monthly_revenues`. The published comparatives — 上月營收, 去年當月營收, the three percentages, the cumulative values, and 備註 — are in the same MOPS row and read by legacy consumers, so they are stored as observed and never reconciled against our own series (audit §4.7, §6, §7.3); `monthly_revenue_growth:v1` leaves v1 with them. Currency is a page-level constant (單位：千元) and not stored. Publication time is `published_at`, set where a capture or legacy record proves it; `pced_*` is raw-only. |
| `income_statement` | All 40 declared legacy identity, statement, quarterly/accumulated metric, and `pced_*` fields | Deprecated pre-XBRL category. The namespace-aware iXBRL facts in `financial_report_facts` replace it; the JSON contract gives every field an explicit disposition. |
| `balance_sheet` | All 24 declared legacy identity, statement, balance, ratio, and `pced_*` fields | Deprecated pre-XBRL category; replaced by `financial_report_facts`. |
| `cash_flow` | All 20 declared legacy identity, statement, cash-flow, and `pced_*` fields | Deprecated pre-XBRL category; replaced by `financial_report_facts`. |
| `quarterly_reports` | All 36 declared legacy identity, financial summary, ratio, and `pced_*` fields | Deprecated pre-XBRL category; replaced by `financial_report_facts` and summaries computed on demand from it. |
| `income_statement_xbrl` | entity/period, namespaced account code, value, unit, context/dimensions | Observed `financial_reports` + `financial_report_facts`. Exactly the three statements this and its two siblings held are stored, bounded by the document's own `id="BalanceSheet"` / `id="StatementOfComprehensiveIncome"` / `id="StatementsOfCashFlows"` anchors, and each fact keeps the `statement` it was printed in and its `account_code`. Legacy stored the current period only; the documents' prior-year comparative columns are stored too and filtered at reconciliation (audit §4.8). A document with a dimensioned fact in these statements is quarantined (ADR-0027). |
| `balance_sheet_xbrl` | entity/instant, namespaced account code, value, unit, context/dimensions | Observed `financial_reports` + `financial_report_facts`; an instant has `period_start` NULL. |
| `cash_flow_xbrl` | entity/period, namespaced account code, value, unit, context/dimensions | Observed `financial_reports` + `financial_report_facts`. 權益變動表, the notes, the 附表 and the `escape="true"` narrative blocks are outside this scope and stay in the raw document; whether they are ever stored is decided at the end of the ROADMAP (owner decision, 2026-09-21). |
| `quarterly_reports_xbrl` | year/quarter and normalized quarterly report values | Observed report identity and facts; the EPS summary and the Q4 single quarter are computed on demand from `financial_report_facts` (ADR-0027). |
| `xbrl_codebook` | `statement_type`, `account_code`, Chinese/English account names | **Not in v1** (ROADMAP §16). No legacy consumer reads it, and this audit inspected no official concept-catalogue endpoint; the iXBRL documents carry the namespace-aware concept that `financial_report_facts` stores (audit §4.8). The printed 會計科目代碼 is stored with each fact as `account_code`, which is what the code ↔ concept reconciliation needs, so no codebook table exists. The codebook is also not the statement universe: its 1,748 codes miss 1,040 real cash-flow subtotal rows (`AA0000`, `AB0000`, `AC0100`–`AC0500`) in 332 documents, so the statements are bounded by the document's anchors instead (audit §4.8). |
| `shareholding` | snapshot date, holding-level bucket, holder count, shares, ownership percent | Observed `shareholding_distributions`, one wide row per stock and week: levels 1–15 as `holders_N`, `shares_N`, `percent_N`, level 16 as `adjustment_shares`/`adjustment_percent` (差異數調整, signed, no holders: 說明4), level 17 as the published `total_*`, including the 158 archived rows above 100% (audit §4.9, ADR-0024). Each week is keyed on the file's own 資料日期. The bulk file's undefined 人數 on the adjustment row stays raw-only. |
| `institutional_investors` | foreign, foreign-dealer, trust, dealer-self, dealer-hedge buy/sell/net; dealer and total net; date/market/symbol/name; `pced_*` | All published per-security flow columns, in shares, are observed in `institutional_flows`. |
| `institutional_summary` | market/date/institution, buy, sell, net | Observed source-published market totals in `institutional_market_flows`; they are not recomputed from stock rows. |
| `foreign_holding` | issued, investable and held shares; investable/held ratio; foreign/mainland legal-limit ratios; change reason; source update date; `pced_*` | Share counts and the investable, held and foreign legal-limit ratios are observed in `foreign_holdings`. The mainland limit, change reason and filing date are not stored (ADR-0027); OTC comes from MOPS `t13sa150_otc` only. |
| `trust_holding` | date/symbol, legacy cumulative “holding” and ratio | The legacy zero-origin calculation is not an absolute holding. It maps to the canonical proxy `institutional_cumulative_flow:v1` (`trust_cumulative_net_shares` and ratio) from PIT-visible flows and issued shares. |
| `dealer_holding` | date/symbol, legacy cumulative “holding” and ratio | The legacy zero-origin calculation maps to `institutional_cumulative_flow:v1` (`dealer_cumulative_net_shares` and ratio), not actual dealer ownership. |
| `margin_trading` | margin buy/sell/cash repayment/previous balance/balance/next limit/utilization; short buy/sell/stock repayment/previous balance/balance/next limit/utilization; offset balance | Source quantities, converted from lots to shares, are observed in `margin_trading`. The utilization ratios exist for TPEx only and are not stored (ADR-0027); ratios computed by the Data Center are canonical derived metrics. |
| `margin_sbl` | margin-short previous balance/buy/sell/balance; SBL previous balance/borrowed/returned/balance/limit/available/adjustment/note | Margin-short values map to `margin_trading`, filled from MI_MARGN / `margin/balance`; the TWT93U / `margin/sbl` 融券 group repeats them and is reconciled, not stored. SBL values map to `securities_lending`, including the signed adjustment and both limits; `next_limit` is the 融券 group's exact-share limit. Both tables publish shares. The source note is not stored (ADR-0027). |
| `margin_summary` | market/date aggregate margin and short balances/changes | **Not in v1** (ROADMAP §16): no legacy consumer reads it. `margin_market_summary:v1` stays defined as the canonical derived reconstruction for a later version; source-published totals, if onboarded later, require a distinct observed definition. |
| `market_indices` | date/market/index symbol/name, close, change points; `pced_*` | Observed `index_prices`, identified by `(source, index_name)` — the published name — for the 126 headline and sector indices (`stock_data_center.v2.indices`). The whole-list sources publish close, change points, and change percent only. `open_value`/`high_value`/`low_value` exist for the TAIEX alone, from `MI_5MINS_HIST`; no source publishes index trade value or when an index name took effect (audit §4.2). |
| `dividend` | date/symbol/name, close before event, reference price, rights/dividend value, action type | Observed `corporate_actions`, keyed by `(stock_id, source, ex_date)`, which is the §51.5 event identity (feed + executed date). The exchange result feeds publish the ex/resumption date, close-before and reference prices, cash dividend, the combined free-share figure, rights terms, and capital-reduction returned cash; `event_type` keeps the feed's own type text. They publish no announcement, record or payment date, and no earnings / capital-surplus split — that split exists only in the MOPS issuer declaration feed, which Step 33 is to store as its own `dividend_declaration` domain (audit §4.10, §4.13). |
| `pe_ratio` | date/symbol and source-published PE | Observed `valuations`, with official PB, dividend yield and the source's dividend year and report period. |
| `valuation_daily` | close, official TTM EPS, official PE, official PE percentile, official ROE | Legacy `pe_official` is observed and maps to `valuations.pe_ratio`. Data Center-calculated TTM EPS, PE, PE percentile and ROE are canonical derived `valuation_metrics:v1` (`ttm_eps`, `pe_ratio`, `pe_percentile`, `roe`; the `_official` suffix is dropped and ROE redefined, owner 2026-09-25); close is referenced from daily prices, not duplicated. |
| `technical_indicators` | MA 5/10/20/60/120/240; volume MA 5/10/20; K/D; RSI 6/12; MACD DIF/DEA/histogram; Bollinger upper/middle/lower; foreign/trust/dealer streak days | Canonical derived `technical_indicators:v1` (table `technical_indicators`, from `daily_prices`) and `institutional_streaks:v1` (table `institutional_streaks`, from `institutional_flows` over the traded days of `daily_prices`), both stored by Step 26-b. `technical_indicators_pit:v1` computes the same formula on demand under full PIT (Step 35-c-4). Formula, calendar, and adjustment conventions are the versioned code constant. |
| `shareholding_concentration` | large/mid/small-holder ratios and counts, spread and week-over-week changes | Canonical derived `shareholding_concentration:v1` from `shareholding_distributions`. |
| `margin_pressure_analysis` | margin utilization/balance ratios, changes, week-over-week metrics, pressure score | Stable ratios/changes are canonical derived `margin_metrics:v1` (its `_wow` columns are daily changes, named `_change`); the balances and limits it copied are observed in `margin_trading`. The composite pressure score has no stable source-independent legacy specification and is downstream-owned; it is not persisted as a source fact. |
| `short_interest_analysis` | short/SBL balances and ratios, changes, week-over-week metrics, pressure score | Stable ratios/changes are canonical derived `short_interest_metrics:v1`; its copy of the short-sale change is `margin_metrics:v1`'s, and the balances and SBL flows it copied are observed in `securities_lending`. The composite pressure score is downstream-owned. |

## v1 storage contract matrix

Every observed table is append-only: a row is added only when a published value
changes, and each row names the fetch it came from (ADR-0027).

| Dataset | Kind | Table and key | First row visible at | Planned consumers |
| --- | --- | --- | --- | --- |
| stock universe | observed | `stocks (stock_id)` | today's list, not history | every table's `stock_id` |
| `daily_price` | observed | `daily_prices (stock_id, source, trade_date)` | release rule `exchange_daily_settled@1` | indicators, valuation, backtests |
| `monthly_revenue` | observed | `monthly_revenues (stock_id, source, revenue_month)` | stored `published_at`; NULL is invisible | EPS and selection |
| `financial_filing` | observed | `financial_reports (stock_id, report_year, report_quarter)` + `financial_report_facts` | stored `published_at`; NULL is invisible | EPS, canonical fundamentals |
| `tdcc_distribution` | observed | `shareholding_distributions (stock_id, source, snapshot_date)` | release rule `tdcc_weekly@1` | concentration metrics |
| `institutional_investor` | observed | `institutional_flows (stock_id, source, trade_date)` | `exchange_daily_settled@1` | holdings/streaks/selection |
| `institutional_market_summary` | observed | `institutional_market_flows (source, trade_date, institution)` | `exchange_daily_settled@1` | market analysis |
| `foreign_holding` | observed | `foreign_holdings (stock_id, source, trade_date)` | `exchange_daily_settled@1` | selection/ownership |
| `margin_trading` | observed | `margin_trading (stock_id, source, trade_date)` | `exchange_daily_settled@1` | margin/short metrics |
| `securities_lending` | observed | `securities_lending (stock_id, source, trade_date)` | `exchange_daily_settled@1` | short-interest metrics |
| `market_index` | observed | `index_prices (source, index_name, trade_date)` | `exchange_daily_settled@1` | benchmarks/regime/backtests |
| `corporate_action` | observed | `corporate_actions (stock_id, source, ex_date)` | release rule `corporate_action_ex_date@1` | adjusted prices/returns |
| `official_valuation` | observed | `valuations (stock_id, source, trade_date)` | `exchange_daily_settled@1` | API/comparison |
| trading calendar | observed | `trading_days (trade_date)` | a calendar, not history | coverage, `--through` runs |
| `dividend_declaration` | observed | planned by Step 33 | Step 33's capture-based contract | dividend research/API |
| `security_tag` | **not in v1** | — | — | — |
| `xbrl_concept_catalog` | **not in v1** | — | — | — |
| `technical_indicators:v1` | canonical derived | `technical_indicators (stock_id, source, trade_date)` | latest inputs; D uses prices dated on or before D; `computed_at` is provenance | both ML repos/API |
| `technical_indicators_pit:v1` | canonical derived | computed on demand | inherited from PIT-visible prices; the reference the stored series is checked against | API |
| `shareholding_concentration:v1` | canonical derived | `shareholding_concentration (stock_id, source, snapshot_date)` | latest TDCC inputs; a change is against the stock's previous snapshot | selection/API |
| `valuation_metrics:v1` | canonical derived | `valuation_metrics (stock_id, source, trade_date)` | latest inputs; a report counts from its first version's `published_at` | both ML repos/API |
| `margin_metrics:v1` | canonical derived | `margin_metrics (stock_id, source, trade_date)` | latest margin inputs; each value from that day's row | selection/API |
| `short_interest_metrics:v1` | canonical derived | `short_interest_metrics (stock_id, source, trade_date)` | latest SBL inputs; each value from that day's row | selection/API |
| `monthly_revenue_growth:v1` | **not in v1** | — | — | superseded by the observed published comparatives (Step 22) |
| `institutional_cumulative_flow:v1` | canonical derived proxy | `institutional_cumulative_flow (stock_id, source, trade_date)` | zero-origin cumulative net flows and their ratio to the same day's issued shares; latest inputs; not absolute holdings | selection/API |
| `institutional_streaks:v1` | canonical derived | `institutional_streaks (stock_id, source, trade_date)` | latest inputs; D uses flows and prices dated on or before D | selection/API |
| `margin_market_summary:v1` | **not in v1** | — | — | no legacy consumer (ROADMAP §16) |

A correction is a later row, visible from its own `recorded_at`. A derived
table instead follows the latest inputs: a corrected input recomputes the
affected dates and overwrites them (ROADMAP §17, disclosed downstream).

Backfill status: every observed dataset except the Step 33 declarations holds
2020-01-02 onward in `stockdc_backfill` (Steps 17-c through 24-b, 35-c-3).
`technical_indicators:v1`, `institutional_streaks:v1` (Step 26-b),
`institutional_cumulative_flow:v1` (Step 26-c),
`shareholding_concentration:v1` (Step 26-d), `margin_metrics:v1` and
`short_interest_metrics:v1` (Step 26-e) and `valuation_metrics:v1` (Step 26-f)
hold every date of their inputs. The Step 33 issuer dividend declarations are
**not started**.
