"""The v2 write path for TDCC shareholding distributions (Step 35-c-2).

OpenData `id=1-5` serves only the latest week, under one resource key, so the
job has no periods to walk: every run fetches the file once, and the week is
the file's own 資料日期. History before the first forward capture is the legacy
archive, already copied by Step 35-c-1. Publication follows `tdcc_weekly@1`.

The seventeen levels of the `tdcc-opendata-v1` profile become one wide row:
levels 1-15 are holding ranges, 16 the signed 差異數調整 with no holder count,
17 the published total.
"""

from __future__ import annotations

from stock_data_center.db import schema_v2 as v2
from stock_data_center.ingestion import models as m
from stock_data_center.ingestion.adapters.tdcc_shareholding import TDCCOpenDataAdapter
from stock_data_center.v2.exchange_daily import Job, whole
from stock_data_center.v2.release_rules import tdcc_available_from

HOLDING_LEVELS = range(1, 16)
ADJUSTMENT_LEVEL = 16
TOTAL_LEVEL = 17


def _columns(level: int) -> tuple[str | None, str, str]:
    """(holders, shares, percent) columns of one level."""
    if level == ADJUSTMENT_LEVEL:
        return None, "adjustment_shares", "adjustment_percent"
    if level == TOTAL_LEVEL:
        return "total_holders", "total_shares", "total_percent"
    return f"holders_{level}", f"shares_{level}", f"percent_{level}"


_ALL_COLUMNS = [
    column
    for level in (*HOLDING_LEVELS, ADJUSTMENT_LEVEL, TOTAL_LEVEL)
    for column in _columns(level)
    if column is not None
]


def _rows(parsed, source: str) -> list[dict]:
    rows = []
    for item in parsed.rows:
        row = {
            "stock_id": item.security_code,
            "source": source,
            "snapshot_date": item.observation.snapshot_date,
            **dict.fromkeys(_ALL_COLUMNS),
        }
        for bucket in item.observation.distribution:
            holders, shares, percent = _columns(int(bucket.bucket_code))
            if holders is not None:
                row[holders] = bucket.holder_count
            row[shares] = whole(bucket.shares)
            row[percent] = bucket.ownership_percent
        rows.append(row)
    return rows


JOBS: dict[str, Job] = {
    job.key: job
    for job in (
        Job(
            v2.shareholding_distributions,
            TDCCOpenDataAdapter(),
            m.TDCCShareholdingRequest,
            _rows,
            period_column="snapshot_date",
            request_of=lambda _period: m.TDCCShareholdingRequest(),
            settled_of=tdcc_available_from,
            latest_only=True,
        ),
    )
}
