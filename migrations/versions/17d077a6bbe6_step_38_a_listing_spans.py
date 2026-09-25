"""step 38-a listing spans

Revision ID: 17d077a6bbe6
Revises: 31e69301ca35
Create Date: 2026-09-25

`listings` (Step 38-a, ADR-0028): one row per listing span, so a company that
moved between markets or was delisted can be represented. `stocks` keeps only
identity; its `market` and `listed_on` become each stock's open span, carrying
the fetch they came from. The next refresh rebuilds the spans from the
exchanges' tables.

The downgrade puts each open span back into `stocks` and refuses, before
changing anything, while a closed span exists or a stock has no open span:
the old shape cannot hold either (CLAUDE.md §81).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '17d077a6bbe6'
down_revision: str | None = '31e69301ca35'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('listings',
    sa.Column('stock_id', sa.String(length=6), nullable=False),
    sa.Column('market', sa.String(length=3), nullable=False),
    sa.Column('listed_on', sa.Date(), nullable=True),
    sa.Column('delisted_on', sa.Date(), nullable=True),
    sa.Column('fetch_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('listed_fetch_id', postgresql.UUID(as_uuid=True), nullable=True),
    sa.Column('delisted_fetch_id', postgresql.UUID(as_uuid=True), nullable=True),
    sa.CheckConstraint("(delisted_on IS NULL) = (delisted_fetch_id IS NULL)", name=op.f('ck_listings_delisted_on_has_fetch')),
    sa.CheckConstraint("(listed_on IS NULL) = (listed_fetch_id IS NULL)", name=op.f('ck_listings_listed_on_has_fetch')),
    sa.CheckConstraint("listed_on IS NULL OR delisted_on IS NULL OR listed_on < delisted_on", name=op.f('ck_listings_span_not_empty')),
    sa.CheckConstraint("market IN ('sii', 'otc')", name=op.f('ck_listings_market_value')),
    sa.ForeignKeyConstraint(['delisted_fetch_id'], ['fetches.id'], name=op.f('fk_listings_delisted_fetch_id_fetches'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['fetch_id'], ['fetches.id'], name=op.f('fk_listings_fetch_id_fetches'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['listed_fetch_id'], ['fetches.id'], name=op.f('fk_listings_listed_fetch_id_fetches'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.stock_id'], name=op.f('fk_listings_stock_id_stocks'), ondelete='RESTRICT'),
    sa.UniqueConstraint('stock_id', 'market', 'delisted_on', name=op.f('uq_listings_stock_id'), postgresql_nulls_not_distinct=True)
    )
    op.create_index('uq_listings_open_stock_id', 'listings', ['stock_id'], unique=True, postgresql_where=sa.text('delisted_on IS NULL'))
    op.execute(
        "INSERT INTO listings (stock_id, market, listed_on, fetch_id, listed_fetch_id) "
        "SELECT stock_id, market, listed_on, fetch_id, "
        "CASE WHEN listed_on IS NULL THEN NULL ELSE fetch_id END FROM stocks"
    )
    op.drop_constraint(op.f('ck_stocks_market_value'), 'stocks', type_='check')
    op.drop_column('stocks', 'listed_on')
    op.drop_column('stocks', 'market')


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM listings WHERE delisted_on IS NOT NULL) THEN "
        "RAISE EXCEPTION 'cannot downgrade: a closed listing span has no place in stocks'; "
        "END IF; "
        "IF EXISTS (SELECT 1 FROM stocks s WHERE NOT EXISTS "
        "(SELECT 1 FROM listings l WHERE l.stock_id = s.stock_id)) THEN "
        "RAISE EXCEPTION 'cannot downgrade: a stock without an open span has no market'; "
        "END IF; END $$"
    )
    op.add_column('stocks', sa.Column('market', sa.String(length=3), nullable=True))
    op.add_column('stocks', sa.Column('listed_on', sa.Date(), nullable=True))
    op.execute(
        "UPDATE stocks s SET market = l.market, listed_on = l.listed_on "
        "FROM listings l WHERE l.stock_id = s.stock_id"
    )
    op.alter_column('stocks', 'market', nullable=False)
    op.create_check_constraint(op.f('ck_stocks_market_value'), 'stocks',
                               "market IN ('sii', 'otc')")
    op.drop_index('uq_listings_open_stock_id', table_name='listings',
                  postgresql_where=sa.text('delisted_on IS NULL'))
    op.drop_table('listings')
