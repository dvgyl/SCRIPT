"""Read SVC rows: x, y, time, status, azimuth, altitude, pressure.

The first line gives the row count. EMOTHAW and PaHaW profile values are
unverified: their real files are blocked by the project governance rule.
Supply every tablet conversion explicitly. Do not infer units from a filename.

Sources: https://arxiv.org/abs/2202.12245 and https://arxiv.org/abs/2411.03044.
The SVC layout is also documented by the acquisition library:
https://github.com/BDALab/handwriting-sample#svc-file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Literal

import numpy as np

from scriptkin.formats import FormatError
from scriptkin.recording import Channel, Device, FloatArray, PenState, Recording, Stroke, TaskRef, Trial


@dataclass(frozen=True)
class Tablet:
    """Explicit source units. EMOTHAW and PaHaW values remain unverified."""

    name: str
    lines_per_mm: float
    time_unit: Literal["s", "ms", "us"]
    angle_unit: Literal["rad", "deg", "decideg"]
    max_pressure: float
    pen_status: dict[int, PenState]
    y_down: bool

    def __post_init__(self) -> None:
        if not np.isfinite(self.lines_per_mm) or self.lines_per_mm <= 0:
            raise ValueError("lines_per_mm must be positive and finite.")
        if not np.isfinite(self.max_pressure) or self.max_pressure <= 0:
            raise ValueError("max_pressure must be positive and finite.")
        if self.time_unit not in ("s", "ms", "us") or self.angle_unit not in ("rad", "deg", "decideg"):
            raise ValueError("Unknown time or angle unit.")
        if set(self.pen_status.values()) != set(PenState):
            raise ValueError("pen_status must define contact and in-air states.")


def tablet_strokes(
    t: FloatArray, xy: FloatArray, states: list[PenState], channels: dict[Channel, FloatArray]
) -> tuple[Stroke, ...]:
    boundaries = [0, *(i for i in range(1, len(t)) if states[i] != states[i - 1]), len(t)]
    return tuple(
        Stroke(t[a:b].copy(), xy[a:b].copy(), states[a], {k: v[a:b].copy() for k, v in channels.items()})
        for a, b in pairwise(boundaries)
        if b > a
    )


class SvcReader:
    def __init__(self, tablet: Tablet):
        self.tablet = tablet

    def read(self, path: str | Path, *, task: TaskRef | None = None) -> Recording:
        source = Path(path)
        data = source.read_bytes()
        try:
            lines = data.decode("utf-8").splitlines()
            count = int(lines[0])
            rows = np.asarray([[float(v) for v in line.split()] for line in lines[1:]], dtype=float)
            if count < 1 or rows.shape != (count, 7) or not np.isfinite(rows).all():
                raise ValueError("Invalid row count, fields, or values.")
            if not np.equal(rows[:, 3], np.floor(rows[:, 3])).all():
                raise ValueError("Pen status must be an integer.")
            states = [self.tablet.pen_status[int(v)] for v in rows[:, 3]]
        except (ValueError, IndexError, KeyError, UnicodeError) as e:
            raise FormatError(f"{source.name}: invalid SVC layout: {e}") from e
        p = self.tablet
        if np.any(rows[:, 6] < 0) or np.any(rows[:, 6] > p.max_pressure):
            raise FormatError(f"{source.name}: pressure is outside the tablet range.")
        t = (rows[:, 2] - rows[0, 2]) * {"s": 1.0, "ms": 0.001, "us": 0.000001}[p.time_unit]
        xy = rows[:, :2] / p.lines_per_mm * [1, -1 if p.y_down else 1]
        angle = {"rad": 1.0, "deg": np.pi / 180, "decideg": np.pi / 1800}[p.angle_unit]
        channels = {
            Channel.FORCE: rows[:, 6] / p.max_pressure,
            Channel.AZIMUTH: rows[:, 4] * angle,
            Channel.ALTITUDE: rows[:, 5] * angle,
        }
        present = frozenset(channels)
        device = Device(p.name, "tablet", present, present)
        trial = Trial(
            0,
            task or TaskRef("svc"),
            tablet_strokes(t, xy, states, channels),
            device,
            started=float(t[0]),
            ended=float(t[-1]),
        )
        digest = hashlib.sha256(data).hexdigest()
        return Recording(digest[:32], digest[:16], device, (trial,), (digest,))
