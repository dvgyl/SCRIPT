"""The registered move list of one bench program, and the path that it commands (protocol section 5).

A move list is the file `bench/programs/<program>.json` that `python/studies/bench_programs.py --write`
writes. It holds the registered program, the start point, and the moves. A move is an end point and a
duration, so the first move has no start without the start point. The translation to a controller file
and the commanded path both need it.

The move list and the commanded path are in the stimulus frame of protocol section 5.1: millimetres,
the origin at the top left, and y down. Every reader output of `scriptkin.recording` has y up.
`commanded_trial` mirrors y, so that a commanded path and a device run share one frame.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from scriptkin import battery
from scriptkin.bench import Design
from scriptkin.bench import program as registered_program
from scriptkin.formats import FormatError
from scriptkin.recording import Device, FloatArray, Stroke, TaskRef, Trial

_DESIGN_FIELDS = (
    "id",
    "task",
    "geometry",
    "scale",
    "speed_mm_s",
    "oscillation_hz",
    "oscillation_mm",
    "fault",
)


class MoveListError(FormatError):
    pass


@dataclass(frozen=True)
class Move:
    """One timed move to an end point. `lift_mm` above 0 is a lift to that height and back."""

    x_mm: float
    y_mm: float
    duration_s: float
    lift_mm: float = 0.0


@dataclass(frozen=True)
class CommandedPath:
    """The commanded position at every commanded time, in the stimulus frame.

    `t` and `xy` hold the start point and the end point of every move, in time order. Between two
    points the stylus moves on the straight line between them. A lift move keeps its end point and
    the stylus is in the air for its duration. `lifts` holds the start and the end time of each lift.
    """

    t: FloatArray
    xy: FloatArray
    lifts: tuple[tuple[float, float], ...]

    @property
    def duration_s(self) -> float:
        return float(self.t[-1])

    def position(self, times: FloatArray) -> FloatArray:
        """The commanded position at each time. Times outside the path hold the nearest end point."""
        return np.column_stack([np.interp(times, self.t, self.xy[:, k]) for k in range(2)])

    def in_contact(self, times: FloatArray) -> FloatArray:
        """True at each time when the stylus is on the surface. The ends of a lift are on the surface."""
        contact = np.ones(len(times), dtype=bool)
        for start, end in self.lifts:
            contact &= ~((times > start) & (times < end))
        return contact


@dataclass(frozen=True)
class MoveList:
    """One registered program: its design, its start point, and its moves, as the file holds them."""

    program: Design
    start_mm: tuple[float, float]
    moves: tuple[Move, ...]
    move_s: float
    ramp_s: float

    @property
    def duration_s(self) -> float:
        return float(sum(m.duration_s for m in self.moves))

    def path(self) -> CommandedPath:
        t = np.concatenate(([0.0], np.cumsum([m.duration_s for m in self.moves])))
        xy = np.array([self.start_mm, *((m.x_mm, m.y_mm) for m in self.moves)], dtype=float)
        lifts = tuple((float(t[i]), float(t[i + 1])) for i, m in enumerate(self.moves) if m.lift_mm > 0)
        return CommandedPath(t, xy, lifts)


def _finite(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise MoveListError(f"{name} must be finite.")
    return number


def read_move_list(path: str | Path) -> MoveList:
    """Read one move list file. The program must be the registered program of the same id."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        program = raw["program"]
        fields = program.get("design", program)
        design = Design(**{k: fields[k] for k in _DESIGN_FIELDS}, base=raw.get("base"))
        registered = registered_program(design.id)
        if design != registered:
            raise MoveListError(f"{path.name}: the program differs from the registered {design.id}.")
        if raw["battery"] != {"id": battery.BATTERY_ID, "version": battery.BATTERY_VERSION}:
            raise MoveListError(f"{path.name}: the battery is not {battery.BATTERY_VERSION}.")
        start = (_finite(raw["start"]["x_mm"], "start"), _finite(raw["start"]["y_mm"], "start"))
        moves = tuple(
            Move(
                _finite(m["x_mm"], "x_mm"),
                _finite(m["y_mm"], "y_mm"),
                _finite(m["duration_s"], "duration_s"),
                _finite(m["lift_mm"], "lift_mm"),
            )
            for m in raw["moves"]
        )
        move_s, ramp_s = _finite(raw["move_s"], "move_s"), _finite(raw["ramp_s"], "ramp_s")
    except MoveListError:
        raise
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise MoveListError(f"{path.name}: {e}") from e
    if not moves or any(m.duration_s <= 0 or m.lift_mm < 0 for m in moves) or move_s <= 0 or ramp_s <= 0:
        raise MoveListError(f"{path.name}: moves need positive durations and lifts that are not negative.")
    return MoveList(design, start, moves, move_s, ramp_s)


def read_move_lists(folder: str | Path) -> dict[str, MoveList]:
    """Every move list in a folder, by program id."""
    lists = [read_move_list(p) for p in sorted(Path(folder).glob("*.json")) if not p.name.startswith("._")]
    return {m.program.id: m for m in lists}


def commanded_strokes(path: CommandedPath, times: FloatArray) -> tuple[Stroke, ...]:
    """The commanded path at the given times as contact strokes with y up, one stroke per contact span.

    A lift ends one stroke at its start and begins the next at its end, with or without a sample
    between them. A span without a sample gives no stroke.
    """
    xy = path.position(times) * [1.0, -1.0]
    contact = path.in_contact(times)
    span = np.zeros(len(times), dtype=np.int64)
    for _, end in path.lifts:
        span += times >= end
    strokes: list[Stroke] = []
    for k in range(len(path.lifts) + 1):
        keep = contact & (span == k)
        if keep.any():
            strokes.append(Stroke(times[keep].copy(), xy[keep].copy()))
    return tuple(strokes)


def _dense(path: CommandedPath, step: float) -> FloatArray:
    """The commanded times, with samples every `step` inside a move that lasts longer than `step`."""
    pieces = [path.t]
    for a, b in zip(path.t[:-1], path.t[1:], strict=True):
        if b - a > step * (1 + 1e-9):
            pieces.append(np.linspace(a, b, int(np.ceil((b - a) / step)) + 1))
    return np.unique(np.concatenate(pieces))


def commanded_trial(move_list: MoveList, times: FloatArray | None = None) -> Trial:
    """The commanded path as a reader output: a completed trial in millimetres with y up.

    Without `times`, the samples are the commanded times, where the path is exact, plus samples
    every `move_s` inside a longer move such as a dwell. A caller that wants a device-like run
    gives its own sample times.
    """
    path = move_list.path()
    t = _dense(path, move_list.move_s) if times is None else np.asarray(times, dtype=float)
    return Trial(
        0,
        TaskRef(move_list.program.task),
        commanded_strokes(path, t),
        Device("command", "tablet"),
        started=float(t[0]),
        ended=float(t[-1]),
    )
