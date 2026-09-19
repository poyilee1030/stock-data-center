# Step 14 驗收報告

狀態：IN REVIEW

範圍：清單與儲存契約的來源實況對齊

Schema 影響：無。Migration：無。PIT 影響：無。`src/` 或 `migrations/` 底下沒有任何
檔案改變。

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 清單、audit 和 schema 一致 | PASS | `docs/data_domain_inventory.json` 中的 `storage_contract` 分類了 24 張表共 194 個非結構性欄位，並以名稱和理由排除其餘 29 張，所以 metadata 中的 53 張表全都有交代。`test_every_stored_column_has_a_source_coverage_entry` 將欄位與實際的 SQLAlchemy metadata 比較；`test_unsourced_and_partial_columns_match_the_audit` 逐列將狀態*和*效果與 audit §5 比較。 |
| 新增沒有來源對應的欄位時，新測試會失敗 | PASS | 四次故障注入，每次都讓應該抓到它的測試失敗（執行結果見下）：在 `daily_price_versions` 和 `financial_facts` 上各加一個未對應欄位、把 `NOT NULL` 欄位的效果設為 `stays NULL`，以及一個 `observed` 目標指名了不存在於任何 migration 或規劃中 PR 的欄位。 |
| 修正無來源欄位的宣稱 | PASS | 股票標籤生效日期、指數成交金額、TAIEX 以外的指數 OHLC、委託簿深度、月營收幣別，以及公司行動的公告日／基準日／發放日和盈餘／資本公積拆分，在 registry 和 audit §5 中都標為 `unsourced` 或 `partially_sourced`，各自附上對儲存欄位的效果。 |
| 恢復被遺漏的有來源欄位 | PASS | 八個月營收已發布的比較值以 `observed` 對應到 `monthly_revenue_versions.*`（Step 22 新增這些欄位）；TAIEX OHLC 改為來自 `MI_5MINS_HIST` 的 `partially_sourced`，而不是 `unsourced`。 |
| 標示不在 v1 的領域 | PASS | 股票標籤、XBRL codebook、信用交易市場彙總和 `monthly_revenue_growth:v1`，在清單矩陣和 ROADMAP §16 中都標為**不在 v1**。 |
| 新增領域 | PASS | `dividend_declaration` 在 v1 儲存契約矩陣中，指名 Step 33 新增的 `dividend_declaration_versions` 表。 |

## 兩份文件改了什麼

清單宣稱有來源、實際沒有的欄位，修正如下：

| 原宣稱 | 修正為 |
| --- | --- |
| `stock_tags` 以明確的生效區間觀察 | 不在 v1：MoneyDJ 第三方快照，沒有生效日期，沒有使用者 |
| `market_indices`「可取得 OHLC／漲跌百分比／成交金額」 | 只有收盤、漲跌點數、漲跌百分比；OHLC 只有 TAIEX，透過 `MI_5MINS_HIST`；`trade_value` 沒有來源；指數 metadata 的生效日期是觀察日期 |
| `daily_quotes.bid`/`ask` → `bid_snapshot`/`ask_snapshot` | → `last_bid_price`/`last_ask_price`：檔案只發布委託簿的一檔，深度資料沒有來源 |
| `monthly_revenue_versions.currency` 作為觀察值 | 頁面層級常數（單位：千元，永遠是 TWD） |
| `dividend`「所有日期……都是 revision 內容」 | 交易所結果資料中都沒有公告日、基準日或發放日，也沒有盈餘／資本公積拆分 |

清單遺漏了使用者會讀取的有來源欄位，修正如下：

| 舊系統欄位 | 原本 | 現在 |
| --- | --- | --- |
| `revenue_last_month`、`revenue_last_year`、`mom_pct`、`yoy_pct`、`revenue_cumulative`、`revenue_cumulative_last_year`、`cumulative_yoy_pct` | 標準衍生（`monthly_revenue_growth:v1`） | 觀察到的 `monthly_revenue_versions.*`（Step 22） |
| `comment`（備註） | 只存在 raw artifact | 觀察到的 `monthly_revenue_versions.note`（Step 22） |
| TAIEX `open_value`/`high_value`/`low_value` | 沒有來源（audit §5） | 部分有來源，`MI_5MINS_HIST`（Step 18） |

audit 隱含但沒有寫明的兩個缺口，由本 PR 加進 §5：

- `corporate_action_versions.old_shares`／`new_shares` 是**部分有來源**：只有減資。
  TWSE `TWTB8U` 面額變更的明細欄位尚未驗證，也沒有找到 TPEx 的面額變更端點
  （§4.10）。
- `security_metadata_versions.name`／`industry` 是**部分有來源**：快照只發布當下
  的值，所以較早的生效日期帶的是當下的值（§4.11）。ROADMAP §16 已經在領域層級
  註明這點；在欄位層級原本看不出來。

audit §5 從兩段文字清單改寫成一張表，每個 `table.column` 一列，狀態為 `unsourced`
或 `partially sourced`，並附上效果。改寫本身沒有刪除任何事實；列數從 8 個分組
條目加一句文字，增加到 32 個明確的欄位，而且現在可以被解析，這讓測試能把它和
registry 比較。

## 沒有來源不代表是 NULL

§5 和 ROADMAP §2.3 原本都說每個無來源欄位「保持 NULL」。其中六個是 `NOT NULL`，
而 `monthly_revenue_versions.currency` 目前由已出貨的程式寫入
（`src/stock_data_center/monthly_revenue/ingestion.py`），而且是 revision identity
比較的一部分。一個照字面遵守那條規則的 PR 會違反 NOT NULL constraint。現在每個
無來源欄位都記錄了它的效果：

| 效果 | 欄位 |
| --- | --- |
| 保持 NULL | 深度資料、指數 `trade_value`，以及五個公司行動欄位 |
| 存放有文件記載的常數 | `monthly_revenue_versions.currency`——頁面單位 單位：千元，永遠是 TWD |
| 存放衍生值 | `market_index_metadata_versions.effective_from`/`effective_to`——我們自己第一次和最後一次觀察的日期 |
| 整張表保持空的 | `security_tag_versions` 和 `xbrl_concept_catalog_versions`，它們的領域不在 v1 |

`test_a_column_that_stays_null_is_actually_nullable` 對照實際 schema 檢查第一列。

## 驗證

乾淨的 PostgreSQL 資料庫（`stockdc_pr14_probe`，從零 migrate 到 `7c9e2a4b6d81`）：

```text
250 passed, 3 skipped, 1 warning in 23.79s
```

`main` 上相同資料庫狀態的基準：183 passed。差異全部來自 11 個新測試；沒有任何
既有測試改變行為。

故障注入，四個同時進行：

```text
$ # 1. add sa.Column("probe_unmapped", sa.Text()) to financial_facts
$ # 2. set monthly_revenue_versions.currency effect to "stays NULL"
$ # 3. drop planned_pr from the monthly_revenue.mom_pct field
$ pytest tests/unit/test_pr14_storage_contract_source_coverage.py -q
FAILED ...::test_every_stored_column_has_a_source_coverage_entry
FAILED ...::test_a_column_that_stays_null_is_actually_nullable
FAILED ...::test_unsourced_and_partial_columns_match_the_audit
FAILED ...::test_observed_targets_exist_in_the_schema_or_name_the_pr_that_adds_them
4 failed, 7 passed
```

先前一次在 `daily_price_versions` 加欄位的探測，也讓同一個涵蓋測試失敗。所有測試
在撰寫文件之前都確認過會失敗（TDD）：前九個是 `9 failed in 0.14s`，review 時新增
的三個是 `3 failed, 8 passed`。

## 已處理的 review 發現

第一次 push 的 code review 提出八項發現；八項都對照 schema 和已出貨的程式確認
屬實，也都在這裡修正。

| # | 發現 | 修正 |
| --- | --- | --- |
| 1 | `docs/schema.md` 的新段落放在儲存對應表格裡面，導致最後兩列顯示成字面上的直線符號文字 | 段落移到表格下方 |
| 2 | 「無來源 ⇒ 保持 NULL」對六個 `NOT NULL` 欄位不成立，而且 `currency` 目前有寫入 | 逐欄記錄效果，並加上測試：*保持 NULL* 意味著可為 null；§5 和 ROADMAP §2.3 改寫措辭 |
| 3 | 報告重複了同一個錯誤的宣稱 | 修正範圍排除的條目 |
| 4 | 八個 `observed` 目標指名了不存在於任何 migration 的欄位 | 加上 `planned_pr: 22`，並加上測試：每個 `observed` 目標都必須存在，或指名新增它的 PR |
| 5 | 防護只涵蓋 `*_versions`，所以 `financial_facts` 等表可能默默多出未對應的欄位 | 涵蓋擴大到每張存放內容的表；其餘每張表都以名稱和理由排除，所以防護是全面的 |
| 6 | §5 的區段 parser 會默默丟掉未來 `### 5.1` 底下的列 | parser 斷言它讀到了區段中每一個 `| \`table.column\`` 列 |
| 7 | `assert record["audit_section"] in audit_text` 是對整份文件的子字串測試，所以「4.1」會匹配到「4.10」 | 移除；`test_referenced_audit_sections_exist` 才是真正的檢查 |
| 8 | 清單說最後買賣*量*是從一個不存在的舊系統欄位觀察到的 | 改寫：來源有發布它，Step 17 儲存它，舊系統的表沒有對應欄位 |

## 已知的環境問題，不是本 PR 造成的

長期存在的本機 `stockdc` 資料庫在這個 branch **以及 `main` 上**都回報 56 個
integration 失敗（完全相同的集合）。它的 `alembic_version` 已經在 head，所以
`command.upgrade(..., "head")` 什麼都不做，而 `market_index` 仍帶有之後一個
migration revision 移除的 `market` 和 `name` 欄位——這個資料庫早於一次修改過的
migration。從零 migrate 的資料庫會產生 metadata 宣告的 schema，整個測試套件
都通過。重建那個本機資料庫就能解決；migration 鏈不需要任何修改。

## 已確認的範圍排除

- 不刪除無來源欄位。大多數保持 NULL；六個 `NOT NULL` 欄位存放有文件記載的常數、
  由我們自己的觀察推導的值，或因為其表不在 v1 而什麼都不存。audit §5 逐欄記錄
  是哪一種，並有測試檢查每個標為*保持 NULL* 的欄位都可為 null。
- 沒有對任何表新增欄位：月營收比較值記錄為 Step 22 的 schema 變更，不在這裡做。
- `dividend_declaration_versions` 記錄為規劃中的領域；由 Step 33 建立這張表。
