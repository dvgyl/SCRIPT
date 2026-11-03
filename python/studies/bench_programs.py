"""Geometry, timing, and motion demands of the bench programs in `docs/BENCH_PROTOCOL.md`.

Run it from the repository root:

    UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen \\
        python -I python/studies/bench_programs.py

With `--write <folder>` it also writes one JSON move list per program. With `--orders <folder>` it
writes the run order of each of the 12 blocks (protocol section 6.3): the 49 run labels of a block in
program order, shuffled by `random.Random(ORDER_SEED + block)`. The protocol defines the programs; this
script computes their path length, duration, speed range, designed speed variability, peak
acceleration, and smallest curvature radius, and generates the registered move lists.

The curves are the battery curves of `scriptkin.battery` (`loops` and `spiral`), scaled about the
origin of the stimulus frame. The loops get a curvature-dependent tangential speed: speed is
proportional to the cube root of the curvature radius, with the radius capped at `RADIUS_CAP_MM`,
and the constant set so that the mean tangential speed over the run equals the program speed. The
spiral gets a constant tangential speed. Moves are sampled every `MOVE_S` seconds along the time
parametrisation. The last move can be shorter.
"""

from __future__ import annotations

import argparse
import csv
import inspect
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from scriptkin import battery
from scriptkin.quality import Refusal
from scriptkin.recording import FloatArray
from scriptkin.signal import DEFAULT_PREPROCESSING, derivatives, welch_band_power

SCALES = (1.0, 0.5)
SPEEDS_MM_S = (20.0, 40.0)
MOVE_S = 0.010
GRID_HZ = 200.0
RADIUS_CAP_MM = 40.0
PROBE = 40000
FAULT_PROGRAMS = ("fault-dwell", "fault-lift", "fault-truncated")
MAIN_REPEATS = 5
FAULT_REPEATS = 3
BLOCKS = 12
ORDER_SEED = 20261103


def block_order(block: int, programs: list[str]) -> list[tuple[str, int]]:
    """The run order of one block: (program, repeat) labels in program order, shuffled by the block seed."""
    labels = [(p, r) for p in programs for r in range(1, MAIN_REPEATS + 1)]
    labels += [(p, r) for p in FAULT_PROGRAMS for r in range(1, FAULT_REPEATS + 1)]
    random.Random(ORDER_SEED + block).shuffle(labels)
    return labels


@dataclass(frozen=True)
class Program:
    id: str
    task: str
    scale: float
    speed_mm_s: float
    path_length_mm: float
    duration_s: float
    speed_min_mm_s: float
    speed_max_mm_s: float
    speed_cv: float
    peak_tangential_acceleration_mm_s2: float
    peak_acceleration_mm_s2: float
    smallest_radius_mm: float
    ldlj_command: float
    band_power_command_mm2_s2: float
    frame_mm: tuple[float, float]
    moves: int


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


def frame(task: str, scale: float) -> tuple[float, float]:
    spec = battery.task_spec(task)["stimulus"]["frameMM"]
    return (float(spec[0]) * scale, float(spec[1]) * scale)


def timed_path(task: str, scale: float, speed: float) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Return the times, positions, and tangential speeds of the probe points of one program."""
    t, xy, derivative = (loops_curve if task == "loops" else spiral_curve)(scale)
    chord = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    length = float(chord.sum())
    if task == "loops":
        radius = np.minimum(1 / derivative[:, 2], RADIUS_CAP_MM)
        shape = radius ** (1 / 3)
        # The duration is sum(chord / (c * shape)); c makes it equal to length / speed.
        mid = (shape[1:] + shape[:-1]) / 2
        v = shape * speed * float(np.sum(chord / mid)) / length
    else:
        v = np.full(len(t), speed)
    times = np.concatenate(([0.0], np.cumsum(chord / ((v[1:] + v[:-1]) / 2))))
    return times, xy, v


def moves(times: FloatArray, xy: FloatArray) -> list[tuple[float, float, float]]:
    """Sample the path every MOVE_S seconds; each move is (end x, end y, duration)."""
    end_times = np.arange(MOVE_S, times[-1], MOVE_S)
    if len(end_times) == 0 or times[-1] - end_times[-1] > 1e-9:
        end_times = np.append(end_times, times[-1])
    points = np.column_stack([np.interp(end_times, times, xy[:, k]) for k in range(2)])
    durations = np.diff(np.concatenate(([0.0], end_times)))
    return [(float(p[0]), float(p[1]), float(d)) for p, d in zip(points, durations, strict=True)]


def program(task: str, scale: float, speed: float) -> tuple[Program, list[tuple[float, float, float]]]:
    times, xy, v = timed_path(task, scale, speed)
    grid = np.arange(0.0, times[-1], 1 / GRID_HZ)
    speed_grid = np.interp(grid, times, v)
    velocity = np.column_stack([np.gradient(np.interp(grid, times, xy[:, k]), grid) for k in range(2)])
    acceleration = np.column_stack([np.gradient(velocity[:, k], grid) for k in range(2)])
    tangential = np.gradient(np.linalg.norm(velocity, axis=1), grid)
    curvature = (loops_curve if task == "loops" else spiral_curve)(scale)[2][:, 2]
    move_list = moves(times, xy)
    short = "s1" if scale == 1.0 else f"s{scale:g}".replace("0.", "0")
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
    band = welch_band_power([grid_speed], GRID_HZ, settings.band_hz, window_s=settings.welch_window_s)
    if isinstance(band, Refusal):
        raise RuntimeError(
            f"{task} at scale {scale} and {speed} mm/s is too short for the band power: {band}"
        )
    summary = Program(
        f"{task.split('-')[0]}-{short}-v{speed:g}",
        task,
        scale,
        speed,
        float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum()),
        float(times[-1]),
        float(v.min()),
        float(v.max()),
        float(np.std(speed_grid, ddof=1) / np.mean(speed_grid)),
        float(np.abs(tangential).max()),
        float(np.linalg.norm(acceleration, axis=1).max()),
        float(1 / curvature.max()),
        ldlj,
        band,
        frame(task, scale),
        len(move_list),
    )
    return summary, move_list


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Geometry, timing, and motion demands of the bench programs."
    )
    parser.add_argument("--write", type=Path, help="folder for the JSON move lists")
    parser.add_argument("--orders", type=Path, help="folder for the block run orders")
    args = parser.parse_args()
    rows: list[dict[str, Any]] = []
    for task in ("loops", "spiral-trace"):
        for scale in SCALES:
            for speed in SPEEDS_MM_S:
                summary, move_list = program(task, scale, speed)
                rows.append(asdict(summary))
                if args.write:
                    args.write.mkdir(parents=True, exist_ok=True)
                    payload = {
                        "program": asdict(summary),
                        "battery": {"id": battery.BATTERY_ID, "version": battery.BATTERY_VERSION},
                        "move_s": MOVE_S,
                        "moves": [{"x_mm": x, "y_mm": y, "duration_s": d} for x, y, d in move_list],
                    }
                    (args.write / f"{summary.id}.json").write_text(
                        json.dumps(payload, indent=1) + "\n", encoding="utf-8"
                    )
    header = (
        "program", "length mm", "duration s", "speed min", "speed max", "speed CV",
        "peak tang. acc.", "peak acc.", "min radius", "LDLJ", "band power", "moves",
    )  # fmt: skip
    print("\t".join(header))
    for r in rows:
        print(
            "\t".join(
                [
                    r["id"],
                    f"{r['path_length_mm']:.1f}",
                    f"{r['duration_s']:.2f}",
                    f"{r['speed_min_mm_s']:.1f}",
                    f"{r['speed_max_mm_s']:.1f}",
                    f"{r['speed_cv']:.3f}",
                    f"{r['peak_tangential_acceleration_mm_s2']:.0f}",
                    f"{r['peak_acceleration_mm_s2']:.0f}",
                    f"{r['smallest_radius_mm']:.2f}",
                    f"{r['ldlj_command']:.2f}",
                    f"{r['band_power_command_mm2_s2']:.4f}",
                    str(r["moves"]),
                ]
            )
        )
    bounds = [r["frame_mm"] for r in rows]
    print(f"largest frame: {max(b[0] for b in bounds):.1f} x {max(b[1] for b in bounds):.1f} mm")
    if args.orders:
        args.orders.mkdir(parents=True, exist_ok=True)
        for block in range(1, BLOCKS + 1):
            with (args.orders / f"block-{block:02d}.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("position", "program", "repeat"))
                for position, (program_id, repeat) in enumerate(
                    block_order(block, [r["id"] for r in rows]), 1
                ):
                    writer.writerow((position, program_id, repeat))
        print(f"wrote {BLOCKS} block orders with seed {ORDER_SEED} + block to {args.orders}")


if __name__ == "__main__":
    main()
