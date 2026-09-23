"""The release rules of the Step 35-c datasets whose publication a rule computes.

ADR-0027 "35-c 定案". A rule is a code constant: correcting one means adding a
version, never editing one (CLAUDE.md §32). Each rule has a Python form for a
single date and an SQL form for filtering stored rows, and the two must agree.
Monthly revenue and financial reports have no rule here: their publication
differs per issuer and is stored as `published_at`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa

TAIPEI = "Asia/Taipei"


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    version: int
    authority: str


TDCC_WEEKLY = Rule(
    "tdcc_weekly",
    1,
    # Registered in v1 as release_rules row tdcc_weekly@1, kind weekday_after_time.
    "Owner decision (ADR-0020 §3, decision 2): the first Sunday strictly after the "
    "snapshot date, 12:00 Asia/Taipei. Derived from the legacy Sunday 10:20 weekly job "
    "plus margin; TDCC publishes no release schedule (audit §4.9). No business-day shift.",
)
TDCC_WEEKDAY = 6  # Sunday, with Monday as 0
TDCC_AT = time(12, 0)

CORPORATE_ACTION_EX_DATE = Rule(
    "corporate_action_ex_date",
    1,
    "Owner decision (2026-09-23, ADR-0027 35-c): an executed event is public from "
    "00:00 Asia/Taipei on its ex-date. The current-year TWT49U, TWTAUU and revivt files "
    "already list ex-dates still to come, with the reference price computed (audit §4.10).",
)


def tdcc_available_from(snapshot_date: date) -> datetime:
    ahead = (TDCC_WEEKDAY - snapshot_date.weekday()) % 7 or 7
    local = datetime.combine(snapshot_date + timedelta(days=ahead), TDCC_AT,
                             tzinfo=ZoneInfo(TAIPEI))
    return local.astimezone(UTC)


def tdcc_available_from_sql(snapshot_date):
    day = sa.cast(snapshot_date, sa.Date)
    # isodow: Monday 1 .. Sunday 7, so the days to the next Sunday are 7 - isodow,
    # and a Sunday itself waits a whole week.
    isodow = sa.cast(sa.extract("isodow", day), sa.Integer)
    ahead = sa.case((isodow == 7, 7), else_=7 - isodow)
    local = day + ahead + sa.literal(timedelta(hours=TDCC_AT.hour, minutes=TDCC_AT.minute))
    return sa.func.timezone(TAIPEI, local)


def corporate_action_available_from(ex_date: date) -> datetime:
    return datetime.combine(ex_date, time(0, 0), tzinfo=ZoneInfo(TAIPEI)).astimezone(UTC)


def corporate_action_available_from_sql(ex_date):
    return sa.func.timezone(TAIPEI, sa.cast(sa.cast(ex_date, sa.Date), sa.DateTime))
