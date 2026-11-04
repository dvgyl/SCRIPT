"""A synthetic bench study on disk, for the end-to-end tests of the `scriptkin bench` analysis commands.

`write_study` writes a repository root with the bench folder of protocol section 15 and a study store.
The default study is the one below. A pilot study (section 6.5) takes 2 mountings and 2 runs per program.
The study has both devices, three mountings each in the block order of section 6.3, five main programs
with 5 runs each, one run of each fault program per block, the reruns that the planned failures need,
and one track for every main run. A lock file and a qualification session for one mounting complete it.

The truth. The motion system executes every run at a speed factor of 1, two runs excepted, which the
camera sees.
Device P reports every position scaled by `scale_p`, so its path length, mean speed, and loop height
differ from the camera by ln(scale_p), and its band power by 2 ln(scale_p). Device W reports the
executed movement. Both devices add position noise of 0.005 mm. `DEFECTS` names the runs that fail and
why, and the reruns that follow them.
"""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import numpy as np
import test_bench_camera as camera_tests
import test_bench_inputs as inputs_tests

from scriptkin import battery, bench
from scriptkin.bench.inputs import RUN_COLUMNS, Build
from scriptkin.bench.moves import MoveList, commanded_trial
from scriptkin.bench.track import write_track
from scriptkin.contract import (
    PRO_PEN_3_CHANNELS,
    TABLET,
    TABLET_SCALE,
    JournalBuilder,
    manifest_bytes,
)
from scriptkin.recording import BenchRole

PROGRAMS = ("loops-l-v20", "loops-l-v40", "loops-s-v40", "spiral-s-v40", "line-osc8")
LINE_PROGRAMS = tuple(p for p in PROGRAMS if p.startswith("line"))
BLOCKS: tuple[tuple[BenchRole, str], ...] = (
    ("W", "M1"),
    ("P", "M1"),
    ("P", "M2"),
    ("W", "M2"),
    ("P", "M3"),
    ("W", "M3"),
)
ORIGINS_MM = {
    ("P", "M1"): (30.0, 120.0),
    ("P", "M2"): (41.0, 131.0),
    ("P", "M3"): (52.0, 118.0),
    ("W", "M1"): (20.0, 100.0),
    ("W", "M2"): (31.0, 111.0),
    ("W", "M3"): (42.0, 98.0),
}
# Both devices sample at 120 Hz, half the real rate, so that the test stays short.
RATE_HZ = 120.0
RUNS_PER_PROGRAM = 5
BUILD = Build("1", "fixture")
COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
QUALIFICATION_SESSION = "3F2A9C1E-0000-4000-8000-0000000000Q1"
Defect = Literal["disturbance", "deviceProblem", "backgrounded", "bump", "invalid_camera"]
DEFECTS: dict[str, Defect] = {
    "P-M1-loops-s-v40-r2": "disturbance",
    "W-M2-spiral-s-v40-r3": "backgrounded",
    "W-M2-spiral-s-v40-r6": "deviceProblem",
    "P-M2-line-osc8-r1": "bump",
    "W-M1-loops-l-v20-r4": "invalid_camera",
    "W-M3-loops-s-v40-r5": "deviceProblem",
}
FAILING: tuple[Defect, ...] = ("disturbance", "deviceProblem", "backgrounded", "bump")
MARKS: dict[str, str] = {"disturbance": "disturbance", "deviceProblem": "deviceProblem"}
NOISE_MM = 0.005
JITTER_S = 0.0003
# The executed speed factor of every run is 1, except these, within the 1 percent of section 7.6. A
# factor off 1 moves the time-paired start of the path fit along the path, and the fit then needs many
# iterations, so the test keeps them few.
SPEED_FACTORS = {"W-M1-loops-s-v40-r1": 1.004, "P-M3-spiral-s-v40-r2": 0.996}
DWELL_BEFORE_S = 0.5
DWELL_AFTER_S = 0.3


@dataclass(frozen=True)
class Truth:
    scale_p: float

    def bias(self, feature: str) -> float:
        """The device P minus device W value on the comparison scale that the study simulates."""
        if feature in ("bench.path_length_mm", "bench.speed_mean_mm_s", "bench.loop_height_mm"):
            return float(np.log(self.scale_p))
        if feature == "bench.band_power_mm2_s2":
            return float(2 * np.log(self.scale_p))
        return 0.0


def session_id(device: BenchRole, mounting: str) -> str:
    return f"3F2A9C1E-0000-4000-8000-0000000000{device}{mounting[1]}"


def block_number(device: BenchRole, mounting: str) -> int:
    return BLOCKS.index((device, mounting)) + 1


def run_id(device: BenchRole, mounting: str, program: str, run_index: int) -> str:
    return f"{device}-{mounting}-{program}-r{run_index}"


def block_runs(
    device: BenchRole, mounting: str, rng: np.random.Generator, runs_per_program: int = RUNS_PER_PROGRAM
) -> list[tuple[str, int]]:
    """The runs of a block in a shuffled order, with the reruns of its failed runs at the end (6.4)."""
    runs = [(p, k) for p in PROGRAMS for k in range(1, runs_per_program + 1)]
    runs += [(f.id, 1) for f in bench.FAULTS]
    ordered = [runs[i] for i in rng.permutation(len(runs))]
    for program, index in list(ordered):
        if DEFECTS.get(run_id(device, mounting, program, index)) not in FAILING:
            continue
        rerun = 6
        ordered.append((program, rerun))
        while DEFECTS.get(run_id(device, mounting, program, rerun)) in FAILING and rerun < 7:
            rerun += 1
            ordered.append((program, rerun))
    return ordered


def executed_strokes(
    move_list: MoveList, speed_factor: float, rng: np.random.Generator
) -> list[tuple[np.ndarray, np.ndarray]]:
    """The executed movement sampled at the device rate: (times, xy with y up in mm) per contact span,
    with the still dwells of section 5.2 before and after the motion."""
    duration = move_list.duration_s / speed_factor
    t = np.arange(int(duration * RATE_HZ) + 1) / RATE_HZ
    t = np.clip(np.sort(t + rng.uniform(-JITTER_S, JITTER_S, len(t))), 0.0, duration)
    t[0] = 0.0
    trial = commanded_trial(move_list, t * speed_factor)
    strokes = [(s.t / speed_factor, s.xy.copy()) for s in trial.strokes]
    before = np.arange(int(DWELL_BEFORE_S * RATE_HZ)) / RATE_HZ - DWELL_BEFORE_S
    after = np.arange(1, int(DWELL_AFTER_S * RATE_HZ) + 1) / RATE_HZ
    first_t, first_xy = strokes[0]
    strokes[0] = (
        np.concatenate((before, first_t)),
        np.concatenate((np.repeat(first_xy[:1], len(before), axis=0), first_xy)),
    )
    last_t, last_xy = strokes[-1]
    strokes[-1] = (
        np.concatenate((last_t, last_t[-1] + after)),
        np.concatenate((last_xy, np.repeat(last_xy[-1:], len(after), axis=0))),
    )
    return [(t_s, xy + rng.normal(0.0, NOISE_MM, xy.shape)) for t_s, xy in strokes]


def _builder(device: BenchRole, mounting: str) -> JournalBuilder:
    header = {
        "role": device,
        "mounting": mounting,
        "motion": {"id": "nextdraw-8511", "version": "test"},
        "protocol": {"version": bench.PROTOCOL_VERSION, "sha256": bench.PROTOCOL_SHA256},
    }
    if device == "P":
        return JournalBuilder(
            session_id(device, mounting), None, purpose="bench", asset="IPAD-02", bench=header
        )
    return JournalBuilder(
        session_id(device, mounting),
        None,
        channels=dict(PRO_PEN_3_CHANNELS),
        pencil="none",
        input="tablet",
        scale=dict(TABLET_SCALE),
        purpose="bench",
        device_model="Mac16,1",
        device_os="macOS 26.1",
        asset="MAC-01",
        bench=header,
        tablet=dict(TABLET),
        max_force=1.0,
        view=(52600.0, 29600.0),
        stimulus_origin=(0.0, 0.0),
    )


def _to_journal(b: JournalBuilder, xy_up_mm: np.ndarray) -> np.ndarray:
    """Millimetres with y up into the units of the journal: points or tablet lines, y down."""
    mm_per_point = float(b.scale["mmPerPoint"])
    ox, oy = b.stimulus_origin
    return np.column_stack((ox + xy_up_mm[:, 0] / mm_per_point, oy - xy_up_mm[:, 1] / mm_per_point))


def _write_run(
    b: JournalBuilder,
    device: BenchRole,
    mounting: str,
    program: str,
    index: int,
    position: int,
    t0: float,
    move_list: MoveList,
    rng: np.random.Generator,
    truth: Truth,
) -> tuple[int, float, Defect | None]:
    """One run into the journal. Returns the trial index, its speed factor, and its defect."""
    rid = run_id(device, mounting, program, index)
    defect = DEFECTS.get(rid)
    design = bench.program(program)
    facts = {
        "program": program,
        "run": index,
        "trajectorySHA256": bench.trajectory_sha256(program),
        "clip": rid,
    }
    trial = b.trial(design.task, 1, t0, position=position, bench=facts)
    b.began(t0 + 0.5)
    speed_factor = SPEED_FACTORS.get(rid, 1.0)
    scale = truth.scale_p if device == "P" else 1.0
    start = t0 + 1.0 + DWELL_BEFORE_S
    last = start
    for t_s, xy in executed_strokes(move_list, speed_factor, rng):
        points = _to_journal(b, xy * scale)
        contact = b.contact(start + float(t_s[0]))
        for i in range(len(t_s)):
            phase = "began" if i == 0 else ("ended" if i == len(t_s) - 1 else "moved")
            b.sample(contact, start + float(t_s[i]), float(points[i, 0]), float(points[i, 1]), phase)
        last = start + float(t_s[-1])
        b.lift(last + 0.002, contact)
    if defect in MARKS:
        b.mark(last + 0.5, trial, MARKS[defect])
    b.end(last + 1.0, "backgrounded" if defect == "backgrounded" else "operatorDone")
    return trial, speed_factor, defect


def _write_track(
    store: Path,
    device: BenchRole,
    mounting: str,
    rid: str,
    move_list: MoveList,
    speed_factor: float,
    defect: Defect | None,
) -> None:
    folder = store / "camera" / f"{device}-{mounting}"
    folder.mkdir(parents=True, exist_ok=True)
    k = block_number(device, mounting)
    track = camera_tests.camera_track(
        move_list,
        speed_factor=speed_factor,
        noise_mm=NOISE_MM,
        rotation_deg=0.1 * k,
        shift_mm=(0.5 * k, -0.3 * k),
        bump=(3.0, 0.3) if defect == "bump" else None,
        missing_share=0.03 if defect == "invalid_camera" else 0.0,
        seed=hash(rid) % 1000,
    )
    write_track(replace(track, facts=replace(track.facts, run_id=rid)), folder)


def _export(folder: Path, b: JournalBuilder) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    data = b.journal_bytes()
    (folder / "journal.jsonl").write_bytes(data)
    (folder / "manifest.json").write_bytes(manifest_bytes(b.session_id, data))


def _write_qualification(store: Path, rng: np.random.Generator) -> None:
    """A qualification session of device P with a 10 s still hold, for the noise floor of section 8.1."""
    b = JournalBuilder(QUALIFICATION_SESSION, "QUAL-01", purpose="qualification", asset="IPAD-02").opened()
    for position, task in enumerate(battery.QUALIFICATION_TASKS, start=1):
        t0 = 1000.0 + 40.0 * position
        b.trial(task, 1, t0, position=position)
        b.began(t0 + 0.5)
        n = int(10 * RATE_HZ) if task == "qual-hold" else 240
        t = t0 + 1.0 + np.arange(n) / RATE_HZ
        xy = (
            np.zeros((n, 2))
            if task == "qual-hold"
            else np.column_stack((np.linspace(0, 100, n), np.zeros(n)))
        )
        points = _to_journal(b, xy + rng.normal(0.0, NOISE_MM, xy.shape))
        contact = b.contact(float(t[0]))
        for i in range(n):
            phase = "began" if i == 0 else ("ended" if i == n - 1 else "moved")
            b.sample(contact, float(t[i]), float(points[i, 0]), float(points[i, 1]), phase)
        b.lift(float(t[-1]) + 0.002, contact)
        b.end(float(t[-1]) + 1.0, "participantDone")
    b.close(1300.0)
    _export(store / "sessions" / QUALIFICATION_SESSION, b)


def write_registry(root: Path, blocks: tuple[tuple[BenchRole, str], ...]) -> None:
    mountings = [
        {
            "device": device,
            "mounting": mounting,
            "block": block_number(device, mounting),
            "originMM": {"x": ORIGINS_MM[(device, mounting)][0], "y": ORIGINS_MM[(device, mounting)][1]},
            "level_deg": 0.1,
            **({"qualificationSession": QUALIFICATION_SESSION} if (device, mounting) == ("P", "M1") else {}),
        }
        for device, mounting in blocks
    ]
    registry = {
        "studyStore": "store",
        "devices": {"P": {"model": "iPad16,3"}, "W": {"model": "PTK670"}},
        "mountings": mountings,
        "constants": {
            "bandPowerPrograms": list(LINE_PROGRAMS),
            "excludedPrograms": [d.id for d in bench.MAIN if d.id not in PROGRAMS],
            "faultDwellGapsOnP": False,
        },
    }
    (root / "bench" / "registry.json").write_text(json.dumps(registry, indent=1) + "\n", encoding="utf-8")


def write_runs_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(RUN_COLUMNS)
        for row in rows:
            writer.writerow([row[c] for c in RUN_COLUMNS])


def write_study(
    root: Path,
    programs_folder: Path,
    move_lists: dict[str, MoveList],
    *,
    scale_p: float = 1.06,
    seed: int = 7,
    mountings: int = 3,
    runs_per_program: int = RUNS_PER_PROGRAM,
) -> list[dict[str, object]]:
    """Write the study under the root and return the rows of `bench/runs.csv`."""
    rng = np.random.default_rng(seed)
    truth = Truth(scale_p)
    inputs_tests.lock_root(root)
    shutil.rmtree(root / "bench" / "programs")
    shutil.copytree(programs_folder, root / "bench" / "programs")
    blocks = BLOCKS[: 2 * mountings]
    write_registry(root, blocks)
    store = root / "store"
    rows: list[dict[str, object]] = []
    for device, mounting in blocks:
        b = _builder(device, mounting).opened()
        t0 = 1000.0
        for position, (program, index) in enumerate(
            block_runs(device, mounting, rng, runs_per_program), start=1
        ):
            rid = run_id(device, mounting, program, index)
            move_list = move_lists[program]
            trial, speed_factor, defect = _write_run(
                b, device, mounting, program, index, position, t0, move_list, rng, truth
            )
            if bench.program(program).fault is None:
                _write_track(store, device, mounting, rid, move_list, speed_factor, defect)
            rows.append(
                {
                    "run_id": rid,
                    "device": device,
                    "mounting": mounting,
                    "block": block_number(device, mounting),
                    "position": position,
                    "program": program,
                    "run_index": index,
                    "session_id": b.session_id,
                    "trial_index": trial,
                    "clip": rid,
                    "status": "recorded",
                    "deviation_row": "",
                }
            )
            t0 += move_list.duration_s + 5.0
        b.close(t0)
        _export(store / "sessions" / b.session_id, b)
    _write_qualification(store, rng)
    write_runs_csv(root / "bench" / "runs.csv", rows)
    return rows
