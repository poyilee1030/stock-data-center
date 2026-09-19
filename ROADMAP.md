# stock-data-center ROADMAP

> 交付以 step 追蹤。一個 step = 一個 branch = 一個 pull request；大到無法審閱的 step 拆成 `step-N-a`、`step-N-b`……（CLAUDE.md §1）。歷史上的 phase 名稱只保留作為舊參照。
>
> 狀態日期：2026-09-18。
>
> 來源實況基準：[`docs/source_field_audit.md`](docs/source_field_audit.md)。本 roadmap 中每個規劃中的 PR，範圍都限於 audit 證明確實存在的欄位。

## 1. 專案目標

`stock-data-center` 取代 `my_stock_project` 的資料庫部分（`stock_db`），為下游研究與 ML 系統使用的台股資料，提供受 PIT 控管、保留 provenance 的儲存。

它必須能回答：

> 在某個歷史時點，哪些資料是有效的、公開可知的、而且確實已經被 ingest？

主要的下游使用者：

```text
stock-eps-model
stock-model-selection
(today: my_stock_project train_eps/, strategies/, backtester/)
```

下游系統絕不可直接查詢 Data Center 的資料庫。

## 1.1 v1 的範圍就是舊系統的範圍，以 PIT 重建

v1 涵蓋 `stock_db` 涵蓋的內容，以及舊系統使用者實際讀取的內容，從 2020-01-02 起，從官方來源重新 ingest，並附上 PIT metadata 與 provenance。

v1 不是資料商可能提供的所有欄位的超集合。只有當一個經驗證的來源欄位能填入時，該欄位才進入 v1（§2.4）。

**v1 的證券範圍只有上市（`sii`）和上櫃（`otc`）。** 興櫃（`rotc`）以及不在任何市場交易的公開發行公司（`pub`）都排除在外。這是 owner 的決定，不是資料取得的限制：MOPS 四種都提供，而交易所對興櫃根本不發布每日行情檔。當來源提供市場選擇器時，adapter 只請求 `sii` 和 `otc`，並在 adapter 邊界拒絕其他值，而不是 ingest 之後再過濾。

---

# 2. 來源實況基準

完整證據在 [`docs/source_field_audit.md`](docs/source_field_audit.md)。本節列出約束之後每個 PR 的事實。

## 2.1 舊系統範圍

| 領域 | 舊系統資料表 | 涵蓋期間 | 舊系統使用者是否讀取 |
|---|---|---|---|
| 每日行情 | `daily_quotes` | 2020-01-02 → 2026-09-11 | 是 |
| 市場指數 | `market_indices` | 同上 | 是（只讀 `index_close`） |
| 官方估值 | `pe_ratio` | 同上 | 是 |
| 法人買賣 | `institutional_investors`、`institutional_summary` | 同上 | 個股是；彙總否 |
| 外資持股 | `foreign_holding` | 同上 | 是（`issued_shares`、`foreign_held_ratio`） |
| 融資融券／借券 | `margin_trading`、`margin_sbl`、`margin_summary` | 同上 | 個股是；彙總否 |
| 月營收 | `monthly_revenue` | 2020M01 → 2026M08 | 是（發布的 MoM／YoY／累計 YoY） |
| 財務報表 | `*_xbrl`、`quarterly_reports_xbrl`、`xbrl_codebook` | 2020Q1 → 2026Q2 | facts 是；codebook 否 |
| TDCC | `shareholding` | 2020-01-03 → 2026-09-11 | 是 |
| 證券 metadata | `stock_info`、`stock_tags` | 當下快照 | `stock_info` 是；tags（MoneyDJ，第三方）否 |
| 公司行動 | `dividend` | 只有 TWSE，2020-02-13 → 2026-09-10 | 否 |
| 衍生資料 | `technical_indicators`、`trust_holding`、`dealer_holding`、`shareholding_concentration`、`valuation_daily`、`margin_pressure_analysis`、`short_interest_analysis` | — | 是 |

舊系統的技術指標和回測都使用未還原的原始價格。舊系統中沒有任何東西使用公司行動資料。

## 2.2 約束設計的來源事實

1. **檢查過的來源都沒有發布逐列的發布時刻。** 在目前的證據規則下，所有匯入的歷史資料都是 `published_at = NULL`，對 Market PIT 不可見。Step 15 是做決定的地方。
   - 舊系統確實記錄了真實的首次看到日期（精度到日）：月營收從 2026M02 起（每天 22:45 執行，每月 1–15 日），XBRL 從 2025Q4 起（每天 23:50 執行）。
   - 更早的舊系統 `publish_time` 值是事後填入的法定期限：2020M01–2026M01 是次月 10 日，2020Q1–2025Q3 是申報期限。
   - 細節見 audit §7.1。
2. **除了 TDCC，每個 v1 領域的官方端點仍提供 2020 年起的資料。** 重新抓取可以透過 Step 9 的 raw-first 生命週期，產生與原始 bytes 一致的 raw artifact。
3. **舊系統的 raw 檔案庫不是官方來源的 bytes。** 舊 scraper 解碼、重新編碼、移除標題列（報告日期只留在目錄路徑裡），並把部分領域解析成 CSV。唯一的例外是 TDCC OpenData 檔案。因此這個檔案庫只有三種用途，不是一般的匯入來源：
   - TDCC 歷史資料
   - 舊系統的首次看到紀錄（月營收從 2026M02、XBRL 從 2025Q4）
   - 對帳基準
4. **官方的 TDCC 歷史資料無法取得。** OpenData 只提供最新一週，入口網站約一年（2026-09-14 時為 51 週）。2025-09-19 之前的週資料只存在於檔案庫，往回 375 週到 2019-06-28（audit §4.9）。
5. **MOPS 月營收和 iXBRL 回傳的是最新更正或修正後的值。** 首次發布的*數值*只有在某次抓取記錄下來時才得以保存：舊系統的首次看到紀錄（月營收從 2026M02、XBRL 從 2025Q4），之後則是 Data Center 的前向抓取。發布*日期*是另一回事：月營收方面，`revswarm` 從有日期的新聞報導重建出 2020M01–2026M01 中 89.7% 的日期（audit §7.4），所以這段歷史大多能以真實公告日（而不是法定期限）對 Market PIT 可見。
6. **發行公司股利宣告是公告型資料，與實際執行的事件沒有連結。** 它們沒有除息日、基準日、發放日，也沒有 locator；MOPS 頁面的註腳自己就這麼說。在單一資料內，`(公司代號, 股利年度, 股利所屬期間, 期別)` 是可用的 key，所以 Step 33 把它們存成獨立的領域；它們永遠不會進入 `corporate_action_versions`。MOPS `t05st09sub` 每次請求提供一個市場一個年度的完整歷史，但法定盈餘公積／資本公積的拆分只從民國 110 年起才有；在那之前，兩種公積是合併成一個數字發布的（audit §4.13）。
7. **交易所結果資料**（`TWT49U`、`TWTAUU`、`TWTB8U`、TPEx `exDailyQ`、TPEx `revivt`、TPEx `pvChgRslt`）每支證券每個實際執行的事件日一列，TWSE 自己的明細 locator 是 `(code, date)`。當年度的檔案也會列出尚未到來日期的結果；那些還不是已執行的事件（ADR-0019）。
   - 提供：前日收盤價與參考價、現金股利、合併的無償配股、現金增資條件、減資換股，以及退還現金。
   - 不提供：公告日、基準日、發放日，也沒有盈餘配股／資本公積配股的拆分。
8. **整份清單的市場指數來源只有收盤、漲跌點數和漲跌百分比。** 沒有指數代碼、開盤、最高、最低或成交金額。指數 OHLC 只有加權指數（TAIEX）有，在 `MI_5MINS_HIST`（`發行量加權股價指數歷史資料`，每次請求一個日曆月，已實際驗證），舊系統從未抓過。沒有找到 TPEx 的對應資料（audit 4.2）。
9. **Step 9 的個股每日 adapter 每支證券每個月需要一次請求**，每日抓取時每個交易日約 2,200 次請求。正式環境需要整個市場的每日端點：每個市場每個交易日一次請求。

## 2.3 沒有來源的欄位

這些既有欄位完全沒有來源欄位，任何 v1 PR 都不得承諾提供。大多數保持 NULL，但**沒有來源不代表是 NULL**：`NOT NULL` 欄位存放的是有文件記載的頁面層級常數，或我們自己產生的值；audit §5 逐欄記錄是哪一種。

```text
daily_price_versions.bid_snapshot / ask_snapshot                （委託簿深度資料；唯一發布的那一檔
                                                                存在 last_bid_price / last_ask_price /
                                                                last_bid_volume / last_ask_volume）
market_index_versions.trade_value
market_index_metadata_versions.effective_from / effective_to   （存的是我們第一次／最後一次觀察到的
                                                                日期，不是官方生效日期）
security_tag_versions.tag / effective_from / effective_to      （只有第三方快照；此領域不在 v1 §16，
                                                                所以表保持空的）
xbrl_concept_catalog_versions.*                                （沒有檢查過任何 catalogue 端點；此領域
                                                                不在 v1 §16，所以表保持空的）
corporate_action_versions.announcement_date / record_date / payment_date
corporate_action_versions.earnings_stock_ratio / capital_surplus_stock_ratio
                                                               （拆分只存在於公告型資料；Step 33
                                                                另外儲存）
monthly_revenue_versions.currency                              （NOT NULL：存放有文件記載的頁面層級
                                                                常數 TWD，絕不為 NULL）
publication_evidence.published_at from an official release     （沒有任何來源發布它）
```

部分有來源的欄位只在某些日期、市場或證券有值。`market_index_versions.open_value / high_value / low_value` 只有 TAIEX 有來源，透過 `MI_5MINS_HIST`（Step 18）；`official_valuation_versions.dividend_per_share` 只有 TPEx；`daily_price_versions.price_direction` 只有 TWSE。audit §5 是完整清單，每欄一列。

兩份清單的規範性、機器可讀形式是 `docs/data_domain_inventory.json` 中的 `storage_contract`：schema 中每張表的每個欄位都在那裡分類，或整張表附理由排除；只要 registry、audit §5 和實際 schema 不一致，就會有 unit test 失敗。它也檢查被記錄為保持 NULL 的欄位確實可為 NULL。

## 2.4 禁止無來源欄位規則

PR 只有在列出以下資訊時，才可以新增或承諾一個儲存欄位：

```text
official endpoint
exact source field label(s)
date range in which the field exists (header variants included)
unit and conversion
```

PR 要把這些證據加進 `docs/source_field_audit.md`。§2.3 中的欄位保持 NULL。API 把它回報為「無法取得」，而不是「資料缺漏」。

---

# 3. 高階架構

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

PostgreSQL 是權威來源。之後可能會在 API 和 resolver 之間加上查詢結果快取（NullCache/Redis），但那不屬於 v1 的交付（§26.2）。

## 3.1 抓取邊界

ingestion 寫成單向的資料流，雖然 v1 全部在一個 process 裡執行：

```text
expected coverage        what each dataset should hold for each period
        |                (Step 16, from the trading calendar)
        v
reconcile                compare against what is stored
        |
        v
fetch jobs               a serializable request + a purpose:
        |                first_capture | gap_fill | correction_check
        v
fetch                    SourceFetcher: job -> FetchedArtifact
        |
        v
raw-first ingest         parse, version, and record the purpose on the ingest run
```

有兩個特性值得現在就寫下來。

**purpose 在發出 job 時就決定，絕不在抓取之後才推斷。** 它決定證據類型（§9）。`first_capture` 產生 `capture_bound`；`gap_fill` 或 `correction_check` 不產生，版本退回使用它的 release rule。如果不這樣做，一筆因為查詢發現缺漏而在 2027 年抓取的資料，會宣稱 2027 年的 `capture_bound`，並在整個 2026 年對 Market PIT 不可見——發布時間會變成「有人剛好什麼時候去看」的函數。

**抓取已經可以替換。** `SourceFetcher` 是只有一個方法的 Protocol，adapter 只描述 resource 並解析 bytes，而 `FetchedArtifact.fetched_at` 是明確的資料，不是呼叫端的 `now()`，所以在別處或更早執行的抓取也帶著誠實的時間戳記。之後要把抓取移出 process，只需要提供另一個 `SourceFetcher`，並把 process 內的 job 清單換成真正的 queue。adapter、證據規則和 writer 都不用改。

v1 不建 queue，也不拆獨立服務。上面的流程是形狀，不是部署拓樸。值得拆出去的理由會是第二個使用者，或是需要多個對外 IP 的來源——目前兩者都不存在。真正存在的壓力是共用的 MOPS 請求配額（§13），由 Step 20-c 的速率控管器在單一 process 內處理。

本節要防止的風險：adapter 自己決定要抓什麼、自己呼叫 fetcher、自己在行內判斷證據。這在單一 process 裡可行，而且在必須拆分的那天之前都看不出問題；到那時，「需要抓什麼」散落在十幾個 adapter 裡，沒有地方能列出來。

---

# 4. 核心架構不變條件

## Invariant A — PostgreSQL 是權威來源

任何快取都不是權威來源。刪除快取不可以改變正確性。

## Invariant B — 快取是可選的

應用程式在以下設定下必須正確運作：

```text
CACHE_BACKEND=none
```

## Invariant C — 快取只影響效能

如果日後加入快取，對同一個標準查詢：

```text
result(cache=none) == result(cache=redis)
```

## Invariant D — 下游系統絕不直接存取 PostgreSQL 或快取

只有 Data Center 的 API／SDK 是公開的。

## Invariant E — PIT 語意由 Data Center 強制執行

下游程式不可以重新實作發布時間、revision 或 ingestion 時間的規則。

## Invariant F — 資料庫的當下狀態不是歷史事實

歷史可見性由時間 metadata 和證據決定，絕不由「這一列今天存在」決定。

## Invariant G — 公司行動的 identity 必須在更正後仍然成立

正規化後的公司行動事件，identity 是 `(security_id, source, source_event_key)`，而且這個 identity 在事件條件被更正後仍然成立。

來源分兩種。

**1. 公告／計畫型資料**

例如：發行公司股利彙總 `t187ap45_L` 和 `mopsfin_t187ap39_O`，以及交易所的預告表，例如 `TWT48U`。它們的日期和條件是可能改變的計畫。

以下都是 revision 內容，絕不可構成 identity：

```text
action_type, board/announcement/ex/record/payment dates, cash amount,
stock or share ratio, reference price, dividend year/period, row ordinal,
company name, adapter version
```

沒有任何公告型資料具備經證明的穩定 identity，所以公告型資料永遠不進入 `corporate_action_versions`。已放棄的股利彙總 pilot 在 TPEx 的實際資料上否證了 `(security, dividend_year, period)`（§21.3）。

**2. 交易所結果資料**

`TWT49U`、`TWTAUU`、`TWTB8U`、TPEx `exDailyQ`、TPEx `revivt` 和 TPEx `pvChgRslt`。每一列記錄交易所在某個交易日實際執行並定價的事件。日期在請求的 `executed_through` 之後的列，是已發布的試算，還不是已執行的事件，不會儲存（ADR-0019）。

- 實際執行的事件日期，或交易所自己的明細 locator（若有），是事件的 identity，而不是 revision 內容：

  ```text
  source_event_key = "<feed>:<locator date>"
  ```

- 同一個 locator 下條件改變，是同一事件的 revision。
- 從資料中消失的列記錄為 retraction。

當下快照中的唯一性是必要的證據，但不是證明。結果資料的 adapter 還必須通過 §27.7 的完整歷史重複掃描與更正回歸測試。

---

# 5. 固定技術堆疊

除非透過 ADR 與 ROADMAP 修訂變更：

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

Docker PostgreSQL image：`postgres:18`。不要使用 `postgres:latest`。

v1 的 raw artifact 儲存是 `data/raw/` 底下的本機檔案系統，包在一層抽象後面，之後可以加上 S3、MinIO 或 NAS。

---

# 6. 時間模型

所有對外有意義的時間戳記都帶時區。來源市場的時區是 `Asia/Taipei`。內部優先使用 UTC 時刻。

- PostgreSQL：`TIMESTAMPTZ`
- SQLAlchemy：`DateTime(timezone=True)`

絕不可默默混用 naive 與帶時區的 datetime。

---

# 7. PIT 語意

## 7.1 Market PIT

> 在市場資訊時間 T，只使用 Data Center 在知識時間 K 之前已知的 publication evidence，哪些資料是公開可知的？

```text
published_at <= information_as_of
AND
publication_evidence.recorded_at <= knowledge_as_of
```

接著在這個知識截止點下，解析出可用的權威證據。

## 7.2 System PIT

> 在時間 T 之前，這個 Data Center 實際且完整地 ingest 了什麼？

```text
ingested_at <= system_as_of           (single-row immutable version)
seal.ingested_at <= system_as_of      (parent+children aggregate)
```

只有已 seal 的 aggregate 可見。

## 7.3 這兩種模式對 backfill 的歷史能回答什麼

- backfill 資料的 System PIT 從它實際匯入的時間開始（2026 年起）。它無法回答「2023-05-15 時已知什麼？」。這是設計使然，永遠不會改變。
- Market PIT 只有在某個版本具有被接受、且 `published_at` 非 null 的證據時，才能回答這個問題。沒有任何來源發布這個時間（§2.2 第 1 點）。
- 在 Step 15 決定之前，backfill 的歷史對 Market PIT 不可見。這是正確的結果，但 ML 使用者無法使用它。

---

# 8. Publication Evidence 語意

業務內容與 publication evidence 是兩條分開的版本鏈。

publication evidence 只能附加。改進或更正的證據會建立取代舊證據的新證據；舊的證據列不會被更新。

絕不可捏造歷史發布時間。如果不知道：

```text
published_at = NULL
```

由規則推導出的界限，只有在記錄為獨立的證據類型、帶有版本化的 rule id、品質等級低於官方證據，且每個來源都明確接受時，才不算捏造的時間。Step 15 決定 v1 是否接受這類界限。

因為證據是另一條鏈，之後才核准的證據可以附加到已經儲存的版本上，不需要改寫業務歷史。

來源系統的發布 locator 不會自動成為業務事件的 identity（見 Invariant G）。

---

# 9. 不可變 Aggregate 語意

XBRL 申報和 TDCC 快照這類複雜資料集是不可變的 aggregate：

```text
draft → children written / validated → seal → visible + immutable
```

只有 seal 會讓複雜 aggregate 變得可見。seal 之後，資料庫的 constraint 和 trigger 必須拒絕 parent／child 的修改。優先使用各資料集專屬、有真正 foreign key 的 seal 表。

---

# 10. Ingestion 時間語意

一般呼叫端不可提供權威的歷史 `ingested_at`。

- 單列不可變版本的系統 ingestion 時間，由受信任的儲存層／資料庫邏輯產生。
- 複雜 aggregate 的權威系統可見時間，在 aggregate seal 時產生。

必須保留時間戳記的歷史 migration，需要另一條受信任的 migration 路徑，並具備明確的 provenance 和測試。

---

# 11. Hash 邊界

以下三者保持分開：

```text
business_content_hash
publication_evidence_hash
raw_artifact_hash
```

- `business_content_hash` 只涵蓋標準化的業務值。
- `raw_artifact_hash` 是原始 bytes 的 SHA-256。

業務內容相同的重複抓取，必須保留 ingest／raw lineage，但不可產生假的業務 revision。

---

# 12. 來源層級的能力

PIT 能力屬於「資料集 + 來源」的組合，記錄在 `dataset_sources`。一個來源經驗證的語意，絕不可授權給另一個來源。

可接受的證據類型依 `(dataset_code, source)` 宣告（ADR-0010）。目前 raw-first 生命週期把 `official` 寫死在程式裡。Step 15 把這項宣告移到各 adapter 的 source policy。

---

# 13. 跨來源政策

第 1 版分開保存各來源的歷史。不可默默平均、合併、覆寫，或挑選最近 ingest 的來源。

如果沒有標準來源政策，而可能有多個來源，就要求明確指定來源，或回傳分開的各來源結果。任何對帳政策都需要 ADR 和永久的回歸測試。

同一來源的兩個端點，不可對同一個邏輯 key 交替寫入 revision。如果它們涵蓋不同欄位，就給它們不同的 source code，或讓其中一個退出正式環境。Step 17-a 以不同的 source code 解決了每日價格的情況。

發行公司／MOPS 的彙總資料和交易所結果資料，不可用證券／日期／金額／比率的啟發式比對，串成權威的事件連結。

---

# 14. Raw Artifact 與 Provenance 規則

每個正規化版本和證據紀錄，都必須能追溯到 ingestion provenance：

```text
ingest_runs → raw_artifacts → normalized business versions → publication evidence
```

raw artifact 以內容定址，而且不可變。絕不可用不同的 bytes 覆寫既有的 raw artifact。

Artifact 來源：

```text
official_fetch   預設；bytes 與官方端點回傳的完全相同
legacy_archive   只用於檔案庫保有、任何官方重新抓取都無法提供的內容：
                   - forward capture 之前的 TDCC 週資料，來自 Step 24
                     指名的檔案庫
                   - 舊系統首次看到的月營收列（2026M02 起）
                   - 2020Q1 起的 XBRL 文件（Step 23）
                 ingest run 記錄檔案庫路徑和檔案 mtime；壓縮檔成員
                 另外記錄其在壓縮檔內的名稱。
                 fetched_at 是 Data Center 讀取的時間。
                 如果檔案庫的檔名與內容不一致，以內容為準，並拒絕
                 該檔案（Step 24）。

v1 依賴一些官方端點無法重現的檔案庫：TDCC `shareholding` 檔案庫、
舊系統的 XBRL 文件，以及舊系統的月營收 `market.csv`；後者現在同時
包含 2026M02 起首次看到的列，以及寫回其中的 `revswarm` 公告日期。
`revswarm.db` 本身不是依賴；它的結果已經在 CSV 裡。

依 owner 決定，這些檔案庫目前全部留在
`~/GitHubLL/my_stock_project/data/raw`，TDCC 檔案庫也不例外。只要 v1
的重建仍依賴這個 repository 以外的路徑，Step 32 就不可宣告 cutover
完成。

因此 `stock-data-center/data/raw` 只是以內容定址的 artifact 儲存區，
不放別的東西。不把來源檔案庫放進去，不只是為了整潔：
`LocalRawArtifactStore.read` 驗證 `storage_uri` 的方式，只是檢查它
是否解析到儲存區根目錄底下；如果檔案庫放在根目錄內，一個直接指向
檔案庫檔案的 `storage_uri` 就能通過完整性檢查。檔案庫放在外面，這
在結構上就不可能發生。
```

---

# 15. XBRL Context Identity

XBRL fact 的 identity 不可只依賴 concept + 日期。使用非 null 的標準 `context_hash`，視情況涵蓋 entity、period、dimensions 和 scenario/segment。優先使用完整的 QName／能辨識 namespace 的 concept identity。

MOPS iXBRL 文件提供帶有明確 dimensions、units 和 decimals 的 context（audit §4.8），所以這個模型有來源依據。

---

# 16. 資料領域歸屬與 v1 涵蓋範圍

`stock-data-center` 擁有觀察到的來源資料集，以及可重用的標準衍生資料集。它不擁有特定模型的實驗性特徵。

| 領域 | v1 | 官方來源（audit 章節） | 備註 |
|---|---|---|---|
| 證券 identity／metadata／生命週期 | MERGED (#10, #11) | `t187ap03_L`、`mopsfin_t187ap03_O`、上市／下市歷史 | 只有當下名稱／產業；沒有歷史產業變更 |
| 交易日曆 | Step 16 | TWSE `FMTQIK`（§4.12） | 只有 TWSE；TPEx 沒有官方來源，實測 1,627 個日期都與 TWSE 相同 |
| 每日價格 | Steps 17-a–c | TWSE `MI_INDEX`、TPEx `stk_wn1430`（§4.1） | 買賣價快照沒有來源 |
| 市場指數 | Step 18 | `MI_INDEX` 指數區段、TPEx `indexSummary`（§4.2）；TAIEX OHLC 用 `MI_5MINS_HIST` | 全部都有收盤／漲跌；OHLC 只有 TAIEX |
| 官方估值 | Step 18 | `BWIBBU_d`、TPEx `pera`（§4.6） | |
| 公司行動（交易所結果） | Steps 19-a–e | `TWT49U`、`TWTAUU`、`TWTB8U`、TPEx `exDailyQ`、`revivt`、`pvChgRslt`（§4.10）；ETF 分割來自 `TWTCAU`、`etfSplitRslt`、`etfRvsRslt` | 沒有公告日／基準日／發放日；沒有 TWSE 面額變更換股比率 |
| 法人買賣／彙總、外資持股 | Step 20 | `T86`、`BFI82U`、`MI_QFIIS`、TPEx `3itrade_hedge`、`3itrdsum`、MOPS `t13sa150_otc`（§4.3–4.4） | |
| 融資融券／借券 | Step 21 | `MI_MARGN`、`TWT93U`、TPEx `margin_bal`、`margin_sbl`（§4.5） | |
| 月營收 | Step 22 | MOPS `t21sc03` `_0`/`_1`（§4.7） | 補上舊系統缺少的 KY 發行公司 |
| 財務報表 | Step 23 | MOPS `t164sb01` iXBRL（§4.8） | 排除金融業，與舊系統相同 |
| 發行公司股利宣告 | Step 33 | MOPS `t05st09sub`，每個市場每年一次；OpenAPI `t187ap45_L`／`mopsfin_t187ap39_O` 作為交叉核對（§4.13） | 新領域；公積拆分只從民國 110 年起 |
| TDCC | Step 24 | OpenData + 合併檔案庫，375 週（§4.9） | |
| 還原價格 | Step 25 | 由交易所參考價推導 | |
| 標準衍生指標 | Step 26 | 衍生 | 移植舊系統的計算程式 |
| 股票標籤、XBRL codebook、信用交易市場彙總 | 不在 v1 | — | 沒有官方來源或沒有使用者 |
| 月營收成長率 | 不在 v1 | MOPS `t21sc03` 有發布（§4.7） | `monthly_revenue_growth:v1` 被觀察到的已發布比較值取代（Step 22） |

欄位層級的清單在 `docs/data_domain_inventory.md`／`.json`。Step 14 讓它與 audit 一致，並加上逐欄的 `storage_contract` registry，由 unit test 對照實際 schema 和 audit §5 檢查。任何已知的 v1 領域都不可以默默變成未對應。

---

# 17. 標準衍生資料集契約

每個標準衍生資料集都需要：

```text
derivation_version
formula/specification
implementation version or git commit
input requirements
PIT-safe input lineage
calendar/timezone convention where relevant
adjustment convention where relevant
```

`computed_at` 是計算的 provenance，不是市場發布時間。

v1 的衍生資料集是移植舊系統使用者讀取的內容（Step 26）。舊系統的綜合「壓力分數」留在下游。

v1 的實體化方式是每個指標存一條滾動的 as-of 序列：每個觀察日期都用該日期截止點時可見的輸入計算。其他 PIT context 在需要時才計算，不實體化。對同一個 PIT context，實體化結果和即時計算結果必須一致。

---

# 18. 公司行動／價格契約

官方每日 OHLC 是觀察到的來源資料。絕不可為了消除機械性的不連續而改寫原始歷史 OHLC。絕不可只憑價格大幅跳動就推斷有公司行動。

```text
raw official OHLC
+ exchange result-feed events (Step 19)
-> versioned adjustment factors
-> adjusted OHLC
```

v1 的還原慣例是交易所參考價比率：

```text
factor(D) = official_reference_price(D) / close_before(D)
```

它適用於每個除權／除息日，以及每個減資或面額變更後恢復交易的日期 D。因為參考價已經扣除現金股利，這個慣例產生的是股利再投入（總報酬型）的還原序列。不含現金股利的純價格序列不在 v1。

一個事件只有在其證據可見的 PIT context 下，才會影響還原序列。

---

# 19. 交付模型——以 Pull Request 為單位的 Roadmap

PR 是規劃、實作、審閱、正確性核准、合併、回退判斷和歷史追溯的單位。

每個 PR 定義：

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

狀態值：`MERGED`、`IN REVIEW`、`PLANNED`、`BLOCKED`、`SUPERSEDED`。

如果必要的正確性標準失敗，就停下來，讓 PR 保持未合併。

---

# 20. Step 帳本

狀態日期：2026-09-18。

| Step | 狀態 | 交付內容 |
|---|---|---|
| 1 | MERGED | 版本化的 PostgreSQL PIT schema 與 v1 儲存契約 |
| 2 | MERGED | 核心 Market／System PIT resolver |
| 3 | MERGED | 證券 metadata + 每日行情的 writer／service 契約 |
| 4 | MERGED | PIT 安全的月營收 writer／service 契約 |
| 5 | MERGED | 財務／XBRL 的 sealed aggregate + EPS 契約 |
| 6 | MERGED | TDCC 快照／股權分散契約 |
| 7 | MERGED | 法人、融資融券、融券賣出與借券的來源資料契約 |
| 8 | MERGED | 市場指數、公司行動與官方估值契約 |
| 9 | MERGED | raw-first 的 TWSE／TPEx 每日行情 ingestion pilot（個股） |
| 10 | MERGED | 當下 TWSE／TPEx 證券 metadata ingestion |
| 11 | MERGED | 權威的證券上市／下市／市場別生命週期歷史 |
| 12 | MERGED | 強化的台灣公司行動契約 |
| 13 | MERGED | 依來源實況重建本 roadmap、`CLAUDE.md`（當時名為 `AGENTS.md`）和 `docs/source_field_audit.md` |
| 14 | MERGED | 清單與儲存契約的來源實況對齊 |
| 15-a | MERGED | 可取得時間的證據詞彙與 ingest purpose |
| 15-b | MERGED | release rule registry 與評估 |
| 15-c | MERGED | 把證據政策套用到 adapter |
| 16 | MERGED | 交易日曆與涵蓋範圍驗證器 |
| 17-a | MERGED | 全市場每日價格 adapter |
| 17-b | MERGED | 全市場每日價格匯入路徑 |
| 17-c | MERGED | 全市場每日價格歷史 backfill 與對帳 |
| 18-a | MERGED | 市場指數 adapter |
| 18-b | MERGED | 市場指數匯入路徑與 backfill |
| 18-c | MERGED | 官方估值 |
| 19-a | MERGED | 結果資料契約、儲存精度與 TPEx adapter |
| 19-b | MERGED | TWSE 結果資料 adapter 及其明細頁 |
| 19-c | MERGED | 公司行動匯入路徑，含 retraction |
| 19-d | MERGED | 公司行動歷史 backfill 與舊系統對帳 |
| 19-e | MERGED | ETF 分割與反分割結果資料 |
| 20-a | MERGED | 個股法人買賣 |
| 20-b | MERGED | 法人買賣市場彙總 |
| 20-c | MERGED | 完整描述來源請求，以及每台主機的請求速率控管 |
| 20-d | MERGED | 外資持股 |
| 21-a | THIS STEP | 融資融券 |
| 21-b | PLANNED | 借券 |
| 22 | PLANNED | 月營收 |
| 23 | PLANNED | 財務報表（iXBRL） |
| 24 | PLANNED | TDCC 股權分散 |
| 25 | PLANNED | 還原價格 |
| 26 | PLANNED | 標準衍生 v1（移植舊系統計算程式） |
| 27 | PLANNED | 排程的前向抓取 |
| 28 | PLANNED | 公開 REST API v1 |
| 29 | PLANNED | Python SDK 與下游整合 |
| 30 | PLANNED | 維運與可觀測性 |
| 31 | PLANNED | 完整的正確性 CI 關卡 |
| 32 | PLANNED | `my_stock_project` 切換與 v1 發布 |
| 33 | PLANNED | 發行公司股利宣告（MOPS OpenAPI），存成獨立領域 |
| 34 | PLANNED | 新增證據目標時仍穩定的 publication-evidence hash |

Steps 1–12 建立了儲存、PIT 和 raw-first 的基礎。它們的 writer 契約包含一些沒有任何來源會填入的欄位（§2.3）。這些欄位保持可為 null、不填值。不刪除它們，因為刪除不會帶來任何正確性上的好處。

Step 編號是本 roadmap 自己的編號，不必與 GitHub pull request 編號一致。到 Step 14 為止兩者一致；之後 ADR-0020 沒有經過 pull request 直接 commit 到 `main`，所以 Step 16 開的是 GitHub #15。這沒有問題，也不會為了修正而重新編號：step 編號識別的是工作，pull request 編號識別的是審閱。每個 step 的驗收報告都記錄交付它的 pull request。

Step 13 是必須重新確立 *step* 編號的地方：已放棄的股利彙總 pilot 佔了這個編號，但根本沒有開成 pull request（§21.3）。原本規劃的 Steps 14–33 在上表重新編成 14–32；33 是新的 step，不是舊編號的延續。原本的「Corporate-Action Identity Research Track」、「Official Reference-Price / Share-Count Pilot」和「Historical Corporate-Action Backfill」由 Step 19 取代。原本的「Source Capability Hook」併入 Step 15。原本的「Legacy Migration and Reconciliation」拆成 Steps 17-a–26 各領域的對帳驗收，以及切換用的 Step 32。原本的快取 step 延後（§26.2）。

---

# 21. 約束後續工作的已合併 Step 註記

## 21.1 Step 9 — 個股每日 pilot

`STOCK_DAY`／`tradingStock` adapter 證明了 raw-first 生命週期可行。它們不適合正式環境的抓取（§2.2 第 9 點）。Step 17-a 決定了它們如何與全市場 adapter 共存：不同的 source code、各自獨立的歷史、不會來回產生 revision。

## 21.2 Step 12 — 公司行動契約

`corporate_action_events` 存放穩定的 `(security, source, source_event_key)`。`corporate_action_versions` 存放行動類型、日期、金額、比率、參考條件和來源條件。Step 19 依 Invariant G(2) 從交易所結果資料填入。§2.3 中的欄位保持 NULL。

## 21.3 Step 13 — 來源實況重建

已放棄的股利彙總 pilot 原本佔這個編號，它從未開成 pull request。Step 13 是取代它的工作：否證其前提的來源調查，以及依來源實際發布內容重建本 roadmap。

它確立的事項，證據在 `docs/source_field_audit.md`：

- 每個官方端點發布什麼、有哪些 header 版本與日期範圍，以及哪些既有欄位沒有來源會填入（§2.3）
- Invariant G 分成公告型資料和交易所結果資料；後者以實際執行日期作為 locator，提供事件 identity，解除了公司行動、還原價格和股利這條線的阻塞
- Step 15 寫成 ADR-0020 的可取得時間證據政策（2026-09-15 核准），包括復原的月營收發布日期
- TDCC 檔案庫從三個目錄合併成一個，共 375 週
- §26 分成做不到的、等待觸發條件的，以及只是不在 v1 的

已放棄 pilot 本身的結果保留為永久 fixture。它提出的 TPEx identity `(security_code, dividend_year, period)` 在 `mopsfin_t187ap39_O` 的實際驗證中失敗：2,483 列，65 個重複群組。代表性的衝突：

```text
security_code 1591, dividend_year 108, period 1
board_date 1080806  and  board_date 1090505
```

沒有任何公告型資料提供在更正後仍穩定的事件 ID。1591/108/1 的衝突保留為永久的回歸 fixture，證明公告型資料會被 adapter 的 identity 政策拒絕。這個 pilot 想要的資訊——現金股利、股票配發、參考價——來自 Step 19 ingest 的交易所結果資料；盈餘／資本公積的拆分則來自 Step 33 以獨立領域儲存的宣告資料。

---

# 22. 規劃中的 PR——對齊與政策

## Step 14 — 來源實況對齊

狀態：**MERGED**。依賴：無。

目標：在寫更多 adapter 之前，讓儲存契約和清單與 `docs/source_field_audit.md` 一致。

範圍：

- 修正 `docs/data_domain_inventory.md` 和 `.json` 中宣稱有來源、實際沒有的欄位：
  - 股票標籤的生效日期
  - 指數成交金額，以及 TAIEX 以外的指數 OHLC（§2.3）
  - 每日委託簿深度（`bid_snapshot`／`ask_snapshot`）；唯一發布的那一檔有來源，屬於 `last_bid_*`／`last_ask_*`
  - 把月營收幣別當成觀察值
  - 公司行動的公告日／基準日／發放日，以及盈餘／資本公積的拆分
- 修正遺漏了有來源、且使用者會讀取的欄位：月營收已發布的比較值，以及 Step 18 新增的 TAIEX OHLC。
- 把股票標籤、XBRL codebook 和信用交易市場彙總標為不在 v1，並新增領域 `dividend_declaration_versions`（Step 33）。
- 新增契約測試：每張觀察型 `*_versions` 表的每個欄位，都要對應到經 audit 的來源欄位，或列為無來源、部分有來源。

Schema 影響：無。Migration：無。PIT 影響：無。

已交付：

- [x] `docs/data_domain_inventory.md`／`.json` 上述每一項宣稱都已修正
- [x] 股票標籤、XBRL codebook、信用交易市場彙總和 `monthly_revenue_growth:v1` 標為不在 v1；新增 `dividend_declaration` 作為規劃中的領域
- [x] audit §5 改寫成每欄一列，涵蓋無來源*和*部分有來源的欄位
- [x] 在 `docs/data_domain_inventory.json` 加入 `storage_contract`：24 張表共 194 個欄位，各標為 `sourced`／`partially_sourced`／`unsourced`／`internal`，其餘 29 張表以名稱和理由排除
- [x] 涵蓋範圍不限於 `*_versions`：`financial_facts`、`tdcc_distribution`、它的 codebook 表、`corporate_action_events` 和 `security_transfer_events` 也存放來源值，也都已分類
- [x] 每個無來源欄位都記錄它的效果——*保持 NULL*、*存放有文件記載的常數*、*存放衍生值*或*整張表保持空的*——因為其中六個是 `NOT NULL`
- [x] `tests/unit/test_pr14_storage_contract_source_coverage.py` 讓 registry、audit §5 和實際的 SQLAlchemy metadata 互相核對

audit 隱含但沒有寫明的兩項修正，由本 PR 加進 §5：

- `corporate_action_versions.old_shares`／`new_shares` 是部分有來源：只有減資，因為 TWSE `TWTB8U` 面額變更的明細欄位尚未驗證，也沒有找到 TPEx 的面額變更端點（§4.10）。
- `security_metadata_versions.name`／`industry` 是部分有來源：快照只發布當下的值，所以較早的生效日期帶的是當下的值（§4.11）。

驗收：

- [x] 清單、audit 和 schema 一致
- [x] 以下情況新測試會失敗：任何已分類的表新增了沒有來源對應的欄位；出現了不在任何清單中的新表；被記錄為保持 NULL 的欄位是 `NOT NULL`；或某個 `observed` 目標指名的欄位不存在於任何 migration 或規劃中的 PR

範圍外：刪除無來源的欄位。

## Step 15 — 可取得時間的證據政策

ADR-0020 已核准（owner，2026-09-15），並確定以下政策。依賴：Step 14、Step 16——每條 release rule 都要依 Step 16 的交易日曆，把落在非營業日的期限往後移，所以 Step 16 先完成。

為了審閱而拆分（CLAUDE.md §1）：

- **Step 15-a — MERGED。** 詞彙及其 provenance：`evidence_types` registry（在儲存層強制 ADR-0020 的排序）、`ingest_runs.purpose` 和 `raw_artifact_observations.artifact_origin`。它本身就是正確的——詞彙存在且被強制執行，而 adapter 仍然產生 `unknown` 證據。
- **Step 15-b — MERGED。** release rule registry 及其對照 Step 16 日曆的評估，加上 `evidence_plan`：純粹決定一次 run 宣告的 purpose 讓它有權宣稱什麼。沒有評估器的版本化規則只是承諾，不是事實，所以 registry 和讀取它的程式一起出貨。它本身就是正確的：規則能解析、政策能計算，而且沒有任何 adapter 的行為改變。
- **Step 15-c — MERGED。** 套用：生命週期寫入規劃好的證據，每個來源透過 `accepted_evidence_types` 選擇加入新類型，各資料集的對帳記錄改變了什麼，並改寫 CLAUDE.md §31–32。改寫放在這裡，是因為 §31–32 描述的是行為，而行為要到 adapter 改變時才改變。

問題：沒有任何來源提供逐列的發布時刻（§2.2 第 1 點）。只接受官方證據時，匯入的歷史對 Market PIT 不可見，而 System PIT 從匯入時間開始（§7.3）。兩者都無法回答使用者的問題（「在日期 D 時什麼是可知的？」）。

舊系統用兩種方式回答：

- 月營收從 2026M02、XBRL 從 2025Q4 起，真實的首次看到抓取日期
- 較早期間則是事後填入的法定期限（audit §7.1）

核准的決定（ADR-0020）：新增三種證據類型，由每個 `(dataset, source)` 選擇加入。

```text
release_rule    版本化、有文件記載的「不晚於」時刻，由來源的發布排程
                或法定期限推導。
                規則（當日結束，Asia/Taipei；確切措辭由 ADR 固定）：
                  月營收                   -> 次月 10 日
                  財務報表，一般產業       -> Q4 03/31、Q1 05/15、Q2 08/15、Q3 11/15
                                              （舊系統的時間窗結束日與 train_eps
                                               截止點；Q2/Q3 比法定的 08/14、
                                               11/14 晚一天）
                  財務報表，金融業         -> 不在 v1 範圍（Step 23）
                  交易所每日資料集         -> 次一日曆日 03:00
                                              （已核准；交易所會在資料確定前
                                               發布當日資料，audit §7；舊系統
                                               23:30 的執行在觀察到的 27 個交易
                                               日中有 5 天需要 03:00 的重試，
                                               audit §7.2）
                  TDCC 每週                -> 資料日期後第一個星期日 12:00
                                              （已核准；依據的是舊系統星期日
                                               10:20 的工作，不是 TDCC 發布的
                                               排程——規則記錄了這個限制）
                每條規則至少是把法定期限移到下一個營業日（Step 16 日曆）。
                例如 2021Q2 解析為 2021-08-16，而不是 08-15；2026M04 營收
                解析為 2026-05-11（audit §7.1）。
                evidence_source 記錄 rule id + version。

capture_bound   Data Center 第一次成功抓取到包含該版本之 artifact 的時刻
                （經證明的上界）。

legacy_capture_bound
                舊 scraper 記錄的首次看到日期，轉換成保守的時刻：第一次
                包含該列的排程執行的結束時間（月營收 22:45 的執行；XBRL
                23:50 的執行，再以檔案 mtime 收緊）。只適用於舊系統每日
                工作在紀錄為真實的期間內的抓取（audit §7.1）。它絕不適用
                於合成的期限值，也絕不適用於抓取時間窗結束後的 backfill
                執行日期（例如 XBRL 2026-08-01 和 2026-08-17 的檔案）。
                evidence_source 指名舊系統的檔案。

press_report_bound
                從報導該申報、帶日期的次級紀錄重建出的發布日期，以當日
                結束（Asia/Taipei）解析。日期為 D 的新聞證明該值在 D 已
                公開，所以這是真實的發布界限，不是排程估計。它的排名低於
                兩種抓取類型，因為精度只到日、是重建的，而且有可量測的
                殘餘錯誤率。
                v1 來源：已寫入舊系統月營收檔案庫的逐列 `publish_time`
                （`data/raw/monthly_revenue/<year>/<year>M<month>/market.csv`），
                2020M01-2026M01 的 128,063 列中有 114,910 列（89.7%），
                經 `revswarm` 的三道污染防護驗證，並與今日 MOPS 的值交叉
                核對，一致率 99.10%（audit §7.4）。
                evidence_source 指名該檔案庫檔案、它的 mtime，以及它的
                raw artifact hash。逐列的 engine／verifier provenance 只
                存在於 `revswarm.db`，而 §14 禁止它成為 ingest 時的依賴，
                所以不記錄。
```

規則：

- 一個版本的優先順序：`capture_bound`，然後 `legacy_capture_bound`，然後 `press_report_bound`，然後 `release_rule`。`release_rule` 絕不會讓版本比抓取或新聞報導所證明的更早可見。
  - 前向抓取的資料使用 Data Center 的抓取時間。
  - 2026M02 起的月營收和 2025Q4 起的 XBRL 使用舊系統的首次看到日期。
  - 更早的歷史使用規則。
- 如果每日工作的抓取時間晚於規則時刻（晚申報者），該列的規則就被否證，只記錄抓取證據。
- backfill 執行的抓取不是首次看到的證據，所以不能否證規則。這類列的解析方式與未被抓取的歷史相同。
- 2026M02 起的月營收，舊系統紀錄中沒有的 `_0` 列，在舊系統最後一次執行（15 日）時尚未公開。它不取得規則證據，以 Data Center 的抓取時間解析。
- 在規則時刻之後才第一次抓到的 revision，只取得 `capture_bound`。更正絕不會在實際被看到之前可見。
- 有記載的限制：在任何抓取紀錄之前的歷史，存的是最新更正後的值，並在規則時刻變為可見。這允許更正的前視偏差，舊系統也有這個問題。它影響 2026M02 之前的月營收、2025Q4 之前的 XBRL，以及前向抓取之前的所有交易所每日資料。Step 27 回報前向抓取的 revision 比率，以量測這個效應的大小。
- 把 source policy（能力與可接受的證據類型）從通用的 raw-first 流程，移到各 adapter 的來源宣告。這吸收了原本的「source capability hook」PR。
- 在 ingest run 上記錄 `purpose`——`first_capture`、`gap_fill` 或 `correction_check`——並由它推導證據類型（§3.1）。purpose 在請求抓取時設定，絕不事後推斷，所以一列因為查詢發現缺漏而在多年後才抓取的資料，不能在那個較晚的時刻宣稱 `capture_bound`。

替代方案：維持只接受官方證據。那麼 v1 只能透過 System PIT 提供歷史，而歷史 Market PIT 查詢什麼都回傳不了。

Schema 影響：`ingest_runs` 上一個具型別、帶 `CHECK` constraint 的 `purpose` 欄位，加上 §14 的 artifact 來源，目前同樣沒有建模。`evidence_type` 和允許清單已經存在。Migration：那個欄位、來源，以及允許清單的資料。

v1 不抓取尚未確定的當日資料（ADR-0020 §10）。交易日 D 在 D+1 03:00 解析，所以當日收盤後的策略無法回測。這是已知的缺口，不是前視偏差：確定後的值在 D 15:00 不可見，而不是錯誤地可見。之後任何要把交易所規則提早到 03:00 之前的提案，都必須在同一個 PR 引入當日抓取。

驗收：

- CLAUDE.md §31–32（未知發布與 backfill 規則）更新為與 ADR-0020 一致。
- 每條規則都有 id、version，以及引用的官方排程或法條。永久測試涵蓋週末、假日、跨年，以及落在非營業日的期限。
- 規則之後的 revision 回歸測試。
- 交易所每日規則以前向抓取驗證：交易日 D 的資料在規則時刻可以抓得到。
- 對從未被抓取過的期間做 `gap_fill` ingest，不會產生 `capture_bound`；版本以其 release rule 解析。

範圍外：為沒有文件記載排程或法條的來源捏造時刻。

耦合說明：adapter Steps 16–24 不被這個 PR 阻塞。在 #15 完成之前，它們產生 `unknown` 證據。核准的證據之後再附加，不需要動到業務版本（§8）。

---

# 23. 規劃中的 PR——官方來源 Adapter 與歷史資料（2020-01-02 起）

Steps 16–24 的共通規則：

- 每個官方端點一個 adapter，同時用於歷史重新抓取和每日營運。
- 有節流、可續跑、有 checkpoint 的歷史執行（Step 9 生命週期）。各 PR 列出大約的請求量。
- audit 中的 header 版本都是明確、有測試的 parser 案例。未知的 header 會把 artifact 送進 quarantine。
- 驗收包括與舊系統 `stock_db` 在 2020-01-02 → 2026-09-11 期間的對帳報告，每個差異都要分類。比較前先做單位正規化（張 → 股、千元 → 元）。

## Step 16 — 交易日曆與涵蓋範圍驗證器

狀態：**MERGED**。依賴：Step 14。Step 15 需要它（ADR-0020 的 release rule 透過這個日曆把期限移離非營業日）。

來源契約：TWSE `FMTQIK`（每月一次請求，列出每個實際交易日），在可取得時與 TWSE `holidaySchedule` 以及全市場每日檔案的日期交叉核對。TPEx 2020 年起的交易日必須與 TWSE 相同，否則差異必須來自 TPEx 的官方來源。

Schema 影響：`trading_calendar_versions`（+ 它的觀察連結，以及第十七個 `publication_evidence` 目標）和 `dataset_expected_coverage`。ADR-0021 記錄了版本以月而不是以日為粒度的原因：月份是發布的 artifact，*也是* revision 的單位，所以更正一個休市日會改變日期清單，成為新版本。以日為粒度的表無法表達被更正的休市日，因為列永遠不會被刪除。

驗證器必須先知道每個資料集應該有什麼，才能回報缺口。這項知識以可查詢的預期涵蓋宣告公開，而不是隱含在報告產生程式裡，因為 Step 27 要把它轉成抓取 job（§3.1）。

驗收：

- [x] 2020-01-02 → 2026-09-11 的日曆與舊系統檔案庫的交易日一致，或每個差異都有解釋
- [x] 颱風休市（例如 2024-07-24/25）以休市呈現
- [x] 涵蓋報告把非交易日和缺漏資料分開，而且不依賴今天的證券範圍
- [x] 可以直接查詢某個 (dataset, period) 範圍的預期涵蓋，而不只是產生報告

實作前量測的基準：在這段期間內，TWSE 和 TPEx 開市的日期完全相同，都是 1,627 天，雙向差異為零，所以本 PR 依賴的 TPEx 等價性是量測出來的，不是假設的。TPEx 沒有官方的日曆來源，所以不寫入任何 TPEx 列；TPEx 資料集宣告使用 TWSE 日曆，宣告中記錄了原因。

## Step 17 — 全市場每日價格

拆成 17-a、17-b 和 17-c：合在一起會超過一個 pull request 能審閱的量
（CLAUDE.md §1），而拆分的接縫讓每一部分本身都正確——
先是經驗證的解析，然後是匯入，最後是歷史資料。

來源契約，2026-09-16 實際驗證（audit §4.1）：

- TWSE `rwd/zh/afterTrading/MI_INDEX?date=…&type=ALLBUT0999&response=json`。
  共十張表；股票區段是第一個欄位為 `證券代號` 的那張，絕不以表的索引
  判斷。指數區段屬於 Step 18，重用同一個 artifact。休市日回應 `stat`
  `很抱歉，沒有符合條件的資料!`。
- TPEx `www/zh-tw/afterTrading/otc?date=YYYY/MM/DD&type=EW&response=json`，也就是
  audit 稱為 `stk_wn1430` 的資料，有三種 header 版本。休市日回應 `stat`
  `ok`，列數為零。
- 每個 (market, trade date) 一個 resource；這段期間約 3,300 次請求。

source code 是 `twse_mi_index` 和 `tpex_otc_quotes`，與 Step 9 的個股 pilot
（`twse`、`tpex`）不同，因為兩種端點的欄位涵蓋不同，而 Invariant G 禁止
它們對同一個邏輯 key 交替產生 revision。

Schema 影響：無。`daily_price_versions` 涵蓋有來源的欄位，買賣價快照保持
NULL。

### Step 17-a — Adapter

狀態：**MERGED** (#19)。依賴：Step 9 生命週期。

範圍內：兩個 adapter 及其請求／解析契約、每一種 TPEx header 版本、單位宣告，
以及改寫 audit，記錄實際資料真正發布了什麼。

驗收：

- 每種 TPEx header 版本都能解析；未知的 header，或與其 header 矛盾的
  `flagField`，都會 fail closed
- 來源單位是宣告的，不是推斷的：TWSE 股／元、TPEx 股／元，兩個市場揭露的
  買賣量都以張為單位，×1,000 正規化為股
- 休市日在兩個市場都 fail closed
- 在涵蓋三種版本的日期上，解析出的值與舊系統 `daily_quotes` 對每支舊系統
  有收錄的證券都相同
- `price_direction` 只宣稱各資料實際發布的內容

範圍外：儲存、證據、CLI、backfill。

### Step 17-b — 匯入路徑

狀態：**MERGED** (#20)。依賴：Step 17-a、Step 11、Step 16。

範圍內：整個市場日的集合式寫入、建立在 Step 9 raw-first 生命週期上的
importer、它的 CLI、兩個 source code 選擇加入 `exchange_daily_settled@1`
及其可接受的證據類型，以及 `daily_price` 的預期涵蓋宣告。

驗收：

- 每個市場有一個真實交易日以 raw-first 方式端到端匯入
- 重跑同一天不產生 revision；內容改變時產生一個
- 不會與 Step 9 pilot 來回產生 revision：source code 不同
- 被 quarantine 的日期保留其 raw artifact，不寫入任何業務列
- 匯入的列依其來源宣告的規則在 Market PIT 下解析
- downgrade 拒絕移除匯入歷史所需要的 source policy

範圍外：backfill。

### Step 17-c — 歷史 backfill 與對帳

狀態：**MERGED** (#21)。依賴：Step 17-b。

範圍內：有節流、可續跑的日期區間 runner；兩個市場 2020-01-02 →
2026-09-11 的 backfill；舊系統對帳報告，以及有價格但沒有 metadata 列的
證券報告。

驗收：

- 期間內每個交易日都已匯入，或明確回報為缺口
- 每日列數，以及 OHLC、成交量、成交金額和成交筆數，都與舊系統
  `daily_quotes` 對帳，每個差異都已分類
- 續跑的執行會接續，而不是重新開始，重跑保持冪等
- 一份有價格但沒有 metadata 列的證券報告（ETF、TDR、特別股）

範圍外：還原價格；在正式環境使用個股 pilot。

## Step 18 — 市場指數與官方估值

拆成 18-a、18-b 和 18-c，沿著 Step 17 證明過的接縫：先是經驗證的解析，
然後是匯入路徑與其 backfill，最後是第二個資料集。五個 adapter 和兩個
importer 遠超過一個 pull request 能審閱的量（CLAUDE.md §1），而且指數和
估值是不同資料表中的不同資料集。

來源契約，2026-09-16 實際驗證（audit §4.2、§4.6）：

- TWSE 指數：`MI_INDEX` 的指數區段。Step 17-c 已經儲存了這些 artifact，
  所以 18-b 可以重新解析它們，不用再抓 1,627 個檔案——但前提是在生命週期
  中加入重新處理的路徑；生命週期以 `import_id` 界定 checkpoint 範圍，目前
  沒有這種路徑。adapter 指名已儲存的 resource；重用不是平白得來的。
- TPEx 指數：`www/zh-tw/afterTrading/indexSummary`，每個交易日一個檔案。
- TAIEX OHLC：`rwd/zh/TAIEX/MI_5MINS_HIST?date=YYYYMM01`，每次請求一個日曆月。
- TWSE 估值：`BWIBBU_d`。TPEx 估值：舊的
  `web/stock/aftertrading/peratio_analysis/pera_result.php` CSV，仍在提供，
  也仍可回溯到 2020。*2026-09-17 更正：*新網站確實以 JSON 提供同一張表，
  `www/zh-tw/afterTrading/peQryDate`，而且與 CSV 逐列相同（audit §4.6）。

指數 identity 是 `(source, section, published index name)`。這些資料中都沒有
官方指數代碼，而只用發布的名稱會衝突：TPEx 的 34 個名稱中有 32 個在價格和
報酬兩個區段重複出現（audit §4.2）。區段是結構性的，不是業務值——價格指數
不會變成報酬指數。

### TPEx 指數 OHLC 的 spike，已解決

ROADMAP 要求先做 spike，才能承諾櫃買指數 OHLC。結果：audit §4.2 的否定結論
**是錯的，但資料仍然無法 backfill**。

`openapi/v1/tpex_index`（`櫃買指數歷史資料`）確實為 `櫃買指數`（TAIEX 的櫃買
對應指數）發布 `Open/High/Low/Close/Change`。它**不接受任何參數**——`d=`、
`date=`、`yr=/mn=` 都被忽略——而且不論名稱為何，永遠回傳當月資料，
2026-09-16 時是 12 列。所以櫃買指數 OHLC 無法 backfill 2020–2026，這些日期
保持 NULL。Step 27 的前向抓取可以從開始那天起累積。

和 `MI_5MINS_HIST` 一樣，它只涵蓋那一個主要指數，而不是 TPEx 發布的其他 33 個。

### Step 18-a — 市場指數 adapter

狀態：**MERGED** (#22)。依賴：Step 9 生命週期、Step 17-c。

範圍內：TWSE 指數區段 adapter、TPEx `indexSummary` adapter 和
`MI_5MINS_HIST` TAIEX adapter，以及它們解析成的契約型別。不寫入任何東西。

驗收：

- 讀取每一個 TWSE 指數區段，而不只第一個——價格和報酬，TWSE、跨市場和
  TIP 都一樣
- 指數 identity 在 TPEx 於兩個區段發布同一名稱時仍然成立
- adapter 指名存放其 bytes 的已儲存 resource，但不宣稱生命週期已經重用它
- 沒有正負號的 `漲跌點數` 由它自己的欄位決定正負，無法辨識的符號會
  fail closed
- 只有 TAIEX 解析 `open/high/low`，其他指數保持 NULL
- 休市日、日期不符、header 改變，以及同一區段內名稱重複，都各自以自己的
  reason code fail closed

範圍外：儲存、證據、CLI、backfill。

### Step 18-b — 市場指數匯入路徑與 backfill

狀態：**MERGED** (#23)。依賴：Step 18-a、Step 16。

範圍內：整個指數日的集合式寫入、importer、source policy 與涵蓋宣告、CLI，
以及兩個市場加上 TAIEX 月份的 2020-01-02 → 2026-09-11 backfill。

驗收：

- 舊系統 `market_indices`（`index_close`、`index_change_points`）在兩個市場
  都已對帳，每個差異都已分類
- 每個交易日 `MI_5MINS_HIST` 的 TAIEX 收盤都等於 `MI_INDEX` 的收盤，否則該列
  被 quarantine
- 重新處理的路徑從 Step 17-c 已儲存的 artifact 匯入 TWSE 指數，該市場不需要
  新的抓取——或者，如果沒有建這條路徑，就直接說明需要重新抓取，而不是
  含糊帶過
- 舊系統從未收集的 TPEx 報酬指數，回報為新資料，而不是對帳差異

範圍外：官方估值；指數成交金額；沒有任何端點提供過去日期的櫃買指數
OHLC；超出明確官方證據的指數更名連結。

### Step 18-c — 官方估值

狀態：**MERGED** (#29)。依賴：Step 18-b。

範圍內：`BWIBBU_d` 和 TPEx `pera` adapter、它們的 importer、source policy
與涵蓋宣告，以及 backfill。

2026-09-17 依下列證據由 owner 決定：

- TPEx 從 `www/zh-tw/afterTrading/peQryDate` JSON 讀取（source code
  `tpex_pe_qry_date`），而不是舊的 CSV。舊的 `pera.php` 頁面會 redirect 到
  載入這個 JSON 的頁面，兩者逐列相同。TWSE 是 `twse_bwibbu_d`。
- 來源標示為未計算的比率存 NULL：TWSE `-`、TPEx `N/A`（兩者都有文件記載）、
  TPEx `"null"`，以及任一交易所剛好為零的比率。TPEx 在 2021-07-26 到
  2022-11-02 把上市首日的比率印成 `"null"`（6840 等），之後印成 `"0"`
  （6720，2024-12-04）。兩者都沒有官方說明；依 owner 決定都視為未計算。
  負的比率、殖利率或股利會把該列送進 quarantine，檔案中的其他列仍然匯入。
- `財報年/季` 從各交易所自己的格式存成西元 `YYYYQn`，`股利年度` 存成
  民國 + 1911。

*2026-09-17 更正（audit §4.6）。* 先前的文字說 TPEx 資料是 big5 CSV、沒有
JSON 替代方案，也沒有自己宣告的報告日期。兩者都錯。CSV 是 MS950，並寫明
`資料日期`。官方新網站透過 `www/zh-tw/afterTrading/peQryDate` 以 JSON 提供
同一張表，附有表格日期、列數和公式說明。兩種格式都沒有寫明單位：比率是
倍數，`殖利率(%)` 是百分點，依說明中的公式，`每股股利` 是每股元。

驗收：

- 舊系統 `pe_ratio` 在兩個市場都已對帳；`dividend_yield`、`pb_ratio`、
  `dividend_year` 和 `report_period` 在舊系統沒有對應，是新資料
- 2025-06-24 那份 5 欄的 TWSE `BWIBBU_d` 檔案由明確的版本解析，或被
  quarantine
- 2025-01-02 之前的 TPEx `財報年/季` 變成 NULL，而不是錯誤；兩個市場不同的
  格式（`115/2` 與 `115Q2`）各自明確解析
- 只有 TPEx 填入 `dividend_per_share`

範圍外：任何由 Data Center 自行計算的估值，那屬於標準衍生資料，歸 Step 26。

## Step 19 — 交易所公司行動結果資料

拆成 19-a 到 19-e。六種資料、兩個 TWSE 明細頁、一次儲存更正、一條帶
retraction 語意的匯入路徑，再加上一次 backfill，遠超過一個 pull request
能審閱的量（CLAUDE.md §1）。拆分的接縫讓每一部分本身都正確：契約和填入
它的 adapter、另一個交易所的 adapter、匯入、歷史，以及途中發現的 ETF 資料。

依賴：Step 9、Step 12、Step 17-c。

取代：已放棄的股利彙總 pilot（§21.3）、原本的參考價 pilot，以及原本的公司行動 backfill。

來源契約（audit §4.10，2026-09-16 逐年重新驗證）：

- TWSE `TWT49U` + `TWT49UDetail`（兩種 header 版本：普通股和特別股）
- TWSE `TWTAUU` + `TWTAVUDetail`
- TWSE `TWTB8U`。`TWTB8UDetail` 已在 19-a 驗證：它沒有發布換股比率，所以不抓明細。
- TPEx `exDailyQ`
- TPEx `revivt`
- TPEx `pvChgRslt`，面額變更資料，已在 19-a 驗證：它發布換股比率和前後兩個面額。

所有 TWSE 結果資料都以 `response=json` 請求，絕不用 `response=csv`。CSV 呈現方式把詳細資料壓平成連結文字 `除權息資料`，破壞了 Invariant G(2) 依賴的 `"{code},{yyyymmdd}"` locator（audit §4.10）。這也是舊系統 CSV 檔案庫只能作為數值對帳基準、永遠無法確立事件 identity 的原因。

歷史：每種清單資料都接受一整年的日期區間，所以 2020-2026 每種資料 7 次請求。TWT49U 在 2020-2026 列出 7,827 個事件——包括舊系統從未保留的 ETF、特別股和 TDR——所以約 7,800 次 `TWT49UDetail` 請求，加上 157 次 `TWTAVUDetail`。TPEx 的列本身就是完整的。

Identity：Invariant G(2)。ADR-0019 記錄結果資料的 identity 規則、`executed_through` 邊界，以及下列儲存更正。

對應：

```text
息 -> ex_dividend        權 -> ex_right        權息 -> ex_right_dividend
capital reduction -> capital_reduction; kind from 減資原因;
                     old/new shares from 每壹仟股換發新股票; cash from 每股退還股款
par-value change  -> TPEx: stock_split / reverse_split, 1 -> 變更股票面額換股率
                     TWSE: other, with reference prices kept (no ratio published)
cash_dividend_per_share, free_share_ratio (÷1,000), rights_ratio (÷1,000),
subscription_price, close_before, official_reference_price,
official_rights_dividend_value (signed); remaining source columns -> source_terms
a published 0 -> NULL (the item does not apply)
security name, TWT49U 最近一次申報* -> not stored with the event
```

§2.3 中的欄位保持 NULL。

### Step 19-a — 結果資料契約、儲存精度、TPEx adapter

狀態：**MERGED** (#24)。

範圍內：locator、請求和列的契約型別；拒絕公告型資料；`executed_through`
邊界；這些資料迫使的儲存更正——有正負號的 `official_rights_dividend_value`、
小數十二位的股數比率、小數八位的每股元——在 migration `8e4b2c7d9a13`；三個
TPEx adapter；ADR-0019；改寫 audit。

驗收：

- 使用 1591/108/1 fixture 的拒絕公告型資料測試
- `source_event_key` 是 `"<feed>:<locator date>"`，而且沒有任何 revision 內容進入其中；不同事件有不同的 key
- 每種 TPEx 資料的完整歷史中，`(code, locator)` 重複數為零
- 日期在 `executed_through` 之後的列只計數，不解析成事件
- 2020-2026 的每一列 TPEx 資料都能對應，或附上說明的理由被 quarantine
- 儲存的比率或有正負號的值能完全還原；downgrade 拒絕舊欄位無法容納的歷史；migration 之後未改變的歷史仍會去重

範圍外：TWSE adapter、儲存寫入、retraction、source policy、CLI、backfill。

### Step 19-b — TWSE 結果資料 adapter

狀態：**MERGED**。依賴：Step 19-a。

範圍內：`TWT49U`、`TWTAUU` 和 `TWTB8U` 清單 adapter，`TWT49UDetail` 和
`TWTAVUDetail` adapter，兩種明細 header 版本，以及以明細補完列的邏輯。

驗收：

- [x] 每種 TWSE 資料的完整歷史中，`(code, locator)` 重複數為零
- [x] 解析出的清單值在日期、前日收盤、參考價、權值+息值和類型上，都與舊系統 `dividend`（6,182 列）相同——在 19-a 之前量測：差異為零，另有 1,602 列只有官方有（1,402 個 ETF、170 個特別股、30 個 TDR）
- [x] `最近一次申報*` 和證券名稱絕不進入業務內容
- [x] TWSE 的面額變更不儲存任何股數條件

每個明細頁都在 19-d 抓取並對應；19-b 以 2020-2026 間抽樣的 68 個明細證明
對應正確，涵蓋兩種 header 版本和兩種減資類型。

### Step 19-c — 公司行動匯入路徑

狀態：**MERGED**。依賴：Step 19-b、Step 11、Step 16。

範圍內：一個區間檔案的集合式事件註冊與版本寫入、TWSE 區間檔案需要的明細
抓取、對在檔案已執行涵蓋範圍內消失的列所屬事件做 retraction、六個 source
policy 及其證據類型，以及 CLI。

驗收：

- [x] 更正回歸測試：同一個 locator 條件改變時，產生同一事件的新 revision；被移除的列產生 retraction，而不是刪除
- [x] 在已執行涵蓋範圍之外的列，絕不會因為缺席而被 retract
- [x] 重跑一個區間不產生 revision
- [x] 被 quarantine 的區間保留其 raw artifact，不寫入任何業務列

TWSE 區間檔案逐列明細頁的抓取，發生在寫入 transaction 開啟之前
（`RawFirstImporter._capture_dependencies`，ADR-0022），所以一個無法補完
某列的明細，會像清單解析失敗一樣把整個區間送進 quarantine。retraction 是
自己的只可附加的 `corporate_action_retractions` 表，每個 `(event_id,
raw_artifact_id)` 一筆事實；之後重新出現是否撤銷 retraction，延到有讀者
需要答案時再決定（ADR-0022 §1）。

### Step 19-d — 公司行動歷史 backfill 與對帳

狀態：**MERGED** (#28)。依賴：Step 19-c。

六種資料 2020-01-01 → 2026-09-11 的真實 backfill 已完成（ADR-0022 §6–8）：
identity 重複數為零，而 `twse_twt49u` 與舊系統 `dividend` 對帳乾淨（6,182 列
中 0 個差異）。實際 backfill 途中發現並修正了兩個真實的 bug：抓到的維護頁
回應被永遠重播，而不是重試（§7）；以及區間層級的 quarantine 默默丟掉了與
壞列同一天的其他所有真實列（§8）——TWT49U 的 2887 系列特別股沒有可用的
明細頁，而 TWT49U 共用的除息日常常一次列出幾十支證券。

範圍內：六種資料及其明細的 2020-01-01 → 2026-09-11 backfill，以及對帳報告。

驗收：

- 每種資料的已儲存歷史中，`(feed, code, locator date)` 重複數為零
- 舊系統 `dividend` 在日期、前日收盤、參考價、權值+息值和類型上都已對帳，每個差異都已分類
- 被 quarantine 的事件（如果有）連同理由一起列出

### Step 19-e — ETF 分割與反分割結果資料

狀態：**MERGED** (#27)。依賴：Step 19-c。Step 25 需要它。

在 19-a 發現：ETF 分割和反分割有自己的結果資料——TWSE `rwd/zh/split/TWTCAU`
（`ETF分割(反分割)恢復買賣參考價格`，列出 0050 在 2025-06-18 的分割）、TPEx
`bulletin/etfSplitRslt` 和 `bulletin/etfRvsRslt`。Step 19 的六種資料都沒有
列出這些事件，所以沒有它們，0050 的還原序列就是錯的。在承諾任何東西之前，
仍需驗證它們的欄位、單位和歷史（§2.4）。

2026-09-17 實際驗證（`docs/source_field_audit.md`）：`TWTCAU` 沒有明細頁，也
沒有換股比率欄位，只有一個分割／反分割方向標籤，以及其他資料也有的那兩個
價格，所以它和 TWTB8U 一樣存成 `other`，而不是把價格相除得出股數。兩個 TPEx
資料在整個 2020-01-01 → 2026-09-11 期間都回應 `totalCount: 0`——TPEx 從未
列出過 ETF 分割或反分割——所以它們的 adapter 會把任何列送進 quarantine，
而不是猜測一個沒有樣本的明細頁 schema。

驗收：

- `TWTCAU`／`etfSplitRslt`／`etfRvsRslt` 的欄位、單位和真實歷史以實際抓取驗證，
  而不是假設
- 三種資料都有 2020-01-01 → 2026-09-11 的真實 backfill；每一種的已儲存歷史中，
  `(feed, code, locator date)` 重複數為零
- 無法判斷方向／類型的列附上理由被 quarantine，而不是猜測

整個 Step 19 的範圍外：MOPS 彙總資料的正規化；還原因子（Step 25）。

## Step 20 — 法人買賣、法人買賣彙總、外資持股

依賴：Step 16、Step 17-c。

來源契約（audit §4.3–4.4）：`T86`、`BFI82U`、`MI_QFIIS`；TPEx `3itrade_hedge`、`3itrdsum`、MOPS `t13sa150_otc`。歷史：約 9,800 次請求。

拆成 20-a–d，每部分一個資料集，因為兩個市場的三個資料集超過單一可審閱變更
的大小上限（`CLAUDE.md` §1）。抓取層的工作獨立成一部分，也就是 20-c，必須在
需要它的那個資料集之前合併。

來源發現，2026-09-17（audit §4.3–4.4，「TPEx new-site JSON endpoints」）：
TPEx 以 GET JSON 提供 `3itrade_hedge`、`3itrdsum` 和它自己的外資持股表，分別是
`insti/dailyTrade`、`insti/summary` 和 `insti/qfii`，可回溯到 2020。

2026-09-18 定案（audit §4.4）：**`insti/qfii` 不等同於 MOPS `t13sa150_otc`。**
它少了 119 個 ETF，沒有陸資法令投資上限比率，也沒有發行公司申報日期。它的
尚可投資比率也有 436 列的四捨五入方式與 MOPS 不同。MOPS 仍是 TPEx 外資持股
的來源，所以 POST resource 和每台主機的速率控管器留在這個 step，作為 20-c。

四個部分合起來的驗收：

- 舊系統 `institutional_investors`、`institutional_summary` 和 `foreign_holding` 都已對帳
- 2026-07-10 那個壞掉的 TPEx 彙總 artifact 重新抓取或被 quarantine
- POST resource 序列化後還原，內容完全相同
- process 內每個 MOPS 請求都經過控管器；有測試證明兩個 adapter 同時執行也不會超過主機配額

### Step 20-a — 個股法人買賣

狀態：**MERGED** (#30)。依賴：Step 16、Step 17-c。

範圍內：TWSE `T86`（`twse_t86`）和 TPEx `insti/dailyTrade`
（`tpex_insti_daily_trade`）；選它而不選舊的 `3itrade_hedge` CSV，是因為它提供
相同的 24 個欄位。範圍內還有：Phase 7 資料集的集合式 writer、importer、source
policy 與涵蓋宣告、CLI、2020-01-02 → 2026-09-11 的 backfill，以及與舊系統
`institutional_investors` 的對帳。

Schema 影響：無。migration 新增 catalog、source、release rule 和涵蓋的列。
兩個來源都遵循 `exchange_daily_settled@1`。

驗收：舊系統 `institutional_investors` 已對帳，每個差異都已分類；兩個市場的
涵蓋都是 1,627／1,627 個日期；每個儲存的列都滿足已發布的恆等式；而且在每個
raw artifact 上，TPEx 兩個沒有儲存的合計都等於已儲存群組的總和。

### Step 20-b — 法人買賣市場彙總

狀態：**MERGED** (#31)。依賴：Step 20-a。

範圍內：TWSE `BFI82U`（`twse_bfi82u`）和 TPEx `insti/summary`
（`tpex_insti_summary`，即 `3itrdsum` 的 JSON，四個欄位相同），寫入
`institutional_market_summary_versions`，單位為元。範圍內還有：importer、
source policy 與涵蓋宣告、CLI、2020-01-02 → 2026-09-11 的 backfill，以及與
舊系統 `institutional_summary` 的對帳。

2026-09-18 定案（audit §4.3）：每個發布的列一個版本，以 `(market, institution)`
識別，使用發布的名稱並去掉 TPEx 的版面縮排。兩個市場保留各自的名稱，TPEx 的
兩個小計列照發布的樣子儲存。2026-07-10 那個壞掉的檔案庫檔案，是舊 scraper 在
臨時休市日存下的；重新抓取時，該日期回應空表，並以 `no_data_for_date` 被
quarantine。

Schema 影響：無。migration 新增 catalog、source、release rule 和涵蓋的列。
兩個來源都遵循 `exchange_daily_settled@1`。

驗收：舊系統 `institutional_summary` 已對帳，每個差異都已分類；兩個市場的
涵蓋都是 1,627／1,627 個日期；每個儲存的列都滿足買進 − 賣出 = 淨額，每個儲存
的日期都滿足其市場發布的合計；2026-07-10 的檔案已重新抓取並被 quarantine。

### Step 20-c — 完整描述來源請求，以及每台主機的請求速率控管

狀態：**MERGED** (#32)。依賴：Step 20 內沒有。Step 20-d 需要它。

- **`SourceResource` 成為完整的請求。** MOPS `t13sa150_otc` 是帶表單 body 的 POST，回傳 big5，現有的 `HttpSourceFetcher` 無法表達：它只送 `GET`，而且固定帶 `Accept: application/json`。加上 method、body 和 headers，讓一個 resource 成為一次抓取的完整、可序列化描述——這也正是一個 job 需要的樣子。
- **每台主機的速率控管器，注入到 fetcher 裡。** 四個 v1 PR（#20、#22、#23、#33）都會呼叫 `mopsov.twse.com.tw`，目前各自 sleep，彼此看不到對方。MOPS 在 2026-07-02 封鎖了舊 scraper，而舊系統 23:50 的 XBRL 時間窗已經會延誤到 03:00 的重試（audit §7.2）。每台主機一份配額，在同一個地方強制執行。

驗收：POST resource 序列化後還原，內容完全相同；並有測試證明兩個 adapter
同時執行也不會超過主機配額。

2026-09-19 定案：

- **請求配額。** `mopsov.twse.com.tw` 的間隔是 3 秒，沿用舊 scraper 在 2026-07-02 被 MOPS 封鎖後採用的間隔（`my_stock_project` `scraper/quarterly/fetch_xbrl.py`，`FETCH_INTERVAL_SECONDS`）。送往受控管主機的請求不會同時進行；每個請求都在前一個請求*結束*後至少間隔這麼久才送出，失敗的請求也算。其他主機不受控管：TWSE／TPEx 的 backfill 保留各自的 `min_interval_seconds`，把它們移到控管器上不屬於這個 step。
- **控管器放在哪裡。** 每個 process 一個 `HostRateGovernor`。沒有另外指定控管器的 `HttpSourceFetcher` 都使用它，`RetryingFetcher` 自己建立的 fetcher 也一樣，所以重試也要等主機的間隔。
- **請求內容寫進 provenance。** `raw_artifact_observations.source_uri` 只記 URL，不足以識別一個 POST。resource 不是一般 GET 時，序列化後的請求內容會寫進抓取它的那次 run 的 `ingest_runs.run_metadata`。每一次抓取都會寫，包括以 dependency 抓取的 resource（#32 review）。主 resource 的請求內容另外寫進 manifest 的 `source_scope`，因此也進入設定 fingerprint。一般 GET 不多寫任何東西，現有 import 的 fingerprint 不變。沒有 schema 變更。
- **實際打 MOPS 的檢查。** 透過新的 fetcher 送出一次舊 scraper 查 2026-09-11 的表單 POST，回傳 548,126 bytes 的 big5 HTML，內含 11 欄的外資持股表；第二次請求等了 3.1 秒才送出。用嚴格的 big5 解碼會出現 11 個替代字元，要採用哪種編碼交給 20-d 的 parser 決定。

驗收證據：`docs/step_reports/step-20-c-acceptance-report.md`。

### Step 20-d — 外資持股

狀態：**MERGED** (#33)。依賴：Step 20-c。

TWSE `MI_QFIIS`（`twse_mi_qfiis`）和 MOPS `t13sa150_otc`（`mops_t13sa150_otc`，
POST，cp950 HTML，每個日期約 550 KB）寫入 `foreign_holding_versions`，每個欄位都
有來源。每個 MOPS 請求都經過 20-c 的控管器。範圍內還有：importer、source policy
與涵蓋宣告、CLI、2020-01-02 → 2026-09-11 的 backfill，以及與舊系統
`foreign_holding` 的對帳。

2026-09-19 定案（audit §4.4「Step 20-d findings」）：

- **MOPS 回溯時有生存者偏差。** MOPS 以今天的證券清單重建每一個過去的日期：
  5371、4130、3426、4987（2026-05..08 停止在 TPEx 交易）和 5236（2026-07-15
  轉到 TWSE）在 2020 年起的每個 MOPS 日期都不見了，雖然舊系統 2026 年 2 月存下的
  檔案有它們。舊系統本身也有同樣的偏差：49 支在 2026-02 之前離開 TPEx 的普通股
  完全不在舊系統中。TPEx 自己的 `insti/qfii` 在過去日期仍列出這些證券。
- **依 owner 決定，TPEx 有兩個來源。** `insti/qfii`（`tpex_insti_qfii`，GET JSON）
  作為第二個 TPEx 來源，各自保存歷史、不合併（CLAUDE.md §30），由 migration
  `f2b6d8a4c1e9` 宣告。MOPS 仍是 TPEx 宣告的涵蓋來源（`dataset_expected_coverage`
  每個市場一個來源，而 MOPS 帶有每個欄位）。`insti/qfii` 缺少大部分 ETF，也沒有
  發布陸資法令投資上限比率、異動原因和最近申報日期，這三欄在該來源保持 NULL。
  消費端如何在兩個 TPEx 來源之間選擇，留給 Step 28 或另一份 ADR。
- **異動原因是一組代碼。** 一格可以有多個代碼（TWSE 每個代碼一個連結，以
  `<br>` 分隔；MOPS 把數字連在一起，如 `24`），儲存為遞增、逗號分隔（`2,4`）；
  空白為 NULL。連結指向每月換 URL 的申報頁，不儲存，所以連結改變不是 revision。
- **比率的算法因來源而異。** E 在每個來源都是 trunc(C / A, 2)；D 在 TWSE 和 MOPS
  是 trunc(B / A, 2)，在 `insti/qfii` 是 round(B / A, 2)。B + C 永遠不超過
  floor(A × F)，只有一個已命名的來源異常（`insti/qfii` 2026-04-07 的 6028）。

四個部分合起來的驗收中，本部分負責：舊系統 `foreign_holding` 已對帳。

驗收證據：`docs/step_reports/step-20-d-acceptance-report.md`。

## Step 21 — 融資融券與借券

依賴：Step 16、Step 17-c。

來源契約（audit §4.5）：`MI_MARGN`、`TWT93U`；TPEx `margin_bal`、`margin_sbl`。歷史：約 6,500 次請求。

拆成 21-a 和 21-b，每部分一個資料集：Step 20-a 單一資料集就有 +832 行，兩個資料集
跨兩個市場會超過單一可審閱變更的大小上限（`CLAUDE.md` §1）。

驗收：舊系統 `margin_trading` 和 `margin_sbl` 在張 → 股換算後對帳。

範圍外：市場彙總區塊（`margin_summary`，沒有使用者讀取）。

### Step 21-a — 融資融券

狀態：**IN REVIEW**。依賴：Step 16、Step 17-c、Step 20-d（單位檢查用它的發行股數）。

範圍內：TWSE `marginTrading/MI_MARGN`（`twse_mi_margn`）和 TPEx `margin/balance`
（`tpex_margin_balance`，舊 `margin_bal` 頁面的 JSON，選它而不選 CSV）寫入
`margin_trading_versions`，張換算成股。範圍內還有：importer、source policy 與涵蓋
宣告、CLI `margin-trading`、2020-01-02 → 2026-09-11 的 backfill，以及與舊系統
`margin_trading` 的對帳。兩個來源都遵循 `exchange_daily_settled@1`。

2026-09-19 定案（audit §4.5「Step 21-a findings」）：

- **交易單位不一定是 1,000 股。** TWSE 自己的註解寫著「除境外指數股票型基金及外國
  股票第二上市外，餘交易單位皆為千股」。期間內的例外是 008201 BP上證50（境外 ETF，
  一張 100 股，2020-01-02 → 2022-07-08），由「次一營業日限額對照 Step 20-d 發行股數
  的 25%」這項檢查找到。依 owner 決定，adapter 以明確的例外清單
  `TWSE_LOT_SHARES` 換算（v2），其餘以 1,000 股換算；對帳持續做這項檢查，出現新的
  例外就會失敗，直到補進清單。
- **融資使用率可以超過 100%（storage contract 修正）。** TPEx 公布 00989B 在
  2026-07-14 的資使用率為 103.1%：單日融資買進就超過限額，因為暫停從次一營業日才
  生效。Step 7 的 0–100 上限沒有來源依據；依 owner 決定，migration `b9d1f3a5c7e2`
  改為只要求 ≥ 0，downgrade 在已有超過 100 的列時會在修改前拒絕（§68、§81）。
- **不儲存的欄位。** TPEx 的 資屬證金、券屬證金，以及兩個交易所的狀態註記，沒有契約
  欄位。

驗收證據：`docs/step_reports/step-21-a-acceptance-report.md`。

### Step 21-b — 借券

狀態：**PLANNED**。依賴：Step 21-a。

TWSE `TWT93U` 和 TPEx `margin/sbl`（舊 `margin_sbl` 頁面的 JSON）寫入
`securities_lending_versions`，對帳舊系統 `margin_sbl`。開工時先查 TWT93U 的單位是股
還是張，以及 21-a 找到的交易單位例外是否也適用。

## Step 22 — 月營收

狀態：**PLANNED**。依賴：Step 4 契約、Step 11。

來源契約（audit §4.7）：MOPS `t21sc03` 頁面，`sii`／`otc` × `_0`／`_1` × 月份，從 2020M01 起。歷史：約 330 次請求。營收 ×1,000 換算為元。

Schema 影響：在 `monthly_revenue_versions` 加上可為 null 的已發布比較值。它們在同一個來源列中，而且使用者會讀取：

```text
revenue_last_month, revenue_last_year_month, mom_pct, yoy_pct,
cumulative_revenue, cumulative_revenue_last_year, cumulative_yoy_pct, note
```

標準衍生的 `monthly_revenue_growth:v1` 移出 v1。

復原的發布日期（audit §7.4）：2020M01-2026M01 從舊系統的 `market.csv` 讀取，`revswarm` 已經把日期寫回——128,063 列中有 114,910 列（89.7%）帶有真實的公告日期。CSV 是介面；`revswarm.db` 不是這個 PR 的執行期依賴。

CSV 只保留日期，所以日期是 10 日的列，無法與保留法定預設值的列區分。由此得出的規則：

```text
publish_time != the 10th of the next month  ->  press_report_bound at end of that day
publish_time == the 10th of the next month  ->  release_rule
```

這幾乎沒有成本，而且永遠不會產生前視偏差。有 40,188 列落在 10 日；其中 36,492 列的 10 日是營業日，release rule 解析到同一天，所以時間戳記相同。只有 10 日落在週末的 3,696 列（2.9%）會比復原日期晚 1-2 天解析，是偏晚而不是偏早。

如果之後需要逐列的 provenance——`engine`、`verified`、`raw_title`、`url`——升級方式是從 `revswarm.db` 匯出一個 provenance 欄位與日期放在一起，而不是在 ingest 時讀取資料庫。

舊系統首次看到的匯入（audit §7.1）：2026M02 起，把舊系統 `market.csv` 的列匯入為首次抓取值的 `legacy_archive` 觀察，並以其 `publish_time` 日期作為 `legacy_capture_bound` 證據。官方重新抓取的結果不同時，成為之後的 revision，證據是 Data Center 自己的抓取時間。2026M02 之前合成的 `publish_time` 值不作為證據匯入。

驗收：

- 舊系統 `monthly_revenue` 已對帳
- `_1` 頁面的 KY 發行公司以新涵蓋出現
- 兩次抓取之間的更正會產生 revision
- 已發布的比較值完全照發布的樣子儲存，絕不與我們自己的序列對帳；2026M06/M07 這一對（1,846 家公司中 11 家不一致）是回歸 fixture（audit §7.3）
- 2026M02 起，每個首次看到的列在 Market PIT 下的解析時間，不早於其舊系統 22:45 執行的結束時間；重新抓取顯示被更正的列，在更正被抓到之前解析為首次抓取的值
- 2020M01-2026M01 期間，`publish_time` 不是次月 10 日的列，在該日結束時解析；落在 10 日的列以 release rule 解析
- 這段期間沒有任何一列會比 release rule 所定的時間更早解析
- `revswarm` 已公告營收的交叉核對在匯入時執行，並記錄一致率；低於 2026-09-15 量測的 99.10% 時，匯入失敗

範圍外：復原 2026M02 之前首次發布的值。

## Step 23 — 財務報表（iXBRL）

狀態：**PLANNED**。依賴：Step 5 契約、Step 11。

來源契約（audit §4.8）：MOPS `t164sb01`，每個 (security, year, quarter, report type) 一份文件，從 2020Q1 起。

**金融業發行公司不在範圍內**，與舊系統相同。v1 是舊系統的範圍（§1.1），排除它們可以讓金融業的會計科目分類和其不同的法定期限不進入 v1。它們的每日價格、月營收和其他所有領域都不受影響；只有它們的財務報表不 ingest。§26.3 記錄了這項排除。

歷史來源（owner 決定；audit §7.1）：

- 2020Q1–2025Q3：把舊系統的文件匯入為 `legacy_archive` 版本。它們是 2026 年 2 月重新抓取的，所以內容等同今天重新抓取的結果，而它們檔名上的日期是合成的期限，不帶任何抓取證據——這些以 release rule 解析。抽樣以官方重新抓取比較；不一致率超過 PR 中設定的門檻時，匯入失敗並改為完整重新抓取。
- 2025Q4 起：把舊系統的文件匯入為 `legacy_archive` 版本。
  - 每日工作在時間窗結束（03/31、05/15、08/15、11/15）前抓到的文件，以執行日期和檔案 mtime 取得 `legacy_capture_bound` 證據。
  - 日期來自之後 backfill 執行（2026-08-01、2026-08-17）的文件，不取得抓取證據，以 release rule 解析。
- 官方重新抓取的結果不同時，成為之後的 revision（修正申報），帶有 Data Center 自己的抓取證據。

這取代了完整的 45,000 次請求重新抓取；以 2026-07-02 MOPS 封鎖後被迫採用的 3 秒間隔，那需要約 38 小時，而且有再次被封鎖的風險。

驗收：

- 舊系統 `*_xbrl` 和 `quarterly_reports_xbrl` 的值透過會計科目代碼 ↔ concept QName 對帳
- 報表類別（合併或個別）被保留
- Step 5 的 EPS 契約成立
- 檔案庫與官方的抽樣比較已執行，不一致率記錄在 audit
- 2025Q4 起，每日工作的抓取在 Market PIT 下的解析時間不早於其舊系統抓取界限
- 匯入後，沒有任何金融業發行公司有財務報表版本

範圍外：金融業發行公司；復原 2025Q4 之前修正前的原始申報。

## Step 24 — TDCC 股權分散

狀態：**PLANNED**。依賴：Step 6 契約、Step 16。

來源契約（audit §4.9）：

- OpenData `id=1-5` 每週，從第一次前向抓取起
- 第一次前向抓取之前用 `legacy_archive` artifact，來自單一合併檔案庫 `my_stock_project/data/raw/shareholding`：426 個檔案，從 2019-06-28 到 2026-09-11 共 **375 週**，其中 348 週落在 v1 期間內。2026-09-15 從三個目錄合併而成（audit §4.9）；`shareholding.bak` 是舊的過濾副本，不是來源。
- 每個檔案都帶有相同的六欄 OpenData header，所以一個 parser 就能處理全部
- 入口網站的個股查詢只用於其約一年時間窗內的修補

新的執行期依賴：7z 讀取器（`py7zr`），因為 2021 年起存成 `.7z`。Step 24 把它加進 `pyproject.toml`；目前沒有宣告。

importer 以資料日期欄位為 key，絕不用檔名：`20200619.CSV` 和 `20200619.zip` 都包含 20200612 的資料，相信檔名會捏造一週並丟掉真正的那一週。它還必須處理 `20190628.zip` 中斜線格式的日期、十個雙 BOM 檔案、大小寫混用的副檔名，以及有重複副本的 51 個內容日期（每一對都完全一致，所以保留任一個都可以）。

驗收：

- 375 週全部匯入，每週以其內容日期為 key
- 檔名與內容日期不一致的檔案被拒絕，並指出這個事實，而不是默默改名
- 剩下每個 10 天以上的間隔都解析為農曆新年休市；任何其他缺口都讓匯入失敗
- 2026-07-09 匯入為完整的 4,003 支證券檔案，而不是 1,849 支證券的重建版本
- 不從 `shareholding.bak` 讀取任何東西；它 2023-09-15 之後的 155 個檔案被過濾到約 1,767 支證券
- 舊系統 `shareholding` 在它有的 340 週上對帳

---

# 24. 規劃中的 PR——衍生資料

## Step 25 — 還原價格

狀態：**PLANNED**。依賴：Step 16、Step 17-c、Steps 19-a–e。

方法：§18 的參考價比率。逐證券計算往回累積的因子。原始 OHLC 不動。

驗收：

- 每個超過有記載門檻的每日收盤到收盤缺口（2020 年起），都分類為可由交易所結果資料事件解釋、可由其他有記載的市場事件解釋，或無法解釋；無法解釋的清單要經過審閱
- 事件日的連續性
- 在請求的 PIT context 中，事件的證據可見之前，任何事件都不會影響序列

範圍外：純價格（不含現金股利）序列；2020 年之前的歷史。

## Step 26 — 標準衍生 v1（移植舊系統計算程式）

狀態：**PLANNED**。依賴：Steps 17-c–25，依各指標的需要。

定義，每個都從舊系統的計算程式移植，並與舊系統的表對帳：

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

實體化遵循 §17：只有滾動的 as-of 序列。

驗收：與舊系統的表對帳。因為 PIT 正確的輸入而產生的刻意差異要列出並解釋。

範圍外：綜合壓力分數（下游）；指標的還原價格版本（之後的 derivation version）。

---

## Step 34 — 穩定的 Publication-Evidence Hash

狀態：**PLANNED**。依賴：無。

問題，由 Step 16 的 review 發現。`publication_evidence_hash` 的計算方式是 `to_jsonb(NEW)` 減去一份固定的排除清單，所以它包含每個證據目標欄位，包括對這個資料集是 NULL 的那些。因此 Step 16 新增第十七個目標時，**每個資料集**算出的 hash 都改變了；Step 8 新增 `market_index_metadata_version_id` 時也發生同樣的事。

後果不是資料損毀——證據只能附加，解析時依品質和 `recorded_at` 排序，而不是依 hash。損失的是去重：這樣的 migration 之後，重新 ingest 相同的證據，無法再透過 `ON CONFLICT (publication_evidence_hash)` 對上已儲存的列，而是附加一筆重複的。

可能的修法：對 `jsonb_strip_nulls(to_jsonb(NEW) - ...)` 做 hash，讓新增的可為 null 目標不會移動不相關資料集的 hash。這會一次改變每個領域的 hash 函數，需要自己的回歸測試集，所以它是獨立的 step，而不是 Step 16 裡的修補。

範圍外：改寫已儲存的 hash。既有的列保留原本的 hash；這個修正穩定的是之後的計算。

## Step 33 — 發行公司股利宣告

狀態：**PLANNED**。依賴：Step 19。

原因：盈餘／法定盈餘公積／資本公積的拆分不存在於任何交易所結果資料中。MOPS 是它唯一的公開來源。

來源契約（audit §4.13，2026-09-15 實際驗證）：

```text
primary   POST mopsov.twse.com.tw/server-java/t05st09sub
          encodeURIComponent=1&step=1&firstin=1&off=1
          TYPEK={sii|otc}  YEAR={ROC year}  qryType=1
          one whole-market big5 HTML table per request

cross-check  openapi.twse.com.tw/v1/opendata/t187ap45_L      JSON, 股利年度 114-115
             www.tpex.org.tw/openapi/v1/mopsfin_t187ap39_O   JSON, frozen at 出表日期 1100804
```

`TYPEK` 有四個值——`sii` 上市、`otc` 上櫃、`rotc` 興櫃、`pub` 不在任何市場交易的公開發行公司。四個集合互不重疊，而成員資格依公司*查詢當下*的狀態，而不是股利年度時的狀態：`otc`／民國 109 回傳的 792 家公司中，有 58 家到 2021 年之後才在 TPEx 掛牌。所以 `sii` + `otc` 涵蓋整個 v1 範圍，包括其成員掛牌前的年度，而 `rotc`／`pub` 只會加入範圍之外的公司（audit §4.13）。

歷史：2 個市場 × 民國 107-115 年 = 18 次請求，之後每天 2 次。`t05st09sub` 在 2026-07-02 封鎖舊 scraper 的那台主機上，所以適用 3 秒間隔；18 次請求不到一分鐘。

TPEx OpenAPI 資料集在民國 110 年 header 改變時停止更新。這是 TPEx 的轉載資料過時，不是來源被撤除：MOPS 正常提供 110-115 年的 `TYPEK=otc`。這個 PR 對兩個市場都使用 MOPS，兩個 OpenAPI 資料只作為重疊年度的獨立交叉核對。

必須處理的 header 版本：

```text
ROC <= 109   股東配發內容底下 6 個子欄位（每列 19 格）
             法定盈餘公積和資本公積合併成「一個」數字發布，
             現金股利和股票股利都一樣
ROC >= 110   8 個子欄位（每列 21 格），兩種公積分開
```

因此 `盈餘轉增資配股` 每一年都有。只有公積的拆分在民國 110 年之前無法取得。那些年度的列把法定盈餘公積部分設為 NULL，並把合併的數字記在資本公積欄位，附上明確的 `reserves_combined` 旗標。任何 PR 都不可以把 110 年之前的資本公積數字當成純公積數字呈現。

儲存：新的 `dividend_declaration_versions` 表。`corporate_action_versions` 不動，其拆分欄位保持 NULL（§2.3）；這個領域以自己的序列發布。

Identity：`(security, dividend_year, dividend_period_text, sequence)`。缺少股利所屬期間的 TPEx OpenAPI 列另外需要董事會決議日期，而仍有兩個群組衝突並被 quarantine（audit §4.13）。

時間模型：

```text
information_as_of  董事會決議（擬議）股利分派日
published_at       前向抓取時用 capture_bound；backfill 時一次匯入的
                   年度，release rule 就是抓取日期，因為沒有任何資料
                   發布逐列的發布時刻
```

驗收：

- 兩種 header 版本都能解析；同一次匯入中 19 格和 21 格的列都能正確還原
- 每個民國 ≤ 109 年的列，以及每個取自凍結 TPEx OpenAPI 資料的列，都設定 `reserves_combined` 旗標
- 只請求 `TYPEK=sii` 和 `TYPEK=otc`；`rotc` 和 `pub` 在 adapter 邊界被拒絕（§1.1）
- 每次 (market, year) 匯入內 identity 唯一；任何重複都送進 quarantine
- 有新掛牌之後重新匯入過去的年度，會把新出現的列加為該期間的新紀錄；不可以把它們與既有列比較成更正，較小的列集合也不可以被解讀為 retraction
- 重新抓取時 決議進度 改變，會在同一個 identity 下產生新版本，而不是原地更新
- 重疊的年度與 TWSE OpenAPI 資料（114-115）和凍結的 TPEx 資料（107-110）對帳；差異要列出
- 這個 PR 不改變 `corporate_action_versions` 和 `security_events`
- API 把這個序列標示為宣告資料，與已執行事件之間沒有經驗證的連結

範圍外：把宣告連結到已執行的除息事件；§1.1 從 v1 範圍排除的 `rotc` 和 `pub` 市場；OpenAPI 資料中只有 TPEx 有的董監酬勞和員工紅利欄位；以 `qryType=2`（股利所屬年度）作為第二個維度。

---

# 25. 規劃中的 PR——維運、API、切換

## Step 27 — 排程的前向抓取

狀態：**PLANNED**。依賴：Steps 16–24。

每日、每週、每月和每季的工作執行 adapter。它們包括重試、以日曆為基礎的缺漏資料警示，以及透過重新抓取近期期間來偵測更正。每個資料集的 revision 比率報告，量化 Step 15 描述的 backfill 限制。

抓取執行 §3.1 的流程：從 Step 16 讀取預期涵蓋、與已儲存的內容對帳、發出帶有 purpose 的 job、抓取、ingest。job 清單在記憶體中，fetcher 是本機的 HTTP fetcher；這個 PR 不建 queue，也不拆獨立服務。重點是由排程器決定要抓什麼、為什麼抓，而不是由任何 adapter 決定。

### 分派迴圈由狀態驅動，而不是固定時刻表

2026-09-16 依舊系統失敗的方式決定。舊系統執行一次排程的抓取；失敗的執行要到隔天
才有下一次執行，而缺口要靠人檢查才會發現。當時的因應是第二個計時器——23:30 加上
03:00 的重試——這是用固定排程去猜需要幾次嘗試。

Step 27 把它反過來。每小時，對每個宣告的 `(dataset, market)`：

```text
read the declaration  -> what should exist
reconcile             -> what is missing now
dispatch              -> one job per still-missing period, and nothing else
```

工作量會自己縮小。如果 20:00 有六個資料集缺漏，就發出六個 job；21:00 還有三個缺漏，
就發出三個；到 23:00 也許只剩一個。失敗的嘗試不需要自己的重試計時器，因為下一小時的
對帳自然會看到它仍然缺漏。已經完整的期間不會分派任何東西。

升級期限結束迴圈：到隔天早上宣告的時刻仍然缺漏的期間，會發出通知，指名資料集、期間
和最後的 reason code，而不是永遠默默重試。

除了迴圈本身，還需要四件事：

1. **缺漏不是唯一的觸發條件。** 一個期間可能存在但錯誤。2026-03-27 舊系統的檔案庫在
   14:10 寫入，那是零股交易確定之前，由一次在前一晚失敗後往前補抓的執行寫入；之後
   `skip if exists` 把那個不完整的版本凍結住，Step 17-c 對帳中 1,062 列的差異就是
   結果。所以迴圈也會重新抓取最近一段期間。這在這裡不需要成本，而且能修正那一整類
   錯誤：相同的內容會去重，同時抓取仍然可稽核，改變的內容則成為新的 revision。
   舊系統做不到這點，因為跳過既有檔案讓重新抓取變成空操作。
2. **每個資料集的最早可取得時間，這不是 release rule。**
   `exchange_daily_settled@1` 說的是市場何時能*知道*一個交易日——D+1 的 03:00，一個
   保守的可見界限。排程需要的是資料何時能被*抓取*，對每日價格來說是 D 當天零股交易
   確定之後。在那之前分派，是向來源要求它還沒發布的東西，而 TPEx 的回應和休市日完全
   一樣。
3. **對永久不存在的期間做 backoff。** TPEx 在 2007-07-02 之前什麼都不提供，也不說明
   原因（audit §4.1）。沒有嘗試次數和最後嘗試時間，針對宣告錯誤的時間窗每小時執行的
   迴圈，就會變成每小時轟炸來源。`import_checkpoints` 保存了一部分；仍需要記錄某個
   期間第一次被發現缺漏的時間。
4. **排程抓取和 gap-fill 抓取宣稱的證據不同，但價值多大取決於領域。** 在 D 的排程
   執行是真正的首次看到，可以宣稱 `capture_bound`；三年後的 backfill 不行。這在發布
   時刻逐列不同的地方很重要，在不是這樣的地方幾乎不重要：

   - **交易所發布的資料——每日價格、指數、法人買賣、融資融券——由交易所依固定
     排程發布**，檔案中每一列的時刻都相同。抓取界限只會把 D+1 03:00 收緊到 D 的
     約 14:30，而且每一列都一樣，所以正確的投資是發布一個引用實際發布時間的新版
     release rule，而不是去搶著觀察它。所有歷史列一次受益，而且不需要抓取任何東西。
   - **發行公司申報的資料——月營收和財務報表——由各發行公司依自己的時程**，在
     法定期限內發布。一家公司 3 日申報，另一家 10 日；規則只能說「10 日之前」，而
     實際日期沒有觀察過就無從得知。這是首次看到的證據無可取代的領域，也是為什麼
     §32 恰好只為這兩個——2026M02 起的月營收和 2025Q4 起的 XBRL——記錄真實的
     首次看到資料，其他都沒有。

   舊 scraper 仍在為兩個發行公司申報領域執行，所以在建 Steps 22 和 23 的期間，這些
   首次看到的紀錄持續累積。期間沒有遺失任何東西，也沒有理由把任何領域提前到規劃
   順序之前。值得點明這個依賴：在這個 repository 自己抓取這些領域之前，那些檔案庫
   仍是首次看到證據的唯一來源。

   對 `daily_price` 來說，執行前向抓取的理由是新鮮度和更正偵測，不是證據品質——
   而更正偵測靠的是重新抓取，不是提早抓取。提早抓取正是舊系統凍結了不完整的
   2026-03-27 的原因。

驗收：

- 兩週無人值守的執行，涵蓋完整，失敗可續跑
- 每小時的迴圈只分派仍缺漏的期間，而且一個晚上內分派的集合會逐輪縮小
- 到升級時刻仍缺漏的期間會發出通知，帶有資料集、期間和最後的 reason code
- 對同一個缺漏期間，排程執行和 gap-fill 執行產生不同的證據，而 gap-fill 的列以其 release rule 解析
- 對已發布值有改變的期間做回溯重新抓取，會產生 revision；值沒有改變的則不產生
- 在任何抓取發生之前，就可以列出待處理的 job 集合

## Step 28 — 公開 REST API v1

狀態：**PLANNED**。依賴：Step 15 的決定、各資料 PR。

提供正確的 Data Center 語意，而不暴露資料表。端點涵蓋舊系統使用者發出的查詢：每日面板、指數、估值、籌碼資料、月營收、財務 facts 與摘要、TDCC、公司行動、還原價格和衍生指標。每個回應都帶有其 PIT context 和 provenance。沒有來源的欄位被省略，或明確標示為無法取得。

## Step 29 — Python SDK 與下游整合契約

狀態：**PLANNED**。下游 repository 不需要 PostgreSQL 帳密。

## Step 30 — 維運與可觀測性

狀態：**PLANNED**。涵蓋 ingest 進度、quarantine、涵蓋與對帳狀態，以及 PIT／資料庫延遲。

## Step 31 — 完整的正確性 CI 關卡

狀態：**PLANNED**。CI 涵蓋 PostgreSQL 18、Alembic、PIT、publication evidence、來源能力、raw-first 重新啟動、公司行動 identity 與 revision、還原價格、衍生資料集，以及 API。公司行動的 CI 包括結果資料重複掃描、更正回歸測試，以及公告型資料拒絕 fixture。

## Step 32 — `my_stock_project` 切換與 v1 發布

狀態：**PLANNED**。依賴：Steps 25–31。

`my_stock_project` 使用 API／SDK，並停止維護一個相互競爭的權威資料庫。最終對帳比較使用者讀取的每張舊系統資料表與 API 結果，每個差異都已分類。

---

# 26. 不在 v1 的項目

原本三種不同的東西被放在同一份清單裡。這裡把它們分開，因為它們需要不同的
處理：第一組永遠不應再被規劃，第二組等待明確的觸發條件，只有第三組可能成為
之後的版本。

## 26.1 無法取得——沒有來源存在

沒有今天不存在的來源，未來任何版本都無法交付這些。提出其中任何一項的 PR，
必須先提出端點和欄位標籤，否則會被拒絕。

| 項目 | 證據 |
|---|---|
| 公司行動的公告日、基準日和發放日 | 檢查過的資料都沒有發布，包括公告型資料（audit §4.10、§4.13） |
| 把股利宣告連結到其已執行的除息事件 | 兩個宣告資料都沒有除息日或 locator；MOPS 頁面自己就這麼說（audit §4.13） |
| 民國 110 年之前法定盈餘公積與資本公積的拆分 | 在民國 110 年 header 改變之前，MOPS 把兩種公積合併成一個數字發布（audit §4.13） |
| 指數成交金額；TAIEX 以外任何指數的 OHLC | 不在 `MI_INDEX` 或 `indexSummary` 中；沒有找到 TPEx 對應 `MI_5MINS_HIST` 的資料（audit §4.2） |
| 證券名稱和產業的歷史變更 | 沒有找到官方的歷史來源；`t187ap03_*` 是當下快照 |
| 委託簿深度（`bid_snapshot`、`ask_snapshot`） | 每日檔案只發布一檔，已存在 `last_bid_*`／`last_ask_*`（audit §4.1） |
| 首次發布的*數值*：2026M02 之前的月營收、2025Q4 之前的 iXBRL、前向抓取之前的交易所每日資料 | MOPS 提供最新更正後的值，而沒有任何抓取記錄下更早的值（audit §7.1）。月營收方面，`revswarm` 的標題數字四捨五入到 0.01 億——足以偵測到曾經發生更正，但不足以還原原本以千元計的數字（audit §7.4）。 |
| 2020M01-2026M01 月營收中沒有經驗證報導的 10.3% 的發布*日期* | `revswarm` 找不到通過其防護的帶日期報導。這些退回使用 release rule。重新執行它之後的引擎可能縮小這個缺口，所以這是涵蓋上的限制，而不是硬性限制。 |
| 入口網站時間窗之前的 TDCC 歷史，如果檔案庫遺失 | 入口網站提供約 51 週，OpenData 只提供最新一週。檔案庫中的 375 週無法從任何官方端點重新抓取，所以檔案庫是唯一的副本（audit §4.9）。這是保存上的限制，不是缺少資料集：v1 需要的每一週都有。 |

## 26.2 等待明確的觸發條件

可以取得，但在觸發條件成立之前刻意不建。沒有觸發條件，就沒有 PR。

| 項目 | 觸發條件 |
|---|---|
| 快取抽象層、Redis 後端 | Step 30 量測的 API／資料庫延遲證明不足。如果日後加入，適用 Invariants A–C。 |
| 股票標籤 | 出現帶有生效日期的官方來源。第三方的當下快照不算。 |
| XBRL codebook、信用交易市場彙總 | 有使用者讀取。目前兩者都沒有。 |
| 純價格還原序列；指標的還原價格版本 | 有使用者需要。重現舊系統使用者的需求時用不到。 |

## 26.3 可以取得，依決定不在 v1

這些有來源，也可以建。它們不在 v1，是為了讓 v1 維持舊系統的範圍。之後的版本
只會從這一組取材。

| 項目 | 需要什麼 |
|---|---|
| 金融業財務報表 | 金融業的會計科目分類和其不同的法定期限（audit §7.1）。MOPS 目前提供這些申報。 |
| 2020 年之前的歷史 | 交易所資料接受更早的日期區間，可以重新抓取。TDCC 不行，除了 `TDCC` 檔案庫保有、回溯到 2019-06-28 的 27 週；更早的都沒了。 |
| `rotc` 和 `pub` 市場 | MOPS 提供全部四個 `TYPEK` 值。是 §1.1 排除的，不是因為無法取得。 |
| 以 `qryType=2`（股利所屬年度）作為股利宣告的第二個維度 | 每個市場每年多一次對 `t05st09sub` 的請求（audit §4.13）。 |
| 只有 TPEx 有的宣告欄位：董監酬勞、員工紅利 | 存在於凍結的 TPEx OpenAPI 資料中；TWSE 沒有對應，所以序列只會有一邊。 |

---

# 27. 跨 PR 驗收規則

## 27.1 時間正確性

絕不可捏造歷史的 `published_at`、`ingested_at` 或知識。由規則推導的界限只以 Step 15 定義的方式存在。

## 27.2 Raw-first ingestion

```text
fetch → durable raw artifact → checkpoint → parse → normalize → canonical write
```

artifact 來源是 `official_fetch`，除非 §14 允許 `legacy_archive`。

## 27.3 失敗分類

無效或模糊的來源資料、未知的 header 版本，以及領域驗證失敗，都送進 quarantine。原始 bytes 被抓取之後，營運上的失敗保持可續跑。

## 27.4 跨來源對帳

最終的事實必須能從已儲存的來源歷史重現，而且與匯入順序無關。

## 27.5 Migration 安全

downgrade 必須能安全地表示已儲存的歷史，否則在修改之前明確失敗。

## 27.6 公司行動／價格規則

原始價格是來源事實。連續性屬於還原因子和還原序列。

## 27.7 公司行動 identity 關卡

真實來源的 adapter 在呼叫 `register_corporate_action_event()` 之前，必須證明：

```text
1. 來源是交易所結果資料（Invariant G(2)）；公告型資料被拒絕；
2. source_event_key 是 "<feed>:<locator date>"，不包含任何 revision 內容；
3. 完整歷史的重複掃描找到零個重複的 key；
4. 同一個 locator 條件改變 -> 同一事件，新的 revision；
5. 不同的已執行事件 -> 不同的 key；
6. 被移除的列 -> retraction，而不是刪除。
```

只要有任何一項無法確立，就在註冊正規化事件之前停下來。

## 27.8 來源欄位規則

每個儲存或承諾的欄位都滿足 §2.4。每個 adapter PR 都要更新 `docs/source_field_audit.md`。

## 27.9 舊系統對帳

每個 adapter 和衍生資料 PR，都要與舊系統 `stock_db` 在 2020-01-02 → 2026-09-11 期間對帳，並分類每個差異。與舊系統資料一致是證據，不是目標：PIT 正確的差異只要有解釋，就可以接受。

## 27.10 範圍紀律

每個 PR 都要說明它刻意不做什麼。不要吸收不相關的重構或最佳化。

## 27.11 審閱／合併證據

合併前，視情況提供：

```text
focused regression suite
full test suite
Ruff/lint
git diff --check
Alembic check
migration round-trip or guarded downgrade test
opt-in live-source verification
legacy reconciliation report
acceptance report (docs/step_reports/step-<N>-acceptance-report.md)
```

---

# 28. 建議的原始碼結構

```text
stock-data-center/
├── README.md
├── ROADMAP.md
├── CLAUDE.md
├── pyproject.toml
├── docker-compose.yml
├── alembic.ini
├── migrations/
├── data/
│   └── raw/                    只作為以內容定址的 artifact 儲存區
│       └── <ab>/<sha256>       由 LocalRawArtifactStore 寫入；不放來源
│                               檔案庫，也沒有 processed/ 暫存層
├── docs/
│   ├── source_field_audit.md
│   ├── data_domain_inventory.md
│   ├── pit_semantics.md
│   ├── schema.md
│   ├── real_source_ingestion.md
│   ├── derived_data.md
│   ├── phase_reports/
│   ├── step_reports/
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

# 29. v1 的完成定義

第 1 版在以下條件都成立時完成：

- PostgreSQL 18 是唯一的權威資料儲存。
- §16 中標為 v1 的每個領域都涵蓋 2020-01-02 到切換為止。資料來自官方端點，但早於前向抓取的 TDCC 週資料除外，那些來自兩個有記載的檔案庫（§14）。
- 每個領域都有舊系統對帳報告，每個差異都已分類。
- 前向抓取依交易日曆無人值守地執行。
- 在核准的 Step 15 政策下，Market PIT 可用於歷史資料。如果 owner 拒絕由規則推導的證據，API 要記載歷史資料只有 System PIT。
- System PIT 精確重建實際的 ingestion。
- 業務 revision 和 publication-evidence revision 是分開的。
- 複雜 aggregate 在併發下安全，並受 seal 保護。
- 支援 XBRL 完整的 context identity。
- 公司行動只來自通過 identity 關卡的交易所結果資料。公告型資料保持未正規化。
- 還原價格使用參考價慣例，並有經審閱的不連續報告。
- 標準衍生 v1 指標重現舊系統的計算程式，PIT 造成的差異都有解釋。
- 除非有經驗證的來源欄位填入，否則任何 API 欄位都不會以資料的形式呈現。
- 下游 ML repo 只使用 API／SDK。不需要 Redis。

---

# 30. 核心設計原則

1. 正確的歷史可見性優先於方便。
2. PostgreSQL 是事實；任何快取都是可丟棄的最佳化。
3. 市場時間和 Data Center 知識時間是兩個分開的時鐘。
4. System PIT 代表實際、完整的 ingestion 歷史。
5. 業務 revision 和 publication evidence 是分開的。
6. 複雜 aggregate 只有在 seal 後才可見，並在併發下保持正確。
7. 除非有明確的對帳政策，否則各來源保持分開。
8. Data Center 可以擁有確定性、跨 repository 的標準衍生資料集。
9. 每個標準衍生資料集都有 derivation version 和 PIT 安全的 lineage。
10. `computed_at` 是推導的 provenance，不是市場發布時間。
11. 特定模型的特徵留在 ML repository。
12. 下游系統絕不重新實作 PIT 規則。
13. 真實資料的匯入是 raw-first、明確指定來源、冪等，並以對帳驅動。
14. 歷史 backfill 絕不捏造發布時間或 System PIT 歷史。由規則推導的界限是有標示的證據，絕不是默默的預設值。
15. 絕不改寫官方原始價格來掩蓋不連續。
16. 公司行動的 identity 必須在更正後仍然成立。公告型資料沒有這種 identity；交易所結果資料透過實際執行的事件日期或 locator 帶有它。
17. 當下快照中的唯一性是要檢驗的證據，不是 identity 的證明。
18. 如果無法取得穩定的來源 identity，就 fail closed，而不是製造一個合成事件。
19. 沒有指名來源欄位，就不儲存也不承諾任何欄位：設計依循來源實際發布的內容。
20. v1 是以 PIT 重建的舊系統範圍，不是所有可想像欄位的超集合。
21. 先量測再最佳化：延遲量測之前不加快取。
