"""Step 35-c-2: the v2 write path for financial reports (ADR-0027 "35-c 定案").

A report version is the whole document: the report row and all its facts are
written in one transaction, and a refiled document with any fact changed is a
new version carrying its full set. MOPS answers `檔案不存在!` for the report id
the filer does not file, so the writer asks for 合併 (`C`) and then 個體 (`A`).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.financials.models import XBRLContext
from stock_data_center.ingestion.models import FetchedArtifact, SourceDataError
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import financial_reports as fr
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TAIWAN_CEMENT = (FIXTURES / "mops_t164sb01_1101_2025Q1_statements.html").read_bytes()
LATER_QUARTER = (FIXTURES / "mops_t164sb01_1101_2025Q3_statements.html").read_bytes()
INDIVIDUAL = (FIXTURES / "mops_t164sb01_1342_2025Q1_statements.html").read_bytes()
BROKER = (FIXTURES / "mops_t164sb01_5864_2020Q1_statements.html").read_bytes()
NO_SUCH_REPORT = "檔案不存在!".encode("cp950")
SEEN = datetime(2025, 5, 10, 8, 0, tzinfo=UTC)


class Replay:
    def __init__(self, *responses: bytes, at: datetime | None = None) -> None:
        self.responses = list(responses)
        self.requests: list[str] = []
        self.at = at

    def fetch(self, resource) -> FetchedArtifact:
        self.requests.append(resource.resource_key)
        return FetchedArtifact(
            content=self.responses.pop(0), source_uri=resource.source_uri,
            fetched_at=self.at or datetime.now(UTC), media_type="text/html",
        )


@pytest.fixture
def store(tmp_path) -> LocalRawArtifactStore:
    return LocalRawArtifactStore(tmp_path)


@pytest.fixture
def universe(db: Connection, store) -> None:
    fetch_id = record_fetch(
        db,
        FetchRecord("stocks", "twse_isin", "twse_isin:test", None, "first_capture",
                    "test:v1", "abc", datetime.now(UTC)),
        content=b"isin", status="succeeded", store=store,
    )
    for stock_id in ("1101", "1342", "5864"):
        db.execute(
            sa.text("INSERT INTO stocks (stock_id, name, market, fetch_id) "
                    "VALUES (:s, :n, 'sii', :f) ON CONFLICT DO NOTHING"),
            {"s": stock_id, "n": stock_id, "f": fetch_id},
        )


def _ingest(db, store, stock_id, year, quarter, *responses, purpose="gap_fill", at=None):
    fetcher = Replay(*responses, at=at)
    outcome = fr.ingest(db, stock_id, year, quarter, fetcher=fetcher, git_commit="abc",
                        purpose=purpose, store=store)
    return outcome, fetcher


def _reports(db) -> list[sa.Row]:
    return db.execute(sa.text(
        "SELECT id, stock_id, report_category, published_at FROM financial_reports "
        "ORDER BY recorded_at")).all()


def _facts(db, report_id) -> list[sa.Row]:
    return db.execute(sa.text(
        "SELECT statement, account_code, concept, period_start, period_end, unit, value "
        "FROM financial_report_facts WHERE report_id = :r"), {"r": report_id}).all()


def test_a_report_and_all_its_facts_are_one_version(db, store, universe) -> None:
    outcome, fetcher = _ingest(db, store, "1101", 2025, 1, TAIWAN_CEMENT)
    assert (outcome.status, outcome.appended) == ("succeeded", 1)
    assert fetcher.requests == ["mops_t164sb01:financial_filing:1101:2025Q1:C"]
    (report,) = _reports(db)
    assert report.report_category == "consolidated"
    facts = _facts(db, report.id)
    assert len(facts) == outcome.parsed == 20
    # The value migrated from v1 for 1101 2025Q1: 1100 cash at 2025-03-31.
    cash = [f for f in facts if f.account_code == "1100" and f.period_end == date(2025, 3, 31)]
    assert [(f.statement, f.period_start, f.value) for f in cash] == [
        ("balance_sheet", None, Decimal(89_680_417_000))]


def test_the_same_document_again_adds_no_version(db, store, universe) -> None:
    _ingest(db, store, "1101", 2025, 1, TAIWAN_CEMENT)
    again, _ = _ingest(db, store, "1101", 2025, 1, TAIWAN_CEMENT)
    assert (again.status, again.appended, again.unchanged) == ("succeeded", 0, 1)
    assert len(_reports(db)) == 1
    fetches = db.scalar(sa.text("SELECT count(*) FROM fetches WHERE source = 'mops_t164sb01'"))
    assert fetches == 2


def test_a_filer_of_individual_reports_is_asked_again_for_a(db, store, universe) -> None:
    outcome, fetcher = _ingest(db, store, "1342", 2025, 1, NO_SUCH_REPORT, INDIVIDUAL)
    assert (outcome.status, outcome.appended) == ("succeeded", 1)
    assert fetcher.requests == [
        "mops_t164sb01:financial_filing:1342:2025Q1:C",
        "mops_t164sb01:financial_filing:1342:2025Q1:A",
    ]
    assert _reports(db)[0].report_category == "individual"
    first = db.execute(sa.text(
        "SELECT status, reason_code FROM fetches WHERE resource_key LIKE '%1342:2025Q1:C'")).one()
    assert tuple(first) == ("empty", "no_such_report")


def test_no_report_under_either_id_is_empty(db, store, universe) -> None:
    outcome, _ = _ingest(db, store, "1101", 2026, 3, NO_SUCH_REPORT, NO_SUCH_REPORT)
    assert (outcome.status, outcome.reason_code) == ("empty", "no_such_report")
    assert _reports(db) == []


def test_a_first_capture_is_public_from_its_fetch_time(db, store, universe) -> None:
    _ingest(db, store, "1101", 2025, 1, TAIWAN_CEMENT, purpose="first_capture", at=SEEN)
    assert _reports(db)[0].published_at == SEEN


def test_a_changed_document_is_a_new_full_version_public_from_recorded_at(
    db, store, universe
) -> None:
    _ingest(db, store, "1101", 2025, 1, TAIWAN_CEMENT, purpose="first_capture", at=SEEN)
    text = TAIWAN_CEMENT.decode("utf-8")
    refiled = text.replace("89,680,417", "89,680,418", 1).encode("utf-8")
    assert refiled != TAIWAN_CEMENT
    outcome, _ = _ingest(db, store, "1101", 2025, 1, refiled, purpose="first_capture")
    assert outcome.appended == 1
    first, second = _reports(db)
    assert (first.published_at, second.published_at) == (SEEN, None)
    assert len(_facts(db, second.id)) == len(_facts(db, first.id)) == 20


def test_a_financial_industry_filer_is_quarantined(db, store, universe) -> None:
    outcome, _ = _ingest(db, store, "5864", 2020, 1, BROKER)
    assert (outcome.status, outcome.reason_code) == ("quarantined", "financial_industry_issuer")
    assert _reports(db) == []


def test_a_fact_with_a_dimension_quarantines_the_whole_document() -> None:
    # ADR-0027 overrides CLAUDE.md §33: no stored fact has a dimension, so the
    # identity has no place for one, and a document with one fails closed.
    from stock_data_center.ingestion import models as m
    from stock_data_center.ingestion.adapters.financial_filing import (
        MOPSFinancialFilingAdapter,
    )

    parsed = MOPSFinancialFilingAdapter().parse(
        TAIWAN_CEMENT, m.FinancialFilingRequest("1101", 2025, 1, "C"))
    fact = parsed.facts[0]
    context = fact.observation.context
    dimensioned = XBRLContext(
        entity_identifier=context.entity_identifier, period_type=context.period_type,
        instant_date=context.instant_date, period_start=context.period_start,
        period_end=context.period_end, explicit_dimensions={"ifrs-full:SegmentsAxis": "x"},
    )
    bad = replace(fact, observation=replace(fact.observation, context=dimensioned))
    with pytest.raises(SourceDataError) as refused:
        fr.facts_of(replace(parsed, facts=(bad, *parsed.facts[1:])))
    assert refused.value.reason_code == "dimensioned_fact"


def test_a_quarter_is_settled_the_day_after_its_deadline() -> None:
    taipei_midnight = timedelta(hours=-8)
    assert fr.settled_at(2025, 1) == datetime(2025, 5, 16, tzinfo=UTC) + taipei_midnight
    assert fr.settled_at(2025, 4) == datetime(2026, 4, 1, tzinfo=UTC) + taipei_midnight


def test_pending_skips_a_stored_report_and_a_financial_industry_filer(db, store, universe) -> None:
    after = fr.settled_at(2025, 1)
    _ingest(db, store, "1101", 2025, 1, TAIWAN_CEMENT, at=after)
    _ingest(db, store, "5864", 2020, 1, BROKER, at=fr.settled_at(2020, 1))
    _ingest(db, store, "1342", 2025, 1, NO_SUCH_REPORT, NO_SUCH_REPORT, at=after)
    # A report fetched before the deadline may still be refiled: ask again.
    _ingest(db, store, "1101", 2025, 3, LATER_QUARTER, at=fr.settled_at(2025, 3) - timedelta(1))
    wanted = [("1101", 2025, 1), ("5864", 2020, 1), ("1342", 2025, 1), ("1101", 2025, 3)]
    assert fr.pending(db, wanted) == [("1342", 2025, 1), ("1101", 2025, 3)]


def test_quarters_are_the_ones_whose_period_ends_in_the_range() -> None:
    assert fr.quarters(date(2025, 3, 31), date(2025, 12, 30)) == [(2025, 1), (2025, 2), (2025, 3)]


def test_the_same_facts_under_another_category_are_a_new_version(db, store, universe) -> None:
    # 合併 and 個體 figures differ in meaning even where the numbers agree.
    _ingest(db, store, "1342", 2025, 1, NO_SUCH_REPORT, INDIVIDUAL)
    (stored,) = _reports(db)
    consolidated = db.execute(sa.text(
        "INSERT INTO financial_reports (stock_id, report_year, report_quarter, report_category, "
        "fetch_id) SELECT stock_id, report_year, report_quarter, 'consolidated', fetch_id "
        "FROM financial_reports WHERE id = :r RETURNING id"), {"r": stored.id}).scalar_one()
    db.execute(sa.text(
        "INSERT INTO financial_report_facts SELECT :c, statement, account_code, concept, "
        "period_start, period_end, unit, value FROM financial_report_facts WHERE report_id = :r"),
        {"c": consolidated, "r": stored.id})
    outcome, _ = _ingest(db, store, "1342", 2025, 1, NO_SUCH_REPORT, INDIVIDUAL)
    assert outcome.appended == 1
    assert [row.report_category for row in _reports(db)] == [
        "individual", "consolidated", "individual"]


def test_a_backfill_walks_every_stock_for_every_quarter(db, store, universe) -> None:
    from stock_data_center.v2.backfill import ALL_KEYS, _unit

    assert fr.KEY in ALL_KEYS
    fetcher = Replay(TAIWAN_CEMENT, NO_SUCH_REPORT, INDIVIDUAL, BROKER)
    report = fr.run(db, date(2025, 1, 1), date(2025, 3, 31), fetcher=fetcher,
                    git_commit="abc", purpose="gap_fill", store=store, unit=_unit)
    # 1101, 1342 (C then A) and 5864 for 2025Q1; the broker's 2020 file quarantines.
    assert len(fetcher.requests) == 4
    assert report == {"periods": 3, "skipped": 0, "appended": 2, "unchanged": 0,
                      "succeeded": 2, "quarantined": 1}


def test_a_fact_beyond_its_column_quarantines_the_document() -> None:
    from stock_data_center.ingestion import models as m
    from stock_data_center.ingestion.adapters.financial_filing import (
        MOPSFinancialFilingAdapter,
    )

    parsed = MOPSFinancialFilingAdapter().parse(
        TAIWAN_CEMENT, m.FinancialFilingRequest("1101", 2025, 1, "C"))
    fact = parsed.facts[0]
    bad = replace(fact, observation=replace(fact.observation, numeric_value=Decimal("1.005")))
    with pytest.raises(SourceDataError) as refused:
        fr.facts_of(replace(parsed, facts=(bad, *parsed.facts[1:])))
    assert refused.value.reason_code == "out_of_range"
