"""declare the technical_indicators derived dataset

Revision ID: c7f4a1e08b23
Revises: d3b8c6f1a294
Create Date: 2026-09-22

Step 26-a. `derived_dataset_definitions` references `dataset_catalog`, so a
derived dataset needs its catalogue entry before any definition of it can be
registered. That is all this migration does.

No `dataset_sources` row: a derived dataset has no publisher. Its visibility is
inherited from the inputs the definition names, which have sources of their own,
and declaring a source here would invite a caller to ask for evidence that
nobody produced. For the same reason there is no release rule and no expected
coverage: the series exists exactly where `daily_price` exists.

It also adds the index that makes evidence resolution by target row usable.
`publication_evidence` had indexes for appending and for resolving a dataset's
evidence in publication order, but none for "the evidence of this one version",
which is the question every resolve asks. Ingestion never noticed: it writes
evidence, it does not look it up. A rolling as-of series asks it once per
observation date, and on the 22 million stored evidence rows that was 600 ms
each — sixteen minutes for one security, twenty-nine days for the market.
With the index it is 3 seconds per security.

The index is partial because only a small minority of evidence rows are daily
prices. It changes no semantics; the other sixteen targets keep the behaviour
they have until a step needs theirs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7f4a1e08b23"
down_revision: str | None = "d3b8c6f1a294"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATASET = "technical_indicators"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES (:dataset,
                    'canonical derived price and volume indicators on raw '
                    'official close', 'v1')
            ON CONFLICT (dataset_code) DO NOTHING
            """
        ).bindparams(dataset=DATASET)
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_publication_evidence_daily_price
                ON publication_evidence (daily_price_version_id)
             WHERE daily_price_version_id IS NOT NULL
            """
        )
    )


GUARD = """
DO $$
DECLARE blocking bigint;
BEGIN
    SELECT count(*) INTO blocking
      FROM derived_dataset_definitions WHERE dataset_code = 'technical_indicators';
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away a declared derived dataset',
            DETAIL = format('%s registered definitions reference it', blocking),
            HINT = 'a definition and its results are append-only; remove them '
                   'deliberately first';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_publication_evidence_daily_price"))
    op.execute(
        sa.text("DELETE FROM dataset_catalog WHERE dataset_code = :dataset")
        .bindparams(dataset=DATASET)
    )
