"""Step 19-a — the result-feed contract and the TPEx adapters, on captured bytes.

Every fixture is a response the endpoint returned on 2026-09-16. The file named
`fetched_20260916` is a current-year file fetched that day: it already lists
events dated after it, which is why a request carries `executed_through`.

Nothing here touches a database. Identity (ROADMAP §27.7 items 1, 2 and 5) and
the parse are proved on bytes; the correction and retraction regressions need
storage and belong to the import step.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.ingestion.adapters import (
    CorporateActionListAdapter,
    TPExExRightDailyAdapter,
    TPExParValueChangeAdapter,
    TPExReductionAdapter,
)
from stock_data_center.ingestion.models import (
    CorporateActionRangeRequest,
    ExchangeLocator,
    SourceDataError,
)
from stock_data_center.ingestion.observations import (
    CorporateActionObservation,
    MarketIndexObservation,
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


EXDAILYQ_2024 = load("tpex_exdailyq_2024.json")
REVIVT_2024 = load("tpex_revivt_2024.json")
PVCHG_2024 = load("tpex_pvchgrslt_2024.json")


def exdailyq(content: bytes = EXDAILYQ_2024, request=None):
    return TPExExRightDailyAdapter().parse(content, request or year(2024))


def revivt(content: bytes = REVIVT_2024, request=None):
    return TPExReductionAdapter().parse(content, request or year(2024))


def pv(content: bytes = PVCHG_2024, request=None):
    return TPExParValueChangeAdapter().parse(content, request or year(2024))


def edit_row(index: int, column: int, value: str):
    def edit(payload):
        payload["tables"][0]["data"][index][column] = value

    return edit


# --- identity (ROADMAP §27.7) -----------------------------------------------


def test_every_adapter_is_a_result_feed_with_its_own_source() -> None:
    adapters = (
        TPExExRightDailyAdapter(), TPExReductionAdapter(), TPExParValueChangeAdapter(),
    )
    assert [adapter.feed for adapter in adapters] == ["exDailyQ", "revivt", "pvChgRslt"]
    assert [adapter.source for adapter in adapters] == [
        "tpex_exdailyq", "tpex_revivt", "tpex_pvchgrslt",
    ]
    for adapter in adapters:
        assert isinstance(adapter, CorporateActionListAdapter)
        assert adapter.dataset_code == "corporate_action"
        assert adapter.market == "TPEx"


def test_source_event_key_is_the_feed_and_the_executed_date() -> None:
    """TPEx publishes no locator, so the executed date is the identity."""
    assert only(exdailyq(), "6629", date(2024, 1, 3)).locator.source_event_key == (
        "exDailyQ:20240103"
    )
    assert only(revivt(), "3064", date(2024, 2, 5)).locator.source_event_key == (
        "revivt:20240205"
    )
    assert only(pv(), "6763", date(2024, 9, 9)).locator.source_event_key == (
        "pvChgRslt:20240909"
    )


def test_a_locator_with_several_dates_joins_them_in_order() -> None:
    """TWSE's par-value locator carries its halt and resumption dates."""
    locator = ExchangeLocator("TWTB8U", "2327", (date(2025, 8, 14), date(2025, 8, 25)))
    assert locator.source_event_key == "TWTB8U:20250814,20250825"
    with pytest.raises(ValueError):
        ExchangeLocator("TWTB8U", "2327", ())
    with pytest.raises(ValueError):
        ExchangeLocator("TWTB8U", " 2327", (date(2025, 8, 25),))


def test_an_announcement_feed_cannot_produce_an_event_key() -> None:
    """The abandoned pilot's collision, kept as a permanent fixture (§21.3).

    `(security, dividend_year, period)` names two board resolutions a year
    apart. No result-feed locator exists for an announcement, and the locator
    type refuses to be built for one.
    """
    rows = json.loads(load("tpex_mopsfin_t187ap39_o_1591_108_1.json"))
    keys = Counter((row["公司代號"], row["股利年度"], row["期別"]) for row in rows)
    assert keys == {("1591", "108", "1"): 2}
    assert sorted(row["董事會決議通過股利分派日"] for row in rows) == [
        "1080806", "1090505",
    ]
    for feed in ("mopsfin_t187ap39_O", "t187ap45_L", "TWT48U", "t05st09sub"):
        with pytest.raises(SourceDataError) as rejected:
            ExchangeLocator(feed, "1591", (date(2019, 8, 6),))
        assert rejected.value.reason_code == "announcement_feed"
    with pytest.raises(SourceDataError) as unknown:
        ExchangeLocator("SOMETHING", "1591", (date(2019, 8, 6),))
    assert unknown.value.reason_code == "unknown_feed"


def test_the_key_holds_no_revision_content() -> None:
    """Corrected terms under one date keep the key; §51.5's list stays out."""
    original = only(exdailyq(), "6629", date(2024, 1, 3))

    def correct(payload):
        row = payload["tables"][0]["data"][0]
        assert row[:2] == ["113/01/03", "6629"]
        row[2] = "泰金寶"             # company name
        row[3] = "56.00"              # close before
        row[4] = "54.00"              # reference price
        row[6] = row[7] = "2.000000"  # dividend value
        row[8] = "除權息"             # action type
        row[13] = "2.00000000"        # cash dividend
        row[14] = "50.00000000"       # free shares per thousand

    corrected = only(exdailyq(mutate(EXDAILYQ_2024, correct)), "6629", date(2024, 1, 3))
    assert corrected.locator == original.locator
    assert corrected.action_type != original.action_type
    assert corrected.fields != original.fields


def test_separate_events_have_separate_keys_and_a_file_never_repeats_one() -> None:
    parsed = exdailyq()
    keys = [(row.security_code, row.locator.source_event_key) for row in parsed.rows]
    assert len(keys) == len(set(keys)) == 1060
    # 6629 paid quarterly: four events, four keys.
    assert {
        row.locator.source_event_key
        for row in parsed.rows if row.security_code == "6629"
    } == {
        "exDailyQ:20240103", "exDailyQ:20240711",
        "exDailyQ:20240912", "exDailyQ:20241218",
    }


def test_a_repeated_event_quarantines_the_file() -> None:
    def repeat(payload):
        rows = payload["tables"][0]["data"]
        rows.append(list(rows[0]))

    with pytest.raises(SourceDataError) as error:
        exdailyq(mutate(EXDAILYQ_2024, repeat))
    assert error.value.reason_code == "ambiguous_identity"


def test_the_security_name_is_not_event_content() -> None:
    renamed = exdailyq(mutate(EXDAILYQ_2024, edit_row(0, 2, "新名稱")))
    adapter = TPExExRightDailyAdapter()
    before = adapter.observation(only(exdailyq(), "6629", date(2024, 1, 3)))
    after = adapter.observation(only(renamed, "6629", date(2024, 1, 3)))
    assert before == after


# --- executed events only ----------------------------------------------------


def test_rows_dated_after_executed_through_are_not_events_yet() -> None:
    """A current-year file lists results the exchange has not executed yet.

    Fetched 2026-09-16, revivt already lists three resumptions on 2026-09-21.
    Invariant G(2) rests on the event being executed, so those rows are counted
    and left for a later fetch, and the file claims completeness only through
    `executed_through`.
    """
    content = load("tpex_revivt_2026_fetched_20260916.json")
    dates = [row[0] for row in json.loads(content)["tables"][0]["data"]]
    assert sum(1 for value in dates if value > "1150915") == 3

    parsed = revivt(content, year(2026, executed_through=date(2026, 9, 15)))
    assert parsed.not_yet_executed == 3
    assert len(parsed.rows) == len(dates) - 3
    assert max(row.event_date for row in parsed.rows) == date(2026, 9, 14)
    assert (parsed.coverage_start, parsed.coverage_end) == (
        date(2026, 1, 1), date(2026, 9, 15),
    )


def test_a_past_range_is_complete_to_its_end() -> None:
    parsed = exdailyq()
    assert parsed.not_yet_executed == 0
    assert (parsed.coverage_start, parsed.coverage_end) == (
        date(2024, 1, 1), date(2024, 12, 31),
    )


def test_a_range_request_is_ordered() -> None:
    with pytest.raises(ValueError):
        CorporateActionRangeRequest(date(2024, 2, 1), date(2024, 1, 1), date(2026, 1, 1))


def test_a_range_with_nothing_executed_is_not_a_request() -> None:
    """Review finding: on 2027-01-01 nothing in the 2027 file has happened yet.

    Such a job would report coverage ending before it starts, and could only
    ever count rows. The issuer skips it instead; the request refuses it.
    """
    with pytest.raises(ValueError, match="executed_through"):
        CorporateActionRangeRequest(date(2027, 1, 1), date(2027, 12, 31), date(2026, 12, 31))
    first_day = CorporateActionRangeRequest(
        date(2027, 1, 1), date(2027, 12, 31), date(2027, 1, 1)
    )
    assert first_day.executed_through == first_day.start


# --- request and failure modes -------------------------------------------------


def test_the_resource_requests_the_range() -> None:
    resource = TPExExRightDailyAdapter().resource(year(2024))
    assert resource.source_uri == (
        "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ"
        "?startDate=2024%2F01%2F01&endDate=2024%2F12%2F31&response=json"
    )
    assert resource.resource_key == "tpex_exdailyq:2024-01-01:2024-12-31"


def test_an_empty_range_is_a_valid_answer_with_no_events() -> None:
    def empty(payload):
        payload["tables"][0]["data"] = []
        payload["tables"][0]["totalCount"] = 0

    parsed = exdailyq(mutate(EXDAILYQ_2024, empty))
    assert parsed.rows == ()
    assert parsed.coverage_end == date(2024, 12, 31)


def test_any_other_status_fails_closed() -> None:
    def down(payload):
        payload["stat"] = "系統維護中"

    with pytest.raises(SourceDataError) as error:
        exdailyq(mutate(EXDAILYQ_2024, down))
    assert error.value.reason_code == "source_status"


def test_the_echoed_range_must_be_the_requested_one() -> None:
    def shifted(payload):
        payload["date"] = "20240101~20241130"

    with pytest.raises(SourceDataError) as error:
        exdailyq(mutate(EXDAILYQ_2024, shifted))
    assert error.value.reason_code == "date_mismatch"

    with pytest.raises(SourceDataError) as error:
        exdailyq(mutate(EXDAILYQ_2024, edit_row(0, 0, "112/12/29")))
    assert error.value.reason_code == "date_mismatch"


def test_a_changed_header_fails_closed() -> None:
    def rename(payload):
        payload["tables"][0]["fields"][5] = "權值(元)"

    with pytest.raises(SourceDataError) as error:
        exdailyq(mutate(EXDAILYQ_2024, rename))
    assert error.value.reason_code == "schema_mismatch"


def test_an_unknown_event_type_fails_closed() -> None:
    with pytest.raises(SourceDataError) as error:
        exdailyq(mutate(EXDAILYQ_2024, edit_row(0, 8, "除權證")))
    assert error.value.reason_code == "unknown_event_type"

    with pytest.raises(SourceDataError) as error:
        revivt(mutate(REVIVT_2024, edit_row(0, 9, "分割減資")))
    assert error.value.reason_code == "unknown_event_type"


# --- exDailyQ -------------------------------------------------------------------


def test_exdailyq_is_self_contained() -> None:
    parsed = exdailyq()
    adapter = TPExExRightDailyAdapter()

    cash = adapter.observation(only(parsed, "6613", date(2024, 1, 8)))
    assert cash.action_type == "ex_dividend"
    # Eight places, which the TWD contract had capped at four.
    assert cash.cash_dividend_per_share == twd("3.42936322")
    assert cash.official_rights_dividend_value == signed("3.429363")
    assert cash.free_share_ratio is None
    assert cash.rights_ratio is None
    assert cash.source_event_type == "除息"
    # The unsourced columns stay NULL (ROADMAP §2.3).
    for name in (
        "announcement_date", "record_date", "payment_date",
        "earnings_stock_ratio", "capital_surplus_stock_ratio",
        "old_shares", "new_shares",
    ):
        assert getattr(cash, name) is None

    both = adapter.observation(only(parsed, "2916", date(2024, 5, 31)))
    assert both.action_type == "ex_right_dividend"
    assert both.cash_dividend_per_share == twd("4")
    assert both.free_share_ratio is None
    assert both.rights_ratio == Decimal("0.13729505098")
    assert both.subscription_price == twd("42")
    assert both.close_before == twd("67.90")
    assert both.official_reference_price == twd("60.51")
    assert both.source_terms == {
        "權值": "3.388683",
        "息值": "4.000000",
        "漲停價": "70.20",
        "跌停價": "54.50",
        "開始交易基準價": "63.90",
        "減除股利參考價": "63.90",
        "現金增資股數": "10000000",
        "公開承銷股數": "1000000",
        "員工認購股數": "1500000",
        "原股東認購股數": "7500000",
    }

    free = adapter.observation(only(parsed, "6712", date(2024, 5, 29)))
    assert free.free_share_ratio == Decimal("0.1")
    assert free.source_event_type == "除權息"


def test_every_exdailyq_row_in_2024_maps() -> None:
    parsed = exdailyq()
    adapter = TPExExRightDailyAdapter()
    kinds = Counter(adapter.observation(row).action_type for row in parsed.rows)
    assert sum(kinds.values()) == 1060
    assert set(kinds) == {"ex_dividend", "ex_right", "ex_right_dividend"}


def test_a_rights_issue_priced_above_the_close_publishes_a_negative_value() -> None:
    """權值+息值 is 除權息前收盤價 − 除權息參考價 (4 TWSE rows, 2 TPEx)."""
    observation = TPExExRightDailyAdapter().observation(
        only(exdailyq(), "8444", date(2024, 12, 12))
    )
    assert observation.official_rights_dividend_value == signed("-0.204602")
    assert observation.rights_ratio == Decimal("0.08002413128")
    assert observation.subscription_price == twd("33.00")


def test_terms_that_contradict_the_type_fail_closed() -> None:
    """Held on every one of 7,359 rows 2020-2026."""
    adapter = TPExExRightDailyAdapter()
    for column, value in ((13, "0.00000000"), (14, "50.00000000")):
        parsed = exdailyq(mutate(EXDAILYQ_2024, edit_row(0, column, value)))
        with pytest.raises(SourceDataError) as error:
            adapter.observation(parsed.rows[0])
        assert error.value.reason_code == "inconsistent_terms"

    rights = next(
        index for index, row in enumerate(
            json.loads(EXDAILYQ_2024)["tables"][0]["data"]
        )
        if row[1] == "8444"
    )
    parsed = exdailyq(mutate(EXDAILYQ_2024, edit_row(rights, 16, "0.00")))
    with pytest.raises(SourceDataError) as error:
        adapter.observation(only(parsed, "8444", date(2024, 12, 12)))
    assert error.value.reason_code == "inconsistent_terms"


def test_a_negative_amount_is_not_a_published_term() -> None:
    with pytest.raises(SourceDataError) as error:
        exdailyq(mutate(EXDAILYQ_2024, edit_row(0, 13, "-1.50000000")))
    assert error.value.reason_code == "invalid_numeric"


# --- revivt ---------------------------------------------------------------------


def test_revivt_reads_its_inline_detail() -> None:
    parsed = revivt()
    assert len(parsed.rows) == 10
    adapter = TPExReductionAdapter()
    offset = adapter.observation(only(parsed, "3064", date(2024, 2, 5)))
    assert offset.action_type == "capital_reduction"
    assert offset.capital_reduction_kind == "loss_offset"
    assert offset.capital_reduction_cash_return_per_share is None
    assert (offset.old_shares, offset.new_shares) == (Decimal(1000), Decimal(300))
    assert offset.ex_date == date(2024, 2, 5)
    assert offset.close_before == twd("10.65")
    assert offset.official_reference_price == twd("35.50")
    assert offset.source_event_type == "彌補虧損"
    assert offset.source_terms == {
        "漲停價格": "39.05",
        "跌停價格": "31.95",
        "開始交易基準價": "35.50",
        "除權參考價": "0.00",
        "停止買賣日期": "2024-01-25",
    }
    kinds = Counter(adapter.observation(row).capital_reduction_kind for row in parsed.rows)
    assert set(kinds) == {"loss_offset", "cash_refund"}
    for row in parsed.rows:
        observation = adapter.observation(row)
        assert (observation.capital_reduction_kind == "cash_refund") == (
            observation.capital_reduction_cash_return_per_share is not None
        )


def test_a_cash_refund_reduction() -> None:
    parsed = revivt()
    refund = next(row for row in parsed.rows if row.source_event_type == "現金減資")
    observation = TPExReductionAdapter().observation(refund)
    assert observation.capital_reduction_kind == "cash_refund"
    assert observation.capital_reduction_cash_return_per_share.value > 0
    assert observation.new_shares < observation.old_shares


def test_a_reason_the_cash_contradicts_fails_closed() -> None:
    parsed = revivt(mutate(REVIVT_2024, edit_row(0, 9, "現金減資")))
    with pytest.raises(SourceDataError) as error:
        TPExReductionAdapter().observation(parsed.rows[0])
    assert error.value.reason_code == "inconsistent_terms"


def test_revivt_detail_that_disagrees_with_its_row_fails_closed() -> None:
    def other_resumption(payload):
        row = payload["tables"][0]["data"][0]
        row[10] = row[10].replace("113/02/05", "113/02/06")

    with pytest.raises(SourceDataError) as error:
        revivt(mutate(REVIVT_2024, other_resumption))
    assert error.value.reason_code == "date_mismatch"

    def other_code(payload):
        row = payload["tables"][0]["data"][0]
        row[10] = row[10].replace("3064&nbsp", "3065&nbsp")

    with pytest.raises(SourceDataError) as error:
        revivt(mutate(REVIVT_2024, other_code))
    assert error.value.reason_code == "invalid_identity"

    def relabelled(payload):
        row = payload["tables"][0]["data"][0]
        row[10] = row[10].replace("每股退還股款:", "每股退還現金:")

    with pytest.raises(SourceDataError) as error:
        revivt(mutate(REVIVT_2024, relabelled))
    assert error.value.reason_code == "schema_mismatch"


def test_a_detail_unit_that_is_not_the_declared_one_fails_closed() -> None:
    def per_share(payload):
        row = payload["tables"][0]["data"][0]
        row[10] = row[10].replace("300.00000000&nbsp股", "300.00000000&nbsp元/股")

    with pytest.raises(SourceDataError) as error:
        revivt(mutate(REVIVT_2024, per_share))
    assert error.value.reason_code == "unit_mismatch"


def test_a_revivt_cash_increase_is_unsupported_until_its_unit_is_seen() -> None:
    """Every row 2020-2026 publishes NA; the ratio's unit has never been seen."""
    def increase(payload):
        row = payload["tables"][0]["data"][0]
        row[10] = row[10].replace(
            "<th>現金增資配股率:</th><td>NA</td>",
            "<th>現金增資配股率:</th><td>0.25000000</td>",
        )

    parsed = revivt(mutate(REVIVT_2024, increase))
    with pytest.raises(SourceDataError) as error:
        TPExReductionAdapter().observation(parsed.rows[0])
    assert error.value.reason_code == "unsupported_terms"


# --- pvChgRslt ------------------------------------------------------------------


def test_a_par_value_change_is_a_split_with_its_published_ratio() -> None:
    parsed = pv()
    assert len(parsed.rows) == 3
    observation = TPExParValueChangeAdapter().observation(
        only(parsed, "6763", date(2024, 9, 9))
    )
    assert observation.action_type == "stock_split"
    assert observation.source_event_type == "變更股票面額"
    assert (observation.old_shares, observation.new_shares) == (
        Decimal(1), Decimal(10),
    )
    assert str(observation.new_shares) == "10"
    assert observation.ex_date == date(2024, 9, 9)
    assert observation.close_before == twd("491.00")
    assert observation.official_reference_price == twd("49.10")
    assert observation.source_terms["變更前股票面額"] == "10.00"
    assert observation.source_terms["變更後股票面額"] == "1.00"
    assert observation.source_terms["停止買賣日期"] == "2024-08-29"


def test_a_par_increase_is_a_reverse_split() -> None:
    def reverse(payload):
        row = payload["tables"][0]["data"][0]
        row[8] = (
            row[8]
            .replace("<td>10.00000000</td>", "<td>0.50000000</td>")
            .replace("<th>變更前股票面額:</th><td>10.00</td>", "<th>變更前股票面額:</th><td>1.00</td>")
            .replace("<th>變更後股票面額:</th><td>1.00</td>", "<th>變更後股票面額:</th><td>2.00</td>")
        )

    observation = TPExParValueChangeAdapter().observation(
        pv(mutate(PVCHG_2024, reverse)).rows[0]
    )
    assert observation.action_type == "reverse_split"
    assert (observation.old_shares, observation.new_shares) == (
        Decimal(1), Decimal("0.5"),
    )


def test_a_ratio_the_par_values_contradict_fails_closed() -> None:
    def contradict(payload):
        row = payload["tables"][0]["data"][0]
        row[8] = row[8].replace("<td>10.00000000</td>", "<td>5.00000000</td>")

    with pytest.raises(SourceDataError) as error:
        TPExParValueChangeAdapter().observation(pv(mutate(PVCHG_2024, contradict)).rows[0])
    assert error.value.reason_code == "inconsistent_terms"


# --- the TWD and ratio precision the feeds need -------------------------------


def test_per_share_amounts_carry_eight_places_and_prices_six() -> None:
    assert TwdAmount(Decimal("3.42936322")).value == Decimal("3.42936322")
    with pytest.raises(ValueError):
        TwdAmount(Decimal("0.123456789"))
    CorporateActionObservation(
        action_type="ex_dividend", ex_date=date(2024, 1, 8),
        cash_dividend_per_share=twd("3.42936322"),
    )
    with pytest.raises(ValueError, match="close_before"):
        CorporateActionObservation(
            action_type="ex_dividend", ex_date=date(2024, 1, 8),
            close_before=twd("1.1234567"),
        )


def test_only_the_published_difference_may_be_negative() -> None:
    with pytest.raises(ValueError):
        TwdAmount(Decimal("-0.1"))
    CorporateActionObservation(
        action_type="ex_right", ex_date=date(2024, 12, 12),
        official_rights_dividend_value=signed("-0.204602"),
    )
    with pytest.raises(ValueError, match="close_before"):
        CorporateActionObservation(
            action_type="ex_right", ex_date=date(2024, 12, 12),
            close_before=signed("-1"),
        )
    with pytest.raises(ValueError):
        SignedTwdAmount(Decimal("0.123456789"))


def test_index_trade_value_keeps_its_four_place_column() -> None:
    with pytest.raises(ValueError, match="trade_value"):
        MarketIndexObservation(
            date(2024, 1, 8), Decimal(100), trade_value=twd("1.12345")
        )


def test_share_ratios_carry_twelve_places() -> None:
    CorporateActionObservation(
        action_type="rights_issue", ex_date=date(2024, 1, 10),
        rights_ratio=Decimal("0.10520361991"),
    )
    with pytest.raises(ValueError, match="rights_ratio"):
        CorporateActionObservation(
            action_type="rights_issue", ex_date=date(2024, 1, 10),
            rights_ratio=Decimal("0.1234567890123"),
        )
