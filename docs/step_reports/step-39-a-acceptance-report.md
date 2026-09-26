# Step 39-a 驗收報告

狀態：IN REVIEW (#72)

範圍：歷史產業分類的第一步。兩個交易所的產業類別調整公告寫進新表 `industry_changes`，公告中的一次變更一列；
產業代碼、類別改名與存續期間是程式常數；公開時點是 release rule `industry_announcement_next_day@1`。
設計見 ADR-0030，來源實測見 audit §4.15，owner 決定見 ROADMAP Step 39「決定」。

## 交付

| | 內容 |
|---|---|
| `src/stock_data_center/v2/industry.py`（新） | ISIN 產業代碼 01–38、公告用名對應（去掉結尾「業」比對，另列觀光事業、電子商務）、16 改名、35–38 新增與櫃買 34／18 併入的存續期間 |
| `src/stock_data_center/v2/industry_changes.py`（新） | 兩個交易所的列表／內文／附件 parser、`classify`、`validate`、raw-first 的 `ingest`、`write`（只新增有變的列、同 key 被另一則公告持有時拒寫）、CLI |
| `src/stock_data_center/v2/release_rules.py` | `INDUSTRY_ANNOUNCEMENT`：發文日隔天 00:00 Asia/Taipei，Python 與 SQL 兩種形式 |
| `src/stock_data_center/db/schema_v2.py` | 新表 `industry_changes`，列入 `APPEND_ONLY` |
| `migrations/versions/f1c5b643f4c7_step_39_a_industry_changes.py`（新） | 建表與 append-only trigger；downgrade 在表內有資料時先拒絕 |
| `pyproject.toml` | 新增 `pypdf>=6,<7`（2023 年公告的原類別在 PDF 附件） |
| 測試 | `tests/unit/test_v2_industry_announcements.py`（53）、`tests/integration/test_v2_industry_changes.py`（12）；`test_schema_v2_baseline.py` 加入新表、`test_v2_listings.py` 的 downgrade 改指定到 38-a 之前的版本（原本的 `-1` 在新 head 下會退掉本步） |
| fixtures | `tests/fixtures/v2/industry/`：2026-09-26 抓的官方回應（TWSE 列表與 4 則內文、TPEx 2023／2025 列表與 3 則內文、兩個交易所 2023 年的 3 份 PDF 附件） |
| 文件 | ADR-0030（新）、audit §4.11／§4.15（新）、`docs/data_domain_inventory.*`、ROADMAP、CLAUDE.md 快照 |

`src/` 新增約 710 行（`industry_changes.py` 582 行、`industry.py` 84 行為新檔）。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 每則列表、內文、附件都是一列 `fetches`，並保存原始檔 | PASS | `test_every_change_is_written_with_its_fetches`；實際 `fetches` 67 列全部 `succeeded`、都有 `sha256`（見下） |
| 一次變更一列，指向內文與附件的 fetch | PASS | 同上；實際 170 列，2023 年的 103 列都有 `attachment_fetch_id`，其他 67 列為 NULL |
| 重跑不再抓內文、不新增列 | PASS | `test_a_rerun_asks_for_no_detail_and_appends_nothing`；實際重跑只抓列表（TWSE 1、TPEx 8 次），`appended` 0 |
| refetch：內容不變不新增，改變的新增 revision | PASS | `test_a_refetch_appends_only_what_changed`；實際 `--refetch --purpose correction_check`：170 列全部 `unchanged` |
| 同一 key 被另一則公告持有時拒寫，結果與讀取順序無關 | PASS | `test_a_key_another_notice_holds_is_refused`；實際 `conflicts` 0 |
| 不可能為真的公告整則 quarantine，下次重抓 | PASS | `test_a_notice_that_cannot_be_true_is_quarantined_and_asked_again`、`test_a_detail_naming_another_notice_is_quarantined`、`validate` 的 7 條；實際 quarantine 0 |
| 每個名稱都對應得到代碼，且在實施日前後存在於該市場 | PASS | `test_every_published_spelling_maps_to_its_isin_code`、`test_categories_exist_only_while_the_exchange_had_them`；實際 170 列全部通過 `validate` |
| 興櫃、創櫃不收 | PASS | `test_subjects_decide_which_announcements_are_changes`、`test_tpex_reads_the_otc_attachment_and_skips_the_emerging_board`；實際 TPEx 4 則 `out_of_scope`、2023 年附件 2 記為 `another_board` |
| 列不可修改；每列必須真的改變類別 | PASS | `test_rows_cannot_be_changed`、`test_a_row_must_change_the_category` |
| Release rule 的 Python 與 SQL 形式一致 | PASS | `test_the_release_rule_agrees_in_python_and_sql`、`test_an_announcement_is_public_from_00_00_the_day_after_its_date` |
| Migration、downgrade、schema 無漂移 | PASS | `test_the_downgrade_refuses_stored_changes`、`test_the_baseline_builds_exactly_schema_v2`；`alembic check`：No new upgrade operations detected |
| 與 legacy `stock_db` 對帳 | PASS | 見下 |

### 測試先於實作

測試寫好時 `industry`、`industry_changes` 模組、資料表與 release rule 都還不存在：單元測試在收集時 ImportError，
整合測試同樣在收集時失敗。實作後單元測試有 1 條失敗：測試把「資訊服務」當成未知名稱，但依設計（去掉結尾「業」比對，
櫃買就寫「半導體」）它是資訊服務業，錯的是測試，改成「資訊」。整合測試有 1 條失敗：測試以股票代號為 key 取列，
3054 有兩次變更，取到的是後一次，改成依 (代號, 實施日) 取。

整合測試只在收集時紅過，所以另外逐一把實作改壞，確認每個行為都有測試抓得到：

| 改壞的方式 | 失敗的測試 |
|---|---|
| 已完成的內文照樣重抓 | `test_a_rerun_asks_for_no_detail_and_appends_nothing` |
| 不檢查同 key 被另一則公告持有 | `test_a_key_another_notice_holds_is_refused` |
| 不比對值、每次都新增 | `test_a_refetch_appends_only_what_changed` |
| 不排除興櫃段落 | TPEx 的整合測試與 112 附件的單元測試 |
| 不核對內文的發文字號 | `test_a_detail_naming_another_notice_is_quarantined` |
| 不限於 `stocks` 內的公司 | 6 條（外鍵違反） |

全套測試：1071 passed。ruff：改動與新增的檔案沒有問題。

## 真實資料

`stockdc_backfill` 升到 `f1c5b643f4c7`（只新增一張表；API 容器不檢查 migration 版本，照常服務），
再跑 `python -m stock_data_center.v2.industry_changes` 三次：一般、重跑、`--refetch --purpose correction_check`。
輸出存在 `log/step-39-a-*.json`。

| | TWSE | TPEx |
|---|---|---|
| 列表請求 | 1（一次涵蓋全部年份） | 8（2019–2026 每年一次） |
| 公告 | 11：變更 9、要點修正 2 | 13：變更 8、要點 1、興櫃／創櫃 4 |
| 實施日早於 2020-01-02（不存） | 3（106、107、108 年） | 1（108 年） |
| 寫入的公告 | 6 | 7 |
| 變更 | 65 | 105 |
| 不在 `stocks` 的公司 | 0 | 0 |
| quarantine／衝突 | 0／0 | 0／0 |

每則公告的筆數與來源研究一致：

| 來源 | 發文日 | 實施日 | 發文字號 | 筆數 | 附件 |
|---|---|---|---|---|---|
| TWSE | 2020-04-30 | 2020-06-01 | 臺證上一字第1091801955號 | 2 | |
| TWSE | 2021-05-04 | 2021-06-01 | 臺證上一字第1101802256號 | 11 | |
| TWSE | 2022-05-05 | 2022-06-01 | 臺證上一字第1111801797號 | 3 | |
| TWSE | 2023-05-22 | 2023-07-03 | 臺證上一字第1121802250號 | 47 | 47 |
| TWSE | 2026-05-08 | 2026-06-01 | 臺證上一字第1151801708號 | 1 | |
| TWSE | 2026-08-19 | 2026-09-01 | 臺證上一字第1151802340號 | 1 | |
| TPEx | 2020-05-19 | 2020-06-01 | 證櫃監字第10902007161號 | 4 | |
| TPEx | 2021-05-11 | 2021-06-01 | 證櫃監字第11002006321號 | 10 | |
| TPEx | 2022-05-10 | 2022-06-01 | 證櫃監字第11102010201號 | 4 | |
| TPEx | 2023-05-23 | 2023-07-03 | 證櫃監字第11202011201號 | 56 | 56 |
| TPEx | 2024-05-15 | 2024-06-03 | 證櫃監字第11302010341號 | 7 | |
| TPEx | 2025-05-21 | 2025-06-02 | 證櫃監字第11402010541號 | 11 | |
| TPEx | 2026-05-19 | 2026-06-01 | 證櫃監字第11502011171號 | 13 | |

`fetches`（dataset `industry_changes`，第一次執行）：TWSE 列表 1、內文 9（3 則 `before_window`）、附件 1；
TPEx 列表 8、內文 8（1 則 `before_window`）、附件 2（1 份 `another_board`）。三次執行共 67 列，全部 `succeeded`、都有原始檔。

### 變更鏈的初步一致性（39-c 的完整檢查之前）

依 (股票, 市場) 串起 163 條變更鏈：

- 相鄰兩次變更，前一次的新類別都等於後一次的原類別：**0** 筆不一致。
- 159 條屬於上市中的期間，最後一次的新類別都等於 `stocks` 今天的 ISIN 產業別：**0** 筆不一致。
- 4 條屬於已結束的期間，等 39-b 的錨點：4712（上櫃，2024-09-25 下櫃）、6806（上市，2026-06-23 下市）、
  8420（上櫃，2024-11-29 下櫃）、8476（上櫃，2023-10-31 轉上市）。

### 與 legacy `stock_db` 對帳（§78）

legacy 沒有產業歷史：`stock_info` 只有一份快照（`symbol`、`name`、`industry`、`listing_date`、`market`），
最新的 `listing_date` 是 2026-09-01。以 2026-09-11（v1 對帳窗的終點）當天有效的類別比對有變更過的 163 檔：

| 結果 | 檔數 |
|---|---|
| 代碼相同 | 160 |
| 代碼不同 | 0 |
| 不在 legacy | 3（已下市） |

包括 3054 在 2026-09-01 改為電子通路業，legacy 快照也已是電子通路業。

## 已知限制

- 公告的完整性無法完全證明：從未出現在公告裡的變更抓不到。TWSE 113、114 年查無公告。39-c 以變更鏈一致性與
  櫃買類股行情佐證（ADR-0030 §6）。
- 公告的撤回或被另一則公告取代尚未見過；後者會被拒寫並報告（`conflicting_notices`）。
- 分類期間與 API 在 39-c；已結束期間的錨點與下市公司的最後已知產業在 39-b。

## 延後的工作

- 39-b：`industry_observations`、兩個交易所的類股行情 adapter。
- 39-c：查詢時推出的分類期間、`industry-classifications` API、`/v1/stocks` 的下市公司產業、完整的一致性與對帳報告。
- 前向抓取（Step 28）：每年 5 月的年度調整與個別申請，重跑本 CLI 即可補上新公告。
