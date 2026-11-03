"""Read UCI ParkinsonHW files for reader and feature tests only.

Sources, CC BY 4.0: https://archive.ics.uci.edu/dataset/395/ and the earlier acquisition description
https://archive.ics.uci.edu/dataset/358/ , which gives the column order, the 0 to 1023 pressure scale,
and "System time in ms". Each row is X;Y;Z;Pressure;GripAngle;Timestamp;Test ID. The real files have
CRLF line ends and no header. The test names come from the readme in the dataset archive. The page of
dataset 358 calls test 2 a stability test on a point, held without touching the screen.

- Time is in milliseconds. The real files confirm it: the median sample interval is 7 ms.
- Pen state comes from pressure: a positive pressure is contact, and zero pressure is in-air. Z does
  not mark the pen state: inside contact it is nonzero in single samples.
- Neither source gives a unit for X, Y, Z, or the grip angle. Positions stay in source units, and Z and
  the grip angle are not channels.

Class folders and class file name prefixes never reach a returned value or an error message. A file is
known only by the SHA-256 of its bytes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from scriptkin.formats import FormatError
from scriptkin.formats.svc import tablet_strokes
from scriptkin.recording import Channel, Device, PenState, Recording, TaskRef, Trial, reader_profile

TESTS = {0: "static-spiral", 1: "dynamic-spiral", 2: "circular-motion"}
PROFILE = reader_profile(
    "parkinsonhw",
    time_unit="ms",
    position_unit="source",
    y="flipped",
    pen_state="pressure > 0 is contact",
    force="pressure, 0 to 1023 scale",
    omitted=["Z", "GripAngle"],
)


def read_parkinsonhw(path: str | Path) -> Recording:
    """Read one file. Each test ID becomes one trial whose index is the test ID."""
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    try:
        lines = data.decode("utf-8-sig").splitlines()
        if lines and lines[0].split(";")[0].strip().lower() == "x":
            lines = lines[1:]
        rows = np.asarray([[float(v) for v in line.split(";")] for line in lines], dtype=float)
        if rows.ndim != 2 or rows.shape[1] != 7 or not len(rows) or not np.isfinite(rows).all():
            raise ValueError("Expected seven finite values per row.")
        if np.any(rows[:, 3] < 0) or not np.isin(rows[:, 6], list(TESTS)).all():
            raise ValueError("Invalid pressure or test ID.")
    except (ValueError, UnicodeError) as e:
        raise FormatError(f"ParkinsonHW file {digest[:16]}: invalid layout: {e}") from e
    present = frozenset({Channel.FORCE})
    device = Device("Wacom Cintiq 12WX", "tablet", present, present, "source")
    trials: list[Trial] = []
    for test in dict.fromkeys(int(v) for v in rows[:, 6]):
        selected = rows[rows[:, 6] == test]
        t = (selected[:, 5] - selected[0, 5]) / 1000
        xy = selected[:, :2] * [1, -1]
        states = [PenState.CONTACT if p > 0 else PenState.IN_AIR for p in selected[:, 3]]
        strokes = tablet_strokes(t, xy, states, {Channel.FORCE: selected[:, 3]})
        task = TaskRef(f"parkinsonhw-{TESTS[test]}")
        trials.append(Trial(test, task, strokes, device, started=float(t[0]), ended=float(t[-1])))
    return Recording(digest[:32], digest[:16], device, tuple(trials), PROFILE, (digest,))


def load_parkinsonhw(root: str | Path) -> tuple[Recording, ...]:
    """Read every data file under `root` in the order of their SHA-256 digests.

    A data file is any `.txt` file except `readme.txt`. Folder and file names decide no order or value.
    """
    paths = (
        p for p in Path(root).rglob("*.txt") if not p.name.startswith("._") and p.name.lower() != "readme.txt"
    )
    return tuple(sorted(map(read_parkinsonhw, paths), key=lambda r: r.source_hashes[0]))
