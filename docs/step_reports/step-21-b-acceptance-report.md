# Step 21-b 驗收報告

狀態：MERGED (#35)

範圍：借券。這個 step 新增 TWSE `marginTrading/TWT93U`（`twse_twt93u`）和 TPEx
`margin/sbl`（`tpex_margin_sbl`）adapter、importer、source policy 與涵蓋宣告、CLI
`securities-lending`、兩個市場 2020-01-02 → 2026-09-11 的 backfill，以及與舊系統
`margin_sbl` 的對帳。

Schema 影響：`securities_lending_versions` 從 Step 7 起就存在，沒有改動。一個
migration `c6e2a8d4f1b7` 只新增列（catalog、兩個來源、release rule 對應、兩個涵蓋
宣告），downgrade 在已有 ingest run、manifest 或版本時會在修改前拒絕。
PIT 影響：沒有新的影響。兩個來源都遵循 `exchange_daily_settled@1`，資料集加入
`DATASET_TARGETS`。依據：TWSE 自己的註解說明這張表每晚約 20:30 和 22:30 更新兩次；
舊系統每日工作在 D+1 03:00 之前存下 137 個 TWSE 和 138 個 TPEx 檔案（檔案 mtime）；
其餘約 1,490 個是 2026 年 1–2 月批次重抓的，與時間無關。唯一晚到的檔案（TPEx
2026-09-02，D+1 09:54 存檔）無法區分是晚發布還是抓取失敗（audit §7.2）。
規模：`src/` 改動 +590 行，低於約 800 行的拆分門檻（`CLAUDE.md` §1）。另外提交
一個 migration、一個對帳腳本、六個 fixture 和兩個測試檔。

## 這個 step 中做出的來源決策

每項決策都記錄在 ROADMAP Step 21-b 和 audit §4.5「Step 21-b findings」。

1. **單位是股，不做換算。** ROADMAP 要求開工時先查 TWT93U 的單位：TWSE 每個交易日的
   `hints` 都是 `單位：股`，adapter 每次檢查；TPEx 的 JSON 沒有標示單位，但呈現它的
   頁面宣告 `subtitle2:"單位：股"`，而且它的融券組逐列等於 `margin/balance`（標示
   `(張)`）的張數 × 1,000。對帳腳本逐日重讀 raw 檔確認這件事，所以 TPEx 的單位每天
   都有證明，不是從數值大小推斷（CLAUDE.md §72）。
2. **21-a 的交易單位例外不適用。** 這張表以股發布：008201 在 2020-01-02 的融券限額是
   387,275 股，恰好是發行量 1,549,100 的 25%；MI_MARGN 同一天是 3,872 張（一張 100 股）。
3. **欄位對應。** 借券賣出組寫入契約欄位：前日餘額 → `previous_balance`、當日賣出 →
   `borrowed`、當日還券 → `returned`、當日調整（TPEx 當日調整數額，有正負）→
   `adjustment`、當日餘額 → `balance`、次一營業日可限額（TPEx 次一營業日可借券賣出
   限額）→ `next_available_limit`、備註 → `note`（去掉空白，空白為 NULL）。
4. **`next_limit` 是融券組的限額。** TWSE 次一營業日限額、TPEx 限額：以股為單位的
   精確融券限額；`margin_trading.short_next_limit` 只存到整張（無條件捨去），這一點
   在每一列都驗證過。這是契約裡唯一有來源的「限額」欄位，audit 原本就把它列為
   有來源。
5. **融券組不重複儲存。** 它的前日餘額、賣出、買進、現券、今日餘額，就是 21-a 已經
   存在 `margin_trading` 的值；對帳證明兩者逐列相等。舊系統 `margin_sbl` 的
   `margin_short_*` 對帳到 `margin_trading`，和 inventory 原本的處置一致。
6. **不儲存的欄位。** 名稱，以及 TWSE 最後一列沒有代號的 `合計`。

## 基準

舊系統 `stock_db.margin_sbl`，2020-01-02 → 2026-09-11，單位是股：

| 市場 | 列數 | 證券數 | 日期數 |
| --- | ---: | ---: | ---: |
| `sii` | 1,606,781 | 1,112 | 1,627 |
| `otc` | 1,166,534 | 876 | 1,627 |

兩個市場的 header 在整段期間都只有一種（抽查 2020–2026 共 11 個日期，backfill 的
1,627 個 manifest 都是同一個 variant）。

## 執行

```text
                  versions   securities  dates   raw artifacts   evidence (release_rule)
twse_twt93u      1,850,682        1,363  1,627           1,627   1,850,682, unknown 0
tpex_margin_sbl  1,322,974        1,028  1,627           1,627   1,322,974, unknown 0
raw artifacts 3,254, 342 MB
```

TWSE 有 2 天抓取逾時（2024-11-18、2025-04-02），沒有寫入任何東西，以同一個 import id
補齊。

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 舊系統 `margin_sbl` 已對帳 | PASS | **TWSE：**比較 1,605,813 列；另外 968 列是舊系統存錯日期的檔案（見下文）。**TPEx：**比較全部 1,166,534 列。兩個市場的借券欄位（前日餘額、賣出、還券、餘額）與舊系統**沒有任何差異**，也沒有只在舊系統的列。 |
| 每個差異都已分類 | PASS | 見下表。`scripts/reconcile_securities_lending.py` 以 0 結束；通過條件是白名單，只有已證明的分類可以通過。 |
| 涵蓋完整 | PASS | 兩個市場：1,627／1,627 個日期。 |
| 每個儲存的列都滿足餘額推算 | PASS | 前日餘額 + 當日賣出 − 當日還券 + 當日調整 = 當日餘額。每個（證券、日期）的最新 revision 共 3,173,656 列；21 列 TWSE 是停止買賣當天歸零（見下文），其餘 0 個失敗。 |
| 每個 raw 檔的融券組都等於 `margin_trading` | PASS | TWSE 1,844,392 列、TPEx 1,284,398 列逐欄相等，限額捨去到整張後相等。不在融資融券表的證券：融券組全為 0（TWSE 6,279、TPEx 38,575 列），或在另一個市場的融資融券表（11 列）；1 列已命名的限額差異。 |

差異分類（全部是舊系統的融券欄位；借券欄位沒有差異）：

| 分類 | TWSE | TPEx | 證明 |
| --- | ---: | ---: | --- |
| `short_side_absent_from_margin_table:file_all_zero` | 5,992 | 38,568 | 證券不在融資融券表（幾乎都是備註 `Y` 未取得信用交易資格），v1 沒有 `margin_trading` 列；raw 檔的融券組和舊系統一樣全為 0。 |
| `short_side_absent_from_margin_table:other_market_margin_table` | 9 | 1 | 轉市場的前一天，兩個交易所的借券表都列出該證券；它的融券組在另一個市場的融資融券表，值與 raw 檔、舊系統三者相等。 |
| `legacy_captured_another_date:legacy_rows` | 968 | 0 | 舊系統 2022-10-06 的 `sii.csv` 其實是 2022-10-18 的資料：與同一天只有 9.1% 一致，與我們 2022-10-18 的每一列都相同。 |

我們有、舊系統沒有的列：TWSE 243,760 列、TPEx 156,433 列屬於完全不在舊系統中的證券；
另外 TWSE 5 列、TPEx 7 列是舊系統那天沒有該證券。

## 對帳中找到的來源現象

原本以為借券賣出餘額永遠滿足來源印出的公式。實際上：

- **停止買賣當天歸零。** 21 列 TWSE 資料備註含 `!`，當日餘額變成 0，卻沒有還券或
  調整（例如 2409、3481 在 2022-09-29 減資停止買賣）；下一個日期從 0 開始，鏈沒有斷。
  對帳只接受這個完整的樣式：備註含 `!`、餘額 0、當日沒有任何流量、下一個日期的前日
  餘額是 0。資料照發布儲存。
- **轉市場的前一天兩邊都列出。** TWSE 註解說明第二次更新會納入「原為上櫃次日將轉為
  上市交易之個股」。所以該證券那一天在 `twse_twt93u` 和 `tpex_margin_sbl` 都有一列，
  值相同；兩個來源各自保存，不合併（CLAUDE.md §30）。
- **限額可能差一天。** 1721 在 2021-02-05：MI_MARGN 已經換成新限額 42,456 張，TWT93U
  還是 47,248,750 股，下一個交易日才是 42,456,680。流量與餘額都相同。對帳以
  `KNOWN_LIMIT_DIFFERENCES` 具名列出。
- **最後一天。** 59 個證券在 MI_MARGN 的最後一天（下市、終止上市），TWT93U 前一天就
  不再列出；對帳計數，不視為失敗。

## 驗證

從零 migrate 的資料庫：

```text
896 passed, 3 skipped, 1 warning
```

`main` 上的基準是 854 passed。差異是 42 個新測試：27 個 unit、15 個 integration。

每個測試如何確認先失敗：

- Unit test：在 adapter 存在之前就在收集時失敗；之後對照 stub（`parse` 直接失敗、
  `resource` 回空 URL），26 個失敗，只有 source code 常數的測試通過。
- Integration test：在 importer 存在之前就在收集時失敗；有 importer、沒有 migration、
  `DATASET_TARGETS` 條目和 CLI 子命令時，9 個失敗。晚到抓取的測試只移除
  `DATASET_TARGETS` 條目時失敗（932 列 `release_rule` 洩漏），恢復後通過。

`ruff check` 對新增和改動的檔案沒有回報新問題；`adapters/__init__.py` 未排序的
`__all__` 在 `main` 上就已存在。

重現對帳：

```bash
python scripts/reconcile_securities_lending.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \
    --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
```

## 踩到的坑

- **背景 backfill 的 PID。** 用 `setsid nohup … &` 啟動時，`$!` 是 setsid 本身的 PID；
  setsid fork 之後就結束了，所以用它等待會立刻回來，看起來像 backfill 已經結束。要用
  `pgrep` 找到真正的 worker 再等待。
- **對帳第一版的錯誤分類。** 不在融資融券表、但融券組不是 0 的 10 列，一開始落到
  `value_differs:source_changed_after_legacy_capture`：那個分類的證明（舊系統等於自己
  的檔案、同檔另一列與我們一致）對這些列也成立，但原因根本不是來源更正。改為先檢查
  另一個市場的融資融券表，找不到就讓對帳失敗。

## 範圍外的發現

- **21-a：008201 在例外日期之後仍出現。** MI_MARGN 在 2022-07-11 → 2022-08-10 仍列出
  008201，值全為 0（`margin_trading` 裡這 23 天是 v1 存下的 0，數值不受交易單位影響）。
  但 `TWSE_LOT_SHARES` 的證據範圍只到 2022-07-08，所以用 `twse-mi-margn:v3` 重抓這段
  日期時整個檔案會以 `unverified_trading_unit` 失敗。已存的資料正確；要在修改 21-a
  adapter 的 step 中決定如何處理（例如全為 0 的列不需要交易單位）。

## 已知限制

- **不在融資融券表的證券沒有 v1 的融券欄位。** 舊系統 `margin_sbl` 對這些列存 0；v1
  的 `margin_trading` 沒有那一列。證明顯示這些值全為 0，或在另一個市場的表裡，所以
  沒有資料遺失。
