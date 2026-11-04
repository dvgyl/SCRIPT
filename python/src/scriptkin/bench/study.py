"""The bench study as the analysis reads it: the files of section 15 once, and every run evaluated once.

`read_study` reads the registry, the lock, the run rows, and the move lists under a root, and checks the
lock (section 14). `incomplete` lists what keeps the study from the analysis of section 13: a block of the
registry without a row, a planned row, or a row of a block or program outside the study. `evaluate_study`
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
    Registry,
    RunRow,
    RunStatus,
    check_build,
    check_lock,
    parse_run_id,
    read_bench_journal,
    read_lock,
    read_registry,
    read_runs,
)
from scriptkin.bench.moves import MoveList, read_move_lists
from scriptkin.bench.runs import CleanMedians, Evaluated, evaluate, trial_of
from scriptkin.bench.track import read_track
from scriptkin.formats.export import read_journal
from scriptkin.formats.journal import JournalError
from scriptkin.qualification import HOLD_TASK
from scriptkin.quality import DEFAULT_QC, Accepted, QcPolicy, Refusal, RefusalCode, assess
from scriptkin.recording import BenchRole, PenState
from scriptkin.signal import DEFAULT_PREPROCESSING, Kinematics, Preprocessing, kinematics, welch_band_power

FAULT_BASE_ID = "loops-l-v20"


@dataclass(frozen=True)
class Study:
    root: Path
    registry: Registry
    lock: Lock | None
    """None before the lock, which only the pilot of section 6.5 allows."""
    rows: tuple[RunRow, ...]
    move_lists: dict[str, MoveList]

    @property
    def bench(self) -> Path:
        return self.root / "bench"

    @property
    def results(self) -> Path:
        return self.bench / "results"

    @property
    def main_programs(self) -> tuple[Design, ...]:
        return tuple(d for d in MAIN if d.id not in self.registry.constants.excluded_programs)


def read_study(root: str | Path, *, locked: bool = True) -> Study:
    """Read the bench files under the root. With `locked`, the lock file must exist and every locked file
    must still match it. The pilot runs before the lock and reads without it."""
    root = Path(root).resolve()
    bench_dir = root / "bench"
    registry = read_registry(bench_dir / "registry.json", root)
    lock = None
    if locked:
        lock = read_lock(bench_dir / "BENCH_LOCK.json")
        check_lock(lock, root)
    rows = read_runs(bench_dir / "runs.csv")
    move_lists = read_move_lists(bench_dir / "programs")
    missing = sorted({r.program for r in rows} - set(move_lists))
    if missing:
        raise InputError(f"bench/programs/ has no move list for {', '.join(missing)}.")
    return Study(root, registry, lock, rows, move_lists)


def incomplete(study: Study, blocks: frozenset[int] | None = None) -> list[str]:
    """Section 13: what keeps the given blocks, by default every block of the registry, from the analysis."""
    wanted = study.registry.blocks if blocks is None else blocks
    problems: list[str] = []
    excluded = set(study.registry.constants.excluded_programs)
    seen: set[int] = set()
    for row in study.rows:
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
        if row.block not in wanted:
            continue
        seen.add(row.block)
        if row.status is RunStatus.PLANNED:
            problems.append(f"{row.run_id} is planned and not recorded.")
    for block in sorted(wanted - seen):
        problems.append(f"Block {block} has no row.")
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


def hold_band_power(folder: Path, settings: Preprocessing = DEFAULT_PREPROCESSING) -> float | Refusal:
    """Section 8.1: the 4 to 12 Hz band power of the grid speed of the `qual-hold` record of a session."""
    try:
        recording = read_journal(folder)
    except JournalError as e:
        raise InputError(f"{folder.name}: {e}") from e
    context = recording.context
    if context is None or context.purpose != "qualification":
        raise InputError(f"{folder.name}: the session is not a qualification session.")
    trial = next((t for t in recording.trials if t.task.id == HOLD_TASK), None)
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
    return {
        (m.device, m.mounting): hold_band_power(
            study.registry.journal_folder(m.qualification_session), settings
        )
        for m in study.registry.mountings
        if m.qualification_session is not None
    }


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
        evaluated[row.run_id] = evaluate(
            row, trial, move_list, track, commanded=commanded[row.program], policy=policy, settings=settings
        )
    runs = mark_unused_reruns(evaluated, study.rows)
    return EvaluatedStudy(study, journals, runs, hold_noise(study, settings))


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
