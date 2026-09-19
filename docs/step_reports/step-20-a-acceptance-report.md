# Step 20-a 驗收報告

狀態：IN REVIEW (#30)

範圍：個股法人買賣。這個 step 新增 TWSE `T86` 和 TPEx `insti/dailyTrade` adapter、
Phase 7 資料集的集合式 writer、importer、source policy 與涵蓋宣告、CLI，以及兩個市場
2020-01-02 → 2026-09-11 的 backfill。

Schema 影響：無。Migration `c4e7a1d3f9b6` 只新增列：`institutional_investor` 的
catalog 條目、兩個 `dataset_sources` 列、它們的 release rule 對應，以及兩個預期涵蓋
宣告。
PIT 影響：沒有新的影響。兩個來源都遵循 `exchange_daily_settled@1`。
規模：`src/` 改動 +832/−0 行。這在約 800 行的拆分門檻上（`CLAUDE.md` §1）；20-a 已經
是最小的接縫，一個資料集。這個 step 另外提交一個腳本和六個 fixture。

## 為什麼 Step 20 要拆分

Step 20 涵蓋兩個市場的三個資料集，還有抓取層的工作。這超過一個可審閱變更能容納的量
（`CLAUDE.md` §1）。ROADMAP 現在把它拆成四部分：

| 部分 | 交付內容 |
| --- | --- |
| 20-a（本部分） | 個股買賣：`T86`、`insti/dailyTrade` |
| 20-b | 市場彙總：`BFI82U`、`insti/summary` |
| 20-c | 完整的 `SourceResource`（method、body、headers）與每台主機的速率控管器 |
| 20-d | 外資持股：`MI_QFIIS`、MOPS `t13sa150_otc` |

## 這個 step 中做出的來源決策

每項決策都記錄在 ROADMAP Step 20 和 audit §4.3–4.4。

1. **`insti/qfii` 無法取代 MOPS。** ROADMAP 原本保留一個問題：TPEx 自己的外資持股
   JSON 能否取代 MOPS `t13sa150_otc`，那樣就不需要 POST resource。答案是不能。在
   2026-09-11 比較：
   - MOPS 列出 1,010 支證券，`insti/qfii` 列出 892 支。只有 MOPS 列出的 119 支全是 ETF。
   - `insti/qfii` 沒有陸資法令投資上限比率，也沒有發行公司申報日期。兩者都是
     `foreign_holding_versions` 的欄位。
   - 尚可投資比率有 436 列的四捨五入方式不同。

   因此 20-c 和 20-d 保留 MOPS。
2. **TPEx 的買賣資料以 JSON 讀取。** `insti/dailyTrade` 是舊的 `3itrade_hedge.php`
   redirect 過去的那個頁面的資料呼叫，有相同的 24 個欄位。請求需要 `type=Daily`
   （必填）和 `sect=EW`（排除權證和牛熊證）。這讓它成為 TWSE `ALLBUT0999` 的對應。
3. **TPEx 的欄位群組來自官方頁面。** JSON 只用三個重複的名稱來標示它的 21 個數值
   欄位。每個欄位屬於哪個群組，由載入這張表的頁面的 `<template id="theads">` 證明。
   adapter 只接受那個確切的 24 欄清單。頁面的另一種版面——16 欄、沒有外資自營商
   群組——在這裡是格式改變，而不是版本：期間內沒有任何日期使用它。
4. **兩個 TPEx 合計不儲存。** 它們是外資及陸資合計，以及自營商合計的買進和賣出。
   契約中沒有它們的欄位。對帳重新讀取每個 raw artifact，在全部 1,249,305 列上都發現
   每個合計都等於兩個已儲存群組的總和。
5. **不重新計算任何值。** 淨額照發布的樣子、帶正負號儲存。負的總量會以格式改變讓它的
   檔案失敗；沒有發生過。

## 基準

舊系統 `stock_db.institutional_investors`，2020-01-02 → 2026-09-11：

| 市場 | 列數 | 日期數 |
| --- | ---: | ---: |
| `sii` | 1,585,890 | 1,627 |
| `otc` | 1,081,815 | 1,627 |

## 執行

```text
                         versions   dates   raw artifacts   evidence (release_rule)
twse_t86                1,876,161   1,627           1,653   1,876,161, unknown 0
tpex_insti_daily_trade  1,249,305   1,627           1,627   1,249,305, unknown 0
data/raw  1.3 GB after this step (783 MB after 18-c)
```

兩個市場來自 Step 16 驗證器的涵蓋：`expected 1627, observed 1627, missing [], unexpected [], is_complete true`。

兩個市場以 `--min-interval-seconds 1.5` 平行執行，每台主機一個 process，約 2 小時
10 分鐘。

**TWSE 對 12 個日期做了流量限制。** 日期是 2024-10-15 和 2024-11-05 → 2024-11-20。
對每一個日期，TWSE 都回應同一個 611 bytes 的 CDN 錯誤頁
（`errorpage-twseweb.cdn.hinet.net`），而不是 JSON。adapter 把那些日期以
`invalid_json` 送進 quarantine，把頁面保留為 raw artifact，然後繼續。執行仍以 exit 0
結束。

再次抓取時，TWSE 正常提供了同一個日期。因此 2024-10-15 → 2024-11-20 這段期間以新的
base import id、3 秒間隔重跑：匯入 26 個日期，0 個失敗。這 26 個日期中有 14 個已經
匯入過，所以重跑去重了 16,933 個版本，沒有建立新版本。12 個失敗的 manifest 及其
quarantine 列作為歷史保留。

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 舊系統 `institutional_investors` 在兩個市場都已對帳 | PASS | **TPEx：**比較全部 1,081,815 列舊系統資料；10 列不同，在 2 個日期。**TWSE：**比較 1,580,450 列；1,591 列不同。其餘 5,440 列舊系統資料落在 6 個舊系統存了別的日期檔案的日期上。沒有任何舊系統列在我們這邊缺少。 |
| 每個差異都已分類 | PASS | 類別是 `value_differs:legacy_row_incomplete`、`value_differs:both_rows_consistent` 和 `legacy_captured_another_date`。每一個都在下文解釋。腳本以 0 結束。 |
| 涵蓋完整 | PASS | 兩個市場：1,627／1,627 個日期。 |
| 每個儲存的列都滿足已發布的恆等式 | PASS | 3,125,466 列中 0 個失敗。逐群組檢查：買進 − 賣出 = 淨額；自行買賣 + 避險 = 自營商淨額；外資 + 投信 + 自營商 = 合計。 |
| TPEx 沒有儲存的合計等於已儲存群組的總和 | PASS | 1,627 個 artifact、1,249,305 列，0 個失敗。 |
| 重跑相同內容不產生 revision | PASS | TWSE 的重試：去重 16,933，新建 0。也有 integration test 涵蓋。 |

## 差異

**`legacy_row_incomplete`：TWSE，130 個日期共 1,491 列。** 在這些列上，舊系統有
NULL，而它有的值放錯了欄位。例如：2007 在 2020-06-16，舊系統的 `dealer_hedge_buy`
是 26,000，賣出、淨額和合計都是 NULL。我們的避險群組是 0，合計是 26,000。舊系統的
parser 弄壞了這些列。我們的列通過每一個恆等式。

**`both_rows_consistent`：4 個日期共 110 列，全在 2026 年。**

| 日期 | TWSE | TPEx | 舊系統檔案儲存時間 |
| --- | ---: | ---: | --- |
| 2026-02-03 | 23 | 5 | 2026-02-03 18:15 |
| 2026-02-11 | — | 5 | 2026-02-11 22:00 |
| 2026-04-07 | 52 | — | 2026-04-07 23:30 |
| 2026-06-05 | 25 | — | 2026-06-05 23:30 |

兩邊的列本身都一致，差別是買進和其淨額之間，或買進和賣出之間差了同一個金額。例如
2317 在 2026-02-03，我們的外資買進和外資賣出都多了 1,425,000。舊系統在交易日當天就
儲存了這些檔案，早於 `exchange_daily_settled@1` 的 `03:00 D+1` 時刻。這就是 audit §7
記錄、而規則刻意排除的當日未確定抓取，所以我們的是確定後的值。舊系統較早的值不匯入：
這個 step 儲存來源現在提供的內容，不為它們做任何首次看到的宣稱。

**`legacy_captured_another_date`：TWSE，6 個日期，5,440 列舊系統資料。** 在這些日期
上，舊系統的值與我們的一致率低於 0.5%：

| 舊系統日期 | 舊系統檔案其實是 |
| --- | --- |
| 2023-08-04 | 2023-08-18 |
| 2024-10-22、2024-10-29 | 2024-10-18 |
| 2021-06-17、2022-04-20、2024-04-08 | 同一個期間外的檔案，812 列，2330 外資買進 8,317,600，三個日期都是 |

這就是 Step 18-c 在舊系統 `pe_ratio` 中發現的缺陷：檔案的形狀正確，但屬於另一個日期。

**舊系統從未有過的已儲存列：TWSE 288,797 列、TPEx 167,490 列。** 全部是 ETF 和其他
非 4 位數的代號。舊系統只保留普通股。沒有任何 4 位數代號在任何日期只出現在來源。

## 驗證

從零 migrate 的資料庫：

```text
670 passed, 3 skipped, 1 warning
```

`main` 上的基準是 633 passed 和 3 skipped。差異是 37 個新測試（25 個 unit、12 個
integration）。其中兩個來自 code review；見下文。

每個測試如何確認先失敗：

- Unit test：24 個對照一個拋出 `NotImplementedError` 的 stub adapter。source code
  常數的測試在 stub 存在之前就在 import 時失敗。
- Integration test：10 個全部在 importer 存在之前就在 import 時失敗。有 importer、
  但沒有 migration 或 CLI 時，5 個失敗：解析、release rule 時刻、涵蓋、CLI，以及兩個
  downgrade 測試。release rule 測試被收緊為要求在 19:00 UTC 可見，並在加入 migration
  之前確認會失敗。

`alembic upgrade head` → `alembic check` 沒有回報新的操作。downgrade 到
`b3d6f0a2c8e5` 只移除這個 migration 的列，upgrade 會還原它們（已測試）。有被
quarantine 的 run 存在時，downgrade 防護在任何修改之前拋出 `P0001`（已測試）。
`ruff check` 相對於 `main` 沒有回報新問題。

重現對帳：

```bash
python scripts/reconcile_institutional_investors.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \
    --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
```

## Code review 發現

對 #30 的 review 找到一個 bug，此外沒有別的。在做任何改動之前，已對照程式檢查並重現。

| # | 發現 | 驗證方式 | 處置 |
| --- | --- | --- | --- |
| 1 | **Market PIT 洩漏。** `institutional_investor` 在 `DATASET_TARGETS`（`evidence/policy.py`）中沒有條目，所以 `plan_many` 看不到一個版本已經儲存的 `capture_bound`。假設在 D+1 03:00 之後抓取 D 的 `first_capture`：它正確地不寫 release rule。之後重新匯入 D，就會把 D+1 03:00 的 `release_rule` 附加進只可附加的證據，讓版本在其經證明的首次看到之前就可見。 | 一個新測試先執行一次晚到的 `first_capture`，再執行 `gap_fill`。它在 1,330 列 `capture_bound` 旁邊找到 1,330 列 `release_rule`。 | **已修正。** 資料集加進 `DATASET_TARGETS`，測試現在只找到 `capture_bound`。第二個永久測試要求每個來源接受 `capture_bound` 的資料集都必須有 `DATASET_TARGETS` 條目，所以 20-b、20-d 和 21 無法重蹈覆轍。兩個測試在修正前都失敗。 |

儲存的 backfill 不受影響。它以 `gap_fill` 執行，從不寫入 `capture_bound`。
`stockdc_backfill` 中這個資料集有 3,125,466 列 `release_rule`，沒有其他證據類型，所以
沒有任何抓取讓重新匯入去牴觸。

## 已確認的範圍排除

- 20-b、20-c 和 20-d 尚未開始；這個 step 只記錄它們的範圍。
- 不儲存舊系統的 `name` 欄位和 `pced_*` 座標。名稱歸證券 metadata，座標歸 raw
  artifact。
- 衍生的 `institutional_cumulative_flow:v1` 和 `institutional_streaks:v1` 仍是 Step 26
  的工作。
