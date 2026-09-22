"""Compute and materialise `technical_indicators:v1`.

Two paths, one definition. `compute` answers a caller's own PIT context on
demand; `materialize` writes the rolling as-of series ROADMAP §17 makes the
only materialised shape, where observation date D is computed at the instant
D's own inputs become public. Both read their inputs through the PIT resolver,
so neither can see a price the other cannot.

The two PIT axes stay apart (CLAUDE.md §15). `information_as_of` is the market
axis and moves with the observation date: it is the instant D's own inputs
became public. `knowledge_as_of` is the Data Center's own axis and is the same
for the whole run, because a series computed today was computed from the
evidence recorded by today — pinning it to each observation date would claim
the Data Center had recorded, in 2020, evidence it first stored in 2026, and
would resolve to nothing at all.

The rolling series is not a single pass over the whole history. A correction to
an earlier trade date that is published later changes every value after the
instant it lands, and nothing before it. So the window is cut into segments at
exactly those instants: inside a segment the visible history is fixed, and one
pass produces every row in it. With no late correction — the ordinary case —
there is one segment and one pass.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import JSON, Connection
from sqlalchemy.dialects.postgresql import insert as pg_insert

from stock_data_center.db.metadata import (
    dataset_release_rules,
    daily_price_versions,
    derived_computation_runs,
    derived_metric_versions,
    publication_evidence,
    security,
)
from stock_data_center.derived.definitions import (
    TECHNICAL_INDICATORS_V1,
    DerivationDefinition,
    DerivationRegistry,
)
from stock_data_center.derived.indicators import (
    METRIC_CODES,
    DailyBar,
    IndicatorRow,
    technical_indicators,
)
from stock_data_center.evidence.release_rules import ReleaseRuleService
from stock_data_center.pit import MarketPITContext, PITResolver
from stock_data_center.pit.source_policy import SourcePolicyResolver

INPUT_DATASET = "daily_price"

# One statement carries at most 65,535 bind parameters, and a row here binds
# fourteen. A whole security's series is far past that — 1,600 trading days of
# 22 metrics is half a million parameters — so the insert is chunked rather
# than left to fail on the securities with the longest histories, which are
# exactly the ones that matter.
INSERT_CHUNK_ROWS = 4000


class UnknownSecurityError(LookupError):
    """Raised when a requested security code has no registered identity."""


class MissingReleaseRuleError(LookupError):
    """Raised when the input dataset declares no release rule.

    Without one there is no defensible instant at which observation date D's
    own inputs became public, and a guessed cutoff would decide the whole
    series. Refusing is the correct outcome.
    """


def _fingerprint(version_ids: Sequence[int]) -> str:
    canonical = json.dumps(
        {"daily_price_version_ids": sorted(int(value) for value in version_ids)},
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TechnicalIndicatorService:
    def __init__(
        self,
        *,
        definition: DerivationDefinition = TECHNICAL_INDICATORS_V1,
        registry: DerivationRegistry | None = None,
        resolver: PITResolver | None = None,
        source_policy: SourcePolicyResolver | None = None,
        release_rules: ReleaseRuleService | None = None,
    ) -> None:
        self._definition = definition
        self._registry = registry or DerivationRegistry()
        self._source_policy = source_policy or SourcePolicyResolver()
        self._resolver = resolver or PITResolver(source_policy=self._source_policy)
        self._release_rules = release_rules or ReleaseRuleService()

    # ------------------------------------------------------------------ cutoffs

    def cutoff(
        self, connection: Connection, *, observation_date: date, source: str
    ) -> datetime:
        """The instant observation date D's own inputs become public."""
        rule = self._rule(connection, source=source)
        return self._release_rules.instant_for(
            connection,
            rule_id=rule["rule_id"],
            version=int(rule["version"]),
            period=observation_date,
        )

    def _rule(self, connection: Connection, *, source: str):
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
        return rule

    def _cutoffs(
        self, connection: Connection, *, trade_dates: Sequence[date], source: str
    ) -> dict[date, datetime]:
        rule = self._rule(connection, source=source)
        return {
            trade_date: self._release_rules.instant_for(
                connection,
                rule_id=rule["rule_id"],
                version=int(rule["version"]),
                period=trade_date,
            )
            for trade_date in trade_dates
        }

    # ------------------------------------------------------------------- inputs

    def _security_id(self, connection: Connection, security_code: str) -> int:
        identity = connection.execute(
            sa.select(security.c.id).where(security.c.security_code == security_code)
        ).scalar_one_or_none()
        if identity is None:
            raise UnknownSecurityError(f"no security registered as {security_code!r}")
        return int(identity)

    def _trade_dates(
        self, connection: Connection, *, security_id: int, source: str, through: date
    ) -> tuple[date, ...]:
        return tuple(
            connection.scalars(
                sa.select(daily_price_versions.c.trade_date)
                .where(
                    daily_price_versions.c.security_id == security_id,
                    daily_price_versions.c.source == source,
                    daily_price_versions.c.trade_date <= through,
                )
                .distinct()
                .order_by(daily_price_versions.c.trade_date)
            )
        )

    def _visible_history(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        trade_dates: Sequence[date],
        context: MarketPITContext,
    ) -> tuple[tuple[DailyBar, ...], tuple[int, ...]]:
        bars: list[DailyBar] = []
        version_ids: list[int] = []
        for trade_date in trade_dates:
            record = self._resolver.resolve(
                connection,
                dataset_code=INPUT_DATASET,
                logical_key={"security_id": security_id, "trade_date": trade_date},
                context=context,
                source=source,
            )
            if record is None:
                # Not yet public at this instant. The day is absent from the
                # history rather than present with nothing in it: a day the
                # market could not see is not a day with an unpublished price.
                continue
            data = record.data
            bars.append(
                DailyBar(
                    trade_date=trade_date,
                    high=_as_float(data["high_price"]),
                    low=_as_float(data["low_price"]),
                    close=_as_float(data["close_price"]),
                    volume=_as_float(data["volume"]),
                )
            )
            version_ids.append(record.provenance.version_id)
        return tuple(bars), tuple(version_ids)

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
    ) -> tuple[IndicatorRow, ...]:
        """The series as one PIT context sees it, computed rather than stored."""
        security_id = self._security_id(connection, security_code)
        policy = self._source_policy.resolve(
            connection, INPUT_DATASET, context, source
        )
        trade_dates = self._trade_dates(
            connection,
            security_id=security_id,
            source=policy.source,
            through=end_date,
        )
        bars, _ = self._visible_history(
            connection,
            security_id=security_id,
            source=policy.source,
            trade_dates=trade_dates,
            context=context,
        )
        return tuple(
            row
            for row in technical_indicators(bars)
            if start_date <= row.trade_date <= end_date
        )

    # -------------------------------------------------------------- materialise

    def _late_publications(
        self, connection: Connection, *, security_id: int, source: str, through: date
    ) -> tuple[tuple[date, datetime], ...]:
        """Every (trade date, publication instant) pair the window can see.

        This decides *when* the visible history changes, never *which* version
        wins: selection stays with the resolver.
        """
        rows = connection.execute(
            sa.select(
                daily_price_versions.c.trade_date,
                publication_evidence.c.published_at,
            )
            .select_from(
                daily_price_versions.join(
                    publication_evidence,
                    sa.and_(
                        publication_evidence.c.dataset_code == INPUT_DATASET,
                        publication_evidence.c.daily_price_version_id
                        == daily_price_versions.c.id,
                    ),
                )
            )
            .where(
                daily_price_versions.c.security_id == security_id,
                daily_price_versions.c.source == source,
                daily_price_versions.c.trade_date <= through,
                publication_evidence.c.published_at.is_not(None),
            )
        ).all()
        return tuple((row[0], row[1]) for row in rows)

    def _segments(
        self,
        connection: Connection,
        *,
        security_id: int,
        source: str,
        observation_dates: Sequence[date],
        cutoffs: dict[date, datetime],
        through: date,
    ) -> list[list[date]]:
        publications = self._late_publications(
            connection, security_id=security_id, source=source, through=through
        )
        segments: list[list[date]] = []
        previous_cutoff: datetime | None = None
        for observation_date in observation_dates:
            cutoff = cutoffs[observation_date]
            starts_segment = previous_cutoff is None or any(
                trade_date < observation_date
                and previous_cutoff < published_at <= cutoff
                for trade_date, published_at in publications
            )
            if starts_segment:
                segments.append([observation_date])
            else:
                segments[-1].append(observation_date)
            previous_cutoff = cutoff
        return segments

    def materialize(
        self,
        connection: Connection,
        *,
        security_code: str,
        start_date: date,
        end_date: date,
        source: str,
        knowledge_as_of: datetime | None = None,
    ) -> int:
        """Write the rolling as-of series for one security and window.

        `knowledge_as_of` defaults to now: the evidence this run could read.
        Pass it explicitly to reproduce a run from a stated knowledge cutoff.
        """
        knowledge_as_of = knowledge_as_of or datetime.now(UTC)
        definition_id = self._registry.definition_id(connection, self._definition)
        security_id = self._security_id(connection, security_code)
        trade_dates = self._trade_dates(
            connection, security_id=security_id, source=source, through=end_date
        )
        observation_dates = [
            trade_date
            for trade_date in trade_dates
            if start_date <= trade_date <= end_date
        ]
        if not observation_dates:
            return 0

        cutoffs = self._cutoffs(connection, trade_dates=trade_dates, source=source)
        segments = self._segments(
            connection,
            security_id=security_id,
            source=source,
            observation_dates=observation_dates,
            cutoffs=cutoffs,
            through=end_date,
        )

        run_id = connection.execute(
            sa.insert(derived_computation_runs)
            .values(
                definition_id=definition_id,
                status="running",
                implementation_version=self._definition.implementation_version,
                started_at=sa.func.statement_timestamp(),
                run_metadata={
                    "security_code": security_code,
                    "source": source,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "knowledge_as_of": knowledge_as_of.isoformat(),
                    "segments": len(segments),
                },
            )
            .returning(derived_computation_runs.c.id)
        ).scalar_one()

        written = 0
        for segment in segments:
            written += self._write_segment(
                connection,
                definition_id=definition_id,
                run_id=run_id,
                security_id=security_id,
                source=source,
                segment=segment,
                trade_dates=trade_dates,
                cutoffs=cutoffs,
                knowledge_as_of=knowledge_as_of,
            )

        connection.execute(
            sa.update(derived_computation_runs)
            .where(derived_computation_runs.c.id == run_id)
            .values(status="succeeded", completed_at=sa.func.statement_timestamp())
        )
        return written

    def _write_segment(
        self,
        connection: Connection,
        *,
        definition_id: int,
        run_id,
        security_id: int,
        source: str,
        segment: Sequence[date],
        trade_dates: Sequence[date],
        cutoffs: dict[date, datetime],
        knowledge_as_of: datetime,
    ) -> int:
        segment_end = segment[-1]
        segment_cutoff = cutoffs[segment_end]
        context = MarketPITContext(
            information_as_of=segment_cutoff, knowledge_as_of=knowledge_as_of
        )
        bars, version_ids = self._visible_history(
            connection,
            security_id=security_id,
            source=source,
            trade_dates=[day for day in trade_dates if day <= segment_end],
            context=context,
        )
        rows = {row.trade_date: row for row in technical_indicators(bars)}
        # The identity of the inputs one observation date actually read: the
        # visible history up to that date, not the whole segment's.
        by_date = {bar.trade_date: index for index, bar in enumerate(bars)}

        payload = []
        for observation_date in segment:
            row = rows.get(observation_date)
            if row is None:
                continue
            used = version_ids[: by_date[observation_date] + 1]
            cutoff = cutoffs[observation_date]
            fingerprint = _fingerprint(used)
            for metric_code in METRIC_CODES:
                value = row.metrics[metric_code]
                payload.append(
                    {
                        "definition_id": definition_id,
                        "security_id": security_id,
                        "observation_date": observation_date,
                        "metric_code": metric_code,
                        "pit_mode": "market",
                        "information_as_of": cutoff,
                        "knowledge_as_of": knowledge_as_of,
                        "system_as_of": None,
                        # The fingerprint covers every input version id; the
                        # identity records what they were without repeating
                        # them. A rolling series that stored each date's whole
                        # history inline would store the history once per day
                        # it survives into — quadratic, for no extra fact.
                        "input_dataset_identity": {
                            INPUT_DATASET: {
                                "source": source,
                                "version_count": len(used),
                                "first_trade_date": bars[0].trade_date.isoformat(),
                                "last_trade_date": observation_date.isoformat(),
                                "last_version_id": int(used[-1]),
                            }
                        },
                        "input_fingerprint": fingerprint,
                        "computation_run_id": run_id,
                        "numeric_value": None if value is None else Decimal(repr(value)),
                        "text_value": None,
                        # A metric without enough history yet still has to fill
                        # exactly one value column, so "not yet" is stored as
                        # JSON null rather than as a row that does not exist.
                        # `sa.null()` is the SQL NULL; a bare Python None on a
                        # JSON column would be stored as the JSON value `null`,
                        # which is not nothing and would break the check that
                        # exactly one column is filled.
                        "json_value": JSON.NULL if value is None else sa.null(),
                    }
                )

        if not payload:
            return 0
        # Re-running a window must not append a second copy of a row it already
        # wrote, and the semantic-identity index is what decides sameness.
        written = 0
        for start in range(0, len(payload), INSERT_CHUNK_ROWS):
            # `rowcount` is unreliable for a multi-row insert that skips
            # conflicts, so count what actually landed. Only the count is kept:
            # returning the ids themselves would ship tens of thousands of them
            # back per security for nothing.
            landed = connection.execute(
                sa.select(sa.func.count()).select_from(
                    pg_insert(derived_metric_versions)
                    .values(payload[start : start + INSERT_CHUNK_ROWS])
                    .on_conflict_do_nothing()
                    .returning(derived_metric_versions.c.id)
                    .cte("inserted")
                )
            ).scalar_one()
            written += int(landed)
        return written


def _as_float(value) -> float | None:
    return None if value is None else float(value)
