"""JSON for API responses, with every exact decimal written as its own digits.

A stored NUMERIC is rendered as a JSON number with exactly its digits
(`1234.56`), never through a binary float, so no value gains digits it was not
published with. Instants are ISO 8601 with their offset, dates ISO 8601.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID


def _encode(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"not a finite number: {value}")
        return format(value, "f")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"not a finite number: {value}")  # JSON has none
    if isinstance(value, int | float | str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, datetime | date):
        return json.dumps(value.isoformat())
    if isinstance(value, UUID):
        return json.dumps(str(value))
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(str(k), ensure_ascii=False)}:{_encode(v)}"
                              for k, v in value.items()) + "}"
    if isinstance(value, list | tuple):
        return "[" + ",".join(_encode(v) for v in value) + "]"
    raise TypeError(f"cannot render {type(value).__name__}")


def dumps(value) -> str:
    return _encode(value)
