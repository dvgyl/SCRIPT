from __future__ import annotations

import numpy as np

from scriptkin import Device, Stroke, TaskRef, Trial
from scriptkin.recording import FloatArray


def make_trial(
    t: FloatArray,
    xy: FloatArray | None = None,
    *,
    strokes: tuple[Stroke, ...] | None = None,
    device: Device | None = None,
    task: TaskRef | None = None,
) -> Trial:
    positions = np.column_stack((t * 10, np.zeros(len(t)))) if xy is None else xy
    return Trial(
        0,
        task or TaskRef("clock"),
        strokes or (Stroke(t, positions),),
        device or Device("synthetic", "pencil"),
        started=float(t[0]),
        ended=float(t[-1]),
    )
