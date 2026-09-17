# Source Field Audit

Status date: 2026-09-16.

This document records which fields the legacy database, the legacy raw archive,
and the official endpoints actually provide. `ROADMAP.md` uses it as the
source-reality baseline: a planned step may promise a stored field only if this
audit (or the step's own verified update to it) names the source field that
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
| TWSE/TPEx daily CSVs (quotes, institutional, foreign holding, margin, SBL, PE, summary, TPEx indices) | Requested with `response=csv`, so the JSON the endpoints also serve — and its `hints`, `notes`, `date` and per-table `fields` — never existed in the archive. Decoded as big5 with `errors="ignore"`, `="…"` wrappers stripped, single-cell rows dropped (`len(row) > 1`), rewritten as quoted UTF-8-BOM CSV, and skipped entirely when the response was under a size floor. The report date survives only in the directory name. | **No.** The bytes are not the official response, and the file cannot say which date it is. |
| TPEx foreign holding | MOPS `t13sa150_otc` POST, rewritten through pandas | No |
| Monthly revenue | Parsed CSV, not the MOPS HTML. Only `_0` (domestic-issuer) pages were fetched. Each month's `market.csv` appends only new `(market, symbol)` keys, so later corrections were never recorded. `publish_time` has two regimes (§7.1). | Not source bytes, but the 2026M02 onward rows are first-seen capture evidence |
| XBRL | iXBRL HTML decoded and rewritten as UTF-8 text. The filename date suffix has two regimes (§7.1). Files for 2020Q1–2025Q3 have February 2026 mtimes. | Not source bytes. 2020Q1–2025Q3 are no older than a fresh re-fetch; 2025Q4 onward are first-seen versions |
| TDCC weekly, the consolidated `shareholding` archive (§4.9) | OpenData bytes written unchanged, `.csv`/`.zip`/`.7z` | **Yes**, for all 375 weeks. |
| Corporate-action year-to-date files (`TWT49U`, `TWTAUU`, `TWTB8U`) | One `all.csv` per year, overwritten daily. Only 2026 keeps the cp950 original. TWSE only. | Partially (2026 only) |
| `stock_info`, `stock_tags` | Parsed current snapshots | No |

The daily archive is also a **re-fetch snapshot, not a daily capture**, which
matters wherever it is used as a reconciliation baseline. `fetch_daily_sii.py`
and `fetch_daily_otc.py` skip any date whose file already exists unless
`FORCE_REPROCESS=1`, and the file modification times say when each one was
actually written:

| When written | `daily_quotes/*/sii.csv` files |
| --- | ---: |
| 2026-01 | 941 |
| 2026-02 | 551 |
| 2026-03 onward | about 21 per month |

So roughly 92% of the window was fetched in one campaign in January and February
2026. For those dates the archive holds the values the exchange served *then*,
already including any correction made between the trade date and that campaign —
it is not first-published data, and it carries no first-seen evidence for daily
prices (CLAUDE.md §32). For dates captured since, skip-if-exists means the
opposite gap: a correction published after the capture day was never recorded.

A row-level difference against this baseline is therefore not automatically a
defect on our side. It can equally be the official value having changed since
the archive was written. Step 17-c's reconciliation classifies such a row as
`legacy_snapshot_differs` and reports it for inspection rather than calling it
either way.

Because the official endpoints still serve 2020 onward for every domain except
TDCC, re-fetching gives byte-faithful artifacts through the existing Step 9
raw-first lifecycle. The archives are needed for TDCC history, for the recovered
monthly-revenue publication dates (§7.4), for the pre-2026 first-seen records
(§7.1), and as a reconciliation baseline.

## 4. Per-domain source fields

✓ = the field exists in the source; ✗ = it does not; "partial" = only for some
dates or markets.

### 4.1 Daily quotes

TWSE `rwd/zh/afterTrading/MI_INDEX?date=YYYYMMDD&type=ALLBUT0999&response=json`
returns one file per trade date: six index sections, two market-summary
sections, then the stock section. The stock-section header is the same for all
1,627 files from 2020-01-02 to 2026-09-11, and the section is identified by that
header rather than by its position among the tables:

```text
證券代號, 證券名稱, 成交股數, 成交筆數, 成交金額, 開盤價, 最高價, 最低價, 收盤價,
漲跌(+/-), 漲跌價差, 最後揭示買價, 最後揭示買量, 最後揭示賣價, 最後揭示賣量, 本益比
```

The section states its own units in `hints`: `單位：元、股`. The `漲跌(+/-)`
cell arrives as coloured markup — `<p style= color:red>+</p>`, `<p> </p>`,
`<p>X</p>` — and the table's own note reads `+/-/X表示漲/跌/不比價`. On every
`X` row and every blank row of every file inspected, `漲跌價差` is `0.00`, so
the magnitude under an `X` is filler rather than a published change. A date the
market never opened answers `stat` `很抱歉，沒有符合條件的資料!` with no tables.

TPEx `www/zh-tw/afterTrading/otc?date=YYYY/MM/DD&type=EW&response=json` (the
feed this audit calls `stk_wn1430`, one file per trade date) has three header
variants, re-verified live on 2026-09-16 at their own boundary dates:

| Dates | Header | `flagField` |
| --- | --- | --- |
| 2020-01-02 → 2020-04-29 (75 files) | 代號, 名稱, 收盤, 漲跌, 開盤, 最高, 最低, 成交股數, 成交金額(元), 成交筆數, 最後買價, 最後賣價, 發行股數, 次日漲停價, 次日跌停價 | 千股 |
| 2020-04-30 → 2025-01-09 (1,147) | adds 最後買量(千股), 最後賣量(千股) | 千股 |
| 2025-01-10 → 2026-09-11 (405) | relabelled 最後買量(張數), 最後賣量(張數) (1 張 = 1,000 shares) | 張數 |

The response carries `flagField` naming its own disclosed-volume label, which
cross-checks the header variant. TPEx signs `漲跌` itself and publishes no
direction column; where TWSE writes `X`, TPEx writes the reason — `除息`,
`除權`, `除權息` — in the same cell, which is the same 不比價 statement and
leaves no number to store. A closed date answers `stat` `ok` with zero rows.

**How far back each endpoint serves**, probed 2026-09-16. The v1 window starting
on 2020-01-02 is a ROADMAP scope decision, not an endpoint limit — both feeds
reach considerably further:

| | Earliest date with rows | How the endpoint answers before it |
| --- | --- | --- |
| TWSE `MI_INDEX` | **2004-02-11** | `stat` = `查詢日期小於93年2月11日，請重新查詢!`. Verified at the boundary: 02-10 refused, 02-11 returns 711 rows. |
| TPEx `otc` | **2007-07-02** | `stat` = `ok` with zero rows, and no explanation. Verified at the boundary: 2007-06-29, the previous trading day, returns zero rows; 07-02 returns 826. |

TPEx's silence matters for anything that walks a date range: an out-of-range
date and a market closure are the same response, which is why Step 17-a names
that reason code `no_data_for_date` rather than `market_closed`. Only the
trading calendar can call a date a closure. The empty responses also carry a
17-column header regardless of era, while 2007-07-02 itself carries the
15-column first variant, so the header does not indicate the range either.

Extending the window past 2020 would need two things beyond changing a date:
the Step 16 calendar covers only 2020-01 onward and the range runner fails
closed outside it, and the two markets would need different
`dataset_expected_coverage.window_start` values, because TWSE reaches three
years further back than TPEx.

| `daily_price_versions` column | TWSE | TPEx |
| --- | --- | --- |
| open/high/low/close, volume, trade_value, trade_count | ✓ | ✓ |
| price_change | ✓ (unsigned 漲跌價差 + sign column) | ✓ (signed) |
| price_direction | ✓ | 不比價 marker only |
| last_bid_price / last_ask_price | ✓ | ✓ |
| last_bid_volume / last_ask_volume | ✓ (shares) | partial (from 2020-04-30, lots) |
| bid_snapshot / ask_snapshot | ✗ | ✗ |

Source fields not stored: TPEx 發行股數 and next-day limit prices; TWSE 本益比
(duplicated by `BWIBBU_d`).

Both feeds publish shares and whole TWD for traded quantity and value. The
disclosed bid/ask level is **not** the same unit in the two markets, and each
side says so itself:

- TWSE declares `單位：元、股` for the whole table and makes no other unit
  statement about it, so that column is shares. Corroborated by `TWT53U`, the
  odd-lot report, which carries the same column labels under the same hint and
  shows `最後揭示買量 = 200,937` for 2330 on 2026-09-11 — a quantity that can
  only be shares. Reading it as lots would multiply every TWSE bid/ask level by
  1,000; on 2026-09-11 that would have put 4,171,000 shares at 00648R's best
  bid against a whole-day volume of 604,430.
- TPEx labels the column itself, `最後買量(千股)` then `最後買量(張數)`, both
  meaning 1,000 shares, so only TPEx converts.

An earlier revision of this audit recorded the TWSE column as lots. That was
never sourced; it is corrected here. Note 3 of the TWSE table,
`除境外指數股票型基金及外國股票第二上市外，餘交易單位皆為千股`, is about the
**trading unit** (board lot) of each security, not about the unit this column
is expressed in, and no stored column depends on it.

Step 17-a's adapters take each unit from the feed that declares it, and the
TWSE adapter re-checks `hints` on every parse: a restatement to `仟股` is a
contract change, not something to discover later in the numbers.

Legacy `daily_quotes` is a subset of both feeds. On every date checked it holds
no row the feed lacks, and the feed holds rows it does not: securities that did
not trade that day (legacy's minimum volume over the whole window is 1), and
instrument classes the legacy scraper never collected — ETFs, preferred shares,
TDRs and similar. On 2026-09-11 that is 287 extra TWSE rows (9 untraded) and 150
extra TPEx rows (29 untraded).

The Step 9 pilot adapters (`STOCK_DAY`, `tradingStock`) take one request per
security per month. Daily capture for about 2,200 securities would need about
2,200 requests per trade date, so these adapters suit pilots and spot checks,
not production. They are also a different field set — no disclosed bid/ask
level — which is why Step 17-a gives the whole-market feeds their own source
codes, `twse_mi_index` and `tpex_otc_quotes`, rather than more revisions of the
pilots' rows.

### 4.2 Market indices

- TWSE: the `MI_INDEX` index sections (`指數`/`報酬指數`, 收盤指數, 漲跌(+/-), 漲跌點數,
  漲跌百分比(%), 特殊處理註記).
- TPEx: `afterTrading/indexSummary` (指數, 收市指數, 漲跌, 漲跌幅度(%), 大盤資訊連結).

Neither source publishes an index code, so identity has to be built — and the
published name alone is not enough. **TPEx repeats one name across its two
sections**: on 2026-09-11 `櫃買指數` appears in the price section at 395.52 and
in the return section at 735.15, and 32 of its 34 names are in both. TWSE
happens to name its return indices distinctly (`發行量加權股價指數` versus
`發行量加權股價報酬指數`), but the identity has to hold for both feeds, so it is
`(source, section, published name)`. The section is structural rather than a
business value: a price index does not become a return index.

Legacy `market_indices` kept only one TPEx section — 43 OTC names in total, with
`櫃買指數` appearing exactly once per trade date — so the whole TPEx return
series is absent from it and is new data here, not a reconciliation difference.

In these whole-list sources `close_value`, `change_points`, and
`change_percent` are sourced; `open_value`, `high_value`, `low_value`, and
`trade_value` are not.
`market_index_metadata_versions.effective_from/effective_to` can only record
first and last observation dates.

Index OHLC exists for one index per market, in endpoints the legacy system never
fetched. TWSE: `rwd/zh/TAIEX/MI_5MINS_HIST?date=YYYYMM01&response=json`
(`發行量加權股價指數歷史資料`) returns 日期, 開盤指數, 最高指數, 最低指數, 收盤指數 for one
calendar month per request, verified live for 2026-01. It covers only
`發行量加權股價指數`, not the other ~270 published indices, and carries no trade
value.

**TPEx does publish the equivalent, and it is still not backfillable.** An
earlier revision of this audit recorded a negative result after `indexes/histIndex`
and `openapi/v1/tpex_otc_index_history` both 404'd. Step 18-a's spike found
`openapi/v1/tpex_index` (`櫃買指數歷史資料`), which serves
`Open/High/Low/Close/Change` for `櫃買指數`. It accepts **no parameters** —
`d=`, `date=` and `yr=/mn=` are all ignored — and always returns the current
calendar month, 12 rows on 2026-09-16, despite its name. So OTC index OHLC
cannot be obtained for past dates and stays NULL for 2020–2026; Step 27's
forward capture can accumulate it from the day it starts. Like `MI_5MINS_HIST`,
it covers the one headline index and not the other 33.

### 4.3 Institutional flows and summary

- TWSE `fund/T86`: one 19-column header for all dates.
- TPEx `3itrade_hedge`: one 24-column header, which adds foreign totals and
  dealer total buy/sell.

Every `institutional_investor_versions` column is sourced for both markets.

Summary sources: TWSE `fund/BFI82U` (單位名稱, 買進金額, 賣出金額, 買賣差額) and
TPEx `3itrdsum` (單位名稱, 買進金額(元), 賣出金額(元), 買賣超(元)). The TPEx file
for 2026-07-10 in the archive is broken.

TPEx new-site JSON, found 2026-09-17 (see "TPEx new-site JSON endpoints" below):
`3itrade_hedge` is also served as `www/zh-tw/insti/dailyTrade`, with the same
24 fields, and `3itrdsum` as `www/zh-tw/insti/summary`, with the same 4 fields.
Both answer 2020-01-02 and 2026-09-11. Step 20 decides which endpoint to use.

### 4.4 Foreign holding

- TWSE `fund/MI_QFIIS`: 12 columns, including the ISIN.
- TPEx MOPS `t13sa150_otc`: 11 columns, no ISIN.

Every `foreign_holding_versions` column is sourced. Its `issued_shares` is the
only whole-market issued-share series for both markets, and legacy consumers
read it.

TPEx foreign holding is also served by TPEx itself, found 2026-09-17:
`www/zh-tw/insti/qfii?date=YYYY/MM/DD&response=json`, a GET request. It has 10
fields: 排行, 代號, 名稱, 發行股數(A), 僑外資及陸資尚可投資股數B=A*F-C,
僑外資及陸資持有股數(C), 僑外資及陸資尚可投資比率(D=B/A),
僑外資及陸資持股比率(E=C/A), 法令投資上限比率(F), 備註. It answers 2020-01-02
(778 rows) and 2026-09-11 (892 rows). Legacy's MOPS-derived table holds 728 and
891 rows on those dates. Nobody has yet checked that the two feeds cover the
same securities or publish the same values; Step 20 must check that before it
replaces MOPS.

### 4.5 Margin and securities lending

- TWSE `MI_MARGN`: a market summary block (項目, 買進, 賣出, 現金(券)償還,
  前日餘額, 今日餘額) plus a 16-column per-security table in lots.
- TPEx `margin_bal`: 20 columns in lots, including 資使用率(%), 券使用率(%),
  資屬證金, and 券屬證金.

Every `margin_trading_versions` quantity column is sourced. The utilization
ratios exist for TPEx only; TWSE values are NULL.

- SBL: TWSE `TWT93U` (15 columns, two header rows) and TPEx `margin_sbl`
  (15 columns). Every `securities_lending_versions` column is sourced.

TPEx new-site JSON, found 2026-09-17: `margin_bal` is also served as
`www/zh-tw/margin/balance`, with the same 20 fields, and `margin_sbl` as
`www/zh-tw/margin/sbl`, with the same 15 fields. Both answer 2020-01-02 and
2026-09-11. Step 21 decides which endpoint to use.

#### TPEx new-site JSON endpoints

Verified 2026-09-17. Each legacy `web/stock/.../*.php` page now answers with a
302 redirect to a page on the new site, `www.tpex.org.tw/zh-tw/mainboard/...`.
That page's own script loads its table from
`www/zh-tw/<action>?date=YYYY/MM/DD&response=json`, read from its
`tables.init({action: ...})` call. These are the official site's data calls,
from the same family as `afterTrading/otc` and `afterTrading/indexSummary`,
which Steps 17 and 18 already use.

| Legacy page | New page | `action` | 2020-01-02 rows (legacy table) |
| --- | --- | --- | --- |
| `3insti/3insti_summary/3itrdsum.php` | `major-institutional/summary/day.html` | `insti/summary` | 8 |
| `3insti/daily_trade/3itrade_hedge.php` | `major-institutional/detail/day.html` | `insti/dailyTrade` | 561 (459) |
| `3insti/qfii/qfii.php` | `major-institutional/stock-ocfi.html` | `insti/qfii` | 778 (728, from MOPS) |
| `margin_trading/margin_balance/margin_bal.php` | `margin-trading/transactions.html` | `margin/balance` | 743 (647) |
| `margin_trading/margin_sbl/margin_sbl.php` | `margin-trading/sbl.html` | `margin/sbl` | 756 (660) |
| `aftertrading/peratio_analysis/pera.php` | `trading/info/daily-pe.html` | `afterTrading/peQryDate` | 772 (see 4.6) |

Every response carries `stat`, a top-level `date` in `YYYYMMDD`, and a table
with its own ROC `date` and `fields`. The probe sent `type=Daily` to every
action; which parameters each action actually reads is not yet established.
Legacy holds fewer rows than every JSON table, and its archive is known to drop
rows (section 3). These row counts are a first observation, not a
reconciliation.

### 4.6 Official valuation

- TWSE `BWIBBU_d`: 證券代號, 證券名稱, 收盤價, 殖利率(%), 股利年度, 本益比, 股價淨值比,
  財報年/季. One anomalous 5-column file on 2025-06-24.
- TPEx `pera`: 股票代號, 公司名稱, 本益比, 每股股利(註), 股利年度, 殖利率(%), 股價淨值比.
  財報年/季 is added from 2025-01-02.

Re-verified live 2026-09-17:

- TWSE `rwd/zh/afterTrading/BWIBBU_d?date=YYYYMMDD&selectType=ALL&response=json`
  serves the 8-field header on 2020-01-02, 2025-06-23, **2025-06-24** and
  2026-09-11. `股利年度` is an ROC integer (`114`). `財報年/季` is written
  `115/2`. `本益比` uses `-` when it is not computed. A closed date answers
  `stat` `很抱歉，沒有符合條件的資料!`. The 5-field header (證券代號, 證券名稱,
  本益比, 殖利率(%), 股價淨值比) is real, but it is what TWSE serves for older
  dates such as 2017-01-03. The legacy archive's 2025-06-24 `sii.csv` has this
  5-field header and different values: 1101 has PE 12.55, against 20.28 today
  and 19.76 / 20.24 on the neighbouring dates. It also has 676 rows, against
  1,044. That legacy file is a bad capture, not the official answer for that
  date.
- TPEx `pera_result.php?o=csv` is MS950 (the response says
  `charset=MS950`; plain big5 fails to decode it). Contrary to the earlier note,
  it **does** state its date (`資料日期:115/09/11`) and ends with a row count
  (`共885筆`). A closed date answers with the header and `共0筆`.
- TPEx also serves the same table as JSON: `www/zh-tw/afterTrading/peQryDate?
  date=YYYY/MM/DD&response=json`. The legacy page redirects to the page that
  loads it. It is identical to the CSV row for row on 2020-01-02 (772 rows) and
  2026-09-11 (885 rows). It adds a table `date`, a `totalCount`, an integer
  `股利年度`, and the page's `notes`. Its field label is `每股股利`, without `(註)`.
- TPEx `notes` state the formulas. 本益比 = 收盤價 / 最近四季每股稅後純益, and it
  is not computed when EPS is zero or negative; the file then prints `N/A`.
  殖利率 = 每股股利 / 收盤價 × 100%, where 每股股利 is cash dividend plus
  earnings stock dividend for the prior year. 每股股利 is not adjusted for later
  capital changes. 股價淨值比 = 收盤價 / 每股淨值.
- TPEx `財報年/季` is written `115Q2`, against TWSE's `115/2`.
- Two first-day markers that the notes do not explain. TPEx 2024-12-04 prints
  `"0"` for both 本益比 and 股價淨值比 of 6720 久昌. That date is 6720's first
  row in the feed, in legacy `pe_ratio`, and in our daily prices. On 2024-12-05
  the same feed prints 28.23 and 8.69. From 2021-07-26 to 2022-11-02, TPEx
  prints the same first-day case as the string `"null"`, for example 6840 東研信超
  on its first day, 2021-07-26. The legacy CSV has `"null"` there too.
  By owner decision (ROADMAP 18-c), both mean not computed and store NULL. A
  ratio of exactly zero is treated the same way on TWSE.

Marker counts over the whole 2020-01-02 → 2026-09-11 backfill, each trade date
counted once:

| Source | Field | Value | Rows | Dates | First → last |
| --- | --- | --- | ---: | ---: | --- |
| TWSE | 本益比 | `-` | 309,579 | 1,627 | 2020-01-02 → 2026-09-11 |
| TWSE | 股價淨值比 | `-` | 262 | 261 | 2021-01-06 → 2026-07-24 |
| TWSE | either ratio | zero | 0 | 0 | — |
| TPEx | 本益比 | `N/A` | 361,936 | 1,627 | 2020-01-02 → 2026-09-11 |
| TPEx | 股價淨值比 | `N/A` | 178 | 157 | 2020-03-24 → 2025-06-20 |
| TPEx | 本益比, 股價淨值比 | `"null"` | 379 each | 205 | 2021-07-26 → 2022-11-02 |
| TPEx | 本益比, 股價淨值比 | `"0"` | 1 each | 1 | 2024-12-04 |

Every TWSE file in the window has the 8-field header. Every TPEx file up to
2024-12-31 has the 7-field header (1,216 dates), and every file from
2025-01-02 has the 8-field one (411 dates).

The legacy `pe_ratio` table disagrees with the official feed on 15 TWSE dates
only, and on each of them the legacy file is a different date's file. Legacy
checked neither the date nor the header of what it saved (section 3):

- On 10 dates the legacy file is an exact copy of another official date,
  sometimes earlier and sometimes later. For example, 2020-12-07 is the
  2020-12-18 file, 2022-01-24 is the 2022-01-18 file, and both 2025-06-04 and
  2025-06-05 are the 2025-06-18 file.
- On 4 dates (2022-02-17, 2023-03-22, 2024-01-08, 2025-08-20) the archived
  `sii.csv` files are byte-identical to one another. They hold data from
  late 2017: 股利年度 105, 財報年/季 106/3.
- On 2025-06-24 the legacy file is the 5-field header, which TWSE serves only
  for older dates.

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

The new MOPS site does not change this. Checked 2026-09-17: the SPA
(`mops.twse.com.tw/mops/`, bundle `assets/index.js`) routes 每月營業收入彙總表
(`t21sc04_ifrs`) through its `redirectToOld` API. That call returns only a
`mopsov.twse.com.tw/mops/web/ajax_t21sc04_ifrs?parameters=…` URL, which is an
old-site HTML page. The site offers no JSON rendering of the monthly table.
`nas/t21` stays the source, and it is a plain GET.

Summary of the new MOPS site's request types, read from the dispatcher in
`assets/index.js`. Every call goes to `mops.twse.com.tw/mops/api/`, and the
`type` field decides how:

| `type` | What the API does |
| --- | --- |
| `base` | POSTs JSON to `api/<name>` and returns JSON data |
| `twse` | POSTs to `api/redirectToOld`, which returns an old-site `mopsov` URL |
| `sii` | POSTs to `api/redirectToSiis` |
| `url` | builds an old-site URL in the browser and makes no API call |

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

Checked 2026-09-17: the new MOPS site has a JSON API for financial statements,
for example `POST mops.twse.com.tw/mops/api/t164sb04` with
`{"companyId","dataType":"2","subsidiaryCompanyId","year"(ROC),"season"}`.
It is **not a substitute** for the iXBRL documents. It returns a rendered
statement: Chinese line-item labels, formatted amounts, and percentages. It has
no concept QName, no context, no dimensions, no `unitRef`, and no `decimals`, so
it cannot meet CLAUDE.md §33. Its own `urlList` points back to
`mopsov.twse.com.tw/server-java/t164sb01?step=1&CO_ID=…&SYEAR=…&SSEASON=…&REPORT_ID=C`,
which legacy fetched with a plain GET. At most, the JSON could serve Step 23 as
a cross-check.

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

All of these were verified live on 2026-09-14 and again, feed by feed and year
by year for 2020-2026, on 2026-09-16 (Step 19-a). Every list feed takes a date
range and answers a whole calendar year in one request.

**A current-year file lists results that have not happened yet.** Fetched on
2026-09-16, TWT49U listed 35 rows for 2026-09-16 and later, TWTAUU listed
resumptions up to 2026-10-19 — the last three with `-` in place of every price —
and `revivt` listed three for 2026-09-21. The exchanges publish the calculation
ahead of the event. Invariant G(2) rests on the event being executed, so a
request declares the last date whose rows count (`executed_through`) and later
rows are counted, not stored.

**Result-feed identity holds over the full history.** Zero duplicate
`(code, locator)` and zero duplicate `(code, event date)` in any feed,
2020-01-01 → 2026-12-31 as served on 2026-09-16:

| Feed | Rows | Locator |
| --- | ---: | --- |
| `TWT49U` | 7,827 | `詳細資料` = `code,yyyymmdd`, the ex-date |
| `TWTAUU` | 157 | `詳細資料` = `code  ,yyyymmdd`, TWSE's file date — one day before the halt in all 14 sampled details, never the resumption date |
| `TWTB8U` | 10 | `詳細資料` = `code,halt,resumption` |
| `exDailyQ` | 7,359 | none; the executed date stands in |
| `revivt` | 111 | none; the resumption date stands in |
| `pvChgRslt` | 13 | none; the resumption date stands in |

**`權值+息值` is signed.** The feeds define it as 除權息前收盤價 − 除權息參考價, and
a rights issue priced above the close makes it negative: TWSE 3563
(2020-03-27, −0.616165), 3138, 1312 and 1312A; TPEx 8444 (2024-12-12,
−0.204602) and 6846. The storage contract allowed only non-negative values until
Step 19-a.

**Share ratios need eleven places.** Free shares and rights are published per
1,000 shares with up to eight places (`202.11906001`), and Step 19 divides by
1,000. `NUMERIC(24, 8)` rounded that silently; Step 19-a widened the four ratio
columns to `NUMERIC(28, 12)`. TPEx publishes cash dividends with eight places
(`3.42936322`), which the TWD contract had capped at four.

**A published zero means the item does not apply.** TWT49U's own note:
`如果無該項配股率則用'0'帶入`. Zero terms are stored as NULL, never as 0.

**The published type agrees with the terms.** On all 7,359 TPEx rows and on 53
TWSE details sampled across years and instrument kinds: 息 has cash and no
shares, 權 has shares and no cash, 權息 has both, and a rights ratio always comes
with a subscription price. An adapter treats a disagreement as a quarantine.

- **TWSE `rwd/zh/exRight/TWT49U?startDate=&endDate=&response=json`**: 資料日期
  (`113年01月04日`), 股票代號, 股票名稱, 除權息前收盤價, 除權息參考價, 權值+息值, 權/息
  (息 6,943 / 權息 443 / 權 441), 漲停價格, 跌停價格, 開盤競價基準, 減除股利參考價,
  詳細資料, 最近一次申報資料 季別/日期, 最近一次申報每股 (單位)淨值,
  最近一次申報每股 (單位)盈餘. An empty range answers
  `{"stat":"很抱歉，沒有符合條件的資料!"}` with no table; the response echoes
  `strDate`/`endDate`.
  **The locator exists only in `response=json`.** `response=csv`, which the legacy
  scraper used, flattens the column to the link label `除權息資料`, and 最近一次申報資料
  季別/日期 likewise loses its MOPS URL. Verified on 2026-09-15: the JSON row for
  00939 on 2026-01-02 carries `00939,20260102` where the archived CSV carries
  `除權息資料`. Step 19 must request JSON; the legacy CSV archive cannot supply
  Invariant G(2) identity.
  **`最近一次申報*` is today's filing, not the event's**: every 2024 row carries
  `115年第2季`. Storing it would revise every past event each quarter, so it is
  not event content.
  The 15-column header is byte-identical in every archived year 2020-2026, so
  this feed needs no header-variant handling.
- **TWSE `rwd/zh/exRight/TWT49UDetail?STK_NO=&T1=&response=json`** (`stat`
  `ok`, lower case; an unknown locator answers `{"stat":"無相關資料"}`). Two
  headers, sampled 2020-2026:
  - common shares (37 of 53 samples, ETFs and TDRs included): 股票代號, 股票名稱,
    (每股配發現金股利)除息 (`24.6 元／股`), (增資配股) 除權 (empty in every sample),
    A. 按普通股股東持股比例每千股無償配股 (`140 股`), B. 員工紅利轉增資,
    C. (有償) 現金增資, 每股認購金額 (`33 元／股`), a. 公開承銷, b. 員工認購,
    ` c. 原股東認購` (leading space included), 按股東持股比例每千股認購
    (`202.11906001 股`). B, C and a-c are share counts, not ratios.
  - preferred shares (16 of 53): 股票代號, 股票名稱, (每股配發現金股利)除息,
    (增資配股) 除權, F. 按特別股股東持股比例每千股無償配股,
    G. 按特別股股東持股比例每千股有償認股, 每股認購金額.
  - Verified live during the real Step 19-d backfill (2026-09-17): Taishin
    Financial's 2887-series preferred/warrant sub-classes have never had a
    working detail page, at all, on any date tried — `2887F` in every year
    2020-2026, `2887Z1` from 2023 on, `2887G`/`2887H`/`2887I` newly in 2026.
    Every request for these codes answers `{"stat":"無相關資料"}`, confirmed
    by repeated fresh requests, not a transient blip. They quarantine
    per-row (ADR-0022 §8) rather than blocking the rest of their shared
    ex-dividend date.
- **TWSE `rwd/zh/reducation/TWTAUU`** (capital reduction): 恢復買賣日期
  (`113/01/22`), 股票代號, 名稱, 停止買賣前收盤價格, 恢復買賣參考價, 漲停價格, 跌停價格,
  開盤競價基準, 除權參考價 (`--` in all 157 rows), 減資原因 (退還股款 87 / 彌補虧損 70),
  詳細資料. The file's notes name the reductions filed together with an ex-dividend
  (`除息併案辦理減資`: 2323 in 2022, 3356 in 2024), which TWT49U leaves out.
- **TWSE `rwd/zh/reducation/TWTAVUDetail?STK_NO=&FILE_DATE=&response=json`**
  (every label ends in `：`): 股票代號, 股票名稱, 停止買賣日期, 每壹仟股換發新股票
  (`855.66635000 股`), 每股退還股款 (`1.443336 元/股`), 原股每股配發現金股利
  (`2.900000 元/股` for 3356), 減資並(有償)現金增資, 每股認購金額, a. 公開承銷,
  b. 員工認購, c. 原股東認購, 按股東持股比例每千股認購. No sampled reduction had a
  cash increase.
- **TWSE `rwd/zh/change/TWTB8U`** (par-value change): 恢復買賣日期, 股票代號, 名稱,
  停止買賣前收盤價格, 恢復買賣參考價, 漲停價格, 跌停價格, 開盤競價基準, 詳細資料. The
  response echoes the range under `params`. 1, 1, 1, 0, 1, 4, 2 events for 2020
  through 2026.
- **TWSE `rwd/zh/change/TWTB8UDetail?STK_NO=&STOP_DATE=&RESUME_DATE=`**,
  verified for 8070 and 2327: it repeats the list row's eight price columns and
  **publishes no exchange ratio**. A TWSE par-value change therefore has no
  sourced `old_shares`/`new_shares`, and is stored as `other` with its prices.
- **TPEx `www/zh-tw/bulletin/exDailyQ?startDate=YYYY/MM/DD&endDate=`**: 除權息日期
  (`113/01/03`), 代號, 名稱, 除權息前收盤價, 除權息參考價, 權值, 息值, 權值+息值, 權/息
  (除息 6,495 / 除權息 434 / 除權 430), 漲停價, 跌停價, 開始交易基準價, 減除股利參考價,
  現金股利, 每仟股無償配股, 現金增資股數, 現金增資認購價, 公開承銷股數, 員工認購股數,
  原股東認購股數, 按持股比例仟股認購. The response echoes `date` as
  `20240101~20241231`; an empty range is `stat` `ok` with an empty table.
- **TPEx `www/zh-tw/bulletin/revivt`** (capital reduction): 恢復買賣日期
  (`1130205`), 股票代號, 名稱, 最後交易日之收盤價格, 減資恢復買賣開始日參考價格, 漲停價格,
  跌停價格, 開始交易基準價, 除權參考價 (`0.00` in all 111 rows), 減資原因 (彌補虧損 90 /
  現金減資 21), and 詳細資料 as inline HTML with eight labels, identical in every
  row: 股票代號/股票名稱:, 停止買賣日期:, 恢復買賣日期:, 每壹仟股換發新股票:
  (`300.00000000&nbsp股`), 每股退還股款: (`0.00000000&nbsp元/股`), 現金增資總股數:,
  現金增資認購價:, 現金增資配股率:. The last three are `NA` in every row, so the
  unit of 現金增資配股率 has never been seen.
- **TPEx `www/zh-tw/bulletin/pvChgRslt`** (par-value change, found through the
  site menu `/data/menu/zh-tw/menu.json` on 2026-09-16): 恢復買賣日期, 證券代號,
  證券名稱, 最後交易日之收盤價格, 恢復買賣開始參考價, 漲停價格, 跌停價格, 開始交易基準價,
  詳細資料, the last as inline HTML: 證券代號/證券名稱:, 停止買賣日期:, 恢復買賣日期:,
  變更股票面額換股率: (`10.00000000`), 變更前股票面額: (`10.00`), 變更後股票面額:
  (`1.00`). 0, 0, 4, 0, 3, 1, 5 events for 2020 through 2026, every one a split,
  and in every one the ratio equals the old par value over the new.

**Not in Step 19's contract, found on the way; verified live for Step 19-e on
2026-09-17.** ETF splits and reverse splits have result feeds of their own.
None of the six Step 19 feeds lists those events, and an adjusted 0050 series
is wrong without them. ROADMAP records them as follow-up work before Step 25.

- **TWSE `rwd/zh/split/TWTCAU?startDate=YYYYMMDD&endDate=YYYYMMDD&response=json`**
  (`ETF分割(反分割)恢復買賣參考價格`): 恢復買賣日期 (ROC slashed, `113/12/11`), ETF代號,
  名稱, 分割(反分割) (`分割` or `反分割`), 停止買賣前收盤價格, 恢復買賣參考價, 漲停價格,
  跌停價格, 開盤競價基準. `formula` confirms 恢復買賣參考價 = 停止買賣前收盤價 /
  分割（反分割）比率 — the ratio is derivable from the two official prices, but the
  feed publishes no source-native integer share count, unlike `TWTAVUDetail`'s
  每壹仟股換發新股票. `old_shares`/`new_shares` therefore stay NULL for this feed;
  only `close_before`/`official_reference_price` are sourced. A probed
  `TWTCAUDetail?STK_NO=&FILE_DATE=` returns an all-dash empty row for every
  locator tried — there is no detail endpoint. 11 events over 2020-01-01 →
  2026-09-11 (all in ROC 113–115 / 2024–2026; zero before). One row
  (00631L 元大台灣50正2, 115/03/31) has an **empty** 分割(反分割) direction field —
  every other row is populated — so its `action_type` cannot be determined
  from this feed alone and it quarantines rather than guessing (CLAUDE.md
  §37/§51.5 fail-closed).
- **TPEx `bulletin/etfSplitRslt` and `bulletin/etfRvsRslt`**
  (`?startDate=YYYY/MM/DD&endDate=YYYY/MM/DD&response=json`, Gregorian
  slashed like `exDailyQ`): same eight-column shape as `pvChgRslt` (恢復買賣日期,
  證券代號, 證券名稱, 最後交易日之收盤價格, 恢復買賣開始參考價, 漲停價格, 跌停價格,
  開始交易基準價, 詳細資料) — but the whole 2020-01-01 → 2026-09-11 window returns
  `totalCount: 0` on both, verified live. TPEx has never had an ETF split or
  reverse-split event in v1's window. Adapters are written and tested against
  this contract but real backfilled history for these two feeds is zero rows,
  not "not yet fetched."

Event volumes in the legacy archive (TWSE, year-to-date files, 2020-01-01 →
2026-09-14): `TWT49U` about 1,280 rows in 2026 alone. The legacy
`par_value_change/2023` directory is empty because the year had no events and
the scraper writes nothing on an empty response, not because the fetch failed.
The legacy system fetched one year-to-date request per feed per year and never
called any Detail endpoint, so no detail field in this section comes from the
archive.

| `corporate_action_versions` column | Exchange result feeds |
| --- | --- |
| ex_date (ex-right date or resumption date) | ✓ |
| close_before, official_reference_price | ✓ in every feed |
| official_rights_dividend_value | ✓ (`TWT49U`, `exDailyQ`), signed |
| cash_dividend_per_share | ✓ (`TWT49UDetail`, `TWTAVUDetail`, `exDailyQ`) |
| free_share_ratio | ✓ (free shares per 1,000 ÷ 1,000) |
| earnings_stock_ratio / capital_surplus_stock_ratio | ✗: exchange feeds publish only the combined free-share figure |
| rights_ratio, subscription_price | ✓ |
| old_shares / new_shares | ✓ for capital reduction (1,000 → 每壹仟股換發新股票) and TPEx par-value change (1 → 變更股票面額換股率); ✗ for TWSE par-value change |
| capital_reduction_kind, capital_reduction_cash_return_per_share | ✓ (減資原因, 每股退還股款) |
| announcement_date, record_date, payment_date | ✗ |

The issuer summary feeds (TWSE `t187ap45_L`, TPEx `mopsfin_t187ap39_O`) do
split earnings and capital-surplus stock dividends, but they lack a stable
event identity (see ROADMAP Step 13). Their contract and coverage limits are in
§4.13; Step 33 stores them as their own domain.

### 4.11 Security metadata, lifecycle, and tags

- Step 10 uses current snapshots (`t187ap03_L`, `mopsfin_t187ap03_O`). Step 11
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
(`assets/t05st09_new.js`). Re-checked 2026-09-17: that route's search action is
of type `url` and points straight at `mopsov…/server-java/t05st09sub` with
method POST. The new site has no JSON rendering for it. Parameter names are case-sensitive: `YEAR` works,
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
| `corporate_action_versions.announcement_date` | unsourced | stays NULL | No exchange result feed carries an announcement date. The issuer declaration feeds that do carry board-resolution dates have no link to an executed event (Step 33, audit 4.13). |
| `corporate_action_versions.record_date` | unsourced | stays NULL | No exchange result feed carries a record date. |
| `corporate_action_versions.payment_date` | unsourced | stays NULL | No exchange result feed carries a payment date. |
| `corporate_action_versions.earnings_stock_ratio` | unsourced | stays NULL | The exchange feeds publish only the combined free-share figure. The earnings / capital-surplus split exists only in the MOPS issuer declaration feed, stored as its own domain by Step 33 (audit 4.13). |
| `corporate_action_versions.capital_surplus_stock_ratio` | unsourced | stays NULL | The exchange feeds publish only the combined free-share figure. The split exists only in the MOPS issuer declaration feed (Step 33), and before ROC 110 the two reserves arrive as one number (audit 4.13). |
| `corporate_action_versions.old_shares` | partially sourced | holds the values that exist | Capital reduction in both markets: the old side is the constant 1,000 of 每壹仟股. TPEx par-value change (`pvChgRslt`): 1 against 變更股票面額換股率. TWSE par-value change: none — `TWTB8UDetail`, verified 2026-09-16, publishes no exchange ratio. |
| `corporate_action_versions.new_shares` | partially sourced | holds the values that exist | Capital reduction in both markets (每壹仟股換發新股票) and TPEx par-value change (變更股票面額換股率). TWSE par-value change: none, as above. |
| `daily_price_versions.price_direction` | partially sourced | holds the values that exist | TWSE publishes `+`/`-`/`X` in its own column. stk_wn1430 signs the number instead and has no direction column, so a TPEx row claims a direction only where the feed prints its 不比價 marker (除息 / 除權 / 除權息), which is stored as `X`. |
| `daily_price_versions.bid_snapshot` | unsourced | stays NULL | Multi-level order-book depth blob. No daily whole-market endpoint publishes depth; the one published level is stored in last_bid_price/last_bid_volume. |
| `daily_price_versions.ask_snapshot` | unsourced | stays NULL | Multi-level order-book depth blob. No daily whole-market endpoint publishes depth; the one published level is stored in last_ask_price/last_ask_volume. |
| `daily_price_versions.last_bid_volume` | partially sourced | holds the values that exist | TWSE on all dates, in shares (`hints: 單位：元、股`, corroborated by `TWT53U`). TPEx only from 2020-04-30, in lots; the label changes 千股 to 張數 on 2025-01-10, both meaning 1,000 shares, and only TPEx is converted. |
| `daily_price_versions.last_ask_volume` | partially sourced | holds the values that exist | TWSE on all dates, in shares (`hints: 單位：元、股`, corroborated by `TWT53U`). TPEx only from 2020-04-30, in lots; the label changes 千股 to 張數 on 2025-01-10, both meaning 1,000 shares, and only TPEx is converted. |
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
ROADMAP Step 15 is the decision point for what to do about that.

Every other column of every table in the storage contract is either sourced for
the whole v1 window and both markets — §4 names the endpoint and the published
field label for each one — or internal: identity, an interval boundary, or
provenance linkage that is not expected to come from a source field.

## 6. Source fields not stored

| Field | Decision |
| --- | --- |
| Monthly-revenue published comparatives | Store as observed (ROADMAP Step 22); legacy consumers read them |
| TPEx daily 發行股數 and next-day limits | Not stored; `foreign_holding.issued_shares` covers both markets |
| TPEx margin 資屬證金/券屬證金, TWSE 註記 | Not stored; no consumer |
| TPEx institutional foreign/dealer totals | Not stored; they are sums of stored columns |
| Ex-right limit prices, opening reference, 減除股利參考價, the TPEx 權值 / 息值 split, capital-increase share counts | Keep in `source_terms` |
| TWT49U 最近一次申報 季別/日期, 每股淨值, 每股盈餘 | Not stored: the issuer's latest filing at fetch time, not the event's (§4.10); storing it would revise every past event each quarter |
| Security name in every result feed | Not stored with the event: a rename would revise every past event |

## 7. Publication time

None of the inspected sources gives a per-row release instant:

- The exchange feeds carry only the data date.
- MOPS `t21sc03` carries only the page generation date.
- `t164sb01` carries none.
- TDCC carries the data date.

Under the current rules, all imported history therefore has
`published_at = NULL` and is invisible to Market PIT. ROADMAP Step 15 is the
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
the 03:00 next-day release rule for exchange daily datasets (ROADMAP Step 15).

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
Step 22 therefore reads the CSV and treats any row on the 10th as rule-bound.

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
