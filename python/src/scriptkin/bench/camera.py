"""The camera record of one run (protocol sections 7.3 to 7.7, 8.2, and 9.2).

The input is a `Track` of `scriptkin.bench.track`. `camera_record` makes the frames with a marker into one
contact stroke in millimetres, assesses it, finds the motion interval inside the window of `motion_window`,
computes the features with the code of a device run, and applies the validity rules inside the motion
interval.

The motion window. The clip starts before the lowering of section 5.2 step 3 and ends after the lift of
step 5. A camera at height D above the marker plane sees a marker raised by dz at X dz / (D - dz) from
its true position, away from the nadir, where X is the distance from the nadir. A lowering at 10 mm/s
therefore appears as a movement of 2 to 5 mm/s at the start points, above the 2 mm/s of section 7.4,
and the rule alone would start the motion interval at the lowering. The rule is unchanged. It applies
only inside a window around the commanded motion of the program, which a coarse alignment of the
speed profiles places in the clip (`coarse_alignment`). The commanded motion is the reference of every
run, so the window never depends on the device record that the camera measures. A motion interval that
touches an edge of the window is cut, because the movement extends beyond the commanded window, and
the run is then a motion failure of section 7.6 (`touches_window`, `ExecutedMovement.window_edge`).

A failed coarse alignment. A clip without a window has no motion interval, so the rules of section 7.3
apply over the whole clip. The record is invalid (section 7.7) only for a camera cause: the clip is
shorter than the commanded motion, or it fails a rule of section 7.3 over the whole clip. A clip without
a camera cause whose profile correlates below `COARSE_CORRELATION` shows a movement that does not match
the command, so the record is valid and `misaligned`, and the run is a motion failure of section 7.6
with the reason `alignment`. With the commanded motion as the only reference, a pause of 0.5, 2, or
4 s inside `loops-l-v20`, or a run 5 percent off the command on `loops-l-v20` or `loops-l-v80`,
correlates at 0.18 to 0.48; as an invalid record it kept the run for the features that the camera
does not reference. A clip that starts 0.2 to 1 s after the movement starts, but is still as long as
the command, also fails the run, with `window_edge` or `alignment`: the camera cannot tell it from a
movement that does not match the command.

The QC policy. The camera record has no policy of its own, so `DEFAULT_QC` applies. Its `max_gap_s` of
0.05 s is twelve frame intervals at 240 frames per second: a marker lost for twelve or more consecutive
frames splits the track into two segments, as the same loss would split a device record. `min_rate_hz`
and `min_segment_s` are far below the frame rate and the clip length, so they never bind.

The LED frame count. A 1 Hz LED read as on or off in each frame gives the frame count of every LED
period. At a measured rate between two integers, a clean period holds either integer count, so a rule
with a tolerance below one frame fails clean clips. The rule here allows one frame plus the 0.1 percent
rate tolerance of section 7.3, on every full period inside the motion interval and on all of them
together. In a simulation over the rates 239.76 to 240.24, the program durations, and the LED and frame
phases, the literal 0.1 percent rule of section 7.3 fails 74 percent of clean clips, and this rule fails
none. This rule detects a rate error above 0.1 percent, two or more frames lost in one period, and one
frame lost in a period that was already short: 6 percent of single lost frames. It cannot tell one lost
frame from the natural short period of a non-integer rate. With capture times the frame interval rule
catches one lost frame; with playback times nothing does.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from scipy.spatial import KDTree

from scriptkin.bench.features import (
    Motion,
    MotionInterval,
    Points,
    RunFeatures,
    commanded_motion,
    contact_grids,
    feature_cells,
    run_features,
    sampling_cells,
)
from scriptkin.bench.moves import CommandedPath, MoveList
from scriptkin.bench.track import BoolArray, FrameTimes, Track
from scriptkin.quality import DEFAULT_QC, Accepted, Refusal, RefusalCode, assess
from scriptkin.recording import Device, FloatArray, IntArray, Stroke, TaskRef, Trial
from scriptkin.signal import DEFAULT_PREPROCESSING, Kinematics, Preprocessing

MARKER_MISSING_SHARE = 0.02
FRAME_INTERVAL_TOLERANCE = 0.01
FRAME_INTERVAL_RESOLUTION_S = 1e-6
LED_RATE_TOLERANCE = 0.001
MAX_PATH_DISTANCE_MM = 0.2
MAX_DURATION_ERROR = 0.01
MAX_ROTATION_DEG = 1.0
COARSE_CORRELATION = 0.5
COARSE_SMOOTHING_S = 0.2
WINDOW_BEFORE_S = 0.2
WINDOW_AFTER_S = 0.15
_ICP_MAX_ITERATIONS = 100
_ICP_TOLERANCE_MM = 1e-6
_MIRROR = np.array([1.0, -1.0])


class CameraRule(StrEnum):
    ALIGNMENT = "alignment"
    """The record has no window (`coarse_alignment`), and the cause is not the movement: the run has no
    commanded motion, the clip does not hold the whole commanded motion, or the clip fails another rule
    over the whole clip."""
    DUPLICATE_FRAME = "duplicate_frame"
    """A duplicate frame inside the motion interval."""
    FRAME_INTERVAL = "frame_interval"
    """Capture times only: a frame interval in the motion interval off the median by 1 percent plus 1 us."""
    LED_COUNT = "led_count"
    """The LED frame count inside the motion interval disagrees with the measured rate."""
    MARKER_MISSING = "marker_missing"
    """More than 2 percent of the frames of the clip have no marker."""


@dataclass(frozen=True)
class CameraRecord:
    """A track as a recording, with its features and the validity rules that it fails."""

    track: Track
    run: RunFeatures
    failed: tuple[CameraRule, ...]
    window_edge: bool = False
    """The motion interval touches an edge of its window (`touches_window`). The record is valid, and
    the executed movement check of section 7.6 fails the run."""
    alignment: CoarseAlignment | None = None
    """The coarse alignment of the clip, or None when it cannot be computed."""

    @property
    def valid(self) -> bool:
        """The record passes every rule and has a motion interval, so it can measure the movement."""
        return not self.failed and isinstance(self.run.motion, Motion)

    @property
    def misaligned(self) -> bool:
        """The clip passes every rule over the whole clip, but its profile does not match the command. The
        record has no motion interval, and the run is a motion failure of section 7.6."""
        return not self.failed and self.alignment is not None and not self.alignment.matches


@dataclass(frozen=True)
class TimeAlignment:
    """Camera time equals device time plus `lag_s`. `correlation` is the normalised peak, from -1 to 1."""

    lag_s: float
    correlation: float


@dataclass(frozen=True)
class SpatialAccuracy:
    """Section 8.2: the device samples onto the camera track over the matched grid times."""

    rotation_deg: float
    rms_mm: float
    p95_mm: float
    matched: int

    @property
    def rotation_deviation(self) -> bool:
        """Section 8.2: a fitted rotation above 1 degree is a deviation."""
        return abs(self.rotation_deg) > MAX_ROTATION_DEG


@dataclass(frozen=True)
class PathFit:
    """A record aligned to the commanded path by a translation and a rotation, with its nearest distances."""

    rotation_deg: float
    shift_mm: tuple[float, float]
    rms_mm: float
    max_mm: float
    iterations: int


class MotionCheck(StrEnum):
    """The executed movement check of section 7.6 for one run. A failure value is its reason in
    `run_status.csv`."""

    PASSED = "passed"
    MOTION_FAILURE = "motion_failure"
    """The largest path distance is above 0.2 mm, or the motion duration is off the command by more than
    1 percent."""
    WINDOW_EDGE = "window_edge"
    """The camera motion interval touches an edge of its window (`CameraRecord.window_edge`)."""
    ALIGNMENT = "alignment"
    """The clip has no camera cause, but its profile does not match the command
    (`CameraRecord.misaligned`)."""


@dataclass(frozen=True)
class ExecutedMovement:
    """Section 7.6 for one run with a valid camera record."""

    fit: PathFit
    motion_s: float
    commanded_s: float
    window_edge: bool = False
    """The camera motion interval touches an edge of its window, so the movement extends beyond the
    commanded window and `motion_s` holds only a part of it (`CameraRecord.window_edge`)."""

    @property
    def check(self) -> MotionCheck:
        if self.window_edge:
            return MotionCheck.WINDOW_EDGE
        if (
            self.fit.max_mm > MAX_PATH_DISTANCE_MM
            or abs(self.motion_s - self.commanded_s) > MAX_DURATION_ERROR * self.commanded_s
        ):
            return MotionCheck.MOTION_FAILURE
        return MotionCheck.PASSED

    @property
    def failure(self) -> bool:
        return self.check is not MotionCheck.PASSED


def camera_trial(track: Track) -> Trial:
    """Section 9.2: the frames with a marker as one contact stroke in millimetres with y up."""
    found = track.found
    t = track.t[found]
    stroke = Stroke(t, track.xy[found])
    started, ended = (float(t[0]), float(t[-1])) if len(t) else (0.0, 0.0)
    return Trial(
        0, TaskRef(track.facts.run_id), (stroke,), Device("camera", "tablet"), started=started, ended=ended
    )


def led_count_agrees(led: BoolArray, rate_hz: float) -> bool:
    """The LED rule of the module docstring on the frames inside the motion interval.

    A period runs from one frame where the LED turns on to the next. Every full period, and all of
    them together, must hold the frames of the measured rate within one frame plus 0.1 percent. One
    full period, which is two frames where the LED turns on, is enough to apply the rule. Without
    one, the rule cannot be applied, and the record is invalid.
    """
    edges = np.flatnonzero(led[1:] & ~led[:-1]) + 1
    if len(edges) < 2:
        return False
    counts = np.diff(edges).astype(float)
    total = float(edges[-1] - edges[0])
    periods = len(counts)
    return bool(np.all(np.abs(counts - rate_hz) < 1 + LED_RATE_TOLERANCE * rate_hz)) and (
        abs(total - periods * rate_hz) < 1 + LED_RATE_TOLERANCE * periods * rate_hz
    )


def whole_clip(track: Track) -> MotionInterval | None:
    """The interval from the first frame to the last, for the rules of section 7.3 without a window."""
    return MotionInterval(float(track.t_frame[0]), float(track.t_frame[-1])) if len(track.t_frame) else None


def validity(track: Track, interval: MotionInterval | None) -> tuple[CameraRule, ...]:
    """Sections 7.3 and 7.7. The rules inside the motion interval need one; the clip rule applies without."""
    failed: list[CameraRule] = []
    if not len(track.frame) or float(np.mean(~track.found)) > MARKER_MISSING_SHARE:
        failed.append(CameraRule.MARKER_MISSING)
    if interval is None:
        return tuple(failed)
    inside = (track.t_frame >= interval.start_s) & (track.t_frame <= interval.end_s)
    if track.duplicate[inside].any():
        failed.append(CameraRule.DUPLICATE_FRAME)
    if track.facts.frame_times == FrameTimes.CAPTURE:
        dt = np.diff(track.t_frame[inside])
        median = float(np.median(dt)) if len(dt) else 0.0
        if not len(dt) or np.any(
            np.abs(dt - median) > FRAME_INTERVAL_TOLERANCE * median + FRAME_INTERVAL_RESOLUTION_S
        ):
            failed.append(CameraRule.FRAME_INTERVAL)
    if not led_count_agrees(track.led[inside], track.facts.frame_rate_hz):
        failed.append(CameraRule.LED_COUNT)
    return tuple(sorted(failed))


def _sliding_correlation(reference: FloatArray, clip: FloatArray) -> FloatArray:
    """The correlation coefficient of `reference` with every window of `clip` of the same length."""
    n = len(reference)
    centred = reference - reference.mean()
    scale = float(np.sqrt(np.sum(centred**2)))
    cross = np.correlate(clip, centred, mode="valid")
    sums = np.concatenate(([0.0], np.cumsum(clip)))
    squares = np.concatenate(([0.0], np.cumsum(clip**2)))
    variance = (squares[n:] - squares[:-n]) - (sums[n:] - sums[:-n]) ** 2 / n
    with np.errstate(divide="ignore", invalid="ignore"):
        coefficients = cross / (scale * np.sqrt(np.maximum(variance, 0.0)))
    return np.where((variance > 0) & (scale > 0), coefficients, -np.inf)


@dataclass(frozen=True)
class CoarseAlignment:
    """Where the commanded motion lies in the clip: camera time equals command time plus `lag_s`.
    `correlation` is the largest coefficient of the smoothed speed profiles, from -1 to 1."""

    lag_s: float
    correlation: float

    @property
    def matches(self) -> bool:
        """Section 7.4: the profile of the clip matches the commanded profile, so the lag places a window."""
        return self.correlation >= COARSE_CORRELATION


def _smoothed(profile: FloatArray, grid_hz: float) -> FloatArray:
    """The moving average of a speed profile over `COARSE_SMOOTHING_S`, with zeros beyond its ends."""
    width = max(1, round(COARSE_SMOOTHING_S * grid_hz))
    return np.convolve(profile, np.full(width, 1.0 / width), mode="same")


def coarse_alignment(
    reference: Motion, ok: Accepted, settings: Preprocessing = DEFAULT_PREPROCESSING
) -> CoarseAlignment | Refusal:
    """Section 7.4: the lag that places the commanded motion in the clip, from the speed profiles.

    The reference is the commanded motion of the program of the run (`features.commanded_motion`),
    for every run. The camera record measures the device record, so the alignment must not depend on
    it: a device record that loses part of the movement would cut the camera interval to the part
    that it holds. The speed profile of the reference inside its motion interval slides over the
    speed profile of the whole clip, and the lag with the largest correlation coefficient is the
    result. The lag is accurate to about one grid step, and `time_alignment` refines it on the motion
    intervals afterwards.

    Both profiles are smoothed with a moving average of `COARSE_SMOOTHING_S` before the correlation.
    The raw profiles of the line programs oscillate at 5 to 11 Hz, and a run off the command
    stretches the oscillation against the commanded one: on the raw profiles, `line-osc11` correlated
    at 0.46 one percent slow and `line-osc8` at 0.39 1.5 percent slow, below `COARSE_CORRELATION`,
    so a run inside the tolerance of section 7.6 lost its camera record. The average over 0.2 s
    removes 5 Hz and above and keeps the loop structure near 1 Hz. In a sweep of synthetic tracks
    0.98 to 1.02 times the command, with and without the lift, the smallest coefficient is 0.56 on
    `line-osc8` (an average over 1.6 of its periods leaves 19 percent of the oscillation), 0.87 on
    `line-osc11`, 0.94 on `line-osc5`, and at least 0.79 on the loops and spiral programs. The fine
    alignment reads the raw profiles as before.

    The alignment refuses when the clip has no grid or does not hold the whole reference interval.
    The caller compares the largest coefficient with `COARSE_CORRELATION` (`CoarseAlignment.matches`).
    The two records of one run correlate above 0.9. A clip of another program correlates below 0.5,
    because its profile has another length and another shape.
    """
    grids = [k for _, k in contact_grids(ok, settings)]
    if not grids:
        return Refusal(RefusalCode.TOO_FEW_VALUES, "The track has no grid.")
    hz = settings.grid_hz
    first, last = float(grids[0].t[0]), float(grids[-1].t[-1])
    clip = _profile_between(grids, first, last, hz)
    profile = _profile(reference, hz)
    if len(clip) < len(profile):
        return Refusal(RefusalCode.TOO_FEW_VALUES, "The clip is shorter than the motion of the reference.")
    coefficients = _sliding_correlation(_smoothed(profile, hz), _smoothed(clip, hz))
    peak = int(np.argmax(coefficients))
    return CoarseAlignment(first + peak / hz - reference.interval.start_s, float(coefficients[peak]))


def motion_window(reference: Motion, found: CoarseAlignment) -> MotionInterval:
    """Where the motion interval rule of section 7.4 applies on a camera record.

    The window is the commanded interval at the lag of `coarse_alignment`, wider by `WINDOW_BEFORE_S`
    before and `WINDOW_AFTER_S` after. Both are shorter than the dwells of 0.5 s and 0.3 s of section
    5.2, so the lowering and the lift stay outside. The executed movement can be 1 percent longer or
    shorter than the command (section 7.6), and the margins hold that difference on every program; a
    run further off the command reaches an edge of the window (`touches_window`). In a sweep of
    synthetic tracks, no program touches an edge within 1.5 percent of the command, the first touch
    is at 2 percent slow on `loops-l-v20` and `spiral-l-v20`, and `loops-l-v20` 1.2 percent slow
    keeps its interval and fails the duration check.
    """
    return MotionInterval(
        reference.interval.start_s + found.lag_s - WINDOW_BEFORE_S,
        reference.interval.end_s + found.lag_s + WINDOW_AFTER_S,
    )


def touches_window(
    motion: Motion, window: MotionInterval, ok: Accepted, settings: Preprocessing = DEFAULT_PREPROCESSING
) -> bool:
    """Whether the motion interval starts at the first grid sample inside the window or ends at the last
    one. The movement then extends beyond the commanded window, and the interval holds only a part of
    it, so the run is a motion failure of section 7.6. The rule compares the interval with the grid
    samples that the rule of section 7.4 searched, so a window edge between two grid samples counts as
    the sample inside it."""
    times = np.concatenate([k.t for _, k in contact_grids(ok, settings)])
    inside = times[(times >= window.start_s) & (times <= window.end_s)]
    return bool(motion.interval.start_s <= inside.min() or motion.interval.end_s >= inside.max())


def camera_record(
    track: Track, reference: Motion | None, settings: Preprocessing = DEFAULT_PREPROCESSING
) -> CameraRecord:
    """The record of a track, with its motion interval inside the window of the commanded motion.

    Without a window, the record is invalid for a camera cause and `misaligned` without one (module
    docstring)."""
    trial = camera_trial(track)
    qc = assess(trial, DEFAULT_QC)
    if not isinstance(qc, Accepted):
        return _without_motion(track, trial, qc.refusals[0], validity(track, None), None, settings)
    found: CoarseAlignment | Refusal
    if reference is None:
        found = Refusal(RefusalCode.NOT_APPLICABLE, "The run has no commanded motion for the alignment.")
    else:
        found = coarse_alignment(reference, qc, settings)
        if isinstance(found, CoarseAlignment) and found.matches:
            window = motion_window(reference, found)
            run = run_features(qc, settings, window=window)
            motion = run.motion if isinstance(run.motion, Motion) else None
            failed = validity(track, motion.interval if motion is not None else None)
            edge = motion is not None and touches_window(motion, window, qc, settings)
            return CameraRecord(track, run, failed, edge, found)
    whole = validity(track, whole_clip(track))
    if isinstance(found, Refusal):
        return _without_motion(track, trial, found, (CameraRule.ALIGNMENT, *whole), None, settings)
    mismatch = Refusal(
        RefusalCode.UNDEFINED,
        f"The speed profiles of the clip and the command correlate at {found.correlation:.2f} at best,"
        f" below {COARSE_CORRELATION:g}.",
    )
    failed = (CameraRule.ALIGNMENT, *whole) if whole else ()
    return _without_motion(track, trial, mismatch, failed, found, settings)


def _without_motion(
    track: Track,
    trial: Trial,
    refusal: Refusal,
    failed: tuple[CameraRule, ...],
    alignment: CoarseAlignment | None,
    settings: Preprocessing,
) -> CameraRecord:
    cells = {**feature_cells(refusal, settings), **sampling_cells(trial, DEFAULT_QC.max_gap_s)}
    return CameraRecord(track, RunFeatures(refusal, cells), tuple(sorted(failed)), alignment=alignment)


def _profile_between(grids: Sequence[Kinematics], start_s: float, end_s: float, grid_hz: float) -> FloatArray:
    """The grid speed of a record on one grid from `start_s` to `end_s`, zero where the record has a gap."""
    count = int(np.floor((end_s - start_s) * grid_hz)) + 1
    t = start_s + np.arange(count) / grid_hz
    profile = np.zeros(count)
    for g in grids:
        inside = (t >= g.t[0]) & (t <= g.t[-1])
        profile[inside] = np.interp(t[inside], g.t, g.speed)
    return profile


def _profile(motion: Motion, grid_hz: float) -> FloatArray:
    """The grid speed of a record on one grid from its motion start, zero where the record has a gap."""
    return _profile_between(motion.grids, motion.interval.start_s, motion.interval.end_s, grid_hz)


def _pearson_by_lag(a: FloatArray, b: FloatArray, max_lag: int) -> tuple[IntArray, FloatArray]:
    """The correlation coefficient of `a[n]` with `b[n + lag]` over their overlap, for each lag."""
    lags = np.arange(-max_lag, max_lag + 1, dtype=np.int64)
    coefficients = np.full(len(lags), -np.inf)
    for i, lag in enumerate(lags):
        start, stop = max(0, -lag), min(len(a), len(b) - lag)
        if stop - start < 3:
            continue
        x, y = a[start:stop], b[start + lag : stop + lag]
        x, y = x - x.mean(), y - y.mean()
        scale = float(np.sqrt(np.sum(x**2) * np.sum(y**2)))
        if scale > 0:
            coefficients[i] = float(np.sum(x * y) / scale)
    return lags, coefficients


def time_alignment(
    device: Motion, camera: Motion, settings: Preprocessing = DEFAULT_PREPROCESSING, *, max_lag_s: float = 0.5
) -> TimeAlignment | Refusal:
    """Section 7.4: the lag from the cross-correlation of the grid speed profiles over the motion intervals.

    Each profile starts at its motion start, so the lag between them is small. The correlation at a lag
    is the correlation coefficient over the overlap of the two profiles, within `max_lag_s`. A sum over
    zero-padded profiles would lose one ramp end per sample of lag, which pulls the peak toward zero.
    The three largest values of a smooth peak are the peak and its two neighbours. The parabola goes
    through those three, so a second peak elsewhere cannot enter the fit. A peak at the edge of the
    lag window has no neighbour on one side and keeps its grid lag.
    """
    a = _profile(device, settings.grid_hz)
    b = _profile(camera, settings.grid_hz)
    lags, c = _pearson_by_lag(a, b, round(max_lag_s * settings.grid_hz))
    peak = int(np.argmax(c))
    if not np.isfinite(c[peak]):
        return Refusal(RefusalCode.TOO_FEW_VALUES, "The speed profiles do not overlap or have no variation.")
    offset = 0.0
    if 0 < peak < len(c) - 1 and np.isfinite(c[peak - 1]) and np.isfinite(c[peak + 1]):
        left, middle, right = c[peak - 1], c[peak], c[peak + 1]
        curvature = left - 2 * middle + right
        if curvature < 0:
            offset = float(0.5 * (left - right) / curvature)
    lag = (float(lags[peak]) + offset) / settings.grid_hz
    return TimeAlignment(camera.interval.start_s - device.interval.start_s + lag, float(c[peak]))


def rigid_fit(source: FloatArray, target: FloatArray) -> tuple[FloatArray, FloatArray]:
    """The least-squares rotation and shift with `target ~ source @ rotation + shift`, without reflection."""
    source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
    u, _, vt = np.linalg.svd((source - source_mean).T @ (target - target_mean))
    rotation = u @ np.diag([1.0, float(np.sign(np.linalg.det(u @ vt)))]) @ vt
    return rotation, target_mean - source_mean @ rotation


def _degrees(rotation: FloatArray) -> float:
    """The counter-clockwise angle of a row-vector rotation."""
    return float(np.degrees(np.arctan2(rotation[0, 1], rotation[0, 0])))


def spatial_accuracy(device: Motion, camera: Motion, alignment: TimeAlignment) -> SpatialAccuracy | Refusal:
    """Section 8.2: the device grid samples at their camera times against the camera grid, interpolated."""
    device_points: list[FloatArray] = []
    camera_points: list[FloatArray] = []
    for g in device.grids:
        t = g.t + alignment.lag_s
        for c in camera.grids:
            inside = (t >= c.t[0]) & (t <= c.t[-1])
            if inside.any():
                device_points.append(g.xy[inside])
                camera_points.append(
                    np.column_stack([np.interp(t[inside], c.t, c.xy[:, k]) for k in range(2)])
                )
    if sum(len(p) for p in device_points) < 3:
        return Refusal(RefusalCode.TOO_FEW_VALUES, "Fewer than 3 grid times match.")
    source, target = np.concatenate(device_points), np.concatenate(camera_points)
    rotation, shift = rigid_fit(source, target)
    distance = np.linalg.norm(source @ rotation + shift - target, axis=1)
    return SpatialAccuracy(
        _degrees(rotation),
        float(np.sqrt(np.mean(distance**2))),
        float(np.percentile(distance, 95)),
        len(distance),
    )


def nearest_on_path(points: FloatArray, vertices: FloatArray) -> FloatArray:
    """The nearest point of a polyline to each point. Exact: it checks every edge that can be nearer.

    Example. The polyline has the vertices (0, 0), (10, 0), and (10, 10), so its edges are 0 and 1,
    and the longest edge is 10. The point (4, 1) has its nearest vertex (0, 0) at 4.12. Its ball of
    radius 4.12 + 10 holds all three vertices, because vertex 2 is at 10.82. Its candidate edges are
    the ones that start or end at a vertex of the ball: edge 0 from the vertices 0 and 1, and edge 1
    from the vertices 1 and 2. The projections are (4, 0) on edge 0 and (10, 1) on edge 1, and
    (4, 0) at 1.0 is the nearest. A vertex outside the ball cannot start or end a nearer edge,
    because every point of such an edge is farther than the ball radius minus the edge length.
    """
    deltas = np.diff(vertices, axis=0)
    lengths = np.linalg.norm(deltas, axis=1)
    last_edge = len(deltas) - 1
    tree = KDTree(vertices)
    bound = np.asarray(tree.query(points)[0])
    # A nearer point lies on an edge with an endpoint within the nearest-vertex distance plus one edge.
    groups = tree.query_ball_point(points, bound + lengths.max() + 1e-9, return_sorted=False)
    counts = np.fromiter((len(g) for g in groups), dtype=np.int64, count=len(points))
    near = np.fromiter(itertools.chain.from_iterable(groups), dtype=np.int64, count=int(counts.sum()))
    # The candidates of a point are the edges that end or start at a vertex of its ball. They stay
    # grouped by point, so the nearest candidate of each point is a minimum over its group.
    candidates = np.clip(np.column_stack((near - 1, near)).ravel(), 0, last_edge)
    rows: IntArray = np.repeat(np.arange(len(points)), 2 * counts)
    starts = np.concatenate(([0], np.cumsum(2 * counts)[:-1]))
    origins = points[rows]
    ends, steps = vertices[candidates], deltas[candidates]
    divisors = np.where(lengths[candidates] > 0, lengths[candidates] ** 2, 1.0)
    fractions = np.clip(np.sum((origins - ends) * steps, axis=1) / divisors, 0, 1)
    projected = ends + steps * fractions[:, None]
    squared = np.sum((origins - projected) ** 2, axis=1)
    smallest = np.minimum.reduceat(squared, starts)
    first = np.flatnonzero(squared == np.repeat(smallest, 2 * counts))
    return projected[first[np.searchsorted(rows[first], np.arange(len(points)))]]


def _rms(aligned: FloatArray, target: FloatArray) -> float:
    return float(np.sqrt(np.mean(np.sum((aligned - target) ** 2, axis=1))))


def path_fit(
    record: Motion, command: CommandedPath, commanded: Motion, settings: Preprocessing = DEFAULT_PREPROCESSING
) -> PathFit | Refusal:
    """The samples of a record onto the commanded path by a least-squares translation and rotation.

    Correspondence. The commanded path is in the stimulus frame with y down; the record has y up. The
    path is mirrored in y first, because a rotation cannot undo a reflection. The record is aligned to
    the commanded motion in time with `time_alignment`, so a sample corresponds to the commanded position
    at its commanded time. The first fit uses these time pairs. The motion system may be slow or fast by
    up to 1 percent (section 7.6), which moves a time pair along the path by millimetres, and a fit on time
    pairs alone would absorb that as a shift. The fit therefore continues as an iterative closest point fit:
    each sample pairs with its nearest point on the mirrored path, the rigid fit is repeated, and it stops
    when the root mean square distance falls by less than 1e-6 mm. The time-paired start keeps the fit
    from a wrong turn of the spiral or a neighbouring loop, and it sets the position along a straight
    line, which the nearest distances cannot.
    """
    samples = _all_samples(record)
    if len(samples.t) < 3:
        return Refusal(RefusalCode.TOO_FEW_VALUES, "The path fit needs 3 samples in the motion interval.")
    alignment = time_alignment(record, commanded, settings)
    if isinstance(alignment, Refusal):
        return alignment
    vertices = command.xy * _MIRROR
    target = command.position(samples.t + alignment.lag_s) * _MIRROR
    rotation, shift = rigid_fit(samples.xy, target)
    aligned = samples.xy @ rotation + shift
    target = nearest_on_path(aligned, vertices)
    rms = _rms(aligned, target)
    iterations = 0
    while iterations < _ICP_MAX_ITERATIONS:
        rotation, shift = rigid_fit(samples.xy, target)
        aligned = samples.xy @ rotation + shift
        target = nearest_on_path(aligned, vertices)
        previous, rms = rms, _rms(aligned, target)
        iterations += 1
        if previous - rms < _ICP_TOLERANCE_MM:
            break
    distance = np.linalg.norm(aligned - target, axis=1)
    return PathFit(
        _degrees(rotation), (float(shift[0]), float(shift[1])), rms, float(distance.max()), iterations
    )


def _all_samples(motion: Motion) -> Points:
    return Points(
        np.concatenate([p.t for p in motion.samples]), np.concatenate([p.xy for p in motion.samples])
    )


def trace_error(
    device: Motion, move_list: MoveList, settings: Preprocessing = DEFAULT_PREPROCESSING
) -> PathFit | Refusal:
    """Section 8.2: the device samples onto the commanded path. `rms_mm` is the trace error."""
    commanded = commanded_motion(move_list, settings)
    if isinstance(commanded, Refusal):
        return commanded
    return path_fit(device, move_list.path(), commanded, settings)


def executed_movement(
    record: CameraRecord, move_list: MoveList, settings: Preprocessing = DEFAULT_PREPROCESSING
) -> ExecutedMovement | Refusal:
    """Section 7.6: the camera samples onto the commanded path, and the motion duration of the command."""
    if not isinstance(record.run.motion, Motion):
        return record.run.motion
    commanded = commanded_motion(move_list, settings)
    if isinstance(commanded, Refusal):
        return commanded
    motion = record.run.motion
    fit = path_fit(motion, move_list.path(), commanded, settings)
    if isinstance(fit, Refusal):
        return fit
    return ExecutedMovement(
        fit, motion.interval.duration_s, commanded.interval.duration_s, record.window_edge
    )
