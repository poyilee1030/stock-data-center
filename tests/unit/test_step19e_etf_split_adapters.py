"""Step 19-e — ETF split and reverse-split result feeds.

`TWTCAU`'s fixture is TWSE's real response for 2020-01-01 → 2026-09-11,
fetched live on 2026-09-17: 11 rows, no `詳細資料` column, no exchange-ratio
field. The two TPEx fixtures are equally real for the same window — both
`totalCount: 0` — so TPEx has never listed one of these events; the adapters
must quarantine a hypothetical row rather than guess its detail-page schema.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.ingestion.adapters import (
    TPExETFReverseSplitAdapter,
    TPExETFSplitAdapter,
    TWSEETFSplitAdapter,
)
from stock_data_center.ingestion.models import (
    CorporateActionRangeRequest,
    SourceDataError,
)
from stock_data_center.market_reference.models import TwdAmount

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def mutate(raw: bytes, edit) -> bytes:
    payload = json.loads(raw)
    edit(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


FULL_HISTORY = CorporateActionRangeRequest(
    start=date(2020, 1, 1), end=date(2026, 9, 11), executed_through=date(2026, 9, 11)
)

TWTCAU_FULL = load("twse_twtcau_2020_2026.json")
# The real fixture's row 9 (00631L, 115/03/31) already has a blank
# 分割(反分割) cell (pinned by test_twtcau_live_fixture_already_has_one_blank
# below); TWTCAU_CLEAN drops it so the other tests can assert on the ten
# unambiguous rows without every one of them tripping the same quarantine.
TWTCAU_CLEAN = mutate(
    TWTCAU_FULL, lambda payload: payload["data"].pop(8)
)
ETFSPLITRSLT_EMPTY = load("tpex_etfsplitrslt_2020_2026_empty.json")
ETFRVSRSLT_EMPTY = load("tpex_etfrvsrslt_2020_2026_empty.json")


def only(parsed, code: str, event_date: date):
    matches = [
        row for row in parsed.rows
        if row.security_code == code and row.event_date == event_date
    ]
    assert len(matches) == 1, f"{code} {event_date} appears {len(matches)} times"
    return matches[0]


def test_twtcau_is_a_result_feed_with_its_own_source() -> None:
    adapter = TWSEETFSplitAdapter()
    assert adapter.feed == "TWTCAU"
    assert adapter.source == "twse_twtcau"
    assert adapter.market == "TWSE"


def test_twtcau_full_history_has_eleven_real_rows_ten_clean() -> None:
    payload = json.loads(TWTCAU_FULL)
    assert len(payload["data"]) == 11
    parsed = TWSEETFSplitAdapter().parse(TWTCAU_CLEAN, FULL_HISTORY)
    assert len(parsed.rows) == 10
    # Identity is (security_code, source_event_key), not the key alone: two
    # different ETFs (00673R, 00706L) both resume on 114/10/22.
    keys = [(row.security_code, row.locator.source_event_key) for row in parsed.rows]
    assert len(keys) == len(set(keys))


def test_twtcau_0050_split_is_stored_as_other_with_no_share_count() -> None:
    parsed = TWSEETFSplitAdapter().parse(TWTCAU_CLEAN, FULL_HISTORY)
    row = only(parsed, "0050", date(2025, 6, 18))
    assert row.action_type == "other"
    assert row.source_event_type == "分割"
    assert row.detail_request is None
    observation = TWSEETFSplitAdapter().observation(row)
    assert observation.action_type == "other"
    assert observation.old_shares is None
    assert observation.new_shares is None
    assert observation.close_before == TwdAmount(Decimal("188.65"))
    assert observation.official_reference_price == TwdAmount(Decimal("47.16"))


def test_twtcau_reverse_split_keeps_its_direction_label() -> None:
    parsed = TWSEETFSplitAdapter().parse(TWTCAU_CLEAN, FULL_HISTORY)
    row = only(parsed, "00632R", date(2024, 12, 11))
    assert row.source_event_type == "反分割"
    assert row.action_type == "other"


def test_twtcau_locator_keys_on_the_executed_date_alone() -> None:
    parsed = TWSEETFSplitAdapter().parse(TWTCAU_CLEAN, FULL_HISTORY)
    row = only(parsed, "0050", date(2025, 6, 18))
    assert row.locator.source_event_key == "TWTCAU:20250618"


def test_twtcau_blank_direction_quarantines_rather_than_guessing() -> None:
    # The real, unmodified fixture's row 9 (00631L, 115/03/31) already has a
    # blank 分割(反分割) cell — no mutation needed to trigger this.
    with pytest.raises(SourceDataError) as error:
        TWSEETFSplitAdapter().parse(TWTCAU_FULL, FULL_HISTORY)
    assert error.value.reason_code == "unknown_event_type"


def test_twtcau_live_fixture_already_has_one_blank_direction_row() -> None:
    payload = json.loads(TWTCAU_FULL)
    directions = [row[3] for row in payload["data"]]
    assert directions.count("") == 1


@pytest.mark.parametrize(
    ("adapter_cls", "content", "feed"),
    [
        (TPExETFSplitAdapter, ETFSPLITRSLT_EMPTY, "etfSplitRslt"),
        (TPExETFReverseSplitAdapter, ETFRVSRSLT_EMPTY, "etfRvsRslt"),
    ],
    ids=["etfSplitRslt", "etfRvsRslt"],
)
def test_tpex_etf_feeds_have_never_listed_a_real_row(adapter_cls, content, feed) -> None:
    adapter = adapter_cls()
    assert adapter.feed == feed
    parsed = adapter.parse(content, FULL_HISTORY)
    assert parsed.rows == ()


@pytest.mark.parametrize("adapter_cls", [TPExETFSplitAdapter, TPExETFReverseSplitAdapter])
def test_tpex_etf_feeds_quarantine_a_row_rather_than_guess_its_schema(adapter_cls) -> None:
    with_a_row = mutate(
        ETFSPLITRSLT_EMPTY,
        lambda payload: payload["tables"][0].update(
            totalCount=1,
            data=[["1130618", "0050", "元大台灣50", "188.65", "47.16", "51.85", "42.45", "47.16", "<detail/>"]],
        ),
    )
    with pytest.raises(SourceDataError) as error:
        adapter_cls().parse(with_a_row, FULL_HISTORY)
    assert error.value.reason_code == "unverified_schema"
