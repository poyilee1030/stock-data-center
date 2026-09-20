"""Step 22-b — walking the monthly-revenue history, and what it covers.

The window is 80 months × 2 markets × 2 pages, about 320 requests against a
host that answers one request every three seconds. The same three properties
the daily backfills needed apply: each month-page carries its own import id so
a run that dies resumes where it stopped, one bad page is reported rather than
fatal, and the declaration — not the runner — decides which months are in
scope.

A month with no page of its own is not a gap in the month: `_1` exists for
every month of the window, but a market-month the source has not published
yet answers 查無資料, and that is reported as `no_data` rather than as a
failure, because a failure is something a rerun could fix.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4, uuid5

import pytest
import sqlalchemy as sa

from stock_data_center.coverage import CoverageValidator, ExpectedCoverageService
from stock_data_center.ingestion.adapters import (
    MOPSOtcMonthlyRevenueAdapter,
    MOPSSiiMonthlyRevenueAdapter,
)
from stock_data_center.ingestion.backfill import (
    MonthlyRevenueBackfill,
    default_base_import_id,
    revenue_page_import_id,
)
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    RevenuePage,
)
from stock_data_center.ingestion.monthly_revenue import MonthlyRevenueImporter
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.monthly_revenue.models import RevenuePeriod
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SII_0 = (FIXTURES / "mops_t21sc03_sii_115_7_0.html").read_bytes()
SII_1 = (FIXTURES / "mops_t21sc03_sii_115_7_1.html").read_bytes()
OTC_0 = (FIXTURES / "mops_t21sc03_otc_115_7_0.html").read_bytes()
NO_DATA = (FIXTURES / "mops_t21sc03_sii_115_9_0_no_data.html").read_bytes()

SII_ROWS, SII_KY_ROWS = 992, 94
MAY, JUNE, JULY = (
    RevenuePeriod(2026, 5), RevenuePeriod(2026, 6), RevenuePeriod(2026, 7)
)
_TITLE = re.compile(r"(\d+)年(\d+)月份")


def restamped(content: bytes, period: RevenuePeriod) -> bytes:
    """The captured page, retitled for another month.

    The adapter checks the title against the request, so one fixture cannot
    answer for three months without this.
    """
    text = content.decode("cp950")
    return _TITLE.sub(f"{period.year - 1911}年{period.month}月份", text, count=1).encode(
        "cp950"
    )


class PageFetcher:
    """Answers each (period, page) request from the fixture of that page."""

    def __init__(self, *, no_data: set[tuple[RevenuePeriod, RevenuePage]] | None = None,
                 fails: set[tuple[RevenuePeriod, RevenuePage]] | None = None) -> None:
        self.no_data = no_data or set()
        self.fails = fails or set()
        self.fetched: list[tuple[RevenuePeriod, RevenuePage]] = []

    def fetch(self, resource):
        _, _, period_text, page_value = resource.resource_key.split(":")
        year, month = (int(part) for part in period_text.split("-"))
        period = RevenuePeriod(year, month)
        page = RevenuePage(page_value)
        self.fetched.append((period, page))
        if (period, page) in self.fails:
            raise TimeoutError("the host did not answer")
        if (period, page) in self.no_data:
            content = restamped(NO_DATA, period)
        else:
            source = SII_1 if page is RevenuePage.FOREIGN else (
                OTC_0 if "otc" in resource.source_uri else SII_0
            )
            content = restamped(source, period)
        return FetchedArtifact(
            content=content,
            source_uri=resource.source_uri,
            fetched_at=datetime.now(UTC),
            media_type="text/html",
        )


class RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def backfill(engine, tmp_path, *, fetcher, sleep=None):
    importer = MonthlyRevenueImporter(
        engine, raw_store=LocalRawArtifactStore(tmp_path / "raw"), fetcher=fetcher
    )
    return MonthlyRevenueBackfill(importer, sleep=sleep or RecordingSleep())


def run(runner, *, adapter=None, start=MAY, end=JULY, base=None, pages=None):
    return runner.run(
        adapter=adapter or MOPSSiiMonthlyRevenueAdapter(),
        start=start,
        end=end,
        base_import_id=base or uuid4(),
        purpose=IngestPurpose.GAP_FILL,
        min_interval_seconds=3.0,
        pages=pages,
    )


def test_the_runner_walks_every_month_and_both_pages(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    fetcher = PageFetcher()
    try:
        report = run(backfill(engine, tmp_path, fetcher=fetcher))
        assert fetcher.fetched == [
            (month, page)
            for month in (MAY, JUNE, JULY)
            for page in (RevenuePage.DOMESTIC, RevenuePage.FOREIGN)
        ]
        assert report.imported == 6
        assert report.failed == 0
        assert report.no_data == 0
        assert report.rows == 3 * (SII_ROWS + SII_KY_ROWS)
        assert report.is_complete
        assert report.as_dict()["requested_pages"] == 6
    finally:
        engine.dispose()


def test_the_runner_asks_only_for_the_pages_it_was_given(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    fetcher = PageFetcher()
    try:
        report = run(
            backfill(engine, tmp_path, fetcher=fetcher),
            start=JULY,
            end=JULY,
            pages=(RevenuePage.FOREIGN,),
        )
        assert fetcher.fetched == [(JULY, RevenuePage.FOREIGN)]
        assert report.rows == SII_KY_ROWS
    finally:
        engine.dispose()


def test_a_range_reaching_before_the_declared_window_is_refused(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    fetcher = PageFetcher()
    try:
        with pytest.raises(ValueError, match="declared coverage window"):
            run(
                backfill(engine, tmp_path, fetcher=fetcher),
                start=RevenuePeriod(2019, 12),
                end=JULY,
            )
        assert fetcher.fetched == []
    finally:
        engine.dispose()


def test_a_month_the_source_has_not_published_is_no_data_not_a_failure(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    fetcher = PageFetcher(no_data={(JULY, RevenuePage.FOREIGN)})
    try:
        report = run(backfill(engine, tmp_path, fetcher=fetcher))
        assert report.no_data == 1
        assert report.failed == 0
        assert report.imported == 5
        assert not report.is_complete
        assert report.as_dict()["no_data_pages"] == [
            {"period": "2026-07", "page": "1", "reason_code": "no_data_for_period"}
        ]
    finally:
        engine.dispose()


def test_one_unanswered_page_is_reported_and_the_walk_continues(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    fetcher = PageFetcher(fails={(JUNE, RevenuePage.DOMESTIC)})
    try:
        report = run(backfill(engine, tmp_path, fetcher=fetcher))
        assert report.failed == 1
        assert report.imported == 5
        assert not report.is_complete
        failure = report.as_dict()["failures"][0]
        assert failure["period"] == "2026-06"
        assert failure["page"] == "0"
        assert failure["reason_code"] == "operational_error"
        assert len(fetcher.fetched) == 6
    finally:
        engine.dispose()


def test_a_resumed_page_is_not_refetched_and_not_throttled(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    base = uuid4()
    first = PageFetcher()
    second = PageFetcher()
    sleep = RecordingSleep()
    try:
        run(backfill(engine, tmp_path, fetcher=first), base=base)
        report = run(
            backfill(engine, tmp_path, fetcher=second, sleep=sleep), base=base
        )
        assert second.fetched == []
        assert report.resumed == 6
        assert report.imported == 0
        assert report.is_complete
        assert sleep.calls == []
    finally:
        engine.dispose()


def test_the_runner_throttles_between_pages_but_not_before_the_first(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    sleep = RecordingSleep()
    try:
        run(backfill(engine, tmp_path, fetcher=PageFetcher(), sleep=sleep))
        assert sleep.calls == [3.0] * 5
    finally:
        engine.dispose()


def test_the_import_id_of_a_page_is_derived_from_the_run_scope() -> None:
    from datetime import date

    first, last = date(2026, 5, 1), date(2026, 7, 1)
    base = default_base_import_id("mops_t21sc03_sii", first, last)
    assert base == default_base_import_id("mops_t21sc03_sii", first, last)
    assert base != default_base_import_id("mops_t21sc03_otc", first, last)
    assert revenue_page_import_id(
        base, "mops_t21sc03_sii", JULY, RevenuePage.DOMESTIC
    ) != revenue_page_import_id(
        base, "mops_t21sc03_sii", JULY, RevenuePage.FOREIGN
    )
    assert revenue_page_import_id(
        base, "mops_t21sc03_sii", JULY, RevenuePage.DOMESTIC
    ) == uuid5(base, "mops_t21sc03_sii:2026-07:0")


def test_each_market_declares_its_own_source_and_month_cadence(
    isolated_database_url: str,
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            declarations = {
                item.market: item
                for item in ExpectedCoverageService().declarations(connection)
                if item.dataset_code == "monthly_revenue"
            }
        assert declarations["TWSE"].source == "mops_t21sc03_sii"
        assert declarations["TPEx"].source == "mops_t21sc03_otc"
        for declaration in declarations.values():
            assert declaration.cadence == "calendar_month"
            assert declaration.period_column == "revenue_period"
            assert declaration.window_start == __import__("datetime").date(2020, 1, 1)
    finally:
        engine.dispose()


def test_the_coverage_report_counts_a_month_either_page_answered(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """A month is covered when the source published it, whichever page did.

    The KY page is not a separate dataset, and a market-month with no foreign
    issuer at all must not read as a gap.
    """
    from datetime import date

    engine = sa.create_engine(isolated_database_url)
    try:
        run(
            backfill(engine, tmp_path, fetcher=PageFetcher()),
            start=MAY,
            end=JUNE,
        )
        run(
            backfill(engine, tmp_path, fetcher=PageFetcher()),
            start=JULY,
            end=JULY,
            pages=(RevenuePage.FOREIGN,),
        )
        with engine.connect() as connection:
            report = CoverageValidator().report(
                connection,
                dataset_code="monthly_revenue",
                market="TWSE",
                start=date(2026, 5, 1),
                end=date(2026, 7, 1),
            )
        assert report.observed == (date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1))
        assert report.missing == ()
        assert report.unexpected == ()
        assert report.is_complete
    finally:
        engine.dispose()


def test_the_coverage_report_names_a_month_that_was_never_imported(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from datetime import date

    engine = sa.create_engine(isolated_database_url)
    try:
        run(
            backfill(engine, tmp_path, fetcher=PageFetcher()),
            start=JULY,
            end=JULY,
        )
        with engine.connect() as connection:
            report = CoverageValidator().report(
                connection,
                dataset_code="monthly_revenue",
                market="TWSE",
                start=date(2026, 5, 1),
                end=date(2026, 7, 1),
            )
        assert report.missing == (date(2026, 5, 1), date(2026, 6, 1))
        assert not report.is_complete
    finally:
        engine.dispose()


def test_one_market_is_not_another_markets_coverage(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from datetime import date

    engine = sa.create_engine(isolated_database_url)
    try:
        run(
            backfill(engine, tmp_path, fetcher=PageFetcher()),
            adapter=MOPSOtcMonthlyRevenueAdapter(),
            start=JULY,
            end=JULY,
            pages=(RevenuePage.DOMESTIC,),
        )
        with engine.connect() as connection:
            twse = CoverageValidator().report(
                connection, dataset_code="monthly_revenue", market="TWSE",
                start=date(2026, 7, 1), end=date(2026, 7, 1),
            )
            tpex = CoverageValidator().report(
                connection, dataset_code="monthly_revenue", market="TPEx",
                start=date(2026, 7, 1), end=date(2026, 7, 1),
            )
        assert twse.missing == (date(2026, 7, 1),)
        assert tpex.observed == (date(2026, 7, 1),)
    finally:
        engine.dispose()


def test_the_cli_walks_a_month_range(isolated_database_url: str, tmp_path: Path) -> None:
    import stock_data_center.ingestion.monthly_revenue as module
    from stock_data_center.ingestion.cli import main

    original = module.MonthlyRevenueImporter.__init__
    fetcher = PageFetcher()

    def static_init(self, engine, **kwargs):
        kwargs["fetcher"] = fetcher
        original(self, engine, **kwargs)

    module.MonthlyRevenueImporter.__init__ = static_init
    try:
        code = main([
            "--database-url", isolated_database_url,
            "--purpose", "gap_fill",
            "monthly-revenue", "--source", "mops_t21sc03_sii",
            "--period", "2026-06", "--through", "2026-07",
            "--page", "both", "--min-interval-seconds", "0",
            "--raw-root", str(tmp_path / "raw"),
        ])
    finally:
        module.MonthlyRevenueImporter.__init__ = original
    assert code == 0
    assert fetcher.fetched == [
        (month, page)
        for month in (JUNE, JULY)
        for page in (RevenuePage.DOMESTIC, RevenuePage.FOREIGN)
    ]


def test_the_period_column_is_derived_from_the_year_and_month(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The coverage period is generated, not written: nothing can disagree."""
    from datetime import date

    engine = sa.create_engine(isolated_database_url)
    try:
        run(
            backfill(engine, tmp_path, fetcher=PageFetcher()),
            start=JULY,
            end=JULY,
            pages=(RevenuePage.DOMESTIC,),
        )
        with engine.connect() as connection:
            periods = set(
                connection.scalars(
                    sa.text("SELECT DISTINCT revenue_period FROM monthly_revenue_versions")
                )
            )
            with pytest.raises(sa.exc.DBAPIError):
                connection.execute(
                    sa.text(
                        "UPDATE monthly_revenue_versions SET revenue_period = "
                        "DATE '2020-01-01'"
                    )
                )
        assert periods == {date(2026, 7, 1)}
    finally:
        engine.dispose()


def test_the_downgrade_removes_the_declaration_and_upgrade_restores_it(
    isolated_database_url: str,
) -> None:
    from alembic import command
    from conftest import alembic_config

    config = alembic_config(isolated_database_url)
    engine = sa.create_engine(isolated_database_url)
    declarations = sa.text(
        "SELECT count(*) FROM dataset_expected_coverage "
        "WHERE dataset_code = 'monthly_revenue'"
    )
    column = sa.text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'monthly_revenue_versions' "
        "AND column_name = 'revenue_period'"
    )
    try:
        command.downgrade(config, "d4a7f2c9b8e1")
        with engine.connect() as connection:
            assert connection.scalar(declarations) == 0
            assert connection.scalar(column) == 0
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(declarations) == 2
            assert connection.scalar(column) == 1
    finally:
        engine.dispose()
