# Step 38-a 驗收報告

狀態：IN REVIEW (#66)

範圍：歷史股票清單的第一步。`stocks` 只放公司身分，新表 `listings` 放每段掛牌期間；清單收 2020-01-02 以後下市、
官方來源證明是普通股的公司；各 adapter 的抓取範圍改成「有尚未結束的掛牌期間」，所以範圍不變。設計見 ADR-0028，
來源實測見 audit §4.11。

## 交付

| | 內容 |
|---|---|
| `src/stock_data_center/v2/listings.py`（新） | 五種頁面的解析、`assemble`（純函式，與輸入順序無關）、`write`、`refresh`、`listed_stock_ids`、CLI |
| `src/stock_data_center/db/schema_v2.py` | `stocks` 拿掉 `market`、`listed_on`；新增 `listings` |
| `migrations/versions/17d077a6bbe6_step_38_a_listing_spans.py`（新） | 建表、搬資料、downgrade 前先檢查 |
| `src/stock_data_center/v2/universe.py` | 保留 ISIN 解析；`load_universe` 由 `listings.refresh` 取代 |
| `v2/backfill.py`、`v2/exchange_daily.py`、`v2/corporate_actions.py`、`v2/financial_reports.py` | 抓取範圍改讀 `listed_stock_ids` |
| `src/stock_data_center/api/__init__.py`、`api/openapi.py` | `/v1/stocks` 回傳 `listings`、`date` 參數、新的存活者偏差揭露 |
| 測試 | `tests/unit/test_v2_listings_assembly.py`（27）、`tests/integration/test_v2_listings.py`（18）、API 2 條；14 個既有測試檔跟著改（fixture 改成 `stocks`＋`listings`、baseline 的表清單、文件契約） |
| fixtures | `tests/fixtures/v2/listings/`：2026-09-25 抓的官方回應節錄（TWSE 兩表、TPEx 兩表、TPEx 2020 未分頁與分頁、ISIN 清單與查詢） |
| 文件 | ADR-0028（新）、ADR-0026 狀態、audit §4.11／§4.14／§5、`docs/security_daily_market.md`、`docs/schema.md`、`docs/api.md`、`docs/architecture.md`、`docs/data_domain_inventory.*`、ROADMAP、CLAUDE.md 快照 |

`src/` +714／−127 行（`listings.py` 586 行為新檔）。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 2020-01-02 至今兩個交易所的每一筆下市都有處置，依來源報告筆數 | PASS | 97 筆（TWSE 42、TPEx 55）：72 段保留、24 段 quarantine、1 段創新板排除；清單見下 |
| 凌陽創新（5236）兩段期間 | PASS | `test_a_transfer_between_markets_is_two_spans`（fixture）；實際 refresh：`otc 2021-07-29 → 2026-07-16`、`sii 2026-07-16 →` |
| 加入下市公司前後，每個 adapter 的請求集合相同 | PASS | `test_every_adapter_asks_for_its_stocks_through_the_open_spans`、`test_a_delisted_company_is_out_of_the_exchange_daily_scope`、`test_the_adapters_read_only_open_spans`；實際：未結束期間 1,947 段 = refresh 前的 `stocks` 1,947 檔 |
| 沒有任何證券類別是從代號格式推論的 | PASS | `test_an_unproven_category_is_quarantined_not_guessed`（含四碼 TDR 9188）、`test_a_lookup_naming_another_category_is_left_out` |
| `listed_on` 與 legacy `stock_info.listing_date` 對帳，差異逐一分類 | PASS | 見下表 |
| 重跑不產生 `listings` 變更；每次抓取都有 `fetches` 列 | PASS | `test_a_second_refresh_changes_nothing`；實際第二、三次 refresh 的 2,019 段、2,006 家逐列相同；每個頁面一列 `fetches`、都有原始檔 |
| Migration 與 downgrade | PASS | `test_the_migration_turns_each_stock_into_an_open_span`、`test_the_downgrade_refuses_a_closed_span`；實際資料 1,947 檔搬成 1,947 段；`alembic check` 無漂移 |
| API：`listings` 與 `date` | PASS | `test_the_stock_list_carries_every_listing_span`、`test_the_stock_list_on_a_date_is_the_companies_listed_that_day`；實測見下 |

### 測試先於實作

測試寫好時 `listings` 模組、資料表與 API 參數都還不存在：單元測試在收集時就失敗（模組層級解析 fixture），整合測試
與 API 的新測試全紅。實際 refresh 發現 TPEx 分頁後，先加 3 條單元測試與 1 條整合測試的斷言，4 條都紅，再實作。
之後逐一把實作改壞：

| 改壞的方式 | 失敗的測試 |
|---|---|
| 不看 `創新板` 備註 | 創新板的 2 條 |
| 上市中期間用 ISIN 日期 | `test_the_listing_table_date_wins_over_the_isin_date` |
| 證明不了的類別照收 | 18 條 |
| 範圍改成所有期間 | 範圍的 2 條 |
| 保留時間窗之前的期間 | 2 條 |
| 不核對宣告筆數 | 分頁的 2 條 |
| 不核對分頁回應的前 10 筆 | `test_a_paged_answer_that_disagrees_with_the_first_page_is_refused` |
| 兩筆上市列之間沒有下市時照樣配對 | **沒有測試失敗**；補 `test_two_listings_with_no_delisting_between_are_quarantined`，確認它在這個改壞下失敗、還原後通過 |

全套測試：949 passed（main 907）。ruff：改動的檔案沒有新問題（`tests/unit/test_phase3_contract_docs.py` 的 import 排序是 main 就有的）。

## 真實資料

在暫存資料庫 `stockdc_step38a` 做：先建到上一版 schema、複製 `stockdc_backfill` 的 1,947 檔 `stocks` 與它們的 fetch，跑
migration，再跑 `python -m stock_data_center.v2.listings` 三次。沒有動 `stockdc_backfill`：API 容器正在讀它，
合併前先改它的 schema 會讓線上的 `/v1/stocks` 壞掉。

**Refresh（第二次起）**：86 次請求（ISIN 2、TWSE 2、TPEx 44＋10 次分頁補抓、ISIN 查詢 28），約 4 分鐘。

| | 數量 |
|---|---:|
| 公司 | 2,006（上市中 1,947＋已下市 59） |
| 期間 | 2,019（未結束 1,947、已結束 72，其中 13 段是轉市場前的期間） |
| 已結束期間的證明 | 上市列 68、ISIN 查詢 4（2809、2823、4712、5306） |
| 早於時間窗、不存 | 430 |
| 創新板排除 | 32 |
| quarantine：證明不了類別 | 24 |
| 警告：沒有上市列的再上市 | 1（2301：光寶電子 2002 年下市，光寶科沿用代號） |

quarantine 的 24 段：TDR 9188、910482、911616、912398；TPEx 911613；上市早於交易所表、ISIN 已註銷的 19 家
1507、1701、1724、1902、2358、2841、3089、3144、5102、5304、5349、5371、5383、5820、6238、6247、6287、8913、8934。

**與成交紀錄交叉比對**（`stockdc_backfill` 的 `daily_prices`，只看今天的普通股）：

| 日期 | `date` 回傳 | 有日行情 | 有期間沒行情 | 有行情沒期間 |
|---|---:|---:|---:|---:|
| 2020-01-02 | 1,698 | 1,639 | 3（3073、3086、8080 停牌） | 0 |
| 2023-06-30 | 1,781 | 1,758 | 3（1435、4192、4806） | 2（6869、6873 的創新板期間） |
| 2026-09-11 | 1,945 | 1,936 | 9（停牌，audit §4.11） | 0 |

`date` 回傳的比有行情的多，多出的是已下市公司（它們的行情還沒收，38-b）與停牌股票。「有行情沒期間」只有創新板
轉一般板前的期間：那段行情已經存在各資料表，不在 universe 裡（已知限制）。

**`listed_on` 對帳 legacy `stock_info.listing_date`**（上市中的 1,947 檔）：

| 分類 | 檔數 | 說明 |
|---|---:|---|
| 相同 | 1,212 | |
| 不同，我們取交易所上市表、legacy 是 ISIN 日期 | 22 | TPEx 12、TWSE 10；抽查的都是交易所日期等於第一筆成交（例如 3718 是 2026-09-03，legacy 1999-01-20） |
| 我們為 NULL（上市早於交易所表），legacy 是 ISIN 日期 | 709 | 刻意不補，ADR-0028 §3 |
| legacy 沒有 | 4 | 2938、3718、7825、7856：legacy 快照之後才上市 |
| legacy 有、我們沒有上市中期間 | 1 | 5371 中強光電：2026-09-03 下櫃（轉投控 3718），類別證明不了 |

**API**（`stockdc_step38a`）：`/v1/stocks?stock_id=5236` 回傳兩段期間、最上層 `market=sii`、`listed_on=2026-07-16`；
2809 回傳 `sii`、`listed_on=null`、`delisted_on=2025-10-01`、最上層 `market=null`；`date=2019-12-31` 回 400；
全部 2,006 家。

## 踩到的坑

- **原本以為** TPEx 按年查的參數是 `year`（ROADMAP 的計畫這樣寫）。它會被忽略、回今年；參數是 `date`，而且回應要核對年份。
- **原本以為** TPEx 下櫃表一次回傳整年。它一次只給 10 筆，`totalCount` 才是整年：第一次 refresh 因此少了 2005–2020 中
  10 個年份的下櫃，22 段上櫃期間看起來沒有結束（被 quarantine 成 `listed_not_on_isin`，正是這個異常讓它被發現）。
  分頁參數要從網站的 `tables.js` 找：`paging-size`、`paging-offset`，而分頁回應沒有欄位名稱。現在每個頁面都必須等於
  它宣告的筆數。
- **原本以為** ISIN 清單的 `上市日` 就是上市日（`stocks.listed_on` 一直這樣存）。它是目前這張 ISIN 紀錄的日期：
  控股公司轉換、創新板轉一般板、某些重新掛牌都會改掉它。
- **原本以為** ISIN 只列上市中的證券。`class_main.jsp` 查得到仍公開發行的下市公司（`市場別` 公開發行、`有價證券別` 普通股），
  這是證明下市公司類別的主要來源；已註銷的公司會轉到 `class_nofind.html`。
- **原本以為** 四碼代號就是股票。9188 精熙-DR 是 TDR。
- 測試檔 `tests/unit/test_v2_listings.py` 與 `tests/integration/test_v2_listings.py` 同名，pytest 收集時衝突；單元測試改名
  `test_v2_listings_assembly.py`。

## 已知限制

- 19 家下市公司證明不了類別，不在清單上（名單見上）。
- 709 檔上市中股票的 `listed_on` 為 NULL；以前 `/v1/stocks` 會回 ISIN 日期（例如 2330 的 1994-09-05），現在回 `null`。
- 已下市公司各資料表都還沒有資料：清單有、行情沒有，存活者偏差仍在（38-b）。
- 創新板轉一般板的公司，創新板期間的資料已經在各資料表裡（6757 從 2023-08-15 起），不在它的期間內。
- 下市公司的名稱：ISIN 查詢還查得到就用它（2823 顯示今天的「凱基人壽」），否則是交易所最後一次公布的名稱（TPEx 是公司全名）。
- 要持續記到新的上市與下市，需要 Step 28 把 refresh 排進每日工作。

## 部署（合併後）

`stockdc_backfill` 要依序：`alembic upgrade head`、`python -m stock_data_center.v2.listings`、`scripts/api_up.sh`。
migration 之後、容器重建之前，舊容器的 `/v1/stocks` 會失敗，所以三步要連著做。
