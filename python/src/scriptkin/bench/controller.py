"""The translation of a move list into a controller file for the NextDraw 8511 (protocol section 4.1).

The controller is the EBB board of the NextDraw 8511. The NextDraw API has no timed move, so the
controller file `<program>.ebb` holds raw EBB commands, one per line. A sender writes each line with a
carriage return. `python/studies/bench_controller.py` writes the controller files and their records.

The file is steps 3 to 5 of the run sequence (section 5.2). It starts with the stylus at the lift height
above the start point, and it ends with the stylus at the lift height above the last end point:

1. `S2` lowers the stylus to the surface at 10 mm/s or less. Its delay holds the queue until the servo
   arrives. `SM,500,0,0,3` then waits 0.5 s.
2. One `SM,<ms>,<steps 1>,<steps 2>,3` executes each move. A lift move is two `S2` commands: up to its
   height, then down to the surface, at 10 mm/s or less. Their delays add up to the duration of the move.
3. `SM,300,0,0,3` waits 0.3 s, and `S2` lifts the stylus at 10 mm/s or less.

The file needs this state of the EBB, which the sender sets before a session: 16 microsteps (`EM,1,1`
before homing), the servo channels of `Machine.servo_channels` (`SC,8`), and servo power for the whole
run (`SR`).

Positions. The stimulus x and y run along the x and y of the travel. Motor 1 is at 80 (x + y) steps and
motor 2 at 80 (x - y) steps, with x and y in millimetres from the stimulus origin. The translation rounds
the motor positions of the start point and of every end point to whole steps, and each `SM` moves by the
difference. An end point then differs from the move list by at most 1 / 160 = 0.00625 mm, and the error
does not accumulate. The origin is a position of the machine, so it is on the grid of whole steps.

Times. `SM` takes whole milliseconds. The translation rounds the end time of every move, counted from the
first move, to whole milliseconds. An end time then differs from the move list by at most 0.5 ms, and the
error does not accumulate. A move of whole milliseconds keeps its duration. A move that rounds to 0 ms
and to 0 steps gives no command. On the EBB, `Clear` 3 empties both step accumulators before each move.
The firmware truncates the step rate, so each `SM` with steps ends one 40 µs tick late, and a program of
10 ms moves runs about 0.4 percent long. Section 4.2 measures the real durations.

The translation refuses a move above the step rate of a motor, a point outside the travel, a move of
0 ms with steps, a lift that moves in x or y, and a lift too short for its height at 10 mm/s.
"""

from __future__ import annotations

import csv
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

from scriptkin.bench import PROGRAMS
from scriptkin.bench.moves import MoveList, read_move_list

CONTROLLER_SUFFIX = ".ebb"
"""The controller file of a program is `<program>.ebb` beside its move list `<program>.json`."""
LIFT_SPEED_MM_S = 10.0
"""The fastest vertical speed of the stylus (section 4.1)."""
MIN_LIFT_MM = 10.0
BEFORE_MS = 500
AFTER_MS = 300
CLEAR = 3
END_TIME_BOUND_MS = 0.5
SERVO_SLOT_MS = 3
"""The EBB gives each servo channel a slot of 3 ms in the servo cycle."""
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
    servo_channels: int
    """The servo channels that the sender sets with `SC,8`."""
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
    def servo_cycle_ms(self) -> int:
        """The time between two pulses of the servo, when `S2` changes the pulse by its rate."""
        return SERVO_SLOT_MS * self.servo_channels

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
    servo_channels=1,
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


def whole_ms(t_s: float) -> int:
    """A time in whole milliseconds, half up. The time is first rounded to 1 ns, so that the float error
    of a sum of durations cannot add or remove a millisecond from a move of whole milliseconds."""
    return math.floor(round(1000 * t_s, 6) + 0.5)


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
        Point(whole_ms(float(t)), motor_position(float(x), float(y), machine))
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
    """One row per point of `RECORD_COLUMNS`: the end time and the position of the move list, the end time
    and the motor positions of the controller file, the quantised position, and its distance."""
    path = move_list.path()
    rows: list[tuple[object, ...]] = []
    for i, (t, (x, y), point) in enumerate(zip(path.t, path.xy, translation.points, strict=True)):
        qx, qy = position_mm(point.motor, machine)
        error = math.hypot(qx - float(x), qy - float(y))
        quantised = (f"{qx:.5f}", f"{qy:.5f}", f"{error:.6f}")
        rows.append((i, f"{t:.6f}", point.end_ms, repr(float(x)), repr(float(y)), *point.motor, *quantised))
    return rows


def write_translation(folder: Path, move_list: MoveList, translation: Translation, machine: Machine) -> Path:
    """Write the controller file and its record `<program>.ebb.csv` into the folder."""
    controller = folder / f"{move_list.program.id}{CONTROLLER_SUFFIX}"
    controller.write_text(translation.text(), encoding="ascii")
    with (folder / f"{controller.name}.csv").open("w", encoding="ascii", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(RECORD_COLUMNS)
        writer.writerows(record_rows(move_list, translation, machine))
    return controller


def controller_digests(folder: Path) -> dict[str, str]:
    """The SHA-256 of the controller file of each program that has a move list in the folder.

    A move list without its controller file is an error, and so is a controller file without its move
    list, such as the file of a program that a later write of the move lists excluded."""
    digests: dict[str, str] = {}
    for p in PROGRAMS:
        move_list, controller = folder / f"{p.id}.json", folder / f"{p.id}{CONTROLLER_SUFFIX}"
        if move_list.is_file() and controller.is_file():
            digests[p.id] = hashlib.sha256(controller.read_bytes()).hexdigest()
        elif move_list.is_file() or controller.is_file():
            found, missing = (move_list, controller) if move_list.is_file() else (controller, move_list)
            raise ControllerError(f"{folder} holds {found.name} without {missing.name}.")
    return digests


def translated_digests(folder: Path, machine: Machine = NEXTDRAW_8511) -> dict[str, str]:
    """`controller_digests`, where each controller file must also be the translation of its move list,
    so that no digest belongs to a stale controller file."""
    digests = controller_digests(folder)
    for pid in digests:
        controller, source = folder / f"{pid}{CONTROLLER_SUFFIX}", folder / f"{pid}.json"
        if controller.read_text(encoding="ascii") != translate(read_move_list(source), machine).text():
            raise ControllerError(
                f"{controller} is not the translation of {source.name}."
                " Run python/studies/bench_controller.py again."
            )
    return digests
