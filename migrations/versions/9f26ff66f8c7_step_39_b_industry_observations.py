"""step 39-b industry observations

Revision ID: 9f26ff66f8c7
Revises: f1c5b643f4c7
Create Date: 2026-09-26

`industry_observations` (Step 39-b, ADR-0030 §2): which category an exchange's
by-category quotes listed a stock under on a trade date. Append-only like every
value table: the baseline's trigger function refuses UPDATE, DELETE and
TRUNCATE.

The downgrade drops the table only while it is empty, and refuses before
changing anything otherwise: dropping it would discard stored history
(CLAUDE.md §81).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '9f26ff66f8c7'
down_revision: str | None = 'f1c5b643f4c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('industry_observations',
    sa.Column('stock_id', sa.String(length=6), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('recorded_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('statement_timestamp()'), nullable=False),
    sa.Column('industry_code', sa.String(length=2), nullable=False),
    sa.Column('fetch_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("industry_code ~ '^[0-3][0-9]$'", name=op.f('ck_industry_observations_industry_code_value')),
    sa.CheckConstraint("source IN ('tpex_otc_quotes', 'twse_mi_index')", name=op.f('ck_industry_observations_source_value')),
    sa.ForeignKeyConstraint(['fetch_id'], ['fetches.id'], name=op.f('fk_industry_observations_fetch_id_fetches'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.stock_id'], name=op.f('fk_industry_observations_stock_id_stocks'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('stock_id', 'source', 'trade_date', 'recorded_at', name='pk_industry_observations')
    )
    op.execute(
        "CREATE TRIGGER immutable_industry_observations BEFORE UPDATE OR DELETE "
        "ON industry_observations FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation()"
    )
    op.execute(
        "CREATE TRIGGER no_truncate_industry_observations BEFORE TRUNCATE "
        "ON industry_observations FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM industry_observations) THEN "
        "RAISE EXCEPTION 'cannot downgrade: industry_observations holds history'; "
        "END IF; END $$"
    )
    op.drop_table('industry_observations')
