"""harden Taiwan corporate-action contract

Revision ID: 7c9e2a4b6d81
Revises: 4d2a6f8c1e30
Create Date: 2026-09-13

Cache impact: none. Corporate-action versions remain authoritative PostgreSQL
history. The migration names source-observed terms explicitly and refuses a
downgrade when the predecessor cannot represent stored history.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c9e2a4b6d81"
down_revision: str | None = "4d2a6f8c1e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_CHECKS = (
    "ck_corporate_action_versions_action_type_value",
    "ck_corporate_action_versions_corporate_action_announcement_by_ex_date",
    "ck_corporate_action_versions_corporate_action_value_present",
    "ck_corporate_action_versions_corporate_action_values_nonnegative",
)

NEW_CHECKS = (
    "ck_corporate_action_versions_action_type_value",
    "ck_corporate_action_versions_corporate_action_announcement_by_ex_date",
    "ck_corporate_action_versions_corporate_action_value_present",
    "ck_corporate_action_versions_corporate_action_values_nonnegative",
    "ck_corporate_action_versions_corporate_action_share_pair",
    "ck_corporate_action_versions_corporate_action_free_share_components",
    "ck_corporate_action_versions_corporate_action_ex_by_record_date",
    "ck_corporate_action_versions_corporate_action_record_by_payment_date",
    "ck_corporate_action_versions_corporate_action_source_event_type_nonempty",
    "ck_corporate_action_versions_corporate_action_required_terms",
    "ck_corporate_action_versions_corporate_action_share_change_required",
    "ck_corporate_action_versions_corporate_action_share_change_direction",
    "ck_corporate_action_versions_corporate_action_unambiguous_new_type",
)

ASSERT_DOWNGRADE_REPRESENTABLE = r"""
DO $$
DECLARE
    unrepresentable_count bigint;
BEGIN
    SELECT count(*) INTO unrepresentable_count
      FROM corporate_action_versions
     WHERE action_type NOT IN (
               'cash_dividend', 'stock_dividend', 'rights', 'ex_dividend',
               'ex_right', 'capital_reduction', 'other'
           )
        OR ex_date IS NULL
        OR earnings_stock_ratio IS NOT NULL
        OR capital_surplus_stock_ratio IS NOT NULL
        OR old_shares IS NOT NULL
        OR new_shares IS NOT NULL
        OR source_event_type IS NOT NULL;

    IF unrepresentable_count > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade Taiwan corporate-action history',
            DETAIL = format(
                '%s append-only corporate-action revision(s) cannot be represented by revision 4d2a6f8c1e30',
                unrepresentable_count
            ),
            HINT = 'Keep revision 7c9e2a4b6d81 or newer; do not delete, merge, or collapse corporate-action history to force this downgrade.';
    END IF;
END;
$$;
"""


def _rehash_versions() -> None:
    op.execute(
        "ALTER TABLE corporate_action_versions DISABLE TRIGGER immutable_corporate_action_versions"
    )
    op.execute(
        r"""
        UPDATE corporate_action_versions AS version
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
    op.execute(
        "ALTER TABLE corporate_action_versions ENABLE TRIGGER immutable_corporate_action_versions"
    )


def _create_new_checks() -> None:
    table = "corporate_action_versions"
    op.create_check_constraint(
        op.f(NEW_CHECKS[0]),
        table,
        "action_type IN ('cash_dividend', 'earnings_stock_dividend', "
        "'capital_surplus_stock_dividend', 'stock_split', 'reverse_split', "
        "'rights_issue', 'capital_reduction', 'ex_dividend', 'ex_right', "
        "'ex_right_dividend', 'other', 'stock_dividend', 'rights')",
    )
    op.create_check_constraint(
        op.f(NEW_CHECKS[1]),
        table,
        "announcement_date IS NULL OR ex_date IS NULL OR announcement_date <= ex_date",
    )
    op.create_check_constraint(
        op.f(NEW_CHECKS[2]),
        table,
        "num_nonnulls(announcement_date, record_date, payment_date, "
        "cash_dividend_per_share, earnings_stock_ratio, "
        "capital_surplus_stock_ratio, free_share_ratio, old_shares, new_shares, "
        "rights_ratio, subscription_price, close_before, official_reference_price, "
        "official_rights_dividend_value, source_event_type) > 0 "
        "OR ex_date IS NOT NULL OR source_terms <> '{}'::jsonb",
    )
    op.create_check_constraint(
        op.f(NEW_CHECKS[3]),
        table,
        "(cash_dividend_per_share IS NULL OR cash_dividend_per_share >= 0) AND "
        "(earnings_stock_ratio IS NULL OR earnings_stock_ratio > 0) AND "
        "(capital_surplus_stock_ratio IS NULL OR capital_surplus_stock_ratio > 0) AND "
        "(free_share_ratio IS NULL OR free_share_ratio > 0) AND "
        "(old_shares IS NULL OR old_shares > 0) AND "
        "(new_shares IS NULL OR new_shares > 0) AND "
        "(rights_ratio IS NULL OR rights_ratio > 0) AND "
        "(subscription_price IS NULL OR subscription_price >= 0) AND "
        "(close_before IS NULL OR close_before >= 0) AND "
        "(official_reference_price IS NULL OR official_reference_price >= 0) AND "
        "(official_rights_dividend_value IS NULL OR official_rights_dividend_value >= 0)",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        op.f(NEW_CHECKS[4]), table, "(old_shares IS NULL) = (new_shares IS NULL)"
    )
    op.create_check_constraint(
        op.f(NEW_CHECKS[5]),
        table,
        "free_share_ratio IS NULL OR free_share_ratio >= "
        "coalesce(earnings_stock_ratio, 0) + coalesce(capital_surplus_stock_ratio, 0)",
    )
    op.create_check_constraint(
        op.f(NEW_CHECKS[6]),
        table,
        "record_date IS NULL OR ex_date IS NULL OR ex_date <= record_date",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        op.f(NEW_CHECKS[7]),
        table,
        "payment_date IS NULL OR record_date IS NULL OR record_date <= payment_date",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        op.f(NEW_CHECKS[8]),
        table,
        "source_event_type IS NULL OR source_event_type <> ''",
    )
    op.create_check_constraint(
        op.f(NEW_CHECKS[9]),
        table,
        "(action_type <> 'cash_dividend' OR cash_dividend_per_share > 0) AND "
        "(action_type <> 'earnings_stock_dividend' OR earnings_stock_ratio IS NOT NULL) AND "
        "(action_type <> 'capital_surplus_stock_dividend' OR capital_surplus_stock_ratio IS NOT NULL) AND "
        "(action_type <> 'rights_issue' OR rights_ratio IS NOT NULL) AND "
        "(action_type NOT IN ('ex_dividend','ex_right','ex_right_dividend') OR ex_date IS NOT NULL)",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        op.f(NEW_CHECKS[10]),
        table,
        "action_type NOT IN ('stock_split','reverse_split','capital_reduction') OR "
        "(old_shares IS NOT NULL AND new_shares IS NOT NULL AND old_shares <> new_shares)",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        op.f(NEW_CHECKS[11]),
        table,
        "(action_type <> 'stock_split' OR new_shares > old_shares) AND "
        "(action_type NOT IN ('reverse_split','capital_reduction') OR new_shares < old_shares)",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        op.f(NEW_CHECKS[12]),
        table,
        "action_type NOT IN ('stock_dividend', 'rights')",
        postgresql_not_valid=True,
    )


def _create_old_checks() -> None:
    table = "corporate_action_versions"
    op.create_check_constraint(
        op.f(OLD_CHECKS[0]),
        table,
        "action_type IN ('cash_dividend', 'stock_dividend', 'rights', "
        "'ex_dividend', 'ex_right', 'capital_reduction', 'other')",
    )
    op.create_check_constraint(
        op.f(OLD_CHECKS[1]),
        table,
        "announcement_date IS NULL OR announcement_date <= ex_date",
    )
    op.create_check_constraint(
        op.f(OLD_CHECKS[2]),
        table,
        "num_nonnulls(announcement_date, record_date, payment_date, "
        "cash_dividend_per_share, stock_dividend_ratio, rights_ratio, "
        "subscription_price, close_before, reference_price, "
        "rights_dividend_value) > 0 OR terms <> '{}'::jsonb",
    )
    op.create_check_constraint(
        op.f(OLD_CHECKS[3]),
        table,
        "(cash_dividend_per_share IS NULL OR cash_dividend_per_share >= 0) AND "
        "(stock_dividend_ratio IS NULL OR stock_dividend_ratio >= 0) AND "
        "(rights_ratio IS NULL OR rights_ratio >= 0) AND "
        "(subscription_price IS NULL OR subscription_price >= 0) AND "
        "(close_before IS NULL OR close_before >= 0) AND "
        "(reference_price IS NULL OR reference_price >= 0) AND "
        "(rights_dividend_value IS NULL OR rights_dividend_value >= 0)",
    )


def upgrade() -> None:
    table = "corporate_action_versions"
    for constraint in OLD_CHECKS:
        op.drop_constraint(op.f(constraint), table, type_="check")

    op.alter_column(table, "ex_date", existing_type=sa.Date(), nullable=True)
    op.alter_column(table, "stock_dividend_ratio", new_column_name="free_share_ratio")
    op.alter_column(
        table, "reference_price", new_column_name="official_reference_price"
    )
    op.alter_column(
        table,
        "rights_dividend_value",
        new_column_name="official_rights_dividend_value",
    )
    op.alter_column(table, "terms", new_column_name="source_terms")
    op.add_column(table, sa.Column("earnings_stock_ratio", sa.Numeric(24, 8)))
    op.add_column(table, sa.Column("capital_surplus_stock_ratio", sa.Numeric(24, 8)))
    op.add_column(table, sa.Column("old_shares", sa.Numeric(24, 8)))
    op.add_column(table, sa.Column("new_shares", sa.Numeric(24, 8)))
    op.add_column(table, sa.Column("source_event_type", sa.Text()))
    _rehash_versions()
    _create_new_checks()


def downgrade() -> None:
    op.execute(ASSERT_DOWNGRADE_REPRESENTABLE)
    table = "corporate_action_versions"
    for constraint in reversed(NEW_CHECKS):
        op.drop_constraint(op.f(constraint), table, type_="check")

    op.drop_column(table, "source_event_type")
    op.drop_column(table, "new_shares")
    op.drop_column(table, "old_shares")
    op.drop_column(table, "capital_surplus_stock_ratio")
    op.drop_column(table, "earnings_stock_ratio")
    op.alter_column(table, "source_terms", new_column_name="terms")
    op.alter_column(
        table,
        "official_rights_dividend_value",
        new_column_name="rights_dividend_value",
    )
    op.alter_column(
        table, "official_reference_price", new_column_name="reference_price"
    )
    op.alter_column(table, "free_share_ratio", new_column_name="stock_dividend_ratio")
    op.alter_column(table, "ex_date", existing_type=sa.Date(), nullable=False)
    _create_old_checks()
    _rehash_versions()
