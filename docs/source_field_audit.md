# Source Field Audit

Status date: 2026-09-15.

This document records which fields the legacy database, the legacy raw archive,
and the official endpoints actually provide. `ROADMAP.md` uses it as the
source-reality baseline: a planned PR may promise a stored field only if this
audit (or the PR's own verified update to it) names the source field that
populates it.

## 1. What was inspected

- Legacy database `stock_db` (container `my_stock_project-db-1`, PostgreSQL 15):
  `information_schema.columns`, row counts, date ranges, and null counts.
- Legacy raw archive `~/GitHubLL/my_stock_project/data/raw`: the header
  signature of every daily file for every trade date from 2020-01-02 to
  2026-09-11, per market.
- Legacy scrapers (`scraper/daily`, `weekly`, `monthly`, `quarterly`): endpoint
  URLs and how each one writes files.
- Legacy consumers (`train_eps`, `strategies`, `backtester`, `calculator`,
  `scripts`): which tables and columns they read.
- Live probes on 2026-09-14: TWSE `TWT49U`, `TWT49UDetail`, `TWTAUU`,
  `TWTAVUDetail`, `FMTQIK`, `holidaySchedule`; TPEx `bulletin/exDailyQ`,
  `bulletin/revivt`; MOPS `t21sc03` `_0` and `_1` pages; the TDCC `qryStock`
  date selector.

## 2. Legacy baseline

The legacy database has 26 tables. All `date` columns are TEXT.

| Legacy table | Rows | Coverage | Read by legacy consumers | Notes |
| --- | ---: | --- | --- | --- |
| `daily_quotes` | 2,931,379 | 2020-01-02 → 2026-09-11 | yes | 2,066 symbols; `bid`/`ask` are NULL in every row; `direction` is populated for TWSE only |
| `institutional_investors` | 2,667,705 | same | yes (calculator) | |
| `foreign_holding` | 2,932,716 | same | yes | `issued_shares`, `foreign_held_ratio` are read |
| `trust_holding`, `dealer_holding` | 2,667,705 each | same | yes | legacy zero-origin cumulative-flow proxies |
| `institutional_summary` | 19,518 | same | no | |
| `margin_trading` | 2,728,779 | same | yes (calculator) | |
| `margin_sbl` | 2,773,315 | same | yes (calculator) | |
| `margin_summary` | 9,762 | same | no | |
| `market_indices` | 449,528 | same | yes | backtester reads `index_close` only; 366 TWSE + 43 TPEx index names; the "symbol" is the Chinese index name |
| `pe_ratio` | 2,936,511 | same | yes (valuation calculator) | |
| `monthly_revenue` | 140,950 | 2020M01 → 2026M08 | yes | consumers read `mom_pct`, `yoy_pct`, `cumulative_yoy_pct`, `revenue_current`, `publish_time`; zero KY issuers; `publish_time` is synthetic before 2026M02 and a real first-seen date from 2026M02 (§7.1) |
| `quarterly_reports_xbrl` | 87,804 | 2020Q1 → 2026Q2 | yes | |
| `income_statement_xbrl` / `balance_sheet_xbrl` / `cash_flow_xbrl` | 2,996,663 / 2,634,648 / 2,380,856 | 2020Q1 → 2026Q2 | yes | long format: account code + value |
| `xbrl_codebook` | — | — | no | |
| `shareholding` | 11,885,220 | 2020-01-03 → 2026-09-11 | yes (calculator) | TDCC levels |
| `stock_info` | 1,944 | current snapshot | yes | from `isin.twse.com.tw` |
| `stock_tags` | 8,661 | current snapshot | no | scraped from MoneyDJ, a third party |
| `dividend` | 6,182 | 2020-02-13 → 2026-09-10 | no | TWSE `TWT49U` only (988 TWSE symbols, 1 TPEx symbol); types 息 5,319 / 權 424 / 權息 439; zero duplicate `(symbol, date)` |
| `technical_indicators`, `shareholding_concentration`, `valuation_daily` (from 2021-03-31), `margin_pressure_analysis`, `short_interest_analysis` | — | — | yes | legacy calculator outputs (derived) |

Legacy technical indicators are computed from raw, unadjusted close prices.
Nothing in the legacy stack consumes dividend or corporate-action data.

## 3. How faithful the raw archive is

| Archive area | How it was written | Usable as an official source-byte artifact? |
| --- | --- | --- |
| TWSE/TPEx daily CSVs (quotes, institutional, foreign holding, margin, SBL, PE, summary, TPEx indices) | Decoded as big5 with `errors="ignore"`, `="…"` wrappers stripped, single-cell title and footnote rows dropped, then rewritten as quoted UTF-8-BOM CSV. The report date survives only in the directory name. | **No.** The bytes are not the official response. |
| TPEx foreign holding | MOPS `t13sa150_otc` POST, rewritten through pandas | No |
| Monthly revenue | Parsed CSV, not the MOPS HTML. Only `_0` (domestic-issuer) pages were fetched. Each month's `market.csv` appends only new `(market, symbol)` keys, so later corrections were never recorded. `publish_time` has two regimes (§7.1). | Not source bytes, but the 2026M02 onward rows are first-seen capture evidence |
| XBRL | iXBRL HTML decoded and rewritten as UTF-8 text. The filename date suffix has two regimes (§7.1). Files for 2020Q1–2025Q3 have February 2026 mtimes. | Not source bytes. 2020Q1–2025Q3 are no older than a fresh re-fetch; 2025Q4 onward are first-seen versions |
| TDCC weekly, the consolidated `shareholding` archive (§4.9) | OpenData bytes written unchanged, `.csv`/`.zip`/`.7z` | **Yes**, for all 375 weeks. |
| Corporate-action year-to-date files (`TWT49U`, `TWTAUU`, `TWTB8U`) | One `all.csv` per year, overwritten daily. Only 2026 keeps the cp950 original. TWSE only. | Partially (2026 only) |
| `stock_info`, `stock_tags` | Parsed current snapshots | No |

Because the official endpoints still serve 2020 onward for every domain except
TDCC, re-fetching gives byte-faithful artifacts through the existing PR #9
raw-first lifecycle. The archives are needed for TDCC history, for the recovered
monthly-revenue publication dates (§7.4), for the pre-2026 first-seen records
(§7.1), and as a reconciliation baseline.

## 4. Per-domain source fields

✓ = the field exists in the source; ✗ = it does not; "partial" = only for some
dates or markets.

### 4.1 Daily quotes

TWSE `exchangeReport/MI_INDEX?type=ALLBUT0999` returns one file per trade date
with index sections followed by the stock section. The stock-section header is
the same for all 1,627 files from 2020-01-02 to 2026-09-11:

```text
證券代號, 證券名稱, 成交股數, 成交筆數, 成交金額, 開盤價, 最高價, 最低價, 收盤價,
漲跌(+/-), 漲跌價差, 最後揭示買價, 最後揭示買量, 最後揭示賣價, 最後揭示賣量, 本益比
```

TPEx `stk_wn1430` (one file per trade date) has three header variants:

| Dates | Header |
| --- | --- |
| 2020-01-02 → 2020-04-29 (75 files) | 代號, 名稱, 收盤, 漲跌, 開盤, 最高, 最低, 成交股數, 成交金額(元), 成交筆數, 最後買價, 最後賣價, 發行股數, 次日漲停價, 次日跌停價 |
| 2020-04-30 → 2025-01-09 (1,147) | adds 最後買量(千股), 最後賣量(千股) |
| 2025-01-10 → 2026-09-11 (405) | relabelled 最後買量(張數), 最後賣量(張數) (1 張 = 1,000 shares) |

| `daily_price_versions` column | TWSE | TPEx |
| --- | --- | --- |
| open/high/low/close, volume, trade_value, trade_count | ✓ | ✓ |
| price_change | ✓ (unsigned 漲跌價差 + sign column) | ✓ (signed) |
| price_direction | ✓ | ✗ |
| last_bid_price / last_ask_price | ✓ | ✓ |
| last_bid_volume / last_ask_volume | ✓ (lots) | partial (from 2020-04-30) |
| bid_snapshot / ask_snapshot | ✗ | ✗ |

Source fields not stored: TPEx 發行股數 and next-day limit prices; TWSE 本益比
(duplicated by `BWIBBU_d`).

The PR #9 pilot adapters (`STOCK_DAY`, `tradingStock`) take one request per
security per month. Daily capture for about 2,200 securities would need about
2,200 requests per trade date, so these adapters suit pilots and spot checks,
not production.

### 4.2 Market indices

- TWSE: the `MI_INDEX` index sections (`指數`/`報酬指數`, 收盤指數, 漲跌(+/-), 漲跌點數,
  漲跌百分比(%), 特殊處理註記).
- TPEx: `afterTrading/indexSummary` (指數, 收市指數, 漲跌, 漲跌幅度(%), 大盤資訊連結).

Neither source has an index code, so identity is `(source, published index
name)`. In these whole-list sources `close_value`, `change_points`, and
`change_percent` are sourced; `open_value`, `high_value`, `low_value`, and
`trade_value` are not.
`market_index_metadata_versions.effective_from/effective_to` can only record
first and last observation dates.

Index OHLC exists for the TAIEX alone, in a separate endpoint the legacy system
never fetched: `rwd/zh/TAIEX/MI_5MINS_HIST?date=YYYYMM01&response=json`
(`發行量加權股價指數歷史資料`) returns 日期, 開盤指數, 最高指數, 最低指數, 收盤指數 for one
calendar month per request, verified live for 2026-01. It covers only
`發行量加權股價指數`, not the other ~270 published indices, and carries no trade
value. No TPEx equivalent was found in this audit; `4.2` probes of
`indexes/histIndex` and `openapi/v1/tpex_otc_index_history` both 404. PR #18
must spike TPEx before promising OTC index OHLC.

### 4.3 Institutional flows and summary

- TWSE `fund/T86`: one 19-column header for all dates.
- TPEx `3itrade_hedge`: one 24-column header, which adds foreign totals and
  dealer total buy/sell.

Every `institutional_investor_versions` column is sourced for both markets.

Summary sources: TWSE `fund/BFI82U` (單位名稱, 買進金額, 賣出金額, 買賣差額) and
TPEx `3itrdsum` (單位名稱, 買進金額(元), 賣出金額(元), 買賣超(元)). The TPEx file
for 2026-07-10 in the archive is broken.

### 4.4 Foreign holding

- TWSE `fund/MI_QFIIS`: 12 columns, including the ISIN.
- TPEx MOPS `t13sa150_otc`: 11 columns, no ISIN.

Every `foreign_holding_versions` column is sourced. Its `issued_shares` is the
only whole-market issued-share series for both markets, and legacy consumers
read it.

### 4.5 Margin and securities lending

- TWSE `MI_MARGN`: a market summary block (項目, 買進, 賣出, 現金(券)償還,
  前日餘額, 今日餘額) plus a 16-column per-security table in lots.
- TPEx `margin_bal`: 20 columns in lots, including 資使用率(%), 券使用率(%),
  資屬證金, and 券屬證金.

Every `margin_trading_versions` quantity column is sourced. The utilization
ratios exist for TPEx only; TWSE values are NULL.

- SBL: TWSE `TWT93U` (15 columns, two header rows) and TPEx `margin_sbl`
  (15 columns). Every `securities_lending_versions` column is sourced.

### 4.6 Official valuation

- TWSE `BWIBBU_d`: 證券代號, 證券名稱, 收盤價, 殖利率(%), 股利年度, 本益比, 股價淨值比,
  財報年/季. One anomalous 5-column file on 2025-06-24.
- TPEx `pera`: 股票代號, 公司名稱, 本益比, 每股股利(註), 股利年度, 殖利率(%), 股價淨值比.
  財報年/季 is added from 2025-01-02.

PE, PB, yield, and dividend year are sourced for both markets.
`dividend_per_share` exists for TPEx only. `report_period` exists for TWSE on
all dates and for TPEx from 2025-01-02.

### 4.7 Monthly revenue

MOPS `nas/t21/{sii|otc}/t21sc03_{ROC year}_{month}_{0|1}.html`. The `_0` page
lists domestic issuers; `_1` lists foreign/KY issuers (for example, 5871
中租-KY appears only in `_1`). Both state `單位：千元`.

```text
公司代號, 公司名稱, 當月營收, 上月營收, 去年當月營收, 上月比較增減(%), 去年同月增減(%),
當月累計營收, 去年累計營收, 前期比較增減(%), 備註
```

- The page header `出表日期` is the page generation date, not a publication
  time.
- Values are the latest corrected ones. First-published values cannot be
  recovered from this source. The only record of them is the legacy
  first-seen rows for 2026M02 onward (§7.1).
- Full history from 2020M01 is about 4 pages per month, roughly 330 requests.

| Field | Status |
| --- | --- |
| revenue | ✓ (×1,000 to TWD) |
| currency | a constant TWD page unit, not a per-row field |
| published comparatives (上月營收, 去年當月營收, three percentages, cumulative values, 備註) | ✓ in the source and read by legacy consumers, but not stored by the current schema |
| KY issuers | missing from legacy, which never fetched `_1`; 140 KY securities appear in legacy `daily_quotes` |

### 4.8 Financial statements (iXBRL)

MOPS `server-java/t164sb01`, one iXBRL HTML per `(CO_ID, SYEAR, SSEASON,
REPORT_ID)`. A 2025Q1 sample has 1,328 `ix:nonFraction` facts, contexts with
`xbrldi:explicitMember` dimensions, `unitRef` values (TWD, Shares, Pure,
EarningsPerShare), and `decimals`. This supports the `financial_facts` context
model.

The endpoint provides no filing ID, publication instant, or amendment
sequence, and it returns the currently effective, possibly amended, report. A
synthetic `filing_key` of `(security, year, quarter, report type)` matches the
endpoint's own request key. Full history is about 45,000 requests (the legacy
archive has 45,324 files).

### 4.9 TDCC shareholding distribution

- OpenData `getOD.ashx?id=1-5`: 資料日期, 證券代號, 持股分級, 人數, 股數,
  占集保庫存數比例%. It serves the **latest week only**.
- 持股分級 is a numeric level. The bulk file never states the share band each
  level means; the portal does. Extracted from all 1,849 files of the
  `shareholding_div` reconstruction before it was deleted, and identical in
  every one of them:

```text
 1  1-999                 6  20,001-30,000     11  200,001-400,000
 2  1,000-5,000           7  30,001-40,000     12  400,001-600,000
 3  5,001-10,000          8  40,001-50,000     13  600,001-800,000
 4  10,001-15,000         9  50,001-100,000    14  800,001-1,000,000
 5  15,001-20,000        10  100,001-200,000   15  1,000,001以上
16  差異數調整（說明4）, or 合計 where the security has no adjustment row
17  合計
```

  Level 16 carries two different labels across securities, so a parser must not
  assume 16 is always the adjustment row and 17 always the total.
- Portal `smWeb/qryStock`: per security, in a different format. On 2026-09-14
  its selector offered 51 dates, 2025-09-19 → 2026-09-11.
- Everything earlier exists only in the archive under
  `~/GitHubLL/my_stock_project/data/raw/shareholding` (ROADMAP §14);
  `stock-data-center/data/raw` holds no source archive, only the
  content-addressed artifact store.

#### The archive

`~/GitHubLL/my_stock_project/data/raw/shareholding` — 426 weekly files
(`.csv`, `.zip`, `.7z`), **375 weeks from 2019-06-28 to 2026-09-11**, of which
348 fall inside the v1 window from 2020-01-02. Every file carries the same
six-column OpenData header and one 資料日期, so a single parser handles all of
them. Zero filename/content mismatches.

It was consolidated on 2026-09-15 from three directories the legacy system and
later work had accumulated. What the survey found, by comparing payloads date by
date rather than just date lists:

- The former `TDCC` directory (371 weeks) **contained** the former
  `shareholding` (340 weeks) on all 336 overlapping dates: 211 identical row for
  row, and on the other 125 the `shareholding` rows were a strict subset, with
  no key present in `shareholding` and absent from `TDCC`.
- The cause was a legacy scraper change on **2023-09-15** that began filtering
  the OpenData file down to its own active-stock universe:

```text
20230908   TDCC 56,372 rows / 3,316 securities   shareholding  identical
20230915   TDCC 56,525 rows / 3,325 securities   shareholding 26,505 / 1,767
```

  The 1,558 dropped securities were ETFs and other non-common-stock codes: 0050,
  0051, 0052, 0053, 0055, 0056, 0057, 000815 and so on. 155 of the 340 files
  were filtered this way.
- The former `shareholding` held exactly four weeks the other lacked, all
  complete and all before the filter change. They were copied in and verified:

```text
2020-06-20   47,447 rows / 2,791 securities
2020-09-25   48,603 / 2,859
2021-02-19   49,606 / 2,918
2022-11-04   54,298 / 3,194
```

- `shareholding_div` was the per-security portal reconstruction of 2026-07-09,
  1,849 securities, built because the bulk file for that week was missing. The
  archive holds the real 4,003-security bulk file for that week, so it was
  deleted after its one unique contribution, the level codebook above, was
  extracted.

The old filtered directory is retained as `shareholding.bak`. It is not a
source. Its only remaining use is reconciling against the legacy database,
which was built from it.

#### Variants and defects the parser must handle

- **The filename is not authoritative.** Two files, `2020/20200619.CSV` and
  `2020/20200619.zip`, carried 資料日期 `20200612`. They were named for their
  download date: the 2020-06-20 re-download still returned the 2020-06-12 table,
  because OpenData serves the latest published week and the next one was not out
  yet. Both payloads are byte-identical to `2020/20200612.csv`
  (md5 `494878fb2d7abc0ad194d4ab45dc3815`), so they held nothing new; they were
  moved to `shareholding/_quarantine/` on 2026-09-15 with a note. There is no 2020-06-19 TDCC week at all — that week's data date is
  2020-06-20, held only by `shareholding`.
  The rule stands regardless: key on the 資料日期 column and reject any file
  whose name disagrees, because this defect is silent. Keying on the name here
  would have invented a 2020-06-19 week and hidden that 2020-06-20 was missing.
- **Date format**: 423 files use `20200103`; one, `2019/20190628.zip`, uses
  `2019/06/28`.
- **Double BOM**: ten files (both copies of 2020-04-30, 05-08, 05-15, 05-22 and
  05-29) carry a second `\ufeff` after `utf-8-sig` decoding.
- **Extension case**: `.csv`, `.CSV` and `.zip`, `.7z` all appear.
- **Duplicate copies**: 51 content dates have more than one file, mostly a
  `.csv` and a `.zip` of the same week in 2020. Every duplicate pair agrees
  exactly on row and security counts, so either copy may be kept.

#### Coverage

Row counts grow from 45,713 rows / 2,689 securities (2019-06-28) to
68,935 / 4,055 (2026). Every interval of 10 or more days is a Lunar New Year
closure:

```text
2021-02-09 -> 2021-02-19   (10 days)
2022-01-28 -> 2022-02-11   (14)
2023-01-19 -> 2023-02-04   (16)
2024-02-07 -> 2024-02-17   (10)
2025-01-24 -> 2025-02-08   (15)
2026-02-13 -> 2026-02-26   (13)
```

There is no unexplained gap. The former filtered directory alone had eight such
intervals, including 2021-11-26 → 2021-12-24.

### 4.10 Corporate actions: exchange result feeds

All of these were verified live on 2026-09-14 and serve historical date ranges.

- **TWSE `rwd/zh/exRight/TWT49U`**: 資料日期, 股票代號, 股票名稱, 除權息前收盤價,
  除權息參考價, 權值+息值, 權/息, 漲停價格, 跌停價格, 開盤競價基準, 減除股利參考價,
  詳細資料, 最近一次申報資料 季別/日期, 最近一次申報每股 (單位)淨值,
  最近一次申報每股 (單位)盈餘. 詳細資料 is the exchange locator `"{code},{yyyymmdd}"`,
  for example `1101,20240701`.
  **The locator exists only in `response=json`.** `response=csv`, which the legacy
  scraper used, flattens the column to the link label `除權息資料`, and 最近一次申報資料
  季別/日期 likewise loses its MOPS URL. Verified on 2026-09-15: the JSON row for
  00939 on 2026-01-02 carries `00939,20260102` where the archived CSV carries
  `除權息資料`. PR #19 must request JSON; the legacy CSV archive cannot supply
  Invariant G(2) identity.
  The 15-column header is byte-identical in every archived year 2020-2026, so
  this feed needs no header-variant handling.
- **TWSE `TWT49UDetail?STK_NO=&T1=`**: (每股配發現金股利)除息, (增資配股)除權,
  A. 每千股無償配股, B. 員工紅利轉增資, C. (有償)現金增資, 每股認購金額, a/b/c
  認購股數, 按股東持股比例每千股認購.
- **TWSE `rwd/zh/reducation/TWTAUU`** (capital reduction): 恢復買賣日期, 股票代號,
  名稱, 停止買賣前收盤價格, 恢復買賣參考價, 漲停價格, 跌停價格, 開盤競價基準, 除權參考價,
  減資原因, 詳細資料 (`"{code},{yyyymmdd}"`).
- **TWSE `TWTAVUDetail?STK_NO=&FILE_DATE=`**: 停止買賣日期, 每壹仟股換發新股票,
  每股退還股款, 原股每股配發現金股利, 減資並(有償)現金增資, 每股認購金額, 認購股數,
  按股東持股比例每千股認購.
- **TWSE `rwd/zh/change/TWTB8U`** (par-value change): 恢復買賣日期, 股票代號, 名稱,
  停止買賣前收盤價格, 恢復買賣參考價, 漲停價格, 跌停價格, 開盤競價基準, 詳細資料. The
  detail fields are not yet verified.
- **TPEx `www/zh-tw/bulletin/exDailyQ`**: 除權息日期, 代號, 名稱, 除權息前收盤價,
  除權息參考價, 權值, 息值, 權值+息值, 權/息, 漲停價, 跌停價, 開始交易基準價, 減除股利參考價,
  現金股利, 每仟股無償配股, 現金增資股數, 現金增資認購價, 公開承銷股數, 員工認購股數,
  原股東認購股數, 按持股比例仟股認購.
- **TPEx `www/zh-tw/bulletin/revivt`** (capital reduction): 恢復買賣日期, 股票代號,
  名稱, 最後交易日之收盤價格, 減資恢復買賣開始日參考價格, 漲停價格, 跌停價格, 開始交易基準價,
  除權參考價, 減資原因, and 詳細資料 as inline HTML (停止買賣日期, 恢復買賣日期,
  每壹仟股換發新股票, 每股退還股款, …).
- **TPEx par-value change**: no endpoint verified yet.

Event volumes in the legacy archive (TWSE, year-to-date files, 2020-01-01 →
2026-09-14): `TWT49U` about 1,280 rows in 2026 alone; `TWTB8U` par-value change
is rare — 1, 1, 1, 0, 1, 4, 2 events for 2020 through 2026. The legacy
`par_value_change/2023` directory is empty because the year had no events and
the scraper writes nothing on an empty response, not because the fetch failed.
The legacy system fetched one year-to-date request per feed per year and never
called any Detail endpoint, so no detail field in this section comes from the
archive.

| `corporate_action_versions` column | Exchange result feeds |
| --- | --- |
| ex_date (ex-right date or resumption date) | ✓ |
| close_before, official_reference_price | ✓ in every feed |
| official_rights_dividend_value | ✓ (`TWT49U`, `exDailyQ`) |
| cash_dividend_per_share | ✓ (`TWT49UDetail`, `exDailyQ`) |
| free_share_ratio | ✓ (free shares per 1,000 ÷ 1,000) |
| earnings_stock_ratio / capital_surplus_stock_ratio | ✗: exchange feeds publish only the combined free-share figure |
| rights_ratio, subscription_price | ✓ |
| old_shares / new_shares | ✓ for capital reduction (1,000 → 每壹仟股換發新股票); par-value change unverified |
| capital_reduction_kind, capital_reduction_cash_return_per_share | ✓ (減資原因, 每股退還股款) |
| announcement_date, record_date, payment_date | ✗ |

The issuer summary feeds (TWSE `t187ap45_L`, TPEx `mopsfin_t187ap39_O`) do
split earnings and capital-surplus stock dividends, but they lack a stable
event identity (see ROADMAP PR #13). Their contract and coverage limits are in
§4.13; PR #33 stores them as their own domain.

### 4.11 Security metadata, lifecycle, and tags

- PR #10 uses current snapshots (`t187ap03_L`, `mopsfin_t187ap03_O`). PR #11
  uses listing and delisting history. No official source of historical name or
  industry changes is used.
- `stock_tags` comes from MoneyDJ, a third party: a current snapshot with no
  effective dates.

### 4.12 Trading calendar

TWSE `afterTrading/FMTQIK?date=YYYYMM01` lists each actual trading day of the
month. For example, July 2024 omits the typhoon closures on 2024-07-24/25.
`holidaySchedule` lists planned closures only; the probe returned rows for 2023
onward and none for 2020.

### 4.13 Issuer dividend declarations

The legacy system never fetched these. Its MOPS usage was `nas/t21/*` (monthly
revenue), `server-java/t164sb01` (iXBRL), `server-java/t13sa150_otc` (TPEx
foreign holding), and the deprecated `ajax_t163sb04/05/20`. There is no archive
baseline for this domain.

**The primary source is the MOPS query endpoint, not the OpenAPI datasets.**
Verified live 2026-09-15:

```text
POST https://mopsov.twse.com.tw/server-java/t05st09sub
     encodeURIComponent=1&step=1&firstin=1&off=1
     TYPEK={sii|otc|rotc|pub}   market
     YEAR={ROC year}
     qryType={1|2}              1 = 董事會決議（擬議）分配股利年度 (resolution year)
                                2 = 股利所屬年度 (the year the dividend belongs to)
```

One request returns the whole market for one year as a big5 HTML table. The
endpoint is discoverable from the MOPS SPA route `t05st09_new`
(`assets/t05st09_new.js`). Parameter names are case-sensitive: `YEAR` works,
`year` returns the big5 error page `參數傳入錯誤`.

Observed row counts (qryType=1):

| TYPEK | 108 | 109 | 110 | 111 | 112 | 113 | 114 | 115 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| otc | 806 | 857 | 890 | 925 | 962 | 989 | 999 | 961 |
| sii | | 1,091 | | | | 1,247 | | |

`otc` 105 returns 4 rows, so usable coverage begins around 107. `sii` and
`qryType=2` behave the same (`otc/113/qryType=2` returns 996 rows).

The four `TYPEK` values are disjoint company sets, probed on 民國113:

| TYPEK | Meaning | Rows | Companies |
| --- | --- | --- | --- |
| `sii` | 上市 | 1,247 | 1,099 |
| `otc` | 上櫃 | 989 | 886 |
| `rotc` | 興櫃 | 262 | 246 |
| `pub` | 公開發行, traded on no board | 315 | 300 |

All six pairwise intersections are empty. `pub` includes issuers with no
exchange ticker at all, such as `000218 亞東證券`, so its codes do not resolve
against `securities`.

**`TYPEK` is the company's market status at query time, not at the dividend
year.** Evidence: of the 792 codes returned by `otc`/民國109, 58 have a TPEx
listing date after 2021-01-01 in the legacy `stock_info`, including `3313 斐成`
(listed 2026-06-01) and `3158 嘉實` (2025-11-17). Their pre-listing years, filed
while they were 興櫃, are returned under `otc` today.

Two consequences:

1. Fetching `sii` and `otc` alone gives the complete declaration history for the
   v1 universe. `rotc` and `pub` add only companies outside it, and ROADMAP §1.1
   excludes them from v1 by decision.
2. A past-year query is not a stable result set. Re-running 民國109 after a new
   TPEx listing returns rows that were absent before, with no change at the
   source. An importer must treat these as new rows for an old period, never as
   corrections to existing rows, and must not read a shrinking row set as
   retraction.

**Header variant at 民國110.** The `股東配發內容` column group changes width:

| ROC years | Sub-columns | Cash split | Stock split |
| --- | --- | --- | --- |
| ≤ 109 | 6 (19 cells per row) | 盈餘分配 / **法定盈餘公積、資本公積 combined** | 盈餘轉增資 / **法定盈餘公積、資本公積 combined** |
| ≥ 110 | 8 (21 cells per row) | 盈餘分配 / 法定盈餘公積 / 資本公積 | 盈餘轉增資 / 法定盈餘公積轉增資 / 資本公積轉增資 |

So 盈餘轉增資配股 — the earnings stock dividend — is available for every year.
Only the split between 法定盈餘公積 and 資本公積 is unavailable before 民國110,
where the two arrive as one figure.

The other 13 logical columns are stable across all probed years: 公司代號/名稱,
決議（擬議）進度, 股利所屬年(季)度, 股利所屬期間, 期別, 董事會決議（擬議）股利分派日,
股東會日期, 期初未分配盈餘/待彌補虧損, 本期淨利(淨損), 可分配盈餘,
分配後期末未分配盈餘, 股利分派之公司章程, 備註.

The page footnote states outright that ex-dividend trading dates and reference
prices are **not** in this table and must be read from the exchange result
feeds, which is independent confirmation of Invariant G(1).

#### OpenAPI mirrors, useful only as cross-checks

| Feed | 出表日期 | Covers | Rows |
| --- | --- | --- | --- |
| TWSE `openapi.twse.com.tw/v1/opendata/t187ap45_L` (上市公司股利分派情形) | 1150914, refreshed | 股利年度 114-115 | 1,226 |
| TPEx `www.tpex.org.tw/openapi/v1/mopsfin_t187ap39_O` (上櫃股利分派情形-董事會通過) | 1100804, **frozen since 2021-08-04** | 股利年度 107-110 | 2,483 |

Both are JSON, both are one request, and neither takes a year parameter. The
TPEx dataset is still listed in the TPEx catalogue but its content stopped
updating at the 民國110 header change; it carries the old 6-column layout. It is
TPEx's own republication going stale, not TPEx or MOPS ceasing to publish — MOPS
`t05st09sub` with `TYPEK=otc` serves 110 through 115 normally. `出表日期` is one
page-level value per file, not a per-row publication time.

#### Identity

Within a feed:

- MOPS and TWSE OpenAPI: `(公司代號, 股利年度, 股利所屬期間, 期別)`. On the TWSE
  OpenAPI snapshot this is 1,226 distinct over 1,226 rows. `(公司代號, 股利年度,
  期別)` alone has 30 duplicate groups there.
- TPEx OpenAPI, which has no 股利所屬期間: `(公司代號, 股利年度, 期別)` has 65
  duplicate groups; adding 董事會決議通過股利分派日 leaves 2, `5009/108/1/1080805`
  and `8426/108/1/1090319`.
- Why duplicates exist: 1591 carries two rows for 股利年度 109, resolved 1090804
  with 0 cash and 1100506 with 0.50 cash. These are a plan and its later
  revision, not two events.

No feed in this section carries an ex-dividend date, a record date, a payment
date, or a locator tying a row to an executed event, so Invariant G(1) holds:
these are announcement feeds and cannot enter `corporate_action_versions`.

#### Rate budget

`t05st09sub` lives on `mopsov.twse.com.tw`, the same host that blocked the
legacy scraper on 2026-07-02, so the 3-second interval applies. The volume is
small: 2 markets × 9 ROC years = 18 requests for the whole history, then 2 per
day to track revisions.

## 5. Schema columns with no source, or with partial coverage

One row per stored column that an official source does not fully provide. An
`unsourced` column has no source field at all; a `partially sourced` column
exists for only some dates, markets, or securities, and the reason says which.
Neither may be presented as data the source publishes.

**Unsourced does not mean NULL.** Six of these columns are `NOT NULL`, so the
Effect column says what each one actually holds:

- *stays NULL* — nothing is written, and the API reports it as unavailable;
- *stores a documented constant* — the source publishes the value once, at page
  level, not per row;
- *stores a derived value* — the column holds something of ours, such as an
  observation date, and never claims to be the source's;
- *table stays empty* — the whole domain is out of v1 (ROADMAP §16), so the
  question of what the column holds does not arise.

This table is the normative list. The machine-readable per-column contract in
`data_domain_inventory.json` (`storage_contract`) must agree with it exactly,
and a unit test fails if it does not — including a check that every column
marked *stays NULL* is in fact nullable in the live schema.

| Column | Status | Effect | Why |
| --- | --- | --- | --- |
| `corporate_action_versions.announcement_date` | unsourced | stays NULL | No exchange result feed carries an announcement date. The issuer declaration feeds that do carry board-resolution dates have no link to an executed event (PR #33, audit 4.13). |
| `corporate_action_versions.record_date` | unsourced | stays NULL | No exchange result feed carries a record date. |
| `corporate_action_versions.payment_date` | unsourced | stays NULL | No exchange result feed carries a payment date. |
| `corporate_action_versions.earnings_stock_ratio` | unsourced | stays NULL | The exchange feeds publish only the combined free-share figure. The earnings / capital-surplus split exists only in the MOPS issuer declaration feed, stored as its own domain by PR #33 (audit 4.13). |
| `corporate_action_versions.capital_surplus_stock_ratio` | unsourced | stays NULL | The exchange feeds publish only the combined free-share figure. The split exists only in the MOPS issuer declaration feed (PR #33), and before ROC 110 the two reserves arrive as one number (audit 4.13). |
| `corporate_action_versions.old_shares` | partially sourced | holds the values that exist | Capital reduction only: the old side is the constant 1,000 of 每壹仟股. TWTB8U par-value-change detail fields are not yet verified and no TPEx par-value endpoint was found. |
| `corporate_action_versions.new_shares` | partially sourced | holds the values that exist | Capital reduction only. TWTB8U par-value-change detail fields are not yet verified and no TPEx par-value endpoint was found. |
| `daily_price_versions.price_direction` | partially sourced | holds the values that exist | TWSE only. stk_wn1430 carries a signed 漲跌 and no direction column. |
| `daily_price_versions.bid_snapshot` | unsourced | stays NULL | Multi-level order-book depth blob. No daily whole-market endpoint publishes depth; the one published level is stored in last_bid_price/last_bid_volume. |
| `daily_price_versions.ask_snapshot` | unsourced | stays NULL | Multi-level order-book depth blob. No daily whole-market endpoint publishes depth; the one published level is stored in last_ask_price/last_ask_volume. |
| `daily_price_versions.last_bid_volume` | partially sourced | holds the values that exist | TWSE on all dates, in lots. TPEx only from 2020-04-30; the label changes 千股 to 張數 on 2025-01-10, both meaning 1,000 shares. |
| `daily_price_versions.last_ask_volume` | partially sourced | holds the values that exist | TWSE on all dates, in lots. TPEx only from 2020-04-30; the label changes 千股 to 張數 on 2025-01-10, both meaning 1,000 shares. |
| `margin_trading_versions.margin_utilization_ratio` | partially sourced | holds the values that exist | TPEx margin_bal only; MI_MARGN publishes no utilization ratio, so the TWSE values stay NULL. |
| `margin_trading_versions.short_utilization_ratio` | partially sourced | holds the values that exist | TPEx margin_bal only; MI_MARGN publishes no utilization ratio, so the TWSE values stay NULL. |
| `market_index_metadata_versions.effective_from` | unsourced | stores a derived value | No official effective date. The column is NOT NULL and holds the first observation date of the published name, which is ours, not the source's. |
| `market_index_metadata_versions.effective_to` | unsourced | stores a derived value | No official effective date. The column holds the last observation date of the published name, which is ours, not the source's. |
| `market_index_versions.open_value` | partially sourced | holds the values that exist | TAIEX only, from rwd/zh/TAIEX/MI_5MINS_HIST (one calendar month per request). The whole-list index sources publish no OHLC and no TPEx equivalent was found. |
| `market_index_versions.high_value` | partially sourced | holds the values that exist | TAIEX only, from rwd/zh/TAIEX/MI_5MINS_HIST. The whole-list index sources publish no OHLC and no TPEx equivalent was found. |
| `market_index_versions.low_value` | partially sourced | holds the values that exist | TAIEX only, from rwd/zh/TAIEX/MI_5MINS_HIST. The whole-list index sources publish no OHLC and no TPEx equivalent was found. |
| `market_index_versions.trade_value` | unsourced | stays NULL | Not in any inspected index source. |
| `monthly_revenue_versions.currency` | unsourced | stores a documented constant | A page-level constant, not a per-row observation: the page states 單位：千元. The column is NOT NULL and part of the revision identity, so it stores the constant TWD; it is never NULL. |
| `official_valuation_versions.dividend_per_share` | partially sourced | holds the values that exist | TPEx pera only; BWIBBU_d has no per-share dividend column. |
| `official_valuation_versions.report_period` | partially sourced | holds the values that exist | TWSE on all dates; TPEx only from 2025-01-02. |
| `security_metadata_versions.name` | partially sourced | holds the values that exist | The snapshot publishes the current name only. No official source of historical name changes is used, so earlier effective dates carry the current value. |
| `security_metadata_versions.industry` | partially sourced | holds the values that exist | The snapshot publishes the current industry only. No official source of historical industry changes is used. |
| `security_tag_versions.tag` | unsourced | table stays empty | Only a third-party (MoneyDJ) snapshot exists; no official source publishes security tags. |
| `security_tag_versions.effective_from` | unsourced | table stays empty | The third-party snapshot carries no effective dates. |
| `security_tag_versions.effective_to` | unsourced | table stays empty | The third-party snapshot carries no effective dates. |
| `xbrl_concept_catalog_versions.concept_qname` | unsourced | table stays empty | No official concept-catalogue endpoint was inspected; the audit covers only the iXBRL documents themselves (4.8). |
| `xbrl_concept_catalog_versions.statement_type` | unsourced | table stays empty | No official concept-catalogue endpoint was inspected. |
| `xbrl_concept_catalog_versions.account_name_zh` | unsourced | table stays empty | No official concept-catalogue endpoint was inspected. |
| `xbrl_concept_catalog_versions.account_name_en` | unsourced | table stays empty | No official concept-catalogue endpoint was inspected. |

Beyond the stored columns, `publication_evidence.published_at` has no official
source at all: no inspected source publishes a per-row release instant (§7).
ROADMAP PR #15 is the decision point for what to do about that.

Every other column of every table in the storage contract is either sourced for
the whole v1 window and both markets — §4 names the endpoint and the published
field label for each one — or internal: identity, an interval boundary, or
provenance linkage that is not expected to come from a source field.

## 6. Source fields not stored

| Field | Decision |
| --- | --- |
| Monthly-revenue published comparatives | Store as observed (ROADMAP PR #22); legacy consumers read them |
| TPEx daily 發行股數 and next-day limits | Not stored; `foreign_holding.issued_shares` covers both markets |
| TPEx margin 資屬證金/券屬證金, TWSE 註記 | Not stored; no consumer |
| TPEx institutional foreign/dealer totals | Not stored; they are sums of stored columns |
| Ex-right limit prices, opening reference, 減除股利參考價, latest NAV/EPS | Keep in `source_terms` |

## 7. Publication time

None of the inspected sources gives a per-row release instant:

- The exchange feeds carry only the data date.
- MOPS `t21sc03` carries only the page generation date.
- `t164sb01` carries none.
- TDCC carries the data date.

Under the current rules, all imported history therefore has
`published_at = NULL` and is invisible to Market PIT. ROADMAP PR #15 is the
decision point for this.

**Same-day rows are published before they are final.** Observed 2026-09-15 at
14:29 Asia/Taipei, an hour after the 13:30 close, on `STOCK_DAY` for 2330:

```text
115/09/14   volume 21,520,958   trade_count 189,734   =  113 shares/trade
115/09/15   volume 13,469,000   trade_count   7,205   = 1,869 shares/trade
```

Volume, value, and OHLC are self-consistent for 09/15, but `成交筆數` is an
order of magnitude too low for that volume. The exchange serves the row while it
is still settling. This is direct evidence against any release rule that resolves
on the trade date itself, independent of the legacy scheduling evidence in §7.1.

### 7.1 Legacy first-seen capture dates

The legacy system did record real capture dates for recent periods. They are
upper bounds on publication at date precision, not release instants.

**Monthly revenue**

The systemd timer `stock-monthly-update` runs daily at 22:45 Asia/Taipei. The
script skips days outside the 1st–15th, and it fetches only the previous
month. A new `(market, symbol)` row is stamped with the run date. Checked in
both `stock_db` and the pre-overwrite backup
`data/backup_monthly_revenue_pre_publish_time_20260913.tar.gz`:

This table describes the legacy **database**. The legacy **archive** CSVs under
`data/raw/monthly_revenue/` no longer match it for the first window: `revswarm`
wrote its recovered announcement dates back into them (§7.4), so those files
carry per-row dates. Sampled 2026-09-15: `2021M05/market.csv` has 1,692 rows
with 11 distinct `publish_time` values from 2021-06-01 to 06-11, 809 of them on
06-10. The Data Center reads the CSV, so ADR-0020 treats the first window as
`press_report_bound`, not as a synthetic deadline.

| Months | `publish_time` in `stock_db` | Meaning |
| --- | --- | --- |
| 2020M01 → 2026M01 (73 months) | one value per month: the 10th of the next month | synthetic statutory deadline, assigned after the fact |
| 2026M02 → 2026M08 (7 months) | 11–15 distinct dates per month, from the 1st to the 13th–15th | real first-seen date of the 22:45 run; values are first-captured, not later corrections |

Limits:

- Rows that first appeared after the 15th were never captured. For 2026M02
  onward, a `_0` row that appears in an official re-fetch but not in the
  legacy record was not public at the last legacy run.
- Stored rows were never updated, so later corrections are not recorded.
- A capture date is an upper bound, and capture failures can make it loose.

First-seen rows per month:

| Month | Rows | First seen after the 10th |
| --- | ---: | ---: |
| 2026M02 | 1,823 | 13 |
| 2026M03 | 1,832 | 17 |
| 2026M04 | 1,842 | 309 |
| 2026M05 | 1,848 | 311 |
| 2026M06 | 1,850 | 204 |
| 2026M07 | 1,851 | 17 |
| 2026M08 | 1,841 | 4 |

- **2026M04**: 2026-05-10 was a Sunday, and 292 rows were first seen on
  Monday 05-11. This is consistent with a deadline that moves to the next
  business day, so a fixed "10th" rule would be one day too early for those
  companies.
- **2026M05 and 2026M06**: batches on 06-11 (297 rows; the 10th was a
  Wednesday) and on 07-13 (181 rows) are not explained by weekends. They are
  probably capture gaps. The run logs for those days are no longer retained.

**XBRL**

The filename suffix is the run date: of `xbrl_scrape_daily` (timer 23:50
Asia/Taipei) or of a manual backfill run. A run can cross midnight, so the
file mtime is the tighter capture instant.

Current capture windows in `schedules/xbrl_scrape_daily.sh` (since commit
`a680e5f`, 2026-08-02; earlier windows opened sooner, for example 2026Q2 from
07-01):

```text
Q4 (prior year)  03/01 – 03/31   statutory deadline 03/31
Q1               04/15 – 05/15   statutory deadline 05/15
Q2               07/15 – 08/15   statutory deadline 08/14, window +1 day
Q3               10/15 – 11/15   statutory deadline 11/14, window +1 day
```

The window end dates (03/31, 05/15, 08/15, 11/15) match the legacy cutoffs in
`train_eps/shared_config.py`.

| Quarter | Filename suffix | Meaning |
| --- | --- | --- |
| 2020Q1 → 2025Q3 | one value per quarter: the window end date (for example 2020Q2 → 2020-08-15, 2022Q3 → 2022-11-15, 2024Q4 → 2025-03-31) | synthetic, from a February 2026 backfill |
| 2025Q4 | 1,653 files dated 2026-03-03 → 03-31; 178 dated 2026-08-17 | daily-job first-seen dates; backfill run |
| 2026Q1 | 1,647 files dated 2026-04-13 → 05-15; 20 dated 2026-08-01 and 176 dated 2026-08-17 | daily-job first-seen dates; backfill runs |
| 2026Q2 | 1,649 files dated 2026-07-29 → 08-15; 168 dated 2026-08-17 | daily-job first-seen dates; backfill run |

A date after the quarter's window end comes from a backfill run, not from
first-seen capture. The 2026-08-17 files are mostly companies that file only
individual (non-consolidated) reports. The legacy scraper could not fetch them
until the `REPORT_ID` fallback fix of 2026-08-16 (see `fetch_xbrl.py`). Their
capture date says nothing about when they filed.

Financial industry (ROADMAP §26.3 excludes their financial statements from v1 by
owner decision, matching legacy; the facts below stand as the record of why):

- Legacy excludes financial stocks on purpose: the converter cannot parse the
  financial chart of accounts (`KNOWN_ISSUES.md`).
- `KNOWN_ISSUES.md` records later deadlines for them: Q1/Q3 5/30 and
  semiannual 8/31.
- The raw archive still holds some financial filings. In 2026Q1, twelve
  financial holdings were first captured by the 2026-08-01 backfill. In
  2026Q2, only 4 financial codes were captured at all.
- The window end dates are therefore not valid release bounds for financial
  companies.

Statutory deadlines that fall on a non-business day move to the next business
day. Examples inside v1 history: 2021-05-15 (Sat), 2022-05-15 (Sun), 2021-08-14
(Sat), 2020-11-14 (Sat), and 2024-03-31 (Sun). A fixed window-end rule would be
one or two days too early in those cases.

### 7.2 Legacy daily-job completeness

The legacy timers are daily-update 23:30, daily-retry 03:00, weekly Sunday
10:20, monthly-revenue 22:45 on days 1-15, and XBRL 23:50.

`logs/daily_retry_*.log` covers 38 consecutive days to 2026-09-14, of which 27
are trade dates. Five were incomplete at 23:30:

| Trade date | Missing at 23:30 | Complete at 03:00? |
| --- | --- | --- |
| 2026-08-18 | TWSE institutional investors | yes |
| 2026-09-01 | TPEx PE ratio | yes |
| 2026-09-02 | TPEx margin trading, TPEx SBL | no — SBL only |
| 2026-09-03 | TPEx PE ratio | yes |
| 2026-09-07 | TPEx margin trading | yes |

So 5 of 27 trade dates (18.5%) were incomplete at 23:30 and 1 of 27 (3.7%) at
03:00. The single 03:00 failure, TPEx SBL for 2026-09-02, was saved at
2026-09-03 09:54, so an 08:00 rule would not have covered it either; and that
timestamp is our own save time, which cannot distinguish a late TPEx
publication from a failed fetch. `error_scraper.log` records missing files
without an HTTP status.

Combined with the same-day incompleteness in §7, this is the evidence behind
the 03:00 next-day release rule for exchange daily datasets (ROADMAP PR #15).

### 7.3 Published comparatives as correction evidence

MOPS monthly revenue publishes 上月營收 alongside 當月營收 (§4.7). The
comparative is the issuer's view of the prior month *at that filing*, not a
copy of what was published then.

Measured on the legacy archive, 1,846 companies present in both 2026M06 and
2026M07: on 11 of them the 2026M07 page's 上月營收 differs from the 2026M06
page's 當月營收.

```text
6441 廣錠    13,094 -> 10,948   -16.4%
5548 安倉   208,439 -> 191,673    -8.0%
4968 立積   330,254 -> 317,192    -4.0%
1903 士紙    11,370 ->  11,611    +2.1%
```

These are issuer corrections made when filing the next month. Storing the
comparatives verbatim makes a correction visible inside a single capture
instead of requiring a diff against a retained earlier page. They are stored as
observed and never reconciled against our own series; a disagreement is the
data.

### 7.4 Recovered monthly-revenue publication dates (`revswarm`)

`~/GitHubLL/revswarm` is a distributed crawler that reconstructs the
announcement date of each monthly revenue filing. Inspected 2026-09-15.

Why it exists: MOPS publishes no per-company filing date. Its README records
that the structured revenue pages (MOPS, Yahoo, MoneyDJ) carry only year-month
and amount, so the date survives only inside news articles.

```text
scope     1,848 securities x 73 months (民國109/1 - 115/1) = 134,904 tasks
engines   Yahoo TW search 101,107 | Google 30,475 | Gemini 3,322
verifier  claude 63,478 | codex 49,639 | mops 2,376 | unverified 19,411
trusted   state='success' AND verified IN (mops, gemini, claude, codex)
          = 115,493 tasks, 85.6% of all tasks
precision day, no time of day
```

Three contamination guards, all recorded in the README as measured decisions:
a worker-side window filter accepting only dates in the 1st-15th of the month
after the revenue month; an exact company-name anchor (so 統一 does not match
統一超); and a server-side re-validation of the window that also rejects
name collisions and articles that are not revenue announcements.

`export_publish_time.py` writes only trusted rows into the legacy
`market.csv`, through 2026M01 only, changing one cell per row, refusing any
file that would not round-trip byte-identically. It was last run 2026-09-13
11:02, two minutes after the `revswarm.db` mtime, so the CSV is current. The
CSV is the interface the Data Center reads; the database is not.

Resulting coverage of the legacy archive:

| Window | Rows | Recovered date | Left at the statutory 10th |
| --- | --- | --- | --- |
| 2020M01 - 2026M01 | 128,063 | 114,910 (89.7%) | 13,153 (10.3%) |
| 2026M02 onward | 12,894 | — | — (real capture dates from the legacy 22:45 job, §7.1) |

A recovered date equal to the 10th is not distinguishable from the fallback by
value alone: 27,035 recovered rows genuinely fall on the 10th. Per-row
provenance (`engine`, `verified`, `raw_title`, `url`) exists only in
`revswarm.db`, which is not exported.

This costs less than it looks. Of the 128,063 rows in the window, 40,188 sit on
the 10th; for 36,492 of those the 10th is a business day, so the release rule
resolves to that same instant and treating them as rule-bound changes nothing.
Only 3,696 rows (2.9%) have a weekend 10th, where the rule moves to the next
business day and the row resolves 1-2 days late. Late is safe; early is not.
PR #22 therefore reads the CSV and treats any row on the 10th as rule-bound.

**Independent quality check.** `revswarm` also captured the announced revenue
from the article headline, rounded to 0.01億, for 110,653 trusted rows.
Comparing the 110,103 that match a legacy row against the value MOPS serves
today:

```text
agree within the 0.01億 rounding   109,116   99.10%
disagree                               987    0.90%
  of which by more than 5%             734
```

The 99.10% agreement is a strong independent validation of the dataset. The
0.90% is an upper bound on historical corrections, not a measurement of them:
the extremes (4162 2020M12, +3,149%; 6169 2025M03, +1,277%) are headline
extraction artifacts such as a cumulative figure reported in place of a monthly
one, not revisions.

**What this does and does not recover.** It recovers the publication *date*,
which is what Market PIT needs. It does not recover the first-published
*value*: the headline figure is rounded to 0.01億, enough to detect that a
correction happened but not to restore the original 千元 number.
