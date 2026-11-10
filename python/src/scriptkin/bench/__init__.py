"""The bench tasks and the bench programs of the bench protocol, written to the contract files.

A bench task is a figure of `scriptkin.battery` at one scale. It goes into `battery.json`. A program is
a motion of one bench task at one speed, with or without a fault. It goes into `bench_programs.json`.
Both captures bundle the programs file, and the decider checks each run against it. The study script
`python/studies/bench_programs.py` imports the designs from here and computes their move lists.

The programs and the tasks are final for the build (protocol 1.2, section 5). The trajectory digest of a
program is the SHA-256 of its controller file under `bench/programs/` (section 2.4). Until that file
exists, the digest is the SHA-256 of a placeholder text that names the program. The protocol digest is
the SHA-256 of `docs/BENCH_PROTOCOL.md` from the time that a controller file exists, and 64 zeros before
(`protocol_sha256`). So `scriptkin contract regenerate` in step 4 of the lock (section 14) writes both.
To add a program, add a row to `MAIN`. Its task follows from its geometry and scale. A new geometry
needs one row in `_TASK_OF_GEOMETRY` here and one figure function in `battery.FIGURES`.
"""

from __future__ import annotations

import functools
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

PROTOCOL_VERSION = "1.2"
PLACEHOLDER_SHA256 = "0" * 64
"""The protocol digest of a programs file that no controller file stands behind."""
PROTOCOL_FILE = Path(__file__).resolve().parents[4] / "docs" / "BENCH_PROTOCOL.md"
"""The bench protocol of the repository."""
PROGRAM_FOLDER = Path(__file__).resolve().parents[4] / "bench" / "programs"
"""The move lists and the controller files of the repository (section 15)."""

Geometry = Literal["loops", "spiral", "line"]
SCALES = {"l": 1.0, "s": 0.7}
LINE_FRAME_MM = (132.0, 16.0)
LINE_START_MM = (4.0, 8.0)
LINE_END_MM = (128.0, 8.0)
LINE_SPEED_MM_S = 20.0
# The run index is 1 to 5 for the planned runs and 6 or 7 for the reruns (protocol section 6.4).
RUNS = range(1, 8)
# The protocol plans the mountings M1 to M6 and can add M7 and M8 (section 6.6).
MOUNTINGS = tuple(f"M{n}" for n in range(1, 9))


@dataclass(frozen=True)
class Design:
    """The registered definition of one program."""

    id: str
    task: str
    geometry: Geometry
    scale: float
    speed_mm_s: float
    oscillation_hz: float = 0.0
    oscillation_mm: float = 0.0
    fault: str | None = None
    base: str | None = None
    """The id of the program that a fault program changes."""


def _figure(geometry: Geometry, size: str, speed: float) -> Design:
    return Design(f"{geometry}-{size}-v{speed:g}", f"bench-{geometry}-{size}", geometry, SCALES[size], speed)


MAIN: tuple[Design, ...] = (
    _figure("loops", "l", 20.0),
    _figure("loops", "l", 40.0),
    _figure("loops", "l", 80.0),
    _figure("loops", "s", 20.0),
    _figure("loops", "s", 40.0),
    _figure("spiral", "l", 20.0),
    _figure("spiral", "l", 40.0),
    _figure("spiral", "s", 20.0),
    _figure("spiral", "s", 40.0),
    Design("line-osc5", "bench-line", "line", 1.0, LINE_SPEED_MM_S, 5.0, 0.4),
    Design("line-osc8", "bench-line", "line", 1.0, LINE_SPEED_MM_S, 8.0, 0.25),
    Design("line-osc11", "bench-line", "line", 1.0, LINE_SPEED_MM_S, 11.0, 0.18),
)
FAULT_BASE = MAIN[0]
FAULTS: tuple[Design, ...] = tuple(
    replace(FAULT_BASE, id=f"fault-{fault}", fault=fault, base=FAULT_BASE.id)
    for fault in ("dwell", "lift", "truncated")
)
PROGRAMS = MAIN + FAULTS


@dataclass(frozen=True)
class BenchTask:
    """One bench task of `battery.json`: a geometry at one scale."""

    id: str
    geometry: Geometry
    scale: float
    """Multiplies every stimulus coordinate about the origin of the frame."""
    mode: str
    max_seconds: float


# The completion mode and the time limit of the bench tasks of each geometry.
_TASK_OF_GEOMETRY: dict[Geometry, tuple[str, float]] = {
    "loops": ("copy", 90),
    "spiral": ("trace", 120),
    "line": ("trace", 60),
}


def _tasks() -> tuple[BenchTask, ...]:
    """One task per distinct task id of `MAIN`, in `MAIN` order. The id names the geometry and the scale."""
    tasks: dict[str, BenchTask] = {}
    for d in MAIN:
        mode, max_seconds = _TASK_OF_GEOMETRY[d.geometry]
        tasks.setdefault(d.task, BenchTask(d.task, d.geometry, d.scale, mode, max_seconds))
    return tuple(tasks.values())


TASKS = _tasks()


def task(task_id: str) -> BenchTask:
    for t in TASKS:
        if t.id == task_id:
            return t
    raise KeyError(task_id)


def program(program_id: str) -> Design:
    for p in PROGRAMS:
        if p.id == program_id:
            return p
    raise KeyError(program_id)


@functools.cache
def registered_digests() -> dict[str, str]:
    """The trajectory digests of the controller files of the repository, read once. Each controller file
    must be the translation of its move list (`scriptkin.bench.controller.translated_digests`)."""
    # The controller module imports this module, so it is imported here and not at the top.
    from scriptkin.bench.controller import translated_digests

    return translated_digests(PROGRAM_FOLDER)


@functools.cache
def protocol_sha256() -> str:
    """The protocol digest of the programs file: the SHA-256 of `docs/BENCH_PROTOCOL.md`.

    Before a controller file exists under `bench/programs/`, it is the placeholder of 64 zeros, so an edit
    of the protocol text changes no contract file. The motion qualification and the pilot execute
    controller files, so from then on each bench journal names the protocol text of its build."""
    if not registered_digests():
        return PLACEHOLDER_SHA256
    return hashlib.sha256(PROTOCOL_FILE.read_bytes()).hexdigest()


def trajectory_sha256(program_id: str) -> str:
    """The trajectory digest of one program: the SHA-256 of its controller file under `bench/programs/`.

    A program without a move list there has the SHA-256 of a placeholder text that names the program.
    This holds for every program before the lock, and for a program that section 4.2 step 7 excludes."""
    pid = program(program_id).id
    digest = registered_digests().get(pid)
    if digest is not None:
        return digest
    return hashlib.sha256(f"SCRIPT bench placeholder trajectory {pid}".encode()).hexdigest()


def programs() -> dict[str, Any]:
    main_ids = {m.id for m in MAIN}
    for p in PROGRAMS:
        task(p.task)
        if p.base is not None and p.base not in main_ids:
            raise KeyError(p.base)
    return {
        "protocol": {"version": PROTOCOL_VERSION, "sha256": protocol_sha256()},
        "programs": [
            {
                "id": p.id,
                "task": p.task,
                "scale": p.scale,
                "speedMMPerS": p.speed_mm_s,
                "oscillation": {"hz": p.oscillation_hz, "mm": p.oscillation_mm} if p.oscillation_hz else None,
                "fault": p.fault,
                "base": p.base,
                "trajectorySHA256": trajectory_sha256(p.id),
            }
            for p in PROGRAMS
        ],
    }


@functools.cache
def programs_json() -> bytes:
    """The canonical bytes of contract/bench_programs.json."""
    return (json.dumps(programs(), indent=1) + "\n").encode()


@functools.cache
def programs_sha256() -> str:
    return hashlib.sha256(programs_json()).hexdigest()
