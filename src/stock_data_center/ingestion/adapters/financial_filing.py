"""The MOPS financial-statement adapter: `t164sb01` (audit §4.8).

`mopsov.twse.com.tw/server-java/t164sb01?step=1&CO_ID=…&SYEAR=…&SSEASON=…&REPORT_ID=…`,
a plain GET, one iXBRL document per filer and quarter. Legacy fetched exactly
this URL; the new site's `t164sb04` JSON renders the same statements without a
concept QName, a context, a unit or `decimals`, so it cannot meet CLAUDE.md §33
and is not a substitute.

`REPORT_ID` is `C` for 合併報表 and `A` for 個體報表, and a filer files one of
them per quarter: 1101 2025Q1 answers `A` with `檔案不存在!` and 1342 answers
`C` the same way (measured 2026-09-21). That page is a source answer, not a
failure, so it is reported as `no_such_report` and the caller asks for the
other id.

**What is stored is the scope legacy `stock_db` stored**: the balance sheet,
the statement of comprehensive income and the statement of cash flows, which
the document marks with its own `id="BalanceSheet"`,
`id="StatementOfComprehensiveIncome"` and `id="StatementsOfCashFlows"` anchors.
Each anchor appears exactly once in every one of the 45,324 archive documents,
and every `ix:nonFraction` inside the table that follows it carries a
會計科目代碼: 16,180,359 facts, none without one. 權益變動表, the notes, the 附表
and the `escape="true"` narrative blocks are counted and stored nowhere;
whether they ever are is a decision the ROADMAP takes at its end.

The v1 universe is sii and otc, and Step 23 excludes the financial industries,
so an emerging, public or non-public filer and a financial holding, broker,
bank or insurer are refused here at the boundary and counted as quarantine.

The filing key is the endpoint's own request key plus a revision fingerprint:
`{security}:{year}Q{quarter}:{report id}:{fingerprint}`. MOPS publishes no
filing id, no publication instant and no amendment sequence, and it serves the
currently effective — possibly amended — report, so the same request answered
twice with the same document is one source revision and answered with a
corrected document is another (`docs/financial_xbrl.md`). The fingerprint is
taken from the normalized statement facts alone, so the archive's UTF-8 copy
of a document and the official cp950 response of it are the same revision.
"""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import ClassVar

from stock_data_center.ingestion.ixbrl import (
    STATEMENT_ANCHORS,
    IXBRLParseError,
    ParsedIXBRLReport,
    ReportCategory,
    StatementSection,
    classify_context_role,
    parse_ixbrl_report,
)
from stock_data_center.ingestion.models import (
    FilingEPS,
    FinancialFilingRequest,
    ParsedFinancialFiling,
    SourceDataError,
    SourceResource,
    StatementFact,
)
from stock_data_center.ingestion.observations import (
    FinancialFactObservation,
    SourcePeriodRole,
    SummaryPeriodBasis,
    XBRLContext,
)

HOST = "https://mopsov.twse.com.tw/server-java/t164sb01"
#: What MOPS answers, under HTTP 200, when the filer files the other report.
NO_SUCH_REPORT = "檔案不存在"
#: The versioned rule that turns a document into one source filing revision.

REPORT_IDS = {
    ReportCategory.CONSOLIDATED: "C",
    ReportCategory.INDIVIDUAL: "A",
}
REPORT_CATEGORIES = {
    ReportCategory.CONSOLIDATED: "consolidated",
    ReportCategory.INDIVIDUAL: "individual",
}
#: `mops-xbrl-context-role:v1` roles that Step 5 accepts as an EPS basis.
EPS_BASIS = {
    SourcePeriodRole.CURRENT_SINGLE_QUARTER: SummaryPeriodBasis.QUARTER,
    SourcePeriodRole.CURRENT_YEAR_TO_DATE: SummaryPeriodBasis.YTD,
    SourcePeriodRole.CURRENT_FULL_YEAR: SummaryPeriodBasis.ANNUAL,
}
BASIC_EPS = "BasicEarningsLossPerShare"
CURRENCY = "TWD"


class MOPSFinancialFilingAdapter:
    """One filer's iXBRL statement document for one quarter."""

    dataset_code = "financial_filing"
    source = "mops_t164sb01"
    version = "mops-t164sb01:v1"
    #: The archive's copy is UTF-8; the official response is cp950. Both decode
    #: to the same document, so neither is given to the parser as a fixed
    #: encoding — `decode_ixbrl_document` tries UTF-8 first and cp950 second.
    variants: ClassVar[dict[str, tuple[str, ...]]] = {
        "t164sb01_statements": tuple(
            anchor for anchor in STATEMENT_ANCHORS.values()
        )
    }

    def resource(self, request: FinancialFilingRequest) -> SourceResource:
        return SourceResource(
            resource_key=(
                f"{self.source}:financial_filing:{request.security_code}:"
                f"{request.period_label}:{request.report_id}"
            ),
            source_uri=(
                f"{HOST}?step=1&CO_ID={request.security_code}"
                f"&SYEAR={request.report_year}&SSEASON={request.report_quarter}"
                f"&REPORT_ID={request.report_id}"
            ),
        )

    def parse(
        self, content: bytes, request: FinancialFilingRequest
    ) -> ParsedFinancialFiling:
        return self._read(content, request, expected_report_id=request.report_id)

    def _read(
        self,
        content: bytes,
        request: FinancialFilingRequest | object,
        *,
        expected_report_id: str | None,
    ) -> ParsedFinancialFiling:
        security_code = request.security_code  # type: ignore[union-attr]
        report_year = request.report_year  # type: ignore[union-attr]
        report_quarter = request.report_quarter  # type: ignore[union-attr]
        if NO_SUCH_REPORT in _preview(content):
            raise SourceDataError(
                "no_such_report",
                f"{self.source} has no {expected_report_id or 'archive'} report for "
                f"{security_code} {report_year}Q{report_quarter}",
            )
        try:
            report = parse_ixbrl_report(content)
        except IXBRLParseError as error:
            raise SourceDataError("unreadable_document", f"{self.source}: {error}") from error
        header = report.header
        if (
            header.company_id != security_code
            or header.report_year != report_year
            or header.report_quarter != report_quarter
        ):
            raise SourceDataError(
                "identity_mismatch",
                f"{self.source} answered with {header.company_id} "
                f"{header.report_year}Q{header.report_quarter}, not {security_code} "
                f"{report_year}Q{report_quarter}",
            )
        if header.is_financial_industry:
            # Step 23 excludes these issuers' financial statements; their other
            # datasets stay in scope (CLAUDE.md v1 scope).
            raise SourceDataError(
                "financial_industry_issuer",
                f"{self.source}: {header.company_id} files as "
                f"{header.industry_sector.value}",
            )
        if not header.in_v1_universe:
            raise SourceDataError(
                "outside_v1_universe",
                f"{self.source}: {header.company_id} is a "
                f"{header.market.value}, not 上市 or 上櫃",
            )
        report_id = REPORT_IDS[header.report_category]
        if expected_report_id is not None and report_id != expected_report_id:
            raise SourceDataError(
                "report_category_mismatch",
                f"{self.source} answered a {header.report_category.value} for a "
                f"REPORT_ID={expected_report_id} request",
            )
        missing = set(STATEMENT_ANCHORS) - report.statement_sections
        if missing:
            raise SourceDataError(
                "statement_missing",
                f"{self.source}: the document prints no "
                f"{', '.join(sorted(section.value for section in missing))} table",
            )
        facts = self._statement_facts(report)
        if not facts:
            raise SourceDataError(
                "statement_empty",
                f"{self.source}: the three statement tables hold no amount",
            )
        fingerprint = _fingerprint(facts)
        return ParsedFinancialFiling(
            security_code=header.company_id,
            report_year=header.report_year,
            report_quarter=header.report_quarter,
            report_category=header.report_category,
            report_id=report_id,
            period_start=report.period_start,
            period_end=report.period_end,
            currency=CURRENCY,
            filing_key=(
                f"{header.company_id}:{header.report_year:04d}Q"
                f"{header.report_quarter}:{report_id}:{fingerprint[:16]}"
            ),
            revision_fingerprint=fingerprint,
            facts=facts,
            eps=self._eps(report, facts),
            market_code=header.market_code or "",
            source_fact_count=len(report.facts),
            note_block_count=report.note_block_count,
            parser_version=report.parser_version,
        )

    def _statement_facts(
        self, report: ParsedIXBRLReport
    ) -> tuple[StatementFact, ...]:
        facts: list[StatementFact] = []
        for fact in report.facts:
            if fact.statement is None:
                continue
            if fact.account_code is None:
                # Never seen: all 16,180,359 statement facts in the archive
                # carry a code. If MOPS prints one without, the row has no
                # source identity and this fails rather than inventing one.
                raise SourceDataError(
                    "statement_row_without_code",
                    f"{self.source}: a {fact.statement.value} row printed "
                    f"{fact.concept_qname} with no 會計科目代碼",
                )
            if fact.value is None:
                # A placeholder (`-`, `無`, `註二`) is not an amount and not
                # zero; it is kept as the text the filer printed.
                observation = FinancialFactObservation(
                    concept_qname=fact.concept_qname,
                    context=report.contexts[fact.context_ref],
                    unit_identity=fact.unit_identity,
                    text_value=fact.raw_text,
                    decimals=fact.decimals,
                    statement=fact.statement.value,
                    account_code=fact.account_code,
                )
            else:
                observation = FinancialFactObservation(
                    concept_qname=fact.concept_qname,
                    context=report.contexts[fact.context_ref],
                    unit_identity=fact.unit_identity,
                    numeric_value=fact.value,
                    decimals=fact.decimals,
                    statement=fact.statement.value,
                    account_code=fact.account_code,
                )
            facts.append(
                StatementFact(
                    statement=fact.statement,
                    account_code=fact.account_code,
                    observation=observation,
                    source=fact,
                )
            )
        return tuple(facts)

    def _eps(
        self, report: ParsedIXBRLReport, facts: tuple[StatementFact, ...]
    ) -> tuple[FilingEPS, ...]:
        entries: dict[SummaryPeriodBasis, FilingEPS] = {}
        for index, statement_fact in enumerate(facts):
            fact = statement_fact.source
            if fact.statement is not StatementSection.INCOME_STATEMENT:
                continue
            if not fact.concept_qname.endswith("}" + BASIC_EPS):
                continue
            if fact.value is None:
                continue
            classification = classify_context_role(report, fact.context_ref)
            basis = EPS_BASIS.get(classification.period_role)
            if basis is None:
                continue
            existing = entries.get(basis)
            if existing is not None:
                if existing.value != fact.value:
                    raise SourceDataError(
                        "ambiguous_eps",
                        f"{self.source}: two {basis.value} basic EPS values, "
                        f"{existing.value} and {fact.value}",
                    )
                continue
            entries[basis] = FilingEPS(
                period_basis=basis,
                value=fact.value,
                unit_identity=fact.unit_identity,
                concept_qname=fact.concept_qname,
                context_ref=fact.context_ref,
                fact_index=index,
                classification=classification,
            )
        return tuple(entries[basis] for basis in SummaryPeriodBasis if basis in entries)


def _preview(content: bytes) -> str:
    """The first bytes of the response, in whichever encoding MOPS used.

    Decoded with `errors="replace"`: the 512-byte cut can land inside a
    double-byte character, and a preview that gave up there would miss
    `檔案不存在!` and report a filer who files individually as an unreadable
    document instead of letting the C→A fallback find their report.
    """

    head = content[:512]
    return head.decode("cp950", errors="replace")


def _fingerprint(facts: tuple[StatementFact, ...]) -> str:
    """One source revision's identity, under `mops-filing-revision:v1`.

    Taken from the normalized statement rows alone — statement, 會計科目代碼,
    QName, context, unit and value — so the same document read from the
    official response and from the archive's re-encoded copy fingerprints the
    same, and a corrected document does not.
    """

    digest = sha256()
    for line in sorted(_fact_line(fact) for fact in facts):
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _fact_line(fact: StatementFact) -> str:
    observation = fact.observation
    value: str
    if observation.numeric_value is not None:
        value = _canonical_number(observation.numeric_value)
    elif observation.text_value is not None:
        value = f"text:{observation.text_value}"
    else:
        value = "nil"
    return "|".join(
        (
            fact.statement.value,
            fact.account_code,
            observation.concept_qname,
            _context_key(observation.context),
            observation.unit_identity,
            value,
        )
    )


def _context_key(context: XBRLContext) -> str:
    dimensions = ",".join(
        f"{name}={value}" for name, value in sorted(context.explicit_dimensions.items())
    )
    return "/".join(
        (
            context.entity_identifier,
            context.period_type,
            context.instant_date.isoformat() if context.instant_date else "",
            context.period_start.isoformat() if context.period_start else "",
            context.period_end.isoformat() if context.period_end else "",
            dimensions,
        )
    )


def _canonical_number(value: Decimal) -> str:
    """`89680417000` and `8.9680417E+10` are the same amount, one fingerprint."""

    normalized = value.normalize()
    sign, digits, exponent = normalized.as_tuple()
    if isinstance(exponent, int) and exponent > 0:
        normalized = normalized.quantize(Decimal(1))
    return f"{normalized:f}"
