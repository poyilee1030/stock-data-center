"""let the monthly-revenue sources carry archive evidence

Revision ID: c8f1a63d5b02
Revises: b7e4c1a95d38
Create Date: 2026-09-20

Step 22-c. The two Step 22-a sources accepted `official` only, so every
version recorded `unknown`. This widens the allowlist to the types the legacy
archive proves — `press_report_bound` and `release_rule` for the recovered
announcement dates, `legacy_capture_bound` for the 22:45 job's own first
sightings — and to `capture_bound`, which a later `correction_check` run
writes when it is the first to see a corrected row (ADR-0020 §5).

No `dataset_release_rules` row is added, deliberately. Declaring
`monthly_revenue_statutory` on the source would hand the statutory instant to
every version the official importer writes, including the KY issuers on the
`_1` page that the archive says nothing about — that is the look-ahead this
step exists to avoid (owner decision, 2026-09-20). The archive importer names
the rule itself, for the rows whose recovered date is the statutory day.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8f1a63d5b02"
down_revision: str | None = "b7e4c1a95d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCES = ("mops_t21sc03_sii", "mops_t21sc03_otc")
ADDED = ("capture_bound", "legacy_capture_bound", "press_report_bound", "release_rule")


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE dataset_sources
               SET accepted_evidence_types = (
                       SELECT array_agg(DISTINCT t ORDER BY t)
                         FROM unnest(accepted_evidence_types || :added) AS t
                   )
             WHERE dataset_code = 'monthly_revenue'
               AND source IN :sources
            """
        ).bindparams(
            sa.bindparam("added", value=list(ADDED)),
            sa.bindparam("sources", value=SOURCES, expanding=True),
        )
    )


GUARD = """
DO $$
DECLARE blocking bigint;
BEGIN
    -- Evidence is append-only, so narrowing the allowlist under stored rows
    -- would not delete them: it would leave them in place and make the
    -- resolver ignore them, which is history that silently stops counting.
    SELECT count(*) INTO blocking
      FROM publication_evidence
     WHERE dataset_code = 'monthly_revenue'
       AND evidence_type IN ('capture_bound', 'legacy_capture_bound',
                             'press_report_bound', 'release_rule');
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = format(
                'monthly_revenue holds %s archive evidence rows', blocking
            ),
            HINT = 'Downgrading would keep them and stop the resolver reading '
                   'them. Remove the evidence deliberately first.';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    op.execute(
        sa.text(
            """
            UPDATE dataset_sources
               SET accepted_evidence_types = ARRAY['official']::varchar[]
             WHERE dataset_code = 'monthly_revenue'
               AND source IN :sources
            """
        ).bindparams(sa.bindparam("sources", value=SOURCES, expanding=True))
    )
