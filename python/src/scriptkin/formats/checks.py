"""Small predicates that check decoded JSON values at the reader boundary."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from typing import Any, cast

Check = Callable[[Any], bool]


def number(v: Any) -> bool:
    return type(v) in (int, float) and math.isfinite(v)


def between(low: float, high: float) -> Check:
    return lambda v: number(v) and low <= v <= high


def uptime(v: Any) -> bool:
    return number(v) and v >= 0


def positive(v: Any) -> bool:
    return number(v) and v > 0


def boolean(v: Any) -> bool:
    return type(v) is bool


def text(v: Any) -> bool:
    return isinstance(v, str)


def nonempty(v: Any) -> bool:
    return isinstance(v, str) and v != ""


def sized(low: int, high: int) -> Check:
    """A string of `low` to `high` code points, as the schema keywords `minLength` and `maxLength` count."""
    return lambda v: isinstance(v, str) and low <= len(v) <= high


def matches(pattern: str) -> Check:
    """A string that `pattern` matches in full, as a schema `pattern` anchored with `^` and `$` does."""
    compiled = re.compile(pattern)
    return lambda v: isinstance(v, str) and compiled.fullmatch(v) is not None


def integer(low: int | None = None, high: int | None = None) -> Check:
    return lambda v: type(v) is int and (low is None or v >= low) and (high is None or v <= high)


def one_of(*values: str) -> Check:
    return lambda v: isinstance(v, str) and v in values


def any_of(*checks: Check) -> Check:
    return lambda v: any(check(v) for check in checks)


def array(item: Check, *, minimum: int = 0, unique: bool = False) -> Check:
    def check(v: Any) -> bool:
        if not isinstance(v, list):
            return False
        items = cast(list[Any], v)
        return (
            len(items) >= minimum
            and all(item(x) for x in items)
            and (not unique or len(set(map(str, items))) == len(items))
        )

    return check


def record(required: Mapping[str, Check], optional: Mapping[str, Check] | None = None) -> Check:
    def check(v: Any) -> bool:
        if not isinstance(v, dict):
            return False
        d = cast(dict[str, Any], v)
        return all(k in d and c(d[k]) for k, c in required.items()) and all(
            c(d[k]) for k, c in (optional or {}).items() if k in d
        )

    return check


def point(v: Any) -> bool:
    return isinstance(v, list) and len(cast(list[Any], v)) == 2 and all(map(number, cast(list[Any], v)))
