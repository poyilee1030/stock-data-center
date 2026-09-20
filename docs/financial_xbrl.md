# Financial Filings and XBRL

## Phase 5 scope

Phase 5 provides cache-free writes and PIT-safe reads for financial filing
aggregates. A filing is a draft parent with XBRL facts and an optional curated
quarterly summary. Only the PostgreSQL-generated seal makes the aggregate
visible. Phase 5 does not add a REST API, cache, TTM EPS, ROE, valuation, or
other derived calculators.

The source `filing_key` identifies one source-native filing revision. A
corrected business filing uses a new source revision key and remains an
independent business version. Publication evidence is appended separately, so
learning, correcting, or retracting a publication time never mutates the
filing or creates a false business revision.

## Aggregate and lineage contract

`FinancialFilingWriter` creates a draft, appends facts and curated summary
metrics, and requests a seal. PostgreSQL serializes sealing and every child
mutation by locking the same parent row. It computes the business hash from
the filing header and ordered children and supplies the trusted seal time.

After sealing, PostgreSQL rejects parent, fact, summary, and seal mutation.
Resolvers ignore drafts. Market PIT additionally requires affirmative accepted
publication evidence; System PIT uses the trusted seal time.

Repeated observations of the same `filing_key` reuse its version while
`financial_filing_version_observations` retains each artifact/run pair. The
generic `publication_evidence_observations` table provides the same guarantee
for deduplicated evidence. Both links are append-only and DB-validated against
the exact `financial_filing` dataset and source. Existing filings are backfilled
with their original lineage by the Phase 5 migration.

## XBRL identity

Concepts use canonical Clark notation:

```text
{namespace-uri}local-name
```

This prevents two taxonomies with the same local concept name from colliding.
Each non-nil fact contains exactly one numeric or text value. Its
storage-generated `context_hash` covers:

```text
entity identifier
period type and dates
explicit dimensions
typed dimensions
scenario
segment
```

PostgreSQL JSONB canonicalizes object-key order. Fact identity is filing,
QName, context hash, and unit, so dimensional facts coexist and a semantically
duplicate fact is rejected.

Legal XBRL `xsi:nil="true"` facts are stored as `is_nil=true` with both value
columns null. A non-nil fact must have exactly one numeric/text value. Nil is
therefore distinct from an absent fact and numeric zero, and `is_nil` is part of
the sealed aggregate's canonical business hash.

## Publication and Q4 semantics

`published_at` records when the filing became public. DB-generated
`recorded_at` records when the Data Center learned the evidence. Market PIT
requires both cutoffs; unknown publication is invisible. Source capability,
accepted evidence types, correction, retraction, and deterministic ranking use
the shared Phase 2 resolver.

There is no calendar-quarter availability shortcut. In particular, the
existence of a Q4 filing or EPS value in today's database cannot make it visible
in February. A Q4 result becomes visible only at its authoritative publication
instant and only under a knowledge cutoff that includes that evidence.

## Curated actual EPS

`quarterly_financial_summary` exposes stable curated facts from the selected
filing. Every row has a required composite same-filing foreign key to its exact
`financial_facts` source. A DB trigger requires the summary value/unit to equal
that non-nil numeric fact and checks that its duration matches the declared
period basis. Sealing revalidates the same contract so a draft fact cannot be
changed underneath an existing summary. The service returns the complete source
fact, including QName, context hash, dimensions, and unit, with each summary
metric.

`basic_eps` is the Phase 5 actual-EPS metric code. Its `period_basis` is always
one of `quarter`, `ytd`, or `annual`; these values can coexist without a summary
identity collision. Other curated balance-sheet metrics may explicitly use the
`instant` basis. The writer never infers basis from `report_quarter` alone.
A `quarter` source fact must use a duration of at most 100 days ending at the
filing period end. `ytd` must start at the filing period start, and `annual`
must use a full-year Q4 duration.

### Source context classifier

Duration length is only a defensive sanity check; it is not the authoritative
classifier. Before creating `basic_eps`, a source adapter must produce a
`SourceContextClassification` containing the source-native context reference,
the versioned classifier rule, the exact expected context dates, and one of
these validated source roles:

```text
current_single_quarter
current_year_to_date
current_full_year
other
```

`classify_eps_period_basis` maps only the first three roles to `quarter`, `ytd`,
or `annual`, verifies the role against the actual normalized XBRL context and
filing period, and rejects `other` or inconsistent/suspicious contexts. The
writer requires this classification for every `basic_eps` summary. Thus a
short duration alone can never authorize a quarter classification; the DB
duration rule remains a second-line invariant for all write paths.

The future real MOPS adapter must implement and permanently test its versioned
source-role rule (for example `mops-xbrl-context-role:v1`) and pass every EPS
context through this classifier before constructing a curated summary. It must
not label a context from dates alone.

`FinancialFilingService.actual_eps` requires the caller to specify this basis,
first resolves the filing through the same seal, source, evidence, and PIT
rules, and then reads the matching `basic_eps` from that exact version. A Q4
full-year value is consequently available as `annual`, never as `quarter`.
The service never searches the current database for an EPS value.

The summary is not a derived-metric engine. Reusable TTM EPS, profitability,
margin, and valuation calculations remain in the later canonical-derived phase
and must inherit PIT visibility from these resolved inputs. In particular,
`annual EPS - Q1 - Q2 - Q3` is not performed in Phase 5.

Legacy summary rows can migrate only when exactly one same-filing numeric fact
matches their value/unit and the legacy metric code explicitly identifies a
quarter, accumulated/YTD, or annual basis. The migration aborts instead of
guessing for ambiguous metrics such as an unqualified `basic_eps`.

## Step 23-a: reading a MOPS document

`stock_data_center.financials.ixbrl` turns one `t164sb01` response into a
`ParsedIXBRLReport`: the filing's own header, its `XBRLContext` objects, its
unit identities, and its `ix:nonFraction` facts with the statement row each was
printed in. It writes nothing and fetches nothing; Steps 23-b and 23-c add the
adapters, the import path and the evidence.

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
  are whole HTML notes; whether any of them is stored is a Step 23-b decision.
- **A cell with no number is reported, never invented.** A short placeholder
  (`-`, `無`, `註二`) keeps its fact with `value=None` and `is_placeholder`; a
  paragraph of narrative written into the numeric element is counted in
  `malformed_numeric_facts` and is not a fact. Both are visible to the importer
  instead of being absorbed. `NaN` and `Infinity` are not amounts and are
  refused outright: `NaN != NaN` would break business-content identity.

### `mops-xbrl-context-role:v1`

This is the versioned source-role rule the section above requires of a real
adapter. A current period is a dimensionless duration context of this entity
ending on the filing's period end. Within that: the fiscal-year start means
year-to-date, or annual in Q4; the quarter start means the single quarter; a Q1
document's one current context is the single quarter. Everything else —
including every prior-year comparative, every instant, and every dimensional
context — is `other`, which `classify_eps_period_basis` refuses. The rule never
reads a role out of a duration's length.
