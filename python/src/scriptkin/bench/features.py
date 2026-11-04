"""The motion interval and the bench features of one run (protocol sections 7.4, 8.1, 9.1, and 9.3).

A run is the `Accepted` result of `scriptkin.quality.assess` with `DEFAULT_QC`, from a device or from
a camera track. `run_features` resamples each accepted contact segment with `scriptkin.signal.kinematics`,
finds the motion interval with `motion_interval`, and computes every feature on the grid samples inside
the interval. The module computes every feature on every run. The analysis applies the program sets of
section 9.1, which `CATALOG` names. A feature that a run cannot define is a refusal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from scipy.signal import find_peaks

from scriptkin.bench import Geometry
from scriptkin.bench.moves import MoveList, commanded_trial
from scriptkin.features import Cell
from scriptkin.qualification import intervals
from scriptkin.quality import Accepted, Refusal, RefusalCode, Rejected, assess
from scriptkin.recording import FloatArray, PenState, Trial
from scriptkin.signal import (
    DEFAULT_PREPROCESSING,
    Kinematics,
    Preprocessing,
    kinematics,
    pool,
    welch_band_power,
)

MOTION_SPEED_MM_S = 2.0
MOTION_RUN = 6
WELCH_MIN_WINDOWS = 3
LOOP_PROMINENCE = 0.25
LOOP_DISTANCE_S = 0.25
MIN_LOOPS = 4
ALL: tuple[Geometry, ...] = ("loops", "spiral", "line")


@dataclass(frozen=True)
class BenchFeature:
    """One cell of a run. `decided_on` is the program set of section 9.1; empty for a descriptive cell."""

    name: str
    unit: str
    definition: str
    decided_on: tuple[Geometry, ...] = ()


CATALOG = (
    BenchFeature("bench.motion_s", "s", "The time from the motion start to the motion end.", ALL),
    BenchFeature(
        "bench.path_length_mm",
        "mm",
        "The trapezoid integral of the grid speed over the motion interval, summed over the segments.",
        ALL,
    ),
    BenchFeature(
        "bench.speed_mean_mm_s", "mm/s", "The mean of the grid speed over the motion interval.", ALL
    ),
    BenchFeature("bench.loop_height_mm", "mm", "The median height of the interior loops.", ("loops",)),
    BenchFeature(
        "bench.speed_cv",
        "1",
        "The standard deviation of the grid speed, with divisor n - 1, divided by its mean.",
        ("loops",),
    ),
    BenchFeature(
        "bench.band_power_mm2_s2",
        "(mm/s)^2",
        "The Welch power of the grid speed in the 4 to 12 Hz band, 2 s windows, at least 3 windows.",
        ("line",),
    ),
    BenchFeature("bench.ldlj", "1", "The mean over segments of ln(D^5 J / S^2)."),
    BenchFeature(
        "bench.chord_length_mm", "mm", "The chord sum of the device samples in the motion interval."
    ),
    BenchFeature("bench.motion_start_s", "s", "The motion start in the clock of the record."),
    BenchFeature("bench.motion_end_s", "s", "The motion end in the clock of the record."),
    BenchFeature("sampling.count", "count", "The count of the contact sample intervals."),
    BenchFeature("sampling.median_s", "s", "The median contact sample interval."),
    BenchFeature("sampling.p5_s", "s", "The 5th percentile of the contact sample intervals."),
    BenchFeature("sampling.p95_s", "s", "The 95th percentile of the contact sample intervals."),
    BenchFeature("sampling.share_above_30ms", "1", "The share of contact sample intervals above 30 ms."),
    BenchFeature(
        "sampling.share_above_max_gap", "1", "The share of contact sample intervals above max_gap_s."
    ),
)
_BENCH_NAMES = tuple(f.name for f in CATALOG if f.name.startswith("bench."))
_SAMPLING_NAMES = tuple(f.name for f in CATALOG if f.name.startswith("sampling."))


@dataclass(frozen=True)
class Interval:
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class Points:
    """The samples of one contact segment inside the motion interval, as the record holds them."""

    t: FloatArray
    xy: FloatArray


@dataclass(frozen=True)
class Motion:
    """One record inside its motion interval, per accepted contact segment, in time order.

    `grids` holds the grid samples inside the interval, with the derivatives of the whole segment.
    `samples` holds the raw samples inside the interval. A segment with no grid sample inside the
    interval is absent from both.
    """

    interval: Interval
    grids: tuple[Kinematics, ...]
    samples: tuple[Points, ...]


@dataclass(frozen=True)
class RunFeatures:
    motion: Motion | Refusal
    cells: dict[str, Cell]


def motion_interval(grids: Sequence[Kinematics]) -> Interval | Refusal:
    """Section 7.4. A run of 6 consecutive grid samples at 2 mm/s or more lies inside one segment.

    The motion start is the first sample of the first such run. The motion end is the last sample of
    the last such run. The interval includes the gaps between the segments.
    """
    starts: list[float] = []
    ends: list[float] = []
    for k in grids:
        if len(k.speed) < MOTION_RUN:
            continue
        fast = (k.speed >= MOTION_SPEED_MM_S).astype(float)
        runs = np.flatnonzero(np.convolve(fast, np.ones(MOTION_RUN), mode="valid") == MOTION_RUN)
        if len(runs):
            starts.append(float(k.t[runs[0]]))
            ends.append(float(k.t[runs[-1] + MOTION_RUN - 1]))
    if not starts:
        return Refusal(
            RefusalCode.UNDEFINED, f"No run of {MOTION_RUN} grid samples reaches {MOTION_SPEED_MM_S:g} mm/s."
        )
    return Interval(min(starts), max(ends))


def motion(ok: Accepted, settings: Preprocessing = DEFAULT_PREPROCESSING) -> Motion | Refusal:
    """The accepted contact segments of one run on the grid, cut to the motion interval."""
    if ok.trial.device.spatial_unit != "mm":
        return Refusal(RefusalCode.UNKNOWN_SCALE, "A physical position scale is required.")
    contact = [s for s in ok.segments if s.state == PenState.CONTACT]
    resampled = [(s, kinematics(s, settings)) for s in contact]
    valid = sorted(((s, k) for s, k in resampled if isinstance(k, Kinematics)), key=lambda pair: pair[1].t[0])
    interval = motion_interval([k for _, k in valid])
    if isinstance(interval, Refusal):
        return interval
    grids: list[Kinematics] = []
    samples: list[Points] = []
    for s, k in valid:
        inside = (k.t >= interval.start_s) & (k.t <= interval.end_s)
        if not inside.any():
            continue
        grids.append(
            Kinematics(
                k.t[inside],
                k.xy[inside],
                k.velocity[inside],
                k.acceleration[inside],
                k.jerk[inside],
                k.speed[inside],
            )
        )
        raw = (s.t >= interval.start_s) & (s.t <= interval.end_s)
        samples.append(Points(s.t[raw], s.xy[raw]))
    return Motion(interval, tuple(grids), tuple(samples))


def _loop_height(y: FloatArray, grid_hz: float) -> Cell:
    """Section 9.1 on y in the stimulus frame, where y goes down and a baseline point is a maximum."""
    if len(y) < 2:
        return Refusal(RefusalCode.TOO_FEW_VALUES, "The loops need two grid samples.")
    p5, p95 = np.percentile(y, [5, 95])
    peaks = find_peaks(y, prominence=LOOP_PROMINENCE * (p95 - p5), distance=round(LOOP_DISTANCE_S * grid_hz))[
        0
    ]
    baseline = np.concatenate(([0], peaks, [len(y) - 1]))
    count = len(baseline) - 1
    if count < MIN_LOOPS:
        return Refusal(RefusalCode.UNDEFINED, f"The run has {count} loops; the median needs {MIN_LOOPS}.")
    heights = [float((y[a] + y[b]) / 2 - y[a:b].min()) for a, b in pairwise(baseline)]
    return float(np.median(heights[1:-1]))


def _speed_cv(speed: FloatArray) -> Cell:
    if len(speed) < 2:
        return Refusal(RefusalCode.TOO_FEW_VALUES, "The speed CV needs two grid samples.")
    mean = float(np.mean(speed))
    if mean <= 0:
        return Refusal(RefusalCode.UNDEFINED, "The mean speed is zero.")
    return float(np.std(speed, ddof=1) / mean)


def _ldlj(k: Kinematics) -> Cell:
    """Section 9.3: ln(D^5 J / S^2) of one segment: its duration D, its chord sum S, its jerk integral J."""
    duration = float(k.t[-1] - k.t[0]) if len(k.t) > 1 else 0.0
    chords = float(np.linalg.norm(np.diff(k.xy, axis=0), axis=1).sum())
    jerk = float(np.trapezoid(np.sum(k.jerk**2, axis=1), k.t)) if len(k.t) > 1 else 0.0
    if duration <= 0 or chords <= 0 or jerk <= 0:
        return Refusal(RefusalCode.UNDEFINED, "The segment has no duration, no length, or no jerk.")
    return float(np.log(duration**5 * jerk / chords**2))


def feature_cells(
    motion: Motion | Refusal, settings: Preprocessing = DEFAULT_PREPROCESSING
) -> dict[str, Cell]:
    """The `bench.*` cells of one record. Every cell is the refusal when the motion interval is one."""
    if isinstance(motion, Refusal):
        return dict.fromkeys(_BENCH_NAMES, motion)
    speed = np.concatenate([g.speed for g in motion.grids])
    y_down = -np.concatenate([g.xy[:, 1] for g in motion.grids])
    cells: dict[str, Cell] = {
        "bench.motion_s": motion.interval.duration_s,
        "bench.path_length_mm": float(sum(float(np.trapezoid(g.speed, g.t)) for g in motion.grids)),
        "bench.speed_mean_mm_s": float(np.mean(speed)),
        "bench.loop_height_mm": _loop_height(y_down, settings.grid_hz),
        "bench.speed_cv": _speed_cv(speed),
        "bench.band_power_mm2_s2": welch_band_power(
            [g.speed for g in motion.grids],
            settings.grid_hz,
            settings.band_hz,
            window_s=settings.welch_window_s,
            min_windows=WELCH_MIN_WINDOWS,
        ),
        "bench.ldlj": pool([_ldlj(g) for g in motion.grids]),
        "bench.chord_length_mm": float(
            sum(float(np.linalg.norm(np.diff(p.xy, axis=0), axis=1).sum()) for p in motion.samples)
        ),
        "bench.motion_start_s": motion.interval.start_s,
        "bench.motion_end_s": motion.interval.end_s,
    }
    return {
        name: cell
        if isinstance(cell, Refusal) or np.isfinite(cell)
        else Refusal(RefusalCode.UNDEFINED, "The feature is not finite.")
        for name, cell in cells.items()
    }


def sampling_cells(trial: Trial, max_gap_s: float) -> dict[str, Cell]:
    """Section 8.1: the contact sample intervals of one run, from its contact strokes."""
    strokes = [s for s in trial.strokes if s.state == PenState.CONTACT]
    base = intervals(strokes)
    above_gap = intervals(strokes, gap_s=max_gap_s)
    if isinstance(base, Refusal):
        return dict.fromkeys(_SAMPLING_NAMES, base)
    if isinstance(above_gap, Refusal):
        return dict.fromkeys(_SAMPLING_NAMES, above_gap)
    return {
        "sampling.count": float(base.count),
        "sampling.median_s": base.median_s,
        "sampling.p5_s": base.p5_s,
        "sampling.p95_s": base.p95_s,
        "sampling.share_above_30ms": base.gap_share,
        "sampling.share_above_max_gap": above_gap.gap_share,
    }


def run_features(ok: Accepted, settings: Preprocessing = DEFAULT_PREPROCESSING) -> RunFeatures:
    """Every cell of `CATALOG` for one run, with the motion that the camera comparisons need."""
    found = motion(ok, settings)
    return RunFeatures(
        found, {**feature_cells(found, settings), **sampling_cells(ok.trial, ok.report.policy.max_gap_s)}
    )


def commanded_motion(
    move_list: MoveList, settings: Preprocessing = DEFAULT_PREPROCESSING
) -> Motion | Refusal:
    """Section 7.6: the motion interval rule on the commanded path, through the same pipeline as a run."""
    qc = assess(commanded_trial(move_list))
    if isinstance(qc, Rejected):
        return qc.refusals[0]
    return motion(qc, settings)
