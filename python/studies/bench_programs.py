"""Geometry, timing, and motion demands of the bench programs in `docs/BENCH_PROTOCOL.md`.

Run it from the repository root:

    UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen \\
        python -I python/studies/bench_programs.py

With `--write <folder>` it also writes one JSON move list per program, fault programs included. With
`--orders <folder>` it writes the run order of each block (protocol section 6.3): the run labels of a
block in program order, shuffled by `random.Random(ORDER_SEED + block)`. `--exclude <id>` drops a
program before the write, for the fast loop program when the motion system fails qualification at
80 mm/s (protocol section 4.2).

The protocol defines the programs. This script computes their path length, duration, speed range,
designed speed variability, peak acceleration, smallest curvature radius, commanded LDLJ and band
power, and generates the registered move lists.

Geometry. The loops and the spiral are the battery curves of `scriptkin.battery`, scaled about the
origin of the stimulus frame. The line is a straight segment in +x whose position along the line
carries a designed oscillation `A (1 - cos(2 pi f t))`. The oscillation modulates the speed at `f`
with the amplitude `2 pi f A`, and keeps the path straight.

Timing. The loops get a curvature-dependent tangential speed: speed is proportional to the cube root
of the curvature radius, with the radius capped at `RADIUS_CAP_MM`, and the constant set so that the
mean tangential speed outside the ramps equals the program speed. The spiral and the line cruise at
the program speed. Every program starts and ends with a ramp of `RAMP_S` at constant tangential
acceleration, from rest to the program speed and back. Moves are sampled every `MOVE_S` seconds
along the time parametrisation. The last move can be shorter.
"""

from __future__ import annotations

import argparse
import csv
import inspect
import json
import math
import random
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np

from scriptkin import battery
from scriptkin.quality import Refusal
from scriptkin.recording import FloatArray
from scriptkin.signal import DEFAULT_PREPROCESSING, derivatives, welch_band_power

SCALES = {"l": 1.0, "s": 0.7}
MOVE_S = 0.010
GRID_HZ = 200.0
RADIUS_CAP_MM = 40.0
RAMP_S = 0.2
PROBE = 40000
LINE_CRUISE_MM = 120.0
LINE_SPEED_MM_S = 20.0
LINE_FRAME_MM = (132.0, 16.0)
LINE_START_MM = (4.0, 8.0)
DWELL_S = 0.5
LIFT_MM = 2.0
LIFT_SPEED_MM_S = 10.0
LIFT_HOLD_S = 0.3
MAIN_REPEATS = 5
FAULT_REPEATS = 3
PLANNED_BLOCKS = 12
BLOCKS = 16
ORDER_SEED = 20261103
WELCH_MIN_WINDOWS = 3

Geometry = Literal["loops", "spiral", "line"]


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
    replace(FAULT_BASE, id=f"fault-{fault}", fault=fault) for fault in ("dwell", "lift", "truncated")
)


@dataclass(frozen=True)
class Move:
    """One timed move to an end point. `lift_mm` above 0 is a lift to that height and back."""

    x_mm: float
    y_mm: float
    duration_s: float
    lift_mm: float = 0.0


@dataclass(frozen=True)
class Program:
    design: Design
    path_length_mm: float
    duration_s: float
    mean_speed_mm_s: float
    cruise_speed_min_mm_s: float
    cruise_speed_max_mm_s: float
    speed_cv: float
    peak_tangential_acceleration_mm_s2: float
    peak_acceleration_mm_s2: float
    smallest_radius_mm: float
    ldlj_command: float
    band_power_command_mm2_s2: float
    frame_mm: tuple[float, float]
    moves: int


def block_order(block: int, programs: list[str]) -> list[tuple[str, int]]:
    """The run order of one block: (program, repeat) labels in program order, shuffled by the block seed."""
    labels = [(p, r) for p in programs for r in range(1, MAIN_REPEATS + 1)]
    labels += [(d.id, r) for d in FAULTS for r in range(1, FAULT_REPEATS + 1)]
    random.Random(ORDER_SEED + block).shuffle(labels)
    return labels


def _loops_defaults() -> dict[str, float]:
    return {k: float(v.default) for k, v in inspect.signature(battery.loops).parameters.items()}


def loops_curve(scale: float) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Return the parameter grid and the scaled loops position and first-derivative arrays."""
    d = _loops_defaults()
    margin, baseline = 4.0, 4.0 + d["height"]
    t = np.linspace(0.0, 2 * math.pi * d["count"], PROBE + 1)
    x = margin + d["overshoot"] + d["pitch"] * t / (2 * math.pi) + d["overshoot"] * np.sin(t)
    y = baseline - d["height"] * (1 - np.cos(t)) / 2
    dx = d["pitch"] / (2 * math.pi) + d["overshoot"] * np.cos(t)
    dy = -d["height"] / 2 * np.sin(t)
    ddx = -d["overshoot"] * np.sin(t)
    ddy = -d["height"] / 2 * np.cos(t)
    curvature = np.abs(dx * ddy - dy * ddx) / (dx**2 + dy**2) ** 1.5 / scale
    return t, np.column_stack((x, y)) * scale, np.column_stack((dx, dy, curvature))


def spiral_curve(scale: float) -> tuple[FloatArray, FloatArray, FloatArray]:
    theta = np.linspace(0.0, 2 * math.pi * battery.SPIRAL_TURNS, PROBE + 1)
    r = battery.SPIRAL_INNER_RADIUS_MM + battery.SPIRAL_TURN_SPACING_MM / (2 * math.pi) * theta
    c = battery.spiral_radius_mm(2 * math.pi * battery.SPIRAL_TURNS) + 2.0
    x, y = c + r * np.cos(theta), c - r * np.sin(theta)
    k = battery.SPIRAL_TURN_SPACING_MM / (2 * math.pi)
    dx, dy = k * np.cos(theta) - r * np.sin(theta), -(k * np.sin(theta) + r * np.cos(theta))
    ddx, ddy = -2 * k * np.sin(theta) - r * np.cos(theta), -(2 * k * np.cos(theta) - r * np.sin(theta))
    curvature = np.abs(dx * ddy - dy * ddx) / (dx**2 + dy**2) ** 1.5 / scale
    return theta, np.column_stack((x, y)) * scale, np.column_stack((dx, dy, curvature))


def frame(design: Design) -> tuple[float, float]:
    if design.geometry == "line":
        return LINE_FRAME_MM
    task = "loops" if design.geometry == "loops" else "spiral-trace"
    spec = battery.task_spec(task)["stimulus"]["frameMM"]
    return (float(spec[0]) * design.scale, float(spec[1]) * design.scale)


def ramp_factor(s: FloatArray, speed_start: float, speed_end: float) -> FloatArray:
    """Constant tangential acceleration over RAMP_S at both ends: speed grows with the root of distance."""
    d_start, d_end = speed_start * RAMP_S / 2, speed_end * RAMP_S / 2
    return np.minimum(1.0, np.minimum(np.sqrt(s / d_start), np.sqrt((s[-1] - s) / d_end)))


def line_path(design: Design) -> tuple[FloatArray, FloatArray, FloatArray]:
    v, f, a = design.speed_mm_s, design.oscillation_hz, design.oscillation_mm
    cruise_s = LINE_CRUISE_MM / v
    if abs(f * cruise_s - round(f * cruise_s)) > 1e-9:
        raise ValueError(f"{design.id}: the cruise must hold a whole number of oscillation cycles.")
    if 2 * math.pi * f * a >= v:
        raise ValueError(f"{design.id}: the speed amplitude must stay below the cruise speed.")
    ramp_mm, acceleration = v * RAMP_S / 2, v / RAMP_S
    t = np.linspace(0.0, 2 * RAMP_S + cruise_s, PROBE + 1)
    u = t - RAMP_S
    w = u - cruise_s
    s = np.where(
        t < RAMP_S,
        acceleration * t**2 / 2,
        np.where(
            u <= cruise_s,
            ramp_mm + v * u + a * (1 - np.cos(2 * math.pi * f * u)),
            ramp_mm + LINE_CRUISE_MM + v * w - acceleration * w**2 / 2,
        ),
    )
    speed = np.where(
        t < RAMP_S,
        acceleration * t,
        np.where(u <= cruise_s, v + 2 * math.pi * f * a * np.sin(2 * math.pi * f * u), v - acceleration * w),
    )
    xy = np.column_stack((LINE_START_MM[0] + s, np.full(len(t), LINE_START_MM[1])))
    return t, xy, speed


def timed_path(design: Design) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Return the times, positions, and tangential speeds of the probe points of one program."""
    if design.geometry == "line":
        return line_path(design)
    t, xy, derivative = (loops_curve if design.geometry == "loops" else spiral_curve)(design.scale)
    chord = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    length = float(chord.sum())
    if design.geometry == "loops":
        radius = np.minimum(1 / derivative[:, 2], RADIUS_CAP_MM)
        shape = radius ** (1 / 3)
        # The cruise duration is sum(chord / (c * shape)); c makes it equal to length / speed.
        mid = (shape[1:] + shape[:-1]) / 2
        v = shape * design.speed_mm_s * float(np.sum(chord / mid)) / length
    else:
        v = np.full(len(t), design.speed_mm_s)
    v = v * ramp_factor(np.concatenate(([0.0], np.cumsum(chord))), float(v[0]), float(v[-1]))
    times = np.concatenate(([0.0], np.cumsum(chord / ((v[1:] + v[:-1]) / 2))))
    return times, xy, v


def move_end_times(times: FloatArray, extra: tuple[float, ...] = ()) -> FloatArray:
    """Every MOVE_S, plus the extra times and the end. Near-duplicate times collapse."""
    grid = np.arange(MOVE_S, times[-1], MOVE_S)
    ends = np.sort(np.concatenate((grid, np.asarray(extra, dtype=float), [times[-1]])))
    return ends[np.concatenate(([True], np.diff(ends) > 1e-9))]


def moves(times: FloatArray, xy: FloatArray, ends: FloatArray) -> list[Move]:
    points = np.column_stack([np.interp(ends, times, xy[:, k]) for k in range(2)])
    durations = np.diff(np.concatenate(([0.0], ends)))
    return [Move(float(p[0]), float(p[1]), float(d)) for p, d in zip(points, durations, strict=True)]


def fault_program(design: Design) -> tuple[list[Move], float, float]:
    """The base program with one change after loop 4 (parameter 8 pi of 16 pi), its path, and duration."""
    times, xy, _ = timed_path(FAULT_BASE)
    split = float(times[PROBE // 2])
    ends = move_end_times(times, (split,))
    full = moves(times, xy, ends)
    k = int(np.searchsorted(ends, split - 1e-9)) + 1
    head, tail = full[:k], full[k:]
    at = head[-1]
    if design.fault == "truncated":
        result = head
    elif design.fault == "dwell":
        result = [*head, Move(at.x_mm, at.y_mm, DWELL_S), *tail]
    elif design.fault == "lift":
        result = [*head, Move(at.x_mm, at.y_mm, 2 * LIFT_MM / LIFT_SPEED_MM_S + LIFT_HOLD_S, LIFT_MM), *tail]
    else:
        raise ValueError(f"unknown fault {design.fault}")
    points = np.array([[float(xy[0, 0]), float(xy[0, 1])]] + [[m.x_mm, m.y_mm] for m in result])
    length = float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())
    return result, length, float(sum(m.duration_s for m in result))


def program(design: Design) -> tuple[Program, list[Move]]:
    times, xy, v = timed_path(design)
    grid = np.arange(0.0, times[-1], 1 / GRID_HZ)
    speed_grid = np.interp(grid, times, v)
    velocity = np.column_stack([np.gradient(np.interp(grid, times, xy[:, k]), grid) for k in range(2)])
    acceleration = np.column_stack([np.gradient(velocity[:, k], grid) for k in range(2)])
    tangential = np.gradient(np.linalg.norm(velocity, axis=1), grid)
    cruise = (times > RAMP_S) & (times < times[-1] - RAMP_S)
    if design.geometry == "line":
        smallest_radius = math.inf
    else:
        curvature = (loops_curve if design.geometry == "loops" else spiral_curve)(design.scale)[2][:, 2]
        smallest_radius = float(1 / curvature.max())
    move_list = moves(times, xy, move_end_times(times))
    length = float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum())
    # The library's derivative (Savitzky-Golay, window 11, order 3) on the 200 Hz grid, as a run is analysed.
    settings = DEFAULT_PREPROCESSING
    grid_xy = np.column_stack([np.interp(grid, times, xy[:, k]) for k in range(2)])
    jerk = derivatives(grid_xy, 1 / GRID_HZ, window=settings.window, polynomial=settings.polynomial, order=3)
    jerk_integral = float(np.trapezoid(np.sum(jerk**2, axis=1), grid))
    ldlj = float(np.log(times[-1] ** 5 / length**2 * jerk_integral))
    grid_speed = np.linalg.norm(
        derivatives(grid_xy, 1 / GRID_HZ, window=settings.window, polynomial=settings.polynomial), axis=1
    )
    band = welch_band_power(
        [grid_speed],
        GRID_HZ,
        settings.band_hz,
        window_s=settings.welch_window_s,
        min_windows=WELCH_MIN_WINDOWS,
    )
    if isinstance(band, Refusal):
        raise RuntimeError(f"{design.id} is too short for the band power: {band}")
    summary = Program(
        design,
        length,
        float(times[-1]),
        length / float(times[-1]),
        float(v[cruise].min()),
        float(v[cruise].max()),
        float(np.std(speed_grid, ddof=1) / np.mean(speed_grid)),
        float(np.abs(tangential).max()),
        float(np.linalg.norm(acceleration, axis=1).max()),
        smallest_radius,
        ldlj,
        band,
        frame(design),
        len(move_list),
    )
    return summary, move_list


def write_program(folder: Path, payload: dict[str, Any], program_id: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    payload["battery"] = {"id": battery.BATTERY_ID, "version": battery.BATTERY_VERSION}
    payload["move_s"] = MOVE_S
    payload["ramp_s"] = RAMP_S
    (folder / f"{program_id}.json").write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Geometry, timing, and motion demands of the bench programs."
    )
    parser.add_argument("--write", type=Path, help="folder for the JSON move lists")
    parser.add_argument("--orders", type=Path, help="folder for the block run orders")
    parser.add_argument("--exclude", action="append", default=[], metavar="ID", help="drop a main program")
    args = parser.parse_args()
    unknown = set(args.exclude) - {d.id for d in MAIN}
    if unknown:
        parser.error(f"unknown program {', '.join(sorted(unknown))}")
    designs = [d for d in MAIN if d.id not in args.exclude]
    rows: list[Program] = []
    for design in designs:
        summary, move_list = program(design)
        rows.append(summary)
        if args.write:
            payload = {"program": asdict(summary), "moves": [asdict(m) for m in move_list]}
            write_program(args.write, payload, design.id)
    fault_rows: list[tuple[Design, float, float, int]] = []
    for design in FAULTS:
        move_list, length, duration = fault_program(design)
        fault_rows.append((design, length, duration, len(move_list)))
        if args.write:
            payload = {
                "program": asdict(design),
                "base": FAULT_BASE.id,
                "path_length_mm": length,
                "duration_s": duration,
                "moves": [asdict(m) for m in move_list],
            }
            write_program(args.write, payload, design.id)
    header = (
        "program", "task", "length mm", "duration s", "mean speed", "cruise min", "cruise max", "speed CV",
        "peak tang. acc.", "peak acc.", "min radius", "LDLJ", "band power", "moves",
    )  # fmt: skip
    print("\t".join(header))
    for r in rows:
        radius = "straight" if math.isinf(r.smallest_radius_mm) else f"{r.smallest_radius_mm:.2f}"
        print(
            "\t".join(
                [
                    r.design.id,
                    r.design.task,
                    f"{r.path_length_mm:.1f}",
                    f"{r.duration_s:.2f}",
                    f"{r.mean_speed_mm_s:.1f}",
                    f"{r.cruise_speed_min_mm_s:.1f}",
                    f"{r.cruise_speed_max_mm_s:.1f}",
                    f"{r.speed_cv:.3f}",
                    f"{r.peak_tangential_acceleration_mm_s2:.0f}",
                    f"{r.peak_acceleration_mm_s2:.0f}",
                    radius,
                    f"{r.ldlj_command:.2f}",
                    f"{r.band_power_command_mm2_s2:.4f}",
                    str(r.moves),
                ]
            )
        )
    print("\nfault program\tbase\tlength mm\tduration s\tmoves")
    for design, length, duration, count in fault_rows:
        print(f"{design.id}\t{FAULT_BASE.id}\t{length:.1f}\t{duration:.2f}\t{count}")
    bounds = [r.frame_mm for r in rows]
    print(f"\nlargest frame: {max(b[0] for b in bounds):.1f} x {max(b[1] for b in bounds):.1f} mm")
    block_s = MAIN_REPEATS * sum(r.duration_s for r in rows) + FAULT_REPEATS * sum(f[2] for f in fault_rows)
    runs = MAIN_REPEATS * len(rows) + FAULT_REPEATS * len(fault_rows)
    print(
        f"per block: {runs} runs, {block_s / 60:.1f} min of motion; {PLANNED_BLOCKS} blocks: "
        f"{runs * PLANNED_BLOCKS} runs, {block_s * PLANNED_BLOCKS / 3600:.1f} h of motion"
    )
    if args.orders:
        args.orders.mkdir(parents=True, exist_ok=True)
        for block in range(1, BLOCKS + 1):
            with (args.orders / f"block-{block:02d}.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("position", "program", "repeat"))
                for position, (program_id, repeat) in enumerate(
                    block_order(block, [r.design.id for r in rows]), 1
                ):
                    writer.writerow((position, program_id, repeat))
        print(f"wrote {BLOCKS} block orders with seed {ORDER_SEED} + block to {args.orders}")


if __name__ == "__main__":
    main()
