# Step 12 驗收報告

狀態：IN REVIEW

範圍：強化台灣公司行動契約

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 明確的股票分割與反分割 | PASS | `stock_split` 和 `reverse_split` 要求正數的 `old_shares`／`new_shares`，且方向相反，由資料庫強制。 |
| 區分盈餘配股與資本公積配股 | PASS | 分開的行動類型，以及 `earnings_stock_ratio`／`capital_surplus_stock_ratio` 欄位；回歸測試確認儲存的列和 hash 各自不同。 |
| 明確的現金增資與減資 | PASS | `rights_issue` 要求 `rights_ratio`；減資要求遞減的新舊股數，並附上有型別的類別。退還現金的減資要求正數的每股退還金額，而彌補虧損類的減資則拒絕此欄位。 |
| 安全的 migration 生命週期 | PASS | 可表示的舊歷史能完整往返。新的、無法表示的歷史會在修改之前拋出 SQLSTATE `P0001`。 |
| 拒絕不可能的值 | PASS | 領域層和 PostgreSQL 的檢查涵蓋缺少的條件、零／負比率、不完整的股數對、錯誤的股數方向，以及日期順序。 |
| Hash 完整性 | PASS | 儲存層的 trigger 對標準化的 revision JSON 做 hash；新的語意欄位都沒有被排除。回歸測試能區分法定的股票股利類別，以及減資類別／退還現金的改變。 |
| 原始價格不變 | PASS | migration 不改動 `daily_price_versions`；受保護 downgrade 的回歸測試確認失敗之後，原始 OHLC 值和 hash 逐 byte 相同。 |
| 具代表性的永久回歸測試 | PASS | 涵蓋現金股利、盈餘／資本公積配股、分割、反分割、現金增資、減資，以及除權息合併的語意。 |

## Migration 保存證據

有資料的 downgrade 回歸測試建立一筆退還現金的減資 revision 及其觀察 provenance，
以及一筆原始每日 OHLC revision。嘗試 downgrade 到 `4d2a6f8c1e30` 會回傳 `P0001`。
Alembic 仍停在 `7c9e2a4b6d81`；schema、事件 revision、觀察列和原始 OHLC revision
都保持完整。

## 驗證

乾淨的 PostgreSQL 資料庫：

```text
239 passed, 3 skipped
```

三個 skip 是 Step 11 需要手動開啟的官方端點 live 測試，不在這個只涉及
schema／領域的 PR 範圍內。Alembic metadata drift 檢查通過。有一個 SQLAlchemy
reflection 警告，來自刻意設為 `NOT VALID` 的 constraint：它們保留 PR 之前的
舊列，同時對所有新寫入強制執行。

## 已確認的範圍排除

Step 12 沒有新增外部來源 adapter、歷史公司行動 backfill、價格跳動推斷、還原
因子、還原價格、總報酬序列、技術指標、交易日曆行為，也沒有任何 Redis／快取工作。
