"""let the TDCC published total percent exceed one hundred

Revision ID: c9a4e7b21d58
Revises: b2c5f8d1a437
Create Date: 2026-09-22

Step 24-a. `tdcc_distribution.ownership_percent` was constrained to
`BETWEEN -100 AND 100`, on the assumption that a share of TDCC custody cannot
exceed the whole. The publisher disagrees: 合計 (level 17) is published above
100 in 158 rows of the archive, over 74 data dates from 2019-07-19 to
2023-10-06, for 49 securities, up to `135.00` (`00663L`, 2022-11-25). Under the
old check those 158 security-weeks could not be stored at all, although their
holder counts and share counts are unremarkable.

What the evidence supports, measured over all 1,377,971 security-weeks in the
archive (audit §4.9):

```text
holding levels 1-15   0.00 .. 100.00        never above 100
差異數調整  16         magnitude <= 35.90    stored negative (see below)
合計        17         0.00 .. 135.00        no publisher-side ceiling
```

So the ceiling moves to where the evidence puts it: the row trigger, which
knows each bucket's role, caps a holding level at 100 and leaves the total
uncapped. The table check keeps the lower bound of -100 — nothing published
approaches it, and a percentage below -100 would be a parse defect rather than
a custody figure.

The adjustment row's sign is not constrained here, and stays "may be signed"
as ADR-0012 has it. The bulk file states its magnitude unsigned while the
publisher's own portal renders it negative (`-28,164` where the file says
`28164`, verified 2026-09-11 for 0056, 00400A, 00401A and 00404A), and
`合計 = Σ levels 1-15 − 差異數調整` holds for every security-week in the
archive with no exception either way. The adapter therefore stores it
negative, but a file that one day states the sign itself is passed through
rather than refused.

Downgrade fails before mutating anything if a stored total exceeds 100:
narrowing the check under those rows would either delete published history or
leave rows no insert could reproduce (CLAUDE.md §81).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9a4e7b21d58"
down_revision: str | None = "b2c5f8d1a437"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "tdcc_distribution"
CHECK = "ck_tdcc_distribution_ownership_percent_range"

ROW_VALIDATION = """
CREATE OR REPLACE FUNCTION stockdc_validate_tdcc_distribution_row()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE role text; profile text;
BEGIN
    SELECT snapshot.distribution_schema, bucket.bucket_role
      INTO profile, role
      FROM tdcc_snapshot_versions snapshot
      LEFT JOIN tdcc_distribution_schema_buckets bucket
        ON bucket.distribution_schema = snapshot.distribution_schema
       AND bucket.bucket_code = NEW.bucket_code
     WHERE snapshot.id = NEW.snapshot_version_id;
    IF role IS NULL THEN
        RAISE EXCEPTION 'bucket % is not defined by distribution schema %',
            NEW.bucket_code, profile USING ERRCODE = '23514';
    END IF;
    IF role = 'adjustment' THEN
        -- Canonical contract: an adjustment row has no holder count.
        IF NEW.holder_count IS NOT NULL AND NEW.holder_count <> 0 THEN
            RAISE EXCEPTION 'adjustment bucket % cannot carry holder_count %',
                NEW.bucket_code, NEW.holder_count USING ERRCODE = '23514';
        END IF;
        NEW.holder_count := NULL;
    ELSIF NEW.holder_count IS NULL
       OR NEW.shares < 0
       OR NEW.ownership_percent < 0 THEN
        RAISE EXCEPTION
            '% bucket % requires holder_count and non-negative shares/percent',
            role, NEW.bucket_code USING ERRCODE = '23514';
    END IF;
    -- A holding level is a share of custody and cannot exceed all of it; the
    -- published total can and does (see this revision's docstring), so only
    -- the holding roles are capped.
    IF role = 'holding' AND NEW.ownership_percent > 100 THEN
        RAISE EXCEPTION
            'holding bucket % cannot own % percent of custody',
            NEW.bucket_code, NEW.ownership_percent USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

PREVIOUS_ROW_VALIDATION = """
CREATE OR REPLACE FUNCTION stockdc_validate_tdcc_distribution_row()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE role text; profile text;
BEGIN
    SELECT snapshot.distribution_schema, bucket.bucket_role
      INTO profile, role
      FROM tdcc_snapshot_versions snapshot
      LEFT JOIN tdcc_distribution_schema_buckets bucket
        ON bucket.distribution_schema = snapshot.distribution_schema
       AND bucket.bucket_code = NEW.bucket_code
     WHERE snapshot.id = NEW.snapshot_version_id;
    IF role IS NULL THEN
        RAISE EXCEPTION 'bucket % is not defined by distribution schema %',
            NEW.bucket_code, profile USING ERRCODE = '23514';
    END IF;
    IF role = 'adjustment' THEN
        -- Canonical contract: an adjustment row has no holder count.
        IF NEW.holder_count IS NOT NULL AND NEW.holder_count <> 0 THEN
            RAISE EXCEPTION 'adjustment bucket % cannot carry holder_count %',
                NEW.bucket_code, NEW.holder_count USING ERRCODE = '23514';
        END IF;
        NEW.holder_count := NULL;
    ELSIF NEW.holder_count IS NULL
       OR NEW.shares < 0
       OR NEW.ownership_percent < 0 THEN
        RAISE EXCEPTION
            '% bucket % requires holder_count and non-negative shares/percent',
            role, NEW.bucket_code USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

GUARD = """
DO $$
DECLARE blocking bigint;
BEGIN
    SELECT count(*) INTO blocking
      FROM tdcc_distribution
     WHERE ownership_percent > 100;
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = format(
                '%s stored distribution rows publish a percent above 100',
                blocking
            ),
            HINT = 'Narrowing the check would leave published history that no '
                   'insert could reproduce. Remove those rows deliberately '
                   'first.';
    END IF;
END $$;
"""


def upgrade() -> None:
    op.drop_constraint(op.f(CHECK), TABLE, type_="check")
    op.create_check_constraint(op.f(CHECK), TABLE, "ownership_percent >= -100")
    op.execute(sa.text(ROW_VALIDATION))


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    op.execute(sa.text(PREVIOUS_ROW_VALIDATION))
    op.drop_constraint(op.f(CHECK), TABLE, type_="check")
    op.create_check_constraint(
        op.f(CHECK), TABLE, "ownership_percent BETWEEN -100 AND 100"
    )
