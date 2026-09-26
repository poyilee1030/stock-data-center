# Step 39-b 驗收報告

狀態：IN REVIEW (#73)

範圍：歷史產業分類的第二步。兩個交易所依類股查的每日行情寫進新表 `industry_observations`：某交易日某類別的頁面列了哪幾檔，
一檔一列。櫃買（`tpex_otc_quotes`）是當日的分類，用來當已結束上櫃期間的錨點與上櫃對帳；證交所（`twse_mi_index`）以今天的分類重建，
只用來給下市公司最後已知的類別。設計見 ADR-0030 §2，來源實測見 audit §4.15「By-category daily quotes」。分類期間與 API 在 39-c。

## 交付

| | 內容 |
|---|---|
| `src/stock_data_center/v2/industry_observations.py`（新） | 兩個交易所的頁面 parser、`check_sweep`（一個日期的所有頁面是否可能為真）、raw-first 的 `ingest`、`write`（只新增有變的列）、`anchors`（已結束期間的錨點日期）、`reconciliation_dates`、CLI |
| `src/stock_data_center/db/schema_v2.py` | 新表 `industry_observations`，列入 `APPEND_ONLY` |
| `migrations/versions/9f26ff66f8c7_step_39_b_industry_observations.py`（新） | 建表與 append-only trigger；downgrade 在表內有資料時先拒絕 |
| `src/stock_data_center/ingestion/raw_storage.py` | `LocalRawArtifactStore.get(digest, byte_size)`：依 SHA-256 讀回原始檔並驗證（錨點要讀已存的全市場行情檔） |
| `scripts/backfill_industry_observations.sh`（新） | 兩個交易所並行回補，timeout + trap，結束前確認沒有殘留程序 |
| `scripts/report_industry_observations.py`（新） | 覆蓋、錨點、上櫃對帳報告（輸出 `log/step-39-b-report.json`） |
| 測試 | `tests/unit/test_v2_industry_observation_pages.py`（40 條，含參數化）、`tests/integration/test_v2_industry_observations.py`（12）；`test_schema_v2_baseline.py` 加入新表；`test_v2_industry_changes.py` 的 downgrade 改指定到 39-a 之前的版本（原本的 `-1` 在新 head 下只會退掉本步的空表） |
| fixtures | `tests/fixtures/v2/industry/observations/`：2026-09-26 抓的官方回應（櫃買 2022-07-01 的 18、2023-07-03 的 18 與 38、2024-09-24 的 01／20／80、週六 2020-01-04；證交所 2020-04-06 的 19 與 26、2025-07-11 的 17、週六 2020-01-04） |
| 文件 | ADR-0030 §2、audit §4.15、`docs/data_domain_inventory.*`、ROADMAP、CLAUDE.md 快照、README；39-a 改為 MERGED |

`src/` 新增約 500 行。

### 每個欄位為什麼要存

| 欄位 | 少了它會失去什麼 |
|---|---|
| `stock_id`、`source`、`trade_date` | 觀測的身分：哪個交易所、哪一天、哪一檔 |
| `recorded_at` | 同一日期重抓時類別改變（證交所的重建會變）要能分辨先後，System PIT 需要它 |
| `industry_code` | 觀測本身。存代碼而不是頁面標籤：櫃買 33、34 的標籤是空字串，名稱由代碼與日期推得 |
| `fetch_id` | 可追溯到那一頁的原始檔 |

不存名稱、價格（`daily_prices` 已有）、80 管理股票的列（不是產業；只報告）。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 一個日期查每個類別，每頁一列 `fetches` 並保存原始檔 | PASS | `test_a_sweep_writes_each_stock_with_the_page_that_listed_it`；實際 3,118 頁全部 `succeeded`、都有原始檔 |
| 每檔一列，指向列出它的那一頁；不在 `stocks` 的只計數 | PASS | 同上；實際 72,991 列，`outside_universe` 櫃買 405、證交所 868（多半是特別股，例如 2881A） |
| 重跑不再查、不新增列 | PASS | `test_a_rerun_asks_nothing_and_a_refetch_appends_only_a_changed_category`；實際重跑 82 個日期全部 `skipped`、0 次請求 |
| refetch：不變不新增，類別改變的新增 revision | PASS | 同上；實際 `--refetch --purpose correction_check`：櫃買 2023-07-03 806 列、證交所 2025-07-11 1,026 列全部 `unchanged` |
| 一頁失敗：該日期不寫、下次重查；其他日期照寫 | PASS | `test_a_failed_page_leaves_its_date_unwritten_and_asked_again` |
| 一檔在兩個類別、櫃買類別在該日不存在、頁面讀不懂、整個日期沒有任何一檔：整個日期 quarantine | PASS | `test_a_stock_on_two_pages_quarantines_its_date`、`test_an_unreadable_page_quarantines_its_date_not_the_run`、單元 `check_sweep` 5 條與 parser 的 13 條；實際 quarantine 0 |
| 頁面是所問的日期與類別（櫃買標籤、證交所 `type` 與標題） | PASS | `test_a_tpex_page_of_another_category_is_refused`、`test_a_twse_page_of_another_category_or_date_is_refused` 等 |
| 非交易日不查 | PASS | `test_a_date_off_the_calendar_is_not_asked`（兩個交易所對週六都回 OK 的空頁，fixture 為證） |
| 列不可修改；代碼與來源受 CHECK 限制 | PASS | `test_rows_cannot_be_changed_and_hold_a_category_code` |
| 錨點是最後一個列出該檔的全市場行情檔；窗內沒有就沒有錨點 | PASS | `test_a_closed_span_is_anchored_on_the_last_file_listing_the_stock`、`test_a_span_never_quoted_in_the_window_has_no_anchor`；實際見下 |
| 上櫃對帳日期依公告推出 | PASS | `test_the_otc_reconciliation_dates_follow_the_announcements` |
| Migration、downgrade、schema 無漂移 | PASS | `test_the_downgrade_refuses_stored_observations`、`test_the_baseline_builds_exactly_schema_v2`；兩個資料庫 `alembic check`：No new upgrade operations detected |
| 與 legacy `stock_db` 對帳 | PASS | 見下 |

### 測試先於實作

測試寫好時模組、資料表都不存在，單元與整合測試都在收集時 ImportError。實作後單元測試 1 條失敗：測試用 38 當「另一個類別」，
但 38 在 2022-07-01 還不存在，先被 `industry_not_in_effect` 擋下——錯的是測試，改成 24。整合測試 2 條失敗：5905 在 2023-07-03
不在 fixture 的 38 頁（測試預期錯）；`pytest.raises` 放在 savepoint 裡面，交易中止後 release 失敗（測試寫法錯）。

整合測試只在收集時紅過，所以另外逐一把實作改壞 13 種方式，確認每一種都有測試抓到。第一輪有兩種沒被抓到，補強測試後都抓到：

| 改壞的方式 | 失敗的測試 |
|---|---|
| 已完成的日期照樣重查 | `test_a_rerun_asks_nothing_…` |
| 不比對類別、每次都新增 | `test_a_rerun_asks_nothing_…` |
| 不檢查一檔在兩個類別 | `test_a_stock_in_two_categories_is_refused` |
| 不檢查櫃買類別在該日是否存在 | `test_a_tpex_category_that_did_not_exist_yet_is_refused` |
| 證交所改用觀測日（而非抓取日）檢查 | `test_twse_is_checked_against_the_categories_of_the_day_it_was_fetched` |
| 不檢查非交易日 | `test_a_date_off_the_calendar_is_not_asked` |
| 查 13 | `test_every_industry_code_is_asked_but_the_electronics_superset` |
| 不檢查櫃買標籤／證交所標題 | 兩條 `…of_another_category…` |
| 不檢查整個日期都空 | `test_a_sweep_with_nothing_on_any_page_is_refused` |
| 錨點把下市當天的檔也算進去 | `test_a_closed_span_is_anchored_…`（第一輪沒抓到：原測試的股票不在那天的檔裡；改用兩天都有的 6488） |
| 一頁失敗後繼續查同一日期的其他頁 | `test_a_failed_page_…`（第一輪沒抓到：結果相同、只多了請求；補上「查到失敗那頁就停」） |
| 錨點包含仍在掛牌的期間 | `test_a_closed_span_is_anchored_…` |

全套測試：1138 passed。ruff：改動與新增的檔案沒有問題。

## 真實資料

`stockdc_backfill` 升到 `9f26ff66f8c7`（只新增一張表），再跑 `scripts/backfill_industry_observations.sh`
（櫃買 `--anchors --reconcile`、證交所 `--anchors`，兩者並行），輸出在 `log/step-39-b-*.jsonl`；
之後另查櫃買 2021-06-02、06-09、06-10（見「上櫃對帳」）。

| | 櫃買 `tpex_otc_quotes` | 證交所 `twse_mi_index` |
|---|---|---|
| 日期 | 58（錨點 40、對帳 16，重疊 1；另查 3） | 27（錨點） |
| 每日期的請求 | 37（36 個類別 + 80） | 36 |
| 頁面（`fetches`，不含下面 refetch 的 73 頁） | 2,146 | 972 |
| 寫入列 | 46,740 | 26,251 |
| 不在 `stocks` | 405 | 868 |
| quarantine／失敗 | 0／0 | 0／0 |
| 80 管理股票 | 每個日期都是 0 檔 | 不查 |

櫃買 57 個有全市場行情檔的日期（2026-09-15 在 daily-price 回補範圍之外），每一個日期全市場檔列出的每一檔上櫃普通股都恰好出現在
一個類別頁，沒有漏掉的。

### 錨點

72 段已結束的期間（上市 31、上櫃 41，其中 13 段是轉上市）：

- 71 段有錨點，並在錨點日期觀測到類別。**5259**（上市，2020-01-09 下市）在窗內的全市場檔都不存在，沒有錨點，照實報告。
- 錨點日期常早於下市日：只有 13 段轉上市與少數下市在下市前一個交易日仍有行情；例如 4712 最後出現在 2024-02-05（2024-09-25 下櫃）、
  6404 在 2023-11-21（2024-07-02）、2499 在 2020-04-06（2020-11-10）。
- 39-a 留下的 4 條已結束的變更鏈都接得上：錨點日期的觀測等於公告推得的類別。

| 股票 | 市場 | 錨點日期 | 觀測 | 公告推得 |
|---|---|---|---|---|
| 4712 | 上櫃 | 2024-02-05 | 02 食品工業 | 02（2024-06-03 才改為其他） |
| 8420 | 上櫃 | 2024-11-20 | 37 運動休閒 | 37 |
| 8476 | 上櫃 | 2023-10-30 | 35 綠能環保 | 35 |
| 6806 | 上市 | 2026-06-22 | 35 綠能環保 | 35 |

- 驗收條件裡的例子：2888（2025-07-11）、2867（2026-08-19）、2809（2025-09-17）都是 17 金融保險業；2448 在 2020-12-23 是 26 光電業。

### 上櫃對帳（原始一致度；分類期間與正式報告在 39-c）

16 個對帳日期（2020-01-02、七次實施日的前一個交易日與當日、2026-09-15），每一檔的觀測與公告推得的類別比對：之後還有變更的取下一次變更的原類別，
否則取錨點（掛牌中取今天的 ISIN，已結束取錨點觀測）。

| 結果 | 筆數 |
|---|---|
| 一致（依下一次變更） | 825 |
| 一致（依今天的 ISIN） | 11,918 |
| 一致（依錨點觀測） | 269 |
| 不同 | 10 |

10 筆不同全在 **2021-06-01**，正好是櫃買 110 年公告（證櫃監字第11002006321號，內文「調整後之產業類別實施日期：110年6月1日」）的 10 家公司：
那天的類股行情仍是原類別。另外查了 06-02、06-09、06-10，以二分搜尋確認 6240 在 2021-06-10 才出現在「其他」：
**這 10 家都在 2021-06-10 才改成新類別，而 06-09 與 06-10 之間只有這 10 家改變**。其他六次實施日當天的行情都已是新類別。
分類：櫃買類股行情晚了 7 個交易日才套用 2021 年的調整，公告本身沒有缺漏；分類期間照公告的實施日期（ADR-0030 決定），
39-c 的對帳要把這 10 筆歸在這個原因之下。

### 與 legacy `stock_db` 對帳（§78）

legacy 沒有產業歷史，`stock_info` 只有一份目前的快照，也不含下市公司：

- 2026-09-15 的 887 檔上櫃觀測：885 檔與 legacy 同類別、0 檔不同、2 檔不在 legacy。
- 13 段轉上市的錨點：12 段與 legacy 同類別；5306 在上櫃的最後一天（2022-03-07）是 20 其他，legacy 是 37 運動休閒——
  它 2022-03-08 轉上市，證交所 112 年公告把它由其他改為運動休閒（2023-07-03），legacy 快照是之後的分類。
- 59 段下市的錨點：legacy 沒有這些公司。

## 已知限制

- 證交所的類股行情是以今天的分類重建，只能給下市公司「最後已知」的類別；它們在 2023-07-03 之前若曾被調整，只能靠公告往回推（39-c）。
- 5259 在窗內沒有錨點。
- 80 管理股票在所有查過的日期都是 0 檔；出現時只報告、不存，那一檔當天的產業由 39-c 決定怎麼處理。
- 櫃買 2021 年的調整在類股行情上延後 7 個交易日（見上）。

## 延後的工作

- 39-c：查詢時推出的分類期間、`industry-classifications` API、`/v1/stocks` 的下市公司產業、完整的變更鏈一致性與上櫃對帳報告。
- 前向抓取（Step 28）：新下市的公司要在下市後補一次錨點（重跑本 CLI 的 `--anchors` 即可）。
