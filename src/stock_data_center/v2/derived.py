"""`technical_indicators_pit:v1`: the on-demand PIT reference (Step 35-c-4).

The stored `technical_indicators:v1` (`derived_store`, Step 26-b) follows the
latest inputs; this computes the same formula under full PIT and writes
nothing, and the two must agree bit for bit while no input has a correction
and the stored series was computed in full (CLAUDE.md §43, §46). A definition
is a code constant and the git commit stands for its implementation version
(§42).

`compute` answers one PIT context: the series as a caller at that
`information_as_of` and `knowledge_as_of` would have seen it. `rolling` is the
rolling as-of series, where observation date D is computed at the instant D's
own close becomes public, so no value in it could see a later price.

The two PIT axes stay apart (CLAUDE.md §14-15). `knowledge_as_of` keeps only
the rows the Data Center had recorded by then, and is the same for the whole
series; `information_as_of` moves with the observation date. Visibility is the
rule of `exchange_daily.visible`: a key's settled value is available from the
rule instant however late it was recorded, a correction from its own
`recorded_at`.

A correction to an earlier trade date that becomes available later changes
every value after that instant and nothing before it, so the rolling window is
cut into segments at exactly those instants: inside a segment the visible
history is fixed, and one pass computes every row in it. With no late
correction there is one segment.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import groupby

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.schema_v2 import daily_prices, stocks
from stock_data_center.v2.exchange_daily import available_from
from stock_data_center.v2.indicators import DailyBar, technical_indicators


@dataclass(frozen=True, slots=True)
class Definition:
    """What CLAUDE.md §42 asks a derived dataset to identify, as a constant."""

    dataset_code: str
    derivation_version: str
    formula_specification: str
    input_tables: tuple[str, ...]
    calendar_timezone: str
    calendar_convention: str
    price_adjustment_convention: str


TECHNICAL_INDICATORS_FORMULA = (
    "Ported from the legacy calculator so the consumers trained on those "
    "values keep reading the same series. MA and VMA over 5/10/20/60/120/240 "
    "trading days of raw official close and volume; KD from a nine-day RSV "
    "smoothed twice at alpha 1/3, with a flat window treated as the neutral "
    "50; RSI 6 and 12 as adjusted exponential means of gain and loss with "
    "com = window - 1; MACD as the 12/26 exponential difference with a "
    "9-period signal; Bollinger bands at the 20-day mean plus and minus two "
    "sample standard deviations. A window containing an unpublished price "
    "yields nothing rather than closing the gap."
)

TECHNICAL_INDICATORS_PIT_V1 = Definition(
    dataset_code="technical_indicators_pit",
    derivation_version="v1",
    formula_specification=TECHNICAL_INDICATORS_FORMULA,
    input_tables=("daily_prices",),
    calendar_timezone="Asia/Taipei",
    calendar_convention=(
        "Trading days of the stock's own daily-price history from one source, in "
        "order. The rolling as-of series computes observation date D at the "
        "instant D's own prices become public under exchange_daily_settled@1 "
        "(03:00 Asia/Taipei on D+1), which is why D's own close is part of D's "
        "value and D+1's is not."
    ),
    price_adjustment_convention=(
        "raw_official_close: no corporate-action adjustment. Legacy computed "
        "these on raw prices and its consumers were trained that way; an "
        "adjusted variant is a later derivation version, not a silent change."
    ),
)


class UnknownStockError(LookupError):
    """The stock is not on today's list (ADR-0026)."""


@dataclass(frozen=True, slots=True)
class PriceRow:
    trade_date: date
    recorded_at: datetime
    available_at: datetime
    high: float | None
    low: float | None
    close: float | None
    volume: float | None


@dataclass(frozen=True, slots=True)
class History:
    """Every row of one stock's series from one source recorded by `knowledge_as_of`."""

    stock_id: str
    source: str
    knowledge_as_of: datetime
    rows: tuple[PriceRow, ...]  # by trade date, then recorded_at

    def visible(self, information_as_of: datetime) -> list[PriceRow]:
        """Each trade date's row as the market could know it, by trade date."""
        latest: dict[date, PriceRow] = {}
        for row in self.rows:
            if row.available_at <= information_as_of:
                latest[row.trade_date] = row  # rows are in recorded order per date
        return [latest[day] for day in sorted(latest)]


def _available(recorded: Sequence[datetime], released: datetime) -> list[datetime]:
    """`exchange_daily.visible`'s rule for one key's rows, in recorded order.

    The first row recorded at or after the rule instant is the settled value
    and every row before it provisional: all are available from the instant.
    A key's first row is therefore always available at its own rule instant;
    only a correction, recorded after the settled row, waits for its
    `recorded_at`."""
    settled = next((at for at in recorded if at >= released), None)
    return [released if at < released or at == settled else at for at in recorded]


def _float(value) -> float | None:
    return None if value is None else float(value)


def history(connection: Connection, *, stock_id: str, source: str, through: date,
            knowledge_as_of: datetime) -> History:
    """One read of the rows a series through `through` may use."""
    t = daily_prices.c
    stored = connection.execute(
        sa.select(t.trade_date, t.recorded_at, t.high_price, t.low_price, t.close_price,
                  t.volume)
        .where(t.stock_id == stock_id, t.source == source, t.trade_date <= through,
               t.recorded_at <= knowledge_as_of)
        .order_by(t.trade_date, t.recorded_at)
    ).all()
    rows: list[PriceRow] = []
    for day, key_rows in groupby(stored, key=lambda r: r.trade_date):
        key_rows = list(key_rows)
        available = _available([r.recorded_at for r in key_rows], available_from(day))
        rows.extend(
            PriceRow(day, r.recorded_at, at, _float(r.high_price), _float(r.low_price),
                     _float(r.close_price), _float(r.volume))
            for r, at in zip(key_rows, available, strict=True)
        )
    return History(stock_id, source, knowledge_as_of, tuple(rows))


@dataclass(frozen=True, slots=True)
class DerivedRow:
    """One observation date's metrics, with the context and inputs behind them.

    `input_fingerprint` is the SHA-256 of the comma-separated
    `<trade_date>@<recorded_at in UTC>` of the rows the date read, in trade-date
    order: those identify each input row of `daily_prices` for this stock and
    source, whatever time zone the session reads timestamps in.
    """

    dataset_code: str
    derivation_version: str
    observation_date: date
    stock_id: str
    source: str
    information_as_of: datetime
    knowledge_as_of: datetime
    input_count: int
    input_fingerprint: str
    git_commit: str
    metrics: Mapping[str, float | None]


class TechnicalIndicators:
    def __init__(self, *, git_commit: str | None = None,
                 definition: Definition = TECHNICAL_INDICATORS_PIT_V1) -> None:
        if git_commit is None:
            from stock_data_center.v2.fetch_log import current_git_commit

            git_commit = current_git_commit()
        self._git_commit = git_commit
        self._definition = definition

    @property
    def git_commit(self) -> str:
        return self._git_commit

    def _history(self, connection, *, stock_id, source, through, knowledge_as_of) -> History:
        if connection.scalar(sa.select(stocks.c.stock_id).where(stocks.c.stock_id == stock_id)) \
                is None:
            raise UnknownStockError(f"{stock_id!r} is not on today's list")
        if source is None:
            sources = connection.scalars(
                sa.select(daily_prices.c.source).distinct()
                .where(daily_prices.c.stock_id == stock_id, daily_prices.c.trade_date <= through,
                       daily_prices.c.recorded_at <= knowledge_as_of)
                .order_by(daily_prices.c.source)
            ).all()
            if len(sources) > 1:
                # CLAUDE.md §30: one series per source, never joined.
                raise ValueError(f"{stock_id} has prices from {', '.join(sources)}; "
                                 "name the source")
            if not sources:
                return History(stock_id, "", knowledge_as_of, ())
            (source,) = sources
        return history(connection, stock_id=stock_id, source=source, through=through,
                       knowledge_as_of=knowledge_as_of)

    def compute(self, connection: Connection, *, stock_id: str, start_date: date,
                end_date: date, information_as_of: datetime, knowledge_as_of: datetime,
                source: str | None = None) -> tuple[DerivedRow, ...]:
        """The series as one PIT context sees it."""
        rows = self._history(connection, stock_id=stock_id, source=source, through=end_date,
                             knowledge_as_of=knowledge_as_of)
        visible = rows.visible(information_as_of)
        wanted = [row.trade_date for row in visible if start_date <= row.trade_date]
        return tuple(self._rows(rows, visible, wanted, lambda _: information_as_of))

    def rolling(self, connection: Connection, *, stock_id: str, start_date: date,
                end_date: date, source: str | None = None,
                knowledge_as_of: datetime | None = None) -> tuple[DerivedRow, ...]:
        """Each date D as the market saw it at D's own release instant.

        `knowledge_as_of` defaults to now: the rows recorded so far. Pass it to
        reproduce a series from a stated knowledge cutoff.
        """
        rows = self._history(connection, stock_id=stock_id, source=source, through=end_date,
                             knowledge_as_of=knowledge_as_of or datetime.now(UTC))
        dates = sorted({row.trade_date for row in rows.rows
                        if start_date <= row.trade_date <= end_date})
        out: list[DerivedRow] = []
        for segment in _segments(rows, dates):
            # Every date's own first row is available at its own cutoff, so
            # each date of the segment has a value.
            visible = rows.visible(available_from(segment[-1]))
            out.extend(self._rows(rows, visible, segment, available_from))
        return tuple(out)

    def _rows(self, history_: History, visible: list[PriceRow], wanted: Sequence[date],
              information_as_of: Callable[[date], datetime]) -> list[DerivedRow]:
        if not wanted:
            return []
        last = wanted[-1]
        inputs = [row for row in visible if row.trade_date <= last]
        metrics = {
            row.trade_date: row.metrics
            for row in technical_indicators([
                DailyBar(trade_date=row.trade_date, high=row.high, low=row.low,
                         close=row.close, volume=row.volume)
                for row in inputs
            ])
        }
        wanted_set = set(wanted)
        digest = hashlib.sha256()
        out: list[DerivedRow] = []
        for index, row in enumerate(inputs):
            digest.update(
                f"{',' if index else ''}{row.trade_date.isoformat()}@"
                f"{row.recorded_at.astimezone(UTC).isoformat()}".encode()
            )
            if row.trade_date not in wanted_set:
                continue
            out.append(DerivedRow(
                dataset_code=self._definition.dataset_code,
                derivation_version=self._definition.derivation_version,
                observation_date=row.trade_date,
                stock_id=history_.stock_id,
                source=history_.source,
                information_as_of=information_as_of(row.trade_date),
                knowledge_as_of=history_.knowledge_as_of,
                input_count=index + 1,
                input_fingerprint=digest.copy().hexdigest(),
                git_commit=self._git_commit,
                metrics=metrics[row.trade_date],
            ))
        return out


def _segments(history_: History, dates: Sequence[date]) -> list[list[date]]:
    """Cut the window where the visible history of an earlier date changes.

    Date D joins the previous date's segment unless some row of a trade date at
    or before that previous date becomes available between the two cutoffs;
    only then would one pass at the segment's last cutoff show an earlier
    member a value its own cutoff could not see.
    """
    events = sorted((row.available_at, row.trade_date) for row in history_.rows)
    instants = [instant for instant, _ in events]
    segments: list[list[date]] = []
    previous: date | None = None
    for day in dates:
        starts = previous is None
        if previous is not None:
            low = bisect_right(instants, available_from(previous))
            high = bisect_right(instants, available_from(day))
            starts = any(key_date <= previous for _, key_date in events[low:high])
        if starts:
            segments.append([day])
        else:
            segments[-1].append(day)
        previous = day
    return segments
