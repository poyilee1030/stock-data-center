# Financial Statements (iXBRL)

MOPS `t164sb01` serves one iXBRL HTML document per `(CO_ID, SYEAR, SSEASON,
REPORT_ID)`. The measured source facts are in `docs/source_field_audit.md` §4.8;
the storage design is ADR-0027 "35-c 定案".

## Reading a MOPS document (Step 23-a)

`stock_data_center.ingestion.ixbrl` turns one `t164sb01` response into a
`ParsedIXBRLReport`: the filing's own header, its `XBRLContext` objects, its
unit identities, and its `ix:nonFraction` facts with the statement row each was
printed in. It writes nothing and fetches nothing.

The documents are rendered HTML with the instance inlined, and they are not
well-formed XML, so the parser reads them as text with case-insensitive
patterns. The measured source facts behind each rule are in
`docs/source_field_audit.md` §4.8.

- **Encoding is cp950.** MOPS declares `charset=big5`, but byte `0xA1E3` is the
  Microsoft `～` U+FF5E, not Big5's `∼` U+223C. Archived copies are the same
  document re-encoded as UTF-8, so UTF-8 is tried first and cp950 second.
- **Concepts are Clark notation**, resolved through the document's own `xmlns`
  declarations. An undeclared prefix fails closed. The single re-serialized
  document that lowercased its prefix declarations is resolved
  case-insensitively only when exactly one declared prefix matches, and the
  repair is recorded on the report.
- **Header values are enumerations.** `ReportType`, `ReportCategory`, `Market`
  and `IndustrySector` are parsed into enums; an unknown value is an error, not
  a passthrough. `is_financial_industry` covers the four financial taxonomies —
  `Miscellaneous industry merging` is not one of them. `market_code` is `sii` or
  `otc` and `None` for the emerging, public and non-public filers the archive
  also holds.
- **A unit is read, never guessed.** A ratio unit that the parser cannot read as
  a ratio is an error, because falling back to its first measure would label
  earnings per share as a currency. A repeated context or unit id is accepted
  only when the two declarations are identical.
- **The transformation is checked.** Only `ixt:numdotdecimal` is implemented;
  `ixt:numcommadecimal` reads the same text the other way round, so an
  unimplemented `format`, or a numeric fact with none, fails closed.
- **Scale and sign are applied, never inferred.** `scale="3"` on a printed
  `89,680,417` is 89,680,417,000; `sign="-"` negates. Facts whose context or
  unit the document never defined fail closed.
- **Narrative `escape="true"` blocks are counted, not returned as facts.** They
  are whole HTML notes and are not stored (see below).
- **A cell with no number is reported, never invented.** A short placeholder
  (`-`, `無`, `註二`) keeps its fact with `value=None` and `is_placeholder`; a
  paragraph of narrative written into the numeric element is counted in
  `malformed_numeric_facts` and is not a fact. Both are visible to the writer
  instead of being absorbed. `NaN` and `Infinity` are not amounts and are
  refused outright: `NaN != NaN` would break the comparison that decides
  whether a document changed.

### `mops-xbrl-context-role:v1`

`classify_context_role` is the versioned rule for what a context means to this
source, kept for the EPS summary that Step 26 computes on demand. A current period is a dimensionless duration context of this entity
ending on the filing's period end. Within that: the fiscal-year start means
year-to-date, or annual in Q4; the quarter start means the single quarter; a Q1
document's one current context is the single quarter. Everything else —
including every prior-year comparative, every instant, and every dimensional
context — is `other`. The rule never reads a role out of a duration's length,
so a short duration alone can never make a context a single quarter.

## Storing a document (Steps 23-b, 35-c-2)

What is stored is the scope legacy `stock_db` stored: the balance sheet, the
statement of comprehensive income and the statement of cash flows — legacy's
`balance_sheet_xbrl`, `income_statement_xbrl` and `cash_flow_xbrl`.
權益變動表, the notes, the 附表 and the `escape="true"` narrative blocks stay in
the raw document; whether they are ever stored is a decision the ROADMAP takes at
its end (owner decision, 2026-09-21).

**The statement is the document's own.** Each statement is marked by an anchor
`<div id="BalanceSheet">`, `<div id="StatementOfComprehensiveIncome">` and
`<div id="StatementsOfCashFlows">`, each followed by exactly one `<table>`.
Every one of the 45,324 archive documents prints all three anchors exactly
once, and every `ix:nonFraction` inside those tables carries a 會計科目代碼 —
16,180,359 facts in the 42,750 documents inside the v1 universe, none without
a code (scan of 2026-09-21). A missing or repeated anchor fails closed.

**The statement is part of fact identity.** `ifrs-full:CashAndCashEquivalents`
is the balance sheet's `1100` and the cash-flow statement's `E00210` — the same
instant, unit and number printed as two statement rows, four such collisions in
every in-scope document. A fact of `financial_report_facts` is therefore
identified by `(report_id, statement, concept, period_start, period_end)`; an
instant has `period_start` NULL, and the constraint is `NULLS NOT DISTINCT`.

`account_code` is stored beside it as content, not identity: it is the row
identity legacy `*_xbrl` keyed on and what reconciliation matches code ↔
concept against, but the concept already separates the two
`ProfitLossBeforeTax` rows that share a statement, a period and a unit
(`A00010` is `ifrs-full`, `A10000` is `tifrs-scf`). 108 of 1,612
(statement, code) pairs map to different concepts in different years, so both
are kept.

**A document is refused whole** when a fact in the three statements carries a
dimension, scenario or segment (`dimensioned_fact`; none of the 16,158,302
stored facts has one, so the key has no place for it — ADR-0027 overrides
CLAUDE.md §33), is not a numeric period fact (`unstorable_fact`), or repeats a
fact identity. Financial-industry issuers and filers outside 上市/上櫃 are
refused at the adapter boundary; that answer is final and not asked again.

**A version is the whole report.** `t164sb01` publishes no filing id, no
publication instant and no amendment sequence, and it serves the currently
effective — possibly amended — report. The writer compares the fetched
document's category and every fact (statement, concept, period, code, unit,
value) with the key's latest version; anything different is a new
`financial_reports` row carrying its full set of facts, written in one
transaction, so a fact a restatement drops stays representable. An unchanged
document logs its fetch and writes nothing.

`REPORT_ID` is `C` for 合併報表 and `A` for 個體報表, and a filer files one of
them per quarter: MOPS answers the other with 98 bytes of `檔案不存在!` under
HTTP 200 (measured 2026-09-21 on 1101 and 1342). That page is a source answer,
not a failure, so the writer asks for `C`, then `A`; both answering it means
the report is not filed yet.

## Publication

`financial_reports.published_at` is stored on a key's first version: the fetch
instant of a first capture, or what the legacy archive proves (a 2025Q4-onward
daily-job file's mtime, otherwise the `financial_statements_general@1` statutory
instant; audit §7.6). Any other fetch purpose proves nothing and stores NULL,
which is Market-PIT invisible. A later version is public from its own
`recorded_at`.

There is no calendar-quarter availability shortcut: the existence of a Q4
report in today's database cannot make it visible in February. A quarter counts
as complete only after a fetch following its statutory deadline, which is why a
backfill asks again for a quarter fetched before then.

The EPS summary, the Q4 single quarter and every ratio built on these facts are
canonical derived data. `valuation_metrics:v1` (Step 26-f) derives the Q4 single
quarter as the annual figure less the third quarter's year to date, as legacy
did, and has none without the third quarter; it reads each quarter's latest
version and counts it from its first version's `published_at`.

A writer holds the job lock `financial_reports/mops_t164sb01` shared, beside its
own stock's lock, until it commits: writers of different stocks run side by
side, and a derived run takes the job lock exclusively so it never reads a
report mid-transaction (`derived_store._fix_inputs`).
