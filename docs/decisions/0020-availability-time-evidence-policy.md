# ADR-0020：可得時間（availability-time）證據政策

狀態：**Accepted**，供 ROADMAP Step 15 實作。owner 於 2026-09-15 裁決下列六項。
延伸 ADR-0002（業務版本與發布證據）與 ADR-0010（來源專屬的可接受證據型別）。

## 裁決摘要

| # | 問題 | 裁決 |
| --- | --- | --- |
| 1 | 交易所每日資料集的 release rule 時刻 | **次一日曆日 03:00**（台北時間） |
| 2 | TDCC 週度是否給 release rule | **給**：資料日期後第一個星期日 12:00 |
| 3 | ingest `purpose` 存在哪裡 | **`ingest_runs` 上具型別、有 `CHECK` 的新欄位**，不放 JSONB |
| 4 | 月營收公告日期的證據來源 | **legacy 封存檔 `market.csv` 本身**（路徑 + mtime）。不讀 `revswarm.db`，不記逐列 engine/verifier |
| 5 | Step 15 是否依賴 Step 16 | **是**，#16 的交易日曆先做 |
| 6 | 是否捕捉當日未結算的列 | **v1 不抓**。D 日資料一律 D+1 03:00 才可見 |

裁決 3 使 ROADMAP Step 15 的「schema impact: none expected」失效，改為一個欄位。
裁決 4 使 ROADMAP Step 15 中「`evidence_source` 記 dataset、engine、verifier」
一句失效，該句刪除。裁決 5 使 ROADMAP 的相依從「Step 14」改為「Step 14、Step 16」。
這三處已在同一個變更中改正。

## 背景

`docs/source_field_audit.md` 稽核過的來源，**沒有任何一個發布逐列的發布時刻**
（§7）：交易所各檔只帶資料日期，MOPS `t21sc03` 只帶頁面產生日期，
`t164sb01` 完全沒有，TDCC 帶的是資料日期。在「只接受 official 證據」的規則下，
每一列匯入的歷史都是 `published_at = NULL`、對 Market PIT 不可見
（CLAUDE.md §31），而 System PIT 從匯入時間才開始。消費端真正要問的
——「在 D 這天，什麼是可被知道的？」—— 完全無法回答。

四項事實限制了任何解法：

1. **真實的捕捉紀錄存在，但只涵蓋近期。** legacy 的每日排程記下了真正的首見日期：
   月營收自 2026M02 起（22:45 的 run，只跑 1–15 日）、XBRL 自 2025Q4 起
   （23:50 的 run）。更早的值在 legacy **資料庫**裡是 2026 年 2 月一次回填
   事後補上的法定死線（audit §7.1）。
2. **月營收歷史的公告日期已經逐列存在於封存檔中。** `revswarm` 從有日期的新聞
   報導重建，並寫回 legacy 封存的 `market.csv`：2020M01–2026M01 的 128,063 列
   中有 114,910 列（89.7%）帶真實公告日期，其餘留在法定的 10 日
   （audit §7.4）。抽查 `2021M05/market.csv`：1,692 列共 11 個不同
   `publish_time`，範圍 2021-06-01 至 06-11，其中 809 列落在 06-10。
3. **法定死線是真的，而且會移動。** 落在非營業日的死線順延到下一個營業日：
   2021-05-15（六）、2022-05-15（日）、2021-08-14（六）、2020-11-14（六）、
   2024-03-31（日）。2026-05-10 是星期日，有 292 列營收在星期一 05-11 才首見
   （audit §7.1）。
4. **交易所當日的列，在結算完成前就已經被發布。** 2026-09-15 14:29（台北時間），
   收盤後一小時，`STOCK_DAY` 給出的 2330 該列成交量 13,469,000 股、
   成交筆數僅 7,205 筆（每筆 1,869 股）；前一交易日是每筆 113 股。
   legacy 23:30 的 run 在觀察到的 27 個交易日中有 5 日需要 03:00 的重試補齊，
   其中 1 日到 03:00 仍不完整（audit §7、§7.2）。

## 決策

### 1. 四種證據型別，以 rank 排序

`publication_evidence.evidence_type` 新增四個值。`quality_rank` 正是 ADR-0002
既有的遞減排序依據，**因此優先序直接由 rank 表達，resolver 不需要任何特例**：

| `evidence_type` | `quality_rank` | 意義 |
| --- | ---: | --- |
| `official` | 90 | 來源自己發布的逐列發布時刻。v1 沒有任何來源提供，此型別保留定義但不使用。 |
| `capture_bound` | 80 | Data Center 自己第一次成功抓到「含有此版本的 artifact」的時刻。已被證明的上界。 |
| `legacy_capture_bound` | 70 | legacy 爬蟲記錄的首見日期，換算成「第一次含有該列的排程 run 的結束時刻」。 |
| `press_report_bound` | 60 | 從有日期的次級紀錄重建、並已寫入 legacy 封存檔的發布日期，取該日日終（台北時間）。 |
| `release_rule` | 40 | 由已公布的時程或法規推導出的、有版本的「不晚於」時刻。 |

`press_report_bound` 排在兩種 capture 之下，因為它是日精度、且為重建值，
帶有可量測的殘餘誤差率；它排在 `release_rule` 之上，因為一篇日期為 D 的報導
**證明**該值在 D 當天已經公開，而規則只主張它「至遲在那時」已公開。

四者的 `evidence_kind` 都是 `'assertion'`、`published_at` 非空，
符合 `publication_shape` 約束。

### 2. Capture 勝過 rule —— 讀取端靠排序，寫入端另有一條

單靠 rank 排序，兩者並存時答案已經正確：capture 早於 rule 時 capture 勝，
版本更早可見（它確實被證明已公開）；capture 晚於 rule 時一樣 capture 勝，
版本更晚可見（絕不會早於證據）。

只有一條比排序更嚴格，而且屬於**寫入端**：

> 若某列的首次捕捉紀錄晚於規則時刻（遲交者），該列的規則即被推翻。
> 只記錄 capture 證據，**不寫入任何 `release_rule` 列**。

回填（backfill）run 的捕捉不是首見證據，因此永遠不能推翻規則；
這類列的解析方式與從未被捕捉的歷史完全相同。

### 3. Release rules（裁決 1、2）

每條規則都有 id、版本，以及引用的時程或法規依據。`evidence_source` 記錄
`rule_id@version`。所有時刻都在 **Asia/Taipei**。

**兩類規則，時刻與順延規則不同**（本段於 Step 15-b 修正：原文寫成「所有規則時刻
皆為台北時間日終」，與下表自己的兩條規則矛盾，是錯誤的概推。表格才是裁決內容，
規則本身不變）：

- **法定死線類**（月營收、財報）：解析到**該日日終**，且一律**順延到下一個營業日**
  （對照 Step 16 的交易日曆）。因此 2021Q2 解析到 2021-08-16 而非 08-15
  （08-15 是星期日），2026M04 的營收解析到 2026-05-11（05-10 是星期日）。
- **排程類**（交易所每日、TDCC 週度）：解析到表中載明的**明確時刻**，
  **不做營業日順延**。次一日曆日 03:00 是檔案已經存在的時刻，與那天是否開市無關；
  而週日本來就不是營業日，順延會把規則推掉一整週。

| Rule id | 範圍 | 時刻 | 依據 |
| --- | --- | --- | --- |
| `monthly_revenue_statutory@1` | 月營收，全體發行人 | 次月 10 日 | 法定申報死線 |
| `financial_statements_general@1` | 財報，一般業 | Q4 03/31、Q1 05/15、Q2 08/15、Q3 11/15 | legacy 捕捉視窗結束日與 `train_eps` 截止日；Q2/Q3 是法定 08/14、11/14 的隔日 |
| — | 財報，**金融業** | **不定義** | 其死線不同（Q1/Q3 05/30、半年報 08/31，audit §7.1），且 Step 23 將其排除於 v1。日後的 step 必須自訂規則，**不得沿用一般業規則**。 |
| `exchange_daily_settled@1` | 交易所每日資料集 | 次一日曆日 03:00 | **裁決 1**。交易所在結算完成前即供應當日的列（事實 4），故任何在交易日當天解析的規則都不成立。23:30 時 27 個交易日中有 5 日不完整，03:00 時只剩 1 日；唯一那次失敗是隔天 09:54 才存檔，改成 08:00 同樣涵蓋不了，且該時戳是我方存檔時間，無法分辨「來源延遲」與「抓取失敗」。 |
| `tdcc_weekly@1` | TDCC 週度分布 | 資料日期後第一個星期日 12:00 | **裁決 2**。依據是 legacy 星期日 10:20 的排程加餘裕。**本規則的依據是我方的排程觀察，不是 TDCC 官方公布的發布時程** —— 稽核中找不到官方時程。此限制隨規則一起記載。 |

規則有版本，且永不就地修改。修正規則的作法是發布 `@2` 並附加新證據去
supersede 舊列；被 supersede 的歷史仍可讀取，這是 ADR-0002 的要求。

### 4. 開關仍然是每一組 `(dataset_code, source)`

ADR-0010 不變。`dataset_sources.accepted_evidence_types` 仍是非空白名單，
預設只有 `official`。每一個新型別都要針對個別來源以明確的 migration 加入，
**絕不從現存證據推論**。白名單未包含的型別，該來源完全忽略；
且過濾仍然先於 supersession。

### 5. 證據型別由「宣告的 ingest 目的」推導（裁決 3）

每一次 ingest run 都要在**請求抓取的當下**宣告目的，絕不事後推斷：

```text
first_capture     預期這次抓取是這些列的第一次見到
gap_fill          補一段已知被漏掉的期間
correction_check  重讀已捕捉過的期間，檢查是否有更正
```

只有 `first_capture` 可以產生 `capture_bound`。多年後因為查詢發現缺漏才抓到的列
屬於 `gap_fill`，不會在該抓取時刻產生 capture 證據，而是依其 release rule 解析。
`correction_check` 若發現值變動，會產生新的業務版本，其 capture 證據就是這次
檢查的時刻 —— **更正絕不會早於它真正被看到的時候可見**。

`purpose` 是 `ingest_runs` 上一個具型別、帶 `CHECK` 約束的欄位，不放進
`run_metadata` JSONB：它決定一次 run 能寫哪種證據，是承載正確性的欄位，
而 JSONB 沒有約束、沒有預設值、沒有型別。ROADMAP §14 的 artifact origin
（`official_fetch` / `legacy_archive`）目前同樣沒有落到 schema，在同一個
migration 一併補上。

### 6. Rule 與 press-report 證據，綁定到版本自己的 artifact

`publication_evidence.raw_artifact_id` 與 `ingest_run_id` 都是 `NOT NULL`，
而 resolver 是以 inner join 接 `raw_artifact_observations`。`release_rule` 這一列
沒有自己的 artifact，因此它綁定到**它所認證的那個業務版本的 artifact 與
ingest run**。

由此導出兩個後果，兩個都是刻意的：

- 規則證據只能由「真的持有 artifact」的 ingest 附加。不存在一個沒有 artifact 的
  「套用規則」批次工作。
- `press_report_bound` 綁定的是它被讀出來的那個封存檔的 artifact ——
  月營收的 `market.csv`，以 §14 的 `legacy_archive` origin 匯入 ——
  而不是 MOPS 頁面。

### 7. 月營收的證據來源就是封存檔（裁決 4）

月營收的逐列 `publish_time` 已經寫在
`~/GitHubLL/my_stock_project/data/raw/monthly_revenue/<year>/<year>M<month>/market.csv`
裡。Data Center 讀這個檔，**不讀 `revswarm.db`** —— 後者是一個仍在運行的爬蟲
的工作資料庫：會變動、無 content-addressing、無不可變保證，把它當成匯入時的
相依，等於讓「2021 年某列的公告日期」取決於那顆資料庫今天的狀態，
違反最高要求。ROADMAP §14 早已如此規定。

因此 `evidence_source` 記的是**封存檔本身**：檔案路徑、mtime，
以及該檔在 §14 `legacy_archive` origin 下的 raw artifact hash。
**不記逐列的 engine / verifier** —— 那些只存在於未匯出的 `revswarm.db`。
ROADMAP Step 15 原本承諾逐列記錄，是做不到的，該句刪除。

兩個窗口以期間區分，不需要逐列 provenance：

- **2020M01–2026M01**：`publish_time` 是 revswarm 寫回的重建公告日期
  （或落在 10 日的 fallback）→ `press_report_bound`。
- **2026M02 起**：`publish_time` 是 legacy 22:45 排程的真實首見日期
  → `legacy_capture_bound`。

稽核註記：audit §7.1 的表描述的是 legacy **資料庫**（每月一個合成值）；
這些 **CSV** 已被 revswarm 寫回逐列日期。兩者不同，§7.1 據此補一句。

### 8. v1 的各來源分別得到哪種證據

| 資料集 | 首次捕捉之前 | forward capture 開始之後 |
| --- | --- | --- |
| 月營收，2020M01–2026M01 | `press_report_bound`（封存檔的逐列日期）；落在 10 日者見 §9 | `capture_bound` |
| 月營收，2026M02 起 | `legacy_capture_bound`（legacy 22:45 run） | `capture_bound` |
| 財報，2020Q1–2025Q3 | `release_rule` | `capture_bound` |
| 財報，2025Q4 起 | `legacy_capture_bound`（legacy 23:50 run，以檔案 mtime 收緊） | `capture_bound` |
| 交易所每日資料集 | `release_rule` | `capture_bound` |
| TDCC 週度 | `release_rule` | `capture_bound` |

2026M02 之後，若某列出現在官方重抓的 `_0` 頁而不在 legacy 紀錄中，
代表它在最後一次 legacy run 時尚未公開。該列不給規則證據，
依其 Data Center 捕捉時刻解析。

legacy XBRL 檔名日期若晚於該季視窗結束日，來自回填 run 而非首見捕捉
（2026-08-01 與 2026-08-17 那批檔案）。它們不是 `legacy_capture_bound`。

### 9. 月營收落在 10 日的那些列

封存檔只寫日期，因此「復原到 10 日」與「維持法定 fallback」在數值上無法區分。
**凡 `publish_time` 為次月 10 日者，一律視為 rule-bound，不視為 press-reported。**

已量測的代價：視窗內 128,063 列中有 40,188 列落在 10 日，其中 36,492 列的
10 日是營業日，規則會解析到同一個時刻，完全不變。只有 3,696 列（2.9%）的
10 日落在週末，會比封存檔寫的日期晚解析 1–2 天。**晚是安全的，早不是。**

### 10. v1 不捕捉當日未結算的列（裁決 6）

D 日的交易所資料一律在 D+1 03:00 才可見。結算完成前那個版本不被記錄。

這**不是** look-ahead：結算後的值 `published_at` 是 D+1 03:00，
「以 D 日 15:00 為準」的查詢看不到它 —— 是偏晚，安全的。
代價是另一件事：系統無法回答「D 日盤後、結算前，公開的是什麼」，
因此**盤後當日決策的策略無法回測**。這是 v1 明確接受的功能缺口。

附帶條件：這個安全性取決於裁決 1。若日後有人把交易所規則改到比 03:00 早
（例如 D 日 16:00）卻仍未捕捉當日的列，結算後的值就會在 16:00 變成可見，
而當時真正公開的是未結算的值 —— **那才是真的偷看未來**。
任何調早 `exchange_daily_settled` 的提案，必須同時導入當日捕捉。

## 後果

- 匯入的歷史在有依據的時刻變成 Market-PIT 可見，而不是完全不可見。
- 可見時間**可能偏晚，但絕不會偏早**：四種型別都是發布時間的上界，
  而 §2 的寫入端規則使 rule 無法主張某列在 capture 證明之前就已公開。
- **明確記載、且明知而接受的限制：** 任何捕捉紀錄之前的歷史，儲存的是
  「最新更正後」的值，卻在規則時刻變為可見。2023 年對 2021 年數字發出的更正，
  會從 2021 年的規則時刻起就可見 —— 這是**更正的 look-ahead**。
  它影響 2026M02 之前的月營收、2025Q4 之前的 XBRL，以及 forward capture
  之前的全部交易所每日資料。legacy 系統有同樣的缺陷。Step 27 會回報
  forward-capture 的改版率，讓這個效應的大小被量測，而不是被假設。
- **v1 無法回測盤後當日策略**（§10）。
- Adapter Steps 16–24 不被本 ADR 阻擋。在其實作 step 附加核可證據之前，
  它們發出 `unknown` 證據；核可後的證據日後附加，不需要動到業務版本。
- 來源政策（capability 與可接受證據型別）從通用的 raw-first 編排移進
  各 adapter 自己的 source 宣告，吸收原本的「source capability hook」step。
- CLAUDE.md §31 與 §32 必須在 Step 15 中依本 ADR 改寫。

## Step 15 的實作要求

- 每條規則都有 id、版本，以及引用的官方時程或法規。永久測試涵蓋週末、假日、
  年度邊界，以及落在非營業日的死線（至少 2021-05-15、2021-08-14、2020-11-14、
  2024-03-31、2026-05-10）。
- 「規則被遲交者推翻」的回歸測試（§2 寫入端規則）。
- 交易所規則以 forward capture 驗證：交易日 D 的資料在規則時刻確實抓得到。
- `gap_fill` 的 ingest 不得產生 `capture_bound`；該版本依 release rule 解析。
- `purpose` 欄位的 `CHECK` 約束拒絕未列舉的值。

## 已否決的替代方案

**只接受 official 證據。** v1 只能透過 System PIT 呈現歷史，
所有歷史 Market-PIT 查詢都回傳空的。這是正確但無用的：
它回答「到 D 為止我們匯入了什麼」，而問題是「在 D 這天什麼是可被知道的」。

**用匯入的 wall-clock 時間當發布時間。** CLAUDE.md §32 明文禁止，
而且它把誤差方向弄反了：每一列 2020 年的資料看起來都像 2026 年才發布，
使得所有歷史對任何歷史查詢都不可見。

**用單一個 `estimate` 型別涵蓋全部四種情況。** 那會讓「已證明的抓取時刻」與
「法定推測」排名相同，resolver 只能靠 `recorded_at` 二選一 —— 也就是靠匯入順序。

**在匯入時讀 `revswarm.db` 以取得逐列 provenance。** 違反 ROADMAP §14 與最高
要求：歷史正確性不得依賴一顆外部可變資料庫的當下狀態。封存檔是介面。

**為沒有公布時程或法規依據的來源發明時刻。** 永久排除。
沒有公布時程的來源，就沒有規則。
