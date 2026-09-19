# Step 15-a 驗收報告

狀態：IN REVIEW

範圍：可取得時間的證據詞彙與 ingest purpose（ADR-0020 §1、§5）

Schema 影響：`evidence_types` registry、`ingest_runs.purpose`、
`raw_artifact_observations.artifact_origin`，以及一個 trigger。
Migration：`2b7d4e9a1c35`。PIT 影響：目前沒有——還沒有東西寫入新的類型。

## 為什麼是 15-a 而不是 15

Step 15 的完整範圍會遠超過 800 行的審閱門檻（CLAUDE.md §1），所以沿著*宣告*
詞彙和*評估*規則之間的接縫拆分：

- **15-a（本 step）**——類型存在、排序被強制執行，而且每次 ingest 都記錄它為
  什麼抓取、bytes 是怎麼取得的。本身就是正確的：adapter 仍然產生 `unknown`
  證據，和以前完全一樣。
- **15-b**——release rule registry 及其對照 Step 16 日曆的評估、從 purpose 到
  證據的推導、寫入端的否證規則，以及改寫 CLAUDE.md §31–32。

release rule registry 刻意放到 15-b，而不是這裡。一個版本化的規則列，如果沒有
能把它轉成時刻的程式，就只是承諾，不是事實，而且會變成拆分規則所警告的
「只合併了半個契約」。

`src/` 改動 **+94 行**，遠低於門檻。

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| ADR-0020 的四種類型以 ADR 的排序註冊 | PASS | `evidence_types` 有全部五列；測試斷言確切的排名*以及*由此得出的順序，所以默默重新編號會失敗。 |
| 排序是儲存層的不變條件，不是靠呼叫端自律 | PASS | 以 rank 95 寫入 `release_rule` 被拒絕；以 40 寫入被接受。 |
| 非肯定的 head 不能排在肯定之前 | PASS | 固定排名類型的 `unknown` 類列必須帶 rank 0。 |
| 註冊不等於授權 | PASS | 未註冊的類型仍由 `accepted_evidence_types`（ADR-0010）管控，不變。 |
| 每次 ingest 都宣告為什麼抓取 | PASS | `ingest_runs.purpose` 帶有 `CHECK`；捏造的 purpose 被拒絕；什麼都沒宣告的 run 記錄為 `unspecified`。 |
| 生命週期記錄宣告的 purpose 和來源 | PASS | 以 `purpose=gap_fill, artifact_origin=legacy_archive` 執行的匯入，儲存的就是這兩個值。 |
| ROADMAP §14 的 artifact 來源已建模 | PASS | `raw_artifact_observations.artifact_origin` 帶有 `CHECK`；`scraped_from_a_blog` 被拒絕。 |

## 這個 step 必須做的決定

對**每一種**類型強制「註冊的排名是唯一合法的排名」，會讓 8 個檔案中 **51 個既有
測試**失敗。那些測試以 rank 100 寫入 `official`，並把排名當成自由參數，用來演練
ADR-0002 的排序契約——那是與 ADR-0020 引入的不同的契約。

與其在一個本該很小的 step 裡改寫 51 個測試，強制執行的範圍限定在 ADR-0020 實際
引入的東西。`evidence_types.rank_is_enforced` 對四種新類型為 true，對 `official`
為 false，registry 列也寫明了原因：`official` 早於這份 ADR，既有的列帶著各種排名，
而且沒有任何 v1 來源以肯定的方式產生它（audit §7）。

這沒有削弱任何東西。ADR-0020 防範的風險是偽造的 `release_rule` 排在真正的
`capture_bound` 之前，而這已經完全固定住了。

## 驗證

從零 migrate 到 `2b7d4e9a1c35` 的資料庫：

```text
327 passed, 3 skipped, 1 warning
```

本 step 之前的基準：312 passed。差異就是 15 個新測試；沒有任何既有測試改變。

Step 14 的儲存契約防護再次發揮作用：`evidence_types` 第一次執行時分類失敗，現在
記錄為附上理由的排除表。

## Code review 發現

五項在改動任何東西之前都經過驗證；沒有一項是誤報。

| # | 發現 | 驗證方式 | 修正 |
| --- | --- | --- | --- |
| 1 | `purpose` 的 `CHECK` 落到了 `import_manifests` 上，而那張表沒有這個欄位 | 在空資料庫上執行 `metadata.create_all`：`column "purpose" does not exist` | 移除。一次腳本化的編輯在兩張表中都匹配到了 `completed_after_started` constraint 的文字。 |
| 2 | `purpose` 預設為 `first_capture`，所以沒有宣告的 run 會被*推斷*為首次抓取 | 對照 ADR-0020 §5 閱讀 | 所有地方的預設都是 `unspecified`。重新抓取很久以前發布的歷史是 `gap_fill`，CLI 說明現在也這麼寫。 |
| 3 | `downgrade()` 丟掉 `purpose` 和 `artifact_origin`，默默把 `gap_fill` 重新解讀為 `first_capture` | 閱讀 | 只要還有任何宣告存在，`P0001` 預檢就拒絕，並附回歸測試。 |
| 4 | `official` 註冊在 90，但儲存的列帶的是 0 和 100，而文件把 90 當成事實呈現 | 閱讀 | `pit_semantics.md` 現在寫明 90 是名目值、rank 100 的 `official` 斷言仍可能排在固定排名的 `capture_bound` 之前，以及 Step 15-b 不可假設 90 描述任何已儲存的列。 |
| 5 | Steps 14 和 16 合併之後仍標為 `THIS PR`／`THIS STEP` | 閱讀 | 兩者都改為 `MERGED`；在 ROADMAP 和 CLAUDE.md 中只有 15-a 是目前的 step。 |

值得記住的是第 1 項：**325 個通過的測試看不到它**，因為測試套件中沒有任何東西
要求 metadata 產生 DDL——migration 建立真正的 schema，而 Alembic autogenerate
不比較 `CHECK` constraint。`tests/integration/test_step15a_metadata_ddl.py` 補上
了這個漏洞，而且立刻找到 Step 16 已經合併的*第二個*潛在缺陷：
`trading_calendar_versions.trading_days_sorted_distinct` 在 metadata 中仍是子查詢
形式，PostgreSQL 在 `CHECK` 中會拒絕它，而 migration 早已把它移到一個 immutable
function 裡。

## 已確認的範圍排除

- 這裡沒有定義、註冊或評估任何 release rule。
- 沒有任何來源的 `accepted_evidence_types` 改變；每個 adapter 仍以
  `published_at = NULL` 寫入 `unknown` 證據。
- CLAUDE.md §31–32 仍描述 ADR-0020 之前的規則；改寫它們要和讓它們成真的行為
  一起，在 15-b 進行。
