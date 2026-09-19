# Step 15-c 驗收報告

狀態：IN REVIEW

範圍：把可取得時間的證據政策套用到 adapter

Schema 影響：`dataset_release_rules`，加上 `daily_price` 的 catalog、source 和規則
對應初始資料。Migration：`4d9f2a6c8b17`。
**PIT 影響：這是匯入的歷史開始對 Market PIT 可見的 step。** `src/` 改動
+269/−41 行，在審閱門檻內。

## 行為改變，直接說明

在這個 step 之前，從官方端點匯入的 `daily_price` 列對 Market PIT **不可見**：端點
沒有發布任何發布時刻，所以匯入記錄的是 `unknown`，並帶 `published_at = NULL`。
既有的 Step 9 回歸測試斷言的正是這一點——`assert market is None`。

現在它以 `exchange_daily_settled@1` 解析：交易日 2025-09-01 在 Asia/Taipei 09-02 的
03:00 變為可見。那個斷言現在反過來，並固定住規則、歸屬和時刻：

```python
assert market is not None
assert market.authoritative_evidence.evidence_type == "release_rule"
assert market.authoritative_evidence.evidence_source == "exchange_daily_settled@1"
assert market.authoritative_evidence.published_at == datetime(2025, 9, 1, 19, 0, tzinfo=UTC)
```

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 政策透過 ingest 生命週期套用 | PASS | `_write_business` 收到一個 `EvidenceContext`，帶有宣告的 purpose 和*記錄下來的*抓取時刻；這個時刻從觀察紀錄讀回，所以續跑的匯入保留原本的時刻，而不是續跑那一刻。 |
| 選擇加入是設定，不是程式 | PASS | `dataset_release_rules` 把 `(dataset_code, source)` 對應到一條版本化的規則，對 `dataset_sources` 和 `release_rules` 都有 foreign key。可以透過 `rule_for` 單獨查詢。 |
| 設定不完整的來源會明確失敗 | PASS | 規劃一個不在 `accepted_evidence_types` 中的類型時，會拋出 `UnacceptedEvidenceTypeError`，而不是寫入 resolver 會過濾掉的證據。 |
| 沒有人抓取過的歷史以其規則解析 | PASS | `gap_fill` 匯入只規劃 `release_rule`，在隔天 03:00。 |
| 前向抓取取代被它打敗的規則 | PASS | 03:00 之後的抓取會否證規則，只記錄抓取證據。 |
| 預設不啟用任何東西 | PASS | 沒有對應的資料集不會取得規則；既沒有規則也沒有抓取時，仍然記錄 `unknown`，和以前完全一樣。 |
| CLAUDE.md §31–32 與行為一致 | PASS | 改寫為已實作的政策，包括 purpose 表格和否證規則。 |

## 這對既有測試的代價

把 `daily_price` 加進 `dataset_sources` 的初始資料，與插入同一列的 fixture 衝突，
所以六個測試檔現在使用 `ON CONFLICT ... DO UPDATE`。有兩個斷言因為行為改變而改變：
上面的 Step 9 Market PIT 斷言，以及它的 `unknown_publication_observations` 計數，
現在是零。

第一次嘗試修改 fixture 時，用了一個寬鬆的腳本化改寫，弄壞了 65 個測試。它被還原，
而不是在上面打補丁，然後依照精確的模式重做。

## 接線暴露出的循環 import

`evidence` 從 `ingestion.models` import `IngestPurpose`，而 `ingestion.daily_market`
import 證據政策——所以先 import 任一個 package，都可能讓另一個只初始化一半。
`IngestPurpose` 和 `ArtifactOrigin` 現在放在 `stock_data_center/provenance.py`，不屬於
這兩個 package：它們是生命週期*宣告*、政策*讀取*的詞彙，所以屬於任一邊都是錯的。

## 驗證

從零 migrate 到 `4d9f2a6c8b17` 的資料庫：

```text
378 passed, 3 skipped, 1 warning
```

本 step 之前的基準：364。

## Code review 發現

七項發現，在改動任何東西之前都經過驗證；沒有一項是誤報。

| # | 發現 | 修正 |
| --- | --- | --- |
| 1 | 續跑時，`purpose` 來自當下的呼叫，而 `captured_at` 來自原本的 run，所以一個抓取後中斷的 `gap_fill` 可以用 `first_capture` 重跑，並在較早的時刻宣稱抓取界限 | 兩者現在都從執行抓取的那次 run 讀回。purpose 屬於那次 run，而不是續跑它的呼叫。 |
| 2 | 否證是從 `version_created` 推導的，所以重新匯入會附加先前一次 run 刻意不寫的那條規則——而且是寫進只可附加的儲存 | 否證現在依據該版本已儲存的任何*經證明*的抓取。已經帶有抓取證據的版本也不再收到無意義的 `unknown` 列。 |
| 3 | migration 那兩個以外的 `daily_price` 來源會中止匯入，與「預設不啟用任何東西」矛盾 | 沒有宣告規則的來源退回 `unknown`，和以前完全一樣。明確失敗只保留給*確實*對應了規則、卻無法承載結果的來源——也就是做到一半的 migration。 |
| 4 | `ArtifactOrigin` 被 import 之後又重新定義，所以兩個 class 不是同一個，跨兩者的 `isinstance` 為 false | 刪除本地定義（是我先前一次編輯留下的）。已在執行期驗證。 |
| 5 | 去重探測使用的欄位比 hash 少，所以真正的新列可能被回報為已去重 | 探測現在使用 hash 涵蓋的所有欄位。 |
| 6 | 選擇加入時取代了允許清單，而不是加入；downgrade 之後清單仍是擴大的，卻沒有規則會產生那些類型 | upgrade 取聯集；downgrade 還原。以 downgrade／再 upgrade 往返檢查：允許清單回到 `{official}`。 |
| 7 | 規則和允許清單逐列查詢，每個證券月約 60 次往返 | `bind()` 每次寫入只解析兩者一次。 |

## 已確認的範圍排除

- 只有 `daily_price` 選擇加入，因為它是目前唯一同時有 adapter 和規則的資料集。
  Steps 17–24 各自讓自己的來源選擇加入。
- 不寫入 `legacy_capture_bound` 或 `press_report_bound` 證據；那些需要 Steps 22 和
  23 的舊系統檔案庫 importer。
- 金融業財務報表的規則仍然未定義，這是刻意的決定。
