"""Quality checks are the only entry point to signal segments."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from scriptkin.recording import Channel, FloatArray, IntArray, PenState, Stroke, Trial, TrialOutcome


class RefusalCode(StrEnum):
    TOO_FEW_VALUES = "too_few_values"
    LOW_RATE = "low_rate"
    DUPLICATE_TIMES = "duplicate_times"
    NONMONOTONIC_TIMES = "nonmonotonic_times"
    NONFINITE = "nonfinite"
    GAPS = "gaps"
    UNRESOLVED_ESTIMATES = "unresolved_estimates"
    UNMATCHED_CORRECTIONS = "unmatched_corrections"
    FINGER_CONTACTS = "finger_contacts"
    MISSING_CHANNEL = "missing_channel"
    SHORT_SEGMENT = "short_segment"
    INCOMPLETE_TRIAL = "incomplete_trial"
    EXCLUDED = "excluded"
    UNKNOWN_SCALE = "unknown_scale"
    SIMULATED_INPUT = "simulated_input"
    NOT_APPLICABLE = "not_applicable"
    UNDEFINED = "undefined"


@dataclass(frozen=True)
class Refusal:
    code: RefusalCode
    detail: str


@dataclass(frozen=True)
class QcPolicy:
    max_gap_s: float = 0.05
    min_segment_s: float = 0.1
    min_rate_hz: float = 40.0
    reject_unresolved: bool = True

    def __post_init__(self) -> None:
        if not all(np.isfinite(v) and v > 0 for v in (self.max_gap_s, self.min_segment_s, self.min_rate_hz)):
            raise ValueError("QC thresholds must be positive and finite.")


DEFAULT_QC = QcPolicy()

_QC_TOKEN = object()


@dataclass(frozen=True, init=False)
class Segment:
    t: FloatArray
    xy: FloatArray
    channels: dict[Channel, FloatArray]
    state: PenState
    unresolved: IntArray
    stroke_index: int
    boot: str

    def __init__(self, stroke: Stroke, start: int, stop: int, stroke_index: int, *, _token: object):
        if _token is not _QC_TOKEN:
            raise TypeError("Use assess() to build a Segment.")
        for key, value in {
            "t": stroke.t[start:stop],
            "xy": stroke.xy[start:stop],
            "channels": {k: v[start:stop] for k, v in stroke.channels.items()},
            "state": stroke.state,
            "stroke_index": stroke_index,
            "boot": stroke.boot,
            "unresolved": stroke.unresolved[start:stop],
        }.items():
            object.__setattr__(self, key, value)


@dataclass(frozen=True)
class QcReport:
    policy: QcPolicy
    findings: tuple[Refusal, ...]
    segment_count: int


@dataclass(frozen=True, init=False)
class Accepted:
    trial: Trial
    segments: tuple[Segment, ...]
    report: QcReport
    refused_states: dict[PenState, Refusal]

    def __init__(
        self,
        trial: Trial,
        segments: tuple[Segment, ...],
        report: QcReport,
        refused_states: dict[PenState, Refusal],
        *,
        _token: object,
    ):
        if _token is not _QC_TOKEN:
            raise TypeError("Use assess() to build Accepted.")
        object.__setattr__(self, "trial", trial)
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "report", report)
        object.__setattr__(self, "refused_states", refused_states)


@dataclass(frozen=True)
class Rejected:
    refusals: tuple[Refusal, ...]
    report: QcReport


def assess(trial: Trial, policy: QcPolicy = DEFAULT_QC) -> Accepted | Rejected:
    """Split at gaps and invalid positions. Never join strokes or boot clocks.

    Bad time order or low rate in a contact stroke rejects the trial. In an in-air stroke,
    the same defects refuse only the in-air features. Unresolved estimates reject the trial.
    Other findings remain in the report. Missing channels refuse their features.
    """
    findings: list[Refusal] = []
    fatal: list[Refusal] = []
    segments: list[Segment] = []
    refused_states: dict[PenState, Refusal] = {}

    def note(code: RefusalCode, detail: str, *, reject: bool = False) -> Refusal:
        r = Refusal(code, detail)
        findings.append(r)
        if reject:
            fatal.append(r)
        return r

    if trial.outcome not in (TrialOutcome.COMPLETED, TrialOutcome.TIME_LIMIT):
        note(RefusalCode.INCOMPLETE_TRIAL, trial.outcome, reject=True)
    if "exclude" in trial.marks:
        note(RefusalCode.EXCLUDED, "The operator excluded the trial.", reject=True)
    if trial.finger_contacts:
        note(RefusalCode.FINGER_CONTACTS, str(trial.finger_contacts))
    if trial.unmatched_corrections:
        note(RefusalCode.UNMATCHED_CORRECTIONS, str(trial.unmatched_corrections))
    for channel in sorted(trial.device.planned_channels - trial.device.observed_channels):
        note(RefusalCode.MISSING_CHANNEL, f"The planned {channel} channel is absent.")
    for si, s in enumerate(trial.strokes):
        if not len(s.t):
            continue
        dt = np.diff(s.t)
        positive = dt[np.isfinite(dt) & (dt > 0) & (dt <= policy.max_gap_s)]
        # If every interval is a gap, use the positive intervals to detect a low rate.
        rate_dt = positive if len(positive) else dt[np.isfinite(dt) & (dt > 0)]
        low_rate = len(rate_dt) > 0 and 1 / float(np.median(rate_dt)) < policy.min_rate_hz
        for code, failed, detail in (
            (RefusalCode.DUPLICATE_TIMES, bool(np.any(dt == 0)), "has duplicate times"),
            (RefusalCode.NONMONOTONIC_TIMES, bool(np.any(dt < 0)), "has decreasing times"),
            (RefusalCode.LOW_RATE, low_rate, "is below min_rate_hz"),
        ):
            if failed:
                r = note(code, f"Stroke {si} {detail}.", reject=s.state == PenState.CONTACT)
                refused_states.setdefault(s.state, r)
        if np.any(s.unresolved):
            note(
                RefusalCode.UNRESOLVED_ESTIMATES,
                f"Stroke {si} has pending estimates.",
                reject=policy.reject_unresolved,
            )
        finite = np.isfinite(s.t) & np.isfinite(s.xy).all(axis=1)
        if not finite.all():
            note(RefusalCode.NONFINITE, f"Stroke {si} has invalid positions or times.")
        if np.any(dt > policy.max_gap_s):
            note(RefusalCode.GAPS, f"Stroke {si} has gaps.")
        start = 0
        for end in range(1, len(s.t) + 1):
            split = (
                end == len(s.t)
                or not finite[end]
                or not finite[end - 1]
                or not (0 < dt[end - 1] <= policy.max_gap_s)
            )
            if not split:
                continue
            if finite[start] and end - start >= 2 and s.t[end - 1] - s.t[start] >= policy.min_segment_s:
                segments.append(Segment(s, start, end, si, _token=_QC_TOKEN))
            else:
                note(RefusalCode.SHORT_SEGMENT, f"Stroke {si}, samples {start}:{end} is too short.")
            start = end
    if not segments:
        note(RefusalCode.TOO_FEW_VALUES, "No segment meets the QC requirements.", reject=True)
    report = QcReport(policy, tuple(findings), len(segments))
    if fatal:
        return Rejected(tuple(fatal), report)
    return Accepted(trial, tuple(segments), report, refused_states, _token=_QC_TOKEN)
