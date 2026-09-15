"""register the ADR-0020 release rules

Revision ID: 3c8e5f1b7a46
Revises: 2b7d4e9a1c35
Create Date: 2026-09-16

ADR-0020 §3. Each rule is versioned and cites the schedule or statute it derives
from; a rule with no authority would be an invented instant, which ROADMAP §2.4
forbids. Rules are never edited in place — correcting one means publishing a new
version — so the table rejects UPDATE and DELETE.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "3c8e5f1b7a46"
down_revision: str | None = "2b7d4e9a1c35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RULES = (
    {
        "rule_id": "monthly_revenue_statutory",
        "version": 1,
        "rule_kind": "day_of_next_month",
        "parameters": {"day": 10},
        "timezone": "Asia/Taipei",
        "business_day_shift": True,
        "authority": (
            "Statutory monthly-revenue filing deadline: the 10th of the next "
            "month. Matches the legacy synthetic publish_time for 2020M01-2026M01 "
            "(audit §7.1)."
        ),
    },
    {
        "rule_id": "financial_statements_general",
        "version": 1,
        "rule_kind": "quarter_deadline",
        "parameters": {"1": "05-15", "2": "08-15", "3": "11-15", "4": "03-31"},
        "timezone": "Asia/Taipei",
        "business_day_shift": True,
        "authority": (
            "General-industry filing deadlines. Q4 03/31 and Q1 05/15 are the "
            "statutory dates; Q2 08/15 and Q3 11/15 are one day after the "
            "statutory 08/14 and 11/14, matching the legacy capture windows and "
            "the train_eps cutoffs (audit §7.1). Financial-industry issuers have "
            "different deadlines and no rule; Step 23 excludes them from v1."
        ),
    },
    {
        "rule_id": "exchange_daily_settled",
        "version": 1,
        "rule_kind": "next_calendar_day_time",
        "parameters": {"time": "03:00"},
        "timezone": "Asia/Taipei",
        "business_day_shift": False,
        "authority": (
            "Owner decision (ADR-0020 §3, decision 1). The exchange serves "
            "same-day rows before they settle (audit §7), so no rule may resolve "
            "on the trade date. The legacy 23:30 run was incomplete on 5 of 27 "
            "observed trade dates and the 03:00 retry on 1 of 27 (audit §7.2). "
            "No business-day shift: the file exists at 03:00 whether or not that "
            "day is a trading day."
        ),
    },
    {
        "rule_id": "tdcc_weekly",
        "version": 1,
        "rule_kind": "weekday_after_time",
        "parameters": {"weekday": 6, "time": "12:00"},
        "timezone": "Asia/Taipei",
        "business_day_shift": False,
        "authority": (
            "Owner decision (ADR-0020 §3, decision 2). Derived from the legacy "
            "Sunday 10:20 weekly job plus margin. This rests on our own schedule "
            "observation, not a published TDCC release schedule — the audit found "
            "none. No business-day shift: Sunday is never a business day, and "
            "shifting would push the rule a whole week."
        ),
    },
)


IMMUTABLE = r"""
CREATE TRIGGER trg_release_rules_immutable
BEFORE UPDATE OR DELETE ON release_rules
FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
"""


def upgrade() -> None:
    op.create_table(
        "release_rules",
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("rule_kind", sa.String(length=32), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("business_day_shift", sa.Boolean(), nullable=False),
        sa.Column("authority", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("rule_id", "version", name=op.f("pk_release_rules")),
        sa.CheckConstraint(
            "rule_kind IN ('day_of_next_month', 'quarter_deadline', "
            "'next_calendar_day_time', 'weekday_after_time')",
            name=op.f("ck_release_rules_rule_kind_value"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_release_rules_version_positive")),
        sa.CheckConstraint(
            "btrim(authority) <> ''", name=op.f("ck_release_rules_authority_nonempty")
        ),
    )
    op.bulk_insert(
        sa.table(
            "release_rules",
            sa.column("rule_id", sa.String),
            sa.column("version", sa.SmallInteger),
            sa.column("rule_kind", sa.String),
            sa.column("parameters", postgresql.JSONB),
            sa.column("timezone", sa.String),
            sa.column("business_day_shift", sa.Boolean),
            sa.column("authority", sa.Text),
        ),
        list(RULES),
    )
    op.execute(IMMUTABLE)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_release_rules_immutable ON release_rules;")
    op.drop_table("release_rules")
