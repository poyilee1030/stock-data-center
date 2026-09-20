"""Step 23-a — parsing one MOPS `t164sb01` iXBRL document.

The fixtures are excerpts of real documents: the head through `<ix:header>`, the
`<xbrli:unit>` definitions, only the `<xbrli:context>` blocks the kept facts
reference, and a handful of `<tr>` rows from each statement section plus every
row holding an EPS fact and one row whose context carries explicit dimensions.
Nothing inside what is kept was edited, so every byte a test asserts on is a
source byte. `mops_t164sb01_1101_2025Q1_official_cp950.html` is the same excerpt
cut from the official response of 2026-09-20, still in the encoding MOPS served.

The variants are not hypothetical. They come from a scan of all 45,324 archive
documents on 2026-09-20 (audit §4.8): one document serialized with uppercase
tags, lowercase attribute names and a line break inside two header values; 907
financial-industry documents to exclude; 1,667 documents outside the v1 sii/otc
universe; and the cp950-vs-big5 tilde that decides whether the text we store is
the text the filer wrote.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.financials import (
    SourcePeriodRole,
    classify_eps_period_basis,
)
from stock_data_center.financials.ixbrl import (
    MOPS_CONTEXT_ROLE_RULE,
    IndustrySector,
    IXBRLParseError,
    ReportCategory,
    ReportType,
    SourceMarket,
    classify_context_role,
    decode_ixbrl_document,
    parse_ixbrl_report,
)
from stock_data_center.financials.models import EPSPeriodBasis

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
IFRS = "http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full"


def fixture(name: str) -> bytes:
    return (FIXTURES / f"mops_t164sb01_{name}.html").read_bytes()


CEMENT_Q1 = fixture("1101_2025Q1_excerpt")
CEMENT_Q1_OFFICIAL = fixture("1101_2025Q1_official_cp950")
CEMENT_Q3 = fixture("1101_2025Q3_excerpt")
CEMENT_Q4 = fixture("1101_2025Q4_excerpt")
UPPERCASE = fixture("1519_2021Q2_excerpt")
BROKER = fixture("5864_2020Q1_excerpt")
EMERGING = fixture("6785_2020Q2_excerpt")
MERGING = fixture("1718_2025Q1_excerpt")


def test_the_header_is_read_from_the_document_not_from_the_file_name() -> None:
    report = parse_ixbrl_report(CEMENT_Q1)

    assert report.header.company_id == "1101"
    assert report.header.company_chinese_name == "臺灣水泥股份有限公司"
    assert report.header.report_year == 2025
    assert report.header.report_quarter == 1
    assert report.header.report_type is ReportType.GENERAL
    assert report.header.report_category is ReportCategory.CONSOLIDATED
    assert report.header.market is SourceMarket.LISTED
    assert report.header.industry_sector is IndustrySector.COMMERCIAL_AND_INDUSTRIAL
    assert report.header.taxonomy_schema_ref == "tifrs-ci-cr-2020-06-30.xsd"
    assert report.period_start == date(2025, 1, 1)
    assert report.period_end == date(2025, 3, 31)


def test_the_filing_period_comes_from_the_declared_year_and_quarter() -> None:
    assert parse_ixbrl_report(CEMENT_Q3).period_end == date(2025, 9, 30)
    assert parse_ixbrl_report(CEMENT_Q4).period_end == date(2025, 12, 31)
    assert parse_ixbrl_report(CEMENT_Q4).period_start == date(2025, 1, 1)


# --- encoding -------------------------------------------------------------


def test_the_official_response_is_cp950_even_though_it_declares_big5() -> None:
    text = decode_ixbrl_document(CEMENT_Q1_OFFICIAL)

    assert "0～90天" in text
    assert "0∼90天" not in text
    assert text == CEMENT_Q1.decode("utf-8")


def test_decoding_the_official_bytes_as_big5_would_change_the_stored_text() -> None:
    # The guard for the line above: this fixture really does contain the byte
    # (0xA1E3) whose Big5 and cp950 mappings differ, so a codec change is caught.
    assert CEMENT_Q1_OFFICIAL.decode("big5") != CEMENT_Q1.decode("utf-8")


def test_the_same_document_parses_the_same_from_either_encoding() -> None:
    official = parse_ixbrl_report(CEMENT_Q1_OFFICIAL)
    archived = parse_ixbrl_report(CEMENT_Q1)

    assert official.facts == archived.facts
    assert official.header == archived.header


# --- serialization variants ----------------------------------------------


def test_uppercase_tags_and_lowercase_attributes_parse() -> None:
    report = parse_ixbrl_report(UPPERCASE)

    assert report.header.company_id == "1519"
    assert report.header.report_year == 2021
    assert report.header.report_quarter == 2
    assert report.facts, "the only uppercase document in the archive has facts"


def test_a_line_break_inside_a_header_value_is_normalized_not_rejected() -> None:
    assert b"Consolidated \r\nreport" in UPPERCASE

    report = parse_ixbrl_report(UPPERCASE)

    assert report.header.report_category is ReportCategory.CONSOLIDATED
    assert report.header.industry_sector is IndustrySector.COMMERCIAL_AND_INDUSTRIAL


# --- universe and industry ------------------------------------------------


def test_the_four_financial_taxonomies_are_recognized_as_financial() -> None:
    report = parse_ixbrl_report(BROKER)

    assert report.header.industry_sector is IndustrySector.BROKER_DEALER
    assert report.header.is_financial_industry is True
    assert report.header.report_category is ReportCategory.INDIVIDUAL
    assert report.header.market_code == "otc"


def test_miscellaneous_industry_merging_is_not_a_financial_industry() -> None:
    # 1409, 1718, 2207 and 2905 each have 52 quarters in the legacy
    # `quarterly_reports_xbrl`, so this taxonomy stays in scope.
    report = parse_ixbrl_report(MERGING)

    assert report.header.industry_sector is IndustrySector.MISCELLANEOUS_MERGING
    assert report.header.is_financial_industry is False


def test_a_market_outside_sii_and_otc_has_no_market_code() -> None:
    report = parse_ixbrl_report(EMERGING)

    assert report.header.market is SourceMarket.EMERGING
    assert report.header.market_code is None
    assert report.header.in_v1_universe is False


def test_an_unknown_header_value_fails_closed() -> None:
    mutated = CEMENT_Q1.replace(
        b">Commercial and industrial<", b">Agricultural cooperative<"
    )

    with pytest.raises(IXBRLParseError, match="IndustrySector"):
        parse_ixbrl_report(mutated)


def test_a_missing_header_fact_fails_closed() -> None:
    mutated = CEMENT_Q1.replace(b'name="tifrs-notes:ReportCategory"', b'name="x:y"')

    with pytest.raises(IXBRLParseError, match="ReportCategory"):
        parse_ixbrl_report(mutated)


# --- concepts, contexts, units --------------------------------------------


def test_concept_names_resolve_to_clark_notation_through_the_documents_xmlns() -> None:
    report = parse_ixbrl_report(CEMENT_Q1)

    assert f"{{{IFRS}}}CashAndCashEquivalents" in {
        fact.concept_qname for fact in report.facts
    }


def test_a_prefix_the_document_never_declared_fails_closed() -> None:
    mutated = CEMENT_Q1.replace(
        b'name="ifrs-full:CashAndCashEquivalents"', b'name="nope:CashAndCashEquivalents"'
    )

    with pytest.raises(IXBRLParseError, match="nope"):
        parse_ixbrl_report(mutated)


def test_an_instant_context_and_a_duration_context_keep_their_shapes() -> None:
    report = parse_ixbrl_report(CEMENT_Q1)

    instant = report.contexts["AsOf20250331"]
    duration = report.contexts["From20250101To20250331"]

    assert instant.period_type == "instant"
    assert instant.instant_date == date(2025, 3, 31)
    assert instant.entity_identifier == "1101"
    assert duration.period_type == "duration"
    assert duration.period_start == date(2025, 1, 1)
    assert duration.period_end == date(2025, 3, 31)


def test_explicit_dimensions_are_kept_in_clark_notation() -> None:
    report = parse_ixbrl_report(CEMENT_Q1)
    dimensional = [
        context
        for context in report.contexts.values()
        if context.explicit_dimensions
    ]

    assert dimensional, "the fixture keeps one dimensional context"
    for context in dimensional:
        for dimension, member in context.explicit_dimensions.items():
            assert dimension.startswith("{") and "}" in dimension
            assert member.startswith("{") and "}" in member


def test_the_four_units_map_to_their_measures() -> None:
    report = parse_ixbrl_report(CEMENT_Q1)

    assert report.units["TWD"] == "iso4217:TWD"
    assert report.units["Shares"] == "xbrli:shares"
    assert report.units["Pure"] == "xbrli:pure"
    assert report.units["EarningsPerShare"] == "iso4217:TWD/xbrli:shares"


# --- fact values ----------------------------------------------------------


def find(report, qname_local: str, context_ref: str):
    for fact in report.facts:
        if fact.concept_qname.endswith("}" + qname_local) and (
            fact.context_ref == context_ref
        ):
            return fact
    raise AssertionError(f"{qname_local} @ {context_ref} not in the fixture")


def test_a_thousands_scaled_amount_becomes_its_full_value() -> None:
    fact = find(parse_ixbrl_report(CEMENT_Q1), "CashAndCashEquivalents", "AsOf20250331")

    assert fact.raw_text == "89,680,417"
    assert fact.scale == 3
    assert fact.value == Decimal("89680417000")
    assert fact.unit_identity == "iso4217:TWD"
    assert fact.decimals == "-3"


def test_a_negative_sign_attribute_negates_the_printed_value() -> None:
    fact = find(
        parse_ixbrl_report(CEMENT_Q3),
        "BasicEarningsLossPerShare",
        "From20250701To20250930",
    )

    assert fact.raw_text == "1.36"
    assert fact.value == Decimal("-1.36")
    assert fact.unit_identity == "iso4217:TWD/xbrli:shares"


def test_the_account_code_and_both_labels_are_kept_with_the_fact() -> None:
    fact = find(parse_ixbrl_report(CEMENT_Q1), "CashAndCashEquivalents", "AsOf20250331")

    assert fact.account_code == "1100"
    assert fact.label_zh == "現金及約當現金"
    assert fact.label_en == "Cash and cash equivalents"


def test_a_fact_pointing_at_a_context_the_document_never_defined_fails_closed() -> None:
    mutated = CEMENT_Q1.replace(b'contextRef="AsOf20250331"', b'contextRef="AsOf19000101"')

    with pytest.raises(IXBRLParseError, match="AsOf19000101"):
        parse_ixbrl_report(mutated)


def test_a_fact_pointing_at_a_unit_the_document_never_defined_fails_closed() -> None:
    mutated = CEMENT_Q1.replace(b'unitRef="TWD"', b'unitRef="USD"')

    with pytest.raises(IXBRLParseError, match="USD"):
        parse_ixbrl_report(mutated)


def test_narrative_note_blocks_are_counted_but_are_not_facts() -> None:
    report = parse_ixbrl_report(CEMENT_Q1)

    assert all(fact.value is not None for fact in report.facts)
    assert report.note_block_count > 0


# --- EPS period roles: mops-xbrl-context-role:v1 --------------------------


def role(report, context_ref: str):
    return classify_context_role(report, context_ref)


def test_the_q1_current_context_is_the_single_quarter() -> None:
    report = parse_ixbrl_report(CEMENT_Q1)
    classification = role(report, "From20250101To20250331")

    assert classification.classifier_rule == MOPS_CONTEXT_ROLE_RULE
    assert classification.period_role is SourcePeriodRole.CURRENT_SINGLE_QUARTER
    assert classification.expected_start == date(2025, 1, 1)
    assert classification.expected_end == date(2025, 3, 31)


def test_q3_separates_the_single_quarter_from_the_year_to_date() -> None:
    report = parse_ixbrl_report(CEMENT_Q3)

    assert (
        role(report, "From20250701To20250930").period_role
        is SourcePeriodRole.CURRENT_SINGLE_QUARTER
    )
    assert (
        role(report, "From20250101To20250930").period_role
        is SourcePeriodRole.CURRENT_YEAR_TO_DATE
    )


def test_the_q4_full_year_context_is_annual() -> None:
    report = parse_ixbrl_report(CEMENT_Q4)

    assert (
        role(report, "From20250101To20251231").period_role
        is SourcePeriodRole.CURRENT_FULL_YEAR
    )


def test_a_prior_year_comparative_is_not_a_current_period() -> None:
    report = parse_ixbrl_report(CEMENT_Q3)

    assert role(report, "From20240101To20240930").period_role is SourcePeriodRole.OTHER
    assert role(report, "From20240701To20240930").period_role is SourcePeriodRole.OTHER


def test_an_instant_context_is_never_a_current_eps_period() -> None:
    report = parse_ixbrl_report(CEMENT_Q1)

    assert role(report, "AsOf20250331").period_role is SourcePeriodRole.OTHER


def test_a_dimensional_context_is_not_the_headline_period() -> None:
    report = parse_ixbrl_report(CEMENT_Q1)
    dimensional = [
        ref
        for ref, context in report.contexts.items()
        if context.explicit_dimensions
        and context.period_type == "duration"
        and context.period_end == report.period_end
    ]

    for ref in dimensional:
        assert role(report, ref).period_role is SourcePeriodRole.OTHER


@pytest.mark.parametrize(
    "document, context_ref, expected",
    [
        (CEMENT_Q1, "From20250101To20250331", EPSPeriodBasis.QUARTER),
        (CEMENT_Q3, "From20250701To20250930", EPSPeriodBasis.QUARTER),
        (CEMENT_Q3, "From20250101To20250930", EPSPeriodBasis.YTD),
        (CEMENT_Q4, "From20250101To20251231", EPSPeriodBasis.ANNUAL),
    ],
)
def test_every_current_role_survives_the_step_5_classifier(
    document: bytes, context_ref: str, expected: EPSPeriodBasis
) -> None:
    report = parse_ixbrl_report(document)

    basis = classify_eps_period_basis(
        context=report.contexts[context_ref],
        filing_period_start=report.period_start,
        filing_period_end=report.period_end,
        report_quarter=report.header.report_quarter,
        classification=classify_context_role(report, context_ref),
    )

    assert basis is expected


def test_the_classifier_rejects_a_role_the_dates_do_not_support() -> None:
    report = parse_ixbrl_report(CEMENT_Q1)

    with pytest.raises(ValueError):
        classify_eps_period_basis(
            context=report.contexts["From20240101To20240331"],
            filing_period_start=report.period_start,
            filing_period_end=report.period_end,
            report_quarter=1,
            classification=classify_context_role(report, "From20250101To20250331"),
        )


# --- the one re-serialized document's lowercased prefix -------------------


def test_the_lowercased_prefix_is_resolved_and_recorded_not_silently_accepted() -> None:
    # 1519 2021Q2 declares `xmlns:tifrs-scf` because its serializer lowercased
    # every attribute name, while two cash-flow facts kept `tifrs-SCF:`.
    assert b'xmlns:tifrs-scf' in UPPERCASE
    assert b'name="tifrs-SCF:' in UPPERCASE

    report = parse_ixbrl_report(UPPERCASE)

    assert report.prefix_case_repairs == ("tifrs-SCF->tifrs-scf",)
    assert any(
        fact.concept_qname.startswith("{http://www.xbrl.org/tifrs/scf/")
        for fact in report.facts
    )


def test_a_document_with_no_prefix_case_defect_records_no_repair() -> None:
    assert parse_ixbrl_report(CEMENT_Q1).prefix_case_repairs == ()


# --- what filers print where a number belongs -----------------------------

DASH = fixture("3543_2020Q2_excerpt")
NARRATIVE_NONFRACTION = fixture("1570_2026Q1_excerpt")


def test_a_dash_printed_where_an_amount_belongs_is_kept_as_a_placeholder() -> None:
    # 3543 2020Q2 prints `-` in an endorsement amount cell that still declares
    # unitRef="TWD". A dash is not zero and not `xsi:nil`, so the fact is kept
    # with no value and the printed text preserved.
    report = parse_ixbrl_report(DASH)
    placeholders = [fact for fact in report.facts if fact.value is None]

    assert placeholders, "the fixture keeps the dash row"
    for fact in placeholders:
        assert fact.raw_text in {"-", "－", "null", "無", "註"}
        assert fact.is_placeholder is True
        assert fact.unit_identity == "iso4217:TWD"


def test_a_non_fraction_the_filer_used_for_prose_is_not_a_fact() -> None:
    # 1570 2026Q1 writes its 重大事項 answers as <ix:nonFraction unitRef="">無此情形</…>.
    assert b'unitRef=""' in NARRATIVE_NONFRACTION

    report = parse_ixbrl_report(NARRATIVE_NONFRACTION)

    assert report.malformed_numeric_facts == 1
    assert all(fact.unit_ref for fact in report.facts)
    assert not any("無此情形" in fact.raw_text for fact in report.facts)


def test_a_document_with_neither_defect_reports_neither() -> None:
    report = parse_ixbrl_report(CEMENT_Q1)

    assert report.malformed_numeric_facts == 0
    assert all(fact.value is not None for fact in report.facts)


PROSE_IN_AMOUNT = fixture("1512_2020Q3_excerpt")
FOOTNOTE_MARK = fixture("2492_2026Q2_excerpt")


def test_a_whole_note_pasted_into_an_amount_cell_is_prose_not_a_fact() -> None:
    # 1512 2020Q3 puts 2,475 characters of receivables narrative into a
    # `tifrs-notes:Amount2` element that still declares unitRef="TWD".
    report = parse_ixbrl_report(PROSE_IN_AMOUNT)

    assert report.malformed_numeric_facts >= 1
    assert not any(len(fact.raw_text) > 40 for fact in report.facts)


def test_a_footnote_marker_where_an_amount_belongs_is_a_placeholder() -> None:
    # 2492 2026Q2 answers a mainland-investment ceiling with 註二.
    report = parse_ixbrl_report(FOOTNOTE_MARK)
    marks = [fact for fact in report.facts if fact.raw_text == "註二"]

    assert marks, "the fixture keeps the 註二 row"
    assert all(fact.value is None and fact.is_placeholder for fact in marks)


# --- review of #39: what the parser must refuse to accept -----------------
#
# Each case below was measured over all 45,324 archive documents before it was
# made an error, so none of them rejects a real filing: no NaN or infinity, no
# format other than `ixt:numdotdecimal`, no conflicting xmlns declaration, no
# parenthesised amount, no duplicate unit id, and exactly one duplicate context
# id — whose two definitions are identical.


def test_a_not_a_number_amount_is_refused_rather_than_stored() -> None:
    mutated = CEMENT_Q1.replace(b">89,680,417</ix:nonFraction>", b">NaN</ix:nonFraction>")

    with pytest.raises(IXBRLParseError, match="finite"):
        parse_ixbrl_report(mutated)


def test_an_infinite_amount_is_refused() -> None:
    mutated = CEMENT_Q1.replace(b">89,680,417</ix:nonFraction>", b">Infinity</ix:nonFraction>")

    with pytest.raises(IXBRLParseError, match="finite"):
        parse_ixbrl_report(mutated)


def test_two_contexts_with_one_id_and_different_periods_fail_closed() -> None:
    duplicate = (
        b'<xbrli:context id="AsOf20250331"><xbrli:entity><xbrli:identifier '
        b'scheme="http://www.twse.com.tw">1101</xbrli:identifier></xbrli:entity>'
        b"<xbrli:period><xbrli:instant>1900-01-01</xbrli:instant></xbrli:period>"
        b"</xbrli:context>"
    )
    mutated = CEMENT_Q1.replace(
        b'<xbrli:context id="AsOf20250331">', duplicate + b'<xbrli:context id="AsOf20250331">', 1
    )

    with pytest.raises(IXBRLParseError, match="AsOf20250331"):
        parse_ixbrl_report(mutated)


def test_the_same_context_declared_twice_identically_is_accepted() -> None:
    # 2886 2025Q4 declares AsOf20241231 twice, character for character the same.
    import re as _re

    body = _re.search(
        rb'<xbrli:context id="AsOf20250331">.*?</xbrli:context>', CEMENT_Q1, _re.S
    ).group(0)
    mutated = CEMENT_Q1.replace(body, body + body, 1)

    report = parse_ixbrl_report(mutated)

    assert report.contexts["AsOf20250331"].instant_date == date(2025, 3, 31)


def test_two_units_with_one_id_and_different_measures_fail_closed() -> None:
    mutated = CEMENT_Q1.replace(
        b'<xbrli:unit id="TWD">',
        b'<xbrli:unit id="TWD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
        b'<xbrli:unit id="TWD">',
        1,
    )

    with pytest.raises(IXBRLParseError, match="TWD"):
        parse_ixbrl_report(mutated)


def test_a_divide_unit_the_parser_cannot_read_is_not_downgraded_to_its_numerator() -> None:
    # Reversing numerator and denominator must not silently make EPS a currency.
    mutated = CEMENT_Q1.replace(b"<xbrli:unitNumerator>", b"<xbrli:unitDenominator>", 1).replace(
        b"</xbrli:unitNumerator>", b"</xbrli:unitDenominator>", 1
    )

    with pytest.raises(IXBRLParseError, match="EarningsPerShare"):
        parse_ixbrl_report(mutated)


def test_a_transformation_format_the_parser_does_not_implement_fails_closed() -> None:
    # ixt:numcommadecimal reads 1.234,56 the other way round. Accepting it
    # silently would turn a decimal point into a thousands separator.
    mutated = CEMENT_Q1.replace(b'format="ixt:numdotdecimal"', b'format="ixt:numcommadecimal"', 1)

    with pytest.raises(IXBRLParseError, match="numcommadecimal"):
        parse_ixbrl_report(mutated)


def test_a_numeric_fact_without_a_format_fails_closed() -> None:
    # The 132 format-less nonFraction elements in the archive all have an empty
    # unitRef and are already classified as prose; a real amount must say how to
    # read it.
    mutated = CEMENT_Q1.replace(b'format="ixt:numdotdecimal" scale="3" decimals="-3" unitRef="TWD">89,680,417',
                                b'scale="3" decimals="-3" unitRef="TWD">89,680,417', 1)

    with pytest.raises(IXBRLParseError, match="format"):
        parse_ixbrl_report(mutated)


def test_the_format_is_kept_on_the_fact() -> None:
    fact = find(parse_ixbrl_report(CEMENT_Q1), "CashAndCashEquivalents", "AsOf20250331")

    assert fact.format == "ixt:numdotdecimal"


def test_one_prefix_declared_twice_with_different_uris_fails_closed() -> None:
    mutated = CEMENT_Q1.replace(
        b'xmlns:ifrs-full="', b'xmlns:ifrs-full="http://example.invalid/other" xmlns:ifrs-full="', 1
    )

    with pytest.raises(IXBRLParseError, match="ifrs-full"):
        parse_ixbrl_report(mutated)


def test_an_uppercase_xmlns_declaration_is_still_a_declaration() -> None:
    mutated = CEMENT_Q1.replace(b'xmlns:ifrs-full=', b'XMLNS:ifrs-full=', 1)

    report = parse_ixbrl_report(mutated)

    assert f"{{{IFRS}}}CashAndCashEquivalents" in {
        fact.concept_qname for fact in report.facts
    }


def test_parentheses_and_a_negative_sign_together_are_refused() -> None:
    # No archive document prints a parenthesised amount at all. Applying both
    # conventions would negate twice, so the combination fails closed instead.
    mutated = CEMENT_Q1.replace(
        b'sign="-" decimals="-3" unitRef="TWD">732,459',
        b'sign="-" decimals="-3" unitRef="TWD">(732,459)',
        1,
    )

    with pytest.raises(IXBRLParseError, match="sign"):
        parse_ixbrl_report(mutated)


def test_the_label_comes_from_the_first_span_pair_in_the_row() -> None:
    report = parse_ixbrl_report(CEMENT_Q1)
    fact = find(report, "CashAndCashEquivalents", "AsOf20250331")

    assert fact.label_zh == "現金及約當現金"
    assert fact.label_en == "Cash and cash equivalents"


def test_the_caller_can_state_the_encoding_instead_of_letting_it_be_sniffed() -> None:
    assert decode_ixbrl_document(CEMENT_Q1_OFFICIAL, encoding="cp950") == CEMENT_Q1.decode("utf-8")
    assert parse_ixbrl_report(CEMENT_Q1, encoding="utf-8").header.company_id == "1101"

    with pytest.raises(IXBRLParseError, match="utf-8"):
        decode_ixbrl_document(CEMENT_Q1_OFFICIAL, encoding="utf-8")
