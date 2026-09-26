"""step 39-a industry changes

Revision ID: f1c5b643f4c7
Revises: 17d077a6bbe6
Create Date: 2026-09-26

`industry_changes` (Step 39-a, ADR-0030): one row per company an exchange
announced would change industry category. Append-only like every value table:
the baseline's trigger function refuses UPDATE, DELETE and TRUNCATE.

The downgrade drops the table only while it is empty, and refuses before
changing anything otherwise: dropping it would discard stored history
(CLAUDE.md §81).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f1c5b643f4c7'
down_revision: str | None = '17d077a6bbe6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('industry_changes',
    sa.Column('stock_id', sa.String(length=6), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('effective_date', sa.Date(), nullable=False),
    sa.Column('recorded_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('statement_timestamp()'), nullable=False),
    sa.Column('announced_on', sa.Date(), nullable=False),
    sa.Column('document_number', sa.Text(), nullable=False),
    sa.Column('old_industry', sa.Text(), nullable=False),
    sa.Column('new_industry', sa.Text(), nullable=False),
    sa.Column('fetch_id', sa.UUID(), nullable=False),
    sa.Column('attachment_fetch_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("source IN ('twse_announcement', 'tpex_announcement')", name=op.f('ck_industry_changes_source_value')),
    sa.CheckConstraint('old_industry <> new_industry', name=op.f('ck_industry_changes_category_changes')),
    sa.ForeignKeyConstraint(['attachment_fetch_id'], ['fetches.id'], name=op.f('fk_industry_changes_attachment_fetch_id_fetches'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['fetch_id'], ['fetches.id'], name=op.f('fk_industry_changes_fetch_id_fetches'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.stock_id'], name=op.f('fk_industry_changes_stock_id_stocks'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('stock_id', 'source', 'effective_date', 'recorded_at', name='pk_industry_changes')
    )
    op.execute(
        "CREATE TRIGGER immutable_industry_changes BEFORE UPDATE OR DELETE ON industry_changes "
        "FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation()"
    )
    op.execute(
        "CREATE TRIGGER no_truncate_industry_changes BEFORE TRUNCATE ON industry_changes "
        "FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM industry_changes) THEN "
        "RAISE EXCEPTION 'cannot downgrade: industry_changes holds history'; "
        "END IF; END $$"
    )
    op.drop_table('industry_changes')
