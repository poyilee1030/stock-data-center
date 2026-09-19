# Step 18-c 驗收報告

狀態：IN REVIEW (#29)

範圍：官方估值。這個 step 新增 TWSE `BWIBBU_d` 和 TPEx `afterTrading/peQryDate`
adapter、它們的 importer、source policy 與涵蓋宣告、CLI，以及兩個市場
2020-01-02 → 2026-09-11 的 backfill。

Schema 影響：無。Migration `b3d6f0a2c8e5` 只新增列：`official_valuation` 的 catalog
條目、兩個 `dataset_sources` 列、它們的 release rule 對應，以及兩個預期涵蓋宣告。
PIT 影響：沒有新的影響。兩個來源都遵循 `exchange_daily_settled@1`。
規模：`src/` 改動 +764/−1 行。這個 step 另外提交一個腳本和八個 fixture。

## 這個 step 中做出的來源決策

每項決策都記錄在 ROADMAP 18-c 和 audit §4.6。

1. **TPEx 以 JSON 讀取，而不是 CSV。** ROADMAP 原本說 TPEx 新網站沒有這張表的 JSON，
   而且 CSV 沒有寫明日期。兩個說法都錯：
   - 舊的 `pera.php` 頁面現在會 redirect（302）到新頁面。那個頁面從
     `www/zh-tw/afterTrading/peQryDate` 載入它的表格。
   - JSON 與 CSV 逐列相同：2020-01-02 有 772 列，2026-09-11 有 885 列。
   - JSON 自己寫明日期兩次，在最上層和表格上，並帶有列數和公式說明。
   - CSV 是 MS950，不是 big5，而且確實寫明 `資料日期`。

   owner 選擇了 JSON。它的 source code 是 `tpex_pe_qry_date`。
2. **未計算的標記存 NULL。** 這些是 TWSE `-`、TPEx `N/A`，以及依 owner 決定的 TPEx
   `"null"` 和任一交易所剛好為零的比率。零和 `"null"` 沒有官方說明；兩者都是 TPEx
   對證券上市首日的呈現方式。負的比率、殖利率或股利會把該列送進 quarantine，同一日期
   的其他列仍然匯入。
3. **財報期間和股利年度被正規化。** `財報年/季` 從各交易所自己的格式（`115/2` 和
   `115Q2`）解析，存成 `YYYYQn`。出現另一個交易所格式的值會讓檔案失敗。`股利年度`
   存成民國年 + 1911。

這個 step 進行期間，同樣的 redirect 檢查發現 TPEx `3itrade_hedge`、`3itrdsum`、
`margin_bal` 和 `margin_sbl`，以及 TPEx 自己的外資持股表（`insti/qfii`）都有 GET
JSON。它也確認了 MOPS 新網站對月營收、iXBRL 或股利宣告都沒有可用的 JSON。這些發現
記錄在 audit §4.3–4.5、§4.7、§4.8 和 §4.13，以及 ROADMAP Steps 20 和 21。要怎麼
處理它們，由那些 step 決定。

## 基準

舊系統 `stock_db.pe_ratio`，2020-01-02 → 2026-09-11：

| 市場 | 列數 | 日期數 | 非 NULL 的 PE |
| --- | ---: | ---: | ---: |
| `sii` | 1,611,824 | 1,627 | 1,302,368 |
| `otc` | 1,324,687 | 1,627 | 962,372 |

## 執行

```text
                     versions   securities  dates   raw artifacts
twse_bwibbu_d       1,612,498        1,121  1,627           1,654
tpex_pe_qry_date    1,324,687          944  1,627           1,943
evidence  2,937,185  release_rule exchange_daily_settled@1, unknown 0
data/raw  783 MB after this step
```

兩個市場來自 Step 16 驗證器的涵蓋：`expected 1627, observed 1627, missing [], unexpected [], is_complete true`。

backfill 跑了兩次，manifest 記錄了兩輪：

1. **v1** 涵蓋 TPEx 2020-01-02 → 2025-03-27 和 TWSE 2020-01-02 → 2024-06-14。它碰到
   TPEx 的 `"null"` 比率，把 205 個完整日期以 `unrecognised_value` 送進 quarantine。
   它也記錄了一次 TWSE 抓取逾時（2024-05-07）和一次列 quarantine（6720，
   2024-12-04）。owner 接著決定那些值代表未計算，v1 被停止。
2. **v2**（`twse-bwibbu-d:v2`、`tpex-pe-qry-date:v2`）跑了四個區間：
   - TWSE 2024-05-07 → 2026-09-11：匯入 574 個日期，0 個失敗。
   - TPEx 2021-07-26 → 2022-11-02：匯入 315 個日期，0 個失敗。這新增了 162,170 個
     版本；87,701 列與 v1 去重。
   - TPEx 2024-12-04：1 個新版本，6720，以 NULL 比率儲存。
   - TPEx 2025-03-28 → 2026-09-11：匯入 358 個日期，0 個失敗。

以新的 adapter 版本重新開始需要新的 import id。生命週期拒絕以改變了的設定 fingerprint
重用 import id。舊的失敗 manifest 和被取代的列 quarantine 作為歷史保留在原處。

在 v1 成功匯入的每個日期上，v1 和 v2 產生相同的輸出。兩者唯一的差別是如何處理
`"null"` 和零。v1 把每個這樣的值都送進 quarantine，而每個出現這種值的日期都在 v2 下
重新匯入。

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 舊系統 `pe_ratio` 在兩個市場都已對帳 | PASS | **TPEx：**比較 1,324,687 列，1 個差異：6720 在 2024-12-04，舊系統是 `0.0`，我們依決定存 NULL。**TWSE：**比較 1,597,366 列，0 個差異。其餘 14,458 列舊系統資料落在 15 個舊系統存了別的日期檔案的日期上。那些日期整體分類，見下文。 |
| 每個差異都已分類 | PASS | 對帳腳本的類別是 `legacy_captured_another_date`（附上舊系統檔案重現的官方日期）、`pe_ratio:*`、`legacy_only` 和 `legacy_only:we_quarantined_*`。沒有剩下未分類的列，腳本以 0 結束。 |
| `dividend_yield`、`pb_ratio`、`dividend_year`、`report_period` 回報為新資料 | PASS | 非 NULL 的列，TWSE／TPEx：PB 1,612,236／1,324,129；殖利率 1,612,498／1,324,687；股利年度 1,612,498／1,324,687；財報期間 1,612,498／355,131。 |
| 2025-06-24 那份 5 欄的 TWSE 檔案由明確的版本解析，或被 quarantine | PASS | TWSE 現在以 8 欄 header 提供 2025-06-24。5 欄的檔案只存在於舊系統檔案庫，而且裝的是別的日期的資料。adapter 明確解析真正的 5 欄 header（`bwibbu_5`，以 TWSE 2017-01-03 的回應測試）。其他任何 header 都以 `schema_mismatch` 讓檔案失敗。期間內全部 1,627 個 TWSE 日期（1,654 次匯入）都是 `bwibbu_8`。 |
| 2025-01-02 之前的 TPEx `財報年/季` 是 NULL 而不是錯誤；`115/2` 和 `115Q2` 各自明確解析 | PASS | `pe_qry_date_7` 涵蓋到 2024-12-31 的 1,216 個日期，`pe_qry_date_8` 涵蓋 2025-01-02 起的 411 個日期。第一個儲存的 TPEx 財報期間在 2025-01-02。測試涵蓋每個交易所拒絕另一個交易所的格式。 |
| 只有 TPEx 填入 `dividend_per_share` | PASS | TPEx 1,324,687 列，TWSE 0。 |
| 涵蓋完整 | PASS | 兩個市場：1,627／1,627 個日期。 |

## 對帳必須先了解的舊系統行為

在 15 個 TWSE 日期上，舊系統存了一個不同日期的檔案，而它的管線中沒有任何東西發現。
舊系統的 CSV 沒有日期，它的 parser 既不檢查日期也不檢查 header。腳本先為每個舊系統
日期評分，看它與我們同一日期的檔案有多一致。一致率低於 50% 時，它在整個期間搜尋舊
系統檔案所重現的官方日期：

| 舊系統日期 | 與同日官方檔案的一致率 | 舊系統檔案其實是 |
| --- | ---: | --- |
| 2020-12-07 | 24.2% | 2020-12-18（100%） |
| 2022-01-24 | 15.9% | 2022-01-18（100%） |
| 2023-05-15 | 12.0% | 2024-06-18（100%） |
| 2024-09-20 | 22.7% | 2024-09-18（100%） |
| 2024-11-11 | 19.4% | 2024-12-18（100%） |
| 2025-02-19、2025-02-20 | 29.9%、26.3% | 2025-02-18（100%） |
| 2025-06-04、2025-06-05 | 22.4%、22.2% | 2025-06-18（100%） |
| 2025-07-01 | 22.2% | 2025-07-18（100%） |
| 2022-02-17、2023-03-22、2024-01-08、2025-08-20 | 7–9% | 同一個逐 byte 相同、裝著 2017 年底資料的檔案（股利年度 105，財報年/季 106/3） |
| 2025-06-24 | 5.8% | 一個較舊的 5 欄檔案，不在期間內 |

同樣這 15 個日期，涵蓋了舊系統缺少的全部 970 個已儲存列。其他任何日期都沒有只在舊
系統或只在來源的列。

這和 Step 17-c 與 Step 18-b 以不完整抓取的形式發現的，是同一類舊系統缺陷。這裡的抓取
大小正確，但日期錯了。

## 實際執行抓到的缺陷

**`"null"` 是未知的 token，所以讓整個日期失敗。** adapter 把任何無法辨識的儲存格
當成格式改變。那是正確的預設，也讓問題浮現：205 個 TPEx 日期被 quarantine，每個 raw
artifact 都保留了。但對一個 owner 隨後做出裁決的值來說，那是錯誤的粒度，因為 379 個
儲存格讓 162,170 列付出代價。修正改變的是標記，而不是 fail-closed 的預設。新的未知
token 仍然會讓它的檔案失敗。

**第一次停止太粗暴。** `pkill -f` 匹配到發出它的 shell，所以指令回報結束碼 144。
backfill process 確實停了。沒有任何 manifest 停在 `running`：被中斷的日期還沒開啟寫入
transaction，而 v2 以新的 id 匯入了它們。

## 驗證

從零 migrate 的資料庫：

```text
633 passed, 3 skipped, 1 warning
```

`main` 上的基準是收集到 595 個（592 passed、3 skipped）。差異是 41 個新測試（30 個
unit、11 個 integration）。

每個測試如何確認先失敗：

- Unit test：25 個對照一個拋出 `NotImplementedError` 的 stub adapter。第 26 個是
  source code 常數，在 stub 存在之前就在 import 時失敗。
- Integration test：10 個全部在 importer 存在之前失敗。實作之後，移除 migration 時
  有 4 個失敗，移除 quarantine 寫入時 quarantine 測試失敗。
- 零／null 決定的測試，對照每次改動之前的程式失敗。

`alembic upgrade head` → `alembic check` 沒有回報新的操作。`downgrade 9f3d7c2e5a41`
恰好移除這個 migration 宣告的列，第二次 upgrade 會還原它們。有被 quarantine 的 run
存在時，downgrade 防護在任何修改之前拋出 `P0001`。`ruff check` 相對於 `main` 沒有回報
新問題。

重現對帳：

```bash
python scripts/reconcile_official_valuation.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \
    --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
```

## Code review 發現

對 #29 的 medium 層級 review 沒有發現 medium 以上嚴重度的 bug。它提出了兩項
low 嚴重度的發現，兩者都在改動任何東西之前對照程式檢查過。

| # | 發現 | 驗證方式 | 處置 |
| --- | --- | --- | --- |
| 1 | 小數超過八位的值會讓整個日期失敗（`invalid_numeric`），而不是只有它那一列 | 閱讀 `_build`。觀察的精度檢查拋出 `ValueError`，變成檔案層級的 `SourceDataError`。 | **維持原狀。** owner 決定讓負值和未計算標記成為列層級；它對精度沒有任何說法。精度改變就是格式改變，而格式改變會讓它的檔案失敗。如果某個資料對每一列都加寬了欄位，逐列拒絕會在一個 `succeeded` 的 manifest 底下記錄 830 筆列 quarantine，把改變藏起來。兩個資料都從未印出超過八位的小數。TPEx 的股利恰好使用八位。 |
| 2 | `"null"` 在每個數值欄位都被接受，但決定只涵蓋兩個上市首日的比率 | 閱讀 `_number`。一個在 `每股股利` 和 `殖利率(%)` 放入 `"null"` 的新測試預期 `unrecognised_value`，結果失敗。 | **已修正。** `"null"` 現在是只限比率的標記（`ratio_not_computed`），而 TPEx 有文件記載的 `N/A` 仍適用於每個值。TPEx adapter 現在是 **v3**。backfill 不需要重跑：audit §4.6 中的 raw artifact 掃描只在兩個比率中找到 `"null"`，所以在每個已儲存的檔案上，v3 產生與 v2 相同的列。manifest 保留它們執行時的 v1 和 v2 標籤。 |

## 已確認的範圍排除

- 計算出的估值是 Step 26 的工作。這個 step 只儲存交易所發布的值。
- 不儲存 `BWIBBU_d` 的 `收盤價` 和 TPEx 的公司名稱。它們歸每日價格和證券 metadata。
- 除了記錄 TPEx JSON 的發現之外，Steps 20 和 21 沒有改變。
