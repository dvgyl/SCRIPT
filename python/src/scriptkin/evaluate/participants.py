"""Participant-level views of a feature table: medians over accepted attempts, and session context."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass

import numpy as np

from scriptkin.features import FeatureRow, FeatureTable
from scriptkin.recording import FloatArray


@dataclass(frozen=True)
class Participant:
    """Context of one participant. A participant seen at several sites or devices joins them with "+"."""

    id: str
    site: str
    device: str
    age_band: str
    administration: str
    hand: str
    first_session: str
    first_opened: str


def _joined(values: set[str]) -> str:
    return "+".join(sorted(values))


def participants(table: FeatureTable) -> dict[str, Participant]:
    """Every participant of the table, rejected trials included. The first session is the earliest one."""
    rows: dict[str, list[FeatureRow]] = {}
    for row in table.rows:
        rows.setdefault(row.participant, []).append(row)
    result: dict[str, Participant] = {}
    for pid, own in rows.items():
        first = min(own, key=lambda r: (r.opened_at is None, r.opened_at or "", r.session))
        result[pid] = Participant(
            pid,
            _joined({r.site for r in own}),
            _joined({r.device for r in own}),
            first.age_band,
            _joined({r.administration for r in own}),
            _joined({r.hand for r in own}),
            first.session,
            first.opened_at or "",
        )
    return result


def medians[K: Hashable](
    table: FeatureTable, columns: Sequence[str], key: Callable[[FeatureRow], K]
) -> tuple[list[K], FloatArray]:
    """The median of each `task/feature` column over the accepted attempts of each key.

    A rejected trial adds nothing. A refused cell adds nothing. A key with no finite value gives NaN.
    Keys come from every row, so a key whose trials were all rejected still appears.
    """
    by_task: dict[str, list[tuple[int, str]]] = {}
    for j, name in enumerate(columns):
        task, _, feature = name.partition("/")
        by_task.setdefault(task, []).append((j, feature))
    order: dict[K, int] = {}
    buckets: dict[tuple[int, int], list[float]] = {}
    for row in table.rows:
        i = order.setdefault(key(row), len(order))
        if row.qc:
            continue
        for j, feature in by_task.get(row.task, ()):
            cell = row.cells.get(feature)
            if isinstance(cell, float):
                buckets.setdefault((i, j), []).append(cell)
    values = np.full((len(order), len(columns)), np.nan)
    for (i, j), found in buckets.items():
        values[i, j] = float(np.median(found))
    return list(order), values


def refused_trials(table: FeatureTable, tasks: set[str]) -> tuple[int, int, dict[str, int]]:
    """Trials of the given tasks: the total, the number QC rejected, and the count of each rejection code."""
    rows = [r for r in table.rows if r.task in tasks]
    codes = Counter(str(code) for r in rows for code in r.qc)
    return len(rows), sum(1 for r in rows if r.qc), dict(sorted(codes.items()))
