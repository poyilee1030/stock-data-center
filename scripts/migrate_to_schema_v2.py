#!/usr/bin/env python
"""Copy the v1 exchange-daily history into the schema v2 tables (ADR-0027).

A trusted migration path (CLAUDE.md §23): each copied row keeps the Data
Center's original write instant, so `recorded_at` is the v1 `ingested_at`
rather than now. Every other v2 write lets the database stamp it.

What is copied, and what is not:

- `fetches`: every v1 ingest run, one to one (each run has exactly one raw
  file). The v2 fetch reuses the run's UUID, so a v1 row's `ingest_run_id` is
  its v2 `fetch_id`.
- `stocks`: fetched today from the ISIN list, not copied (ADR-0026).
- `trading_days`: the v1 calendar version each month was last seen with.
- Value tables: only stocks on today's list, only the sources ADR-0027 keeps,
  only the 126 indices in `stock_data_center.v2.indices`. The Step 9 pilot
  sources (`twse`, `tpex`) and `tpex_insti_qfii` are not copied.
- Every v1 key holds exactly one version today; the script refuses to run if
  that stops being true, because "latest version" would then need a rule.
- Step 35-c-1 (ADR-0027 "35-c 定案"): monthly revenue, financial reports and
  their facts, and TDCC distributions, for the stocks on today's list.
  `published_at` is the version's one assertion of publication, if any. A
  sealed aggregate (a report, a TDCC week) was recorded when it was sealed.
  Monthly revenue keeps several versions per key; see `MONTHLY_REVENUES`.

Each table is copied in its own transaction and skipped if it already holds
rows, so an interrupted run resumes.

    DATABASE_URL=postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \
        .venv/bin/python scripts/migrate_to_schema_v2.py
"""

from __future__ import annotations

import json
import os
import sys
import time

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.ingestion.lifecycle import current_git_commit
from stock_data_center.v2.indices import KEPT_INDICES
from stock_data_center.v2.universe import load_universe

FETCHES = """
INSERT INTO fetches (id, dataset, source, resource_key, source_uri, purpose,
                     adapter_version, git_commit, fetched_at, status,
                     reason_code, reason_detail, attempt, sha256, byte_size)
SELECT r.id, r.dataset_code, r.source,
       r.run_metadata->>'resource_key', o.source_uri, r.purpose,
       r.run_metadata->>'adapter_version', m.git_commit, o.fetched_at,
       CASE WHEN r.status = 'succeeded' THEN 'succeeded'
            WHEN q.reason_code IS NOT NULL THEN 'quarantined'
            ELSE 'failed' END,
       COALESCE(q.reason_code, k.error_code,
                CASE WHEN r.status = 'running' THEN 'interrupted' END),
       COALESCE(q.reason_detail, k.error_detail),
       1, decode(a.raw_artifact_hash, 'hex'), a.byte_size
  FROM ingest_runs r
  JOIN raw_artifact_observations o ON o.ingest_run_id = r.id
  JOIN raw_artifacts a ON a.id = o.raw_artifact_id
  LEFT JOIN import_manifests m ON m.import_id::text = r.run_metadata->>'import_id'
  LEFT JOIN LATERAL (
        SELECT reason_code, reason_detail FROM import_quarantine iq
         WHERE iq.ingest_run_id = r.id ORDER BY iq.id LIMIT 1) q ON true
  -- An operational failure (adapter, dependency or writer error) has no
  -- quarantine row; v1 kept its reason on the resource's checkpoint.
  LEFT JOIN import_checkpoints k ON k.last_ingest_run_id = r.id
"""

# The version a month was last seen with, not the highest id: v1 deduplicates on
# content, so a month that went A -> B -> A has no third version, and max(id)
# would pick the overturned B. Every sighting is either the version's own run or
# an observation of it; the latest sighting decides, and its fetch is kept.
TRADING_DAYS = """
WITH sighting AS (
    SELECT v.id AS version_id, v.calendar_month, o.fetched_at, o.ingest_run_id
      FROM trading_calendar_versions v
      JOIN (SELECT id AS version_id, ingest_run_id FROM trading_calendar_versions
            UNION
            SELECT calendar_version_id, ingest_run_id
              FROM trading_calendar_version_observations) seen ON seen.version_id = v.id
      JOIN raw_artifact_observations o ON o.ingest_run_id = seen.ingest_run_id
     WHERE v.market = 'TWSE'
), latest AS (
    SELECT DISTINCT ON (calendar_month) version_id, ingest_run_id
      FROM sighting
     ORDER BY calendar_month, fetched_at DESC, ingest_run_id DESC
)
INSERT INTO trading_days (trade_date, fetch_id)
SELECT DISTINCT ON (d.day) d.day, l.ingest_run_id
  FROM latest l
  JOIN trading_calendar_versions v ON v.id = l.version_id,
       unnest(v.trading_days) AS d(day)
 ORDER BY d.day
"""

# (target, v1 table, kept sources, v1 -> v2 column map)
PER_STOCK = [
    ("daily_prices", "daily_price_versions", ("twse_mi_index", "tpex_otc_quotes"), {
        c: c for c in (
            "open_price", "high_price", "low_price", "close_price", "volume",
            "trade_value", "trade_count", "price_change", "price_direction",
            "last_bid_price", "last_ask_price", "last_bid_volume", "last_ask_volume")}),
    ("valuations", "official_valuation_versions", ("twse_bwibbu_d", "tpex_pe_qry_date"), {
        c: c for c in (
            "pe_ratio", "pb_ratio", "dividend_yield", "dividend_year", "report_period")}),
    ("institutional_flows", "institutional_investor_versions",
     ("twse_t86", "tpex_insti_daily_trade"), {
        c: c for c in (
            *(f"{p}_{s}" for p in ("foreign", "foreign_dealer", "trust", "dealer_self",
                                   "dealer_hedge") for s in ("buy", "sell", "net")),
            "dealer_net", "total_net")}),
    ("foreign_holdings", "foreign_holding_versions", ("twse_mi_qfiis", "mops_t13sa150_otc"), {
        c: c for c in (
            "issued_shares", "investable_shares", "held_shares", "investable_ratio",
            "held_ratio", "foreign_legal_limit_ratio")}),
    ("margin_trading", "margin_trading_versions", ("twse_mi_margn", "tpex_margin_balance"), {
        "margin_buy": "margin_buy", "margin_sell": "margin_sell",
        "margin_cash_repayment": "margin_cash_repayment",
        "margin_previous_balance": "margin_previous_balance",
        "margin_balance": "margin_balance", "margin_limit": "margin_next_limit",
        "short_buy": "short_buy", "short_sell": "short_sell",
        "short_stock_repayment": "short_stock_repayment",
        "short_previous_balance": "short_previous_balance",
        "short_balance": "short_balance", "short_limit": "short_next_limit",
        "offset_balance": "offset_balance"}),
    ("securities_lending", "securities_lending_versions", ("twse_twt93u", "tpex_margin_sbl"), {
        "previous_balance": "previous_balance", "sold": "borrowed",
        "returned": "returned", "adjustment": "adjustment", "balance": "balance",
        "next_limit": "next_limit", "next_available_limit": "next_available_limit"}),
]


def per_stock_sql(target: str, source_table: str, columns: dict[str, str]) -> str:
    targets = ", ".join(columns)
    selects = ", ".join(f"v.{source}" for source in columns.values())
    return f"""
INSERT INTO {target} (stock_id, source, trade_date, recorded_at, {targets}, fetch_id)
SELECT s.security_code, v.source, v.trade_date, v.ingested_at, {selects}, v.ingest_run_id
  FROM {source_table} v
  JOIN security s ON s.id = v.security_id
  JOIN stocks k ON k.stock_id = s.security_code
 WHERE v.source = ANY(:sources)
"""


INDEX_PRICES = """
INSERT INTO index_prices (source, index_name, trade_date, recorded_at, open_value,
                          high_value, low_value, close_value, change_points,
                          change_percent, fetch_id)
SELECT v.source, substr(m.index_code, length(v.source) + 2), v.trade_date,
       v.ingested_at, v.open_value, v.high_value, v.low_value, v.close_value,
       v.change_points, v.change_percent, v.ingest_run_id
  FROM market_index_versions v
  JOIN market_index m ON m.id = v.market_index_id
 WHERE m.index_code = ANY(:codes)
"""

MARKET_FLOWS = """
INSERT INTO institutional_market_flows (source, trade_date, institution, recorded_at,
                                        buy, sell, net, fetch_id)
SELECT source, trade_date, institution, ingested_at, buy, sell, net, ingest_run_id
  FROM institutional_market_summary_versions
"""


# 116 keys from 2026M02 hold two versions: MOPS's current value and the value
# legacy captured first, which v1 imported from the archive about two hours
# later. The legacy value was public first and the MOPS value corrects it, so
# the archive version is placed one microsecond before the key's earliest
# official version; otherwise "latest row" would be the overturned value.
MONTHLY_REVENUES = """
INSERT INTO monthly_revenues (stock_id, source, revenue_month, recorded_at, revenue,
                              revenue_last_month, revenue_last_year_month,
                              cumulative_revenue, cumulative_revenue_last_year,
                              mom_pct, yoy_pct, cumulative_yoy_pct, note,
                              published_at, fetch_id)
SELECT s.security_code, v.source, v.revenue_period,
       CASE WHEN o.artifact_origin = 'legacy_archive' AND official.first_at < v.ingested_at
            THEN official.first_at - interval '1 microsecond'
            ELSE v.ingested_at END,
       v.revenue::bigint, v.revenue_last_month::bigint, v.revenue_last_year_month::bigint,
       v.cumulative_revenue::bigint, v.cumulative_revenue_last_year::bigint,
       v.mom_pct, v.yoy_pct, v.cumulative_yoy_pct, v.note, p.published_at, v.ingest_run_id
  FROM monthly_revenue_versions v
  JOIN security s ON s.id = v.security_id
  JOIN stocks k ON k.stock_id = s.security_code
  JOIN raw_artifact_observations o
    ON o.raw_artifact_id = v.raw_artifact_id AND o.ingest_run_id = v.ingest_run_id
  LEFT JOIN LATERAL (
        SELECT min(w.ingested_at) AS first_at
          FROM monthly_revenue_versions w
          JOIN raw_artifact_observations ow
            ON ow.raw_artifact_id = w.raw_artifact_id AND ow.ingest_run_id = w.ingest_run_id
         WHERE w.security_id = v.security_id AND w.source = v.source
           AND w.revenue_period = v.revenue_period AND w.id <> v.id
           AND ow.artifact_origin = 'official_fetch') official ON true
  LEFT JOIN publication_evidence p
    ON p.monthly_revenue_version_id = v.id AND p.evidence_kind IN ('assertion', 'correction')
"""

# The v1 filing id becomes the report id, so the facts need no lookup.
FINANCIAL_REPORTS = """
INSERT INTO financial_reports (id, stock_id, report_year, report_quarter, report_category,
                               published_at, recorded_at, fetch_id)
SELECT v.id, s.security_code, v.report_year, v.report_quarter, v.report_category,
       p.published_at, seal.ingested_at, v.ingest_run_id
  FROM financial_filing_versions v
  JOIN financial_filing_seals seal ON seal.filing_version_id = v.id
  JOIN security s ON s.id = v.security_id
  JOIN stocks k ON k.stock_id = s.security_code
  LEFT JOIN publication_evidence p
    ON p.financial_filing_version_id = v.id AND p.evidence_kind IN ('assertion', 'correction')
"""

# No stored fact has a dimension, scenario or segment; the unique key would
# refuse two facts that only a context told apart, so none is silently merged.
FINANCIAL_REPORT_FACTS = """
INSERT INTO financial_report_facts (report_id, statement, account_code, concept,
                                    period_start, period_end, unit, value)
SELECT f.filing_version_id, f.statement, f.account_code, f.concept_qname,
       CASE WHEN f.period_type = 'duration' THEN f.period_start END,
       CASE WHEN f.period_type = 'instant' THEN f.instant_date ELSE f.period_end END,
       f.unit_identity, f.numeric_value
  FROM financial_facts f
  JOIN financial_reports r ON r.id = f.filing_version_id
"""

_LEVEL = "max(d.{column}) FILTER (WHERE d.bucket_code = '{level}')"
SHAREHOLDING = f"""
INSERT INTO shareholding_distributions (
    stock_id, source, snapshot_date, recorded_at,
    {", ".join(f"holders_{n}, shares_{n}, percent_{n}" for n in range(1, 16))},
    adjustment_shares, adjustment_percent, total_holders, total_shares, total_percent,
    fetch_id)
SELECT s.security_code, v.source, v.snapshot_date, seal.ingested_at,
       {", ".join(
           f"{_LEVEL.format(column='holder_count', level=n)}, "
           f"{_LEVEL.format(column='shares', level=n)}::bigint, "
           f"{_LEVEL.format(column='ownership_percent', level=n)}"
           for n in range(1, 16))},
       {_LEVEL.format(column='shares', level=16)}::bigint,
       {_LEVEL.format(column='ownership_percent', level=16)},
       {_LEVEL.format(column='holder_count', level=17)},
       {_LEVEL.format(column='shares', level=17)}::bigint,
       {_LEVEL.format(column='ownership_percent', level=17)},
       v.ingest_run_id
  FROM tdcc_snapshot_versions v
  JOIN tdcc_snapshot_seals seal ON seal.snapshot_version_id = v.id
  JOIN security s ON s.id = v.security_id
  JOIN stocks k ON k.stock_id = s.security_code
  JOIN tdcc_distribution d ON d.snapshot_version_id = v.id
 GROUP BY v.id, s.security_code, seal.ingested_at
"""


def _single_version(connection, table: str, key: str, scope: str = "", params=None) -> None:
    """Refuse if any copied key has several v1 versions.

    Only the rows being copied count: 008201 has 612 revised margin rows (a
    unit correction on 2026-09-19), but it is not a common stock and is never
    copied.
    """
    duplicated = connection.execute(
        sa.text(f"SELECT count(*) FROM (SELECT 1 FROM {table} v {scope} GROUP BY {key} "
                "HAVING count(*) > 1) x"),
        params or {},
    ).scalar_one()
    if duplicated:
        raise SystemExit(f"{table} has {duplicated} copied keys with several versions")


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    engine = sa.create_engine(url)
    report: dict[str, dict] = {}

    def step(name: str, run) -> None:
        with engine.begin() as connection:
            if connection.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {name})")).scalar():
                report[name] = {"skipped": "already populated"}
                return
            began = time.monotonic()
            rows = run(connection)
            report[name] = {"rows": rows, "seconds": round(time.monotonic() - began, 1)}
            print(f"{name}: {rows}", file=sys.stderr, flush=True)

    step("fetches", lambda c: c.execute(sa.text(FETCHES)).rowcount)
    def load_stocks(connection):
        loaded = load_universe(connection, git_commit=current_git_commit())
        refused = {market: result for market, result in loaded.items() if isinstance(result, str)}
        if refused:
            # The raw pages are already on disk; the quarantine rows roll back
            # with this transaction, and nothing downstream may run on half a universe.
            raise SystemExit(f"ISIN list not loaded: {refused}")
        return sum(loaded.values())

    step("stocks", load_stocks)
    step("trading_days", lambda c: c.execute(sa.text(TRADING_DAYS)).rowcount)

    for target, source_table, sources, columns in PER_STOCK:
        def copy(connection, source_table=source_table, sources=sources, columns=columns,
                 target=target):
            _single_version(
                connection,
                source_table,
                "v.security_id, v.source, v.trade_date",
                "JOIN security s ON s.id = v.security_id "
                "JOIN stocks k ON k.stock_id = s.security_code WHERE v.source = ANY(:sources)",
                {"sources": list(sources)},
            )
            return connection.execute(
                sa.text(per_stock_sql(target, source_table, columns)),
                {"sources": list(sources)},
            ).rowcount
        step(target, copy)

    codes = [f"{source}:{name}" for source, names in KEPT_INDICES.items() for name in names]

    def copy_indices(connection):
        _single_version(
            connection,
            "market_index_versions",
            "v.market_index_id, v.source, v.trade_date",
            "JOIN market_index m ON m.id = v.market_index_id WHERE m.index_code = ANY(:codes)",
            {"codes": codes},
        )
        return connection.execute(sa.text(INDEX_PRICES), {"codes": codes}).rowcount

    step("index_prices", copy_indices)

    def copy_market_flows(connection):
        _single_version(connection, "institutional_market_summary_versions",
                        "source, trade_date, institution")
        return connection.execute(sa.text(MARKET_FLOWS)).rowcount

    step("institutional_market_flows", copy_market_flows)

    step("monthly_revenues", lambda c: c.execute(sa.text(MONTHLY_REVENUES)).rowcount)

    def copy_reports(connection):
        rows = connection.execute(sa.text(FINANCIAL_REPORTS)).rowcount
        connection.execute(sa.text(
            "SELECT setval(pg_get_serial_sequence('financial_reports', 'id'), "
            "(SELECT max(id) FROM financial_reports))"))
        return rows

    step("financial_reports", copy_reports)
    step("financial_report_facts", lambda c: c.execute(sa.text(FINANCIAL_REPORT_FACTS)).rowcount)
    step("shareholding_distributions", lambda c: c.execute(sa.text(SHAREHOLDING)).rowcount)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
