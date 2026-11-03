"""Device qualification: what a qualification session shows about one iPad and Pencil (DESIGN section 16).

The report describes the recording. Whether a device is qualified for comparison is a decision of
the analysis plan, not of this module.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from scriptkin.quality import Refusal, RefusalCode
from scriptkin.recording import Channel, FloatArray, PenState, Recording, ScaleSource, Stroke, TrialOutcome

GAP_S = 0.030
HOLD_TASK = "qual-hold"
LINE_TASK = "qual-line"


@dataclass(frozen=True)
class Intervals:
    """Sample intervals inside contact strokes. Percentiles use linear interpolation."""

    count: int
    median_s: float
    p5_s: float
    p95_s: float
    gap_share: float


@dataclass(frozen=True)
class QualificationReport:
    session_id: str
    device_model: str
    device_asset: str
    intervals: Intervals | Refusal
    hold_jitter_mm: float | Refusal
    line_deviation_mm: float | Refusal
    scale: ScaleSource
    planned_channels: frozenset[Channel]
    observed_channels: frozenset[Channel]


def _intervals(strokes: Sequence[Stroke]) -> Intervals | Refusal:
    dt = np.concatenate([np.diff(s.t) for s in strokes]) if strokes else np.empty(0)
    dt = dt[np.isfinite(dt)]
    if not len(dt):
        return Refusal(RefusalCode.TOO_FEW_VALUES, "No contact stroke has two samples.")
    p5, median, p95 = np.percentile(dt, [5, 50, 95])
    return Intervals(len(dt), float(median), float(p5), float(p95), float(np.mean(dt > GAP_S)))


def _hold_residual(xy: FloatArray) -> FloatArray:
    """Distance of each sample from the mean position of its stroke."""
    return np.linalg.norm(xy - xy.mean(axis=0), axis=1)


def _line_residual(xy: FloatArray) -> FloatArray:
    """Orthogonal distance of each sample from the total-least-squares line of its stroke."""
    centred = xy - xy.mean(axis=0)
    normal = np.linalg.svd(centred, full_matrices=False)[2][-1]
    return centred @ normal


def _pooled_rms(
    recording: Recording, task: str, residual: Callable[[FloatArray], FloatArray]
) -> float | Refusal:
    """Root mean square of per-stroke residuals over the completed trials of one task, in millimetres."""
    if recording.device.spatial_unit != "mm":
        return Refusal(RefusalCode.UNKNOWN_SCALE, "A physical position scale is required.")
    finished = (TrialOutcome.COMPLETED, TrialOutcome.TIME_LIMIT)
    strokes = [
        s.xy
        for t in recording.trials
        if t.task.id == task and t.outcome in finished
        for s in t.strokes
        if s.state == PenState.CONTACT and len(s.t) >= 2 and np.isfinite(s.xy).all()
    ]
    if not strokes:
        return Refusal(RefusalCode.TOO_FEW_VALUES, f"No completed {task} trial has a contact stroke.")
    squares = np.concatenate([residual(xy) ** 2 for xy in strokes])
    return float(np.sqrt(np.mean(squares)))


def qualify(recording: Recording) -> QualificationReport:
    """Report sample timing, still-hold jitter, straight-line deviation, scale source, and channels."""
    context = recording.context
    if context is None or context.purpose != "qualification":
        raise ValueError("A qualification report needs a session whose purpose is qualification.")
    contact = [s for t in recording.trials for s in t.strokes if s.state == PenState.CONTACT]
    return QualificationReport(
        recording.session_id,
        recording.device.model,
        context.device_asset,
        _intervals(contact),
        _pooled_rms(recording, HOLD_TASK, _hold_residual),
        _pooled_rms(recording, LINE_TASK, _line_residual),
        context.scale,
        recording.device.planned_channels,
        recording.device.observed_channels,
    )
