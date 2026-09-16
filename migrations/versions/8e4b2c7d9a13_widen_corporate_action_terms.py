"""widen corporate-action terms to what the result feeds publish

Revision ID: 8e4b2c7d9a13
Revises: 7a2c9e4d1b58
Create Date: 2026-09-16

Step 19-a. Two storage facts the exchange result feeds falsified:

- Share ratios are published per thousand shares with eight places —
  `202.11906001` — so the canonical ratio needs eleven. NUMERIC(24, 8) rounded
  it without complaint. The four share-ratio columns become NUMERIC(28, 12);
  the integer part keeps its sixteen digits.
- `權值+息值` is 除權息前收盤價 − 除權息參考價, and a rights issue priced above the
  close makes it negative (six rows 2020-2026). The non-negative check no
  longer covers `official_rights_dividend_value`; every other term keeps it.

The business hash is computed from each value's text, and a wider scale
changes that text, so existing revisions are rehashed in both directions — the
same treatment `7c9e2a4b6d81` gave its own column changes. Without it an
unchanged re-import would create a fake revision.

The downgrade refuses, before any mutation, while a stored term needs more
than eight places or a stored difference is negative. Rounding or dropping
either would rewrite observed history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e4b2c7d9a13"
down_revision: str | None = "7a2c9e4d1b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "corporate_action_versions"
RATIOS = (
    "earnings_stock_ratio",
    "capital_surplus_stock_ratio",
    "free_share_ratio",
    "rights_ratio",
)
CHECK = "ck_corporate_action_versions_corporate_action_values_nonnegative"
PRICES_NONNEGATIVE = (
    "(subscription_price IS NULL OR subscription_price >= 0) AND "
    "(close_before IS NULL OR close_before >= 0) AND "
    "(official_reference_price IS NULL OR official_reference_price >= 0)"
)
TERMS_NONNEGATIVE = (
    "(cash_dividend_per_share IS NULL OR cash_dividend_per_share >= 0) AND "
    "(capital_reduction_cash_return_per_share IS NULL OR "
    "capital_reduction_cash_return_per_share > 0) AND "
    "(earnings_stock_ratio IS NULL OR earnings_stock_ratio > 0) AND "
    "(capital_surplus_stock_ratio IS NULL OR capital_surplus_stock_ratio > 0) AND "
    "(free_share_ratio IS NULL OR free_share_ratio > 0) AND "
    "(old_shares IS NULL OR old_shares > 0) AND "
    "(new_shares IS NULL OR new_shares > 0) AND "
    "(rights_ratio IS NULL OR rights_ratio > 0) AND "
)
UPGRADED_CHECK = TERMS_NONNEGATIVE + PRICES_NONNEGATIVE
ORIGINAL_CHECK = (
    TERMS_NONNEGATIVE
    + PRICES_NONNEGATIVE
    + " AND (official_rights_dividend_value IS NULL OR "
    "official_rights_dividend_value >= 0)"
)

ASSERT_DOWNGRADE_REPRESENTABLE = f"""
DO $$
DECLARE
    unrepresentable_count bigint;
BEGIN
    SELECT count(*) INTO unrepresentable_count
      FROM {TABLE}
     WHERE {" OR ".join(f"{name} <> round({name}, 8)" for name in RATIOS)}
        OR official_rights_dividend_value < 0;

    IF unrepresentable_count > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade corporate-action terms',
            DETAIL = format(
                '%s revision(s) hold a share ratio finer than eight places or a '
                'negative rights+dividend value, which revision 7a2c9e4d1b58 '
                'cannot store',
                unrepresentable_count
            ),
            HINT = 'Keep revision 8e4b2c7d9a13 or newer; do not round, delete, '
                   'or collapse corporate-action history to force this downgrade.';
    END IF;
END;
$$;
"""


def _rehash() -> None:
    op.execute(f"ALTER TABLE {TABLE} DISABLE TRIGGER immutable_{TABLE}")
    op.execute(
        f"""
        UPDATE {TABLE} AS version
           SET business_content_hash = encode(
               digest(
                   convert_to(
                       (to_jsonb(version) - ARRAY[
                           'id', 'business_content_hash', 'ingested_at',
                           'raw_artifact_id', 'ingest_run_id', 'source',
                           'security_id', 'market_index_id', 'event_id',
                           'trade_date', 'effective_from'
                       ])::text,
                       'UTF8'
                   ),
                   'sha256'
               ),
               'hex'
           )
        """
    )
    op.execute(f"ALTER TABLE {TABLE} ENABLE TRIGGER immutable_{TABLE}")


def _retype(precision: int, scale: int) -> None:
    for name in RATIOS:
        op.alter_column(
            TABLE,
            name,
            type_=sa.Numeric(precision, scale),
            existing_type=sa.Numeric(),
        )


def upgrade() -> None:
    op.drop_constraint(op.f(CHECK), TABLE, type_="check")
    _retype(28, 12)
    op.create_check_constraint(op.f(CHECK), TABLE, UPGRADED_CHECK)
    _rehash()


def downgrade() -> None:
    op.execute(ASSERT_DOWNGRADE_REPRESENTABLE)
    op.drop_constraint(op.f(CHECK), TABLE, type_="check")
    _retype(24, 8)
    op.create_check_constraint(op.f(CHECK), TABLE, ORIGINAL_CHECK)
    _rehash()
