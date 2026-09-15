"""Step 16 — the TWSE FMTQIK trading-calendar adapter.

FMTQIK lists each *actual* trading day of one month, so a closure is an absence,
never a flag. The adapter must therefore refuse anything that could silently
turn one month's list into another's.
"""

import json
from pathlib import Path
from datetime import date

import pytest

from stock_data_center.ingestion.adapters import TWSETradingCalendarAdapter
from stock_data_center.ingestion.models import (
    SourceDataError,
    TradingCalendarRequest,
)


FIELDS = ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"]


def payload(*roc_dates: str, stat: str = "OK", fields: list[str] | None = None) -> bytes:
    body = {
        "stat": stat,
        "title": "113年07月市場成交資訊",
        "fields": FIELDS if fields is None else fields,
        "data": [
            [day, "9,519,820,690", "435,755,556,160", "3,002,153", "23,058.57", "26.32"]
            for day in roc_dates
        ],
    }
    return json.dumps(body, ensure_ascii=False).encode()


def parse(content: bytes, month: date = date(2024, 7, 1)):
    return TWSETradingCalendarAdapter().parse(content, TradingCalendarRequest(month))


def test_month_of_trading_days_is_parsed_from_roc_dates() -> None:
    parsed = parse(payload("113/07/01", "113/07/02", "113/07/05"))

    assert parsed.market == "TWSE"
    assert parsed.month == date(2024, 7, 1)
    assert parsed.trading_days == (
        date(2024, 7, 1),
        date(2024, 7, 2),
        date(2024, 7, 5),
    )


def test_a_closure_is_an_absence_not_a_flag() -> None:
    """2024-07-24/25 were typhoon closures; FMTQIK simply omits them."""
    parsed = parse(payload("113/07/23", "113/07/26"))

    assert date(2024, 7, 24) not in parsed.trading_days
    assert date(2024, 7, 25) not in parsed.trading_days
    assert parsed.trading_days == (date(2024, 7, 23), date(2024, 7, 26))


def test_days_are_sorted_and_deduplicated_into_a_canonical_list() -> None:
    parsed = parse(payload("113/07/05", "113/07/01", "113/07/05", "113/07/02"))

    assert parsed.trading_days == (
        date(2024, 7, 1),
        date(2024, 7, 2),
        date(2024, 7, 5),
    )


def test_a_day_outside_the_requested_month_is_rejected() -> None:
    """The request key is the month; a payload for another month is not ours.

    The TDCC archive taught this the hard way (audit 4.9): a source that serves
    a neighbouring period under the requested name is silent, and keying on the
    request rather than the payload invents a period that never existed.
    """
    with pytest.raises(SourceDataError) as error:
        parse(payload("113/07/01", "113/06/28"))

    assert error.value.reason_code == "date_mismatch"


def test_an_empty_month_is_rejected_rather_than_read_as_a_full_closure() -> None:
    with pytest.raises(SourceDataError) as error:
        parse(payload())

    assert error.value.reason_code == "empty_coverage"


def test_a_source_level_error_is_not_read_as_an_empty_calendar() -> None:
    with pytest.raises(SourceDataError) as error:
        parse(payload("113/07/01", stat="很抱歉，沒有符合條件的資料!"))

    assert error.value.reason_code == "source_error"


@pytest.mark.parametrize(
    "roc_date",
    ["113/13/01", "113/07/32", "1130701", "113/7", "", "  ", "abc/07/01"],
)
def test_a_malformed_date_fails_loudly(roc_date: str) -> None:
    with pytest.raises(SourceDataError) as error:
        parse(payload(roc_date))

    assert error.value.reason_code == "invalid_date"


def test_a_changed_header_quarantines_instead_of_guessing_the_date_column() -> None:
    with pytest.raises(SourceDataError) as error:
        parse(payload("113/07/01", fields=["交易日期", "成交股數"]))

    assert error.value.reason_code == "schema_mismatch"


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(SourceDataError) as error:
        parse(b"<html>maintenance</html>")

    assert error.value.reason_code == "invalid_json"


def test_resource_identity_is_stable_and_names_the_official_endpoint() -> None:
    adapter = TWSETradingCalendarAdapter()
    resource = adapter.resource(TradingCalendarRequest(date(2024, 7, 1)))

    assert resource.resource_key == "twse:trading-calendar:2024-07"
    assert resource.source_uri == (
        "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
        "?date=20240701&response=json"
    )
    assert adapter.dataset_code == "trading_calendar"
    assert adapter.source == "twse"
    assert adapter.market == "TWSE"


def test_request_requires_the_first_day_of_a_month() -> None:
    with pytest.raises(ValueError):
        TradingCalendarRequest(date(2024, 7, 2))


REAL_CAPTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_real_july_2024_bytes_show_the_typhoon_closure_as_absence() -> None:
    """Captured from the official endpoint on 2026-09-15, byte for byte."""
    content = (REAL_CAPTURES / "twse_fmtqik_202407.json").read_bytes()

    parsed = parse(content, date(2024, 7, 1))

    assert len(parsed.trading_days) == 21
    assert parsed.trading_days[0] == date(2024, 7, 1)
    assert parsed.trading_days[-1] == date(2024, 7, 31)
    assert date(2024, 7, 24) not in parsed.trading_days
    assert date(2024, 7, 25) not in parsed.trading_days
    # 07-23 to 07-26 is the closure gap, not a weekend.
    assert parsed.trading_days[16] == date(2024, 7, 23)
    assert parsed.trading_days[17] == date(2024, 7, 26)


def test_real_january_2020_bytes_show_the_lunar_new_year_closure() -> None:
    content = (REAL_CAPTURES / "twse_fmtqik_202001.json").read_bytes()

    parsed = parse(content, date(2020, 1, 1))

    assert len(parsed.trading_days) == 15
    assert parsed.trading_days[0] == date(2020, 1, 2)
    assert parsed.trading_days[-1] == date(2020, 1, 31)
    assert parsed.trading_days[12] == date(2020, 1, 20)
    assert parsed.trading_days[13] == date(2020, 1, 30)
