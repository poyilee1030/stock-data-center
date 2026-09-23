"""Step 35-b-1: the v2 exchange-daily jobs, their row mapping and release rule.

The column contract is taken from `schema_v2`, not from the mapping itself: a
job must fill exactly its table's value columns, so a column added to a table
and forgotten by its job fails here rather than being stored NULL forever.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.v2 import exchange_daily as xd
from stock_data_center.v2.indices import KEPT_INDICES

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DAY = date(2026, 9, 11)

# (job key, fixture) for every one of the 17 jobs ROADMAP Step 35-b-1 wires.
FIXTURE_OF = {
    "daily_prices/twse_mi_index": "twse_mi_index_allbut0999_20260911.json",
    "daily_prices/tpex_otc_quotes": "tpex_otc_quotes_20260911.json",
    "valuations/twse_bwibbu_d": "twse_bwibbu_d_20260911.json",
    "valuations/tpex_pe_qry_date": "tpex_peqrydate_20260911.json",
    "institutional_flows/twse_t86": "twse_t86_20260911.json",
    "institutional_flows/tpex_insti_daily_trade": "tpex_insti_daily_trade_20260911.json",
    "institutional_market_flows/twse_bfi82u": "twse_bfi82u_20260911.json",
    "institutional_market_flows/tpex_insti_summary": "tpex_insti_summary_20260911.json",
    "foreign_holdings/twse_mi_qfiis": "twse_mi_qfiis_20260911.json",
    "foreign_holdings/mops_t13sa150_otc": "mops_t13sa150_otc_20260911.html",
    "margin_trading/twse_mi_margn": "twse_mi_margn_20260911.json",
    "margin_trading/tpex_margin_balance": "tpex_margin_balance_20260911.json",
    "securities_lending/twse_twt93u": "twse_twt93u_20260911.json",
    "securities_lending/tpex_margin_sbl": "tpex_margin_sbl_20260911.json",
    "index_prices/twse_mi_index": "twse_mi_index_allbut0999_20260911.json",
    "index_prices/tpex_index_summary": "tpex_index_summary_20260911.json",
    "index_prices/twse_mi_5mins_hist": "twse_mi_5mins_hist_202601.json",
}


def _rows(key: str) -> list[dict]:
    job = xd.JOBS[key]
    period = date(2026, 1, 1) if job.monthly else DAY
    content = (FIXTURES / FIXTURE_OF[key]).read_bytes()
    return job.rows(job.adapter.parse(content, job.request(period)))


def _by_stock(key: str, stock_id: str) -> dict:
    (row,) = [row for row in _rows(key) if row["stock_id"] == stock_id]
    return row


def test_every_roadmap_source_is_wired_and_nothing_else() -> None:
    assert set(xd.JOBS) == set(FIXTURE_OF)
    kept = {job.source for job in xd.JOBS.values()}
    assert not kept & {"twse", "tpex", "tpex_insti_qfii"}  # ADR-0027: not v2 sources


@pytest.mark.parametrize("key", sorted(FIXTURE_OF))
def test_a_job_fills_exactly_its_tables_columns(key: str) -> None:
    job = xd.JOBS[key]
    expected = {c.name for c in job.table.columns} - {"recorded_at", "fetch_id"}
    rows = _rows(key)
    assert rows, key
    assert {frozenset(row) for row in rows} == {frozenset(expected)}
    assert {row["source"] for row in rows} == {job.source}
    keys = [tuple(row[c] for c in job.key_columns) for row in rows]
    assert len(keys) == len(set(keys)), "one row per key within a file"


def test_integer_columns_are_ints_and_decimals_keep_their_published_digits() -> None:
    row = _by_stock("daily_prices/twse_mi_index", "2330")
    # Values as migrated to stockdc_backfill from v1 for 2026-09-11.
    assert row["close_price"] == Decimal(2410)
    assert row["price_change"] == Decimal(-40)
    assert (row["volume"], row["trade_value"], row["trade_count"]) == (
        21131357, 51050141409, 164402
    )
    assert type(row["volume"]) is int and type(row["last_bid_volume"]) is int
    assert (row["last_bid_volume"], row["last_ask_volume"]) == (1078, 91)


def test_otc_prices_and_lot_volumes_are_normalised_to_shares() -> None:
    row = _by_stock("daily_prices/tpex_otc_quotes", "6488")
    assert (row["close_price"], row["volume"], row["last_bid_volume"]) == (
        Decimal(902), 4372000, 25000
    )


def test_renamed_columns_map_to_their_v1_meaning() -> None:
    margin = _by_stock("margin_trading/twse_mi_margn", "2330")
    assert (margin["margin_limit"], margin["short_limit"]) == (6483092000, 6483092000)
    lending = _by_stock("securities_lending/twse_twt93u", "2330")
    assert (lending["sold"], lending["balance"], lending["next_available_limit"]) == (
        61000, 16244514, 6756118
    )


def test_every_domain_matches_the_migrated_values_for_one_stock() -> None:
    assert _by_stock("valuations/tpex_pe_qry_date", "6488")["pe_ratio"] == Decimal("43.79")
    assert _by_stock("institutional_flows/twse_t86", "2330")["total_net"] == -8832445
    held = _by_stock("foreign_holdings/mops_t13sa150_otc", "6488")
    assert (held["held_shares"], held["held_ratio"]) == (131239769, Decimal("27.44"))
    assert _by_stock("securities_lending/tpex_margin_sbl", "6488")["next_limit"] == 119528431


def test_market_flows_are_keyed_by_institution() -> None:
    rows = {row["institution"]: row for row in _rows("institutional_market_flows/twse_bfi82u")}
    assert rows["合計"]["net"] == -111258520070
    assert len(rows) == 6


def test_only_the_kept_indices_are_mapped() -> None:
    twse = _rows("index_prices/twse_mi_index")
    tpex = _rows("index_prices/tpex_index_summary")
    # 2026-09-11 in stockdc_backfill: 73 TWSE and 46 TPEx indices survive the list.
    assert len(twse) == 73 and len(tpex) == 46
    assert {row["index_name"] for row in twse} <= KEPT_INDICES["twse_mi_index"]
    assert {row["index_name"] for row in tpex} <= KEPT_INDICES["tpex_index_summary"]


def test_the_taiex_month_maps_one_row_per_trade_date() -> None:
    rows = _rows("index_prices/twse_mi_5mins_hist")
    first = min(rows, key=lambda row: row["trade_date"])
    assert first["index_name"] == "指數:發行量加權股價指數"
    assert first["trade_date"] == date(2026, 1, 2)
    assert (first["open_value"], first["close_value"]) == (
        Decimal("29016.68"), Decimal("29349.81")
    )
    assert first["change_points"] is None


def test_a_share_count_with_a_fraction_is_refused_not_truncated() -> None:
    with pytest.raises(ValueError, match="whole"):
        xd.whole(Decimal("1.5"))


def test_the_release_rule_is_the_next_day_at_three_taipei_time() -> None:
    assert (xd.RELEASE_RULE.rule_id, xd.RELEASE_RULE.version) == ("exchange_daily_settled", 1)
    # 2026-09-12 03:00 Asia/Taipei is 2026-09-11 19:00 UTC; a Friday resolves on
    # Saturday because the rule has no business-day shift.
    assert xd.available_from(DAY) == datetime(2026, 9, 11, 19, 0, tzinfo=UTC)
    assert all(job.release_rule is xd.RELEASE_RULE for job in xd.JOBS.values())
