"""Parsing one MOPS `t164sb01` inline-XBRL financial report.

The endpoint returns a rendered HTML page with the XBRL instance inlined: an
`<ix:header>` holding the filing's own description, an `<ix:resources>` block of
`<xbrli:context>` and `<xbrli:unit>` definitions, and the statements as tables
whose amount cells are `<ix:nonFraction>` elements.

The documents are not well-formed XML — they open `<html>` twice, and one of the
45,324 archived documents was re-serialized with uppercase tags and lowercase
attribute names — so this module reads them as text with case-insensitive
patterns, exactly as the legacy processor had to. What it refuses to do is
guess: an unknown header value, an undeclared namespace prefix, a fact pointing
at a context or unit the document never defined, and a context shape MOPS has
never emitted each raise `IXBRLParseError` instead of being normalized away.

Two filer defects are common enough to be part of the contract rather than
errors, and both are reported rather than absorbed: a cell that prints `-` or
`無` where an amount belongs keeps its fact with no value and
`is_placeholder`, because a dash is neither zero nor `xsi:nil`; and an
`ix:nonFraction` with an empty `unitRef`, which some filing tools use for a
narrative 重大事項 answer, is counted in `malformed_numeric_facts` and is not a
fact.

Scope: this module only reads a document. Fetching, storage, source declarations
and publication evidence belong to Steps 23-b and 23-c.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from stock_data_center.financials.classification import (
    SourceContextClassification,
    SourcePeriodRole,
)
from stock_data_center.financials.models import XBRLContext

MOPS_IXBRL_PARSER_VERSION = "mops-ixbrl-parser:v1"
MOPS_CONTEXT_ROLE_RULE = "mops-xbrl-context-role:v1"

#: MOPS declares `charset=big5`, but it serves the Microsoft mapping: 0xA1E3 is
#: `～` U+FF5E, which Python's `big5` codec decodes as `∼` U+223C. Verified on
#: 2026-09-20 against 1101 2025Q1, where the official response decoded with
#: cp950 equals the archived UTF-8 document character for character.
SOURCE_ENCODING = "cp950"


class IXBRLParseError(ValueError):
    """The document is not a shape this parser has ever seen from MOPS."""


class ReportType(str, Enum):
    GENERAL = "Financial report (general)"
    RETROSPECTIVE = "Financial report (retrospective)"
    RETROSPECTIVE_MATERIAL = "Financial Report (retrospective - material)"
    FIRST_TIME_ADOPTION = "Financial report (first time adoption)"


class ReportCategory(str, Enum):
    CONSOLIDATED = "Consolidated report"
    INDIVIDUAL = "Individual report"


class SourceMarket(str, Enum):
    LISTED = "Listed company"
    OTC = "Over-the-counter"
    EMERGING = "Emerging stock market"
    EMERGING_APPLICANT = "Emerging Stock Company (Applying for listing on TWSE/GTSM)"
    PUBLIC = "Public company"
    NON_PUBLIC = "Non-public company"


class IndustrySector(str, Enum):
    COMMERCIAL_AND_INDUSTRIAL = "Commercial and industrial"
    FINANCIAL_HOLDING = "Financial holding"
    BANKING = "Banking and savings institution"
    INSURANCE = "Insurance"
    BROKER_DEALER = "Broker-dealer"
    MISCELLANEOUS_MERGING = "Miscellaneous industry merging"


#: The industries v1 excludes, matching the legacy scope (ROADMAP Step 23,
#: §26.3). `Miscellaneous industry merging` is not one of them: the legacy
#: `quarterly_reports_xbrl` holds 52 quarters each for 1409, 1718, 2207 and 2905.
FINANCIAL_INDUSTRIES = frozenset(
    {
        IndustrySector.FINANCIAL_HOLDING,
        IndustrySector.BANKING,
        IndustrySector.INSURANCE,
        IndustrySector.BROKER_DEALER,
    }
)

#: The v1 security universe is 上市 and 上櫃 only (CLAUDE.md v1 scope). Emerging,
#: public and non-public filers exist in the archive and have no market code.
MARKET_CODES: Mapping[SourceMarket, str] = MappingProxyType(
    {SourceMarket.LISTED: "sii", SourceMarket.OTC: "otc"}
)

QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
QUARTER_START_MONTH = {1: 1, 2: 4, 3: 7, 4: 10}

_XMLNS = re.compile(r'xmlns:([A-Za-z0-9._-]+)\s*=\s*"([^"]+)"', re.I)
_HEADER_FACT = re.compile(
    r"<ix:nonNumeric\b[^>]*\bname\s*=\s*\"tifrs-notes:(\w+)\"[^>]*>(.*?)</ix:nonNumeric>",
    re.I | re.S,
)
_SCHEMA_REF = re.compile(r"<link:schemaRef\b[^>]*\bxlink:href\s*=\s*\"([^\"]+)\"", re.I)
_CONTEXT = re.compile(r"<xbrli:context\b(.*?)</xbrli:context>", re.I | re.S)
_UNIT = re.compile(r"<xbrli:unit\b(.*?)</xbrli:unit>", re.I | re.S)
_ID = re.compile(r"\bid\s*=\s*\"([^\"]+)\"", re.I)
_IDENTIFIER = re.compile(r"<xbrli:identifier\b[^>]*>(.*?)</xbrli:identifier>", re.I | re.S)
_INSTANT = re.compile(r"<xbrli:instant\b[^>]*>(.*?)</xbrli:instant>", re.I | re.S)
_START = re.compile(r"<xbrli:startDate\b[^>]*>(.*?)</xbrli:startDate>", re.I | re.S)
_END = re.compile(r"<xbrli:endDate\b[^>]*>(.*?)</xbrli:endDate>", re.I | re.S)
_EXPLICIT = re.compile(
    r"<xbrldi:explicitMember\b[^>]*\bdimension\s*=\s*\"([^\"]+)\"[^>]*>(.*?)"
    r"</xbrldi:explicitMember>",
    re.I | re.S,
)
_UNSUPPORTED_CONTEXT = re.compile(r"<xbrldi:typedMember|<xbrli:segment|<xbrli:forever", re.I)
_MEASURE = re.compile(r"<xbrli:measure\b[^>]*>(.*?)</xbrli:measure>", re.I | re.S)
_HAS_DIVIDE = re.compile(r"<xbrli:divide|<xbrli:unitNumerator|<xbrli:unitDenominator", re.I)
_DIVIDE = re.compile(
    r"<xbrli:unitNumerator\b(.*?)</xbrli:unitNumerator>\s*"
    r"<xbrli:unitDenominator\b(.*?)</xbrli:unitDenominator>",
    re.I | re.S,
)
_ROW = re.compile(r"<tr\b.*?</tr>", re.I | re.S)
_NONFRACTION = re.compile(r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", re.I | re.S)
_NOTE_BLOCK = re.compile(r"<ix:nonNumeric\b[^>]*\bescape\s*=\s*\"true\"", re.I)
_ATTR = re.compile(r"([:\w-]+)\s*=\s*\"([^\"]*)\"")
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.I | re.S)
_SPAN = re.compile(r"<span\b[^>]*\bclass\s*=\s*\"(zh|en)\"[^>]*>(.*?)</span>", re.I | re.S)
_TAG = re.compile(r"<[^>]*>", re.S)

#: A cell that holds no number holds one of two things, and the whole archive
#: shows both: a short placeholder such as `-`, `無` or a footnote marker `註二`,
#: which is neither zero nor `xsi:nil` and so keeps its fact without a value;
#: or, when a filing tool misuses the numeric element, a whole paragraph of
#: narrative, which is not a fact at all. The boundary is length, because that
#: is the only thing that separates them in the source.
PLACEHOLDER_MAX_LENGTH = 8

#: The only `format` in the archive. It reads `1,234.56` the way this parser
#: does. `ixt:numcommadecimal` reads the same text the other way round and
#: `ixt:zerodash` turns a dash into a real zero, so an unimplemented
#: transformation is refused rather than read with the wrong one.
IMPLEMENTED_FORMATS = frozenset({"ixt:numdotdecimal"})


def _text(value: str) -> str:
    """Strip markup and collapse the whitespace a re-serialized page inserts."""

    return " ".join(_TAG.sub("", value).split())


@dataclass(frozen=True, slots=True)
class IXBRLHeader:
    """What the filing says about itself, from `<ix:header>`."""

    company_id: str
    company_chinese_name: str
    company_english_name: str
    report_year: int
    report_quarter: int
    report_type: ReportType
    report_category: ReportCategory
    market: SourceMarket
    industry_sector: IndustrySector
    taxonomy_schema_ref: str | None

    @property
    def is_financial_industry(self) -> bool:
        return self.industry_sector in FINANCIAL_INDUSTRIES

    @property
    def market_code(self) -> str | None:
        return MARKET_CODES.get(self.market)

    @property
    def in_v1_universe(self) -> bool:
        return self.market_code is not None


@dataclass(frozen=True, slots=True)
class ParsedFact:
    """One `<ix:nonFraction>`, with the statement row it was printed in."""

    concept_qname: str
    context_ref: str
    unit_ref: str
    unit_identity: str
    value: Decimal | None
    raw_text: str
    scale: int
    sign: str | None
    decimals: str | None
    format: str
    account_code: str | None
    label_zh: str | None
    label_en: str | None
    #: The source printed a placeholder such as `-` instead of a number.
    is_placeholder: bool = False


@dataclass(frozen=True, slots=True)
class ParsedIXBRLReport:
    header: IXBRLHeader
    period_start: date
    period_end: date
    contexts: Mapping[str, XBRLContext]
    units: Mapping[str, str]
    facts: tuple[ParsedFact, ...]
    note_block_count: int
    #: `ix:nonFraction` elements with no unit: prose the filer's tool wrote into
    #: the numeric element. They are not facts, and they are not silently lost.
    malformed_numeric_facts: int = 0
    #: Prefix spellings resolved case-insensitively, as `used->declared`.
    prefix_case_repairs: tuple[str, ...] = ()
    parser_version: str = MOPS_IXBRL_PARSER_VERSION


def decode_ixbrl_document(raw: bytes, *, encoding: str | None = None) -> str:
    """Decode a response, or an archived copy of one.

    The official response is cp950; the legacy archive holds the same document
    re-encoded as UTF-8. A caller that knows which it is holding should say so:
    `encoding` is then the only codec tried, and a document that does not decode
    is an error instead of a silent fallback. Without it, UTF-8 is tried first
    and cp950 second, which separates the two in practice because Chinese cp950
    text is not valid UTF-8.
    """

    if encoding is not None:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as error:
            raise IXBRLParseError(f"document is not {encoding}") from error
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode(SOURCE_ENCODING)
    except UnicodeDecodeError as error:  # pragma: no cover - defensive
        raise IXBRLParseError(f"document is neither UTF-8 nor {SOURCE_ENCODING}") from error


def _parse_namespaces(text: str) -> dict[str, str]:
    namespaces: dict[str, str] = {}
    for prefix, uri in _XMLNS.findall(text):
        declared = namespaces.setdefault(prefix, uri)
        if declared != uri:
            raise IXBRLParseError(
                f"prefix {prefix!r} is declared as both {declared!r} and {uri!r}"
            )
    return namespaces


def _enum_value(enum: type[Enum], field: str, raw: str) -> Enum:
    try:
        return enum(raw)
    except ValueError as error:
        raise IXBRLParseError(f"unknown tifrs-notes:{field} value: {raw!r}") from error


def _parse_header(text: str) -> IXBRLHeader:
    found: dict[str, str] = {}
    for name, value in _HEADER_FACT.findall(text):
        found.setdefault(name, _text(value))

    def require(field: str) -> str:
        value = found.get(field)
        if not value:
            raise IXBRLParseError(f"document has no tifrs-notes:{field}")
        return value

    year_text, quarter_text = require("Year"), require("Quarter")
    if not year_text.isdigit() or quarter_text not in {"1", "2", "3", "4"}:
        raise IXBRLParseError(f"unusable period in header: {year_text}Q{quarter_text}")

    schema_ref = _SCHEMA_REF.search(text)
    return IXBRLHeader(
        company_id=require("CompanyID"),
        company_chinese_name=require("CompanyChineseName"),
        company_english_name=found.get("CompanyEnglishName", ""),
        report_year=int(year_text),
        report_quarter=int(quarter_text),
        report_type=_enum_value(ReportType, "ReportType", require("ReportType")),
        report_category=_enum_value(
            ReportCategory, "ReportCategory", require("ReportCategory")
        ),
        market=_enum_value(SourceMarket, "Market", require("Market")),
        industry_sector=_enum_value(
            IndustrySector, "IndustrySector", require("IndustrySector")
        ),
        taxonomy_schema_ref=schema_ref.group(1) if schema_ref else None,
    )


def _clark(
    qname: str, namespaces: Mapping[str, str], repairs: set[str] | None = None
) -> str:
    """Resolve `prefix:local` against the document's own xmlns declarations.

    XML prefixes are case-sensitive and MOPS normally keeps them so. The one
    re-serialized document in the archive (1519 2021Q2) lowercased every
    attribute name, which turned `xmlns:tifrs-SCF` into `xmlns:tifrs-scf` while
    two fact names kept the original spelling. A case-insensitive fallback is
    used only when it resolves to exactly one declared prefix, and every use is
    recorded on the report so the importer can see it rather than infer it.
    """

    prefix, _, local = qname.partition(":")
    if not local:
        raise IXBRLParseError(f"concept name has no prefix: {qname!r}")
    namespace = namespaces.get(prefix)
    if namespace is None:
        folded = {
            declared: uri
            for declared, uri in namespaces.items()
            if declared.lower() == prefix.lower()
        }
        if len(folded) != 1:
            raise IXBRLParseError(f"document never declared the prefix {prefix!r}")
        (declared, namespace), = folded.items()
        if repairs is not None:
            repairs.add(f"{prefix}->{declared}")
    return f"{{{namespace}}}{local}"


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(_text(value))
    except ValueError as error:
        raise IXBRLParseError(f"unusable context date: {value!r}") from error


def _parse_contexts(
    text: str, namespaces: Mapping[str, str], repairs: set[str]
) -> dict[str, XBRLContext]:
    contexts: dict[str, XBRLContext] = {}
    for body in _CONTEXT.findall(text):
        identity = _ID.search(body)
        if identity is None:
            raise IXBRLParseError("xbrli:context without an id")
        context_id = identity.group(1)
        if _UNSUPPORTED_CONTEXT.search(body):
            raise IXBRLParseError(
                f"context {context_id} uses a shape this parser has not seen: "
                "typed dimensions, segment, or a forever period"
            )
        entity = _IDENTIFIER.search(body)
        if entity is None:
            raise IXBRLParseError(f"context {context_id} has no entity identifier")

        dimensions = {
            _clark(dimension, namespaces, repairs): _clark(
                _text(member), namespaces, repairs
            )
            for dimension, member in _EXPLICIT.findall(body)
        }
        instant, start, end = (
            _INSTANT.search(body),
            _START.search(body),
            _END.search(body),
        )
        if instant is not None:
            context = XBRLContext(
                entity_identifier=_text(entity.group(1)),
                period_type="instant",
                instant_date=_parse_date(instant.group(1)),
                explicit_dimensions=dimensions,
            )
        elif start is not None and end is not None:
            context = XBRLContext(
                entity_identifier=_text(entity.group(1)),
                period_type="duration",
                period_start=_parse_date(start.group(1)),
                period_end=_parse_date(end.group(1)),
                explicit_dimensions=dimensions,
            )
        else:
            raise IXBRLParseError(f"context {context_id} has no usable period")
        declared = contexts.setdefault(context_id, context)
        if declared != context:
            raise IXBRLParseError(
                f"context {context_id} is declared twice with different content"
            )
    if not contexts:
        raise IXBRLParseError("document defines no xbrli:context")
    return contexts


def _parse_units(text: str) -> dict[str, str]:
    units: dict[str, str] = {}
    for body in _UNIT.findall(text):
        identity = _ID.search(body)
        if identity is None:
            raise IXBRLParseError("xbrli:unit without an id")
        unit_id = identity.group(1)
        if _HAS_DIVIDE.search(body):
            # A ratio unit must be read as a ratio. Falling back to the first
            # measure would quietly label earnings per share as a currency.
            divide = _DIVIDE.search(body)
            if divide is None:
                raise IXBRLParseError(f"unit {unit_id} divides in a shape this parser cannot read")
            numerator = _MEASURE.search(divide.group(1))
            denominator = _MEASURE.search(divide.group(2))
            if numerator is None or denominator is None:
                raise IXBRLParseError(f"unit {unit_id} divides without measures")
            identity_text = f"{_text(numerator.group(1))}/{_text(denominator.group(1))}"
        else:
            measure = _MEASURE.search(body)
            if measure is None:
                raise IXBRLParseError(f"unit {unit_id} has no measure")
            identity_text = _text(measure.group(1))
        declared = units.setdefault(unit_id, identity_text)
        if declared != identity_text:
            raise IXBRLParseError(
                f"unit {unit_id} is declared twice, as {declared!r} and {identity_text!r}"
            )
    if not units:
        raise IXBRLParseError("document defines no xbrli:unit")
    return units


def _row_labels(row: str) -> tuple[str | None, str | None, str | None]:
    cells = _CELL.findall(row)
    if not cells:
        return None, None, None
    code = _text(cells[0])
    account_code = code if re.fullmatch(r"[0-9A-Z]{4,6}", code) else None
    # The first pair is the account's own label. A header row can carry
    # several, and taking the last one would label the fact with a column head.
    labels: dict[str, str] = {}
    for kind, value in _SPAN.findall(row):
        labels.setdefault(kind, value)
    return (
        account_code,
        _text(labels["zh"]) if "zh" in labels else None,
        _text(labels["en"]) if "en" in labels else None,
    )


class _NotANumber(Exception):
    """The cell holds narrative, not an amount."""


def _parse_value(raw_text: str, scale: int, sign: str | None) -> Decimal | None:
    cleaned = _text(raw_text).replace(",", "").replace(" ", "")
    parenthesised = cleaned.startswith("(") and cleaned.endswith(")")
    if parenthesised:
        if sign:
            # Parentheses and sign="-" are two conventions for one minus. No
            # archive document prints a parenthesised amount at all, so rather
            # than guess which one the filer meant, refuse the combination.
            raise IXBRLParseError(
                f"amount uses both parentheses and sign={sign!r}: {raw_text!r}"
            )
        cleaned = "-" + cleaned[1:-1]
    try:
        value = Decimal(cleaned)
    except Exception:  # Decimal raises InvalidOperation
        if len(cleaned) <= PLACEHOLDER_MAX_LENGTH:
            return None
        raise _NotANumber from None
    if not value.is_finite():
        # Decimal accepts NaN and Infinity. Neither is an amount, and NaN would
        # break business-content identity and deduplication downstream.
        raise IXBRLParseError(f"amount is not finite: {raw_text!r}")
    if sign == "-":
        value = -value
    elif sign:
        raise IXBRLParseError(f"unknown sign attribute: {sign!r}")
    return value.scaleb(scale)


def _parse_facts(
    text: str,
    *,
    namespaces: Mapping[str, str],
    contexts: Mapping[str, XBRLContext],
    units: Mapping[str, str],
    repairs: set[str],
) -> tuple[tuple[ParsedFact, ...], int]:
    rows = [(match.start(), match.end(), match.group(0)) for match in _ROW.finditer(text)]
    facts: list[ParsedFact] = []
    malformed = 0
    row_index = 0
    # A statement row holds about ten amount cells, so its labels are read once
    # per row rather than once per fact.
    cached_row = -1
    cached_labels: tuple[str | None, str | None, str | None] = (None, None, None)
    for match in _NONFRACTION.finditer(text):
        while row_index < len(rows) and rows[row_index][1] <= match.start():
            row_index += 1
        in_row = row_index < len(rows) and rows[row_index][0] <= match.start()
        if not in_row:
            account_code, label_zh, label_en = None, None, None
        else:
            if row_index != cached_row:
                cached_row, cached_labels = row_index, _row_labels(rows[row_index][2])
            account_code, label_zh, label_en = cached_labels

        attributes = {
            name.lower(): value for name, value in _ATTR.findall(match.group(1))
        }
        concept = attributes.get("name")
        context_ref = attributes.get("contextref")
        unit_ref = attributes.get("unitref")
        if not concept or not context_ref:
            raise IXBRLParseError(
                f"ix:nonFraction without name or context: {attributes}"
            )
        if not unit_ref:
            # Some filing tools answer a narrative 重大事項 question inside an
            # ix:nonFraction with an empty unitRef. It is prose, not an amount.
            malformed += 1
            continue
        if context_ref not in contexts:
            raise IXBRLParseError(f"fact references undefined context {context_ref}")
        if unit_ref not in units:
            raise IXBRLParseError(f"fact references undefined unit {unit_ref}")
        transformation = attributes.get("format")
        if not transformation:
            raise IXBRLParseError(
                f"numeric fact {concept} has no format attribute"
            )
        if transformation not in IMPLEMENTED_FORMATS:
            raise IXBRLParseError(
                f"fact {concept} uses transformation {transformation}, "
                "which this parser does not implement"
            )
        scale_text = attributes.get("scale", "0")
        try:
            scale = int(scale_text)
        except ValueError as error:
            raise IXBRLParseError(f"unusable scale: {scale_text!r}") from error

        raw_text = _text(match.group(2))
        try:
            value = _parse_value(raw_text, scale, attributes.get("sign"))
        except _NotANumber:
            # A filing tool wrote a whole note into the numeric element.
            malformed += 1
            continue
        facts.append(
            ParsedFact(
                concept_qname=_clark(concept, namespaces, repairs),
                context_ref=context_ref,
                unit_ref=unit_ref,
                unit_identity=units[unit_ref],
                value=value,
                raw_text=raw_text,
                scale=scale,
                sign=attributes.get("sign"),
                decimals=attributes.get("decimals"),
                format=transformation,
                account_code=account_code,
                label_zh=label_zh,
                label_en=label_en,
                is_placeholder=value is None,
            )
        )
    return tuple(facts), malformed


def parse_ixbrl_report(
    raw: bytes | str, *, encoding: str | None = None
) -> ParsedIXBRLReport:
    """Read one `t164sb01` document into its header, contexts, units and facts.

    A caller that knows whether these bytes are an official cp950 response or a
    UTF-8 archive copy passes `encoding`; see `decode_ixbrl_document`.
    """

    text = (
        decode_ixbrl_document(raw, encoding=encoding) if isinstance(raw, bytes) else raw
    )
    namespaces = _parse_namespaces(text)
    repairs: set[str] = set()
    header = _parse_header(text)
    contexts = _parse_contexts(text, namespaces, repairs)
    units = _parse_units(text)

    facts, malformed = _parse_facts(
        text, namespaces=namespaces, contexts=contexts, units=units, repairs=repairs
    )

    month, day = QUARTER_END[header.report_quarter]
    period_start = date(header.report_year, 1, 1)
    period_end = date(header.report_year, month, day)

    return ParsedIXBRLReport(
        header=header,
        period_start=period_start,
        period_end=period_end,
        contexts=MappingProxyType(contexts),
        units=MappingProxyType(units),
        facts=facts,
        note_block_count=len(_NOTE_BLOCK.findall(text)),
        malformed_numeric_facts=malformed,
        prefix_case_repairs=tuple(sorted(repairs)),
    )


def classify_context_role(
    report: ParsedIXBRLReport, context_ref: str
) -> SourceContextClassification:
    """Decide what a context means to this source, under `mops-xbrl-context-role:v1`.

    A current period is a dimensionless duration of this entity ending on the
    filing's period end. Within that, the fiscal-year start means year-to-date —
    annual in Q4 — and the quarter start means the single quarter. Everything
    else, including every prior-year comparative, is `other`, which Step 5's
    `classify_eps_period_basis` refuses. The rule never reads a role out of a
    duration's length alone.
    """

    context = report.contexts.get(context_ref)
    if context is None:
        raise IXBRLParseError(f"document has no context {context_ref}")

    role = SourcePeriodRole.OTHER
    if (
        context.period_type == "duration"
        and not context.explicit_dimensions
        and not context.typed_dimensions
        and context.entity_identifier == report.header.company_id
        and context.period_end == report.period_end
    ):
        quarter = report.header.report_quarter
        quarter_start = date(
            report.header.report_year, QUARTER_START_MONTH[quarter], 1
        )
        if context.period_start == report.period_start:
            role = (
                SourcePeriodRole.CURRENT_FULL_YEAR
                if quarter == 4
                else SourcePeriodRole.CURRENT_YEAR_TO_DATE
                if quarter > 1
                else SourcePeriodRole.CURRENT_SINGLE_QUARTER
            )
        elif context.period_start == quarter_start:
            role = SourcePeriodRole.CURRENT_SINGLE_QUARTER

    start = context.period_start if context.period_type == "duration" else context.instant_date
    end = context.period_end if context.period_type == "duration" else context.instant_date
    if start is None or end is None:
        raise IXBRLParseError(f"context {context_ref} has no dates to classify")
    return SourceContextClassification(
        source_context_ref=context_ref,
        classifier_rule=MOPS_CONTEXT_ROLE_RULE,
        period_role=role,
        expected_start=start,
        expected_end=end,
    )
