"""Step 35-c-1: the v2 tables for monthly revenue, financials, TDCC and corporate
actions (ADR-0027 "35-c 定案").

The column contract is written out here from the ADR, not read back from
`schema_v2`, so a column the table grows or loses without an ADR change fails.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from stock_data_center.v2 import release_rules as rr
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

LEVELS = range(1, 16)
COLUMNS = {
    "monthly_revenues": [
        "stock_id", "source", "revenue_month", "recorded_at", "revenue",
        "revenue_last_month", "revenue_last_year_month", "cumulative_revenue",
        "cumulative_revenue_last_year", "mom_pct", "yoy_pct", "cumulative_yoy_pct",
        "note", "published_at", "fetch_id",
    ],
    "financial_reports": [
        "id", "stock_id", "report_year", "report_quarter", "report_category",
        "published_at", "recorded_at", "fetch_id",
    ],
    "financial_report_facts": [
        "report_id", "statement", "account_code", "concept", "period_start",
        "period_end", "unit", "value",
    ],
    "shareholding_distributions": [
        "stock_id", "source", "snapshot_date", "recorded_at",
        *(f"{kind}_{level}" for level in LEVELS for kind in ("holders", "shares", "percent")),
        "adjustment_shares", "adjustment_percent",
        "total_holders", "total_shares", "total_percent", "fetch_id",
    ],
    "corporate_actions": [
        "stock_id", "source", "ex_date", "recorded_at", "event_type", "close_before",
        "reference_price", "rights_dividend_value", "cash_dividend_per_share",
        "free_share_ratio", "rights_ratio", "subscription_price", "old_shares",
        "new_shares", "cash_return_per_share", "retracted", "fetch_id",
    ],
}


@pytest.fixture
def fetch_id(db: Connection, tmp_path):
    from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore

    fetch = record_fetch(
        db,
        FetchRecord("test", "test", "t", None, "gap_fill", "t", "abc", datetime.now(UTC)),
        content=b"raw", status="succeeded", store=LocalRawArtifactStore(tmp_path),
    )
    db.execute(sa.text(
        "INSERT INTO stocks (stock_id, name, market, fetch_id) VALUES ('2330', '台積電', 'sii', :f) "
        "ON CONFLICT DO NOTHING"), {"f": fetch})
    return fetch


def _insert(db: Connection, table: str, **values) -> None:
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    db.execute(sa.text(f"INSERT INTO {table} ({columns}) VALUES ({params})"), values)


def _revenue(db, fetch_id, **values) -> None:
    _insert(db, "monthly_revenues", **{
        "stock_id": "2330", "source": "mops_t21sc03_sii", "revenue_month": date(2026, 8, 1),
        "revenue": 335_772_000_000, "fetch_id": fetch_id, **values})


def _report(db, fetch_id, **values) -> int:
    values = {"stock_id": "2330", "report_year": 2026, "report_quarter": 2,
              "report_category": "consolidated", "fetch_id": fetch_id, **values}
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    return db.execute(sa.text(
        f"INSERT INTO financial_reports ({columns}) VALUES ({params}) RETURNING id"), values
    ).scalar_one()


def _fact(db, report_id, **values) -> None:
    _insert(db, "financial_report_facts", **{
        "report_id": report_id, "statement": "balance_sheet", "account_code": "1100",
        "concept": "{http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full}CashAndCashEquivalents",
        "period_start": None, "period_end": date(2026, 6, 30), "unit": "iso4217:TWD",
        "value": Decimal(2_550_000_000_000), **values})


def _distribution(db, fetch_id, **values) -> None:
    _insert(db, "shareholding_distributions", **{
        "stock_id": "2330", "source": "tdcc_opendata", "snapshot_date": date(2026, 9, 18),
        "total_holders": 1_900_000, "total_shares": 25_930_380_458,
        "total_percent": Decimal("100.00"), "fetch_id": fetch_id, **values})


def _action(db, fetch_id, **values) -> None:
    _insert(db, "corporate_actions", **{
        "stock_id": "2330", "source": "twse_twt49u", "ex_date": date(2026, 9, 16),
        "event_type": "息", "close_before": Decimal(1250), "reference_price": Decimal(1244),
        "fetch_id": fetch_id, **values})


@pytest.mark.parametrize("table", sorted(COLUMNS))
def test_each_table_has_exactly_the_adr_columns(db: Connection, table: str) -> None:
    actual = db.scalars(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t "
        "ORDER BY ordinal_position"), {"t": table}).all()
    assert actual == COLUMNS[table]


WRITERS = {
    "monthly_revenues": lambda db, f: _revenue(db, f),
    "financial_reports": lambda db, f: _report(db, f),
    "financial_report_facts": lambda db, f: _fact(db, _report(db, f)),
    "shareholding_distributions": lambda db, f: _distribution(db, f),
    "corporate_actions": lambda db, f: _action(db, f),
}


@pytest.mark.parametrize("table", sorted(WRITERS))
@pytest.mark.parametrize("statement", ["UPDATE {t} SET {c} = {c}", "DELETE FROM {t}"])
def test_every_table_is_append_only(db: Connection, fetch_id, table: str, statement: str) -> None:
    WRITERS[table](db, fetch_id)
    column = COLUMNS[table][1]
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(sa.text(statement.format(t=table, c=column)))


def test_a_revenue_month_is_the_first_of_the_month(db, fetch_id) -> None:
    with pytest.raises(IntegrityError), db.begin_nested():
        _revenue(db, fetch_id, revenue_month=date(2026, 8, 2))


def test_a_percentage_with_a_third_decimal_is_refused(db, fetch_id) -> None:
    _revenue(db, fetch_id, yoy_pct=Decimal("33.84"))
    with pytest.raises(IntegrityError), db.begin_nested():
        _revenue(db, fetch_id, mom_pct=Decimal("1.005"))


def test_a_correction_is_a_second_row(db, fetch_id) -> None:
    _revenue(db, fetch_id, published_at=datetime(2026, 9, 9, 8, 0, tzinfo=UTC))
    _revenue(db, fetch_id, revenue=335_772_000_001)
    rows = db.execute(sa.text(
        "SELECT revenue, published_at FROM monthly_revenues ORDER BY recorded_at")).all()
    assert [r.revenue for r in rows] == [335_772_000_000, 335_772_000_001]
    assert rows[1].published_at is None


def test_an_instant_fact_is_one_row_per_report_concept_and_date(db, fetch_id) -> None:
    report = _report(db, fetch_id)
    _fact(db, report)
    # The same cash is a cash-flow row too: another statement, another row.
    _fact(db, report, statement="cash_flow", account_code="E00210")
    with pytest.raises(IntegrityError), db.begin_nested():
        _fact(db, report)  # NULL period_start must not make it distinct


def test_a_report_has_one_category_quarter_and_known_statements(db, fetch_id) -> None:
    for values in ({"report_quarter": 5}, {"report_category": "both"}):
        with pytest.raises(IntegrityError), db.begin_nested():
            _report(db, fetch_id, **values)
    report = _report(db, fetch_id)
    with pytest.raises(IntegrityError), db.begin_nested():
        _fact(db, report, statement="equity")
    with pytest.raises(IntegrityError), db.begin_nested():
        _fact(db, report, period_start=date(2026, 7, 1))  # starts after it ends


def test_a_total_above_100_percent_is_kept_but_not_above_999(db, fetch_id) -> None:
    _distribution(db, fetch_id, total_percent=Decimal("135.00"),
                  adjustment_shares=-28_164, adjustment_percent=Decimal("-35.90"))
    with pytest.raises(IntegrityError), db.begin_nested():
        _distribution(db, fetch_id, snapshot_date=date(2026, 9, 11),
                      total_percent=Decimal("1000.00"))


def test_a_corporate_action_is_not_retracted_by_default(db, fetch_id) -> None:
    _action(db, fetch_id, rights_dividend_value=Decimal("-0.616165"),
            free_share_ratio=Decimal("0.20211906001"))
    assert db.scalar(sa.text("SELECT retracted FROM corporate_actions")) is False


def test_the_tdcc_rule_is_the_sunday_noon_after_the_snapshot(db) -> None:
    # 2026-09-18 is a Friday; the Sunday after is 09-20, 12:00 Taipei = 04:00 UTC.
    assert rr.tdcc_available_from(date(2026, 9, 18)) == datetime(2026, 9, 20, 4, tzinfo=UTC)
    # A Sunday snapshot waits a whole week: the rule is strictly after.
    assert rr.tdcc_available_from(date(2026, 9, 20)) == datetime(2026, 9, 27, 4, tzinfo=UTC)
    for day in (date(2019, 6, 28), date(2023, 10, 20), date(2026, 9, 20), date(2026, 9, 17)):
        assert db.scalar(sa.select(rr.tdcc_available_from_sql(sa.literal(day)))) == (
            rr.tdcc_available_from(day)
        )


def test_a_corporate_action_is_public_from_midnight_of_its_ex_date(db) -> None:
    ex_date = date(2026, 9, 16)
    assert rr.corporate_action_available_from(ex_date) == datetime(2026, 9, 15, 16, tzinfo=UTC)
    assert db.scalar(sa.select(rr.corporate_action_available_from_sql(sa.literal(ex_date)))) == (
        rr.corporate_action_available_from(ex_date)
    )
    assert (rr.TDCC_WEEKLY.rule_id, rr.TDCC_WEEKLY.version) == ("tdcc_weekly", 1)
    assert (rr.CORPORATE_ACTION_EX_DATE.rule_id, rr.CORPORATE_ACTION_EX_DATE.version) == (
        "corporate_action_ex_date", 1
    )
    assert rr.corporate_action_available_from(ex_date) - rr.corporate_action_available_from(
        ex_date - timedelta(days=1)) == timedelta(days=1)


PREVIOUS_HEAD = "272951c4e3eb"


def test_the_downgrade_refuses_while_a_table_holds_rows(isolated_database_url, tmp_path) -> None:
    from alembic import command
    from conftest import alembic_config, alembic_head

    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore

            fetch = record_fetch(
                connection,
                FetchRecord("t", "t", "t", None, "gap_fill", "t", "abc", datetime.now(UTC)),
                content=b"raw", status="succeeded", store=LocalRawArtifactStore(tmp_path),
            )
            connection.execute(sa.text(
                "INSERT INTO stocks (stock_id, name, market, fetch_id) "
                "VALUES ('2330', 'x', 'sii', :f)"), {"f": fetch})
            _distribution(connection, fetch)
        with pytest.raises(DBAPIError):
            command.downgrade(alembic_config(isolated_database_url), PREVIOUS_HEAD)
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                alembic_head()
            )
            assert connection.scalar(sa.text("SELECT count(*) FROM shareholding_distributions")) == 1
    finally:
        engine.dispose()


def test_an_empty_downgrade_drops_the_tables_and_upgrade_restores_them(
    isolated_database_url,
) -> None:
    from alembic import command
    from conftest import alembic_config

    config = alembic_config(isolated_database_url)
    engine = sa.create_engine(isolated_database_url)
    present = sa.text("SELECT count(*) FROM information_schema.tables WHERE table_name = ANY(:t)")
    try:
        command.downgrade(config, PREVIOUS_HEAD)
        with engine.connect() as connection:
            assert connection.scalar(present, {"t": list(COLUMNS)}) == 0
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(present, {"t": list(COLUMNS)}) == len(COLUMNS)
    finally:
        engine.dispose()
