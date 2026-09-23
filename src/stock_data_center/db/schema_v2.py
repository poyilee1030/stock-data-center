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

from stock_data_center.db.metadata import aware_timestamp, metadata, uuid_type

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
        )
        for column, (digits, places) in table_columns.items()
    ]


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
)
