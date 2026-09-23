"""A key range's whole market-PIT history, read once and resolved in memory.

`PITResolver.resolve` answers one logical key at one context. A caller that
needs a security's entire daily-price history at many information cutoffs — a
rolling as-of series is exactly that — would otherwise ask it once per trade
date per cutoff.

What makes one read enough: the authoritative evidence of a version depends on
the knowledge cutoff and the source policy, never on `information_as_of`. So for
a fixed `knowledge_as_of`, every version's evidence is settled once, and the
visible version of a key at any information cutoff is a selection over what is
already in memory. `visible_at` and `market_order` are that selection, and
`resolve` uses the same two functions, so the two paths cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from stock_data_center.pit.models import AuthoritativeEvidence


def visible_at(
    evidence: AuthoritativeEvidence | None, information_as_of: datetime
) -> bool:
    """Whether a version with this evidence is public at `information_as_of`."""
    return (
        evidence is not None
        and evidence.affirms_publication
        and evidence.published_at <= information_as_of
    )


def market_order(
    evidence: AuthoritativeEvidence, version_id: int
) -> tuple[datetime, int]:
    """Among visible versions of one key, the greatest of these wins."""
    assert evidence.published_at is not None
    return (evidence.published_at, version_id)


@dataclass(frozen=True, slots=True)
class HistoricalVersion:
    version_id: int
    logical_key: tuple[Any, ...]
    data: Mapping[str, Any]
    evidence: AuthoritativeEvidence


@dataclass(frozen=True, slots=True)
class MarketPITHistory:
    """Every version of a key range that could ever be visible.

    Versions whose authoritative evidence does not affirm publication are left
    out: no information cutoff can make them visible.
    """

    dataset_code: str
    source: str
    knowledge_as_of: datetime
    logical_key_columns: tuple[str, ...]
    versions: tuple[HistoricalVersion, ...]

    def publication_instants(self) -> tuple[tuple[tuple[Any, ...], datetime], ...]:
        """Each (logical key, instant) at which the visible set can change."""
        return tuple(
            (version.logical_key, version.evidence.published_at)
            for version in self.versions
        )

    def visible(
        self, information_as_of: datetime
    ) -> dict[tuple[Any, ...], HistoricalVersion]:
        """The version `resolve` would return for each key at this cutoff."""
        selected: dict[tuple[Any, ...], HistoricalVersion] = {}
        for version in self.versions:
            if not visible_at(version.evidence, information_as_of):
                continue
            current = selected.get(version.logical_key)
            if current is None or market_order(
                version.evidence, version.version_id
            ) > market_order(current.evidence, current.version_id):
                selected[version.logical_key] = version
        return selected


def build_history(
    *,
    dataset_code: str,
    source: str,
    knowledge_as_of: datetime,
    logical_key_columns: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    evidence: Mapping[int, AuthoritativeEvidence],
    internal_columns: frozenset[str],
) -> MarketPITHistory:
    versions = tuple(
        HistoricalVersion(
            version_id=int(row["id"]),
            logical_key=tuple(row[column] for column in logical_key_columns),
            data={
                key: value
                for key, value in row.items()
                if key not in internal_columns
            },
            evidence=evidence[int(row["id"])],
        )
        for row in rows
        if int(row["id"]) in evidence
        and evidence[int(row["id"])].affirms_publication
    )
    return MarketPITHistory(
        dataset_code=dataset_code,
        source=source,
        knowledge_as_of=knowledge_as_of,
        logical_key_columns=tuple(logical_key_columns),
        versions=versions,
    )
