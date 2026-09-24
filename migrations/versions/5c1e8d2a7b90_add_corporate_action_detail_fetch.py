"""add corporate action detail fetch

Revision ID: 5c1e8d2a7b90
Revises: 76245e1b428b
Create Date: 2026-09-24

Step 35-c-3 review of #50: a TWT49U or TWTAUU row takes its prices from the
list and its terms from the event's detail page, two raw files. `fetch_id`
names the list; `detail_fetch_id` names the detail, and must be set exactly
for those two feeds. The table must hold no such row without it: a table
that does is refused before any change, since no stored value says which
detail fetch produced it.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '5c1e8d2a7b90'
down_revision: str | None = '76245e1b428b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DETAIL_FEEDS = "('twse_twt49u', 'twse_twtauu')"


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM corporate_actions "
        f"WHERE source IN {DETAIL_FEEDS}) THEN RAISE EXCEPTION "
        "'cannot upgrade: corporate_actions holds TWT49U/TWTAUU rows with no detail fetch; "
        "remove and refetch them first'; END IF; END $$"
    )
    op.add_column('corporate_actions', sa.Column('detail_fetch_id', postgresql.UUID(), nullable=True))
    op.create_foreign_key(
        op.f('fk_corporate_actions_detail_fetch_id_fetches'), 'corporate_actions', 'fetches',
        ['detail_fetch_id'], ['id'], ondelete='RESTRICT',
    )
    op.create_check_constraint(
        op.f('ck_corporate_actions_detail_fetch_for_detail_feeds'), 'corporate_actions',
        f"(detail_fetch_id IS NOT NULL) = (source IN {DETAIL_FEEDS})",
    )


def downgrade() -> None:
    # Dropping the column would cut these rows from their detail pages (§81).
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM corporate_actions WHERE detail_fetch_id IS NOT NULL) "
        "THEN RAISE EXCEPTION 'cannot downgrade: corporate_actions rows name their detail fetch'; "
        "END IF; END $$"
    )
    op.drop_constraint(
        op.f('ck_corporate_actions_detail_fetch_for_detail_feeds'), 'corporate_actions', type_='check'
    )
    op.drop_constraint(
        op.f('fk_corporate_actions_detail_fetch_id_fetches'), 'corporate_actions', type_='foreignkey'
    )
    op.drop_column('corporate_actions', 'detail_fetch_id')
