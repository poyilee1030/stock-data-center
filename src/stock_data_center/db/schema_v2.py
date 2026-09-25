"""Schema v2: one wide, append-only row per (stock, source, date).

ADR-0027. The v1 tables beside these are dropped domain by domain once each
domain's ingestion writes here. Rules every table below follows:

- A stock is its official code (`stock_id`); the universe is today's ISIN list
  of listed and OTC common stocks (ADR-0026).
- A row is appended only when a published value changes; the old row stays.
  `recorded_at` is the Data Center's own write instant, never caller-supplied.
- Every row names the fetch it came from; the fetch names the raw file.
- No per-row publication evidence, observations or business hashes. When a
  value became public is computed from the dataset's release rule, and for a
  corrected value from its `recorded_at`.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from stock_data_center.db.base import aware_timestamp, metadata, uuid_type
from stock_data_center.v2.concentration import METRICS as CONCENTRATION_METRICS
from stock_data_center.v2.cumulative_flow import PARTIES as CUMULATIVE_PARTIES
from stock_data_center.v2.indicators import METRIC_CODES
from stock_data_center.v2.margin_metrics import MARGIN_METRICS, SHORT_INTEREST_METRICS
from stock_data_center.v2.streaks import PARTIES
from stock_data_center.v2.valuation import METRICS as VALUATION_METRICS

# Decimal columns are unconstrained `numeric` with a CHECK, not `numeric(p, 2)`:
# PostgreSQL casts to a column's typmod before any CHECK runs, so `numeric(10, 2)`
# silently rounds a published 30.555 to 30.56. Stored values must be exactly what
# the source published (CLAUDE.md §72), so a third decimal is refused instead.
# Significant decimals are what count: 30.500000 is 30.5, and trim_scale says so.
# (integer digits, decimal places), from the 2020-2026 maxima: price 19,880,
# index close 420,878, PE 24,950, ratios 100.00.
PRICE = (8, 2)
INDEX_VALUE = (10, 2)
PERCENT = (6, 2)
RATIO = (4, 2)


def _decimal(name: str) -> sa.Column:
    """An exact decimal column; its precision is enforced by `_decimal_checks`."""
    return sa.Column(name, sa.Numeric())


def _decimal_checks(table_columns: dict[str, tuple[int, int]]) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(
            f"{column} IS NULL OR (scale(trim_scale({column})) <= {places} "
            f"AND abs({column}) < 1e{digits})",
            name=f"{column}_precision",
            info={"precision": (column, digits, places)},
        )
        for column, (digits, places) in table_columns.items()
    ]


def precision(table: sa.Table) -> dict[str, tuple[int, int]]:
    """Each decimal column's (integer digits, decimal places), as its CHECK
    enforces them, so a writer can refuse a value before the INSERT does."""
    return {
        column: (digits, places)
        for constraint in table.constraints
        if (spec := constraint.info.get("precision"))
        for column, digits, places in (spec,)
    }


def _recorded_at() -> sa.Column:
    return sa.Column(
        "recorded_at",
        aware_timestamp,
        nullable=False,
        server_default=sa.text("statement_timestamp()"),
    )


def _fetch_id() -> sa.Column:
    return sa.Column(
        "fetch_id",
        uuid_type,
        sa.ForeignKey("fetches.id", ondelete="RESTRICT"),
        nullable=False,
    )


def _stock_id() -> sa.Column:
    return sa.Column(
        "stock_id",
        sa.String(6),
        sa.ForeignKey("stocks.stock_id", ondelete="RESTRICT"),
        nullable=False,
    )


def _nonnegative(*columns: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(f"{column} IS NULL OR {column} >= 0", name=f"{column}_nonnegative")
        for column in columns
    ]


# ---------------------------------------------------------------- foundation

fetches = sa.Table(
    "fetches",
    metadata,
    sa.Column(
        "id", uuid_type, primary_key=True, server_default=sa.text("gen_random_uuid()")
    ),
    sa.Column("dataset", sa.String(64), nullable=False),
    sa.Column("source", sa.String(32), nullable=False),
    sa.Column("resource_key", sa.Text(), nullable=False),
    sa.Column("source_uri", sa.Text()),
    sa.Column("purpose", sa.String(16), nullable=False),
    sa.Column("adapter_version", sa.String(64), nullable=False),
    sa.Column("git_commit", sa.String(64)),
    sa.Column("fetched_at", aware_timestamp, nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("reason_code", sa.String(64)),
    sa.Column("reason_detail", sa.Text()),
    sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
    # The raw file: stored content-addressed at data/raw/<ab>/<hex sha256>.
    sa.Column("sha256", sa.LargeBinary()),
    sa.Column("byte_size", sa.BigInteger()),
    sa.CheckConstraint(
        "purpose IN ('first_capture', 'gap_fill', 'correction_check', 'unspecified')",
        name="purpose_value",
    ),
    sa.CheckConstraint(
        "status IN ('succeeded', 'empty', 'failed', 'quarantined')", name="status_value"
    ),
    sa.CheckConstraint("sha256 IS NULL OR length(sha256) = 32", name="sha256_length"),
    sa.CheckConstraint(
        "status <> 'succeeded' OR sha256 IS NOT NULL", name="success_has_raw_file"
    ),
    sa.CheckConstraint(
        "(sha256 IS NULL) = (byte_size IS NULL)", name="raw_file_described"
    ),
    sa.CheckConstraint("attempt > 0", name="attempt_positive"),
)
sa.Index(
    "ix_fetches_resource",
    fetches.c.dataset,
    fetches.c.source,
    fetches.c.resource_key,
    fetches.c.fetched_at,
)

stocks = sa.Table(
    "stocks",
    metadata,
    sa.Column("stock_id", sa.String(6), primary_key=True),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("market", sa.String(3), nullable=False),
    sa.Column("industry", sa.Text()),
    sa.Column("listed_on", sa.Date()),
    _fetch_id(),
    sa.CheckConstraint("market IN ('sii', 'otc')", name="market_value"),
    sa.CheckConstraint("btrim(stock_id) <> ''", name="stock_id_nonempty"),
)

trading_days = sa.Table(
    "trading_days",
    metadata,
    sa.Column("trade_date", sa.Date(), primary_key=True),
    _fetch_id(),
)


# ---------------------------------------------------------------- exchange daily


def _daily_key() -> list[sa.Column]:
    return [
        _stock_id(),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        _recorded_at(),
    ]


def _daily_pk(table: str) -> sa.PrimaryKeyConstraint:
    return sa.PrimaryKeyConstraint(
        "stock_id", "source", "trade_date", "recorded_at", name=f"pk_{table}"
    )


daily_prices = sa.Table(
    "daily_prices",
    metadata,
    *_daily_key(),
    _decimal("open_price"),
    _decimal("high_price"),
    _decimal("low_price"),
    _decimal("close_price"),
    sa.Column("volume", sa.BigInteger()),
    sa.Column("trade_value", sa.BigInteger()),
    sa.Column("trade_count", sa.Integer()),
    _decimal("price_change"),
    sa.Column("price_direction", sa.String(4)),
    _decimal("last_bid_price"),
    _decimal("last_ask_price"),
    sa.Column("last_bid_volume", sa.BigInteger()),
    sa.Column("last_ask_volume", sa.BigInteger()),
    _fetch_id(),
    _daily_pk("daily_prices"),
    *_decimal_checks(
        {
            column: PRICE
            for column in (
                "open_price", "high_price", "low_price", "close_price",
                "price_change", "last_bid_price", "last_ask_price",
            )
        }
    ),
    *_nonnegative(
        "volume", "trade_value", "trade_count", "last_bid_volume", "last_ask_volume"
    ),
)

index_prices = sa.Table(
    "index_prices",
    metadata,
    sa.Column("source", sa.String(32), nullable=False),
    sa.Column("index_name", sa.Text(), nullable=False),
    sa.Column("trade_date", sa.Date(), nullable=False),
    _recorded_at(),
    _decimal("open_value"),
    _decimal("high_value"),
    _decimal("low_value"),
    _decimal("close_value"),
    _decimal("change_points"),
    _decimal("change_percent"),
    _fetch_id(),
    sa.PrimaryKeyConstraint(
        "source", "index_name", "trade_date", "recorded_at", name="pk_index_prices"
    ),
    *_decimal_checks(
        {
            **{
                column: INDEX_VALUE
                for column in (
                    "open_value", "high_value", "low_value", "close_value", "change_points"
                )
            },
            "change_percent": PERCENT,
        }
    ),
)

valuations = sa.Table(
    "valuations",
    metadata,
    *_daily_key(),
    _decimal("pe_ratio"),
    _decimal("pb_ratio"),
    _decimal("dividend_yield"),
    sa.Column("dividend_year", sa.SmallInteger()),
    sa.Column("report_period", sa.String(8)),
    _fetch_id(),
    _daily_pk("valuations"),
    *_decimal_checks({"pe_ratio": PRICE, "pb_ratio": PRICE, "dividend_yield": PERCENT}),
)

institutional_flows = sa.Table(
    "institutional_flows",
    metadata,
    *_daily_key(),
    *(
        sa.Column(f"{party}_{side}", sa.BigInteger())
        for party in ("foreign", "foreign_dealer", "trust", "dealer_self", "dealer_hedge")
        for side in ("buy", "sell", "net")
    ),
    sa.Column("dealer_net", sa.BigInteger()),
    sa.Column("total_net", sa.BigInteger()),
    _fetch_id(),
    _daily_pk("institutional_flows"),
)

institutional_market_flows = sa.Table(
    "institutional_market_flows",
    metadata,
    sa.Column("source", sa.String(32), nullable=False),
    sa.Column("trade_date", sa.Date(), nullable=False),
    sa.Column("institution", sa.Text(), nullable=False),
    _recorded_at(),
    sa.Column("buy", sa.BigInteger()),
    sa.Column("sell", sa.BigInteger()),
    sa.Column("net", sa.BigInteger()),
    _fetch_id(),
    sa.PrimaryKeyConstraint(
        "source",
        "trade_date",
        "institution",
        "recorded_at",
        name="pk_institutional_market_flows",
    ),
)

foreign_holdings = sa.Table(
    "foreign_holdings",
    metadata,
    *_daily_key(),
    sa.Column("issued_shares", sa.BigInteger()),
    sa.Column("investable_shares", sa.BigInteger()),
    sa.Column("held_shares", sa.BigInteger()),
    _decimal("investable_ratio"),
    _decimal("held_ratio"),
    _decimal("foreign_legal_limit_ratio"),
    _fetch_id(),
    _daily_pk("foreign_holdings"),
    *_decimal_checks(
        {
            column: RATIO
            for column in ("investable_ratio", "held_ratio", "foreign_legal_limit_ratio")
        }
    ),
)

margin_trading = sa.Table(
    "margin_trading",
    metadata,
    *_daily_key(),
    sa.Column("margin_buy", sa.BigInteger()),
    sa.Column("margin_sell", sa.BigInteger()),
    sa.Column("margin_cash_repayment", sa.BigInteger()),
    sa.Column("margin_previous_balance", sa.BigInteger()),
    sa.Column("margin_balance", sa.BigInteger()),
    sa.Column("margin_limit", sa.BigInteger()),
    sa.Column("short_buy", sa.BigInteger()),
    sa.Column("short_sell", sa.BigInteger()),
    sa.Column("short_stock_repayment", sa.BigInteger()),
    sa.Column("short_previous_balance", sa.BigInteger()),
    sa.Column("short_balance", sa.BigInteger()),
    sa.Column("short_limit", sa.BigInteger()),
    sa.Column("offset_balance", sa.BigInteger()),
    _fetch_id(),
    _daily_pk("margin_trading"),
)

securities_lending = sa.Table(
    "securities_lending",
    metadata,
    *_daily_key(),
    sa.Column("previous_balance", sa.BigInteger()),
    sa.Column("sold", sa.BigInteger()),
    sa.Column("returned", sa.BigInteger()),
    sa.Column("adjustment", sa.BigInteger()),
    sa.Column("balance", sa.BigInteger()),
    sa.Column("next_limit", sa.BigInteger()),
    sa.Column("next_available_limit", sa.BigInteger()),
    _fetch_id(),
    _daily_pk("securities_lending"),
)

# ---------------------------------------------------------------- issuer and TDCC
# ADR-0027 "35-c 定案". Monthly revenue and financial reports are filed by each
# issuer on its own day, so their first row stores `published_at` (NULL: nothing
# proves when it was public, so no client sees it). TDCC and corporate actions
# follow release rules (`stock_data_center.v2.release_rules`) and store none.
# (integer digits, decimal places), from the 2020-2026 maxima.
GROWTH_PERCENT = (8, 2)  # yoy 25,266,600.00
HOLDING_PERCENT = (3, 2)  # TDCC totals reach 135.00
FACT_VALUE = (14, 2)  # 9,375,654,727,000 TWD; EPS 275.06
PER_SHARE = (4, 8)  # cash dividend 144.39154400
RIGHTS_VALUE = (8, 6)  # 權值+息值 5,185.002642, signed
SHARE_RATIO = (2, 12)  # 3.157029360970 shares per share
SHARE_COUNT = (5, 8)  # 992.03164 new shares per 1,000

monthly_revenues = sa.Table(
    "monthly_revenues",
    metadata,
    _stock_id(),
    sa.Column("source", sa.String(32), nullable=False),
    sa.Column("revenue_month", sa.Date(), nullable=False),
    _recorded_at(),
    # TWD: the source's thousands times 1,000, which leaves no fraction.
    sa.Column("revenue", sa.BigInteger(), nullable=False),
    sa.Column("revenue_last_month", sa.BigInteger()),
    sa.Column("revenue_last_year_month", sa.BigInteger()),
    sa.Column("cumulative_revenue", sa.BigInteger()),
    sa.Column("cumulative_revenue_last_year", sa.BigInteger()),
    _decimal("mom_pct"),
    _decimal("yoy_pct"),
    _decimal("cumulative_yoy_pct"),
    sa.Column("note", sa.Text()),
    sa.Column("published_at", aware_timestamp),
    _fetch_id(),
    sa.PrimaryKeyConstraint(
        "stock_id", "source", "revenue_month", "recorded_at", name="pk_monthly_revenues"
    ),
    sa.CheckConstraint(
        "revenue_month = date_trunc('month', revenue_month)::date", name="revenue_month_first_day"
    ),
    *_decimal_checks(
        {column: GROWTH_PERCENT for column in ("mom_pct", "yoy_pct", "cumulative_yoy_pct")}
    ),
)

# One row per report version. A version is the whole report: a refiled report
# with any fact changed is a new row carrying its full set of facts, which is
# how a fact dropped by a restatement stays representable.
financial_reports = sa.Table(
    "financial_reports",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    _stock_id(),
    sa.Column("report_year", sa.SmallInteger(), nullable=False),
    sa.Column("report_quarter", sa.SmallInteger(), nullable=False),
    sa.Column("report_category", sa.String(12), nullable=False),
    sa.Column("published_at", aware_timestamp),
    _recorded_at(),
    _fetch_id(),
    sa.UniqueConstraint(
        "stock_id", "report_year", "report_quarter", "recorded_at",
        name="uq_financial_reports_version",
    ),
    sa.CheckConstraint("report_quarter BETWEEN 1 AND 4", name="report_quarter_range"),
    sa.CheckConstraint(
        "report_category IN ('consolidated', 'individual')", name="report_category_value"
    ),
)

# A fact's identity is its statement, concept and period; no stored fact has a
# dimension, so a document with one is quarantined (ADR-0027, overriding
# CLAUDE.md §33). An instant has no start.
financial_report_facts = sa.Table(
    "financial_report_facts",
    metadata,
    sa.Column(
        "report_id",
        sa.BigInteger(),
        sa.ForeignKey("financial_reports.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("statement", sa.String(16), nullable=False),
    sa.Column("account_code", sa.String(16), nullable=False),
    sa.Column("concept", sa.Text(), nullable=False),
    sa.Column("period_start", sa.Date()),
    sa.Column("period_end", sa.Date(), nullable=False),
    sa.Column("unit", sa.String(32), nullable=False),
    _decimal("value"),
    sa.UniqueConstraint(
        "report_id", "statement", "concept", "period_start", "period_end",
        name="uq_financial_report_facts_identity",
        postgresql_nulls_not_distinct=True,
    ),
    sa.CheckConstraint(
        "statement IN ('balance_sheet', 'income_statement', 'cash_flow')",
        name="statement_value",
    ),
    sa.CheckConstraint(
        "period_start IS NULL OR period_start <= period_end", name="period_order"
    ),
    sa.CheckConstraint("concept ~ '^\\{[^{}]+\\}[^{}]+$'", name="concept_qname"),
    sa.CheckConstraint("value IS NOT NULL", name="value_present"),
    *_decimal_checks({"value": FACT_VALUE}),
)

shareholding_distributions = sa.Table(
    "shareholding_distributions",
    metadata,
    *_daily_key()[:2],
    sa.Column("snapshot_date", sa.Date(), nullable=False),
    _recorded_at(),
    # Levels 1-15 are holding ranges (`stock_data_center.v2.tdcc`), 16 is TDCC's
    # signed reconciliation difference with no holder count, 17 its total.
    *(
        column
        for level in range(1, 16)
        for column in (
            sa.Column(f"holders_{level}", sa.BigInteger()),
            sa.Column(f"shares_{level}", sa.BigInteger()),
            _decimal(f"percent_{level}"),
        )
    ),
    sa.Column("adjustment_shares", sa.BigInteger()),
    _decimal("adjustment_percent"),
    sa.Column("total_holders", sa.BigInteger()),
    sa.Column("total_shares", sa.BigInteger()),
    _decimal("total_percent"),
    _fetch_id(),
    sa.PrimaryKeyConstraint(
        "stock_id", "source", "snapshot_date", "recorded_at",
        name="pk_shareholding_distributions",
    ),
    *_decimal_checks(
        {
            column: HOLDING_PERCENT
            for column in (
                *(f"percent_{level}" for level in range(1, 16)),
                "adjustment_percent",
                "total_percent",
            )
        }
    ),
    *_nonnegative(
        *(f"{kind}_{level}" for level in range(1, 16) for kind in ("holders", "shares")),
        "total_holders", "total_shares",
    ),
)

# One row per executed event: the key is the event's identity, the feed plus its
# execution date (CLAUDE.md §51.5). A row the feed drops is retracted by a new
# row with `retracted`, never deleted.
corporate_actions = sa.Table(
    "corporate_actions",
    metadata,
    *_daily_key()[:2],
    sa.Column("ex_date", sa.Date(), nullable=False),
    _recorded_at(),
    sa.Column("event_type", sa.Text(), nullable=False),
    _decimal("close_before"),
    _decimal("reference_price"),
    _decimal("rights_dividend_value"),
    _decimal("cash_dividend_per_share"),
    _decimal("free_share_ratio"),
    _decimal("rights_ratio"),
    _decimal("subscription_price"),
    _decimal("old_shares"),
    _decimal("new_shares"),
    _decimal("cash_return_per_share"),
    sa.Column("retracted", sa.Boolean(), nullable=False, server_default=sa.false()),
    _fetch_id(),
    # TWT49U and TWTAUU publish a row's terms only on its detail page, a second
    # raw file: `fetch_id` is the list, this the detail. NULL for the other feeds.
    sa.Column(
        "detail_fetch_id", uuid_type, sa.ForeignKey("fetches.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.PrimaryKeyConstraint(
        "stock_id", "source", "ex_date", "recorded_at", name="pk_corporate_actions"
    ),
    sa.CheckConstraint("event_type <> ''", name="event_type_nonempty"),
    sa.CheckConstraint(
        "(detail_fetch_id IS NOT NULL) = (source IN ('twse_twt49u', 'twse_twtauu'))",
        name="detail_fetch_for_detail_feeds",
    ),
    sa.CheckConstraint(
        "(old_shares IS NULL) = (new_shares IS NULL)", name="share_pair"
    ),
    *_decimal_checks(
        {
            "close_before": PRICE,
            "reference_price": PRICE,
            "subscription_price": PRICE,
            "rights_dividend_value": RIGHTS_VALUE,
            "cash_dividend_per_share": PER_SHARE,
            "cash_return_per_share": PER_SHARE,
            "free_share_ratio": SHARE_RATIO,
            "rights_ratio": SHARE_RATIO,
            "old_shares": SHARE_COUNT,
            "new_shares": SHARE_COUNT,
        }
    ),
    *_nonnegative(
        "close_before", "reference_price", "subscription_price", "cash_dividend_per_share",
        "cash_return_per_share", "free_share_ratio", "rights_ratio", "old_shares", "new_shares",
    ),
)

# ---------------------------------------------------------------- derived (Step 26)
#
# One wide row per (stock, source, date), computed from the latest input rows and
# overwritten when an input is corrected (CLAUDE.md §43, §46): not history, so not
# append-only, and no per-row version, commit or lineage (§45). `computed_at` is
# the instant the run fixed its inputs: every input row recorded by then was read,
# none recorded later, so the next run starts from the rows recorded after it.


def _derived_key() -> list[sa.Column]:
    return [
        _stock_id(),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
    ]


def _computed_at() -> sa.Column:
    return sa.Column("computed_at", aware_timestamp, nullable=False)


technical_indicators = sa.Table(
    "technical_indicators",
    metadata,
    *_derived_key(),
    *(sa.Column(code, postgresql.DOUBLE_PRECISION()) for code in METRIC_CODES),
    _computed_at(),
    sa.PrimaryKeyConstraint("stock_id", "source", "trade_date",
                            name="pk_technical_indicators"),
)

institutional_streaks = sa.Table(
    "institutional_streaks",
    metadata,
    *_derived_key(),
    *(sa.Column(f"{party}_streak_days", sa.Integer(), nullable=False) for party in PARTIES),
    _computed_at(),
    sa.PrimaryKeyConstraint("stock_id", "source", "trade_date",
                            name="pk_institutional_streaks"),
)

institutional_cumulative_flow = sa.Table(
    "institutional_cumulative_flow",
    metadata,
    *_derived_key(),
    *(
        column
        for party in CUMULATIVE_PARTIES
        for column in (
            sa.Column(f"{party}_cumulative_net_shares", sa.BigInteger(), nullable=False),
            sa.Column(f"{party}_cumulative_net_ratio", postgresql.DOUBLE_PRECISION()),
        )
    ),
    _computed_at(),
    sa.PrimaryKeyConstraint("stock_id", "source", "trade_date",
                            name="pk_institutional_cumulative_flow"),
)

# Keyed by the TDCC snapshot date, which need not be a trading day.
shareholding_concentration = sa.Table(
    "shareholding_concentration",
    metadata,
    *_derived_key()[:2],
    sa.Column("snapshot_date", sa.Date(), nullable=False),
    *(
        sa.Column(metric, sa.BigInteger() if metric.endswith("_count")
                  else postgresql.DOUBLE_PRECISION())
        for metric in CONCENTRATION_METRICS
    ),
    _computed_at(),
    sa.PrimaryKeyConstraint("stock_id", "source", "snapshot_date",
                            name="pk_shareholding_concentration"),
)


def _day_metrics(name: str, metrics: tuple[str, ...]) -> sa.Table:
    """A derived table of one day's metrics: a change is a share count, the rest ratios."""
    return sa.Table(
        name,
        metadata,
        *_derived_key(),
        *(
            sa.Column(metric, sa.BigInteger() if metric.endswith("_change")
                      else postgresql.DOUBLE_PRECISION())
            for metric in metrics
        ),
        _computed_at(),
        sa.PrimaryKeyConstraint("stock_id", "source", "trade_date", name=f"pk_{name}"),
    )


margin_metrics = _day_metrics("margin_metrics", MARGIN_METRICS)
short_interest_metrics = _day_metrics("short_interest_metrics", SHORT_INTEREST_METRICS)

valuation_metrics = sa.Table(
    "valuation_metrics",
    metadata,
    *_derived_key(),
    *(sa.Column(metric, postgresql.DOUBLE_PRECISION()) for metric in VALUATION_METRICS),
    _computed_at(),
    sa.PrimaryKeyConstraint("stock_id", "source", "trade_date", name="pk_valuation_metrics"),
)

# Append-only tables: every value table and the fetch log. `stocks` is reference
# data refreshed in place from the latest list; `trading_days` is corrected in
# place when the exchange revises its calendar.
APPEND_ONLY = (
    "fetches",
    "daily_prices",
    "index_prices",
    "valuations",
    "institutional_flows",
    "institutional_market_flows",
    "foreign_holdings",
    "margin_trading",
    "securities_lending",
    "monthly_revenues",
    "financial_reports",
    "financial_report_facts",
    "shareholding_distributions",
    "corporate_actions",
)
