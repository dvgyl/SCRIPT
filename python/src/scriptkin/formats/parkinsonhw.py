"""Read UCI ParkinsonHW files for reader and feature tests only.

Source: https://archive.ics.uci.edu/dataset/395/ . CC BY 4.0.
The earlier acquisition description supplies the column order and time unit:
https://archive.ics.uci.edu/dataset/358/ .
Rows contain X;Y;Z;Pressure;GripAngle;Timestamp;Test ID. Timestamps are
milliseconds. A positive pressure marks contact. Zero pressure marks in-air.
The UCI page does not specify a physical coordinate scale or the grip-angle
unit. Keep source positions and omit grip angle from calibrated channels.
Class folders and class prefixes are never returned as recording metadata.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from scriptkin.formats import FormatError
from scriptkin.formats.svc import tablet_strokes
from scriptkin.recording import Channel, Device, PenState, Recording, TaskRef, Trial


def read_parkinsonhw(path: str | Path) -> Recording:
    source = Path(path)
    data = source.read_bytes()
    try:
        lines = data.decode("utf-8-sig").splitlines()
        if lines and lines[0].split(";")[0].strip().lower() == "x":
            lines = lines[1:]
        rows = np.asarray([[float(v) for v in line.split(";")] for line in lines], dtype=float)
        if rows.ndim != 2 or rows.shape[1] != 7 or not len(rows) or not np.isfinite(rows).all():
            raise ValueError("Expected seven finite values per row.")
        if np.any(rows[:, 3] < 0) or not np.isin(rows[:, 6], [0, 1, 2]).all():
            raise ValueError("Invalid pressure or test ID.")
    except (ValueError, UnicodeError) as e:
        raise FormatError(f"{source.name}: invalid ParkinsonHW layout: {e}") from e
    present = frozenset({Channel.FORCE})
    device = Device("Wacom Cintiq 12WX", "tablet", present, present, "source")
    trials: list[Trial] = []
    for test in dict.fromkeys(int(v) for v in rows[:, 6]):
        selected = rows[rows[:, 6] == test]
        t = (selected[:, 5] - selected[0, 5]) / 1000
        xy = selected[:, :2] * [1, -1]
        states = [PenState.CONTACT if p > 0 else PenState.IN_AIR for p in selected[:, 3]]
        strokes = tablet_strokes(t, xy, states, {Channel.FORCE: selected[:, 3]})
        trials.append(
            Trial(
                test, TaskRef(f"parkinsonhw-{test}"), strokes, device, started=float(t[0]), ended=float(t[-1])
            )
        )
    digest = hashlib.sha256(data).hexdigest()
    return Recording(digest[:32], digest[:16], device, tuple(trials), (digest,))


def load_parkinsonhw(root: str | Path) -> tuple[Recording, ...]:
    """Read drawing files. Do not read labels or infer them from paths."""
    paths = sorted(p for p in Path(root).rglob("*.txt") if p.stem.startswith(("P_", "C_", "H_", "synthetic")))
    return tuple(read_parkinsonhw(p) for p in paths)
