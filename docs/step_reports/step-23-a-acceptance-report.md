# Step 23-a 驗收報告

狀態：IN REVIEW (#39)

範圍：財務報表的第一部分，iXBRL parser 與文件契約。這個 step 新增：

- `src/stock_data_center/financials/ixbrl.py`：一份 MOPS `t164sb01` 回應 →
  header、`XBRLContext`、unit identity、`ix:nonFraction` 事實，以及
  `mops-xbrl-context-role:v1`
- `scripts/scan_xbrl_archive.py`：用同一支 parser 掃真實檔案庫的證據工具
- 十二個 fixture 與 `tests/unit/test_step23a_ixbrl_parser.py`（55 個測試）

**沒有**來源宣告、migration、抓取、寫入。資料庫裡仍然沒有任何財務報表資料，
要到 23-c 才有。

Schema 影響：無。PIT 影響：無（沒有任何版本或證據被寫入）。

規模：`src/` +654／−0 行，低於約 800 行的拆分門檻（`CLAUDE.md` §1）。另外提交一支
201 行的腳本、458 行測試與十二個 fixture（共約 360 KB）。

## 為什麼 Step 23 拆成三部分

owner 於 2026-09-20 決定。檔案庫是 45,324 份文件、26 GB，一份報表有 428–4,421 個
`ix:nonFraction`（262–8,414，平均 913）。parser、兩個來源的匯入路徑、全量 backfill 與對帳合在一個 PR 遠超過
可審閱的大小，而接縫是現成的，且每一段自己成立：

- **23-a**（本 step）只有解析，沒有消費者，也沒有任何寫入。
- **23-b** 接上官方與檔案庫 adapter 及 importer；版本先帶 `unknown` 證據，只有
  System PIT 看得到——與 22-a 相同的偏晚而不偏早。
- **23-c** 才跑全量 backfill、抽樣關卡、發布證據與舊系統對帳。

## 這個 step 中發現的來源事實

每一項都以實測為準，數字寫進 `docs/source_field_audit.md` §4.8「Step 23-a findings」。

1. **編碼是 cp950，不是它自己宣告的 big5。** MOPS 在 `<META>` 裡寫 `charset=big5`，
   `Content-type` 沒有 charset。`0xA1E3` 在 cp950 是 `～`(U+FF5E)，在 Python 的
   `big5` 是 `∼`(U+223C)。**原本以為**照宣告用 big5 就對了；實測是舊系統檔案庫用的是
   cp950 對映，而且 2026-09-20 抓下來的 1101 2025Q1 官方回應（1,955,768 bytes）以
   cp950 解碼後，與檔案庫那份 UTF-8 文件**逐字元完全相同**。有回歸測試：那份 fixture
   含有這個位元組，換成 big5 會紅。
2. **要排除的金融業是四個，不是「非一般業都算」。** `IndustrySector` 有六個值，
   `Miscellaneous industry merging`（1409、1718、2207、2905）**不是**金融業——舊系統
   `quarterly_reports_xbrl` 對這四檔各有 52 季。金融業是 `Financial holding` 278、
   `Broker-dealer` 252、`Banking and savings institution` 251、`Insurance` 126，
   共 907 份（掃描輸出的券商是 249，差的 3 份是下面第 8 點那三份解析失敗的 2855）。
3. **1,667 份文件不在 v1 宇宙內。** 檔案庫含興櫃、公開發行、非公開發行的申報，因為
   舊系統 `active_stocks.txt` 收了它們。parser 給它們 `market_code = None`，
   23-b 在邊界擋掉。
4. **有一份文件是被申報者自己的瀏覽器重新序列化過的。** 1519 2021Q2：大寫標籤、
   小寫屬性名、`Consolidated report` 與 `Commercial and industrial` 被斷行，檔案裡
   還留著 `file:///C:/Users/…` 連結。小寫化把 `xmlns:tifrs-SCF` 變成
   `xmlns:tifrs-scf`，但兩個事實名稱仍寫 `tifrs-SCF:`。**原本以為**全域 case-insensitive
   就夠了；prefix 在 XML 裡是大小寫敏感的，所以另外處理：只有在剛好對到一個已宣告
   prefix 時才以不分大小寫解析，並把每一次修補記在 `prefix_case_repairs` 上，
   讓 23-b 看得到而不是猜。
5. **會計科目代碼在列裡，不在事實裡。** 每一列是
   `<td>1100</td><td><span class="zh">…</span><span class="en">…</span></td>` 加上
   每個 context 一格金額。舊系統 `*_xbrl` 以這個代碼為 key，所以 parser 把代碼與
   中英文標籤跟著事實一起留下——這就是 23-c 的「代碼 ↔ concept QName」對帳所需，
   不必為此建 codebook 表（`docs/data_domain_inventory.md` 已註明）。
6. **instance 結構高度一致。** unit 只有四種、`format` 只有 `ixt:numdotdecimal`、
   `scale` 只有 `3`／`0`／`-2`，沒有 `xsi:nil`、沒有 typed dimension、沒有
   `<xbrli:segment>`、沒有 forever period。維度只以 `<xbrldi:explicitMember>` 出現在
   `<xbrli:scenario>` 裡。parser 對這四件沒見過的形狀 fail closed。

7. **金額格裡不是數字的兩種情況。** **原本以為**金額格一定是數字，第一次全量掃描
   有 100 份文件因此失敗。實際上有兩種：短的佔位符（`-`、`無`、`null`、`註二`，
   13 個事實分布在 10 份文件）——它不是 0 也不是 `xsi:nil`，所以留下沒有值的事實並
   保留印出的字；以及整段敘述被寫進數值元素（140 個，分布在 87 份文件，有的
   `unitRef=""`、有的照樣寫 `unitRef="TWD" scale="3"`）——那不是事實，計入
   `malformed_numeric_facts`。來源裡唯一能分辨兩者的只有長度。
8. **三份文件真的壞掉，全是 2855。** 引用了文件裡從未定義的 context，其中 2022Q4 的
   `AsOf2022121` 是打錯的日期。金額無法定位到時點，所以 fail closed；2855 是券商，
   本來就不在 v1。
9. **41,397,846 個數值事實。** 每份 262–8,414 個，平均 913 個，另有 13,344,559 個
   敘述區塊。這是 23-c 要規劃的量級。

## 驗收

| 驗收標準 | 結果 | 證據 |
| --- | --- | --- |
| 每一種實測到的來源變體都有 fixture 與測試 | PASS | 十二個 fixture：一般合併、Q3（季／累計）、Q4（年度）、大寫序列化、券商（金融業、個體）、興櫃、mim、官方 cp950 位元組、`-` 佔位符、`unitRef=""` 敘述、整段敘述寫進金額格、`註二`。41 個測試 |
| cp950 與 big5 的差異有回歸測試 | PASS | `test_the_official_response_is_cp950_even_though_it_declares_big5` 與其守門測試 |
| 未知的 header 值 fail closed | PASS | `test_an_unknown_header_value_fails_closed`、`test_a_missing_header_fact_fails_closed` |
| QName 以 Clark notation 產出，prefix 由文件自己的 xmlns 解析 | PASS | `test_concept_names_resolve_to_clark_notation_through_the_documents_xmlns`、`test_a_prefix_the_document_never_declared_fails_closed` |
| EPS 期間角色由 `mops-xbrl-context-role:v1` 判定並通過 Step 5 的 classifier | PASS | 四個 parametrize 案例（Q1 quarter、Q3 quarter/ytd、Q4 annual）＋去年同期、instant、帶維度皆為 `other` |
| 金額格裡不是數字時不猜成 0 | PASS | `test_a_dash_printed_where_an_amount_belongs_is_kept_as_a_placeholder`、`test_a_footnote_marker_where_an_amount_belongs_is_a_placeholder`、`test_a_whole_note_pasted_into_an_amount_cell_is_prose_not_a_fact`、`test_a_non_fraction_the_filer_used_for_prose_is_not_a_fact` |
| 掃描腳本在真實檔案庫上跑完並記錄分布 | PASS | 見下方全量掃描輸出，45,321／45,324 |

測試：`pytest tests/unit` 608 passed；`pytest tests/integration` 431 passed、3 skipped
（live-source，需 `RUN_LIVE_SOURCE_TESTS=1`）。
沒有 migration，所以沒有 alembic 檢查項。

### 先紅後綠

`test_step23a_ixbrl_parser.py` 在 `financials/ixbrl.py` 存在之前就先寫好並跑紅
（collection error）。實作之後 4 個仍紅：三個是 fixture 內容問題，一個
（`test_uppercase_tags_and_lowercase_attributes_parse`）暴露了上面第 4 點的 prefix
大小寫缺陷。prefix 修補的兩個測試是在那之後補的，但已用停用 fallback 的方式確認會紅：

```text
FAILED test_uppercase_tags_and_lowercase_attributes_parse
FAILED test_a_line_break_inside_a_header_value_is_normalized_not_rejected
FAILED test_the_lowercased_prefix_is_resolved_and_recorded_not_silently_accepted
3 failed, 33 passed
```

第 7 點那四個測試也是先紅後綠：第一次全量掃描把 100 份失敗文件攤開來之後，先寫測試
（`3 failed, 36 passed`／`2 failed, 39 passed`），再改 parser。掃描共跑三輪：
100 → 12 → 3 份失敗。

## 全量檔案庫掃描

`python scripts/scan_xbrl_archive.py --workers 12`：

```text
scanning 45,324 documents under ~/GitHubLL/my_stock_project/data/raw/xbrl with 12 workers
documents          45,324
parsed             45,321
failed             3
  2021Q3_2855_20211115.html: fact references undefined context AsOf20210331
  2021Q4_2855_20220331.html: fact references undefined context AsOf20210331
  2022Q4_2855_20230331.html: fact references undefined context AsOf2022121

report_type
   45,020  Financial report (general)
      240  Financial Report (retrospective - material)
       34  Financial report (retrospective)
       27  Financial report (first time adoption)

report_category
   40,992  Consolidated report
    4,329  Individual report

market
   23,580  Listed company
   20,074  Over-the-counter
      779  Emerging stock market
      544  Emerging Stock Company (Applying for listing on TWSE/GTSM)
      320  Public company
       24  Non-public company

industry_sector
   44,313  Commercial and industrial
      278  Financial holding
      251  Banking and savings institution
      249  Broker-dealer
      126  Insurance
      104  Miscellaneous industry merging

units
   45,321  iso4217:TWD
   45,321  iso4217:TWD/xbrli:shares
   45,321  xbrli:pure
   45,321  xbrli:shares

scales
   45,321  0
   45,321  3
   42,066  -2

taxonomy schemaRef
   35,519  tifrs-ci-cr-2020-06-30.xsd
    3,646  tifrs-ci-ir-2020-06-30.xsd
    1,643  tifrs-ci-cr-2026-03-31.xsd
    1,634  tifrs-ci-cr-2025-06-30.xsd
    1,411  tifrs-ci-cr-2019-03-31.xsd
      254  tifrs-fh-2020-06-30.xsd
      204  tifrs-bd-cr-2020-06-30.xsd
      167  tifrs-ci-ir-2026-03-31.xsd
      166  tifrs-ci-ir-2025-06-30.xsd
      161  tifrs-basi-cr-2020-06-30.xsd
      127  tifrs-ci-ir-2019-03-31.xsd
      112  tifrs-ins-ir-2020-06-30.xsd
       92  tifrs-mim-2020-06-30.xsd
       69  tifrs-basi-ir-2020-06-30.xsd
       23  tifrs-bd-ir-2020-06-30.xsd
       13  tifrs-fh-2025-06-30.xsd
       11  tifrs-fh-2019-03-31.xsd
        9  tifrs-bd-cr-2019-03-31.xsd
        9  tifrs-bd-cr-2025-06-30.xsd
        7  tifrs-basi-cr-2019-03-31.xsd

current EPS period roles present per document
   22,098  current_single_quarter, current_year_to_date
   11,931  current_single_quarter
   10,779  current_full_year
      513  current_year_to_date

prefix case repairs 2
        2  tifrs-SCF->tifrs-scf

header disagrees with file name  0

placeholder facts  13 in 10 documents
prose in nonFraction 140 in 87 documents

numeric facts      41,397,846
narrative blocks   13,344,559
facts per document min 262 max 8,414 mean 913
```

45,324 份中 45,321 份解析成功。失敗的三份都是 2855（統一證券，券商，本來就不在 v1）：
2021Q3 與 2021Q4 引用文件裡不存在的 `AsOf20210331`，2022Q4 引用打錯的 `AsOf2022121`。
金額無法定位到時點，fail closed 是對的。

513 份文件只有 `current_year_to_date` 沒有單季欄位（Q2／Q3 只印累計數），
classifier 照實反映，不補一個不存在的單季 context。

## 對 #39 審查意見的處理

審查提了 10 項。每一項在改成 fail-closed 之前，先對全部 45,324 份文件量過會不會擋掉
真實文件（`scratchpad` 的探測腳本；數字寫進 audit §4.8）：

| 條件 | 全檔出現次數 |
| --- | ---: |
| 金額是 `NaN` 或 `Infinity` | 0 |
| `format` 不是 `ixt:numdotdecimal` | 0 |
| 數值事實沒有 `format` | 132，全部是 `unitRef=""` 的 prose（早已被擋） |
| 同一 prefix 宣告成兩個 URI | 0 |
| 大寫 `XMLNS:` | 0 |
| 括號負數（不論有無 `sign`） | 0 |
| 重複的 `xbrli:unit` id | 0 |
| 重複的 `xbrli:context` id | **1**（2886 2025Q4 的 `AsOf20241231`，兩份定義逐字相同） |
| 嚴格 pattern 讀不出來的 `xbrli:divide` | 0 |
| 一列有多組 `class="zh"` 且含 fact | 0 |

處理：

1. **NaN／Infinity**（實測屬實：`value=Decimal('NaN')`、`is_placeholder=False`、不計入
   malformed）→ `is_finite()` 不成立就 raise。`NaN != NaN` 會破壞 §24 的
   business content hash 與 §26 的 dedup。
2. **重複 id 靜默覆蓋**（實測屬實）→ 內容不同 raise；逐字相同接受（2886 那份的行為
   有測試釘住）。unit 同樣處理。
3. **`xbrli:divide` 退化成單一 measure** → 只要出現 divide/numerator/denominator 就
   必須讀成比率，讀不出來 raise。否則 EPS 會被標成 `iso4217:TWD`。
4. **`format` 不驗證** → 必填且限 `ixt:numdotdecimal`，並存進 `ParsedFact.format`。
   23-b 的 forward capture 一旦遇到 `ixt:numcommadecimal` 會反向解讀小數點。
5. **`_XMLNS` 缺 `re.I`、不檢查衝突** → 兩者都補。
6. **括號負數與 `sign="-"` 併用會二次取負** → raise。附帶事實：全檔 0 次括號負數，
   所以那段括號處理本來就沒有來源支持。
7. **`dict(_SPAN.findall(row))` 取最後一組** → 改取第一組。全檔沒有「含 fact 且多組
   zh span」的列，所以目前沒有實際影響，但取第一組才是對的。
8. **`_row_labels` 每個 fact 重跑** → 每列算一次。
9. **編碼嗅探**（部分同意）→ 加 `encoding=` 參數，指定了就只試那一個、失敗即報錯；
   預設仍是嗅探。原則採納，但「UTF-8 只是機率性排除 cp950」言過其實：整份 2 MB
   中文 cp950 文件通過 UTF-8 strict 解碼實務上不會發生。
10. **用 `assert` 驗證** → `ixbrl.py` 改成 `IXBRLParseError`。`classification.py`
    那兩個是 Step 5 既有程式，不在本 PR 範圍。

改完重跑全量掃描，結果與改之前**完全相同**：45,321／45,324 解析成功，同樣三份 2855
失敗，13 個佔位符、140 段 prose、41,397,846 個事實。新的 fail-closed 沒有擋掉任何
真實文件。

這批測試同樣先紅後綠（12 紅 → 實作 → 綠）。其中兩個測試的第一版 mutation 沒打中
程式路徑（`<xbrli:unitNumerator-x>` 的 `\b` 仍然匹配；`sign` 屬性順序寫錯），
改成真的會觸發的 mutation 之後，再用停用防護的方式確認會紅。

## 已知限制與刻意延後

- **敘述性區塊沒有解析成事實。** `escape="true"` 的 `ix:nonNumeric` 是整段 HTML 附註，
  parser 只計數。要不要儲存、怎麼儲存是 23-b 的決定。
- **沒有跨文件的檢查。** 同一 `(year, quarter, symbol)` 不重複、檔名與 header 一致
  這兩件事由掃描腳本驗證，尚未變成 importer 的不變條件——那屬於 23-b。
- **`prefix_case_repairs` 目前只有記錄。** 由 23-b 決定要接受還是隔離。
- **括號負數沒有來源支持。** 全檔 0 次；那段處理保留，但與 `sign` 併用時 fail closed。
- **教學章**：這個 repo 不產出 `docs/stepNN.html`，所以 `cold-read` 不適用。
