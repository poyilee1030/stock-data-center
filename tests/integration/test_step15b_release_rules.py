"""Step 15-b — release rules: registered, versioned, and evaluated.

Every instant here is Asia/Taipei. A statutory deadline resolves at end of day
and moves to the next business day; a scheduled rule resolves at its stated time
and does not move (ADR-0020 §3, as corrected in this step).
"""

from __future__ import annotations

from datetime import date, datetime
from itertools import count
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.evidence import ReleaseRuleService, UnknownReleaseRuleError
from stock_data_center.market_calendar import (
    CalendarCoverageError,
    TradingCalendarObservation,
    TradingCalendarWriter,
)
from stock_data_center.market_calendar.models import CalendarLineageRef


pytestmark = pytest.mark.integration

TAIPEI = ZoneInfo("Asia/Taipei")
_DIGESTS = count(0x66000)

RULES = ReleaseRuleService()
CALENDAR = TradingCalendarWriter()


def lineage(db: Connection) -> CalendarLineageRef:
    run_id = db.execute(
        sa.text(
            "INSERT INTO ingest_runs (dataset_code, source, status, started_at) "
            "VALUES ('trading_calendar', 'twse', 'succeeded', statement_timestamp()) "
            "RETURNING id"
        )
    ).scalar_one()
    digest = f"{next(_DIGESTS):064x}"
    artifact_id = db.execute(
        sa.text(
            "INSERT INTO raw_artifacts "
            "(raw_artifact_hash, storage_uri, byte_size, media_type) "
            "VALUES (:digest, :uri, 1, 'application/json') RETURNING id"
        ),
        {"digest": digest, "uri": f"data/raw/{digest[:2]}/{digest}"},
    ).scalar_one()
    db.execute(
        sa.text(
            "INSERT INTO raw_artifact_observations "
            "(raw_artifact_id, ingest_run_id, source_uri, fetched_at) "
            "VALUES (:artifact, :run, 'https://twse.test', statement_timestamp())"
        ),
        {"artifact": artifact_id, "run": run_id},
    )
    return CalendarLineageRef(artifact_id, run_id)


def open_days(db: Connection, month: date, days: tuple[date, ...]) -> None:
    last = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    CALENDAR.append_month(
        db,
        source="twse",
        observation=TradingCalendarObservation(
            market="TWSE",
            calendar_month=month,
            trading_days=days,
            coverage_through=last.fromordinal(last.toordinal() - 1),
        ),
        lineage=lineage(db),
    )


def weekdays(year: int, month: int, *, closed: tuple[date, ...] = ()) -> tuple[date, ...]:
    """Every weekday of a month, as a stand-in trading calendar."""
    day = date(year, month, 1)
    days = []
    while day.month == month:
        if day.weekday() < 5 and day not in closed:
            days.append(day)
        day = date.fromordinal(day.toordinal() + 1)
    return tuple(days)


def taipei(year, month, day, hour=23, minute=59, second=59) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=TAIPEI)


def test_the_four_adr_rules_are_registered_with_their_authority(
    db: Connection,
) -> None:
    rows = {
        (row["rule_id"], row["version"]): row
        for row in db.execute(
            sa.text(
                "SELECT rule_id, version, rule_kind, business_day_shift, "
                "timezone, authority FROM release_rules"
            )
        ).mappings()
    }

    assert set(rows) == {
        ("monthly_revenue_statutory", 1),
        ("financial_statements_general", 1),
        ("exchange_daily_settled", 1),
        ("tdcc_weekly", 1),
    }
    for key, row in rows.items():
        assert row["timezone"] == "Asia/Taipei", key
        assert row["authority"].strip(), key
    # Statutory deadlines move off a closure; scheduled instants do not.
    assert rows[("monthly_revenue_statutory", 1)]["business_day_shift"] is True
    assert rows[("financial_statements_general", 1)]["business_day_shift"] is True
    assert rows[("exchange_daily_settled", 1)]["business_day_shift"] is False
    assert rows[("tdcc_weekly", 1)]["business_day_shift"] is False


def test_monthly_revenue_resolves_to_the_tenth_of_the_next_month(
    db: Connection,
) -> None:
    open_days(db, date(2024, 2, 1), weekdays(2024, 2))

    instant = RULES.instant_for(
        db, rule_id="monthly_revenue_statutory", version=1, period=date(2024, 1, 1)
    )

    # 2024-02-10 is a Saturday, so the deadline moves to Monday the 12th.
    assert instant == taipei(2024, 2, 12)


def test_the_adr_worked_example_for_a_weekend_revenue_deadline(
    db: Connection,
) -> None:
    """ADR-0020 §3: 2026M04 resolves to 2026-05-11, not the 10th."""
    open_days(db, date(2026, 5, 1), weekdays(2026, 5))

    instant = RULES.instant_for(
        db, rule_id="monthly_revenue_statutory", version=1, period=date(2026, 4, 1)
    )

    assert instant == taipei(2026, 5, 11)


def test_the_adr_worked_example_for_a_weekend_quarter_deadline(
    db: Connection,
) -> None:
    """ADR-0020 §3: 2021Q2 resolves to 2021-08-16, not 08-15."""
    open_days(db, date(2021, 8, 1), weekdays(2021, 8))

    instant = RULES.instant_for(
        db,
        rule_id="financial_statements_general",
        version=1,
        period=date(2021, 6, 30),
    )

    assert instant == taipei(2021, 8, 16)


@pytest.mark.parametrize(
    ("quarter_end", "month", "expected"),
    [
        (date(2024, 3, 31), 5, date(2024, 5, 15)),
        (date(2024, 6, 30), 8, date(2024, 8, 15)),
        (date(2024, 9, 30), 11, date(2024, 11, 15)),
    ],
)
def test_each_general_industry_quarter_has_its_own_deadline(
    db: Connection, quarter_end: date, month: int, expected: date
) -> None:
    open_days(db, date(2024, month, 1), weekdays(2024, month))

    instant = RULES.instant_for(
        db,
        rule_id="financial_statements_general",
        version=1,
        period=quarter_end,
    )

    assert instant.date() == expected


def test_a_fourth_quarter_deadline_falls_in_the_following_year(
    db: Connection,
) -> None:
    """Q4 2023 is due 2024-03-31, a Sunday, so it moves to 04-01."""
    open_days(db, date(2024, 3, 1), weekdays(2024, 3))
    open_days(db, date(2024, 4, 1), weekdays(2024, 4))

    instant = RULES.instant_for(
        db,
        rule_id="financial_statements_general",
        version=1,
        period=date(2023, 12, 31),
    )

    assert instant == taipei(2024, 4, 1)


def test_a_statutory_deadline_moves_off_a_market_closure_not_only_a_weekend(
    db: Connection,
) -> None:
    """The calendar decides, so a typhoon closure moves the deadline too."""
    open_days(db, date(2024, 7, 1), weekdays(2024, 7, closed=(date(2024, 7, 10),)))

    instant = RULES.instant_for(
        db, rule_id="monthly_revenue_statutory", version=1, period=date(2024, 6, 1)
    )

    assert instant == taipei(2024, 7, 11)


def test_the_exchange_rule_resolves_at_three_in_the_morning_the_next_day(
    db: Connection,
) -> None:
    """No business-day shift: the file exists whether or not the market opens."""
    instant = RULES.instant_for(
        db, rule_id="exchange_daily_settled", version=1, period=date(2024, 7, 23)
    )

    assert instant == datetime(2024, 7, 24, 3, 0, tzinfo=TAIPEI)


def test_the_exchange_rule_needs_no_calendar_at_all(db: Connection) -> None:
    """It must answer for a date the calendar has never imported."""
    instant = RULES.instant_for(
        db, rule_id="exchange_daily_settled", version=1, period=date(2031, 1, 1)
    )

    assert instant == datetime(2031, 1, 2, 3, 0, tzinfo=TAIPEI)


def test_the_tdcc_rule_resolves_at_noon_on_the_following_sunday(
    db: Connection,
) -> None:
    instant = RULES.instant_for(
        db, rule_id="tdcc_weekly", version=1, period=date(2024, 7, 24)
    )

    assert instant == datetime(2024, 7, 28, 12, 0, tzinfo=TAIPEI)


def test_a_data_date_already_on_a_sunday_moves_to_the_next_one(
    db: Connection,
) -> None:
    """"After the data date" is strict; the same day would claim same-instant."""
    instant = RULES.instant_for(
        db, rule_id="tdcc_weekly", version=1, period=date(2024, 7, 28)
    )

    assert instant == datetime(2024, 8, 4, 12, 0, tzinfo=TAIPEI)


def test_a_deadline_the_calendar_cannot_reach_raises_rather_than_guessing(
    db: Connection,
) -> None:
    """A rule that needs a business-day shift needs the calendar to say so."""
    with pytest.raises(CalendarCoverageError):
        RULES.instant_for(
            db,
            rule_id="monthly_revenue_statutory",
            version=1,
            period=date(2030, 1, 1),
        )


def test_an_unregistered_rule_or_version_is_an_error(db: Connection) -> None:
    with pytest.raises(UnknownReleaseRuleError):
        RULES.instant_for(
            db, rule_id="financial_statements_financial", version=1,
            period=date(2024, 3, 31),
        )
    with pytest.raises(UnknownReleaseRuleError):
        RULES.instant_for(
            db, rule_id="monthly_revenue_statutory", version=2,
            period=date(2024, 1, 1),
        )


def test_a_rule_is_never_edited_in_place(db: Connection) -> None:
    """ADR-0020 §3: correcting a rule means publishing a new version."""
    with pytest.raises(sa.exc.DBAPIError):
        with db.begin_nested():
            db.execute(
                sa.text(
                    "UPDATE release_rules SET parameters = '{\"day\": 25}'::jsonb "
                    "WHERE rule_id = 'monthly_revenue_statutory'"
                )
            )


def test_the_evidence_source_names_the_rule_and_its_version(db: Connection) -> None:
    open_days(db, date(2024, 2, 1), weekdays(2024, 2))

    resolved = RULES.resolve(
        db, rule_id="monthly_revenue_statutory", version=1, period=date(2024, 1, 1)
    )

    assert resolved.evidence_source == "monthly_revenue_statutory@1"
    assert resolved.published_at == taipei(2024, 2, 12)


def test_the_planned_ranks_are_the_registered_ranks(db: Connection) -> None:
    """Finding 5. The Python constants used to drift from the registry freely.

    A future ADR that changed a rank in the migration alone would have failed
    only at insert time, with no unit-level signal.
    """
    from stock_data_center.evidence import EVIDENCE_RANKS

    registered = dict(
        db.execute(
            sa.text(
                "SELECT evidence_type, quality_rank FROM evidence_types "
                "WHERE rank_is_enforced"
            )
        ).all()
    )

    assert EVIDENCE_RANKS == registered


def test_a_rule_that_could_not_be_evaluated_is_refused_at_registration(
    db: Connection,
) -> None:
    """Finding 3. "The 29th of next month" has no meaning in February."""
    with pytest.raises(sa.exc.DBAPIError):
        with db.begin_nested():
            db.execute(
                sa.text(
                    """
                    INSERT INTO release_rules
                        (rule_id, version, rule_kind, parameters, timezone,
                         business_day_shift, authority)
                    VALUES ('impossible_day', 1, 'day_of_next_month',
                            '{"day": 30}'::jsonb, 'Asia/Taipei', true, 'test')
                    """
                )
            )

    db.execute(
        sa.text(
            """
            INSERT INTO release_rules
                (rule_id, version, rule_kind, parameters, timezone,
                 business_day_shift, authority)
            VALUES ('representable_day', 1, 'day_of_next_month',
                    '{"day": 28}'::jsonb, 'Asia/Taipei', true, 'test')
            """
        )
    )


def test_release_rules_cannot_be_truncated(db: Connection) -> None:
    """Finding 4. Evidence cites a rule by string; no foreign key protects it."""
    with pytest.raises(sa.exc.DBAPIError):
        with db.begin_nested():
            db.execute(sa.text("TRUNCATE release_rules"))
