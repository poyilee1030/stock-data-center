# Step 26-c 驗收報告

狀態：IN REVIEW

範圍：`institutional_cumulative_flow:v1`，移植舊系統 `calculate_trust_holding.py`、`calculate_dealer_holding.py`，
沿用 26-b 的增量執行器。

## 交付

| | 內容 |
|---|---|
| schema | `institutional_cumulative_flow`：`trust_cumulative_net_shares`、`dealer_cumulative_net_shares`（bigint）、`trust_cumulative_net_ratio`、`dealer_cumulative_net_ratio`（double，可為 NULL），key `(stock_id, source, trade_date)`，加 `computed_at`；migration `0f6386ea08db`，降級不設防 |
| `src/stock_data_center/v2/cumulative_flow.py` | 累加與比率的純函式 |
| `src/stock_data_center/v2/derived_store.py` | 資料集定義、`HOLDING_SOURCE` 對照、計算 |
| scripts | `reconcile_institutional_cumulative_flow.py`（新）；`verify_derived_store.py` 加上累積流量與 `--dataset` |
| 測試 | `tests/unit/test_v2_cumulative_flow.py`（11）；`test_v2_derived_store.py` 加 7 條；`test_schema_v2_baseline.py` 的衍生表清單 |
| 文件 | ROADMAP §20、Step 26（26-c 小節）；CLAUDE.md 快照；`derived_data.md`、`schema.md`、`institutional_financing.md`、domain inventory（md／json）、README；26-b 報告標為 MERGED |

`src/` +120／−1 行。

## 每一欄的理由

- 兩個累積淨股數：舊系統下游讀的值。沒有固定視窗，拿掉它，每次查詢都要從每支股票的法人歷史起點加總。
- 兩個比率：舊系統下游讀的值。拿掉它，下游要自己找對同一天、同一市場的外資持股來源（`twse_mi_qfiis` 或
  `mops_t13sa150_otc`），還要照舊系統的方式四捨五入。
- 不存 `issued_shares`：它是 `foreign_holdings` 的觀測值，不重複存。舊系統的 `name` 與 `pced_*` 也不存。

## 公式

- 法人檔的每一天一列（有成交但沒有法人列的日子沒有列，累積值不變），淨額從序列第一天起累加，缺值當 0。
- 比率：`ROUND((累積 / 發行股數 * 100)::numeric, 4)`。舊系統兩個欄位都是 double，所以除法是 double，轉 numeric
  時保留 15 位有效數字，再以 PostgreSQL 的 numeric ROUND 四捨五入遠離零。移植照做：`format(x, ".15g")` 轉
  `Decimal`、`ROUND_HALF_UP`。當天沒有外資持股列或發行股數為 0 時是 NULL。
- 每個市場一條序列（舊系統依 `(market, symbol)` 分區，CLAUDE.md §30 也是）；轉板的股票在新市場從 0 起算。
- **沒有暖機緩衝**：加總永遠記得第一天，所以每次重寫的序列都從第一列讀起。整數加總沒有殘差，增量與整段逐位相同。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 與舊系統的表對帳，差異逐一分類 | PASS | 0 筆無法歸因（見下方） |
| 增量與整段重算逐位相同 | PASS | 1,959 條序列 × 6 個起點，6,810,793 列 0 筆不同；`test_an_incremental_sum_equals_the_full_one_beyond_the_buffer` |
| 沒有用到未來的資料 | PASS | `test_a_cumulative_value_uses_no_input_dated_after_it`：D 之前的累積值與比率，在加入之後的淨額與發行股數、整段重算後不變 |
| 每張新表的理由 | PASS | 上方「每一欄的理由」 |

### 測試先於實作

測試在 `cumulative_flow`、新表與資料集都不存在時寫好：unit 與整合測試因 import 失敗而紅，
`test_the_derived_tables_are_the_only_ones_after_the_baseline` 因表不存在而紅。純函式以舊系統 `stock_db` 的真實輸出為
fixture（2330、8069、6446 的 2020 年；6446 那年在舊系統沒有外資持股列，比率全為 NULL），兩個法人、245 天逐位相同。
之後逐一把實作改壞：

| 改壞的方式 | 失敗的測試數 |
|---|---:|
| 只加總暖機緩衝內的日子 | 1 |
| 外資持股的來源不對應到法人序列 | 3 |
| 外資持股不列為輸入 | 1 |
| 比率用最新的發行股數，而不是當天的 | 3 |
| 四捨五入改成銀行家捨入 | 2 |
| 不經 15 位有效數字直接四捨五入 | 3 |

全套測試（從零 migrate 的測試資料庫）：749 passed。`alembic check` 無差異。ruff：新檔案 0 個問題。

## `stockdc_backfill` 上的實測

```text
alembic upgrade head                                     6ecc3eefb103 -> 0f6386ea08db
derived_store --dataset institutional_cumulative_flow    1959 series, 2,600,961 rows, 1m35s
（再跑一次）                                               0 series, 0 rows
verify_derived_store.py --dataset institutional_cumulative_flow   116 秒，exit 0
```

表大小 349 MB；13 支股票共 8,780 列沒有比率（當天沒有外資持股列）。

### 舊系統對帳

```text
scripts/reconcile_institutional_cumulative_flow.py --start 2020-01-02 --end 2026-09-11
```

| | 數量 |
|---|---:|
| 共同的 (股票, 市場, 日期) | 2,600,387 |
| `ours_only` | 574，全是只有我們的法人檔有的日子 |
| `legacy_only` | 67，全是只有舊系統的法人檔有的日子 |
| 比較的（鍵, 法人） | 5,200,774 |
| 累積淨股數不同，且恰好等於兩邊淨額差的累計 | 1,766,020 |
| 比率不同：舊系統的外資持股檔屬於別的日期（TWSE 2022-06-28、2024-12-19） | 372 |
| 比率不同：MOPS 沒有這支股票（5236 在 TPEx 的 1,203 天） | 2,406 |
| 無法歸因 | 0 |

- **累積差異很多，但原因很少。** 一天的淨額差會帶到之後的每一天，所以不逐筆判斷，而是要求差額到股為止等於
  「到那天為止兩邊淨額差的累計」（只有一邊有的日子算它的全部淨額）。1,766,020 筆全部相等。淨額差本身是 Step 20-a
  已歸類的輸入差異：舊系統把另一天的法人檔存成六個 TWSE 日期（`legacy_captured_another_date`）、`legacy_row_incomplete`
  等；574／67 筆單邊的鍵也是同一批日子。
- **比率只在累積值相同時比較。** 372 筆是 Step 20-d 的 `legacy_file_matches_no_date_in_window`：舊系統這兩天的
  外資持股檔對不上任何日期，除以的是別的發行股數。2,406 筆是 Step 20-d 記載的 MOPS 生存者偏差：MOPS 以今天的清單
  重建過去，5236 凌陽創新 2026-07-15 轉到 TWSE，所以它在 TPEx 的每一天都沒有發行股數，我們的比率是 NULL。
  schema v2 不保留 `tpex_insti_qfii`（ADR-0027），所以沒有別的來源可補。

## 踩到的坑

- **原本以為** 累積差異要逐筆找原因。一天錯的淨額會一路帶下去，逐筆看是 176 萬筆；改成「差額必須恰好等於淨額差的
  累計」，一個條件就驗完，而且比逐筆歸類更嚴格。
- **原本以為** 比率可以用 Python 的 `round`。舊系統是 double 除法、轉 numeric（15 位有效數字）、numeric ROUND（四捨五入
  遠離零）；`round` 是銀行家捨入，`Decimal(float)` 會帶進 17 位的二進位誤差，兩種都有測試會抓。

## 已知限制

- 5236 在 TPEx 期間（2021-07-29 起 1,204 天，截至 2026-09-11 的對帳視窗內 1,203 天與舊系統不同）沒有比率：MOPS 的生存者偏差，沒有替代來源。
- 累積值不是持股：沒有期初持股，從序列第一天（2020-01-02 或上市首日）起算。

## 延後

- 26-d–26-f：股權集中度、融資融券與借券指標、估值指標。
