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
for 2026-07-10 in the archive is broken; Step 20-b found why (below).

TPEx new-site JSON, found 2026-09-17 (see "TPEx new-site JSON endpoints" below):
`3itrade_hedge` is also served as `www/zh-tw/insti/dailyTrade`, with the same
24 fields, and `3itrdsum` as `www/zh-tw/insti/summary`, with the same 4 fields.
Both answer 2020-01-02 and 2026-09-11.

Step 20-a settled the per-security endpoints, verified 2026-09-18:

- **TWSE** `rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALLBUT0999&response=json`.
  `hints` states `單位：股`. `ALLBUT0999` means every security except warrants
  and CBBCs. It returns 1,037 rows on 2020-01-02 and 1,330 on 2026-09-11. A
  closed day answers `{"stat": "很抱歉，沒有符合條件的資料!", "total": 0}`.
- **TPEx** `www/zh-tw/insti/dailyTrade?date=YYYY/MM/DD&type=Daily&sect=EW&response=json`,
  source code `tpex_insti_daily_trade`. `type` is mandatory; without it the
  answer is `{"stat": "參數輸入錯誤"}`. `sect=EW` is 所有證券(不含權證、牛熊證),
  the TPEx counterpart of `ALLBUT0999` and the selector the legacy scraper sent
  to `3itrade_hedge`. `sect=AL` adds warrants: 3,681 rows on 2020-01-02 against
  561. A closed day answers `stat: ok` with an empty table.
- **TPEx column groups.** The JSON labels its 21 value columns with only three
  repeated names: 買進股數, 賣出股數, 買賣超股數. The group each triple belongs to
  is in the `<template id="theads">` of the official page,
  `zh-tw/mainboard/trading/major-institutional/detail/day.html`. The groups are,
  in order: 外資及陸資(不含外資自營商), 外資自營商, 外資及陸資, 投信,
  自營商(自行買賣), 自營商(避險), 自營商. The response also carries a second,
  always-empty table (`{}`). The same page defines a 16-column layout, with no
  foreign-dealer group, for older dates. No window date uses it, and the
  adapter treats it as a format change.
- **Not stored:** TPEx's 外資及陸資 total and the 自營商 total's buy and sell
  have no contract column. Each is the sum of two stored groups. The Step 20-a
  reconciliation re-reads every raw artifact to prove this.
- **Legacy kept common stock only.** On 2020-01-02 legacy holds 901 TWSE and
  459 TPEx rows. Both official files hold every legacy row, plus 136 and 102
  other rows, all ETFs and other non-4-digit instruments. Those are source data
  and are stored.

Both markets satisfy every published identity on the captured dates: buy −
sell = net per group, self + hedge = dealer net, and foreign (excluding foreign
dealers) + trust + dealer = total. Nothing is recomputed.

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
891 rows on those dates.

Checked 2026-09-18 against MOPS `t13sa150_otc` for 2026-09-11: **`insti/qfii`
does not replace MOPS.**

- **Coverage.** MOPS lists 1,010 securities and `insti/qfii` lists 892. The 119
  that only MOPS lists are all ETFs, including the bond ETFs. `insti/qfii` alone
  lists `8349A`, a preferred share.
- **Fields.** `insti/qfii` has no 陸資法令投資上限比率 and no
  最近一次上櫃公司申報外資持股異動日期. Those are
  `foreign_holding_versions.mainland_legal_limit_ratio` and
  `source_last_update_date`.
- **Values.** For the 891 securities both list, issued, investable and held
  shares agree exactly. 尚可投資比率 differs by 0.01 in 436 rows, for example
  5455 is 99.87 in MOPS and 99.88% in `insti/qfii`: the two sources round
  differently. 備註 differs in 8 rows.
- **Schedule.** `insti/qfii` notes that the table updates at 18:00 and again
  at 22:00.

MOPS stays the TPEx foreign-holding source. Its POST resource and the per-host
governor therefore stay in Step 20; ROADMAP Step 20-c builds them.

#### Step 20-d findings (2026-09-19)

Verified by live fetches and the 2020-01-02 → 2026-09-11 backfill of all three
sources.

- **TWSE `MI_QFIIS`**, `rwd/zh/fund/MI_QFIIS?date=YYYYMMDD&selectType=ALLBUT0999&response=json`.
  One 12-field header for the whole window; `hints` is `單位:股`. The two share
  ratios (`外資及陸資尚可投資比率`, `全體外資及陸資持股比率`) are JSON numbers,
  every other value a string, so the payload must be read with exact decimals.
  A security that has not filed yet gets the JSON integer `0` as
  `最近一次上市公司申報外資及陸資持股異動日期` (4581 on 2020-03-06). A closed
  date answers `stat` `OK` with `total` 0.
- **MOPS `t13sa150_otc`**, a POST of `step=2&years=<Gregorian>&months=MM&days=DD&bcode=`.
  MS950 HTML: strict big5 fails on a few security names (安碁, 宏碁 …), cp950
  decodes the whole page. One 11-column header for the whole window, titled
  `<ROC yyy/mm/dd>　外資及陸資投資持股統計`. `最近一次上櫃公司申報外資持股異動日期`
  is sometimes blank (7839 on 2026-09-11). A closed date answers
  `查無所需資料` with no table.
- **`與前日異動原因`** is a set of single-digit codes 2–5, defined in each page's
  note; blank is an ordinary market-trade change. A cell may hold several codes:
  TWSE wraps each in its own link, separated by `<br>` (2303 on 2020-05-15:
  `2<br>4`); MOPS runs them together inside one link (5483 on 2020-04-06:
  `24`). The links point at filing pages whose query month changes every month.
  Stored as the codes, ascending and comma-separated (`2,4`); blank is NULL.
- **Ratio arithmetic.** In both markets E = trunc(C / A, 2) on every row; D =
  trunc(B / A, 2) in TWSE and MOPS and round(B / A, 2) in `insti/qfii`, which is
  the 436-row difference noted above. B + C never exceeds floor(A × F), and
  falls below it when the source withholds capacity: `insti/qfii` marks some of
  those rows `禁止投資`, with B = 0.
- **MOPS drops securities no longer listed.** MOPS rebuilds every past date from
  today's security list. 5371 中光電, 4130 健亞, 3426 台興 and 4987 科誠
  stopped trading on TPEx between 2026-05 and 2026-08, and 5236 凌陽創新 moved
  to TWSE on 2026-07-15; all five are absent from every MOPS date back to
  2020-01-02, although legacy's files, fetched in February 2026, list them.
  TPEx's `insti/qfii` still lists them for past dates. Owner decision
  2026-09-19: `insti/qfii` is stored as a second TPEx source, `tpex_insti_qfii`,
  each source keeping its own history. For `insti/qfii` the mainland limit,
  change reason and last-update date are not published and stay NULL; its
  `排行`, `名稱` and `備註` (blank, `禁止投資`, or `已達上限` — 6497 from
  2020-05-04 to 2020-08-24) have no contract column.

### 4.5 Margin and securities lending

- TWSE `MI_MARGN`: a market summary block (項目, 買進, 賣出, 現金(券)償還,
  前日餘額, 今日餘額) plus a 16-column per-security table in lots.
- TPEx `margin_bal`: 20 columns in lots, including 資使用率(%), 券使用率(%),
  資屬證金, and 券屬證金.

Every `margin_trading_versions` quantity column is sourced. The utilization
ratios exist for TPEx only; TWSE values are NULL.

- SBL: TWSE `TWT93U` (15 columns, two header rows) and TPEx `margin_sbl`
  (15 columns), in shares. Every `securities_lending_versions` column is
  sourced (Step 21-b findings below).

TPEx new-site JSON, found 2026-09-17: `margin_bal` is also served as
`www/zh-tw/margin/balance`, with the same 20 fields, and `margin_sbl` as
`www/zh-tw/margin/sbl`, with the same 15 fields. Both answer 2020-01-02 and
2026-09-11. Step 21 chose the JSON for both.

#### Step 21-a findings (2026-09-19)

Verified by live fetches and the 2020-01-02 → 2026-09-11 backfill of both
markets.

- **Endpoints.** TWSE `rwd/zh/marginTrading/MI_MARGN?date=YYYYMMDD&selectType=ALL&response=json`
  (`twse_mi_margn`); TPEx `www/zh-tw/margin/balance?date=YYYY/MM/DD&response=json`
  (`tpex_margin_balance`), chosen over the legacy CSV. One header per market for
  the whole window. A closed date answers TWSE `很抱歉，沒有符合條件的資料` with
  no tables, and TPEx `stat` `ok` with an empty table.
- **Units.** TWSE states the unit only in its market summary rows,
  `融資(交易單位)` and `融券(交易單位)`; TPEx labels `(張)`. TWSE's MI_INDEX
  note defines the trading unit: 除境外指數股票型基金及外國股票第二上市外，餘交易
  單位皆為千股. In the window that exception is **008201 BP上證50** (offshore ETF,
  ISIN HK0000052297, 2020-01-02 → 2022-07-08), which trades in lots of 100: its
  next-day limit × 100 equals 25% of its issued units on all 612 of its dates.
  It was found by comparing every next-day limit with 25% of the issued shares
  Step 20-d stored; no other security departs from a 1,000-share lot except
  where its issued shares moved at least twofold nearby. The adapter's
  `TWSE_LOT_SHARES` records each exception with the dates its evidence covers;
  the same code outside them fails its file as `unverified_trading_unit`
  (`twse-mi-margn:v3`).
- **Column order.** TPEx lists 券賣 before 券買 on the short side; TWSE repeats
  買進/賣出/前日餘額/今日餘額/次一營業日限額 for both sides.
- **Utilization above 100.** TPEx published 資使用率 103.1% for 00989B on
  2026-07-14: 15,568 lots bought in one day against a 15,113-lot limit. The stop
  takes effect on the next business day (TWSE's note: 備註欄係表明成交日次一營業日
  股票融資融券狀況), so one day can overshoot. Step 7's 0–100 cap is relaxed to
  non-negative (migration `b9d1f3a5c7e2`).
- **Roll-forward.** On every stored row of both markets, margin previous + buy −
  sell − cash repayment = balance and short previous + sell − buy − stock
  repayment = balance.
- **Not stored.** TPEx 資屬證金 and 券屬證金, and both exchanges' status notes
  (TWSE `O X @ % !`, TPEx codes such as `11 C`), have no contract column.

#### Step 21-b findings (2026-09-19)

Verified by live fetches across the window and the 2020-01-02 → 2026-09-11
backfill of both markets.

- **Endpoints.** TWSE `rwd/zh/marginTrading/TWT93U?date=YYYYMMDD&response=json`
  (`twse_twt93u`); TPEx `www/zh-tw/margin/sbl?date=YYYY/MM/DD&response=json`
  (`tpex_margin_sbl`), chosen over the legacy CSV. One header per market for
  the whole window. TWSE groups its 15 fields as 股票 (2) / 融券 (6) /
  借券賣出 (6) / 備註 (1) and ends with a codeless `合計` row; TPEx has no total
  row. A closed date answers TWSE `stat` `OK` with no rows (and no `hints`),
  and TPEx `stat` `ok` with an empty table.
- **Units: shares, not lots.** TWSE `hints` reads `單位：股` on every trading
  date. The TPEx JSON states no unit; the page that renders it declares
  `subtitle2:"單位：股"`, and its 融券 group equals `margin/balance`'s `(張)`
  columns × 1,000 row for row. Nothing is converted, so 21-a's trading-unit
  exceptions do not apply: 008201's 融券 limit here is 387,275 shares, exactly
  25% of its 1,549,100 issued units, where MI_MARGN publishes 3,872 lots of 100.
- **Mapping.** `securities_lending_versions` takes the 借券賣出 group:
  前日餘額 → `previous_balance`, 當日賣出 → `borrowed`, 當日還券 → `returned`,
  當日調整 (TPEx 當日調整數額) → `adjustment`, 當日餘額 → `balance`,
  次一營業日可限額 (TPEx 次一營業日可借券賣出限額) → `next_available_limit`,
  and 備註 → `note`, stripped of its padding, blank as NULL (TWSE `X Y V % Z !`
  combinations such as `XV!`). `next_limit` is the 融券 group's
  次一營業日限額 (TPEx 限額): the short-sale limit in exact shares, which
  `margin_trading.short_next_limit` holds only rounded down to whole lots.
- **當日調整 is signed.** Positions moved between the ordinary, credit and
  lending accounts, and error corrections (both exchanges' notes); the TWSE
  market total on 2024-01-02 was −565,000.
- **Roll-forward.** 前日餘額 + 當日賣出 − 當日還券 + 當日調整 = 當日餘額, the
  formula both exchanges print, holds on every stored row of both markets.
- **Not stored.** Names, the `合計` row, and the 融券 group's balances and
  flows, which repeat `margin_trading`: the reconciliation re-reads every raw
  file and finds them equal to the stored 21-a short side. A security the
  margin table does not list appears here with an all-zero 融券 group; almost
  all carry note `Y` (未取得信用交易資格: TWSE 6,276 of 6,289 rows, TPEx 38,575
  of 38,576).
- **Market moves.** On the evening before a security moves between TPEx and
  TWSE, both lending tables list it with the same values (TWSE's note:
  第二次更新時將納入原為上櫃次日將轉為上市交易之個股); its 融券 group is then in
  the other market's margin table. Eleven such rows in the window (TWSE 10,
  TPEx 1, the move from TWSE to TPEx of 6423 on 2026-01-21). Each source keeps
  what it published.
- **停止買賣 clears the balance.** On 21 TWSE rows with note `!` the balance
  goes to zero with no 還券 or 調整 (capital reductions and delistings, e.g.
  2409 and 3481 on 2022-09-29); the next date starts from zero. The
  roll-forward check accepts only that pattern.
- **Limits can disagree for a day.** 1721 on 2021-02-05: MI_MARGN already
  shows the new limit (42,456 lots) while TWT93U shows 47,248,750 shares,
  moving to 42,456,680 from the next trading date.

Step 20-b settled the summary endpoints, verified 2026-09-18:

- **TWSE** `rwd/zh/fund/BFI82U?type=day&dayDate=YYYYMMDD&response=json`,
  source code `twse_bfi82u`. `hints` states `單位：元`. Six rows, in this
  order on every sampled date from 2020 to 2026: 自營商(自行買賣),
  自營商(避險), 投信, 外資及陸資(不含外資自營商), 外資自營商, 合計. A closed
  day answers `{"stat": "很抱歉，沒有符合條件的資料!"}`.
- **TPEx** `www/zh-tw/insti/summary?date=YYYY/MM/DD&response=json`, source
  code `tpex_insti_summary`. Eight rows: 外資及陸資合計, 外資及陸資(不含自營商),
  外資自營商, 投信, 自營商合計, 自營商(自行買賣), 自營商(避險), 三大法人合計*.
  The page indents the four subgroup rows with U+3000; the stored name drops
  that indent and the raw artifact keeps it. A closed day answers `stat: ok`
  with an empty table.
- **A five-row TWSE layout existed.** Legacy files for 2021-08-26,
  2022-09-22 and 2025-03-14, saved 2026-01-30 → 2026-02-01, have one combined
  `外資` row and no `外資自營商` row. Its values equal today's
  外資及陸資(不含外資自營商), and 合計 is unchanged. TWSE now serves six rows
  for those dates; the adapter treats the five-row layout as a format change.
- **Names are per market.** The two exchanges name the foreign row
  differently, and TPEx publishes two subtotals TWSE does not. Rows are
  stored under the published name, as the indices are (Step 18); nothing maps
  one market onto the other.
- **Totals.** Both exchanges note that the foreign-dealer row is already
  inside the dealer rows and is not added into the total. TWSE 合計 is
  self + hedge + trust + foreign; TPEx 三大法人合計* is foreign + trust +
  自營商合計. The Step 20-b reconciliation checks every stored date.
- **The broken 2026-07-10 file.** 2026-07-10, a Friday, was an unscheduled
  closure. It is not on TWSE's published 2026 holiday schedule, but it is
  absent from the TWSE trading calendar (Step 16), and neither market has
  prices or flows for it. The
  legacy scraper saved TPEx's empty-table answer into its CSV archive, which
  is why the file does not parse. Re-fetched, TPEx answers the date exactly as
  it answers a Sunday. It is not a gap.

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

#### Step 22-a findings (2026-09-20)

Verified by live fetches of both markets' `_0` and `_1` pages for every January
2020–2026 and 2026M08 (32 pages, every one parsed).

- **Encoding.** Big5 as Microsoft writes it: strict `big5` rejects the pages
  (0xF9 bytes), `cp950` reads them whole with no replacement character.
- **Structure.** One table per industry, each headed `單位：千元`, then a
  whole-market total table whose 備註 column is commented out. A company row
  is 11 `<td>` cells; the totals (`合計`, `全部國內上市公司合計`) start with a
  `<th>`. One header for the whole window. The page title names the market and
  the ROC year-month (`上市公司115年7月份(累計與當月)營業收入統計表`), and is the
  same on `_0` and `_1`; only the whole-market total row names the page
  (`全部國內上市公司合計` / `全部國外上市公司合計`), on every page sampled.
- **Values.** Percentages carry thousands separators (`4,533.33`); a blank
  percentage has no base and is stored as NULL. 備註 is `-` when empty and is
  stored verbatim.
- **`_0` and `_1` are disjoint**, and `_0` lists no KY issuer (checked on the
  same market's two pages for otc 2023M06 and sii 2026M07; 22-b checks every
  month). The legacy scraper hard-codes `_0`
  (`scraper/monthly/fetch_monthly_revenue.py`), which is why legacy has no KY
  issuer.
- **Non-answers.** A month not yet published answers a page reading 查無資料.
  Under load the host once answered the 18 bytes `Unreachable Server` with
  status 200, and HTTP 502 another time; the next request succeeded both times.
- **Sources.** One per market, `mops_t21sc03_sii` and `mops_t21sc03_otc`: a
  security moving market can appear on both markets' pages in one month
  (legacy 5236, 2026M06).
- **Corrections are visible across fetches.** Today's 2026M06 page already
  carries the corrected values: 6441 廣錠 reads 10,948 千元 where legacy first
  captured 13,094, so the §7.3 disagreement exists only between legacy's
  first capture and today's pages.

#### Step 22-b findings (2026-09-20)

The full history is imported: 2020M01–2026M08, both markets, both pages, 320
pages, 150,641 rows (`mops_t21sc03_sii` 82,859, `mops_t21sc03_otc` 67,782).
Coverage is 80 of 80 months in each market. Two pages answered HTTP 502 and
succeeded on a rerun with a new import id; no page quarantined, and no month
of the window answers 查無資料.

- **The two pages never share a company.** Every one of the 160 stored
  market-months was re-parsed from its raw artifacts and intersected: zero
  shared companies. `_1` holds 94 issuers in 上市 and 30 in 上櫃 over the
  window (6,647 and 2,304 rows), all of them new coverage — legacy has none.
- **The source rewrites its own history by issuer status.** `t21sc03` is
  regenerated, and a month's page lists the issuers that hold that status
  *now*. 540 legacy rows (230 上市, 310 上櫃) are for issuers that appear on no
  month's page of either market. Three of them, checked by hand on 2026-09-20
  for 2020M01: 2867 三商壽 and 6806 森崴能源 are on the `pub` page, and 3454
  晶睿 is on none of `sii`, `otc`, `rotc` or `pub`. `pub` and `rotc` are
  outside the v1 universe by owner decision, so these rows are not a gap in
  what v1 covers. The mirror image is 1,257 rows we hold for months before the
  issuer was listed, which legacy had no page to read.
- **A market change shows up on both sides.** 77 rows are ours under 上市 where
  legacy filed them under 上櫃, and 78 the other way round.
- **Legacy's own decoding differs from the page's.** 98 notes differ from ours
  without the page having changed: 22 where legacy collapsed runs of
  whitespace, 69 where its decoding lost bytes (`不銹鋼`'s 銹 is `F9 D7`, which
  strict big5 rejects), one where it read the same bytes as another character
  (`‧` and `•` are both `A1 45`), and 6 where the page's literal `NA` became
  NULL. 11 further rows are months legacy simply missed for an issuer it holds
  on both sides.
- **Corrections after legacy's capture are the bulk of the value
  differences.** 166 rows' 當月營收 differ with the next month's 上月營收
  agreeing with ours. 986 further comparatives are restatements those
  corrections explain, 3 are comparatives that disagree with the month they
  restate while ours agrees, 2 are legacy cumulatives that do not follow
  legacy's own rows (1611 2026M05: legacy holds 341,881 where its own 338,195
  + 86,945 is our 425,140), and 4 are percentages following amounts already
  explained.
- **Sixteen rows have no mechanical proof** and are named individually in
  `scripts/reconcile_monthly_revenue.py` (`KNOWN_LATE_REPUBLICATION`): 2425
  and 1235 rewrote their 備註, 2887 台新新光金 and 2608 restated their 去年
  columns, 6692, 4402 and 6101 replaced their 備註. All sixteen are 2026
  months, the tail legacy captured most recently, and none changes 當月營收.

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


#### Step 23-a findings (2026-09-20)

Measured with `scripts/scan_xbrl_archive.py` over **all 45,324 archive
documents**, and with one live fetch of `t164sb01?step=1&CO_ID=1101&SYEAR=2025&SSEASON=1&REPORT_ID=C`.

**The header describes the filing, and it agrees with the file name.**
`<ix:header>` carries `tifrs-notes:CompanyID`, `Year`, `Quarter`, `ReportType`,
`ReportCategory`, `Market`, `IndustrySector`, `CompanyChineseName` and
`CompanyEnglishName`. Every one of the 45,324 documents has all of them, and in
every one the company, year and quarter equal the ones in the file name and
directory. There is no duplicate `(year, quarter, symbol)`: 26 quarters, 2020Q1
to 2026Q2, 1,857 symbols. `<ix:references>` adds a `link:schemaRef` such as
`tifrs-ci-cr-2020-06-30.xsd`, whose middle segment is the industry taxonomy and
whose `cr`/`ir` marks consolidated or individual.

| Field | Values |
| --- | --- |
| `ReportType` | `Financial report (general)` 45,023; `Financial Report (retrospective - material)` 240; `Financial report (retrospective)` 34; `Financial report (first time adoption)` 27 |
| `ReportCategory` | `Consolidated report` 40,994; `Individual report` 4,329; one document breaks the value across a line |
| `Market` | `Listed company` 23,583; `Over-the-counter` 20,074; `Emerging stock market` 779; `Emerging Stock Company (Applying for listing on TWSE/GTSM)` 544; `Public company` 320; `Non-public company` 24 |
| `IndustrySector` | `Commercial and industrial` 44,312; `Financial holding` 278; `Broker-dealer` 252; `Banking and savings institution` 251; `Insurance` 126; `Miscellaneous industry merging` 104; one breaks across a line |

**The financial industries to exclude are four, not five.** 907 documents are
`Financial holding`, `Broker-dealer`, `Banking and savings institution` or
`Insurance`. `Miscellaneous industry merging` is **not** one of them: its filers
are 1409, 1718, 2207 and 2905, and the legacy `quarterly_reports_xbrl` holds 52
quarters for each of them, so the legacy parser read them fine. Meanwhile 2801,
2855 and 2881 have no legacy rows at all, which is the exclusion legacy achieved
by only understanding the general-industry account table (`KNOWN_ISSUES.md`).

**1,667 documents are outside the v1 universe.** Emerging, public and
non-public filers appear in the archive because the legacy scraper's
`active_stocks.txt` included them. v1 is sii and otc only, so they are rejected
at the adapter boundary rather than stored.

**The encoding is cp950, not big5.** The response declares
`charset=big5` in a `<META>` tag and sends no charset in the `Content-type`
header. Byte `0xA1E3` is `～` U+FF5E under cp950 and `∼` U+223C under Python's
`big5`. The legacy archive used the cp950 mapping. The proof is exact: the
official response for 1101 2025Q1 fetched on 2026-09-20 is 1,955,768 bytes, and
decoding it with cp950 yields a string **identical, character for character**,
to the 1,927,903-character archived UTF-8 document. That is also the first data
point for Step 23-c's archive-vs-official sample gate: for this document the
February 2026 archive copy and today's response are the same document.

**Uniform instance structure.** Across the corpus the only units are
`iso4217:TWD`, `xbrli:shares`, `xbrli:pure` and the `iso4217:TWD/xbrli:shares`
divide; the only `format` is `ixt:numdotdecimal`; the only `scale` values are
`3` (thousands, the statements' unit), `0` and `-2`; `sign="-"` is common; there
is no `xsi:nil`, no typed dimension, no `<xbrli:segment>` and no forever period.
Dimensions appear only as `<xbrldi:explicitMember>` inside `<xbrli:scenario>`.
Amounts are printed with thousands separators, so `scale="3"` on `89,680,417`
means 89,680,417,000 TWD.

**One document was re-serialized by the filer's own browser.** 1519 2021Q2 is
the single document with uppercase tags (`<DIV>`, `<TR>`) and lowercase
attribute names; it still contains a `file:///C:/Users/…` link. The lowercasing
turned `xmlns:tifrs-SCF` into `xmlns:tifrs-scf` while two fact names kept
`tifrs-SCF:`, and it broke `Consolidated report` and `Commercial and industrial`
across a line. The parser is therefore case-insensitive throughout, normalizes
whitespace inside header values, and resolves a prefix case-insensitively only
when exactly one declared prefix matches — recording every such repair on the
parsed report rather than silently accepting it.

**Volume.** A document holds between 262 and 8,414 `ix:nonFraction` facts,
913 on average: **41,397,846** across the archive, plus 13,344,559
`escape="true"` narrative blocks that are whole HTML notes, not values.
Statement amounts and narrative blocks are therefore separate storage questions
for Step 23-b, and 41 million rows is the number 23-c has to plan for.

**What filers write where a number belongs.** Two defects recur, and the whole
corpus was parsed to size them:

- **A short placeholder**: `-`, `無`, `null`, or a footnote marker such as
  `註二` (2492 2026Q2), in a cell that still declares `unitRef="TWD"`. 13 facts
  in 10 documents. A dash is not zero and not `xsi:nil`, so the fact is kept
  with no value and the printed text preserved.
- **A whole paragraph**: a filing tool writes narrative into the numeric
  element. 140 in 87 documents, either with an empty `unitRef` (1570 2026Q1
  answers 重大或有負債 with `無此情形`) or with a real one (1512 2020Q3 pastes
  2,475 characters of receivables narrative into `tifrs-notes:Amount2`, which
  still says `unitRef="TWD" scale="3"`). These are not facts. Length is the
  only thing in the source that separates them from a placeholder.

**What the parser refuses, measured before it was made an error.** The review of
PR #39 asked for several fail-closed checks; each was first measured over the
whole corpus, so none of them rejects a real filing:

| Condition | Occurrences in 45,324 documents |
| --- | ---: |
| `NaN` or `Infinity` in an amount | 0 |
| a `format` other than `ixt:numdotdecimal` | 0 |
| a numeric fact with no `format` | 132, all of them the empty-`unitRef` prose above |
| one prefix declared with two different URIs | 0 |
| an uppercase `XMLNS:` declaration | 0 |
| a parenthesised amount, with or without `sign` | 0 |
| a duplicate `xbrli:unit` id | 0 |
| a duplicate `xbrli:context` id | **1** — 2886 2025Q4 declares `AsOf20241231` twice, character for character the same |
| a `xbrli:divide` the strict pattern cannot read | 0 |
| a statement row with more than one `class="zh"` label and a fact | 0 |

So a repeated context or unit id is accepted only when the two declarations are
identical, and refused when they differ; everything else in the table is an
error. Parentheses deserve a note of their own: no archive document prints a
parenthesised amount at all, so that branch rests on no source evidence, and
combining it with `sign="-"` — two conventions for one minus — is refused rather
than resolved by guessing.

**Three documents genuinely fail, all of them 2855.** 2021Q3 and 2021Q4
reference `AsOf20210331`, and 2022Q4 references `AsOf2022121` — a mistyped
date — and neither context is defined anywhere in the document. Failing closed
is correct: the amounts cannot be placed in time. 2855 is a broker-dealer, so
these documents are outside v1 anyway. Everything else parses: **45,321 of
45,324**.

**The account code lives in the row, not in the fact.** Each statement row is
`<td>1100</td><td><span class="zh">現金及約當現金</span><span class="en">Cash and
cash equivalents</span></td>` followed by one amount cell per context. The
legacy `*_xbrl` tables are keyed by that 會計科目代碼, so the parser keeps the
code and both labels with each fact; this is what makes Step 23-c's
code ↔ concept QName reconciliation possible without storing a codebook.

**EPS period roles.** `mops-xbrl-context-role:v1` finds exactly the roles the
statements print: Q1 documents carry only a single-quarter current context, Q2
and Q3 carry a single-quarter and a year-to-date one, and Q4 carries the
full-year one. Prior-year comparatives share the same concepts and differ only
by context, which is why role assignment reads the context, never the value's
position or a duration's length.

#### Step 23-b findings (2026-09-21)

**Which rows are a statement's, and how many.** Each of the three statements
legacy `stock_db` stored is marked by the document's own anchor — `<div
id="BalanceSheet">`, `<div id="StatementOfComprehensiveIncome">`, `<div
id="StatementsOfCashFlows">` — each followed by exactly one `<table>`. Measured
over all 45,324 archive documents: every document prints all three anchors, and
each exactly once. Inside those tables, in the 42,750 documents in the v1
sii/otc non-financial universe:

| | Facts |
| --- | ---: |
| balance sheet | 7,070,429 |
| statement of comprehensive income | 4,352,694 |
| statement of cash flows | 4,757,236 |
| **total** | **16,180,359** |
| of those, with no 會計科目代碼 | **0** |

The rest of the document — 權益變動表, the notes, the 附表 and 12.9 million
`escape="true"` narrative blocks — is out of this step's storage scope by owner
decision (2026-09-21): the scope is the legacy database's, and what else to
store is decided at the end of the ROADMAP.

**The Step 5 fact identity does not fit a real document, and the statement is
what fixes it.** Collision groups per candidate identity, over those 42,750
documents:

| Identity | Documents with a collision | Groups | Groups with differing values |
| --- | ---: | ---: | ---: |
| QName + context + unit | 42,750 | 171,000 | 0 |
| statement + QName + context + unit | **0** | **0** | 0 |
| statement + 會計科目代碼 + context + unit | **0** | **0** | 0 |

The 171,000 are one number printed as a row of two statements — the balance
sheet's `1100` and the cash-flow statement's `E00210` are both
`ifrs-full:CashAndCashEquivalents`, same instant and unit — four in every
document. They never disagree, which is why they are the same number and not a
source inconsistency. Adding the statement removes every collision; the
會計科目代碼 is not needed for identity, because the QName already separates
`A00010` (`ifrs-full:ProfitLossBeforeTax`) from `A10000`
(`tifrs-scf:ProfitLossBeforeTax`) in the same cash-flow table, same context,
same unit, same value.

**The legacy codebook is not the universe.** Restricting to the 1,748 codes in
legacy `xbrl_codebook` also removes every collision, but it drops 1,040
statement rows in 332 documents whose codes the codebook does not list — the
cash-flow subtotals `AA0000`, `AB0000` and `AC0100`–`AC0500`. The anchors are
therefore the cut, not a code list, and no codebook is stored (ROADMAP §16
stands).

**Legacy stored the current period only.** `balance_sheet_xbrl` holds one
period per quarter and `income_statement_xbrl` holds the quarter and the
accumulated period, both current. The documents print the prior-year
comparative columns beside them, and 23-b stores those too: they are rows the
source printed, and dropping a column would need a rule the document does not
give. Legacy parity on that axis is a filter at reconciliation time (Step
23-c), not a parse-time omission.

**`REPORT_ID` is exclusive, and the miss is a page.** Measured 2026-09-21:
`t164sb01?step=1&CO_ID=1101&SYEAR=2025&SSEASON=1&REPORT_ID=A` answers HTTP 200
with 98 bytes, `<h4 ...>檔案不存在!</h4><br>` in cp950; the same request for
1342 with `REPORT_ID=C` answers the same page, and each answers its own id with
a document. So a filer files 合併 or 個體 in a quarter, never both, which is
the archive's one-document-per-(symbol, quarter) shape seen from the endpoint.
The adapter reports that page as `no_such_report` rather than as a parse
failure.


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

### 7.5 What the archive was actually made to prove (Step 22-c)

Measured 2026-09-20 on the whole window, 2020M01–2026M08, both markets:
140,963 archive rows against 150,757 stored versions.

| Claim | 上市 | 上櫃 |
| --- | ---: | ---: |
| `press_report_bound`, at the end of the recovered announcement day | 47,189 | 40,365 |
| `release_rule`, for a row still on the statutory 10th | 21,418 | 18,507 |
| `legacy_capture_bound`, 2026M02 onward | 6,888 | 5,978 |
| archive rows with no version to carry them | 230 | 388 |

140,345 of 150,757 versions now resolve under Market PIT. What the remaining
10,412 are is the point of the step:

- **8,952 belong to issuers no archive month holds.** 95 上市 and 30 上櫃 codes
  appear on the official foreign page and in no archive month, because the
  legacy scraper hard-coded the `_0` URL. Nothing proves when they were
  published, so they keep `unknown` (owner decision, 2026-09-20). Giving them
  the statutory rule would have been look-ahead: the first-seen data for
  2026M02 onward shows 13–311 domestic issuers a month filing after the 10th,
  and a foreign issuer is not more likely to be early. This is why the rule is
  named by the archive importer for the rows that claim it, and is *not*
  declared on the source: a declaration would hand the same instant to every
  row legacy never saw.
- **The other 1,460 are rows the archive holds for an issuer it does have, and
  mostly the corrected versions of 2026M02 onward** (1,088 of them fall in that
  window). Where the issuer
  corrected a row after legacy captured it, the archive's first-captured value
  is its own version and resolves; the corrected one has nothing proving when
  it became public, so it waits for a capture. Step 22-b imported today's
  pages as `gap_fill`, which proves nothing about publication (ADR-0020 §2),
  so for those rows the proof has to come from a later `correction_check` run
  — Step 27's forward capture.

Two consequences are accepted and recorded rather than fixed:

- **The 166 corrected rows of 2020M01–2026M01 resolve from their announcement
  date.** We hold only the corrected value there (restoring the first-published
  one is out of scope, ROADMAP Step 22), so the recovered date lands on it and
  makes it visible earlier than the correction was. 166 rows of 140,963, and
  the 2026M02-onward window is free of it because both the date and the value
  are legacy's own capture there.
- **System PIT answers the first-captured value for a corrected 2026M02+ row.**
  The archive version was ingested last, and System PIT reports the latest
  revision by ingestion time — which is what actually happened, because
  `ingested_at` is storage-generated and the archive really was read today
  (CLAUDE.md §23, §74).

A note legacy mangled is not a new version. Step 22-b measured the four ways
its copy differs from the page with no issuer rewrite (§4.7); a 2026M02-onward
row differing only that way dedups onto the official version and carries the
capture bound, rather than forking a version whose Market-PIT answer would be
the mangled text. 15 rows across the window.
