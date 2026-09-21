"""let financial filings carry the archive's publication evidence

Revision ID: e1b7d4a92c63
Revises: a3f5d9c17e48
Create Date: 2026-09-21

Step 23-c. `mops_t164sb01` accepted `official` only, so every filing Step 23-b
wrote recorded `unknown`: System-PIT visible, Market-PIT invisible. This widens
the allowlist to what the legacy iXBRL archive can prove —
`legacy_capture_bound` for the daily job's own first sightings from 2025Q4, and
`release_rule` for every document a bulk run wrote — and to `capture_bound`,
which an official `first_capture` or `correction_check` run writes when it is
genuinely the first to see a filing (ADR-0020 §5).

No `dataset_release_rules` row is added, deliberately, for the same reason Step
22-c added none. `financial_statements_general@1` is the statutory deadline;
declaring it on the source would hand that instant to every version the
official importer ever writes, including filings that were filed late and that
nothing has ever proved were on time. The archive importer names the rule
itself, for the documents whose file proves no sighting.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1b7d4a92c63"
down_revision: str | None = "a3f5d9c17e48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADDED = ("capture_bound", "legacy_capture_bound", "release_rule")


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE dataset_sources
               SET accepted_evidence_types = (
                       SELECT array_agg(DISTINCT t ORDER BY t)
                         FROM unnest(accepted_evidence_types || :added) AS t
                   )
             WHERE dataset_code = 'financial_filing'
               AND source = 'mops_t164sb01'
            """
        ).bindparams(sa.bindparam("added", value=list(ADDED)))
    )


GUARD = """
DO $$
DECLARE blocking bigint;
BEGIN
    -- Evidence is append-only, so narrowing the allowlist under stored rows
    -- would not delete them: it would leave them in place and make the
    -- resolver ignore them, which is history that silently stops counting
    -- (CLAUDE.md §81).
    SELECT count(*) INTO blocking
      FROM publication_evidence
     WHERE dataset_code = 'financial_filing'
       AND evidence_type IN ('capture_bound', 'legacy_capture_bound',
                             'release_rule');
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = format(
                'financial_filing holds %s archive evidence rows', blocking
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
             WHERE dataset_code = 'financial_filing'
               AND source = 'mops_t164sb01'
            """
        )
    )
