"""One bench run from its journal trial and its track (protocol sections 5.3, 6.4, 7.6, 7.7, 8.2, 9).

`trial_of` finds the trial of a row in its journal and checks that the two agree. `evaluate` computes
everything that the analysis reads from one run: the quality assessment, the bench features, the library
features of section 9.3, the camera record with its validity, the executed movement check, the time
alignment, the spatial accuracy, the trace error, and the outcome of section 6.4.

The outcome. A main run fails when `assess` rejects it, when the camera check finds a motion failure, or
when the operator marked `disturbance` or `deviceProblem`. The analysis uses the valid runs. A run without
a valid camera record has no motion failure and stays valid for the features that the camera does not
reference (section 7.7). `failed_without_motion_check` is the status of the sensitivity analysis of
section 7.6, which keeps the motion failures.

The comparison scale of section 11.1 is a property of the feature name: `comparison_value` takes the
natural logarithm of every feature of the feature set except `bench.speed_cv`. A refusal, and a value
that is not positive on the logarithmic scale, give NaN, which the statistics read as a missing run.

The fault rule of section 5.3 is `fault_detected`. The clean medians come from the valid `loops-l-v20`
runs of the block, which only the analysis knows, so the caller passes them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from scriptkin import bench
from scriptkin.bench.camera import (
    CameraRecord,
    CameraRule,
    ExecutedMovement,
    PathFit,
    SpatialAccuracy,
    TimeAlignment,
    camera_record,
    path_fit,
    spatial_accuracy,
    time_alignment,
)
from scriptkin.bench.features import CATALOG as BENCH_CATALOG
from scriptkin.bench.features import Motion, RunFeatures, commanded_motion, run_features
from scriptkin.bench.inputs import InputError, Journal, RunRow
from scriptkin.bench.moves import MoveList
from scriptkin.bench.track import Track
from scriptkin.features import CATALOG as LIBRARY_CATALOG
from scriptkin.features import Cell, extract
from scriptkin.quality import DEFAULT_QC, Accepted, QcPolicy, Refusal, RefusalCode, Rejected, assess
from scriptkin.recording import BenchRunContext, PenState, Trial
from scriptkin.signal import DEFAULT_PREPROCESSING, Preprocessing

FEATURE_SET = tuple(f.name for f in BENCH_CATALOG if f.decided_on)
CAMERA_FEATURES = (
    "bench.motion_s",
    "bench.path_length_mm",
    "bench.speed_mean_mm_s",
    "bench.loop_height_mm",
)
LOG_SCALE = frozenset(FEATURE_SET) - {"bench.speed_cv"}
FAILURE_MARKS = ("disturbance", "deviceProblem")
FAULT_TOLERANCE = 0.02
TRUNCATED_SHARE = 0.6
DWELL_S = 0.5


class CameraState(StrEnum):
    ABSENT = "absent"
    """No track file for the run."""
    INVALID = "invalid"
    """A track that fails a rule of section 7.3, has no motion interval, or has no executed movement
    check of section 7.6. The run then leaves the camera-referenced comparison (section 7.7)."""
    VALID = "valid"


@dataclass(frozen=True)
class Outcome:
    """Section 6.4 for one run."""

    rejected: tuple[RefusalCode, ...]
    marks: tuple[str, ...]
    """The `disturbance` and `deviceProblem` marks of the trial."""
    camera: CameraState
    camera_rules: tuple[CameraRule, ...]
    motion_failure: bool | None
    """None without a valid camera record or a path fit."""
    unused_rerun: bool = False
    """Section 6.4: the rerun follows fewer failed earlier runs of its program and mounting than its
    run index needs, so the analysis does not use it (`scriptkin.bench.study.mark_unused_reruns`)."""

    @property
    def failed_without_motion_check(self) -> bool:
        return bool(self.rejected or self.marks)

    @property
    def failed(self) -> bool:
        return self.failed_without_motion_check or bool(self.motion_failure)

    @property
    def reason(self) -> str:
        parts = [str(code) for code in self.rejected] + list(self.marks)
        if self.motion_failure:
            parts.append("motion_failure")
        if self.unused_rerun:
            parts.append("unused_rerun")
        return ";".join(parts)


@dataclass(frozen=True)
class Evaluated:
    """Everything that the analysis reads from one run."""

    row: RunRow
    trial: Trial
    move_list: MoveList
    qc: Accepted | Rejected
    features: RunFeatures | Refusal
    """The bench features of the device record, or the refusal of `assess` when it rejects the trial."""
    library: dict[str, Cell]
    """The library features of section 9.3; every cell a refusal when `assess` rejects the trial."""
    camera: CameraRecord | None
    executed: ExecutedMovement | Refusal | None
    alignment: TimeAlignment | Refusal | None
    accuracy: SpatialAccuracy | Refusal | None
    trace: PathFit | Refusal | None
    outcome: Outcome

    @property
    def valid(self) -> bool:
        """A main run that the analysis uses: not failed, not a fault run, and not an unused rerun."""
        return not self.row.fault and not self.outcome.failed and not self.outcome.unused_rerun

    def device_value(self, name: str) -> Cell:
        return self.features.cells[name] if isinstance(self.features, RunFeatures) else self.features

    def camera_value(self, name: str) -> Cell:
        if self.camera is None:
            return Refusal(RefusalCode.NOT_APPLICABLE, "The run has no camera record.")
        if self.outcome.camera is not CameraState.VALID:
            return Refusal(RefusalCode.NOT_APPLICABLE, "The camera record is invalid.")
        return self.camera.run.cells[name]

    def camera_difference(self, name: str) -> float:
        """Section 9.2: the device value minus the camera value on the comparison scale, or NaN."""
        return comparison_value(name, self.device_value(name)) - comparison_value(
            name, self.camera_value(name)
        )


def comparison_value(name: str, cell: Cell) -> float:
    """Section 11.1: ln of a logarithmic feature, the value of `bench.speed_cv`, NaN for a refusal."""
    if isinstance(cell, Refusal):
        return math.nan
    if name in LOG_SCALE:
        return math.log(cell) if cell > 0 else math.nan
    return cell


def trial_of(journal: Journal, row: RunRow) -> Trial:
    """The trial of a row in its journal. The row, the trial, and the programs file must agree."""
    if (journal.role, journal.mounting) != (row.device, row.mounting):
        raise InputError(
            f"{row.run_id}: session {journal.recording.session_id} is device {journal.role} mounting"
            f" {journal.mounting}."
        )
    trial = next((t for t in journal.recording.trials if t.index == row.trial_index), None)
    if trial is None:
        raise InputError(
            f"{row.run_id}: session {journal.recording.session_id} has no trial {row.trial_index}."
        )
    context = trial.context
    if not isinstance(context, BenchRunContext):
        raise InputError(f"{row.run_id}: trial {row.trial_index} is not a bench run.")
    program = bench.program(row.program)
    expected = (row.run_id, row.program, row.run_index, bench.trajectory_sha256(program.id), program.task)
    found = (context.clip, context.program, context.run, context.trajectory_sha256, trial.task.id)
    if found != expected:
        raise InputError(
            f"{row.run_id}: trial {row.trial_index} of session {journal.recording.session_id} records"
            f" {found}, not {expected}."
        )
    return trial


def evaluate(
    row: RunRow,
    trial: Trial,
    move_list: MoveList,
    track: Track | None,
    *,
    commanded: Motion | Refusal | None = None,
    policy: QcPolicy = DEFAULT_QC,
    settings: Preprocessing = DEFAULT_PREPROCESSING,
) -> Evaluated:
    """Everything of one run. `commanded` is `commanded_motion(move_list)`, which a study computes once
    per program; without it the function computes it."""
    if commanded is None:
        commanded = commanded_motion(move_list, settings)
    qc = assess(trial, policy)
    features: RunFeatures | Refusal
    library: dict[str, Cell]
    if isinstance(qc, Accepted):
        features, library = run_features(qc, settings), extract(qc, settings)
    else:
        features, library = qc.refusals[0], {f.name: qc.refusals[0] for f in LIBRARY_CATALOG}
    device = features.motion if isinstance(features, RunFeatures) else None
    device = device if isinstance(device, Motion) else None
    reference = device if device is not None else commanded if isinstance(commanded, Motion) else None
    camera = camera_record(track, reference, settings) if track is not None else None
    executed = alignment = accuracy = None
    if camera is not None and camera.valid and isinstance(camera.run.motion, Motion):
        executed = _executed(camera.run.motion, move_list, commanded, settings)
        if device is not None:
            alignment = time_alignment(device, camera.run.motion, settings)
            if isinstance(alignment, TimeAlignment):
                accuracy = spatial_accuracy(device, camera.run.motion, alignment)
    trace = _fit(device, move_list, commanded, settings) if device is not None else None
    if camera is None:
        state = CameraState.ABSENT
    elif isinstance(executed, ExecutedMovement):
        state = CameraState.VALID
    else:
        state = CameraState.INVALID
    outcome = Outcome(
        tuple(r.code for r in qc.refusals) if isinstance(qc, Rejected) else (),
        tuple(m for m in trial.marks if m in FAILURE_MARKS),
        state,
        camera.failed if camera is not None else (),
        executed.failure if isinstance(executed, ExecutedMovement) else None,
    )
    return Evaluated(
        row, trial, move_list, qc, features, library, camera, executed, alignment, accuracy, trace, outcome
    )


def _fit(
    record: Motion, move_list: MoveList, commanded: Motion | Refusal, settings: Preprocessing
) -> PathFit | Refusal:
    """Section 8.2 as `camera.trace_error` computes it, on a commanded motion computed once."""
    if isinstance(commanded, Refusal):
        return commanded
    return path_fit(record, move_list.path(), commanded, settings)


def _executed(
    record: Motion, move_list: MoveList, commanded: Motion | Refusal, settings: Preprocessing
) -> ExecutedMovement | Refusal:
    """Section 7.6 as `camera.executed_movement` computes it, on a commanded motion computed once."""
    if isinstance(commanded, Refusal):
        return commanded
    fit = _fit(record, move_list, commanded, settings)
    if isinstance(fit, Refusal):
        return fit
    return ExecutedMovement(fit, record.interval.duration_s, commanded.interval.duration_s)


@dataclass(frozen=True)
class CleanMedians:
    """The medians of the valid `loops-l-v20` runs of a block (section 5.3)."""

    motion_s: float
    path_length_mm: float


def _within(value: Cell, target: float, tolerance: float) -> bool:
    return not isinstance(value, Refusal) and abs(value - target) <= tolerance * target


def fault_detected(run: Evaluated, clean: CleanMedians, *, gaps_on_p: bool) -> tuple[bool, str]:
    """Section 5.3: whether every expected result of the fault program holds, with the reason when not.

    The dwell rule reads the contact strokes only. A bench journal also holds the hover strokes of the
    lowering and the lift, which the library counts as segments and checks for gaps. A contact gap is
    a contact sample interval above `max_gap_s`, the interval that gives the `gaps` finding.
    """
    program = bench.program(run.row.program)
    motion = run.device_value("bench.motion_s")
    if program.fault == "dwell":
        gap_share = run.device_value("sampling.share_above_max_gap")
        gaps = not isinstance(gap_share, Refusal) and gap_share > 0
        segments = (
            sum(s.state == PenState.CONTACT for s in run.qc.segments) if isinstance(run.qc, Accepted) else 0
        )
        expect_gaps = gaps_on_p and run.row.device == "P"
        if expect_gaps and not (gaps and segments == 2):
            return False, "no gaps finding with two contact segments"
        if not expect_gaps and gaps:
            return False, "a gaps finding"
        target = clean.motion_s + DWELL_S
        if not _within(motion, target, FAULT_TOLERANCE):
            return False, f"motion_s {_text(motion)} is not within 2 percent of {target:.3f}"
        return True, ""
    if program.fault == "lift":
        count = run.library["timing.stroke_count"]
        if isinstance(count, Refusal) or count != 2:
            return False, f"stroke_count {_text(count)} is not 2"
        return True, ""
    if program.fault == "truncated":
        path = run.device_value("bench.path_length_mm")
        for name, value, median in (
            ("path_length_mm", path, clean.path_length_mm),
            ("motion_s", motion, clean.motion_s),
        ):
            if isinstance(value, Refusal) or value > TRUNCATED_SHARE * median:
                return False, f"{name} {_text(value)} is above 0.6 times {median:.3f}"
        return True, ""
    raise ValueError(f"{program.id} is not a fault program.")


def _text(cell: Cell) -> str:
    return cell.code if isinstance(cell, Refusal) else f"{cell:.4g}"
