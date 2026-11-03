"""The task battery: stimulus geometry in millimetres, written to contract/battery.json.

Geometry lives here and nowhere else. The app draws the polylines that this module writes.
Frame coordinates have the origin at the top-left of the stimulus frame, x to the right, y down.
"""

from __future__ import annotations

import functools
import hashlib
import itertools
import json
import math
import re
from collections.abc import Callable, Sequence
from typing import Any

BATTERY_ID = "script-v1"
BATTERY_VERSION = "1.1.0"

Point = tuple[float, float]
Polyline = list[Point]

_MAX_CHORD_MM = 0.5
_DECIMALS = 4

SPIRAL_INNER_RADIUS_MM = 8.0
SPIRAL_TURN_SPACING_MM = 21.0
SPIRAL_TURNS = 2
STIMULUS_STROKE_MM = 0.6


def _round(p: Point) -> list[float]:
    return [round(p[0], _DECIMALS) + 0.0, round(p[1], _DECIMALS) + 0.0]


def _sample(curve: Callable[[float], Point], t0: float, t1: float) -> Polyline:
    """Sample a smooth curve at equal arc-length steps no longer than _MAX_CHORD_MM."""
    probe = 20000
    ts = [t0 + (t1 - t0) * i / probe for i in range(probe + 1)]
    pts = [curve(t) for t in ts]
    cumulative = [0.0]
    for a, b in itertools.pairwise(pts):
        cumulative.append(cumulative[-1] + math.dist(a, b))
    n = max(1, math.ceil(cumulative[-1] / _MAX_CHORD_MM))
    out: Polyline = []
    j = 0
    for i in range(n + 1):
        s = cumulative[-1] * i / n
        while j < probe - 1 and cumulative[j + 1] < s:
            j += 1
        span = cumulative[j + 1] - cumulative[j]
        u = 0.0 if span == 0 else (s - cumulative[j]) / span
        out.append(curve(ts[j] + (ts[j + 1] - ts[j]) * u))
    return out


def spiral_radius_mm(theta: float) -> float:
    return SPIRAL_INNER_RADIUS_MM + SPIRAL_TURN_SPACING_MM / (2 * math.pi) * theta


def spiral() -> dict[str, Any]:
    outer = spiral_radius_mm(2 * math.pi * SPIRAL_TURNS)
    margin = 2.0
    side = 2 * (outer + margin)
    c = side / 2

    def curve(theta: float) -> Point:
        r = spiral_radius_mm(theta)
        return (c + r * math.cos(theta), c - r * math.sin(theta))

    path = _sample(curve, 0.0, 2 * math.pi * SPIRAL_TURNS)
    return _paths_stimulus((side, side), [path], start=path[0])


def loops(
    count: int = 8, pitch: float = 12.0, overshoot: float = 4.0, height: float = 20.0
) -> dict[str, Any]:
    """A row of joined cursive "l" loops: each loop rises, turns back at the top, and descends."""
    margin = 4.0
    baseline = margin + height

    def curve(t: float) -> Point:
        return (
            margin + overshoot + pitch * t / (2 * math.pi) + overshoot * math.sin(t),
            baseline - height * (1 - math.cos(t)) / 2,
        )

    path = _sample(curve, 0.0, 2 * math.pi * count)
    width = 2 * margin + 2 * overshoot + pitch * count
    return _paths_stimulus((width, height + 2 * margin), [path], start=path[0])


def _pentagon(cx: float, cy: float, r: float, rotation: float) -> Polyline:
    corners = [
        (cx + r * math.cos(rotation + k * 2 * math.pi / 5), cy - r * math.sin(rotation + k * 2 * math.pi / 5))
        for k in range(5)
    ]
    path: Polyline = []
    for a, b in zip(corners, [*corners[1:], corners[0]], strict=True):
        steps = max(1, math.ceil(math.dist(a, b) / _MAX_CHORD_MM))
        path.extend(
            (a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps) for i in range(steps)
        )
    path.append(corners[0])
    return path


def pentagons(radius: float = 18.0, separation: float = 28.8) -> dict[str, Any]:
    """Two regular pentagons. A corner of each enters the other, so the overlap has four sides."""
    margin = 3.0
    half_height = radius * math.sin(2 * math.pi / 5)
    cy = margin + half_height
    left = margin + radius * math.cos(math.pi / 5)
    right = left + separation
    paths = [_pentagon(left, cy, radius, 0.0), _pentagon(right, cy, radius, math.pi)]
    width = right + radius * math.cos(math.pi / 5) + margin
    return _paths_stimulus((width, 2 * half_height + 2 * margin), paths)


def _polyline(points: Sequence[Point], closed: bool = False) -> Polyline:
    corners = [*points, points[0]] if closed else list(points)
    path: Polyline = []
    for a, b in itertools.pairwise(corners):
        steps = max(1, math.ceil(math.dist(a, b) / _MAX_CHORD_MM))
        path.extend(
            (a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps) for i in range(steps)
        )
    path.append(corners[-1])
    return path


def house() -> dict[str, Any]:
    body = _polyline([(10, 30), (50, 30), (50, 60), (10, 60)], closed=True)
    roof = _polyline([(6, 30), (30, 10), (54, 30)])
    door = _polyline([(26, 60), (26, 46), (34, 46), (34, 60)])
    window = _polyline([(14, 38), (22, 38), (22, 46), (14, 46)], closed=True)
    return _paths_stimulus((60.0, 64.0), [body, roof, door, window])


def practice() -> dict[str, Any]:
    line = _polyline([(6.0, 20.0), (56.0, 20.0)])
    circle = _sample(lambda t: (78.0 + 15.0 * math.cos(t), 20.0 - 15.0 * math.sin(t)), 0.0, 2 * math.pi)
    return _paths_stimulus((100.0, 40.0), [line, circle], start=line[0])


def qualification_bar() -> dict[str, Any]:
    """A 100 mm bar with end ticks. The operator measures it with a steel rule or an ID-1 card."""
    bar = _polyline([(5.0, 10.0), (105.0, 10.0)])
    ticks = [_polyline([(5.0, 4.0), (5.0, 16.0)]), _polyline([(105.0, 4.0), (105.0, 16.0)])]
    return _paths_stimulus((110.0, 20.0), [bar, *ticks])


def qualification_dot() -> dict[str, Any]:
    ring = _sample(lambda t: (10.0 + 1.5 * math.cos(t), 10.0 - 1.5 * math.sin(t)), 0.0, 2 * math.pi)
    return _paths_stimulus((20.0, 20.0), [ring], start=(10.0, 10.0))


def qualification_line() -> dict[str, Any]:
    line = _polyline([(5.0, 10.0), (105.0, 10.0)])
    return _paths_stimulus((110.0, 20.0), [line], start=line[0])


QUALIFICATION_TASKS = ("qual-reference", "qual-hold", "qual-line")
# Each bench task: its base task, the name of the figure in the operator instruction, and the scale.
_BENCH_TASKS: dict[str, tuple[str, str, float]] = {
    "bench-loops-l": ("loops", "loops", 1.0),
    "bench-loops-s": ("loops", "loops", 0.5),
    "bench-spiral-l": ("spiral-trace", "spiral", 1.0),
    "bench-spiral-s": ("spiral-trace", "spiral", 0.5),
}
BENCH_TASKS = tuple(_BENCH_TASKS)


def _scaled(stimulus: dict[str, Any], scale: float) -> dict[str, Any]:
    """The stimulus with every coordinate multiplied by `scale` about the origin of its frame."""

    def grown(p: Sequence[float]) -> Point:
        return (p[0] * scale, p[1] * scale)

    return _paths_stimulus(
        grown(stimulus["frameMM"]),
        [[grown(p) for p in path] for path in stimulus["paths"]],
        start=grown(stimulus["start"]) if "start" in stimulus else None,
    )


def _bench_task(task_id: str, tasks: Sequence[dict[str, Any]]) -> dict[str, Any]:
    base_id, name, scale = _BENCH_TASKS[task_id]
    base = next(t for t in tasks if t["id"] == base_id)
    return _task(
        task_id,
        base["mode"],
        f"Bench: the {name} at scale {scale}.",
        _scaled(base["stimulus"], scale),
        base["completion"]["maxSeconds"],
    )


def _paths_stimulus(frame: Point, paths: Sequence[Polyline], start: Point | None = None) -> dict[str, Any]:
    stimulus: dict[str, Any] = {"kind": "paths", "frameMM": _round(frame), "strokeMM": STIMULUS_STROKE_MM}
    if start is not None:
        stimulus["start"] = _round(start)
    stimulus["paths"] = [[_round(p) for p in path] for path in paths]
    return stimulus


def _task(
    task_id: str, mode: str, instruction: str, stimulus: dict[str, Any] | None, max_seconds: float
) -> dict[str, Any]:
    return {
        "id": task_id,
        "mode": mode,
        "instruction": instruction,
        "stimulus": stimulus,
        "completion": {"kind": "participantDone", "maxSeconds": max_seconds},
    }


def battery() -> dict[str, Any]:
    tasks = [
        _task(
            "practice",
            "trace",
            "Practice: trace the line and the circle. This one does not count.",
            practice(),
            60,
        ),
        _task(
            "spiral-trace",
            "trace",
            "Trace the spiral. Start at the dot near the middle. Try not to lift the pencil.",
            spiral(),
            120,
        ),
        _task(
            "loops", "copy", "Copy this row of loops in one line. Try not to lift the pencil.", loops(), 90
        ),
        _task("pentagons", "copy", "Copy this drawing.", pentagons(), 120),
        _task("house", "copy", "Copy this drawing.", house(), 180),
        _task(
            "clock",
            "free",
            "Draw a clock. Put in all the numbers and set the hands to ten past eleven.",
            None,
            180,
        ),
        _task(
            "sentence",
            "copy",
            "Copy this sentence in your usual handwriting.",
            {
                "kind": "text",
                "frameMM": [110.0, 12.0],
                "text": "The quick brown fox jumps over the lazy dog.",
                "fontSizeMM": 6.0,
            },
            180,
        ),
        _task(
            "qual-reference",
            "trace",
            "Operator: measure the bar with a steel rule or an ID card.",
            qualification_bar(),
            300,
        ),
        _task(
            "qual-hold",
            "trace",
            "Operator: hold the pencil tip still on the dot for 10 seconds.",
            qualification_dot(),
            60,
        ),
        _task(
            "qual-line", "trace", "Operator: draw along the line at an even speed.", qualification_line(), 60
        ),
    ]
    tasks += [_bench_task(task_id, tasks) for task_id in BENCH_TASKS]
    return {"id": BATTERY_ID, "version": BATTERY_VERSION, "tasks": tasks}


@functools.cache
def battery_bytes() -> bytes:
    text = json.dumps(battery(), indent=1, ensure_ascii=False, allow_nan=False)
    text = re.sub(r"\[\s+(-?[0-9.e+-]+),\s+(-?[0-9.e+-]+)\s+\]", r"[\1, \2]", text)
    return (text + "\n").encode()


@functools.cache
def battery_sha256() -> str:
    return hashlib.sha256(battery_bytes()).hexdigest()


def task_spec(task_id: str) -> dict[str, Any]:
    """A new copy of one task specification, parsed from the battery bytes."""
    for task in json.loads(battery_bytes())["tasks"]:
        if task["id"] == task_id:
            return task
    raise KeyError(task_id)
