"""Step 22-c — when legacy's note is the page's note read through a bad decoder.

Step 22-b measured the four ways legacy's copy differs with no issuer rewrite
(audit §4.7). Only those shapes may dedup onto the official version: anything
else is a different filing, and giving it today's wording on a version dated to
legacy's capture is the look-ahead this step exists to avoid.
"""

from __future__ import annotations

from stock_data_center.ingestion.monthly_revenue_archive import same_published_note

STEEL = "本月及本年營收較去年同期增加，係因鎳價上漲帶動不銹鋼售價提高，另一因素為去年基期較低。"
STEEL_MANGLED = (
    "本月及本年營收較去年同期增加，係因鎳價上漲帶動不�袗�售價提高，另一因素為去年基期較低。"
)


def test_a_note_legacy_could_not_decode_is_the_same_note() -> None:
    assert same_published_note(STEEL, STEEL_MANGLED)


def test_a_note_the_issuer_rewrote_is_not_the_same_note() -> None:
    """Even when legacy's copy of it is also mangled."""
    rewritten = "本月營收較去年同期減少，主要係出貨遞延至次月所致。"
    assert not same_published_note(rewritten, STEEL_MANGLED)


def test_a_mangled_note_with_extra_text_is_not_the_same_note() -> None:
    assert not same_published_note(STEEL, STEEL_MANGLED + "另補充：本月無重大事項。")


def test_collapsed_whitespace_and_the_literal_NA_still_count() -> None:
    assert same_published_note("1. 甲   2. 乙", "1. 甲 2. 乙")
    assert same_published_note("NA", None)
    assert not same_published_note("重要說明", None)


def test_the_same_page_bytes_read_as_another_character_count() -> None:
    """`‧` (U+2027) and `•` (U+2022) are both `A1 45` in the page."""
    assert same_published_note("甲‧乙", "甲•乙")
