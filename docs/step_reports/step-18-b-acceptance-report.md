# Step 18-b 驗收報告

狀態：IN REVIEW

範圍：市場指數匯入路徑與 backfill

Schema 影響：無。Migration `7a2c9e4d1b58` 只新增列：三個 `dataset_sources`、它們的
release rule 對應，以及兩個 `daily_price` 形式的預期涵蓋宣告。
PIT 影響：沒有新的影響——三個來源都遵循 `exchange_daily_settled@1`。
`src/` 改動 +600/−22 行，另外提交一個腳本。

## 基準

舊系統 `stock_db.market_indices`，2020-01-02 → 2026-09-11：1,627 個日期共 449,528 列，
366 個不同的 TWSE 名稱和 43 個 TPEx 名稱。

Step 18-a 的解析（以 #22 合併），及其 identity 發現：`(source, section, published name)`，
因為 TPEx 的 34 個名稱中有 32 個在價格和報酬區段重複。

## 執行

```text
                     indices   dates        rows
twse_mi_index            335   1,627     365,775
tpex_index_summary        95   1,627     111,224
twse_mi_5mins_hist         1   1,630       1,630
                                        ---------
                                          478,629
evidence 478,629 release_rule    artifacts 5,046    data/raw 560 MB
```

兩個整份清單的 backfill：`imported 1627, resumed 0, failed 0, is_complete true`。
TAIEX 執行涵蓋 81 個月；它的 1,630 個日期比期間多出 2026 年 9 月 11 日之後的三天。

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 舊系統 `market_indices` 在兩個市場都已對帳 | PASS | TWSE：比較 365,342 列，**23 個差異**，全在兩個日期，下文解釋。TPEx：比較 51,623 列，**差異為零**。 |
| 每個差異都已分類 | PASS | 五個類別，分開計數：`legacy_only`（23）、根本不是指數的舊系統列（32,540）、只在來源的報酬區段（TWSE 220／TPEx 59,601）、只在來源的價格區段（213／0），以及零個數值不一致。 |
| `MI_5MINS_HIST` 的 TAIEX 收盤等於 `MI_INDEX` 的收盤 | PASS | **比較 1,627 個日期，零個不一致。** 兩個 TWSE 來源都發布 `發行量加權股價指數`；交叉核對逐日把它們並列並回報，而不是取平均。 |
| TPEx 報酬指數是新資料，不是差異 | PASS | 我們有 95 個 TPEx 指數——43 個價格、52 個報酬——而舊系統有 43 個，恰好就是價格區段。只在來源的 59,601 列是舊系統從未收集的報酬序列。 |
| 重新處理的路徑，或直接說明需要重新抓取 | PASS，走第二種 | 沒有建立重新處理的路徑，TWSE 指數檔案重新抓取了一次。原因記錄在程式中和下文；以內容定址讓成本只在請求，而不在儲存。 |
| 涵蓋完整 | PASS | 兩個市場都是 `expected 1627, observed 1627, missing [], unexpected [], is_complete true`，來自 Step 16 的驗證器。 |

## 為什麼 TWSE 指數檔案重新抓取了一次

Step 18-a 指名了 `stored_resource_key()`，讓重新處理的路徑可以讀取 Step 17-c 儲存的
bytes。這裡否決了建立那條路徑，理由值得記錄：lineage 的 foreign key 要求每個版本的
`(raw_artifact_id, ingest_run_id)` 都存在於 `raw_artifact_observations`，而那張表的
`fetched_at` 是*我們從來源讀取的時間*。重新處理的執行是從磁碟讀取。它要不捏造一個
抓取時刻，要不就繼承原本的時刻，而後者會把一個不是這次執行產生的時刻餵給 ADR-0020 的
抓取判斷。這個 repository 以前就被這類改動咬過，不值得為了省一小時的請求去做。

以內容定址讓重新抓取幾乎沒有成本。重新抓取的 `MI_INDEX` bytes hash 到已經在磁碟上的
artifact，所以 TWSE 的 `raw_artifacts` 沒有增加任何東西；只有每個日期一筆觀察列，而
這是誠實的——我們確實又讀了一次 TWSE。TPEx `indexSummary` 是不同的端點，所以那些
artifact 是新的。

## 測試抓到的兩個缺陷

**批次 writer 的 key 不唯一。** 它以連結欄位作為新建列的 key，這對每個交易日一支證券
成立，但對 TAIEX 匯入不成立：一個指數橫跨 21 個日期，全部坍縮到同一個 key，每個日期的
證據被附到最後回來的那個版本上。資料庫以 `publication precedes source date` 抓到它。
key 現在是連結欄位加上 identity 欄位。

**migration 的 downgrade 刪太多了。** 它移除了每個 `market_index` 來源，包括 Phase 8
fixture 建立的 `market_index/twse` 列，而那一列有 ingest run 參照——違反 `RESTRICT`。
它現在只刪除自己宣告的三個來源。與 Step 17-b 的 review 發現 3 同一個形狀，只是晚了
一個 migration。

## 實際執行抓到的一個缺陷

**月份迴圈有 Step 17-c 為日期迴圈修正過的續跑缺陷。** TAIEX backfill 在 81 個月中的
第 56 個月因為 `ReadTimeout` 中止，而重跑時重新抓取了全部 56 個月，因為迴圈仍然產生
新的 `uuid4()` base。這和 #21 的第一項發現相同，只是在兄弟迴圈裡——正是在一個地方
套用的修正會遺留下來的那種變體。`month_import_id` 現在由範圍 id 推導，而一個無法抵達
的月份以非零結束碼回報，而不是結束整個執行。

## 對帳必須先了解的舊系統行為

在數字有意義之前，必須先分類兩種舊系統行為，而兩者都是靠查看不吻合的部分找到的，
而不是靠假設。

**舊系統存了不是指數的東西。** 32,540 列是 `漲跌證券數合計` 市場寬度表——
`1.一般股票`、`12.公司債`、`13.ETN`、`證券合計(1+6+14+15)`、`持平`、`未成交`。舊系統
的 parser 把它們和真正的指數一起寫進 `market_indices`。它們被算作自己的類別，絕不算
作差異。

**有兩個舊系統日期不完整。** 剩下的 23 個 `legacy_only` 列全部落在 2024-01-25 和
2026-02-09，那兩天舊系統有 95 和 139 個指數列，而相鄰日期是 277 和 287。那兩次舊系統
抓取是不完整的——和 Step 17-c 中 2026-03-27 的每日價格案例同一類。它們依日期回報，
而不是被解釋掉，因為無法解釋的列不可以藏在預期的類別裡。

兩種資料也需要不同的比對規則，有一次嘗試在數字揭露之前就弄錯了：TWSE 為報酬指數取
不同的名稱，而舊系統收集了兩個區段，所以舊系統的名稱與存放它的那個區段比對；TPEx 在
兩個區段重複同一個名稱，而舊系統只保留價格那個，所以比對必須限定在價格區段內。共用
一條規則在 TPEx 上產生了 50,014 個假差異。

## 記錄在程式中的範圍排除

**不**寫入 `market_index_metadata_versions`。資料庫強制一個版本的 ingest run 帶有該
版本自己的 `dataset_code`，所以指數 metadata 需要自己的 run、source policy 和證據——
為了 audit §5 已經記錄為我們自己的衍生值、而不是來源的值的兩個欄位，去接第二個資料集
的線路。發布的名稱存在 `market_index.index_code` 中，identity 本來就是從那裡讀取。

## 驗證

從零 migrate 的資料庫：

```text
469 passed, 3 skipped, 1 warning
```

本 step 之前的基準：457。差異就是這裡新增的 12 個 integration test，每一個都確認過先
失敗——10 個在 importer 存在之前，2 個來自 review，對照 push 時的程式。

在乾淨資料庫上的 migration 往返：`upgrade head` 寫入 catalog 列、三個來源、三個規則
對應和兩個涵蓋宣告；`downgrade` 恰好移除這些，包括 catalog 列；`upgrade head` 重新
寫入。

`ruff check` 相對於 `main` 沒有回報新問題。

## Code review 發現

六項發現，在改動任何東西之前都經過驗證；沒有一項是誤報。

| # | 發現 | 驗證方式 | 處置 |
| --- | --- | --- | --- |
| 1 | **每個**月份都失敗時，`taiex-history` 會當掉：`result` 和最後的 `import_id` 只在成功時才賦值，所以回報區塊會讀一個不存在的 manifest | 一個回歸測試用永遠拋出錯誤的 fetcher 驅動 CLI：在 `asdict(result)` 拋出 `UnboundLocalError` 之前就發生 `NoResultFound`。 | **已修正。** 全部失敗的執行會印出它的失敗並以 1 結束。這個 step 新增的失敗路徑沒能撐過它自己的最壞情況——完全失敗的執行，正是它的報告最重要的那一次。 |
| 2 | `append_index_metadata_snapshot` 是死碼，而且被呼叫的話是錯的：它把同一個指數的兩筆觀察合併，回傳的 tuple 長度與輸入不同，會破壞 `zip(..., strict=True)` 的證據模式 | `src` 或 `tests` 中任何地方都沒有呼叫者。 | **已刪除。** 約 60 行沒有測試的程式，屬於這個 step 明確不擁有的資料集（§67）。擁有指數 metadata 的 step 會寫它，並附上測試。 |
| 3 | `_existing_rows` 依來源和實體過濾，卻沒有依期間過濾，不像它的每日價格雙胞胎 | 閱讀兩者。一次 `correction_check` 重跑會每個日期載入全部 365,775 個已儲存的列，每筆觀察掃描約 1,340 個候選。 | 已修正。以 identity 的期間欄位界定範圍。 |
| 4 | downgrade 防護計算的是版本數，但擋住的 foreign key 是 `ingest_runs → dataset_sources`；一個被 quarantine 的日期會留下一個沒有版本的 run，所以防護通過，而 DELETE 做到一半失敗 | 一個回歸測試先 quarantine 一個休市日再 downgrade：sqlstate `23001`，修改到一半時的原始 FK 違規，而 §81 要的是修改前刻意的拒絕。 | 已修正。防護計算 ingest run、manifest 和版本，現在會在碰任何東西之前拋出 `P0001`。 |
| 5 | 不對稱的 downgrade：upgrade 建立了 `dataset_catalog` 列，downgrade 卻從不移除它 | 閱讀。 | 已修正。downgrade 會移除它，但只在沒有任何來源宣告這個資料集之後。 |
| 6 | 容差的註解對 `0.0001` 寫的是「百分之一」 | 閱讀。 | 已修正。 |

真正重要的是第 1 項，而且一針見血：這個 step 為了因應實際的 `ReadTimeout`，*新增*了
逐月的失敗路徑，而那條路徑沒有針對全部失敗的情況測試過。

## 已確認的範圍排除

- 官方估值屬於 18-c。
- 指數成交金額保持 NULL：檢查過的來源都沒有發布。
- 過去日期的櫃買指數 OHLC 保持 NULL——`openapi/v1/tpex_index` 不接受參數，永遠回答
  當月（audit §4.2）。
- 沒有指數更名連結。改名的指數在官方證據另有說明之前是新的 identity。
