# Step 15-b 驗收報告

狀態：IN REVIEW

範圍：release rule registry 與評估（ADR-0020 §2、§3）

Schema 影響：`release_rules`，以 ADR 確定的四條規則作為初始資料，加上一個不可變
trigger。Migration：`3c8e5f1b7a46`。PIT 影響：無——`evidence_plan` 會計算，但
還沒有任何 adapter 寫入新的類型。

`src/` 改動 **+333 行**，遠低於 800 行的審閱門檻，所以這個 step 沒有再拆。
Step 15-c 是接縫上的決定，不是大小上的：見下文。

## 實作時在 ADR-0020 發現的缺陷

ADR-0020 §3 寫道**所有**規則時刻都在 Asia/Taipei 當日結束時解析，而且**全部**
移到下一個營業日。它自己的表格兩度與此矛盾：`exchange_daily_settled` 是次一日曆日
03:00，`tdcc_weekly` 是星期日 12:00，兩者都不是當日結束，也都不能移到營業日——
03:00 是檔案存在的時間，不論市場是否開市；而星期日永遠不是營業日，移動的話會把
規則推後整整一週。

表格是 owner 的決定（決定 1 和 2 明確指名了那些時刻）；那句概括性的句子是文字上
錯誤的推廣。ADR 現在區分法定期限和排程時刻。**沒有任何規則改變。**

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 每條規則都有 id、version，以及引用的排程或法條 | PASS | `release_rules` 有四條規則；`authority` 不可為空，每條都引用其背後的法條、owner 決定或 audit 章節。 |
| 規則有版本，而且永遠不編輯 | PASS | trigger 拒絕 `UPDATE` 和 `DELETE`；未知的版本會拋出錯誤，而不是退回最接近的版本。 |
| 週末、假日、跨年，以及落在非營業日的期限 | PASS | ADR 的兩個範例都是回歸測試：2021Q2 → 2021-08-16（08-15 是星期日），以及 2026M04 → 2026-05-11（05-10 是星期日）。Q4 2023 → 2024-04-01 跨年並落在星期日。2024-07-10 的颱風休市把營收期限移到 07-11，所以移動依循真實日曆，而不是星期幾的規則。 |
| 排程時刻不移動 | PASS | 2024-07-23 的 `exchange_daily_settled` 解析為 07-24 03:00，即使 07-24 休市；對日曆從未匯入過的年份也能回答。 |
| 證據來源指名規則及其版本 | PASS | `monthly_revenue_statutory@1`。 |
| 寫入端的否證規則 | PASS | 晚於規則時刻的首次看到只寫入抓取證據；晚於規則的 `gap_fill` 抓取無法否證規則，因為它不是首次看到。 |
| `gap_fill` 不產生抓取界限 | PASS | 它只規劃規則證據；沒有規則時退回 `unknown`。 |

## `evidence_plan` 決定什麼

| Purpose | 可以宣稱抓取界限嗎？ |
| --- | --- |
| `first_capture` | 可以 |
| `correction_check` | 只對它新發現的 revision |
| `gap_fill` | 不行 |
| `unspecified` | 不行 |

沒有任何可證明的東西時，就不宣稱任何東西：規劃退回 `unknown`，並帶
`published_at = NULL`，與 ADR-0020 之前的行為完全相同。

## 為什麼接線放在 Step 15-c

沒有達到 800 行門檻，所以這次拆分是接縫上的決定，而不是大小上的。套用政策會改變
**每個** adapter 寫入的內容，從 `unknown` 變成真正的 publication evidence，讓歷史
第一次對 Market PIT 可見。它需要各來源選擇加入 `accepted_evidence_types`、各資料集
對改變內容的對帳，以及改寫 CLAUDE.md §31–32——而 §31–32 描述的是行為，在行為
改變之前改寫它們，會讓文件往另一個方向出錯。

這個 step 本身就是正確的：規則能解析、政策能計算，而且沒有任何 adapter 的行為改變。

## 驗證

從零 migrate 到 `3c8e5f1b7a46` 的資料庫：

```text
364 passed, 3 skipped, 1 warning
```

本 step 之前的基準：327。差異就是 37 個新測試；除了 evidence-plan 相關的測試之外，
沒有任何既有測試改變，而那些測試現在傳入契約要求的規則歸屬。

Step 14 的儲存契約防護再次要求新表必須分類，而 Step 15-a 的 metadata-DDL 防護讓
新的 constraint 保持正確。

## Code review 發現

六項發現，在改動任何東西之前都經過驗證；沒有一項是誤報。附帶的一項評論不成立，
見下文。

| # | 發現 | 驗證方式 | 修正 |
| --- | --- | --- | --- |
| 1 | `FIRST_CAPTURE` 忽略了 `version_created`，所以重跑 backfill 會在較晚的時刻寫入第二個 `capture_bound` | 實際執行：第一次的界限是 2024-02-05，重跑的界限是 2026-09-16。兩個界限的排名都是 80，resolver 以 `recorded_at` 打破平手，所以較寬鬆的那個勝出，一列原本在較早 `information_as_of` 可見的資料變得不可見。 | 首次看到一列就代表建立它的版本，所以兩種 purpose 現在都以 `version_created` 把關。三個回歸測試。 |
| 2 | `rule_source` 預設為 `"release_rule"`，而不是 `rule_id@version` | 對照 ADR-0020 §3 閱讀 | 改為必填並驗證：只可附加的儲存永遠無法更正沒有歸屬的證據。 |
| 3 | `day_of_next_month` 大於 28 時在二月會拋出錯誤 | `date(2024,2,1).replace(day=30)` → `ValueError` | 以 `CHECK` 在註冊時拒絕這種規則。「次月 29 日」在二月沒有意義。 |
| 4 | 不可變 trigger 漏了 `TRUNCATE` | repo 在 `4d2a6f8c1e30` 和 `d81b5c9a3f20` 中把列層級與陳述層級的 trigger 成對使用 | 已加上。證據以字串引用規則，沒有 foreign key，所以一次 truncate 會抹掉每一列由規則推導的證據背後的依據。 |
| 5 | `EVIDENCE_RANKS` 複製了 registry 的內容，卻沒有任何東西綁定兩者 | 閱讀 | 一個 integration test 斷言這些常數等於被強制執行的 registry 列。 |
| 6 | `market="TWSE"` 默默把 TWSE 日曆套用到上櫃發行公司 | 閱讀 | 行為不變——這是 ADR-0021 §4 的決定——但現在它是帶有這段理由的具名常數，而不是單純的預設值。 |

### 一項不成立的評論

review 也說「命令列上的 alembic 會忽略匯出的 `DATABASE_URL`，改用 alembic.ini 寫死
的 URL」。事實並非如此：`migrations/env.py:16` 讀取
`config.attributes.get("database_url") or os.getenv("DATABASE_URL")` 並覆蓋 ini。
以執行 `DATABASE_URL=...stockdc_envcheck alembic upgrade head` 檢查，schema 建在
`stockdc_envcheck`，而不是 ini 的 `stockdc`。

另一項評論——長期存在的本機 `stockdc` 資料庫落後好幾個 revision，而且是本機
integration 失敗的唯一原因——是正確的，這也是本報告每個數字都來自從零 migrate 的
資料庫的原因。

## 已確認的範圍排除

- 沒有任何 adapter 寫入新的證據類型；每一個仍然產生 `unknown`。
- 沒有任何來源的 `accepted_evidence_types` 改變。
- 沒有為金融業財務報表定義規則：它們的期限不同，而 Step 23 把它們排除在 v1 之外。
  之後的 step 必須新增自己的規則，而不是重用一般產業的規則。
