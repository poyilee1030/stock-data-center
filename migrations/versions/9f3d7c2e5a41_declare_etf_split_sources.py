"""declare the ETF split / reverse-split result-feed sources

Revision ID: 9f3d7c2e5a41
Revises: 02f0a144b1fc
Create Date: 2026-09-17

Step 19-e. Three more exchange result feeds join the `corporate_action`
dataset, same shape as 02f0a144b1fc: `capture_bound` evidence only, no
release rule, no fixed schedule to cite. `twse_twtcau` has real 2020-2026
history (verified live 2026-09-17: 11 events); `tpex_etfsplitrslt` and
`tpex_etfrvsrslt` have never listed a row in that window, so their adapters
quarantine any row rather than guess an unverified detail-page schema
(source_field_audit.md's ETF-split section).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f3d7c2e5a41"
down_revision: str | None = "02f0a144b1fc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCES = ("twse_twtcau", "tpex_etfsplitrslt", "tpex_etfrvsrslt")


def upgrade() -> None:
    for source in SOURCES:
        op.execute(
            sa.text(
                """
                INSERT INTO dataset_sources
                    (dataset_code, source, supports_market_pit, supports_system_pit,
                     publication_time_quality, evidence_status,
                     accepted_evidence_types, is_canonical)
                VALUES ('corporate_action', :source, true, true, 0, 'verified',
                        ARRAY['official', 'capture_bound']::varchar[], false)
                ON CONFLICT (dataset_code, source) DO UPDATE
                   SET accepted_evidence_types = (
                           SELECT array_agg(DISTINCT t ORDER BY t)
                             FROM unnest(
                                 dataset_sources.accepted_evidence_types
                                 || EXCLUDED.accepted_evidence_types
                             ) AS t
                       )
                """
            ).bindparams(source=source)
        )


_SOURCE_LIST = "(" + ",".join(f"'{source}'" for source in SOURCES) + ")"

# Same guard shape as 02f0a144b1fc: count what the FK on
# dataset_sources(dataset_code, source) actually blocks before deleting.
GUARD = f"""
DO $$
DECLARE blocking bigint;
BEGIN
    SELECT (SELECT count(*) FROM ingest_runs
             WHERE dataset_code = 'corporate_action' AND source IN {_SOURCE_LIST})
         + (SELECT count(*) FROM import_manifests
             WHERE dataset_code = 'corporate_action' AND source IN {_SOURCE_LIST})
      INTO blocking;
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away declared ETF-split sources',
            DETAIL = format('%s ingest runs or manifests reference them', blocking),
            HINT = 'history is append-only; remove the history deliberately first';
    END IF;
END $$;
"""


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    sources = sa.bindparam("sources", value=list(SOURCES), expanding=True)
    op.execute(
        sa.text(
            "DELETE FROM dataset_sources "
            "WHERE dataset_code = 'corporate_action' AND source IN :sources"
        ).bindparams(sources)
    )
