"""A synthetic bench study on disk, for the end-to-end tests of the `scriptkin bench` analysis commands.

`write_study` writes a repository root with the bench folder of protocol section 15 and a study store.
The default study is the complete design of section 6.3: both devices, six mountings each in the block
order W P P W P W W P W P P W, four main programs with 4 runs each, one fault run per block with the
fault programs in turn over the mountings of a device, the reruns that the planned failures need, the
two runs of `EXTRA_RUNS`, and one track for every main run. A lock file and a qualification session
for one mounting complete it. A pilot study (section 6.5) takes 2 mountings and 2 runs per program. An
interim study takes 3 mountings with the rows of the later blocks planned.

The truth. The motion system executes every run at a speed factor of 1, two runs excepted, which the
camera sees.
Device P reports every position scaled by `scale_p`, so its path length, mean speed, and loop height
differ from the camera by ln(scale_p), and its band power by 2 ln(scale_p). Device W reports the
executed movement. Both devices add position noise of 0.005 mm. `DEFECTS` names the runs that fail and
why, the reruns that follow them, and one run whose device record is turned by 2 degrees against the
camera, which section 8.2 reports as a deviation.
"""

from __future__ import annotations

import csv
import json
import shutil
import zlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import numpy as np
import test_bench_camera as camera_tests
import test_bench_inputs as inputs_tests

from scriptkin import battery, bench
from scriptkin.bench.inputs import ORDER_COLUMNS, PLANNED_RUNS, RUN_COLUMNS, Build
from scriptkin.bench.moves import MoveList, commanded_trial
from scriptkin.bench.study import BLOCK_ORDER
from scriptkin.bench.track import write_track
from scriptkin.contract import (
    PRO_PEN_3_CHANNELS,
    TABLET,
    TABLET_SCALE,
    JournalBuilder,
    manifest_bytes,
)
from scriptkin.recording import BenchRole

# Three distinct covariate points of section 11.5, so that the covariate model fits.
PROGRAMS = ("loops-l-v20", "loops-l-v40", "spiral-s-v40", "line-osc8")
LINE_PROGRAMS = tuple(p for p in PROGRAMS if p.startswith("line"))
# The 12 blocks of section 6.3. The mounting index of a device counts its blocks in order.
BLOCKS: tuple[tuple[BenchRole, str], ...] = tuple(
    (device, f"M{1 + BLOCK_ORDER[:k].count(device)}")
    for k, device in enumerate(BLOCK_ORDER)
    if device in ("P", "W")
)
# Each mounting shifts the device by 10 mm in x from the previous one (section 6.1).
ORIGINS_MM = {
    (device, mounting): (
        20.0 + 10.0 * int(mounting[1]) + (10.0 if device == "P" else 0.0),
        100.0 + 7.0 * (int(mounting[1]) % 2),
    )
    for device, mounting in BLOCKS
}
# Both devices sample at 120 Hz, half the real rate, so that the test stays short.
RATE_HZ = 120.0
RUNS_PER_PROGRAM = 4
BUILD = Build("1", "fixture")
COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
# The digest that the test tracks name as their calibration (`camera_track`).
CALIBRATION_SHA256 = "c" * 64
QUALIFICATION_SESSION = "3F2A9C1E-0000-4000-8000-0000000000Q1"
Defect = Literal["disturbance", "deviceProblem", "backgrounded", "bump", "invalid_camera", "rotated"]
DEFECTS: dict[str, Defect] = {
    "P-M1-loops-l-v40-r2": "disturbance",
    "W-M2-spiral-s-v40-r3": "backgrounded",
    "W-M2-spiral-s-v40-r6": "deviceProblem",
    "P-M2-line-osc8-r1": "bump",
    "W-M1-loops-l-v20-r5": "invalid_camera",
    "W-M3-loops-l-v40-r4": "deviceProblem",
    "P-M4-spiral-s-v40-r2": "rotated",
}
FAILING: tuple[Defect, ...] = ("disturbance", "deviceProblem", "backgrounded", "bump")
MARKS: dict[str, str] = {"disturbance": "disturbance", "deviceProblem": "deviceProblem"}
# A rerun that no failed run needs, which the analysis must not use (section 6.4).
UNUSED_RERUNS: dict[str, tuple[str, int]] = {"P-M3-loops-l-v20-r6": ("loops-l-v20", 6)}
# The runs beyond the 4 per program of this study: the unused rerun, and a fifth planned run that
# carries the invalid camera record, so that its mounting keeps 4 valid camera runs as the 5 runs of
# the protocol would (section 6.4).
EXTRA_RUNS: dict[str, tuple[str, int]] = {**UNUSED_RERUNS, "W-M1-loops-l-v20-r5": ("loops-l-v20", 5)}
ROTATION_DEVIATION_DEG = 2.0
NOISE_MM = 0.005
JITTER_S = 0.0003
# The executed speed factor of every run is 1, except these, within the 1 percent of section 7.6. A
# factor off 1 moves the time-paired start of the path fit along the path, and the fit then needs many
# iterations, so the test keeps them few.
SPEED_FACTORS = {"W-M1-loops-l-v40-r1": 1.004, "P-M3-spiral-s-v40-r2": 0.996}
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
    """The runs of a block: the planned runs in a shuffled order, the extra planned run of `EXTRA_RUNS`,
    then the reruns of its failed runs and the extra rerun (section 6.4). The block holds one fault run;
    the fault programs take turns over the mountings of the device. The planned runs, which come first,
    are the order file of the block."""
    runs = [(p, k) for p in PROGRAMS for k in range(1, runs_per_program + 1)]
    runs.append((bench.FAULTS[(int(mounting[1]) - 1) % len(bench.FAULTS)].id, 1))
    ordered = [runs[i] for i in rng.permutation(len(runs))]
    extras = [(p, k) for rid, (p, k) in EXTRA_RUNS.items() if rid.startswith(f"{device}-{mounting}-")]
    ordered.extend((p, k) for p, k in extras if k <= PLANNED_RUNS)
    for program, index in list(ordered):
        if DEFECTS.get(run_id(device, mounting, program, index)) not in FAILING:
            continue
        rerun = 6
        ordered.append((program, rerun))
        while DEFECTS.get(run_id(device, mounting, program, rerun)) in FAILING and rerun < 7:
            rerun += 1
            ordered.append((program, rerun))
    ordered.extend((p, k) for p, k in extras if k > PLANNED_RUNS)
    return ordered


def write_order(root: Path, block: int, runs: list[tuple[str, int]]) -> None:
    """The order file of a block (section 6.3): its planned runs at the positions of their rows."""
    folder = root / "bench" / "order"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"block-{block:02d}.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(ORDER_COLUMNS)
        for position, (program, index) in enumerate(runs, start=1):
            if index <= PLANNED_RUNS:
                writer.writerow((position, program, index))


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
        if defect == "rotated":
            xy = camera_tests.rotated(xy, ROTATION_DEVIATION_DEG)
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
        rotation_deg=0.05 * k,
        shift_mm=(0.5 * k, -0.3 * k),
        bump=(3.0, 0.3) if defect == "bump" else None,
        missing_share=0.03 if defect == "invalid_camera" else 0.0,
        seed=zlib.crc32(rid.encode()) % 1000,
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
            "calibrationSHA256": CALIBRATION_SHA256,
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
    mountings: int = 6,
    runs_per_program: int = RUNS_PER_PROGRAM,
    planned_after: bool = False,
) -> list[dict[str, object]]:
    """Write the study under the root and return the rows of `bench/runs.csv`. With `planned_after`,
    the main runs of the blocks after the given mountings are planned rows, as `bench/runs.csv` holds
    them before those blocks run (section 15)."""
    rng = np.random.default_rng(seed)
    truth = Truth(scale_p)
    inputs_tests.lock_root(root)
    shutil.rmtree(root / "bench" / "programs")
    shutil.copytree(programs_folder, root / "bench" / "programs")
    shutil.rmtree(root / "bench" / "order")
    blocks = BLOCKS[: 2 * mountings]
    write_registry(root, blocks)
    store = root / "store"
    rows: list[dict[str, object]] = []
    for device, mounting in blocks:
        b = _builder(device, mounting).opened()
        t0 = 1000.0
        runs = block_runs(device, mounting, rng, runs_per_program)
        write_order(root, block_number(device, mounting), runs)
        for position, (program, index) in enumerate(runs, start=1):
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
    if planned_after:
        for device, mounting in BLOCKS[2 * mountings :]:
            planned = [(p, k) for p in PROGRAMS for k in range(1, runs_per_program + 1)]
            write_order(root, block_number(device, mounting), planned)
            for position, (program, index) in enumerate(planned, start=1):
                rows.append(
                    {
                        "run_id": run_id(device, mounting, program, index),
                        "device": device,
                        "mounting": mounting,
                        "block": block_number(device, mounting),
                        "position": position,
                        "program": program,
                        "run_index": index,
                        "session_id": "",
                        "trial_index": "",
                        "clip": "",
                        "status": "planned",
                        "deviation_row": "",
                    }
                )
    _write_qualification(store, rng)
    write_runs_csv(root / "bench" / "runs.csv", rows)
    return rows
