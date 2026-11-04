"""The camera record of one run (protocol sections 7.3 to 7.7, 8.2, and 9.2).

The input is a `Track` of `scriptkin.bench.track`. `camera_record` makes the frames with a marker into one
contact stroke in millimetres, assesses it, finds the motion interval, computes the features with the code
of a device run, and applies the validity rules inside the motion interval.

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
    feature_cells,
    run_features,
    sampling_cells,
)
from scriptkin.bench.moves import CommandedPath, MoveList
from scriptkin.bench.track import BoolArray, FrameTimes, Track
from scriptkin.quality import DEFAULT_QC, Accepted, Refusal, RefusalCode, assess
from scriptkin.recording import Device, FloatArray, IntArray, Stroke, TaskRef, Trial
from scriptkin.signal import DEFAULT_PREPROCESSING, Preprocessing

MARKER_MISSING_SHARE = 0.02
FRAME_INTERVAL_TOLERANCE = 0.01
FRAME_INTERVAL_RESOLUTION_S = 1e-6
LED_RATE_TOLERANCE = 0.001
MAX_PATH_DISTANCE_MM = 0.2
MAX_DURATION_ERROR = 0.01
_ICP_MAX_ITERATIONS = 100
_ICP_TOLERANCE_MM = 1e-6
_MIRROR = np.array([1.0, -1.0])


class CameraRule(StrEnum):
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

    @property
    def valid(self) -> bool:
        return not self.failed and isinstance(self.run.motion, Motion)


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


@dataclass(frozen=True)
class PathFit:
    """A record aligned to the commanded path by a translation and a rotation, with its nearest distances."""

    rotation_deg: float
    shift_mm: tuple[float, float]
    rms_mm: float
    max_mm: float
    iterations: int


@dataclass(frozen=True)
class ExecutedMovement:
    """Section 7.6 for one run with a valid camera record."""

    fit: PathFit
    motion_s: float
    commanded_s: float

    @property
    def failure(self) -> bool:
        return (
            self.fit.max_mm > MAX_PATH_DISTANCE_MM
            or abs(self.motion_s - self.commanded_s) > MAX_DURATION_ERROR * self.commanded_s
        )


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
    them together, must hold the frames of the measured rate within one frame plus 0.1 percent.
    Without two full periods the rule cannot be applied, and the record is invalid.
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


def camera_record(track: Track, settings: Preprocessing = DEFAULT_PREPROCESSING) -> CameraRecord:
    trial = camera_trial(track)
    qc = assess(trial, DEFAULT_QC)
    if isinstance(qc, Accepted):
        run = run_features(qc, settings)
    else:
        refusal = qc.refusals[0]
        run = RunFeatures(
            refusal, {**feature_cells(refusal, settings), **sampling_cells(trial, DEFAULT_QC.max_gap_s)}
        )
    interval = run.motion.interval if isinstance(run.motion, Motion) else None
    return CameraRecord(track, run, validity(track, interval))


def _profile(motion: Motion, grid_hz: float) -> FloatArray:
    """The grid speed of a record on one grid from its motion start, zero where the record has a gap."""
    count = int(np.floor(motion.interval.duration_s * grid_hz)) + 1
    t = motion.interval.start_s + np.arange(count) / grid_hz
    profile = np.zeros(count)
    for g in motion.grids:
        inside = (t >= g.t[0]) & (t <= g.t[-1])
        profile[inside] = np.interp(t[inside], g.t, g.speed)
    return profile


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


def _nearest_on_path(points: FloatArray, vertices: FloatArray) -> FloatArray:
    """The nearest point of a polyline to each point. Exact: it checks every edge that can be nearer."""
    deltas = np.diff(vertices, axis=0)
    lengths = np.linalg.norm(deltas, axis=1)
    last_edge = len(deltas) - 1
    tree = KDTree(vertices)
    bound = np.asarray(tree.query(points)[0])
    # A nearer point lies on an edge with an endpoint within the nearest-vertex distance plus one edge.
    groups = tree.query_ball_point(points, bound + lengths.max() + 1e-9)
    nearest = np.empty_like(points)
    for block in np.array_split(np.arange(len(points)), max(1, (len(points) + 255) // 256)):
        edges = [
            np.unique(np.clip(np.concatenate((v - 1, v)), 0, last_edge))
            for v in (np.asarray(groups[i], dtype=np.int64) for i in block)
        ]
        rows: IntArray = np.repeat(block, [len(e) for e in edges])
        candidates = np.concatenate(edges)
        offsets = points[rows] - vertices[candidates]
        divisors = np.where(lengths[candidates] > 0, lengths[candidates] ** 2, 1.0)
        fractions = np.clip(np.sum(offsets * deltas[candidates], axis=1) / divisors, 0, 1)
        projected = vertices[candidates] + deltas[candidates] * fractions[:, None]
        squared = np.sum((points[rows] - projected) ** 2, axis=1)
        first = np.lexsort((squared, rows))[np.searchsorted(rows, block)]
        nearest[block] = projected[first]
    return nearest


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
    target = _nearest_on_path(aligned, vertices)
    rms = _rms(aligned, target)
    iterations = 0
    while iterations < _ICP_MAX_ITERATIONS:
        rotation, shift = rigid_fit(samples.xy, target)
        aligned = samples.xy @ rotation + shift
        target = _nearest_on_path(aligned, vertices)
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
    return ExecutedMovement(fit, motion.interval.duration_s, commanded.interval.duration_s)
