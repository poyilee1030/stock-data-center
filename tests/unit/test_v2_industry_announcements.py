"""Step 39-a: the exchanges' industry reclassification announcements.

The fixtures are live responses saved on 2026-09-26: TWSE's announcement list
and four details (109–115 formats and the 108 one before the window), the TPEx
lists of 2023 and 2025 and three details, and the 112 attachments of both
exchanges, whose PDF tables carry the only record of each company's old
category that year.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from stock_data_center.v2 import industry
from stock_data_center.v2 import industry_changes as ic
from stock_data_center.v2.release_rules import (
    INDUSTRY_ANNOUNCEMENT,
    industry_announcement_available_from,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v2" / "industry"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ---------------------------------------------------------------- codes


@pytest.mark.parametrize(
    ("name", "code"),
    [
        # ISIN names, as `stocks.industry` holds them
        ("建材營造業", "14"), ("其他業", "20"), ("半導體業", "24"), ("觀光餐旅", "16"),
        ("金融保險業", "17"), ("數位雲端", "36"), ("居家生活", "38"),
        # the announcements' own spellings
        ("建材營造", "14"), ("其他", "20"), ("生技醫療", "22"), ("半導體", "24"),
        ("貿易百貨", "18"), ("文化創意", "32"), ("電腦及週邊設備業", "25"),
        # categories renamed or merged in 2023
        ("觀光事業", "16"), ("電子商務", "34"), ("電子商務業", "34"),
    ],
)
def test_every_published_spelling_maps_to_its_isin_code(name, code):
    assert industry.code_of(name) == code


def test_an_unknown_name_has_no_code():
    assert industry.code_of("電腦及週邊") is None  # half of a wrapped PDF cell
    assert industry.code_of("") is None


def test_category_16_was_renamed_on_2023_07_03():
    assert industry.name_of("16", date(2023, 7, 2)) == "觀光事業"
    assert industry.name_of("16", date(2023, 7, 3)) == "觀光餐旅"
    assert industry.name_of("24", date(2020, 1, 2)) == "半導體業"


def test_categories_exist_only_while_the_exchange_had_them():
    before, on = date(2023, 7, 2), date(2023, 7, 3)
    for code in ("35", "36", "37", "38"):
        for market in ("sii", "otc"):
            assert not industry.exists(code, market, before)
            assert industry.exists(code, market, on)
    # TPEx merged 電子商務 into 數位雲端 and 貿易百貨 into 居家生活
    assert industry.exists("34", "otc", before) and not industry.exists("34", "otc", on)
    assert industry.exists("18", "otc", before) and not industry.exists("18", "otc", on)
    assert industry.exists("18", "sii", on)
    assert not industry.exists("34", "sii", before)  # a TPEx category only
    assert not industry.exists("99", "sii", on)


# ---------------------------------------------------------------- release rule


def test_an_announcement_is_public_from_00_00_the_day_after_its_date():
    assert industry_announcement_available_from(date(2023, 5, 22)) == datetime(
        2023, 5, 22, 16, 0, tzinfo=UTC)  # 2023-05-23 00:00 Asia/Taipei
    assert INDUSTRY_ANNOUNCEMENT.rule_id == "industry_announcement_next_day"
    assert INDUSTRY_ANNOUNCEMENT.version == 1
    assert "ADR-0030" in INDUSTRY_ANNOUNCEMENT.authority


# ---------------------------------------------------------------- lists


def test_twse_list_names_every_announcement():
    listed = ic.parse_twse_list(_read("twse_list.json"))
    assert len(listed) == 11
    first = listed[0]
    assert first.key == "1151802340"  # the document number's digits
    assert first.locator == {"id": "88BA9DDB9BA111F19A80005056BE3760"}
    assert first.announced_on == date(2026, 8, 19)
    assert first.document_number == "臺證上一字第1151802340號"
    assert first.source == "twse_announcement"


def test_twse_list_with_changed_fields_is_refused():
    payload = json.loads(_read("twse_list.json"))
    payload["fields"][1] = "日期"
    with pytest.raises(ic.AnnouncementFormatError):
        ic.parse_twse_list(json.dumps(payload).encode())


def test_twse_list_missing_rows_is_refused():
    payload = json.loads(_read("twse_list.json"))
    payload["data"].pop()
    with pytest.raises(ic.AnnouncementFormatError):
        ic.parse_twse_list(json.dumps(payload).encode())


def test_tpex_list_decodes_the_detail_locator():
    listed = ic.parse_tpex_list(_read("tpex_list_2023.json"), 2023)
    assert [item.announced_on for item in listed] == [
        date(2023, 9, 22), date(2023, 5, 23), date(2023, 4, 18), date(2023, 3, 28)]
    annual = listed[1]
    assert annual.key == "11202011201"
    assert annual.locator == {"content_file": "MTEyMDIwMTEyMDEuaHRtbA==",
                              "docId": "MTEyMDIwMTEyMDE="}
    assert annual.source == "tpex_announcement"


def test_tpex_list_for_another_year_is_refused():
    with pytest.raises(ic.AnnouncementFormatError):
        ic.parse_tpex_list(_read("tpex_list_2023.json"), 2024)


@pytest.mark.parametrize(
    ("subject", "kind"),
    [
        ("上市公司計47家調整產業類別之實施日期，請查照。", "change"),
        ("立萬利創新股份有限公司申請調整產業類別，准予變更之實施日期，請查照。", "change"),
        ("調整部分上（興）櫃公司產業類別，自112年7月3日起實施。", "change"),
        ("公告調整部分上櫃公司產業類別，自115年6月1日起實施。", "change"),
        ("修正本公司「上市公司產業類別劃分暨調整要點」部分條文如附件一，自112年7月3日起實施，請查照。",
         "regulation"),
        ("修正本中心「上櫃公司產業類別劃分暨調整要點」第2點及第3點條文如附件一，自112年7月3日起實施。",
         "regulation"),
        ("修正本公司「有價證券上市審查準則」第二十一條、…「上市公司產業類別劃分暨調整要點」第二條及第三條",
         "regulation"),
        (("公告興櫃一般板公司愛爾達科技股份有限公司（股票代號：8487）之產業類別由「文化創意業」調整為「數位雲端」，"
          "並自112年9月25日起實施。"), "out_of_scope"),
        ("為創櫃板產業類別調整及新增，修正登錄創櫃板申請書，暨部分創櫃板公司配合調整其產業類別，自112年4月20日起實施。",
         "out_of_scope"),
    ],
)
def test_subjects_decide_which_announcements_are_changes(subject, kind):
    assert ic.classify(subject) == kind


# ---------------------------------------------------------------- details


def _twse(name: str) -> ic.Announcement:
    return ic.parse_twse_detail(_read(name))


def _tpex(name: str) -> ic.Announcement:
    return ic.parse_tpex_detail(_read(name))


def test_twse_110_detail_lists_every_change_in_its_text():
    notice = _twse("twse_detail_1101802256.json")
    assert notice.key == "1101802256"
    assert notice.announced_on == date(2021, 5, 4)
    assert notice.document_number == "臺證上一字第1101802256號"
    assert notice.effective_date == date(2021, 6, 1)
    assert notice.attachments == []
    changes = ic.text_changes(notice.text)
    assert len(changes) == 11
    assert changes[0] == ic.Change("1443", "紡織纖維", "其他")
    assert ic.Change("8499", "其他", "其他電子業") in changes


def test_twse_individual_change_of_2026():
    notice = _twse("twse_detail_1151802340.json")
    assert notice.effective_date == date(2026, 9, 1)
    assert ic.text_changes(notice.text) == [ic.Change("3054", "食品工業", "電子通路業")]


def test_twse_108_detail_takes_effect_before_the_window():
    notice = _twse("twse_detail_1081801827.json")
    assert notice.effective_date == date(2019, 7, 1)
    assert notice.effective_date < industry.WINDOW_START


def test_twse_112_detail_points_to_its_attachment():
    notice = _twse("twse_detail_1121802250.json")
    assert notice.effective_date == date(2023, 7, 3)
    assert ic.text_changes(notice.text) == []
    assert notice.attachments == [
        ("公告附件1", "https://www.twse.com.tw/staticFiles/announcement/announcement/1121802250-1.pdf")]
    assert len(ic.text_codes(notice.text, "sii")) == 47


def test_twse_112_attachment_gives_every_old_category():
    listed = ic.parse_attachment(_read("twse_1121802250-1.pdf"), "sii")
    assert len(listed) == 47
    assert ic.Change("3130", "資訊服務業", "數位雲端") in listed
    assert ic.Change("8454", "貿易百貨", "數位雲端") in listed
    # a cell PDF wraps over two lines
    assert ic.Change("2442", "電腦及週邊設備業", "建材營造") in listed
    assert ic.Change("2424", "電腦及週邊設備業", "通信網路業") in listed
    assert ic.Change("3054", "半導體業", "食品工業") in listed
    # an innovation-board company is listed like any other
    assert ic.Change("6869", "其他", "綠能環保") in listed


def test_tpex_112_detail_and_its_two_attachments():
    notice = _tpex("tpex_detail_11202011201.json")
    assert notice.announced_on == date(2023, 5, 23)
    assert notice.document_number == "證櫃監字第11202011201號"
    assert notice.effective_date == date(2023, 7, 3)  # from the subject
    assert [url for _, url in notice.attachments] == [
        "https://www.tpex.org.tw/storage/eb_data/11205/112020112011-1.pdf",
        "https://www.tpex.org.tw/storage/eb_data/11205/112020112011-2.pdf"]
    # the text's OTC part only: its emerging-board companies are out of scope
    codes = ic.text_codes(notice.text, "otc")
    assert len(codes) == 56 and "3085" in codes and "2949" not in codes
    otc = ic.parse_attachment(_read("tpex_112020112011-1.pdf"), "otc")
    assert len(otc) == 56
    assert ic.Change("5903", "貿易百貨", "居家生活") in otc
    assert ic.Change("3085", "電子商務", "數位雲端") in otc
    assert ic.Change("2718", "觀光事業", "建材營造") in otc
    assert ic.Change("6123", "電腦及週邊設備業", "資訊服務業") in otc
    assert {change.stock_id for change in otc} == codes
    # the second attachment lists emerging-board companies only
    assert ic.parse_attachment(_read("tpex_112020112011-2.pdf"), "otc") == []


def test_tpex_114_detail_survives_the_source_typos():
    notice = _tpex("tpex_detail_11402010541.json")
    assert notice.effective_date == date(2025, 6, 2)
    changes = ic.text_changes(notice.text)
    assert len(changes) == 11
    assert ic.Change("6187", "其他電子業", "半導體業") in changes  # no closing bracket
    assert ic.Change("6240", "其他", "資訊服務業") in changes  # 「其他」」
    assert ic.Change("3521", "電腦及週邊設備業", "建材營造") in changes  # 由（…）由


def test_tpex_109_detail_uses_older_spellings():
    notice = _tpex("tpex_detail_10902007161.json")
    assert notice.effective_date == date(2020, 6, 1)
    assert ic.text_changes(notice.text) == [
        ic.Change("3629", "光電業", "文化創意業"),
        ic.Change("3687", "文化創意業", "電子商務業"),
        ic.Change("5450", "電腦及週邊設備業", "其他業"),
        ic.Change("5481", "電子零組件業", "其他業"),
    ]


def test_disagreeing_effective_dates_are_refused():
    payload = json.loads(_read("tpex_detail_11402010541.json"))
    payload["data"]["subject"] = "調整部分上櫃公司產業類別，自114年6月3日起實施。"
    with pytest.raises(ic.AnnouncementFormatError):
        ic.parse_tpex_detail(json.dumps(payload, ensure_ascii=False).encode())


def test_a_detail_without_an_effective_date_is_refused():
    payload = json.loads(_read("twse_detail_1151802340.json"))
    payload["data"][0][5] = payload["data"][0][5].replace("實施日期：115年9月1日", "")
    with pytest.raises(ic.AnnouncementFormatError):
        ic.parse_twse_detail(json.dumps(payload, ensure_ascii=False).encode())


# ---------------------------------------------------------------- validation


def _notice(**overrides) -> ic.Announcement:
    fields = {"source": "twse_announcement", "key": "k", "document_number": "n",
              "announced_on": date(2023, 5, 22), "effective_date": date(2023, 7, 3),
              "subject": "上市公司計1家調整產業類別之實施日期", "text": "", "attachments": []}
    return ic.Announcement(**{**fields, **overrides})


def test_validation_accepts_what_the_source_published():
    changes = [ic.Change("3130", "資訊服務業", "數位雲端")]
    assert ic.validate(_notice(), changes) == changes


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (ic.Change("3130", "資訊", "數位雲端"), "unknown_industry"),
        (ic.Change("3130", "資訊服務業", "雲端"), "unknown_industry"),
        (ic.Change("3130", "數位雲端", "資訊服務業"), "industry_not_in_effect"),  # 36 is new that day
        (ic.Change("3130", "資訊服務業", "資訊服務業"), "no_change"),
    ],
)
def test_validation_refuses_what_cannot_be_true(change, reason):
    with pytest.raises(ic.AnnouncementFormatError, match=reason):
        ic.validate(_notice(), [change])


def test_validation_refuses_a_stock_listed_twice():
    changes = [ic.Change("3130", "資訊服務業", "數位雲端"), ic.Change("3130", "其他業", "數位雲端")]
    with pytest.raises(ic.AnnouncementFormatError, match="duplicate_stock"):
        ic.validate(_notice(), changes)


def test_validation_refuses_an_announcement_with_no_change():
    with pytest.raises(ic.AnnouncementFormatError, match="no_changes"):
        ic.validate(_notice(), [])


def test_tpex_merged_categories_are_valid_only_before_2023_07_03():
    otc = _notice(source="tpex_announcement")
    assert ic.validate(otc, [ic.Change("5903", "貿易百貨", "居家生活")])
    later = _notice(source="tpex_announcement", effective_date=date(2024, 6, 3))
    with pytest.raises(ic.AnnouncementFormatError, match="industry_not_in_effect"):
        ic.validate(later, [ic.Change("5903", "貿易百貨", "居家生活")])
