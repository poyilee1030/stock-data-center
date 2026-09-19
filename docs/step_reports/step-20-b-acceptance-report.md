# Step 20-b 驗收報告

狀態：IN REVIEW (#31)

範圍：法人買賣市場彙總。這個 step 新增 TWSE `BFI82U` 和 TPEx `insti/summary`
adapter、importer、source policy 與涵蓋宣告、CLI，以及兩個市場 2020-01-02 →
2026-09-11 的 backfill。它也處理了 2026-07-10 那個壞掉的 TPEx 檔案庫檔案。

Schema 影響：無。`institutional_market_summary_versions` 從 Step 7 起就存在。
Migration `d5f8b2e4a0c7` 只新增列：`institutional_market_summary` 的 catalog 條目、
兩個 `dataset_sources` 列、它們的 release rule 對應，以及兩個預期涵蓋宣告。
PIT 影響：沒有新的影響。兩個來源都遵循 `exchange_daily_settled@1`，而這個資料集加入
`DATASET_TARGETS`，所以重新匯入時，已儲存的抓取仍會否證規則（#30 的發現）。
規模：`src/` 改動 +547/−0 行，低於約 800 行的拆分門檻（`CLAUDE.md` §1）。這個 step
另外提交一個腳本和六個 fixture。

## 這個 step 中做出的來源決策

每項決策都記錄在 ROADMAP Step 20-b 和 audit §4.3。

1. **TPEx 以 JSON 讀取。** `insti/summary` 是舊的 `3itrdsum.php` redirect 過去的那個
   頁面的資料呼叫，有相同的四個欄位。
2. **每一列是一個版本，以發布的名稱為 key。** 邏輯 key 是
   `(market, trade_date, institution)`。兩個交易所對列的命名不同：TWSE 寫
   外資及陸資(不含外資自營商)，TPEx 寫 外資及陸資(不含自營商)。TPEx 還發布兩個 TWSE
   沒有的小計：外資及陸資合計 和 自營商合計。列以各交易所發布的名稱儲存，和指數
   （Step 18）一樣，沒有任何東西把一個市場對應到另一個。舊系統的英文代碼只出現在
   對帳腳本中。
3. **TPEx 的縮排是版面。** TPEx 以 U+3000 縮排它的四個子群組列。儲存的名稱去掉縮排，
   raw artifact 保留它。
4. **恰好是發布的列，依發布的順序。** adapter 對每個市場接受一份法人清單。新增、缺少、
   改名或移動位置的列，都以 `schema_mismatch` 讓檔案失敗。順序顯示了層級結構，而舊
   系統已經遇過一次版面改變（見下文）。
5. **單位是證明的，不是假設的。** TWSE 的欄位標籤沒有單位，所以 adapter 要求 `hints`
   是 `單位：元`。TPEx 的標籤寫著 `(元)`，由 header 比對涵蓋。
6. **不重新計算任何值。** 淨額照發布的樣子、帶正負號儲存。負的買進或賣出會讓檔案
   失敗；沒有發生過。

## 壞掉的 2026-07-10 檔案

舊系統檔案庫的 `institutional_summary/2026/20260710/otc.csv` 無法解析。它是 TPEx 的
JSON 回應被包進 CSV 儲存格，而它的表格是空的。

2026-07-10（星期五）是臨時休市。它不在 TWSE 發布的 2026 年休市日程中，但它不在 TWSE
交易日曆（Step 16）中，而且兩個市場那天都沒有價格或買賣資料。舊系統那天沒有任何列。
2026-09-18 實際重新抓取時，TPEx 對這個日期的回答與對星期日完全一樣：`stat: ok` 加上
空表。這個日期以 `no_data_for_date` 被 quarantine（import `ad45b141-…`，quarantine 列
225），官方 bytes 保留為 raw artifact `1c67b3a4-…`。以日曆驅動的 backfill 從不請求
這個日期，因為它不是交易日。它不是缺口。這個回應是永久 fixture，
`tests/fixtures/tpex_insti_summary_20260710_closed.json`。

## 基準

舊系統 `stock_db.institutional_summary`，2020-01-02 → 2026-09-11：

| 市場 | 列數 | 日期數 |
| --- | ---: | ---: |
| `sii` | 9,756 | 1,627 |
| `otc` | 9,762 | 1,627 |

`sii` 少了六列：三個日期各少兩列（見下文）。

## 執行

```text
                     versions   dates   raw artifacts   evidence (release_rule)
twse_bfi82u             9,762   1,627           1,627   9,762, unknown 0
tpex_insti_summary     13,016   1,627           1,628  13,016, unknown 0
```

兩個市場來自 Step 16 驗證器的涵蓋：`expected 1627, observed 1627, missing [], unexpected [], is_complete true`。

兩個市場以 `--min-interval-seconds 1.5` 平行執行，每台主機一個 process，約 48 分鐘。

**TWSE 有一個日期逾時。** 2026-03-12 以 `ReadTimeout` 失敗，這是營運上的錯誤：沒有
抓到任何東西，所以沒有寫入任何東西。以新的 import id 重跑這單一日期，就匯入了。失敗
的 manifest 作為歷史保留。TPEx 的第 1,628 個 artifact 是 2026-07-10 的重新抓取。

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 舊系統 `institutional_summary` 在兩個市場都已對帳 | PASS | **TWSE：**9,756 列舊系統資料全部比較；5 個日期上有 20 列不同，另有 3 個日期上我們的 6 列在舊系統中缺少。**TPEx：**9,762 列舊系統資料全部比較；2 個日期上有 4 列不同。沒有任何舊系統列在我們這邊缺少。 |
| 每個差異都已分類 | PASS | 類別是 `legacy_captured_before_settlement`、`source_changed_after_legacy_capture` 和 `legacy_file_five_row_layout`。每一個都在下文解釋。只有在每個差異都帶有類別時，腳本才以 0 結束。 |
| 涵蓋完整 | PASS | 兩個市場：1,627／1,627 個日期。 |
| 每個儲存的列都滿足買進 − 賣出 = 淨額 | PASS | 22,778 列中 0 個失敗。 |
| 每個儲存的日期都滿足其市場發布的合計 | PASS | 0 個失敗。TWSE 合計 = 自行買賣 + 避險 + 投信 + 外資，1,627 個日期。TPEx 外資及陸資合計 和 自營商合計 = 它們的縮排列，而 三大法人合計* = 外資 + 投信 + 自營商合計，1,627 個日期。 |
| 壞掉的 2026-07-10 檔案庫檔案重新抓取或被 quarantine | PASS | 實際重新抓取並以 `no_data_for_date` 被 quarantine，raw artifact 保留。它是休市日（見上文）。 |
| 重跑相同內容不產生 revision | PASS | Integration test：去重 8，新建 0，沒有新的證據。 |

## 差異

**`legacy_captured_before_settlement`：3 個日期共 12 列。**

| 日期 | TWSE | TPEx | 舊系統檔案儲存時間（Asia/Taipei） |
| --- | ---: | ---: | --- |
| 2026-02-03 | 4 | 2 | 2026-02-03 18:15 |
| 2026-02-11 | — | 2 | 2026-02-11 22:00 |
| 2026-03-27 | 4 | — | 2026-03-27 14:56 |

舊系統在交易日當天就儲存了這些檔案，早於 `exchange_daily_settled@1` 的 `03:00 D+1`
時刻。2026-02-03 和 2026-02-11 是 Step 20-a 在 `institutional_investors` 中發現的同一批
當日抓取。兩邊的列本身都一致，我們的是確定後的值。舊系統較早的值不匯入
（ADR-0020 §10）。

**`source_changed_after_legacy_capture`：TWSE，3 個日期共 12 列。**

| 日期 | 舊系統檔案儲存時間（Asia/Taipei） |
| --- | --- |
| 2022-09-27 | 2026-01-31 21:20 |
| 2022-10-25 | 2026-01-31 21:31 |
| 2026-01-23 | 2026-02-01 12:55 |

舊系統在確定之後很久才儲存這些檔案。兩邊的列都滿足每一個恆等式，但 TWSE 現在對自營商
避險、投信和外資列以及合計提供的是別的金額。自營商自行買賣列沒有改變。例如
2022-09-27，我們的外資買進和賣出都多了 2,286,897,850，淨額不變，而投信淨額多了
4,711,500。

所以在舊系統於 2026-01/02 抓取、和我們於 2026-09-18 抓取之間，TWSE 改變了這三個日期
提供的值。這個 step 儲存來源現在提供的內容，放在規則時刻之下。這讓更正後的值從 D+1
03:00 起可見，也就是 ADR-0020（後果）和 ROADMAP Step 15 對前向抓取之前的交易所每日
資料所接受的**更正前視偏差**。舊系統檔案不是這個資料集的首次看到證據（CLAUDE.md §32
指名了它們是首次看到證據的僅有兩個資料集：月營收和 XBRL），所以它們無法否證規則。
這個差異在這裡被計數，而不被更正。這是交易所每日資料中第一個經量測的確定後更正案例，
1,627 個 TWSE 日期中有 3 個，也是 Step 27 要量測的 revision 比率的一個資料點。

**`legacy_file_five_row_layout`：TWSE，3 個日期共 6 列。**

在 2021-08-26、2022-09-22 和 2025-03-14，舊系統沒有外資列，也沒有外資自營商列。
這些日期的舊系統檔案（儲存於 2026-01-30 → 2026-02-01）有五列：一個合併的 `外資`
列，沒有 `外資自營商`。舊系統的 parser 丟掉了它沒有代碼可對應的那一列。在全部三個
日期上，舊系統的 `外資` 都恰好等於我們的 外資及陸資(不含外資自營商)，舊系統的合計
也等於我們的。

TWSE 現在對這些日期提供六列的版面。如果五列的版面再次出現，adapter 會以
`schema_mismatch` 讓檔案失敗，而不是去猜 `外資` 是哪一列。audit §4.3 記錄了這個版本。

**舊系統從未有過的已儲存列：TPEx 3,254 列。** 這些是 外資及陸資合計 和 自營商合計
兩個小計，各 1,627 列。舊系統兩者都沒有保留。兩者都通過上面的合計檢查。

## 驗證

從零 migrate 的資料庫：

```text
711 passed, 3 skipped, 1 warning
```

`main` 上的基準是 670 passed 和 3 skipped。差異是 41 個新測試：29 個 unit 和 12 個
integration。

每個測試如何確認先失敗：

- Unit test：29 個全部對照一個拋出 `NotImplementedError` 的 stub adapter 失敗，而且
  在 stub 存在之前就在 import 時失敗。
- Integration test：全部在 importer 存在之前就在 import 時失敗。有 importer、但沒有
  migration 或 CLI 時，12 個中有 9 個失敗：兩個解析測試、release rule 時刻、更正、
  涵蓋、CLI、兩個 downgrade 測試，以及晚到抓取的測試。
- 晚到抓取的測試，以及 Step 20-a「每個接受 `capture_bound` 的資料集都有
  `DATASET_TARGETS` 條目」的永久防護，都在加入 migration 之後、加入條目之前失敗。
  晚到抓取的測試在 `capture_bound` 旁邊找到 `release_rule`，也就是 #30 的洩漏。

`alembic upgrade head` → `alembic check` 沒有回報新的操作。downgrade 到
`c4e7a1d3f9b6` 只移除這個 migration 的列，Step 20-a 的宣告不動。upgrade 會還原它們
（已測試）。有被 quarantine 的 run 存在時，downgrade 防護在任何修改之前拋出 `P0001`
（已測試）。`ruff check` 相對於 `main` 沒有回報新問題。

重現對帳：

```bash
python scripts/reconcile_institutional_summary.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \
    --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
```

`--legacy-archive` 預設為 `~/GitHubLL/my_stock_project/data/raw/institutional_summary`。
只有在分類差異時才需要它，依據每個檔案的儲存時間和內容。

## Code review 發現

對 #31 的 review 在對帳腳本中找到一個問題，在儲存資料的程式中則沒有發現任何問題。在
做任何改動之前，已對照腳本檢查過。

| # | 發現 | 驗證方式 | 處置 |
| --- | --- | --- | --- |
| 1 | **只憑 metadata 就給了數值類別。** 只要舊系統檔案在確定之後儲存、而舊系統的列一致，`classify_value` 就把差異標為 `source_changed_after_legacy_capture`。它從不檢查舊系統的資料庫列是否與它自己的檔案相符，或兩邊的尺度是否一致。我們這邊的系統性錯誤，例如每個金額 ×1000，會被吸收成「來源改變了」。 | 一次探測把每個儲存的金額 ×1000，然後執行提交的腳本。8,520 列 TWSE 和 7,420 列 TPEx 被標為 `source_changed_after_legacy_capture`，另有 1,400 列被標為 `legacy_captured_before_settlement`。腳本仍以 1 結束，但只是因為縮放也破壞了六個五列版面的比對。沒有那三個日期的話，它會以 0 結束。 | **已修正。** 數值差異現在只有在以下條件成立時才取得類別：舊系統的資料庫列等於它的檔案庫檔案，而且那一天至少有一個非零的列與我們的完全一致。這證明尺度和列的對應相同。其他任何情況都是單純的 `value_differs`，會讓執行失敗。在同樣的探測下，現在有 9,205 列 TWSE 和 8,135 列 TPEx 無法解釋，腳本以 1 結束。在真實資料上，每個類別和計數都不變，並以 0 結束。 |

reviewer 也說腳本從不檢查我們的列是否滿足買進 − 賣出 = 淨額。它有檢查，是在每個已
儲存的列上另外檢查：任何失敗都讓執行以 1 結束（22,778 列中 0 個失敗）。在被分類為
`source_changed_after_legacy_capture` 或 `legacy_captured_before_settlement` 的六個日期
中，每一天作為錨點的列都是 自營商(自行買賣) 或其他沒有改變的列之一。

## 已確認的範圍排除

- 20-c 和 20-d 尚未開始。
- 沒有與 Step 20-a 的跨資料集檢查。個股買賣以股為單位，彙總以元為單位，所以沒有
  價格的話，兩者的總和無法比較。
- 單一日期的 CLI 在日期被 quarantine 時，仍以 traceback 結束。quarantine 本身有正確
  記錄。這是共用的生命週期行為，這裡沒有改變。
