"""A request's PIT context, resolved to explicit instants before any query (CLAUDE.md §59).

Market PIT takes `information_as_of` and `knowledge_as_of`, system PIT
`system_as_of` (§14); the two modes do not mix. An instant must carry its UTC
offset (`docs/pit_semantics.md`). `latest` and `now` are aliases for the
instant the request arrived, and so is a market parameter left out (owner,
2026-09-25): the response names which were aliases and which defaulted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from stock_data_center.v2.visibility import PIT, MarketPIT, SystemPIT

MARKET = ("information_as_of", "knowledge_as_of")
SYSTEM = "system_as_of"
ALIASES = frozenset({"latest", "now"})


class PITError(ValueError):
    """A PIT parameter the API cannot resolve."""


@dataclass(frozen=True)
class Resolved:
    pit: PIT
    defaulted: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)

    def describe(self) -> dict:
        if isinstance(self.pit, SystemPIT):
            instants = {"mode": "system", SYSTEM: self.pit.system_as_of.isoformat()}
        else:
            instants = {"mode": "market",
                        **{name: getattr(self.pit, name).isoformat() for name in MARKET}}
        return {**instants, "defaulted": self.defaulted, "aliases": self.aliases}


def _instant(name: str, text: str, arrived: datetime, aliases: dict[str, str]) -> datetime:
    if text in ALIASES:
        aliases[name] = text
        return arrived
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise PITError(f"{name} is not an ISO 8601 instant: {text!r}") from None
    if value.tzinfo is None or value.utcoffset() is None:
        raise PITError(f"{name} needs a UTC offset, such as +08:00: {text!r}")
    return value


def resolve(params: dict[str, str], arrived: datetime) -> Resolved:
    """The PIT context of a request that arrived at `arrived`."""
    aliases: dict[str, str] = {}
    if SYSTEM in params:
        if any(name in params for name in MARKET):
            raise PITError(f"{SYSTEM} is system PIT and cannot be combined with "
                           f"{' or '.join(MARKET)}")
        return Resolved(SystemPIT(_instant(SYSTEM, params[SYSTEM], arrived, aliases)),
                        aliases=aliases)
    instants, defaulted = {}, []
    for name in MARKET:
        if name in params:
            instants[name] = _instant(name, params[name], arrived, aliases)
        else:
            instants[name] = arrived
            defaulted.append(name)
    return Resolved(MarketPIT(**instants), defaulted, aliases)
