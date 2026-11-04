"""Translate the bench move lists into controller files for the NextDraw 8511 (protocol section 4.1).

Run it from the repository root after `bench_programs.py --write bench/programs`:

    UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen \\
        python -I python/studies/bench_controller.py bench/programs

For each move list `<program>.json` in the folder, it writes the controller file `<program>.ebb` and the
record `<program>.ebb.csv`. The SHA-256 of the controller file is the trajectory digest of the program
(section 2.4). `scriptkin contract regenerate` then writes it into `contract/bench_programs.json`. The
script writes nothing when it refuses a program.

The controller is the EBB board of the NextDraw 8511. The NextDraw API has no timed move, so the
controller file holds raw EBB commands, one per line. A sender writes each line with a carriage return
through `usb_command()` or a serial connection. The file is steps 3 to 5 of the run sequence (section 5.2).
It starts with the stylus at the lift height above the start point, and it ends with the stylus at the
lift height above the last end point:

1. `S2` lowers the stylus to the surface at 10 mm/s or less. Its delay holds the queue until the servo
   arrives. `SM,500,0,0,3` then waits 0.5 s.
2. One `SM,<ms>,<steps 1>,<steps 2>,3` executes each move. `Clear` 3 empties both step accumulators, so
   each move ends within about one 40 µs tick of its duration. A lift move is two `S2` commands: up to its
   height, then down to the surface, at 10 mm/s or less. Their delays add up to the duration of the move.
3. `SM,300,0,0,3` waits 0.3 s, and `S2` lifts the stylus at 10 mm/s or less.

Positions. The stimulus x and y run along the x and y of the travel. Motor 1 is at 80 (x + y) steps and
motor 2 at 80 (x - y) steps, with x and y in millimetres from the stimulus origin. The script rounds the
motor positions of the start point and of every end point to whole steps, and each `SM` moves by the
difference. An end point then differs from the move list by at most 1 / 160 = 0.00625 mm, and the error
does not accumulate. The origin is a position of the machine, so it is on the grid of whole steps.

Times. `SM` takes whole milliseconds. The script rounds the end time of every move, counted from the first
move, to whole milliseconds. An end time then differs from the move list by at most 0.5 ms, and the error
does not accumulate. A move of whole milliseconds keeps its duration. A move that rounds to 0 ms and to
0 steps gives no command.

The script refuses a move above the step rate of a motor, a point outside the travel, a move of 0 ms
with steps, a lift that moves in x or y, and a lift too short for its height at 10 mm/s.

The record holds one row per point: the start point, then the end point of each move. A row gives the
end time and the position of the move list, the end time and the motor positions of the controller file,
the quantised position in millimetres, and its distance from the move list. The bounds are
`Machine.end_point_bound_mm` and `END_TIME_BOUND_MS`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from scriptkin.bench import CONTROLLER_SUFFIX
from scriptkin.bench.moves import MoveList, MoveListError, read_move_lists

LIFT_SPEED_MM_S = 10.0
"""The fastest vertical speed of the stylus (section 4.1)."""
MIN_LIFT_MM = 10.0
BEFORE_MS = 500
AFTER_MS = 300
CLEAR = 3
END_TIME_BOUND_MS = 0.5
SM_DELAY_MAX_MS = 100_000
"""`SM` caps a move of 0 steps on both motors at this duration."""
S2_MAX = 65_535
"""The largest pulse, rate, and delay of `S2`."""
RECORD_COLUMNS = (
    "point",
    "end_s",
    "end_ms",
    "x_mm",
    "y_mm",
    "motor1",
    "motor2",
    "quantised_x_mm",
    "quantised_y_mm",
    "error_mm",
)


class ControllerError(ValueError):
    pass


@dataclass(frozen=True)
class Machine:
    """The facts of the motion system that the translation uses (protocol section 4.3)."""

    steps_per_mm: int
    """Steps of each motor per millimetre along x or y. 80 is 16 microsteps (`EM,1,1`)."""
    max_step_rate: int
    """The largest rate of one motor, in steps per second, that `SM` accepts."""
    travel_mm: tuple[float, float]
    """The usable travel in x and y from the home position."""
    origin_mm: tuple[float, float]
    """The position of the stimulus origin in the travel."""
    servo_pin: int
    """The RP pin of the pen-lift servo for `S2`."""
    servo_cycle_ms: int
    """The time between two pulses of the servo: 3 ms for each servo channel of `SC,8`."""
    servo_surface: int
    """The servo pulse, in units of 1/12 µs, at which the stylus rests on the surface."""
    servo_per_mm: float
    """The change of the servo pulse per millimetre of stylus height."""
    lift_mm: float
    """The height of the stylus above the surface before and after a run (section 5.2)."""

    @property
    def end_point_bound_mm(self) -> float:
        """The largest distance between a quantised point and its point of the move list."""
        return 1 / (2 * self.steps_per_mm)

    @property
    def servo_rate(self) -> int:
        """The `S2` rate, in pulse units per servo cycle, that moves the stylus at 10 mm/s or less."""
        return math.floor(abs(self.servo_per_mm) * LIFT_SPEED_MM_S * self.servo_cycle_ms / 1000)

    def pulse(self, height_mm: float) -> int:
        """The servo pulse of a height above the surface. It rounds away from the surface, so that the
        lift height is never below its setting."""
        units = math.ceil(height_mm * abs(self.servo_per_mm))
        return self.servo_surface + (units if self.servo_per_mm > 0 else -units)

    def slew_ms(self, height_mm: float) -> int:
        """The time that the servo takes from the surface to the height at `servo_rate`."""
        cycles = math.ceil(abs(self.pulse(height_mm) - self.servo_surface) / self.servo_rate)
        return cycles * self.servo_cycle_ms


# Provisional until the owner measures the machine (section 4.3). The servo values are those of the
# AxiDraw brushless servo: pin RB2, one channel, 5400 to 12600 for 17 mm, and the surface at 40 percent.
NEXTDRAW_8511 = Machine(
    steps_per_mm=80,
    max_step_rate=25_000,
    travel_mm=(300.0, 218.0),
    origin_mm=(20.0, 20.0),
    servo_pin=5,
    servo_cycle_ms=3,
    servo_surface=8280,
    servo_per_mm=7200 / 17,
    lift_mm=10.0,
)


@dataclass(frozen=True)
class Point:
    """One point of a program in the controller file: its end time in whole milliseconds from the first
    move, and its motor positions in whole steps from the stimulus origin."""

    end_ms: int
    motor: tuple[int, int]


@dataclass(frozen=True)
class Translation:
    """The controller file of one program and the quantised points that it commands."""

    commands: tuple[str, ...]
    points: tuple[Point, ...]
    """The start point, then the end point of every move."""

    def text(self) -> str:
        return "".join(f"{command}\n" for command in self.commands)


def motor_position(x_mm: float, y_mm: float, machine: Machine) -> tuple[int, int]:
    k = machine.steps_per_mm
    return round(k * (x_mm + y_mm)), round(k * (x_mm - y_mm))


def position_mm(motor: tuple[int, int], machine: Machine) -> tuple[float, float]:
    k = 2 * machine.steps_per_mm
    return (motor[0] + motor[1]) / k, (motor[0] - motor[1]) / k


def sm(ms: int, steps: tuple[int, int]) -> str:
    return f"SM,{ms},{steps[0]},{steps[1]},{CLEAR}"


def s2(pulse: int, delay_ms: int, machine: Machine) -> str:
    if not 1 <= pulse <= S2_MAX or not 0 <= delay_ms <= S2_MAX:
        raise ControllerError(f"S2 takes a pulse and a delay up to {S2_MAX}, not {pulse} and {delay_ms} ms.")
    return f"S2,{pulse},{machine.servo_pin},{machine.servo_rate},{delay_ms}"


def check_machine(machine: Machine) -> None:
    if machine.lift_mm < MIN_LIFT_MM:
        raise ControllerError(f"The lift height is {machine.lift_mm} mm. Section 4.1 needs {MIN_LIFT_MM} mm.")
    if not 1 <= machine.servo_rate <= S2_MAX:
        raise ControllerError(f"No S2 rate moves the stylus at {LIFT_SPEED_MM_S} mm/s or less.")


def _point_name(index: int) -> str:
    return "the start point" if index == 0 else f"the end point of move {index}"


def translate(move_list: MoveList, machine: Machine = NEXTDRAW_8511) -> Translation:
    """The controller file of one move list. A move that the machine cannot execute as registered
    raises `ControllerError`."""
    check_machine(machine)
    name = move_list.program.id
    path = move_list.path()
    points = tuple(
        Point(round(1000 * float(t)), motor_position(float(x), float(y), machine))
        for t, (x, y) in zip(path.t, path.xy, strict=True)
    )
    travel_x, travel_y = machine.travel_mm
    for i, point in enumerate(points):
        qx, qy = position_mm(point.motor, machine)
        x, y = machine.origin_mm[0] + qx, machine.origin_mm[1] + qy
        if not (0 <= x <= travel_x and 0 <= y <= travel_y):
            raise ControllerError(
                f"{name}: {_point_name(i)} is at ({x:.3f}, {y:.3f}) mm, outside the travel of"
                f" {travel_x:g} x {travel_y:g} mm."
            )
    lift_ms = machine.slew_ms(machine.lift_mm)
    commands = [s2(machine.servo_surface, lift_ms, machine), sm(BEFORE_MS, (0, 0))]
    for i, move in enumerate(move_list.moves, start=1):
        ms = points[i].end_ms - points[i - 1].end_ms
        steps = (points[i].motor[0] - points[i - 1].motor[0], points[i].motor[1] - points[i - 1].motor[1])
        where = f"{name}: move {i}"
        if move.lift_mm > 0:
            if steps != (0, 0):
                raise ControllerError(f"{where} lifts the stylus and moves {steps} steps.")
            slew = machine.slew_ms(move.lift_mm)
            if 2 * slew > ms:
                raise ControllerError(
                    f"{where} lasts {ms} ms. A lift of {move.lift_mm:g} mm and back at"
                    f" {LIFT_SPEED_MM_S:g} mm/s or less takes {2 * slew} ms."
                )
            commands.append(s2(machine.pulse(move.lift_mm), ms - slew, machine))
            commands.append(s2(machine.servo_surface, slew, machine))
        elif ms == 0:
            if steps != (0, 0):
                raise ControllerError(f"{where} lasts 0 ms after rounding and moves {steps} steps.")
        else:
            for motor, count in enumerate(steps, start=1):
                if abs(count) * 1000 > machine.max_step_rate * ms:
                    raise ControllerError(
                        f"{where} needs {abs(count) * 1000 / ms:.0f} steps/s on motor {motor}."
                        f" The limit is {machine.max_step_rate} steps/s."
                    )
            if steps == (0, 0) and ms > SM_DELAY_MAX_MS:
                raise ControllerError(f"{where} waits {ms} ms. SM caps a wait at {SM_DELAY_MAX_MS} ms.")
            commands.append(sm(ms, steps))
    commands += [sm(AFTER_MS, (0, 0)), s2(machine.pulse(machine.lift_mm), lift_ms, machine)]
    return Translation(tuple(commands), points)


def record_rows(move_list: MoveList, translation: Translation, machine: Machine) -> list[tuple[object, ...]]:
    path = move_list.path()
    rows: list[tuple[object, ...]] = []
    for i, (t, (x, y), point) in enumerate(zip(path.t, path.xy, translation.points, strict=True)):
        qx, qy = position_mm(point.motor, machine)
        error = math.hypot(qx - float(x), qy - float(y))
        quantised = (f"{qx:.5f}", f"{qy:.5f}", f"{error:.6f}")
        rows.append((i, f"{t:.6f}", point.end_ms, repr(float(x)), repr(float(y)), *point.motor, *quantised))
    return rows


def write_translation(folder: Path, move_list: MoveList, translation: Translation, machine: Machine) -> Path:
    controller = folder / f"{move_list.program.id}{CONTROLLER_SUFFIX}"
    controller.write_text(translation.text(), encoding="ascii")
    with (folder / f"{controller.name}.csv").open("w", encoding="ascii", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(RECORD_COLUMNS)
        writer.writerows(record_rows(move_list, translation, machine))
    return controller


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the NextDraw 8511 controller file of each move list.")
    parser.add_argument("folder", type=Path, help="the folder of the move lists, bench/programs at the lock")
    args = parser.parse_args()
    machine = NEXTDRAW_8511
    try:
        move_lists = read_move_lists(args.folder)
        if not move_lists:
            raise ControllerError(f"{args.folder} holds no move list.")
        translations = {pid: translate(ml, machine) for pid, ml in move_lists.items()}
    except (MoveListError, ControllerError) as e:
        sys.exit(f"bench_controller: {e}")
    header = ("program", "moves", "commands", "duration ms", "end-point error mm", "end-time error ms")
    print("\t".join((*header, "peak steps/s", "SHA-256")))
    low, high = [math.inf, math.inf], [-math.inf, -math.inf]
    for pid, translation in translations.items():
        move_list = move_lists[pid]
        controller = write_translation(args.folder, move_list, translation, machine)
        path = move_list.path()
        quantised = [position_mm(p.motor, machine) for p in translation.points]
        errors = [math.dist(q, (float(x), float(y))) for q, (x, y) in zip(quantised, path.xy, strict=True)]
        lags = [abs(p.end_ms - 1000 * float(t)) for p, t in zip(translation.points, path.t, strict=True)]
        rates = [
            max(abs(b.motor[k] - a.motor[k]) for k in range(2)) * 1000 / (b.end_ms - a.end_ms)
            for a, b in zip(translation.points, translation.points[1:], strict=False)
            if b.end_ms > a.end_ms
        ]
        for k in range(2):
            low[k] = min(low[k], *(q[k] for q in quantised))
            high[k] = max(high[k], *(q[k] for q in quantised))
        digest = hashlib.sha256(controller.read_bytes()).hexdigest()
        print(
            f"{pid}\t{len(move_list.moves)}\t{len(translation.commands)}\t{translation.points[-1].end_ms}"
            f"\t{max(errors):.5f}\t{max(lags):.3f}\t{max(rates):.0f}\t{digest}"
        )
    print(
        f"\nbounds: end point {machine.end_point_bound_mm:g} mm, end time {END_TIME_BOUND_MS:g} ms,"
        f" step rate {machine.max_step_rate} steps/s per motor"
    )
    (travel_x, travel_y), (origin_x, origin_y) = machine.travel_mm, machine.origin_mm
    print(
        f"origin ({origin_x:g}, {origin_y:g}) mm in the travel of {travel_x:g} x {travel_y:g} mm."
        f" Every program fits for an origin from ({-low[0]:.2f}, {-low[1]:.2f})"
        f" to ({travel_x - high[0]:.2f}, {travel_y - high[1]:.2f}) mm."
    )
    speed = machine.servo_rate / abs(machine.servo_per_mm) / machine.servo_cycle_ms * 1000
    print(
        f"servo: pin RP{machine.servo_pin}, cycle {machine.servo_cycle_ms} ms, rate {machine.servo_rate}"
        f" ({speed:.2f} mm/s), surface {machine.servo_surface}, lift {machine.lift_mm:g} mm at"
        f" {machine.pulse(machine.lift_mm)} in {machine.slew_ms(machine.lift_mm)} ms"
    )
    print(f"wrote {len(translations)} controller files to {args.folder}")


if __name__ == "__main__":
    main()
