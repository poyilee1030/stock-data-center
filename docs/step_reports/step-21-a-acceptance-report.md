# Step 21-a 驗收報告

狀態：IN REVIEW (#34)

範圍：融資融券。這個 step 新增 TWSE `marginTrading/MI_MARGN`（`twse_mi_margn`）和
TPEx `margin/balance`（`tpex_margin_balance`）adapter、importer、source policy 與
涵蓋宣告、CLI `margin-trading`、兩個市場 2020-01-02 → 2026-09-11 的 backfill，以及
與舊系統 `margin_trading` 的對帳。借券是 Step 21-b。

Schema 影響：`margin_trading_versions` 從 Step 7 起就存在。兩個 migration：
`a4c8e2f6b1d3` 只新增列（catalog、兩個來源、release rule 對應、兩個涵蓋宣告）；
`b9d1f3a5c7e2` 把使用率的 CHECK 從 0–100 放寬為 ≥ 0，這是依 owner 決定的 storage
contract 修正（見下文），downgrade 有防護。
PIT 影響：沒有新的影響。兩個來源都遵循 `exchange_daily_settled@1`，資料集加入
`DATASET_TARGETS`。依據：舊系統每日工作在交易日當天就存下 137 個 TWSE 和 136 個
TPEx 檔案（檔案 mtime）；其餘約 1,490 個是 2026 年 1–2 月批次重抓的，與時間無關。
規模：`src/` 改動 +653/−8 行，低於約 800 行的拆分門檻（`CLAUDE.md` §1）。另外提交
一個對帳腳本和六個 fixture。

## 為什麼 Step 21 拆成兩部分

Step 20-a 單一資料集就有 +832 行；融資融券和借券是兩個資料集、跨兩個市場，合在一起
會超過單一可審閱變更的大小。依 Step 20 的接縫拆成 21-a（融資融券）和 21-b（借券），
每部分本身完整。

## 這個 step 中做出的來源決策

每項決策都記錄在 ROADMAP Step 21-a 和 audit §4.5「Step 21-a findings」。

1. **TPEx 以 JSON 讀取。** `margin/balance` 是舊 `margin_bal` 頁面的資料呼叫，20 個
   欄位相同。短方欄位順序是「券賣」在「券買」前面（有測試）。
2. **單位來自來源的陳述。** TWSE 只在市場彙總列標示 `融資(交易單位)`、
   `融券(交易單位)`，adapter 每次都檢查這兩個標籤；TPEx 標示 `(張)`，由 header
   比對涵蓋。
3. **交易單位不一定是 1,000 股。** 見下一節。
4. **使用率可以超過 100%。** 見下下節。
5. **不儲存的欄位。** TPEx 的 資屬證金、券屬證金，以及兩個交易所的狀態註記（TWSE
   `O X @ % !`，TPEx `11 C` 之類），沒有契約欄位，保留在 raw artifact。

## 交易單位：008201

原本以為兩個市場的「張」都是 1,000 股。對帳腳本加入一項檢查：兩個交易所都在融資
餘額達上市股份 25% 時暫停融資，所以次一營業日限額換算成股後，應約等於 Step 20-d
儲存的發行股數的 25%。這項檢查找到 **008201 BP上證50**：612 天（2020-01-02 →
2022-07-08）比值穩定為 10，發行股數沒有變動。

TWSE 自己的 MI_INDEX 註解寫著：「除境外指數股票型基金及外國股票第二上市外，餘交易
單位皆為千股。」008201 是境外 ETF（ISIN `HK0000052297`），一張 100 股：限額 × 100
恰好等於發行量的 25%（例如 2020-01-02：3,872 × 100 = 387,200，發行量 1,549,100 的
25% 是 387,275）。

依 owner 決定，adapter 以明確的例外清單換算，其餘證券以 1,000 股換算；清單附上證據，
不在解析時從數值大小推斷（CLAUDE.md §72）。每個例外都記錄證據涵蓋的日期
（`TWSE_LOT_SHARES = {"008201": (100, 2020-01-02, 2022-07-08)}`，TWSE v3），範圍外
出現同一代號時整個檔案以 `unverified_trading_unit` 失敗。對帳持續做這項檢查。

其他比值偏離的證券（2832、3717、4572、4904、5904、6996）都**不是**交易單位的問題：
它們的發行股數在附近期間變動了一倍以上（減資、面額變更，或外資持股來源單日發布錯誤
的數字，例如 4904 在 2023-12-11、6996 在 2025-09-03/04 少了一位數），限額和發行股數
的更新時間不同。對帳腳本把這類歸為 `above_5:issued_shares_moved`，只有發行股數穩定、
比值卻偏離的才讓對帳失敗。

一開始我以「008201 的成交量是 100 的倍數」當作證據，這是錯的：成交量包含零股交易，
幾乎所有證券的成交量都不是 1,000 的倍數，所以它證明不了任何事，報告不採用。

## 使用率超過 100%

原本 Step 7 的 schema 把 `margin_utilization_ratio`／`short_utilization_ratio` 限制在
0–100，沒有來源依據。TPEx 公布 00989B（台新美國非投等債）在 2026-07-14 的資使用率
為 103.1%：前資餘額 15 張，當天資買 15,568 張，資餘額 15,583 張超過資限額 15,113 張，
備註 `O`（停止融資）。TWSE 的註解說明「備註欄係表明成交日次一營業日股票融資融券
狀況」，暫停從次一營業日才生效，所以單日可以超買；股數減少時也會發生同樣的情形。

依 owner 決定，migration `b9d1f3a5c7e2` 把上限放寬為 ≥ 0，模型的上限一併移除；
downgrade 在已有超過 100 的列時會在修改前拒絕（§68、§81）。整段 TPEx backfill 中只有
這一天超過 100。

## 基準

舊系統 `stock_db.margin_trading`，2020-01-02 → 2026-09-11，單位是張：

| 市場 | 列數 | 證券數 | 日期數 |
| --- | ---: | ---: | ---: |
| `sii` | 1,600,814 | 1,106 | 1,627 |
| `otc` | 1,127,965 | 849 | 1,627 |

2020-01-02 抽查：兩個官方來源都包含舊系統的每一支證券（沒有 Step 20-d MOPS 那種
生存者偏差），5371、4130 等之後下市的證券也還在。

## 執行

```text
                      versions   securities  dates   raw artifacts   evidence (release_rule)
twse_mi_margn        1,845,064        1,356  1,627           2,239   1,845,064, unknown 0
tpex_margin_balance  1,284,398        1,001  1,627           1,628   1,284,398, unknown 0
raw artifacts 3,254, 333 MB
```

TWSE 的 1,845,064 個版本包含 008201 的 612 個 v2 修正 revision：v1 以 1,000 股換算的
錯誤 revision 作為歷史保留（只可附加），v2 以 100 股換算的是較新的 revision。

backfill 的過程：

1. 兩個市場的 v1 backfill。TWSE 1 天逾時（2023-02-01），以同一個 import id 補齊；
   TPEx 的 2026-07-14 因使用率 103.1% 被 schema 擋下。
2. 加入例外清單（TWSE v2）與 migration 之後：TWSE 以新的 import id 重跑
   2020-01-02 → 2022-07-08（1 天逾時，補齊）；TPEx 以新的 import id 補匯入
   2026-07-14。

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 舊系統 `margin_trading` 在張 → 股換算後對帳 | PASS | **TWSE：**比較全部 1,600,814 列舊系統資料，**差異為零**。**TPEx：**比較全部 1,127,965 列，**差異為零**。沒有只在舊系統的列，也沒有舊系統存錯日期的檔案。 |
| 每個差異都已分類 | PASS | 沒有差異需要分類。`scripts/reconcile_margin_trading.py` 以 0 結束。 |
| 涵蓋完整 | PASS | 兩個市場：1,627／1,627 個日期。 |
| 每個儲存的列都滿足餘額推算 | PASS | 融資：前日餘額 + 買進 − 賣出 − 現金償還 = 今日餘額；融券：前日餘額 + 賣出 − 買進 − 現券償還 = 今日餘額。每個（證券、日期）的最新 revision 共 3,128,850 列，0 個失敗。 |
| 交易單位經過驗證 | PASS | 以證券的交易單位換算後，限額對發行股數 25% 的比值：沒有未解釋的偏離；170 列歸為發行股數變動。 |

舊系統沒有、我們有的列：TWSE 243,638 列、TPEx 156,433 列，全部屬於完全不在舊系統
中的證券（舊系統沒有收集的 ETF 等）。

## 驗證

從零 migrate 的資料庫：

```text
854 passed, 3 skipped, 1 warning
```

`main` 上的基準是 809 passed。差異是 45 個新測試：28 個 unit、17 個 integration。

每個測試如何確認先失敗：

- Unit test：在 adapter 存在之前就在收集時失敗；之後對照 stub，20 個失敗（只有
  source code 常數的測試通過）。
- Integration test：在 importer 存在之前就在收集時失敗；有 importer、沒有 migration
  和 CLI 時，8 個失敗。晚到抓取的測試在移除 `DATASET_TARGETS` 條目時失敗（920 列
  `release_rule` 洩漏），恢復後通過。
- 例外清單與使用率：4 個 unit test 和 3 個 integration test 在修改之前失敗；「負的
  使用率仍然失敗」是防止倒退的測試，一開始就通過。
- Code review 修正：3 個 unit test 和 1 個 integration test 在修改之前失敗（見下一節；
  其中一個 unit test 取代原本沒有日期的例外清單測試）。

`ruff check` 對新增和改動的檔案沒有回報新問題；`adapters/__init__.py` 未排序的
`__all__` 在 `main` 上就已存在。

重現對帳：

```bash
python scripts/reconcile_margin_trading.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \
    --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
```

## Code review（#34）

四項發現都成立，都在本 step 範圍內修正：

1. **（中）對帳的通過條件是黑名單。** 原本只排除 `legacy_only` 和 `value_differs`，
   `legacy_file_missing` 或之後新增的分類不會讓對帳失敗。改為白名單 `EXPLAINED`：只有
   已經證明的分類可以通過，其他一律失敗。`legacy_row_incomplete` 也收緊：只有在每個
   不同的欄位在舊系統都是 NULL 時才算，否則歸為 `value_differs`。
2. **（低）例外清單沒有日期。** 代號重新分配給別的證券時，會被靜默以 100 股換算、
   差十倍。改為 `(股數, 起, 迄)`，範圍外以 `unverified_trading_unit` 讓整個檔案失敗；
   adapter 升為 `twse-mi-margn:v3`。
3. **（低）使用率沒有欄位上限。** 放寬為 ≥ 0 之後，超過 `NUMERIC(12,8)` 能存的值
   （≥ 10,000）會在寫入時才失敗。模型加上上限 `9999.99999999`，在解析時就以
   `invalid_numeric` 讓檔案失敗。
4. **（低）例外清單不在設定指紋裡。** 修改清單不會改變 manifest 的指紋，續跑會混用
   兩種換算。`_source_semantics` 加入 `lot_shares` 與 `default_lot_shares`。

不需要重跑 backfill：008201 的所有日期都在它的範圍內，v3 的輸出和 v2 相同；只有
設定指紋不同，所以之後的續跑要用新的 import id。

## 踩到的坑

- **對帳腳本的單位 bug：** 第一版把儲存的股數當成張來比較，全部差 1,000 倍。先用
  前 30 天試跑才抓到；現在換算時要求每個值都是整數張。
- **續跑與 git 狀態：** 前 28 天回補時工作目錄是乾淨的，之後新增了未提交的對帳腳本；
  補跑時 lifecycle 比對 manifest 的 git commit（含 dirty 標記）不同，拒絕重用那 28 天
  的 import id。那 28 天早已成功，資料不受影響，但這是 lifecycle 既有的行為：續跑前
  工作目錄的狀態要和原本一致。記錄在 README 的已知問題，不在本 step 修。

## 已知限制與延後的工作

- **交易單位例外清單是人工維護的。** 新的境外 ETF 或外國股票第二上市掛牌時，要靠
  對帳的單位檢查發現，再補進 `TWSE_LOT_SHARES`。
- **Step 20-d 的外資持股來源有單日錯誤的發行股數**（4904 在 2023-12-11、6996 在
  2025-09-03/04）。那些是照發布樣子存下的來源資料，單位檢查已正確歸類；是否需要在
  20-d 的對帳中另外標出，留給之後決定。
- **修正帳本：** ROADMAP Step 20-d 已改為 **MERGED** (#33)。
