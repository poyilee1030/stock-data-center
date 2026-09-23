"""Compute `technical_indicators:v1` on demand.

Nothing here writes a result. ROADMAP §17 makes on-demand computation the
default: the values are a deterministic function of daily-price versions that
are already stored with their evidence, and Step 26-a measured the stored
long-form series at 56 GB against the 2 GB of prices it is a function of.

Two questions, one history read each:

`compute` answers one PIT context — the series as a caller at that
`information_as_of` and `knowledge_as_of` would have seen it.

`rolling` answers the rolling as-of series, where observation date D is computed
at the instant D's own inputs become public. That is the series a backtest
needs: no value in it could see a price published after its own date's cutoff.

The two PIT axes stay apart (CLAUDE.md §15). `information_as_of` is the market
axis and moves with the observation date. `knowledge_as_of` is the Data
Center's own axis and is the same for the whole series, because a series
computed today is computed from the evidence recorded by today — pinning it to
each observation date would claim the Data Center had recorded, in 2020,
evidence it first stored in 2026, and would resolve to nothing at all.

The rolling series is not a single pass. A correction to an earlier trade date
that is published later changes every value after the instant it lands, and
nothing before it. So the window is cut into segments at exactly those
instants: inside a segment the visible history is fixed, and one pass produces
every row in it. With no late correction — the ordinary case — there is one
segment and one pass.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import dataset_release_rules, security
from stock_data_center.derived.definitions import (
    TECHNICAL_INDICATORS_V1,
    DerivationDefinition,
)
from stock_data_center.derived.indicators import DailyBar, technical_indicators
from stock_data_center.evidence.release_rules import ReleaseRuleService
from stock_data_center.pit import (
    HistoricalVersion,
    MarketPITContext,
    MarketPITHistory,
    PITResolver,
)
from stock_data_center.pit.source_policy import SourcePolicyResolver

INPUT_DATASET = "daily_price"


class UnknownSecurityError(LookupError):
    """Raised when a requested security code has no registered identity."""


class MissingReleaseRuleError(LookupError):
    """Raised when the input dataset declares no release rule.

    Without one there is no defensible instant at which observation date D's
    own inputs became public, and a guessed cutoff would decide the whole
    series. Refusing is the correct outcome.
    """


@dataclass(frozen=True, slots=True)
class DerivedRow:
    """One observation date's metrics, with the context and inputs behind them.

    `input_fingerprint` is the SHA-256 of the comma-separated daily-price
    version ids the date read, in trade-date order: the whole input identity in
    64 characters, and reproducible by anyone holding the same versions.
    """

    dataset_code: str
    derivation_version: str
    observation_date: date
    source: str
    information_as_of: datetime
    knowledge_as_of: datetime
    input_version_count: int
    input_fingerprint: str
    metrics: Mapping[str, float | None]


class TechnicalIndicatorService:
    def __init__(
        self,
        *,
        definition: DerivationDefinition = TECHNICAL_INDICATORS_V1,
        resolver: PITResolver | None = None,
        source_policy: SourcePolicyResolver | None = None,
        release_rules: ReleaseRuleService | None = None,
    ) -> None:
        self._definition = definition
        self._source_policy = source_policy or SourcePolicyResolver()
        self._resolver = resolver or PITResolver(source_policy=self._source_policy)
        self._release_rules = release_rules or ReleaseRuleService()

    # ------------------------------------------------------------------ cutoffs

    def cutoff(
        self, connection: Connection, *, observation_date: date, source: str
    ) -> datetime:
        """The instant observation date D's own inputs become public."""
        return self._cutoffs(
            connection, observation_dates=[observation_date], source=source
        )[observation_date]

    def _cutoffs(
        self,
        connection: Connection,
        *,
        observation_dates: Sequence[date],
        source: str,
    ) -> dict[date, datetime]:
        rule = connection.execute(
            sa.select(
                dataset_release_rules.c.rule_id, dataset_release_rules.c.version
            ).where(
                dataset_release_rules.c.dataset_code == INPUT_DATASET,
                dataset_release_rules.c.source == source,
            )
        ).mappings().one_or_none()
        if rule is None:
            raise MissingReleaseRuleError(
                f"{INPUT_DATASET} from {source!r} declares no release rule, so the "
                "rolling as-of series has no instant to compute D at"
            )
        return self._release_rules.instants_for(
            connection,
            rule_id=rule["rule_id"],
            version=int(rule["version"]),
            periods=observation_dates,
        )

    # ------------------------------------------------------------------- inputs

    def _security_id(self, connection: Connection, security_code: str) -> int:
        identity = connection.execute(
            sa.select(security.c.id).where(security.c.security_code == security_code)
        ).scalar_one_or_none()
        if identity is None:
            raise UnknownSecurityError(f"no security registered as {security_code!r}")
        return int(identity)

    def _history(
        self,
        connection: Connection,
        *,
        security_code: str,
        through: date,
        knowledge_as_of: datetime,
        source: str | None,
    ) -> MarketPITHistory:
        return self._resolver.market_history(
            connection,
            dataset_code=INPUT_DATASET,
            key_filter={"security_id": self._security_id(connection, security_code)},
            through={"trade_date": through},
            knowledge_as_of=knowledge_as_of,
            source=source,
        )

    # ------------------------------------------------------------------ compute

    def compute(
        self,
        connection: Connection,
        *,
        security_code: str,
        start_date: date,
        end_date: date,
        context: MarketPITContext,
        source: str | None = None,
    ) -> tuple[DerivedRow, ...]:
        """The series as one PIT context sees it."""
        history = self._history(
            connection,
            security_code=security_code,
            through=end_date,
            knowledge_as_of=context.knowledge_as_of,
            source=source,
        )
        wanted = [
            day
            for day in _trade_dates(history)
            if start_date <= day <= end_date
        ]
        return self._rows(
            history,
            visible=history.visible(context.information_as_of),
            observation_dates=wanted,
            information_as_of=lambda _: context.information_as_of,
        )

    def rolling(
        self,
        connection: Connection,
        *,
        security_code: str,
        start_date: date,
        end_date: date,
        source: str,
        knowledge_as_of: datetime | None = None,
    ) -> tuple[DerivedRow, ...]:
        """The rolling as-of series: each date D as the market saw it at D's cutoff.

        `knowledge_as_of` defaults to now: the evidence recorded so far. Pass it
        explicitly to reproduce a series from a stated knowledge cutoff.
        """
        history = self._history(
            connection,
            security_code=security_code,
            through=end_date,
            knowledge_as_of=knowledge_as_of or datetime.now(UTC),
            source=source,
        )
        observation_dates = [
            day
            for day in _trade_dates(history)
            if start_date <= day <= end_date
        ]
        if not observation_dates:
            return ()
        cutoffs = self._cutoffs(
            connection, observation_dates=observation_dates, source=history.source
        )
        rows: list[DerivedRow] = []
        for segment in _segments(history, observation_dates, cutoffs):
            rows.extend(
                self._rows(
                    history,
                    visible=history.visible(cutoffs[segment[-1]]),
                    observation_dates=segment,
                    information_as_of=cutoffs.__getitem__,
                )
            )
        return tuple(rows)

    # ------------------------------------------------------------------ rows

    def _rows(
        self,
        history: MarketPITHistory,
        *,
        visible: Mapping[tuple, HistoricalVersion],
        observation_dates: Sequence[date],
        information_as_of: Callable[[date], datetime],
    ) -> list[DerivedRow]:
        if not observation_dates:
            return []
        last = observation_dates[-1]
        # A day not yet public at this instant is absent from the history
        # rather than present with nothing in it: a day the market could not
        # see is not a day with an unpublished price.
        versions = sorted(
            (
                version
                for version in visible.values()
                if version.data["trade_date"] <= last
            ),
            key=lambda version: version.data["trade_date"],
        )
        bars = [
            DailyBar(
                trade_date=version.data["trade_date"],
                high=_as_float(version.data["high_price"]),
                low=_as_float(version.data["low_price"]),
                close=_as_float(version.data["close_price"]),
                volume=_as_float(version.data["volume"]),
            )
            for version in versions
        ]
        metrics = {row.trade_date: row.metrics for row in technical_indicators(bars)}

        wanted = set(observation_dates)
        digest = hashlib.sha256()
        out: list[DerivedRow] = []
        for index, version in enumerate(versions):
            digest.update(f"{',' if index else ''}{version.version_id}".encode())
            day = version.data["trade_date"]
            if day not in wanted:
                continue
            out.append(
                DerivedRow(
                    dataset_code=self._definition.dataset_code,
                    derivation_version=self._definition.derivation_version,
                    observation_date=day,
                    source=history.source,
                    information_as_of=information_as_of(day),
                    knowledge_as_of=history.knowledge_as_of,
                    input_version_count=index + 1,
                    input_fingerprint=digest.copy().hexdigest(),
                    metrics=metrics[day],
                )
            )
        return out


def _trade_dates(history: MarketPITHistory) -> list[date]:
    return sorted({version.data["trade_date"] for version in history.versions})


def _segments(
    history: MarketPITHistory,
    observation_dates: Sequence[date],
    cutoffs: Mapping[date, datetime],
) -> list[list[date]]:
    """Cut the window where the visible history of an earlier date changes.

    Observation date D joins the previous date's segment unless some version of
    a trade date at or before that previous date is published between the two
    cutoffs. Only then would one pass at the segment's last cutoff show an
    earlier member a price its own cutoff could not see. Every other instant —
    D's own price landing, above all — leaves the earlier members alone.

    This decides *where* the visible history changes, never *which* version
    wins: selection stays with the history, and so with the resolver's rule.
    """
    events = sorted(
        (published_at, key_date)
        for key_date, published_at in (
            (dict(zip(history.logical_key_columns, key))["trade_date"], instant)
            for key, instant in history.publication_instants()
        )
    )
    instants = [instant for instant, _ in events]

    segments: list[list[date]] = []
    previous: date | None = None
    for observation_date in observation_dates:
        starts_segment = previous is None
        if previous is not None:
            low = bisect_right(instants, cutoffs[previous])
            high = bisect_right(instants, cutoffs[observation_date])
            starts_segment = any(
                key_date <= previous for _, key_date in events[low:high]
            )
        if starts_segment:
            segments.append([observation_date])
        else:
            segments[-1].append(observation_date)
        previous = observation_date
    return segments


def _as_float(value) -> float | None:
    return None if value is None else float(value)


__all__ = [
    "INPUT_DATASET",
    "DerivedRow",
    "MissingReleaseRuleError",
    "TechnicalIndicatorService",
    "UnknownSecurityError",
]
