"""Step 27-a: which row a PIT context sees, for every observed table.

Market PIT (CLAUDE.md §15): a row is eligible when its `available_at` is not
after `information_as_of` and its `recorded_at` is not after `knowledge_as_of`,
and a key's answer is its latest eligible row. System PIT (§16): only
`recorded_at <= system_as_of`. `available_at` is computed, never supplied: a
key's first row from its dataset's release rule, or from its stored
`published_at` where publication differs per issuer; every later row from its
own `recorded_at` (`docs/pit_semantics.md`).

Each test stages rows with explicit `recorded_at`, as the trusted migration
path did (§23): the database stamps it otherwise.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from stock_data_center.db.schema_v2 import (
    corporate_actions,
    daily_prices,
    financial_report_facts,
    financial_reports,
    index_prices,
    institutional_market_flows,
    monthly_revenues,
    shareholding_distributions,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import exchange_daily as xd
from stock_data_center.v2 import visibility as vis
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch
from stock_data_center.v2.release_rules import (
    corporate_action_available_from,
    tdcc_available_from,
)

pytestmark = pytest.mark.integration

DAY = date(2024, 7, 1)  # a Monday
NOW = datetime(2030, 1, 1, tzinfo=UTC)
LATEST = vis.MarketPIT(information_as_of=NOW, knowledge_as_of=NOW)


@pytest.fixture
def fetch_id(db, tmp_path):
    fetch = record_fetch(
        db,
        FetchRecord("daily_price", "twse_mi_index", "t", None, "gap_fill", "t", "abc",
                    datetime(2024, 1, 1, tzinfo=UTC)),
        content=b"t", status="succeeded", store=LocalRawArtifactStore(tmp_path),
    )
    for stock_id in ("2330", "2317"):
        db.execute(sa.text("INSERT INTO stocks (stock_id, name, market, fetch_id) "
                           "VALUES (:s, :n, 'sii', :f) ON CONFLICT DO NOTHING"),
                   {"s": stock_id, "n": stock_id, "f": fetch})
    return fetch


def _price(db, fetch_id, close, recorded_at, *, day=DAY, stock_id="2330",
           source="twse_mi_index"):
    db.execute(sa.insert(daily_prices).values(
        stock_id=stock_id, source=source, trade_date=day, close_price=Decimal(close),
        volume=1, fetch_id=fetch_id, recorded_at=recorded_at))


def _closes(db, pit, **kwargs) -> dict[tuple[str, date], Decimal]:
    rows = vis.rows(db, "daily_prices", pit, start=kwargs.pop("start", DAY),
                    end=kwargs.pop("end", DAY), **kwargs)
    return {(r["stock_id"], r["trade_date"]): r["close_price"] for r in rows}


RELEASED = xd.available_from(DAY)  # 2024-07-02 03:00 Asia/Taipei


# ---------------------------------------------------------------- contexts


def test_a_pit_context_needs_timezone_aware_instants() -> None:
    # docs/pit_semantics.md: an instant without an offset is invalid.
    with pytest.raises(ValueError, match="timezone"):
        vis.MarketPIT(information_as_of=datetime(2024, 1, 1), knowledge_as_of=NOW)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone"):
        vis.SystemPIT(system_as_of=datetime(2024, 1, 1))  # noqa: DTZ001


def test_every_observed_table_has_one_visibility_family() -> None:
    # §19: visibility is computed in one place per dataset family.
    assert set(vis.FAMILIES) == {
        "daily_prices", "index_prices", "valuations", "institutional_flows",
        "institutional_market_flows", "foreign_holdings", "margin_trading",
        "securities_lending", "monthly_revenues", "financial_reports",
        "shareholding_distributions", "corporate_actions",
    }


# ---------------------------------------------------------------- rule-based


def test_a_value_is_invisible_before_its_rule_instant(db, fetch_id) -> None:
    _price(db, fetch_id, "100", RELEASED + timedelta(hours=1))
    before = vis.MarketPIT(RELEASED - timedelta(seconds=1), NOW)
    assert _closes(db, before) == {}
    assert _closes(db, vis.MarketPIT(RELEASED, NOW)) == {("2330", DAY): Decimal(100)}


def test_a_provisional_row_is_superseded_by_the_settled_one_at_the_instant(db, fetch_id) -> None:
    _price(db, fetch_id, "99", RELEASED - timedelta(hours=10))  # seen before it settled
    _price(db, fetch_id, "100", RELEASED + timedelta(days=3))   # the settled value, late
    assert _closes(db, vis.MarketPIT(RELEASED, NOW)) == {("2330", DAY): Decimal(100)}


def test_the_knowledge_cutoff_keeps_rows_recorded_after_it_out(db, fetch_id) -> None:
    # "What had the Data Center recorded by then": a backfilled value was public
    # at the rule instant but unknown to the Data Center until it was recorded.
    recorded = datetime(2026, 9, 19, tzinfo=UTC)
    _price(db, fetch_id, "100", recorded)
    assert _closes(db, vis.MarketPIT(RELEASED, recorded - timedelta(seconds=1))) == {}
    assert _closes(db, vis.MarketPIT(RELEASED, recorded)) == {("2330", DAY): Decimal(100)}


def test_a_correction_is_visible_from_its_own_recorded_at(db, fetch_id) -> None:
    _price(db, fetch_id, "100", RELEASED)
    corrected = RELEASED + timedelta(days=30)
    _price(db, fetch_id, "101", corrected)
    at = vis.MarketPIT(corrected - timedelta(seconds=1), NOW)
    assert _closes(db, at) == {("2330", DAY): Decimal(100)}
    assert _closes(db, vis.MarketPIT(corrected, NOW)) == {("2330", DAY): Decimal(101)}
    # Reproducing a run before the correction was recorded.
    assert _closes(db, vis.MarketPIT(NOW, corrected - timedelta(seconds=1))) == {
        ("2330", DAY): Decimal(100)}


def test_system_pit_sees_what_was_recorded_whatever_its_publication(db, fetch_id) -> None:
    early = RELEASED - timedelta(hours=10)
    _price(db, fetch_id, "99", early)
    assert _closes(db, vis.SystemPIT(early)) == {("2330", DAY): Decimal(99)}
    assert _closes(db, vis.SystemPIT(early - timedelta(seconds=1))) == {}


def test_filters_by_stock_source_and_date(db, fetch_id) -> None:
    for day in (DAY, DAY + timedelta(days=1)):
        _price(db, fetch_id, "1", RELEASED, day=day)
        _price(db, fetch_id, "2", RELEASED, day=day, stock_id="2317")
    _price(db, fetch_id, "3", RELEASED, source="tpex_otc_quotes")
    assert set(_closes(db, LATEST, start=DAY, end=DAY + timedelta(days=1),
                       stock_ids=["2330"], sources=["twse_mi_index"])) == {
        ("2330", DAY), ("2330", DAY + timedelta(days=1))}
    rows = vis.rows(db, "daily_prices", LATEST, start=DAY, end=DAY)
    assert sorted((r["stock_id"], r["source"]) for r in rows) == [
        ("2317", "twse_mi_index"), ("2330", "tpex_otc_quotes"), ("2330", "twse_mi_index")]


def test_a_row_carries_when_it_became_available_and_its_provenance(db, fetch_id) -> None:
    _price(db, fetch_id, "100", RELEASED + timedelta(hours=1))
    [row] = vis.rows(db, "daily_prices", LATEST, start=DAY, end=DAY)
    assert (row["available_at"], row["recorded_at"], row["fetch_id"]) == (
        RELEASED, RELEASED + timedelta(hours=1), fetch_id)


def test_a_table_without_a_stock_is_keyed_by_its_own_columns(db, fetch_id) -> None:
    for index_name, close in (("TAIEX", "20000"), ("Electronics", "1000")):
        db.execute(sa.insert(index_prices).values(
            source="twse_mi_index", index_name=index_name, trade_date=DAY,
            close_value=Decimal(close), fetch_id=fetch_id, recorded_at=RELEASED))
    db.execute(sa.insert(institutional_market_flows).values(
        source="twse_bfi82u", trade_date=DAY, institution="foreign", net=5,
        fetch_id=fetch_id, recorded_at=RELEASED))
    indices = vis.rows(db, "index_prices", LATEST, start=DAY, end=DAY)
    assert sorted(r["index_name"] for r in indices) == ["Electronics", "TAIEX"]
    assert [r["net"] for r in vis.rows(db, "institutional_market_flows", LATEST,
                                       start=DAY, end=DAY)] == [5]
    with pytest.raises(ValueError, match="stock"):
        vis.rows(db, "index_prices", LATEST, start=DAY, end=DAY, stock_ids=["2330"])


# ---------------------------------------------------------------- TDCC


def test_tdcc_uses_its_own_rule_not_the_exchange_one(db, fetch_id) -> None:
    # §29: one source's rule never authorizes another's. A Friday snapshot is
    # public the Sunday after at noon, not at 03:00 the next day.
    friday = date(2024, 7, 5)
    released = tdcc_available_from(friday)
    db.execute(sa.insert(shareholding_distributions).values(
        stock_id="2330", source="tdcc_opendata", snapshot_date=friday, total_holders=9,
        fetch_id=fetch_id, recorded_at=datetime(2024, 7, 5, 12, tzinfo=UTC)))

    def seen(at):
        return [r["total_holders"] for r in vis.rows(
            db, "shareholding_distributions", vis.MarketPIT(at, NOW), start=friday, end=friday)]

    assert seen(xd.available_from(friday)) == []
    assert seen(released - timedelta(seconds=1)) == []
    assert seen(released) == [9]


# ---------------------------------------------------------------- corporate actions


def _action(db, fetch_id, ex_date, recorded_at, *, retracted=False, reference="90"):
    db.execute(sa.insert(corporate_actions).values(
        stock_id="2330", source="tpex_exdailyq", ex_date=ex_date, event_type="除息",
        reference_price=Decimal(reference), retracted=retracted, fetch_id=fetch_id,
        recorded_at=recorded_at))


def test_a_corporate_action_is_public_from_its_ex_date(db, fetch_id) -> None:
    ex = date(2024, 7, 10)
    _action(db, fetch_id, ex, datetime(2024, 6, 20, tzinfo=UTC))
    released = corporate_action_available_from(ex)
    rows = vis.rows(db, "corporate_actions", vis.MarketPIT(released - timedelta(seconds=1), NOW),
                    start=ex, end=ex)
    assert rows == []
    assert len(vis.rows(db, "corporate_actions", vis.MarketPIT(released, NOW),
                        start=ex, end=ex)) == 1


def test_a_retracted_corporate_action_is_gone_from_its_retraction_on(db, fetch_id) -> None:
    # CLAUDE.md §51.5: a row the feed drops is retracted by a new row, never
    # deleted. The event was first recorded after its ex-date, so it is the
    # settled value and the retraction a correction, from its own recorded_at.
    ex = date(2024, 7, 10)
    _action(db, fetch_id, ex, datetime(2024, 7, 11, tzinfo=UTC))
    retracted_at = datetime(2024, 8, 1, tzinfo=UTC)
    _action(db, fetch_id, ex, retracted_at, retracted=True)

    def seen(pit):
        return len(vis.rows(db, "corporate_actions", pit, start=ex, end=ex))

    assert seen(vis.MarketPIT(retracted_at - timedelta(seconds=1), NOW)) == 1
    assert seen(vis.MarketPIT(retracted_at, NOW)) == 0
    assert seen(vis.SystemPIT(retracted_at)) == 0


def test_a_retraction_first_seen_after_the_ex_date_settles_the_event_as_gone(db, fetch_id) -> None:
    # The rule's own consequence: the event was listed before its ex-date
    # (provisional) and the first file seen after the instant no longer had it,
    # so the settled state at the ex-date is "no event", from the ex-date on.
    ex = date(2024, 7, 10)
    _action(db, fetch_id, ex, datetime(2024, 6, 20, tzinfo=UTC))
    _action(db, fetch_id, ex, datetime(2024, 8, 1, tzinfo=UTC), retracted=True)
    at = vis.MarketPIT(corporate_action_available_from(ex), NOW)
    assert vis.rows(db, "corporate_actions", at, start=ex, end=ex) == []
    before = vis.MarketPIT(corporate_action_available_from(ex), datetime(2024, 7, 31, tzinfo=UTC))
    assert len(vis.rows(db, "corporate_actions", before, start=ex, end=ex)) == 1


# ---------------------------------------------------------------- published_at


def _revenue(db, fetch_id, revenue, recorded_at, published_at=None, month=date(2024, 6, 1)):
    db.execute(sa.insert(monthly_revenues).values(
        stock_id="2330", source="mops_t21sc03_sii", revenue_month=month, revenue=revenue,
        published_at=published_at, fetch_id=fetch_id, recorded_at=recorded_at))


def _revenues(db, pit) -> list[int]:
    return [r["revenue"] for r in vis.rows(db, "monthly_revenues", pit,
                                           start=date(2024, 6, 1), end=date(2024, 6, 1))]


def test_an_unknown_publication_is_market_invisible_but_system_visible(db, fetch_id) -> None:
    # §31: nothing proves when it became public.
    recorded = datetime(2026, 9, 19, tzinfo=UTC)
    _revenue(db, fetch_id, 100, recorded, published_at=None)
    assert _revenues(db, LATEST) == []
    assert _revenues(db, vis.SystemPIT(recorded)) == [100]


def test_a_backfilled_publication_time_is_honored(db, fetch_id) -> None:
    # §32: a proven historical publication time is preserved, though the row
    # was recorded long after.
    published = datetime(2024, 7, 10, 9, tzinfo=UTC)
    recorded = datetime(2026, 9, 19, tzinfo=UTC)
    _revenue(db, fetch_id, 100, recorded, published_at=published)
    assert _revenues(db, vis.MarketPIT(published - timedelta(seconds=1), NOW)) == []
    assert _revenues(db, vis.MarketPIT(published, NOW)) == [100]
    assert _revenues(db, vis.MarketPIT(published, recorded - timedelta(seconds=1))) == []


def test_a_revenue_correction_is_visible_from_its_recorded_at(db, fetch_id) -> None:
    published = datetime(2024, 7, 10, 9, tzinfo=UTC)
    _revenue(db, fetch_id, 100, published, published_at=published)
    corrected = datetime(2024, 8, 3, tzinfo=UTC)
    _revenue(db, fetch_id, 105, corrected)  # a later row stores no published_at
    assert _revenues(db, vis.MarketPIT(corrected - timedelta(seconds=1), NOW)) == [100]
    assert _revenues(db, vis.MarketPIT(corrected, NOW)) == [105]


def _report(db, fetch_id, eps, recorded_at, published_at=None):
    report_id = db.execute(sa.insert(financial_reports).values(
        stock_id="2330", report_year=2024, report_quarter=1, report_category="consolidated",
        published_at=published_at, fetch_id=fetch_id, recorded_at=recorded_at,
    ).returning(financial_reports.c.id)).scalar_one()
    db.execute(sa.insert(financial_report_facts).values(
        report_id=report_id, statement="income_statement", account_code="9750",
        concept="{urn:t}Eps", period_start=date(2024, 1, 1), period_end=date(2024, 3, 31),
        unit="iso4217:TWD/xbrli:shares", value=Decimal(eps)))
    return report_id


def test_a_report_version_is_seen_with_exactly_its_own_facts(db, fetch_id) -> None:
    # §20: a report and all its facts are one version.
    published = datetime(2024, 5, 15, 15, tzinfo=UTC)
    first = _report(db, fetch_id, "8.70", published, published_at=published)
    restated = datetime(2024, 9, 1, tzinfo=UTC)
    second = _report(db, fetch_id, "8.75", restated)

    def seen(pit):
        reports = vis.rows(db, "financial_reports", pit, start=date(2024, 3, 31),
                           end=date(2024, 3, 31))
        facts = vis.report_facts(db, [r["id"] for r in reports])
        return [(r["id"], [f["value"] for f in facts[r["id"]]]) for r in reports]

    assert seen(vis.MarketPIT(published - timedelta(seconds=1), NOW)) == []
    assert seen(vis.MarketPIT(published, NOW)) == [(first, [Decimal("8.70")])]
    assert seen(vis.MarketPIT(restated, NOW)) == [(second, [Decimal("8.75")])]
    assert seen(vis.SystemPIT(published)) == [(first, [Decimal("8.70")])]


def test_a_report_is_filtered_by_its_period_end(db, fetch_id) -> None:
    published = datetime(2024, 5, 15, 15, tzinfo=UTC)
    _report(db, fetch_id, "8.70", published, published_at=published)
    assert vis.rows(db, "financial_reports", LATEST, start=date(2024, 4, 1),
                    end=date(2024, 12, 31)) == []
    assert len(vis.rows(db, "financial_reports", LATEST, start=date(2024, 1, 1),
                        end=date(2024, 3, 31))) == 1


def test_a_table_without_a_source_rejects_a_source_filter(db) -> None:
    with pytest.raises(ValueError, match="no source filter"):
        vis.rows(db, "financial_reports", LATEST, start=date(2024, 1, 1),
                 end=date(2024, 3, 31), sources=["mops"])


# ---------------------------------------------------------------- one place


def test_exchange_daily_visible_is_the_same_rule(db, fetch_id) -> None:
    # The older entry point answers "public by as_of, with everything recorded".
    _price(db, fetch_id, "99", RELEASED - timedelta(hours=10))
    _price(db, fetch_id, "100", RELEASED + timedelta(days=3))
    for at in (RELEASED - timedelta(seconds=1), RELEASED, RELEASED + timedelta(days=5)):
        old = xd.visible(db, daily_prices, as_of=at, start=DAY, end=DAY)
        new = vis.rows(db, "daily_prices", vis.MarketPIT(at, NOW), start=DAY, end=DAY)
        assert [dict(r) for r in old] == [dict(r) for r in new]
