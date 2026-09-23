"""The v2 write path for monthly revenue (ADR-0027 "35-c 定案", Step 35-c-2).

One job per market and page: MOPS publishes a market's month as a domestic
page (`_0`) and a foreign-issuer page (`_1`, the KY companies), and a company is
on exactly one of them. The runner is the exchange-daily one; what differs is
the key's date column, the request, and when a month's page stops changing.

Publication is per issuer, so the runner stores `published_at` on a key's first
row: the fetch time of a first capture, NULL for any other purpose.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from stock_data_center.db import schema_v2 as v2
from stock_data_center.ingestion import adapters as a
from stock_data_center.ingestion import models as m
from stock_data_center.monthly_revenue.models import RevenuePeriod
from stock_data_center.v2.exchange_daily import Job, whole

_AMOUNTS = (
    "revenue", "revenue_last_month", "revenue_last_year_month",
    "cumulative_revenue", "cumulative_revenue_last_year",
)
_PERCENTS = ("mom_pct", "yoy_pct", "cumulative_yoy_pct")


def _rows(parsed, source: str) -> list[dict]:
    month = date(parsed.period.year, parsed.period.month, 1)
    return [
        {
            "stock_id": row.security_code,
            "source": source,
            "revenue_month": month,
            # Already TWD: the adapter multiplies the published thousands out.
            **{name: whole(getattr(row.observation, name)) for name in _AMOUNTS},
            **{name: getattr(row.observation, name) for name in _PERCENTS},
            "note": row.observation.note,
        }
        for row in parsed.rows
    ]


def settled_at(month: date) -> datetime:
    """When a month's page is complete: 00:00 Asia/Taipei on the first day of
    the month after next. Filings are due on the 10th of the next month and
    13-311 domestic issuers a month file later (audit §7.1), so the page keeps
    changing well after the deadline; a completeness choice, not a visibility one."""
    after_next = (month.replace(day=1) + timedelta(days=62)).replace(day=1)
    return datetime.combine(after_next, time(0), tzinfo=ZoneInfo("Asia/Taipei")).astimezone(UTC)


def _job(adapter, page: m.RevenuePage) -> Job:
    return Job(
        v2.monthly_revenues,
        adapter,
        m.MonthlyRevenueRequest,
        _rows,
        monthly=True,
        period_column="revenue_month",
        variant="foreign" if page is m.RevenuePage.FOREIGN else "",
        request_of=lambda month: m.MonthlyRevenueRequest(
            RevenuePeriod(month.year, month.month), page
        ),
        settled_of=settled_at,
        # The page for a month nobody has filed yet (audit §4.7).
        empty=frozenset({"no_data_for_period"}),
    )


JOBS: dict[str, Job] = {
    job.key: job
    for adapter in (a.MOPSSiiMonthlyRevenueAdapter(), a.MOPSOtcMonthlyRevenueAdapter())
    for job in (_job(adapter, m.RevenuePage.DOMESTIC), _job(adapter, m.RevenuePage.FOREIGN))
}
