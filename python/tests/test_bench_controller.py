from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
import test_bench_moves as moves_tests

from scriptkin import bench
from scriptkin.bench.controller import (
    NEXTDRAW_8511,
    ControllerError,
    Machine,
    controller_digests,
    translate,
    translated_digests,
)
from scriptkin.bench.moves import Move, MoveList

programs_folder = moves_tests.programs_folder
move_lists = moves_tests.move_lists

STUDY = Path(__file__).resolve().parents[1] / "studies" / "bench_controller.py"
# The facts of the EBB command set (protocol reference M4) and of the NextDraw kinematics, at 16 microsteps.
STEPS_PER_MM = 80
STEP_RATE = 25_000
SM_DELAY_MAX_MS = 100_000
END_POINT_BOUND_MM = 1 / 160
END_TIME_BOUND_MS = 0.5
LIFT_SPEED_MM_S = 10.0


MACHINE = NEXTDRAW_8511


@pytest.fixture(scope="module")
def controller_folder(tmp_path_factory: pytest.TempPathFactory, programs_folder: Path) -> Path:
    """The move lists with the controller files and records that the script writes beside them."""
    folder = tmp_path_factory.mktemp("controller")
    shutil.copytree(programs_folder, folder, dirs_exist_ok=True)
    subprocess.run([sys.executable, "-I", str(STUDY), str(folder)], check=True, capture_output=True)
    return folder


def to_mm(motor: tuple[int, int]) -> tuple[float, float]:
    """The position of the motor positions: x = (m1 + m2) / 160 and y = (m1 - m2) / 160."""
    return (motor[0] + motor[1]) / (2 * STEPS_PER_MM), (motor[0] - motor[1]) / (2 * STEPS_PER_MM)


def height_mm(pulse: float) -> float:
    return (pulse - MACHINE.servo_surface) / MACHINE.servo_per_mm


@dataclass(frozen=True)
class Event:
    """One command as the EBB executes it: the queue holds the next command until `end_ms`."""

    kind: str
    start_ms: int
    end_ms: int
    motor: tuple[int, int]
    """The motor positions at the end."""
    steps: tuple[int, int] = (0, 0)
    pulse_from: float = 0.0
    """The servo pulse at the start."""
    pulse_to: float = 0.0
    """The servo pulse at the end of an `SM`, or the target of an `S2`."""
    slew_ms: int = 0
    """The time that the servo of an `S2` takes from `pulse_from` to `pulse_to`."""

    def speed_mm_s(self) -> float:
        return abs(height_mm(self.pulse_to) - height_mm(self.pulse_from)) / self.slew_ms * 1000


def replay(text: str, start: tuple[int, int]) -> list[Event]:
    """Execute a controller file from the start point with the stylus at the lift height.

    `SM` adds its steps over its duration and refuses a rate above 25,000 steps/s. `S2` slews the servo
    pulse by its rate once per servo cycle and holds the queue for its delay."""
    events: list[Event] = []
    time, motor = 0, start
    pulse_from = pulse_to = float(MACHINE.pulse(MACHINE.lift_mm))
    slew_start, slew_ms = 0, 0

    def pulse_at(t: int) -> float:
        if slew_ms == 0 or t >= slew_start + slew_ms:
            return pulse_to
        return pulse_from + (pulse_to - pulse_from) * (t - slew_start) / slew_ms

    assert text.endswith("\n") and "\r" not in text
    for line in text.splitlines():
        kind, *fields = line.split(",")
        values = [int(f) for f in fields]
        if kind == "SM":
            ms, s1, s2, clear = values
            assert ms >= 1 and clear == 3, line
            assert abs(s1) * 1000 <= STEP_RATE * ms and abs(s2) * 1000 <= STEP_RATE * ms, line
            assert (s1, s2) != (0, 0) or ms <= SM_DELAY_MAX_MS, line
            motor = (motor[0] + s1, motor[1] + s2)
            events.append(Event("SM", time, time + ms, motor, (s1, s2), pulse_at(time), pulse_at(time + ms)))
            time += ms
        elif kind == "S2":
            target, pin, rate, delay = values
            assert pin == MACHINE.servo_pin and rate >= 1 and 1 <= target <= 65_535, line
            current = pulse_at(time)
            pulse_from, pulse_to, slew_start = current, float(target), time
            slew_ms = math.ceil(abs(target - current) / rate) * MACHINE.servo_cycle_ms
            events.append(Event("S2", time, time + delay, motor, (0, 0), current, float(target), slew_ms))
            time += delay
        else:
            pytest.fail(f"not an SM or S2 command: {line}")
    return events


def read_record(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="ascii", newline="") as stream:
        return list(csv.DictReader(stream))


def _translated(folder: Path, pid: str) -> tuple[list[Event], list[dict[str, str]]]:
    record = read_record(folder / f"{pid}.ebb.csv")
    start = (int(record[0]["motor1"]), int(record[0]["motor2"]))
    return replay((folder / f"{pid}.ebb").read_text(encoding="ascii"), start), record


def knots(events: list[Event]) -> dict[int, tuple[int, int]]:
    """The motor positions at each time from the first move when the stylus is on the surface after a
    command: the start of the moves, and the end of each move and of each lowering."""
    t0 = events[1].end_ms
    on_surface = [e for e in events[2:-2] if e.kind == "SM" or height_mm(e.pulse_to) == 0]
    return {0: events[1].motor} | {e.end_ms - t0: e.motor for e in on_surface}


@pytest.mark.parametrize("pid", [p.id for p in bench.PROGRAMS])
def test_the_replayed_controller_file_keeps_the_end_points_durations_and_lifts(
    controller_folder: Path, move_lists: dict[str, MoveList], pid: str
) -> None:
    # Detects a translation that drifts, rounds the start point, drops, splits, or adds a move, shifts a move
    # in time, changes a move of whole milliseconds, moves in x or y during a lift, lifts to the wrong height,
    # moves the stylus up or down faster than 10 mm/s, moves on the surface while the stylus is in the air,
    # leaves out the waits of 0.5 s and 0.3 s, ends before the last lift arrives, or exceeds the step rate
    # or the travel.
    move_list = move_lists[pid]
    events, record = _translated(controller_folder, pid)
    path = move_list.path()
    start_motor = (int(record[0]["motor1"]), int(record[0]["motor2"]))
    assert math.dist(to_mm(start_motor), path.xy[0]) <= END_POINT_BOUND_MM

    lower, before, *body, after, lift = events
    assert lower.kind == "S2" and height_mm(lower.pulse_from) >= 10 and height_mm(lower.pulse_to) == 0
    assert lower.slew_ms <= lower.end_ms - lower.start_ms and lower.speed_mm_s() <= LIFT_SPEED_MM_S
    assert (before.kind, before.end_ms - before.start_ms, before.steps) == ("SM", 500, (0, 0))
    assert (after.kind, after.end_ms - after.start_ms, after.steps) == ("SM", 300, (0, 0))
    assert lift.kind == "S2" and height_mm(lift.pulse_to) >= 10 and lift.speed_mm_s() <= LIFT_SPEED_MM_S
    assert lift.slew_ms <= lift.end_ms - lift.start_ms

    t0 = before.end_ms
    found = knots(events)
    assert before.motor == start_motor and list(found) == sorted(found)
    used: list[int] = []
    for i, (t, xy) in enumerate(zip(path.t, path.xy, strict=True)):
        lo = math.ceil(1000 * float(t) - END_TIME_BOUND_MS - 1e-9)
        hi = math.floor(1000 * float(t) + END_TIME_BOUND_MS + 1e-9)
        near = [k for k in range(lo, hi + 1) if k in found]
        assert len(near) == 1, (i, float(t))
        assert math.dist(to_mm(found[near[0]]), xy) <= END_POINT_BOUND_MM + 1e-12, i
        used.append(near[0])
    assert used == sorted(used) and set(used) == set(found)

    for i, move in enumerate(move_list.moves, start=1):
        whole = round(1000 * move.duration_s)
        if abs(1000 * move.duration_s - whole) < 1e-6:
            assert used[i] - used[i - 1] == whole, i
        inside = [e for e in body if used[i - 1] < e.end_ms - t0 <= used[i]]
        if move.lift_mm > 0:
            up, down = inside
            assert (up.kind, down.kind) == ("S2", "S2") and up.motor == down.motor
            assert 0 <= height_mm(up.pulse_to) - move.lift_mm <= 1 / MACHINE.servo_per_mm
            assert up.slew_ms <= up.end_ms - up.start_ms and up.speed_mm_s() <= LIFT_SPEED_MM_S
            assert height_mm(down.pulse_to) == 0 and down.slew_ms <= down.end_ms - down.start_ms
            assert down.speed_mm_s() <= LIFT_SPEED_MM_S
        else:
            assert [e.kind for e in inside] == (["SM"] if used[i] > used[i - 1] else [])
    for e in body:
        if e.kind == "SM" and e.steps != (0, 0):
            assert height_mm(e.pulse_from) == 0 and height_mm(e.pulse_to) == 0
    s2_moves = [e for e in body if e.kind == "S2"]
    assert len(s2_moves) == 2 * sum(m.lift_mm > 0 for m in move_list.moves)
    for e in events:
        x, y = to_mm(e.motor)
        assert 0 <= MACHINE.origin_mm[0] + x <= MACHINE.travel_mm[0]
        assert 0 <= MACHINE.origin_mm[1] + y <= MACHINE.travel_mm[1]


@pytest.mark.parametrize("pid", ["loops-l-v80", "fault-lift", "line-osc11"])
def test_the_record_holds_the_quantised_points_of_the_controller_file(
    controller_folder: Path, move_lists: dict[str, MoveList], pid: str
) -> None:
    # Detects a record whose rows miss a point, whose time or motor positions differ from the controller
    # file, whose quantised position does not follow from the motor positions, whose move list values
    # are not the registered ones, or whose error exceeds the bound.
    events, record = _translated(controller_folder, pid)
    path = move_lists[pid].path()
    found = knots(events)
    assert len(record) == len(path.t)
    for i, (row, t, xy) in enumerate(zip(record, path.t, path.xy, strict=True)):
        motor = (int(row["motor1"]), int(row["motor2"]))
        assert int(row["point"]) == i and found[int(row["end_ms"])] == motor
        assert float(row["end_s"]) == pytest.approx(float(t), abs=1e-6)
        assert (float(row["x_mm"]), float(row["y_mm"])) == (float(xy[0]), float(xy[1]))
        assert (float(row["quantised_x_mm"]), float(row["quantised_y_mm"])) == to_mm(motor)
        assert float(row["error_mm"]) == pytest.approx(math.dist(to_mm(motor), xy), abs=1e-6)
        assert float(row["error_mm"]) <= END_POINT_BOUND_MM


def _line(*moves: Move, start: tuple[float, float] = (4.0, 8.0)) -> MoveList:
    return MoveList(bench.program("line-osc5"), start, moves, 0.01, 0.2)


@pytest.mark.parametrize(
    ("move_list", "machine", "message"),
    [
        (_line(Move(7.2, 8.0, 0.01)), MACHINE, r"move 1 needs 25600 steps/s on motor 1"),
        (_line(Move(128.0, 8.0, 6.0)), replace(MACHINE, origin_mm=(200.0, 20.0)), "outside the travel"),
        (_line(Move(4.0, 8.0, 0.1), start=(-30.0, 8.0)), MACHINE, "the start point is at"),
        (_line(Move(4.5, 8.0, 0.7, 2.0)), MACHINE, "move 1 lifts the stylus and moves"),
        (_line(Move(4.0, 8.0, 0.4, 2.0)), MACHINE, "move 1 lasts 400 ms"),
        (_line(Move(4.1, 8.0, 0.0101), Move(4.2, 8.0, 0.0003)), MACHINE, "move 2 lasts 0 ms after rounding"),
        (_line(Move(4.0, 8.0, 100.001)), MACHINE, "SM caps a wait"),
        (_line(Move(5.0, 8.0, 0.1)), replace(MACHINE, lift_mm=5.0), "Section 4.1 needs 10.0 mm"),
    ],
)
def test_the_translation_refuses_a_move_that_the_machine_cannot_execute_as_registered(
    move_list: MoveList, machine: Machine, message: str
) -> None:
    # Detects a translation that clips a fast move, a point outside the travel, a lift that moves or that
    # is too short for 10 mm/s, a move that rounds to 0 ms, a wait above the cap of SM, or a lift below 10 mm.
    with pytest.raises(ControllerError, match=message):
        translate(move_list, machine)


def test_the_translation_accepts_a_move_at_the_step_rate_limit() -> None:
    # Detects a step-rate check that refuses 250 steps in 10 ms, which is the 25,000 steps/s of the EBB.
    assert translate(_line(Move(7.125, 8.0, 0.01))).commands[2] == "SM,10,250,250,3"


def test_a_refused_program_stops_the_script_before_it_writes_a_file(
    tmp_path: Path, programs_folder: Path
) -> None:
    # Detects a script that exits with 0 after a refusal, or that writes the controller files of the other
    # programs, so that the folder holds a partial translation.
    shutil.copytree(programs_folder, tmp_path, dirs_exist_ok=True)
    fast = tmp_path / "line-osc5.json"
    raw = json.loads(fast.read_text(encoding="utf-8"))
    raw["moves"][100]["x_mm"] += 5.0
    fast.write_text(json.dumps(raw), encoding="utf-8")
    done = subprocess.run([sys.executable, "-I", str(STUDY), str(tmp_path)], capture_output=True, text=True)
    assert done.returncode == 1 and "line-osc5: move 101 needs" in done.stderr
    assert not list(tmp_path.glob("*.ebb*"))


@pytest.fixture
def registered_folder(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clears the cached digests and programs file around a test that moves `bench.PROGRAM_FOLDER`."""

    def clear() -> None:
        bench.registered_digests.cache_clear()
        bench.protocol_sha256.cache_clear()
        bench.programs_json.cache_clear()
        bench.programs_sha256.cache_clear()

    clear()
    yield
    monkeypatch.undo()
    clear()


def test_the_programs_file_takes_each_trajectory_digest_from_its_controller_file(
    controller_folder: Path, monkeypatch: pytest.MonkeyPatch, registered_folder: None
) -> None:
    # Detects a programs file that keeps a placeholder digest beside a controller file, or that hashes
    # another file than the controller file of the program.
    monkeypatch.setattr(bench, "PROGRAM_FOLDER", controller_folder)
    table = {p["id"]: p["trajectorySHA256"] for p in json.loads(bench.programs_json())["programs"]}
    files = {p.id: controller_folder / f"{p.id}.ebb" for p in bench.PROGRAMS}
    assert table == {pid: hashlib.sha256(path.read_bytes()).hexdigest() for pid, path in files.items()}


def test_the_protocol_digest_is_the_placeholder_until_a_controller_file_exists(
    controller_folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered_folder: None
) -> None:
    # Detects a programs file that keeps the placeholder of 64 zeros as its protocol digest after step 4 of
    # the lock, so that every bench journal names no protocol text, or a digest that changes before that step.
    protocol = tmp_path / "BENCH_PROTOCOL.md"
    protocol.write_text("the protocol text at the lock\n", encoding="utf-8")
    monkeypatch.setattr(bench, "PROTOCOL_FILE", protocol)
    # A folder without a controller file, whatever the repository holds after the lock.
    monkeypatch.setattr(bench, "PROGRAM_FOLDER", tmp_path / "programs")
    assert json.loads(bench.programs_json())["protocol"] == {
        "version": bench.PROTOCOL_VERSION,
        "sha256": bench.PLACEHOLDER_SHA256,
    }
    bench.registered_digests.cache_clear()
    bench.protocol_sha256.cache_clear()
    bench.programs_json.cache_clear()
    monkeypatch.setattr(bench, "PROGRAM_FOLDER", controller_folder)
    assert json.loads(bench.programs_json())["protocol"] == {
        "version": bench.PROTOCOL_VERSION,
        "sha256": hashlib.sha256(protocol.read_bytes()).hexdigest(),
    }


def test_the_trajectory_digests_refuse_a_move_list_or_a_controller_file_alone(
    tmp_path: Path, controller_folder: Path
) -> None:
    # Detects digests that silently keep the placeholder for a move list without its controller file, or
    # that accept a controller file whose move list is gone, such as a program excluded after translation.
    assert controller_digests(tmp_path) == {}
    excluded = tmp_path / "excluded"
    shutil.copytree(controller_folder, excluded)
    for name in ("loops-l-v80.json", "loops-l-v80.ebb", "loops-l-v80.ebb.csv"):
        (excluded / name).unlink()
    assert set(controller_digests(excluded)) == {p.id for p in bench.PROGRAMS} - {"loops-l-v80"}
    (excluded / "spiral-s-v20.ebb").unlink()
    with pytest.raises(ControllerError, match=r"spiral-s-v20\.json without spiral-s-v20\.ebb"):
        controller_digests(excluded)
    orphan = tmp_path / "orphan"
    shutil.copytree(controller_folder, orphan)
    (orphan / "line-osc8.json").unlink()
    with pytest.raises(ControllerError, match=r"line-osc8\.ebb without line-osc8\.json"):
        controller_digests(orphan)


def test_the_programs_file_refuses_a_controller_file_that_is_not_the_translation_of_its_move_list(
    tmp_path: Path, controller_folder: Path, monkeypatch: pytest.MonkeyPatch, registered_folder: None
) -> None:
    # Detects a programs file that takes the digest of a stale controller file, written before its move
    # list changed, so that the motion system would execute another movement than the registered one,
    # or of a controller file with other line ends, whose digest differs from the translation.
    shutil.copytree(controller_folder, tmp_path, dirs_exist_ok=True)
    monkeypatch.setattr(bench, "PROGRAM_FOLDER", tmp_path)
    assert translated_digests(tmp_path) == controller_digests(tmp_path)
    changed = tmp_path / "line-osc5.json"
    raw = json.loads(changed.read_text(encoding="utf-8"))
    raw["moves"][100]["x_mm"] += 0.05
    changed.write_text(json.dumps(raw), encoding="utf-8")
    assert "line-osc5" in controller_digests(tmp_path)
    with pytest.raises(ControllerError, match=r"line-osc5\.ebb is not the translation of line-osc5\.json"):
        bench.programs_json()
    shutil.copy(controller_folder / "line-osc5.json", changed)
    crlf = tmp_path / "spiral-l-v40.ebb"
    crlf.write_bytes(crlf.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(ControllerError, match=r"spiral-l-v40\.ebb is not the translation"):
        bench.programs_json()
