#!/usr/bin/env python
"""Step 35-c-1 acceptance: the copied history equals v1, row for row.

Every check compares v2 with v1 by a route independent of the copy SQL in
`migrate_to_schema_v2.py`, in both directions (`EXCEPT` each way), restricted
to the stocks on today's list:

- monthly revenue: each v1 version's values and publication instant;
- financial reports and every fact of each;
- TDCC: the wide rows unpivoted back to v1's seventeen rows per week;
- publication: TDCC's rule instant equals v1's evidence on every week, and the
  monthly-revenue key corrected after legacy's capture ends on MOPS's value.

Read-only; prints one JSON report and exits 1 if any count is not zero.

    DATABASE_URL=postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \\
        .venv/bin/python scripts/verify_schema_v2_35c.py
"""

from __future__ import annotations

import json
import os
import sys

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.v2.release_rules import tdcc_available_from_sql

IN_UNIVERSE = "JOIN security s ON s.id = v.security_id JOIN stocks k ON k.stock_id = s.security_code"

MR_V1 = f"""
SELECT s.security_code, v.source, v.revenue_period, v.revenue::numeric,
       v.revenue_last_month::numeric, v.revenue_last_year_month::numeric,
       v.cumulative_revenue::numeric, v.cumulative_revenue_last_year::numeric,
       v.mom_pct::numeric, v.yoy_pct::numeric, v.cumulative_yoy_pct::numeric, v.note,
       p.published_at, v.ingest_run_id
  FROM monthly_revenue_versions v {IN_UNIVERSE}
  LEFT JOIN (SELECT monthly_revenue_version_id, published_at FROM publication_evidence
              WHERE dataset_code = 'monthly_revenue' AND published_at IS NOT NULL) p
    ON p.monthly_revenue_version_id = v.id"""
MR_V2 = """
SELECT stock_id, source, revenue_month, revenue::numeric, revenue_last_month::numeric,
       revenue_last_year_month::numeric, cumulative_revenue::numeric,
       cumulative_revenue_last_year::numeric, mom_pct, yoy_pct, cumulative_yoy_pct, note,
       published_at, fetch_id
  FROM monthly_revenues"""

FR_V1 = f"""
SELECT v.id, s.security_code, v.report_year, v.report_quarter, v.report_category,
       p.published_at, seal.ingested_at, v.ingest_run_id
  FROM financial_filing_versions v
  JOIN financial_filing_seals seal ON seal.filing_version_id = v.id {IN_UNIVERSE}
  LEFT JOIN (SELECT financial_filing_version_id, published_at FROM publication_evidence
              WHERE dataset_code = 'financial_filing' AND published_at IS NOT NULL) p
    ON p.financial_filing_version_id = v.id"""
FR_V2 = """
SELECT id, stock_id, report_year, report_quarter, report_category, published_at,
       recorded_at, fetch_id FROM financial_reports"""

FACTS_V1 = f"""
SELECT f.filing_version_id, f.statement, f.account_code, f.concept_qname,
       f.period_type, coalesce(f.instant_date, f.period_end), f.period_start,
       f.unit_identity, f.numeric_value
  FROM financial_facts f JOIN financial_filing_versions v ON v.id = f.filing_version_id
  {IN_UNIVERSE}"""
FACTS_V2 = """
SELECT report_id, statement, account_code, concept,
       CASE WHEN period_start IS NULL THEN 'instant' ELSE 'duration' END,
       period_end, period_start, unit, value
  FROM financial_report_facts"""

LEVEL_COLUMNS = [
    *((str(n), f"holders_{n}", f"shares_{n}", f"percent_{n}") for n in range(1, 16)),
    ("16", "NULL::bigint", "adjustment_shares", "adjustment_percent"),
    ("17", "total_holders", "total_shares", "total_percent"),
]
TDCC_V1 = f"""
SELECT s.security_code, v.source, v.snapshot_date, seal.ingested_at, d.bucket_code,
       d.holder_count, d.shares, d.ownership_percent, v.ingest_run_id
  FROM tdcc_snapshot_versions v
  JOIN tdcc_snapshot_seals seal ON seal.snapshot_version_id = v.id
  JOIN tdcc_distribution d ON d.snapshot_version_id = v.id {IN_UNIVERSE}"""
TDCC_V2 = " UNION ALL ".join(
    f"SELECT stock_id, source, snapshot_date, recorded_at, '{level}', {holders}, "
    f"{shares}::numeric, {percent}, fetch_id FROM shareholding_distributions"
    for level, holders, shares, percent in LEVEL_COLUMNS
)


def _difference(connection, left: str, right: str) -> int:
    return connection.execute(
        sa.text(f"SELECT count(*) FROM (({left}) EXCEPT ALL ({right})) x")
    ).scalar_one()


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    engine = sa.create_engine(url)
    report: dict[str, dict[str, int]] = {}
    with engine.connect() as connection:
        for name, v1, v2, table in (
            ("monthly_revenues", MR_V1, MR_V2, "monthly_revenues"),
            ("financial_reports", FR_V1, FR_V2, "financial_reports"),
            ("financial_report_facts", FACTS_V1, FACTS_V2, "financial_report_facts"),
            ("shareholding_distributions", TDCC_V1, TDCC_V2, "shareholding_distributions"),
        ):
            report[name] = {
                "rows": connection.scalar(sa.text(f"SELECT count(*) FROM {table}")),
                "v1_not_in_v2": _difference(connection, v1, v2),
                "v2_not_in_v1": _difference(connection, v2, v1),
            }
            print(name, report[name], file=sys.stderr, flush=True)

        rule = connection.execute(
            sa.select(sa.func.count())
            .select_from(sa.text(
                f"tdcc_snapshot_versions v {IN_UNIVERSE} JOIN publication_evidence p "
                "ON p.tdcc_snapshot_version_id = v.id AND p.dataset_code = 'tdcc_snapshot'"))
            .where(sa.literal_column("p.published_at").is_distinct_from(
                tdcc_available_from_sql(sa.literal_column("v.snapshot_date"))))
        ).scalar_one()
        report["tdcc_rule_instant_differs_from_v1_evidence"] = {"weeks": rule}

        # A key corrected after legacy's capture: its latest row is MOPS's value.
        report["monthly_revenue_latest_is_not_official"] = {"keys": connection.execute(sa.text("""
            WITH corrected AS (
              SELECT stock_id, source, revenue_month FROM monthly_revenues
               GROUP BY 1, 2, 3 HAVING count(*) > 1
            ), latest AS (
              SELECT DISTINCT ON (m.stock_id, m.source, m.revenue_month) m.fetch_id
                FROM monthly_revenues m JOIN corrected USING (stock_id, source, revenue_month)
               ORDER BY m.stock_id, m.source, m.revenue_month, m.recorded_at DESC
            )
            SELECT count(*) FROM latest
              JOIN raw_artifact_observations o ON o.ingest_run_id = latest.fetch_id
             WHERE o.artifact_origin <> 'official_fetch'""")).scalar_one()}
        report["monthly_revenue_published"] = {
            "rows": connection.scalar(sa.text(
                "SELECT count(*) FROM monthly_revenues WHERE published_at IS NOT NULL")),
            "keys_with_two_rows": connection.scalar(sa.text(
                "SELECT count(*) FROM (SELECT 1 FROM monthly_revenues "
                "GROUP BY stock_id, source, revenue_month HAVING count(*) > 1) x")),
        }
    print(json.dumps(report, indent=2, default=str))
    failed = [
        name for name, counts in report.items()
        if name != "monthly_revenue_published"
        and any(value for key, value in counts.items() if key != "rows")
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
