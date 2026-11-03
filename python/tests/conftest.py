from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

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


def pytest_configure(config: pytest.Config) -> None:
    if config.option.basetemp is None:
        config.option.basetemp = str(Path(__file__).resolve().parents[2] / "build" / "pytest")
