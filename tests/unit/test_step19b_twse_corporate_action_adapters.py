"""Step 19-b — the TWSE result-feed adapters and their detail pages.

Every fixture is a response TWSE returned on 2026-09-16; the files named
`fetched_20260916` are current-year files that already list later events.
TWSE's list rows publish no amounts, so every TWT49U and TWTAUU row is
completed by one detail page. TWTB8U's detail page publishes nothing new
(verified in 19-a) and is not fetched.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.ingestion.adapters import (
    CorporateActionListAdapter,
    TPExExRightDailyAdapter,
    TWSEDividendDetailAdapter,
    TWSEExRightAdapter,
    TWSEParValueChangeAdapter,
    TWSEReductionAdapter,
    TWSEReductionDetailAdapter,
)
from stock_data_center.ingestion.models import (
    CorporateActionDetailRequest,
    CorporateActionRangeRequest,
    SourceDataError,
)
from stock_data_center.market_reference.models import (
    CorporateActionObservation,
    SignedTwdAmount,
    TwdAmount,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def mutate(raw: bytes, edit) -> bytes:
    payload = json.loads(raw)
    edit(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def year(value: int, executed_through: date | None = None) -> CorporateActionRangeRequest:
    return CorporateActionRangeRequest(
        start=date(value, 1, 1),
        end=date(value, 12, 31),
        executed_through=executed_through or date(2026, 9, 16),
    )


def twd(value: str) -> TwdAmount:
    return TwdAmount(Decimal(value))


def signed(value: str) -> SignedTwdAmount:
    return SignedTwdAmount(Decimal(value))


def only(parsed, code: str, event_date: date):
    matches = [
        row for row in parsed.rows
        if row.security_code == code and row.event_date == event_date
    ]
    assert len(matches) == 1, f"{code} {event_date} appears {len(matches)} times"
    return matches[0]


TWT49U_2024 = load("twse_twt49u_2024.json")
TWTAUU_2024 = load("twse_twtauu_2024.json")
TWTB8U_2025 = load("twse_twtb8u_2025.json")


def twt49u(content: bytes = TWT49U_2024, request=None):
    return TWSEExRightAdapter().parse(content, request or year(2024))


def dividend_detail(name: str, row):
    return TWSEDividendDetailAdapter().parse(
        load(f"twse_detail_{name}.json"), row.detail_request
    )


def twtauu(content: bytes = TWTAUU_2024, request=None):
    return TWSEReductionAdapter().parse(content, request or year(2024))


def reduction_detail(name: str, row, content: bytes | None = None):
    return TWSEReductionDetailAdapter().parse(
        content or load(f"twse_detail_{name}.json"), row.detail_request
    )


def test_every_adapter_is_a_twse_result_feed_with_its_own_source() -> None:
    adapters = (TWSEExRightAdapter(), TWSEReductionAdapter(), TWSEParValueChangeAdapter())
    assert [adapter.feed for adapter in adapters] == ["TWT49U", "TWTAUU", "TWTB8U"]
    assert [adapter.source for adapter in adapters] == [
        "twse_twt49u", "twse_twtauu", "twse_twtb8u",
    ]
    for adapter in adapters:
        assert isinstance(adapter, CorporateActionListAdapter)
        assert adapter.dataset_code == "corporate_action"
        assert adapter.market == "TWSE"
    # A detail artifact belongs to the source whose row it completes.
    assert TWSEDividendDetailAdapter.source == TWSEExRightAdapter.source
    assert TWSEReductionDetailAdapter.source == TWSEReductionAdapter.source
    assert TWSEDividendDetailAdapter.dataset_code == "corporate_action"


def test_the_list_resource_requests_json_for_the_range() -> None:
    """CSV replaces the locator with a link label (audit 4.10)."""
    resource = TWSEExRightAdapter().resource(year(2024))
    assert resource.source_uri == (
        "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
        "?startDate=20240101&endDate=20241231&response=json"
    )
    assert resource.resource_key == "twse_twt49u:2024-01-01:2024-12-31"


def test_a_detail_resource_is_keyed_by_its_event() -> None:
    row = only(twt49u(), "2454", date(2024, 1, 4))
    resource = TWSEDividendDetailAdapter().resource(row.detail_request)
    assert resource.resource_key == "twse_twt49u:detail:2454:TWT49U:20240104"


def test_the_security_name_is_not_event_content() -> None:
    def rename(payload):
        payload["data"][0][2] = "聯發科技"

    row = only(twt49u(), "2454", date(2024, 1, 4))
    renamed = only(twt49u(mutate(TWT49U_2024, rename)), "2454", date(2024, 1, 4))
    detail = dividend_detail("49_2454_20240104", row)
    adapter = TWSEExRightAdapter()
    assert adapter.observation(row, detail) == adapter.observation(renamed, detail)


def test_the_latest_filing_columns_are_not_event_content() -> None:
    def refile(payload):
        payload["data"][0][12] = "115年第3季(https://mops.twse.com.tw/mops/web/t163sb01)"
        payload["data"][0][13] = "300.00"
        payload["data"][0][14] = "40.00"

    row = only(twt49u(), "2454", date(2024, 1, 4))
    refiled = only(twt49u(mutate(TWT49U_2024, refile)), "2454", date(2024, 1, 4))
    detail = dividend_detail("49_2454_20240104", row)
    adapter = TWSEExRightAdapter()
    assert adapter.observation(row, detail) == adapter.observation(refiled, detail)


def test_a_tpex_row_takes_no_detail() -> None:
    parsed = TPExExRightDailyAdapter().parse(load("tpex_exdailyq_2024.json"), year(2024))
    row = parsed.rows[0]
    assert row.detail_request is None
    twse_row = only(twt49u(), "2454", date(2024, 1, 4))
    detail = replace(
        dividend_detail("49_2454_20240104", twse_row), security_code=row.security_code
    )
    with pytest.raises(ValueError, match="take no detail"):
        TPExExRightDailyAdapter().observation(row, detail)


# --- identity (ROADMAP §27.7) -----------------------------------------------


def test_source_event_key_is_the_feed_and_the_exchange_locator_dates() -> None:
    assert only(twt49u(), "2454", date(2024, 1, 4)).locator.source_event_key == (
        "TWT49U:20240104"
    )
    # TWTAUU's own locator is its file date, not the resumption date.
    assert only(twtauu(), "3308", date(2024, 4, 1)).locator.source_event_key == (
        "TWTAUU:20240320"
    )
    # TWTB8U's locator carries the halt date and the resumption date.
    par = TWSEParValueChangeAdapter().parse(TWTB8U_2025, year(2025))
    assert only(par, "2327", date(2025, 8, 25)).locator.source_event_key == (
        "TWTB8U:20250814,20250825"
    )


def test_the_key_holds_no_revision_content() -> None:
    """Corrected terms under one locator keep the key; §51.5's list stays out."""
    original = only(twt49u(), "2454", date(2024, 1, 4))

    def correct(payload):
        for row in payload["data"]:
            if row[11] == "2454,20240104":
                row[2] = "聯發科技"          # company name
                row[3] = "960.00"            # close before
                row[4] = "930.00"            # reference price
                row[5] = "30.000000"         # rights + dividend value
                row[6] = "權息"              # action type

    corrected = only(twt49u(mutate(TWT49U_2024, correct)), "2454", date(2024, 1, 4))
    assert corrected.locator == original.locator
    assert corrected.action_type != original.action_type
    assert corrected.fields != original.fields


def test_separate_events_have_separate_keys_and_a_file_never_repeats_one() -> None:
    parsed = twt49u()
    keys = [(row.security_code, row.locator.source_event_key) for row in parsed.rows]
    assert len(keys) == len(set(keys)) == 1184
    # 2543 went ex twice in 2024: two events, two keys.
    assert {
        row.locator.source_event_key
        for row in parsed.rows if row.security_code == "2543"
    } == {"TWT49U:20240530", "TWT49U:20241022"}


def test_a_repeated_locator_quarantines_the_file() -> None:
    def repeat(payload):
        payload["data"].append(list(payload["data"][0]))

    with pytest.raises(SourceDataError) as error:
        twt49u(mutate(TWT49U_2024, repeat))
    assert error.value.reason_code == "ambiguous_identity"


def test_a_locator_that_disagrees_with_its_row_fails_closed() -> None:
    def other_code(payload):
        payload["data"][0][11] = "2330,20240104"

    def other_date(payload):
        payload["data"][0][11] = "2454,20240105"

    def malformed(payload):
        payload["data"][0][11] = "除權息資料"

    def earlier_date(payload):
        payload["data"][0][11] = "2454,20240103"

    for edit in (other_code, other_date, earlier_date, malformed):
        with pytest.raises(SourceDataError) as error:
            twt49u(mutate(TWT49U_2024, edit))
        assert error.value.reason_code == "invalid_identity"


def test_a_par_value_locator_must_end_on_its_resumption() -> None:
    def other_resumption(payload):
        for row in payload["data"]:
            if row[1] == "2327":
                row[8] = "2327,20250814,20250822"

    with pytest.raises(SourceDataError) as error:
        TWSEParValueChangeAdapter().parse(mutate(TWTB8U_2025, other_resumption), year(2025))
    assert error.value.reason_code == "invalid_identity"


def test_a_reduction_locator_must_not_follow_its_resumption() -> None:
    def late(payload):
        for row in payload["data"]:
            if row[1] == "3308":
                row[10] = "3308  ,20240402"

    with pytest.raises(SourceDataError) as error:
        twtauu(mutate(TWTAUU_2024, late))
    assert error.value.reason_code == "invalid_identity"


# --- executed events only ----------------------------------------------------


def test_rows_dated_after_executed_through_are_not_events_yet() -> None:
    """A current-year file lists results the exchange has not executed yet.

    Fetched 2026-09-16, TWT49U already lists 2026-09-17. Invariant G(2) rests on
    the event being executed, so those rows are counted and left for a later
    fetch, and the file claims completeness only through `executed_through`.
    """
    content = load("twse_twt49u_2026_fetched_20260916.json")
    dates = [row[0] for row in json.loads(content)["data"]]
    future = sum(1 for value in dates if value > "115年09月15日")
    assert future == 35

    parsed = twt49u(content, year(2026, executed_through=date(2026, 9, 15)))
    assert parsed.not_yet_executed == future
    assert len(parsed.rows) == len(dates) - future
    assert max(row.event_date for row in parsed.rows) == date(2026, 9, 15)
    assert (parsed.coverage_start, parsed.coverage_end) == (
        date(2026, 1, 1), date(2026, 9, 15),
    )

    # TWTAUU lists resumptions to 2026-10-19, the last three with `-` for
    # every price: parsing them would fail, and they are not events yet.
    content = load("twse_twtauu_2026_fetched_20260916.json")
    later = [row for row in json.loads(content)["data"] if row[0] > "115/09/15"]
    assert len(later) == 8
    assert sum(1 for row in later if row[3] == "-") == 3
    reductions = twtauu(content, year(2026, executed_through=date(2026, 9, 15)))
    assert reductions.not_yet_executed == len(later)
    assert all(row.event_date <= date(2026, 9, 15) for row in reductions.rows)


def test_a_past_range_is_complete_to_its_end() -> None:
    parsed = twt49u()
    assert parsed.not_yet_executed == 0
    assert (parsed.coverage_start, parsed.coverage_end) == (
        date(2024, 1, 1), date(2024, 12, 31),
    )


# --- TWT49U + TWT49UDetail ---------------------------------------------------


def test_twt49u_list_values_equal_legacy_dividend() -> None:
    """Three legacy `dividend` rows, one per type; 19-c reconciles all 6,182."""
    parsed = twt49u()
    legacy = {
        ("2454", date(2024, 1, 4)): ("953.00", "928.40", "24.600000", "ex_dividend"),
        ("6442", date(2024, 1, 10)): ("67.20", "65.94", "1.254762", "ex_right"),
        ("2543", date(2024, 5, 30)): ("59.50", "48.42", "11.076155", "ex_right_dividend"),
    }
    for (code, day), (close, reference, value, action) in legacy.items():
        row = only(parsed, code, day)
        assert row.action_type == action
        assert row.fields["ex_date"] == day
        assert row.fields["close_before"] == twd(close)
        assert row.fields["official_reference_price"] == twd(reference)
        assert row.fields["official_rights_dividend_value"] == signed(value)
    # Legacy kept only four-digit codes: ETFs, preferred shares and TDRs are new.
    assert sum(
        1 for row in parsed.rows
        if len(row.security_code) == 4 and not row.security_code.startswith("0")
    ) == 907


def test_twt49u_keeps_event_terms_and_drops_the_current_snapshot_columns() -> None:
    """`最近一次申報*` is today's filing, not the event's: a 2024 row carries
    115年第2季. Storing it would revise every past event each quarter."""
    row = only(twt49u(), "2454", date(2024, 1, 4))
    assert row.source_event_type == "息"
    assert row.source_terms == {
        "漲停價格": "1020.00",
        "跌停價格": "836.00",
        "開盤競價基準": "928.00",
        "減除股利參考價": "928.40",
    }
    raw = json.loads(TWT49U_2024)
    assert "115年第2季" in raw["data"][0][12]


def test_every_twt49u_row_waits_for_its_detail() -> None:
    parsed = twt49u()
    assert all(row.detail_request is not None for row in parsed.rows)
    row = only(parsed, "2454", date(2024, 1, 4))
    assert row.detail_request == CorporateActionDetailRequest(row.locator)
    resource = TWSEDividendDetailAdapter().resource(row.detail_request)
    assert resource.source_uri == (
        "https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail"
        "?STK_NO=2454&T1=20240104&response=json"
    )
    with pytest.raises(SourceDataError) as error:
        TWSEExRightAdapter().observation(row)
    assert error.value.reason_code == "detail_required"


def test_a_cash_dividend_takes_its_amount_from_the_detail() -> None:
    row = only(twt49u(), "2454", date(2024, 1, 4))
    observation = TWSEExRightAdapter().observation(
        row, dividend_detail("49_2454_20240104", row)
    )
    assert isinstance(observation, CorporateActionObservation)
    assert observation.action_type == "ex_dividend"
    assert observation.cash_dividend_per_share == twd("24.6")
    assert observation.free_share_ratio is None
    assert observation.rights_ratio is None
    assert observation.subscription_price is None
    assert observation.close_before == twd("953.00")
    # The unsourced columns stay NULL (ROADMAP §2.3).
    for name in (
        "announcement_date", "record_date", "payment_date",
        "earnings_stock_ratio", "capital_surplus_stock_ratio",
        "old_shares", "new_shares",
    ):
        assert getattr(observation, name) is None


def test_free_shares_and_rights_are_per_thousand_and_divided_by_1000() -> None:
    row = only(twt49u(), "2543", date(2024, 5, 30))
    observation = TWSEExRightAdapter().observation(
        row, dividend_detail("49_2543_20240530", row)
    )
    assert observation.action_type == "ex_right_dividend"
    assert observation.cash_dividend_per_share == twd("0.4")
    assert observation.free_share_ratio == Decimal("0.14")
    # 202.11906001 per 1,000 needs eleven places after the division.
    assert observation.rights_ratio == Decimal("0.20211906001")
    assert observation.subscription_price == twd("33")
    assert observation.source_terms["C. (有償) 現金增資"] == "60000000"
    assert observation.source_terms["a. 公開承銷"] == "6000000"
    assert observation.source_terms["減除股利參考價"] == "51.84"


def test_a_rights_only_event() -> None:
    row = only(twt49u(), "6442", date(2024, 1, 10))
    observation = TWSEExRightAdapter().observation(
        row, dividend_detail("49_6442_20240110", row)
    )
    assert observation.action_type == "ex_right"
    assert observation.cash_dividend_per_share is None
    assert observation.free_share_ratio is None
    assert observation.rights_ratio == Decimal("0.10520361991")
    assert observation.subscription_price == twd("57")


def test_the_preferred_share_detail_is_its_own_variant() -> None:
    """Seven columns, F and G per thousand preferred shares."""
    content = load("twse_twt49u_2026_fetched_20260916.json")
    row = only(
        twt49u(content, year(2026, executed_through=date(2026, 9, 15))),
        "1312A", date(2026, 6, 11),
    )
    detail = dividend_detail("49_1312A_20260611", row)
    assert detail.variant == "preferred"
    observation = TWSEExRightAdapter().observation(row, detail)
    assert observation.action_type == "ex_right"
    assert observation.rights_ratio == Decimal("0.28404")
    assert observation.subscription_price == twd("14")
    assert observation.free_share_ratio is None
    # Priced above the close, so the reference rises: 權值+息值 is
    # 除權息前收盤價 − 除權息參考價 and published negative (4 TWSE rows, 2 TPEx).
    assert observation.official_rights_dividend_value == signed("-0.170421")


def test_the_common_share_detail_is_named_as_such() -> None:
    row = only(twt49u(), "2454", date(2024, 1, 4))
    assert dividend_detail("49_2454_20240104", row).variant == "common"


def test_a_type_the_terms_contradict_fails_closed() -> None:
    """Held on all 7,359 TPEx rows and on 53 sampled TWSE details."""
    row = only(twt49u(), "2454", date(2024, 1, 4))
    detail = dividend_detail("49_2454_20240104", row)
    adapter = TWSEExRightAdapter()
    no_cash = replace(detail, values={**detail.values, "cash_dividend": None})
    with pytest.raises(SourceDataError) as error:
        adapter.observation(row, no_cash)
    assert error.value.reason_code == "inconsistent_terms"

    free_too = replace(detail, values={**detail.values, "free_per_thousand": Decimal(50)})
    with pytest.raises(SourceDataError) as error:
        adapter.observation(row, free_too)
    assert error.value.reason_code == "inconsistent_terms"

    rights = only(twt49u(), "6442", date(2024, 1, 10))
    rights_detail = dividend_detail("49_6442_20240110", rights)
    unpriced = replace(
        rights_detail, values={**rights_detail.values, "subscription_price": None}
    )
    with pytest.raises(SourceDataError) as error:
        adapter.observation(rights, unpriced)
    assert error.value.reason_code == "inconsistent_terms"


def test_a_detail_for_another_security_fails_closed() -> None:
    row = only(twt49u(), "6442", date(2024, 1, 10))
    with pytest.raises(SourceDataError) as error:
        dividend_detail("49_2454_20240104", row)
    assert error.value.reason_code == "invalid_identity"


def test_an_empty_detail_is_quarantined_not_read_as_no_terms() -> None:
    row = only(twt49u(), "2454", date(2024, 1, 4))
    with pytest.raises(SourceDataError) as error:
        TWSEDividendDetailAdapter().parse(load("twse_detail_empty.json"), row.detail_request)
    assert error.value.reason_code == "no_data_for_date"


def test_a_detail_unit_that_is_not_the_declared_one_fails_closed() -> None:
    row = only(twt49u(), "2454", date(2024, 1, 4))

    def shares_for_cash(payload):
        payload["data"][0][2] = "24.6 股"

    with pytest.raises(SourceDataError) as error:
        TWSEDividendDetailAdapter().parse(
            mutate(load("twse_detail_49_2454_20240104.json"), shares_for_cash),
            row.detail_request,
        )
    assert error.value.reason_code == "unit_mismatch"


def test_a_changed_detail_header_fails_closed() -> None:
    row = only(twt49u(), "2454", date(2024, 1, 4))

    def rename(payload):
        payload["fields"][4] = "A. 每千股無償配股"

    with pytest.raises(SourceDataError) as error:
        TWSEDividendDetailAdapter().parse(
            mutate(load("twse_detail_49_2454_20240104.json"), rename),
            row.detail_request,
        )
    assert error.value.reason_code == "schema_mismatch"


def test_a_detail_for_a_feed_it_does_not_complete_is_refused() -> None:
    row = only(twtauu(), "3308", date(2024, 4, 1))
    with pytest.raises(ValueError):
        TWSEDividendDetailAdapter().resource(row.detail_request)


# --- TWT49U list failure modes -----------------------------------------------


def test_an_empty_range_is_a_valid_answer_with_no_events() -> None:
    parsed = twt49u(load("twse_twt49u_empty.json"))
    assert parsed.rows == ()
    assert parsed.coverage_end == date(2024, 12, 31)
    empty_par = TWSEParValueChangeAdapter().parse(
        load("twse_twtb8u_2023_empty.json"), year(2023)
    )
    assert empty_par.rows == ()


def test_any_other_status_is_not_an_empty_range() -> None:
    maintenance = json.dumps({"stat": "系統維護中"}, ensure_ascii=False).encode()
    with pytest.raises(SourceDataError) as error:
        twt49u(maintenance)
    assert error.value.reason_code == "source_status"


def test_the_echoed_range_must_be_the_requested_one() -> None:
    def shifted(payload):
        payload["endDate"] = "20241130"

    with pytest.raises(SourceDataError) as error:
        twt49u(mutate(TWT49U_2024, shifted))
    assert error.value.reason_code == "date_mismatch"

    def outside(payload):
        payload["data"][0][0] = "112年12月29日"
        payload["data"][0][11] = "2454,20231229"

    with pytest.raises(SourceDataError) as error:
        twt49u(mutate(TWT49U_2024, outside))
    assert error.value.reason_code == "date_mismatch"


def test_a_changed_list_header_fails_closed() -> None:
    def rename(payload):
        payload["fields"][5] = "權值"

    with pytest.raises(SourceDataError) as error:
        twt49u(mutate(TWT49U_2024, rename))
    assert error.value.reason_code == "schema_mismatch"


def test_an_unknown_event_type_fails_closed() -> None:
    def unknown(payload):
        payload["data"][0][6] = "權證"

    with pytest.raises(SourceDataError) as error:
        twt49u(mutate(TWT49U_2024, unknown))
    assert error.value.reason_code == "unknown_event_type"


# --- TWTAUU + TWTAVUDetail ---------------------------------------------------


def test_a_cash_refund_reduction() -> None:
    row = only(twtauu(), "3308", date(2024, 4, 1))
    assert row.source_event_type == "退還股款"
    resource = TWSEReductionDetailAdapter().resource(row.detail_request)
    assert resource.source_uri == (
        "https://www.twse.com.tw/rwd/zh/reducation/TWTAVUDetail"
        "?STK_NO=3308&FILE_DATE=20240320&response=json"
    )
    observation = TWSEReductionAdapter().observation(
        row, reduction_detail("au_3308_20240320", row)
    )
    assert observation.action_type == "capital_reduction"
    assert observation.capital_reduction_kind == "cash_refund"
    assert observation.ex_date == date(2024, 4, 1)
    assert (observation.old_shares, observation.new_shares) == (
        Decimal(1000), Decimal("855.66635"),
    )
    assert observation.capital_reduction_cash_return_per_share == twd("1.443336")
    assert observation.cash_dividend_per_share is None
    assert observation.close_before == twd("28.20")
    assert observation.official_reference_price == twd("31.26")
    assert observation.source_terms["停止買賣日期"] == "2024-03-21"
    assert "除權參考價" not in observation.source_terms


def test_a_loss_offset_reduction() -> None:
    row = only(twtauu(), "3432", date(2024, 1, 22))
    observation = TWSEReductionAdapter().observation(
        row, reduction_detail("au_3432_20240110", row)
    )
    assert observation.capital_reduction_kind == "loss_offset"
    assert observation.capital_reduction_cash_return_per_share is None
    assert observation.new_shares == Decimal("540.65419")


def test_a_reduction_filed_with_an_ex_dividend_keeps_both_amounts() -> None:
    """`除息併案辦理減資`: TWT49U leaves these out, TWTAUU carries the cash."""
    row = only(twtauu(), "3356", date(2024, 11, 11))
    observation = TWSEReductionAdapter().observation(
        row, reduction_detail("au_3356_20241030", row)
    )
    assert observation.capital_reduction_kind == "cash_refund"
    assert observation.capital_reduction_cash_return_per_share == twd("1.1")
    assert observation.cash_dividend_per_share == twd("2.9")


def test_a_reason_the_cash_contradicts_fails_closed() -> None:
    refund = only(twtauu(), "3308", date(2024, 4, 1))
    offset = only(twtauu(), "3432", date(2024, 1, 22))
    adapter = TWSEReductionAdapter()

    detail = reduction_detail("au_3308_20240320", refund)
    no_cash = replace(detail, values={**detail.values, "cash_return": None})
    with pytest.raises(SourceDataError) as error:
        adapter.observation(refund, no_cash)
    assert error.value.reason_code == "inconsistent_terms"

    detail = reduction_detail("au_3432_20240110", offset)
    cash = replace(detail, values={**detail.values, "cash_return": Decimal(1)})
    with pytest.raises(SourceDataError) as error:
        adapter.observation(offset, cash)
    assert error.value.reason_code == "inconsistent_terms"


def test_a_loss_offset_with_a_cash_increase_is_its_own_kind() -> None:
    """Not observed 2020-2026; the detail labels it explicitly, so it maps."""
    row = only(twtauu(), "3432", date(2024, 1, 22))

    def increase(payload):
        payload["data"][0][6] = "5,000,000 股"
        payload["data"][0][7] = "10 元/股"
        payload["data"][0][11] = "150.5 股"

    detail = reduction_detail(
        "au_3432_20240110", row,
        mutate(load("twse_detail_au_3432_20240110.json"), increase),
    )
    observation = TWSEReductionAdapter().observation(row, detail)
    assert observation.capital_reduction_kind == "loss_offset_with_cash_increase"
    assert observation.rights_ratio == Decimal("0.1505")
    assert observation.subscription_price == twd("10")


def test_the_halt_date_must_fall_between_locator_and_resumption() -> None:
    row = only(twtauu(), "3308", date(2024, 4, 1))

    def late(payload):
        payload["data"][0][2] = "113/04/02"

    detail = reduction_detail(
        "au_3308_20240320", row,
        mutate(load("twse_detail_au_3308_20240320.json"), late),
    )
    with pytest.raises(SourceDataError) as error:
        TWSEReductionAdapter().observation(row, detail)
    assert error.value.reason_code == "inconsistent_terms"


def test_an_unknown_reduction_reason_fails_closed() -> None:
    def unknown(payload):
        payload["data"][0][9] = "分割減資"

    with pytest.raises(SourceDataError) as error:
        twtauu(mutate(TWTAUU_2024, unknown))
    assert error.value.reason_code == "unknown_event_type"


# --- TWTB8U -------------------------------------------------------------------


def test_a_twse_par_value_change_keeps_prices_and_claims_no_share_terms() -> None:
    """TWTB8UDetail repeats the list row and publishes no exchange ratio."""
    parsed = TWSEParValueChangeAdapter().parse(TWTB8U_2025, year(2025))
    assert len(parsed.rows) == 4
    row = only(parsed, "2327", date(2025, 8, 25))
    assert row.detail_request is None
    observation = TWSEParValueChangeAdapter().observation(row)
    assert observation.action_type == "other"
    assert observation.source_event_type == "變更股票面額"
    assert observation.ex_date == date(2025, 8, 25)
    assert observation.close_before == twd("546.00")
    assert observation.official_reference_price == twd("136.50")
    assert observation.old_shares is None and observation.new_shares is None
    assert observation.source_terms["停止買賣日期"] == "2025-08-14"
