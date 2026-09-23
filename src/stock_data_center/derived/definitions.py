"""Derivation definitions and their idempotent registration.

A definition is the whole semantic identity of a canonical derived dataset
(ROADMAP §17): the formula, the implementation that computed it, the inputs it
is allowed to read, the calendar it reads them on, and the price-adjustment
convention it assumes.

Registration compares those fields against what is stored rather than against a
hash computed here. `definition_hash` is storage-generated, and a second hash
written in Python would be a second definition of identity that could drift
from the one the database enforces.

A changed formula is a new `derivation_version`. Registering different
semantics under an existing version is refused rather than silently accepted,
because the version is what every stored result cites.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.db.metadata import derived_dataset_definitions


@dataclass(frozen=True, slots=True)
class DerivationDefinition:
    dataset_code: str
    derivation_version: str
    storage_strategy: Literal["materialized", "virtual"]
    formula_specification: str
    implementation_version: str
    input_dataset_codes: tuple[str, ...]
    calendar_timezone: str | None = None
    calendar_convention: str | None = None
    price_adjustment_convention: str | None = None


SEMANTIC_COLUMNS = (
    "storage_strategy",
    "formula_specification",
    "implementation_version",
    "input_dataset_codes",
    "calendar_timezone",
    "calendar_convention",
    "price_adjustment_convention",
)


def _stored_value(definition: DerivationDefinition, column: str):
    value = getattr(definition, column)
    return list(value) if column == "input_dataset_codes" else value


class DerivationRegistry:
    """Store and look up definitions without ever rewriting one."""

    def register(self, connection: Connection, definition: DerivationDefinition) -> int:
        stored = self._stored(connection, definition)
        if stored is not None:
            differing = [
                column
                for column in SEMANTIC_COLUMNS
                if stored[column] != _stored_value(definition, column)
            ]
            if differing:
                raise ValueError(
                    f"{definition.dataset_code} {definition.derivation_version} is "
                    f"already registered with different {', '.join(differing)}; a "
                    "formula or convention change needs its own derivation_version"
                )
            return int(stored["id"])

        # `definition_hash` and `registered_at` are storage-generated; supplying
        # them here would be a caller claiming identity the database owns.
        return int(
            connection.execute(
                sa.insert(derived_dataset_definitions)
                .values(
                    dataset_code=definition.dataset_code,
                    derivation_version=definition.derivation_version,
                    **{
                        column: _stored_value(definition, column)
                        for column in SEMANTIC_COLUMNS
                    },
                )
                .returning(derived_dataset_definitions.c.id)
            ).scalar_one()
        )

    def definition_id(
        self, connection: Connection, definition: DerivationDefinition
    ) -> int:
        stored = self._stored(connection, definition)
        if stored is None:
            raise LookupError(
                f"{definition.dataset_code} {definition.derivation_version} is not "
                "registered; register the definition before computing with it"
            )
        return int(stored["id"])

    @staticmethod
    def _stored(connection: Connection, definition: DerivationDefinition):
        return connection.execute(
            sa.select(derived_dataset_definitions).where(
                derived_dataset_definitions.c.dataset_code == definition.dataset_code,
                derived_dataset_definitions.c.derivation_version
                == definition.derivation_version,
            )
        ).mappings().one_or_none()


TECHNICAL_INDICATORS_V1 = DerivationDefinition(
    dataset_code="technical_indicators",
    derivation_version="v1",
    storage_strategy="virtual",
    formula_specification=(
        "Ported from the legacy calculator so the consumers trained on those "
        "values keep reading the same series. MA and VMA over 5/10/20/60/120/240 "
        "trading days of raw official close and volume; KD from a nine-day RSV "
        "smoothed twice at alpha 1/3, with a flat window treated as the neutral "
        "50; RSI 6 and 12 as adjusted exponential means of gain and loss with "
        "com = window - 1; MACD as the 12/26 exponential difference with a "
        "9-period signal; Bollinger bands at the 20-day mean plus and minus two "
        "sample standard deviations. A window containing an unpublished price "
        "yields nothing rather than closing the gap."
    ),
    implementation_version="stock_data_center.derived.indicators@step-26-a",
    input_dataset_codes=("daily_price",),
    calendar_timezone="Asia/Taipei",
    calendar_convention=(
        "Trading days of the security's own daily-price history, in order. The "
        "rolling as-of series, computed on demand, computes observation date D at "
        "the instant D's own prices become public under the daily_price release "
        "rule (exchange_daily_settled@1: 03:00 on D+1), which is why D's own close "
        "is part of D's value and D+1's is not."
    ),
    price_adjustment_convention=(
        "raw_official_close: no corporate-action adjustment. Legacy computed "
        "these on raw prices and its consumers were trained that way; an "
        "adjusted variant is a later derivation version, not a silent change."
    ),
)
