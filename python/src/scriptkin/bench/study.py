"""The bench study as the analysis reads it: the files of section 15 once, and every run evaluated once.

`read_study` reads the registry, the lock, the run rows, the move lists, and the block orders under a
root, and checks the lock (section 14). `incomplete` lists what keeps the study from the analysis of
section 13: a registry without the complete design of section 6.3, a block without a row, a planned
row, a row of a block or program outside the study, or a block whose rows do not hold every run of
its order file. `evaluate_study`
reads each journal once, checks its build against the lock (section 2.4), finds the trial of every
recorded row, reads the track when its file exists, and evaluates the run. A void row is not evaluated.

The main program set is `MAIN` without the excluded programs of the registry. A run of an excluded
program is an error, because the lock removed the program before the main runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from scriptkin.bench import MAIN, Design
from scriptkin.bench.features import WELCH_MIN_WINDOWS, Motion, commanded_motion
from scriptkin.bench.inputs import (
    PLANNED_RUNS,
    InputError,
    Journal,
    Lock,
    MountingRecord,
    Registry,
    RunLabel,
    RunRow,
    RunStatus,
    check_build,
    check_lock,
    parse_run_id,
    read_bench_journal,
    read_lock,
    read_orders,
    read_qualification_journal,
    read_registry,
    read_runs,
)
from scriptkin.bench.moves import MoveList, read_move_lists
from scriptkin.bench.runs import CleanMedians, Evaluated, evaluate, trial_of
from scriptkin.bench.track import Track, read_track
from scriptkin.qualification import HOLD_TASK
from scriptkin.quality import DEFAULT_QC, Accepted, QcPolicy, Refusal, RefusalCode, assess
from scriptkin.recording import BenchRole, PenState
from scriptkin.signal import DEFAULT_PREPROCESSING, Kinematics, Preprocessing, kinematics, welch_band_power

FAULT_BASE_ID = "loops-l-v20"
BLOCK_ORDER = "WPPWPWWPWPPW"
"""The device of blocks 1 to 12 (section 6.3)."""
EXTRA_BLOCK_ORDER = "PWWP"
"""The device of blocks 13 to 16, which section 6.6 can add."""


@dataclass(frozen=True)
class Study:
    root: Path
    registry: Registry
    lock: Lock | None
    """None before the lock, which only the pilot of section 6.5 allows."""
    rows: tuple[RunRow, ...]
    move_lists: dict[str, MoveList]
    orders: dict[int, tuple[RunLabel, ...]]
    """The run order of each block from `bench/order/` (section 6.3), by block number."""
    changed_code: tuple[str, ...] = ()
    """The locked code files that changed after the lock. Only a post hoc analysis allows them
    (section 13), and every output of the analysis then says so."""

    @property
    def post_hoc(self) -> bool:
        return bool(self.changed_code)

    @property
    def bench(self) -> Path:
        return self.root / "bench"

    @property
    def results(self) -> Path:
        return self.bench / "results"

    @property
    def main_programs(self) -> tuple[Design, ...]:
        return tuple(d for d in MAIN if d.id not in self.registry.constants.excluded_programs)


def read_study(root: str | Path, *, locked: bool = True, post_hoc: bool = False) -> Study:
    """Read the bench files under the root. With `locked`, the lock file must exist and every locked file
    must still match it, except the code files that `post_hoc` allows to differ (`check_lock`). The
    pilot runs before the lock and reads without it."""
    root = Path(root).resolve()
    bench_dir = root / "bench"
    registry = read_registry(bench_dir / "registry.json", root)
    lock = None
    changed: list[str] = []
    if locked:
        lock = read_lock(bench_dir / "BENCH_LOCK.json")
        changed = check_lock(lock, root, post_hoc=post_hoc)
    rows = read_runs(bench_dir / "runs.csv")
    move_lists = read_move_lists(bench_dir / "programs")
    missing = sorted({r.program for r in rows} - set(move_lists))
    if missing:
        raise InputError(f"bench/programs/ has no move list for {', '.join(missing)}.")
    return Study(root, registry, lock, rows, move_lists, read_orders(bench_dir / "order"), tuple(changed))


def design_problems(registry: Registry) -> list[str]:
    """Section 6.3: what keeps the registry from the complete design of the study.

    The registry gets a mounting record when the mounting is done (section 6.1), so it cannot tell a
    complete study from a part of one by itself. The complete design is blocks 1 to 12 with 6 mountings
    per device in the order W P P W P W W P W P P W, or blocks 1 to 16 with 8 mountings and blocks 13 to
    16 in the order P W W P. The mounting index of a device counts its blocks in order. Section 13
    forbids a device comparison before the last block, so `scriptkin bench analyse` refuses a registry
    with any other set of blocks.
    """
    blocks = registry.blocks
    if blocks not in (frozenset(range(1, 13)), frozenset(range(1, 17))):
        return [
            f"The registry lists blocks {sorted(blocks)}; the design of section 6.3 needs blocks 1 to 12,"
            " or 1 to 16 after the interim check added mountings."
        ]
    order = BLOCK_ORDER + (EXTRA_BLOCK_ORDER if len(blocks) == 16 else "")
    by_block = {m.block: m for m in registry.mountings}
    counts: dict[BenchRole, int] = {"P": 0, "W": 0}
    problems: list[str] = []
    for block, device in enumerate(order, start=1):
        record = by_block[block]
        counts[record.device] += 1
        if record.device != device:
            problems.append(
                f"Block {block} is device {record.device}; section 6.3 puts device {device} there."
            )
        expected = f"M{counts[record.device]}"
        if record.mounting != expected:
            problems.append(
                f"Block {block} is mounting {record.mounting} of device {record.device}; its block order"
                f" makes it {expected}."
            )
    return problems


def incomplete(study: Study, blocks: frozenset[int] | None = None) -> list[str]:
    """Section 13: what keeps the given blocks from the analysis.

    Without `blocks`, the whole study: the registry must hold the complete design of section 6.3
    (`design_problems`), every block must be complete, and every row must be recorded. With `blocks`,
    as the interim check gives, the rows of other blocks are skipped before the registry check, so that
    the planned rows of later blocks do not refuse the interim.

    A block is complete when its rows hold every run label of its order file `bench/order/block-<bb>.csv`
    (section 6.3), each at its position. `bench/runs.csv` can get a row only when a run is recorded, so
    without this rule the analysis would run during the last block, after its first run. A rerun of
    section 6.4 is an extra row, and any other row outside the order is a problem.
    """
    wanted = study.registry.blocks if blocks is None else blocks
    problems = design_problems(study.registry) if blocks is None else []
    excluded = set(study.registry.constants.excluded_programs)
    seen: set[int] = set()
    for row in study.rows:
        if blocks is not None and row.block not in wanted:
            continue
        if row.block not in study.registry.blocks:
            problems.append(f"{row.run_id} is in block {row.block}, which the registry does not list.")
            continue
        record = study.registry.mounting(row.device, row.mounting)
        if record.block != row.block:
            problems.append(
                f"{row.run_id} is in block {row.block}; the registry puts the mounting in {record.block}."
            )
        if row.program in excluded:
            problems.append(f"{row.run_id} is a run of the excluded program {row.program}.")
        seen.add(row.block)
        if row.status is RunStatus.PLANNED:
            problems.append(f"{row.run_id} is planned and not recorded.")
    for block in sorted(wanted):
        if block not in seen:
            problems.append(f"Block {block} has no row.")
        elif block in study.registry.blocks:
            problems.extend(missing_runs(study, block))
    return problems


def missing_runs(study: Study, block: int) -> list[str]:
    """Section 6.3: what keeps a block of the registry with rows from completeness (`incomplete`)."""
    order = study.orders.get(block)
    if order is None:
        return [f"Block {block} has no order file bench/order/block-{block:02d}.csv."]
    record = next(m for m in study.registry.mountings if m.block == block)
    rows = {(r.program, r.run_index): r for r in study.rows if r.block == block}
    problems: list[str] = []
    for label in order:
        run_id = f"{record.device}-{record.mounting}-{label.program}-r{label.repeat}"
        row = rows.pop((label.program, label.repeat), None)
        if row is None:
            problems.append(f"Block {block} has no row for {run_id}, position {label.position} of its order.")
        elif row.position != label.position:
            problems.append(f"{run_id} is at position {row.position}; the order puts it at {label.position}.")
    for row in rows.values():
        if not row.rerun:
            problems.append(f"{row.run_id} is not in the order of block {block}.")
    return problems


@dataclass(frozen=True)
class EvaluatedStudy:
    """The runs of a study after `evaluate_study`, with the journals they came from."""

    study: Study
    journals: dict[str, Journal]
    """By session id."""
    runs: dict[str, Evaluated]
    """By run id, recorded rows only."""
    hold: dict[tuple[BenchRole, str], float | Refusal]
    """The band power of the still hold of each mounting with a qualification session (section 8.1)."""


def hold_band_power(journal: Journal, settings: Preprocessing = DEFAULT_PREPROCESSING) -> float | Refusal:
    """Section 8.1: the 4 to 12 Hz band power of the grid speed of the `qual-hold` record of a session."""
    trial = next((t for t in journal.recording.trials if t.task.id == HOLD_TASK), None)
    if trial is None:
        return Refusal(RefusalCode.NOT_APPLICABLE, f"The session has no {HOLD_TASK} trial.")
    qc = assess(trial)
    if not isinstance(qc, Accepted):
        return qc.refusals[0]
    grids = [kinematics(s, settings) for s in qc.segments if s.state == PenState.CONTACT]
    speeds = [k.speed for k in grids if isinstance(k, Kinematics)]
    if not speeds:
        return next(k for k in grids if isinstance(k, Refusal))
    return welch_band_power(
        speeds,
        settings.grid_hz,
        settings.band_hz,
        window_s=settings.welch_window_s,
        min_windows=WELCH_MIN_WINDOWS,
    )


def hold_noise(
    study: Study, settings: Preprocessing = DEFAULT_PREPROCESSING
) -> dict[tuple[BenchRole, str], float | Refusal]:
    """The hold band power of every mounting with a qualification session. After the lock, the session
    must come from the locked build of its device, as a bench journal must (section 2.4)."""
    hold: dict[tuple[BenchRole, str], float | Refusal] = {}
    for m in study.registry.mountings:
        if m.qualification_session is None:
            continue
        folder = study.registry.journal_folder(m.qualification_session)
        journal = read_qualification_journal(folder, m.device, m.mounting)
        if study.lock is not None:
            check_build(study.lock, journal)
        hold[(m.device, m.mounting)] = hold_band_power(journal, settings)
    return hold


def evaluate_study(
    study: Study,
    rows: tuple[RunRow, ...] | None = None,
    *,
    policy: QcPolicy = DEFAULT_QC,
    settings: Preprocessing = DEFAULT_PREPROCESSING,
) -> EvaluatedStudy:
    """Every recorded row, by default all of them, evaluated. Journals are read once per session."""
    journals: dict[str, Journal] = {}
    commanded: dict[str, Motion | Refusal] = {}
    evaluated: dict[str, Evaluated] = {}
    for row in study.rows if rows is None else rows:
        if not row.recorded:
            continue
        journal = journals.get(row.session_id)
        if journal is None:
            journal = read_bench_journal(study.registry.journal_folder(row.session_id))
            if study.lock is not None:
                check_build(study.lock, journal)
            journals[row.session_id] = journal
        trial = trial_of(journal, row)
        move_list = study.move_lists[row.program]
        if row.program not in commanded:
            commanded[row.program] = commanded_motion(move_list, settings)
        track_file = study.registry.track_file(row)
        track = read_track(track_file) if track_file.is_file() else None
        if track is not None:
            check_calibration(track, study.registry.mounting(row.device, row.mounting))
        evaluated[row.run_id] = evaluate(
            row, trial, move_list, track, commanded=commanded[row.program], policy=policy, settings=settings
        )
    runs = mark_unused_reruns(evaluated, study.rows)
    return EvaluatedStudy(study, journals, runs, hold_noise(study, settings))


def check_calibration(track: Track, record: MountingRecord) -> None:
    """Section 7.2: the track was made with the calibration of its mounting, by the digest of the file."""
    if track.facts.calibration_sha256 != record.calibration_sha256:
        raise InputError(
            f"{track.facts.run_id}: the track names calibration {track.facts.calibration_sha256}, not the"
            f" calibration {record.calibration_sha256} of mounting {record.mounting} of device"
            f" {record.device}."
        )


def mark_unused_reruns(evaluated: Mapping[str, Evaluated], rows: tuple[RunRow, ...]) -> dict[str, Evaluated]:
    """Section 6.4: a rerun is used only when an earlier run of its program and mounting failed.

    Each failed run earns one rerun, and a rerun that passed earns none. So the rerun with run index 6
    needs one failed earlier run, and the rerun with run index 7 needs two: the failed planned run and
    the failed first rerun, or two failed planned runs. A void row counts as a failed run, because its
    recording left the study. A rerun that breaks the rule keeps its evaluation and gets the status
    `unused_rerun`, so that `run_status.csv` reports it and no analysis uses it.
    """
    failed: dict[tuple[BenchRole, str, str], list[int]] = {}
    for row in rows:
        if row.status is RunStatus.VOID:
            failed.setdefault((row.device, row.mounting, row.program), []).append(row.run_index)
    for run in evaluated.values():
        if run.outcome.failed:
            key = (run.row.device, run.row.mounting, run.row.program)
            failed.setdefault(key, []).append(run.row.run_index)
    marked: dict[str, Evaluated] = {}
    for run_id, run in evaluated.items():
        row = run.row
        earlier = [i for i in failed.get((row.device, row.mounting, row.program), []) if i < row.run_index]
        if row.rerun and len(earlier) < row.run_index - PLANNED_RUNS:
            run = replace(run, outcome=replace(run.outcome, unused_rerun=True))
        marked[run_id] = run
    return marked


def clean_medians(evaluated: Mapping[str, Evaluated], block: int) -> CleanMedians | None:
    """Section 5.3: the medians of the valid `loops-l-v20` runs of a block, or None without such a run."""
    values = [
        (e.device_value("bench.motion_s"), e.device_value("bench.path_length_mm"))
        for e in evaluated.values()
        if e.row.block == block and e.row.program == FAULT_BASE_ID and e.valid
    ]
    clean = [(m, p) for m, p in values if not isinstance(m, Refusal) and not isinstance(p, Refusal)]
    if not clean:
        return None
    motion, path = zip(*clean, strict=True)
    return CleanMedians(float(np.median(motion)), float(np.median(path)))


def start_point_mm(study: Study, run_id: str) -> tuple[float, float]:
    """The commanded start point of a run in the marker plane, for `scriptkin bench track --start-mm`.

    The start point of the move list is in the stimulus frame, with y down. The marker plane has y up,
    so the plane point is the origin of the mounting plus (x, -y).
    """
    device, mounting, program, _ = parse_run_id(run_id)
    origin = study.registry.mounting(device, mounting).origin_mm
    move_list = study.move_lists.get(program)
    if move_list is None:
        raise InputError(f"bench/programs/ has no move list for {program}.")
    x, y = move_list.start_mm
    return origin[0] + x, origin[1] - y
