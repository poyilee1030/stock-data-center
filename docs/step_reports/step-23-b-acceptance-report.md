# Step 23-b 驗收報告

狀態：IN REVIEW (#40)

範圍：財務報表的第二部分，兩個 adapter 與匯入路徑。這個 step 新增：

- migration `a3f5d9c17e48`
- 官方 adapter `MOPSFinancialFilingAdapter`（來源 `mops_t164sb01`，`REPORT_ID`
  C→A fallback、raw-first、每台主機 3 秒間隔）
- 檔案庫 adapter `LegacyFinancialFilingArchiveAdapter`（同一個來源，
  `legacy_archive` 來源位元組）
- importer `FinancialFilingImporter` 與 `FinancialFilingArchiveImporter`
- CLI `financial-filing`、`financial-filing-archive`
- parser 增加 statement section（23-a 的 `ParsedFact` 多一個 `statement`）

全量 backfill、抽樣關卡與舊系統對帳是 23-c。

## 存哪些事實

**與舊資料庫相同的範圍**（owner 決策，2026-09-21）。舊系統 `stock_db` 存的是
`balance_sheet_xbrl`、`income_statement_xbrl`、`cash_flow_xbrl` 三張表；本 step 存的
就是這三張，由文件自己的錨點界定：`<div id="BalanceSheet">`、
`<div id="StatementOfComprehensiveIncome">`、`<div id="StatementsOfCashFlows">`，
各後接恰好一張 `<table>`。權益變動表、附註、附表與 `escape="true"` 敘述區塊在
manifest 裡計數、不存；要不要存留到 ROADMAP 最後再決定。

2026-09-21 對全部 45,324 份檔案庫文件實測（audit §4.8）：每一份都印出這三個錨點
各一次；v1 宇宙內 42,750 份文件的三張表共 **16,180,359** 筆
`ix:nonFraction`，**0 筆沒有會計科目代碼**。

## Schema 影響

`financial_facts` 新增 `statement` 與 `account_code`；
`financial_filing_versions` 新增 `report_category`。三者皆可為 null，
`uq_financial_fact_identity` 加上 `statement` 並使用 `NULLS NOT DISTINCT`，
所以 Phase 5 沒有 statement 的事實去重行為完全不變。三個欄位都進
sealed `business_content_hash`，payload 的 `ORDER BY` 也加上 statement，
讓排序成為全序（CLAUDE.md §24）。來源 `mops_t164sb01` 在同一支 migration 宣告，
只接受 `official`、不宣告 release rule。downgrade 在已存有 statement 事實、
report category，或這個來源已有版本／ingest run／manifest 時，會在修改前拒絕（§81）。

**為什麼身分要加 statement。** Step 5 的身分是 filing + QName + context + unit，
真實文件裝不下：`ifrs-full:CashAndCashEquivalents` 同時是資產負債表的 `1100` 和
現金流量表的 `E00210`，同一個時點、同一個單位、同一個數字。全檔案庫 171,000 組、
每份文件正好四組；本 step 匯入的 1,732 份真實文件量到的正是 **6,928 = 1,732 × 4**。
加上 statement 之後是 **0**。會計科目代碼不需要進身分：QName 本身已經分得開同一張
表、同一個 context、同一個單位的兩列 `ProfitLossBeforeTax`（`A00010` 是
`ifrs-full`、`A10000` 是 `tifrs-scf`）。

**沒有用舊系統的 codebook 當範圍。** 只取 `xbrl_codebook` 那 1,748 個代碼也能把碰撞
清成 0，但會丟掉 332 份文件裡 1,040 列真實的現金流量表小計
（`AA0000`、`AB0000`、`AC0100`–`AC0500`）。所以界定用錨點，不用代碼清單，
ROADMAP §16「codebook 不在 v1」維持不變。

## PIT 影響

沒有新證據。`mops_t164sb01` 只接受 `official`，也不宣告 release rule，所以每個版本
都記 `unknown`：只有 System PIT 看得到，Market PIT 看不到。這是偏晚、不是偏早。
證據由 23-c 附加。

## 規模

`src/` +1,187／−31 行，超過約 800 行的拆分門檻（CLAUDE.md §1）。接縫在這裡不可再分：
fact identity 的 migration、產生這些欄位的 adapter、以及寫入它們的 importer 必須
同時落地，否則合併進去的會是半個契約。新增檔案中約四成是說明性 docstring。

## 驗收

驗收在真實資料上跑：2025Q1 全季 1,814 份檔案庫文件，匯入 `stockdc_backfill`。

```text
1,814 份文件
1,732 存入（601,564 筆事實），0 失敗
   36 擋掉：金融業發行公司
   46 擋掉：非 sii／otc（興櫃、公開發行、非公開發行）
```

| 驗收標準 | 結果 | 證據 |
| --- | --- | --- |
| 報表類別（合併或個別）被保留 | **PASS** | `report_category`：合併 1,578、個別 154，合計 1,732 |
| Step 5 的 EPS 契約成立 | **PASS** | 1,732 份文件各有一筆 `basic_eps`，period_basis 全是 `quarter`（Q1 只有單季當期 context）；0 份沒有 summary；1101 2025Q1 = 0.07，單位 `iso4217:TWD/xbrli:shares` |
| 匯入後，沒有任何金融業發行公司有財務報表版本 | **PASS** | 39 家金融業代號在 `financial_filing_versions` 的版本數 = **0**；36 份在邊界擋掉並記入 `import_quarantine`（`financial_industry_issuer`） |
| 同一份文件重複匯入不產生假版本，provenance 仍可稽核 | **PASS** | 以新的 import id 重跑 150 份：`created` 0、`deduplicated` 150；版本數不變，`financial_filing_version_observations` 由 1,732 增為 1,882（+150） |

其他量測：

| 量測 | 值 |
| --- | ---: |
| 事實：資產負債表 | 307,882 |
| 事實：現金流量表 | 179,890 |
| 事實：綜合損益表 | 113,792 |
| 沒有會計科目代碼的事實 | 0 |
| `(filing, QName, context, unit)` 重複組 | 6,928 |
| `(filing, QName, context, unit, statement)` 重複組 | **0** |
| `published_at IS NULL` 的發布證據 | 1,732／1,732 |
| 1101 2025Q1 System PIT 可見 | 是 |
| 1101 2025Q1 Market PIT 可見 | 否 |
| raw artifact origin | 全部 `legacy_archive` |

## 舊系統對帳（資產負債表，2025Q1 當期）

完整對帳是 23-c，這裡先做一次同範圍的比對：1,732 家共同發行公司、
`instant_date = 2025-03-31` 的資產負債表列。

```text
本系統 102,583 列
舊系統 102,583 列
完全相同 102,170
只有一邊 413（兩邊各 413）
```

413 列全部是會計科目代碼 **3997／3998／3999**，而且舊系統的值**正好是我們的
1,000 倍**（413 列，比值全部是 1E+3）。原因是這三個科目的單位是 `xbrli:shares`
（待註銷股本的股數），不是仟元：舊系統對整張表一律乘 1,000，把股數也乘了進去。
本系統套用文件自己的 `scale` 與 `unitRef`，所以股數就是股數（CLAUDE.md §72）。
這是一個有文件記載、PIT 正確的差異（§78），不是回歸。其餘 102,170 列逐筆相同。

## 來源實測：`REPORT_ID` 是互斥的

2026-09-21 實測：`t164sb01?step=1&CO_ID=1101&SYEAR=2025&SSEASON=1&REPORT_ID=A`
回傳 HTTP 200 與 98 bytes 的 cp950 `檔案不存在!`；1342 的 `REPORT_ID=C` 回同一頁，
而各自的另一個 id 回的是文件。所以一家公司一季只申報合併或個體其中一種——
這正是檔案庫「一個 (代號, 季) 一份文件」的形狀從端點看過去的樣子。那一頁是來源的
回答而不是失敗，adapter 回報 `no_such_report`，CLI 的 `auto` 再去要另一個 id，
CLI 測試用真實的那 98 bytes 當 fixture。

## 測試

先寫會紅的測試再實作：16 個 adapter 單元測試、15 個 importer／migration／CLI
整合測試，全部在實作前跑紅過。

```text
tests/unit/test_step23b_financial_filing_adapters.py        16 passed
tests/integration/test_step23b_financial_filing_ingestion.py 15 passed
全套                                                       1,070 passed, 3 skipped
alembic upgrade head / downgrade / upgrade                 pass
alembic check                                              no new upgrade operations detected
```

Fixture 是真實文件切出來的：head 到 `</ix:header>`、只保留被引用的 context、
接著三張報表各自的錨點、`<table>` 與數列真實 `<tr>`，保留的部分沒有一個位元被改過。
`mops_t164sb01_no_report.html` 是 MOPS 2026-09-21 真實回應的那 98 bytes。

## 已知限制與延後的工作

- 全量 backfill（45,324 份）、檔案庫對官方的抽樣關卡、完整的舊系統對帳：23-c。
- 發布證據（2025Q4 起的 `legacy_capture_bound`，其餘 release rule）：23-c。
  在那之前財務報表在 Market PIT 下一律看不到。
- 權益變動表、附註、附表與敘述區塊要不要存：ROADMAP 最後再決定（owner）。
- 舊系統只存當期，本系統把文件印出來的去年同期比較欄一起存了。要做與舊系統
  完全同範圍的比對，在對帳時依 context 過濾即可（23-c）。
- 本 step 沒有 `docs/stepNN.html` 教材頁，冷讀 N/A。

## Code review 的處理（#40，2026-09-21）

六項發現全部成立，全部修掉。HIGH 那一項在真實檔案庫上重現過，也在真實檔案庫上驗證修好。

**HIGH — 單季 EPS 等於累計 EPS 時整份申報被拒。** 原本 importer 以
`(filing, concept, unit, numeric_value, statement, period_end)` 反查已寫入的 EPS 事實，
而單季與累計的 `period_end` 相同：值一樣時兩個 basis 會拿到同一筆
`source_fact_id`，seal 的 summary 契約必然有一邊不成立，整份匯入以 23514 中止。
6160 2024Q2 兩個 EPS 都是 -0.41（context 分別是 `From20240401To20240630` 與
`From20240101To20240630`）；實際跑下去比 reviewer 說的更早就死在
`classify_eps_period_basis`（「source classification does not match the XBRL context」）。
修法是不要反查：`FilingEPS` 帶 `fact_index`，adapter 在讀出 EPS 時就記下它是
`ParsedFinancialFiling.facts` 的第幾列，importer 直接用那一列寫回的 id。
reviewer 列出的 10 份文件現在全部匯入成功，6160 2024Q2 的 quarter 與 ytd 指向不同事實
（`period_start` 分別是 2024-04-01 與 2024-01-01）。新增 fixture
`mops_t164sb01_6160_2024Q2_statements.html` 與回歸測試。

**MEDIUM — 檔案庫 importer 沒有強制 `legacy_archive`。** 只換 fetcher 不夠：
`RawFirstImporter.run` 預設 `OFFICIAL_FETCH`，漏傳 kwarg 的呼叫者會把檔案庫位元組
記成官方抓取。改成 override `run()` 強制寫死，並加測試（不傳 kwarg 也必須是
`legacy_archive`）。

**MEDIUM — `resource()` 在 quarantine 區之外丟 SourceDataError。** 檔名帶著合成日期、
要 glob 才知道，而 `resource()` 跑在 manifest 建立之前，例外會整個逃出
`run()`，在 45,000 份的迴圈裡不留任何紀錄。改成 `resource()` 只回傳 pattern，
解析交給新的 `ArchiveGlobFetcher`，它在受保護的 fetch 區塊裡丟——失敗會留下
`failed` 的 manifest。`source_uri` 記的是實際讀到的檔案，manifest 記的是請求的
pattern。加了「找不到檔案留下 failed manifest」的測試。

**LOW — `_preview` 可能靜默回傳 `""`。** 改成 `cp950` 加 `errors="replace"`，
512 byte 的切點落在雙位元組字元中間也不會漏掉 `檔案不存在!`。

**LOW — statement span 止於第一個 `</table>`，索引取自轉小寫的副本。** 改成用
`re.compile(r"<table\b|</table>", re.I)` 數巢狀深度走到自己那個結尾，順便不再對
每份文件複製數 MB 字串。修完對**全部 45,324 份**文件重跑三張表的事實數，與修改前
**逐份相同（0 份不一致）**，解析失敗仍然只有已知的 3 份 2855。

**LOW — C→A fallback 比對格式化訊息字串。** 改讀
`error.__cause__.reason_code`。

修完全套測試 1,074 passed（新增 4 條）。
