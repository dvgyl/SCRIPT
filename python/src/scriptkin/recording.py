"""Reader output in millimetres, seconds, and radians, with y up."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]


class Channel(StrEnum):
    FORCE = "force"
    ALTITUDE = "altitude"
    AZIMUTH = "azimuth"
    ROLL = "roll"
    HOVER = "hover"


class PenState(StrEnum):
    CONTACT = "contact"
    IN_AIR = "in_air"


class TrialOutcome(StrEnum):
    COMPLETED = "completed"
    TIME_LIMIT = "timeLimit"
    ABORTED = "aborted"
    INTERRUPTED = "interrupted"
    OPEN = "open"


@dataclass(frozen=True)
class Device:
    model: str
    input: Literal["pencil", "directTouchAsPen", "tablet"]
    planned_channels: frozenset[Channel] = frozenset()
    observed_channels: frozenset[Channel] = frozenset()
    spatial_unit: Literal["mm", "source"] = "mm"


@dataclass(frozen=True)
class TaskRef:
    id: str
    mode: Literal["trace", "copy", "free"] = "free"
    paths: tuple[FloatArray, ...] = ()


@dataclass(frozen=True)
class Stroke:
    t: FloatArray
    xy: FloatArray
    state: PenState = PenState.CONTACT
    channels: dict[Channel, FloatArray] = field(default_factory=dict)
    unresolved: IntArray = field(default_factory=lambda: np.array([], dtype=np.int64))
    boot: str = "source"
    contact: int | None = None
    max_force: float | None = None

    def __post_init__(self) -> None:
        n = len(self.t)
        if self.t.shape != (n,) or self.xy.shape != (n, 2):
            raise ValueError("Stroke arrays must have shapes (n,) and (n, 2).")
        if len(self.unresolved) not in (0, n) or any(v.shape != (n,) for v in self.channels.values()):
            raise ValueError("Channel and estimate arrays must match the sample count.")
        for a in (self.t, self.xy, self.unresolved, *self.channels.values()):
            a.setflags(write=False)


@dataclass(frozen=True)
class Trial:
    index: int
    task: TaskRef
    strokes: tuple[Stroke, ...]
    device: Device
    attempt: int = 1
    outcome: TrialOutcome = TrialOutcome.COMPLETED
    started: float | None = None
    ended: float | None = None
    boot: str = "source"
    finger_contacts: int = 0
    unmatched_corrections: int = 0
    marks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Recording:
    session_id: str
    participant: str
    device: Device
    trials: tuple[Trial, ...]
    source_hashes: tuple[str, ...] = ()
    unmatched_corrections: int = 0
