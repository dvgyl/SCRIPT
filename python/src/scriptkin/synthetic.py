"""Synthetic cohorts with known truth (docs/DESIGN.md section 8).

Each participant has latent movement traits and a latent depression score. The score drives PHQ-9 and
GAD-7 item responses through a graded response model, so labels come from `labels.labels` on real
`response` lines. A planted effect shifts chosen traits by the score. Strokes follow each stimulus
polyline with a minimum-jerk speed profile, tremor at right angles to the path, micro-movement speed
bumps with Gamma amplitudes, and pen lifts at pauses. A device model samples the strokes. Every session
is written with `contract.JournalBuilder` and read back with the export reader, so a cohort passes
`read_journal`, `assess`, `extract`, and `labels`. The truth stays outside the recordings.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import numpy as np
from scipy.optimize import root_scalar
from scipy.signal import lfilter
from scipy.special import expit

from scriptkin import battery
from scriptkin.contract import FORCE, PENCIL_PRO_CHANNELS, JournalBuilder, manifest_bytes
from scriptkin.evaluate.metrics import auc
from scriptkin.formats.journal import recording, replay
from scriptkin.labels import Label, labels
from scriptkin.recording import FloatArray, Recording

Rng = np.random.Generator

MEASURED_TASKS = ("spiral-trace", "loops", "pentagons", "house", "clock", "sentence")
MAX_FORCE = 4.166666666666667
ORIGIN_POINTS = (147.5, 200.0)


@dataclass(frozen=True)
class Traits:
    """Latent movement traits of one participant. Every trait is positive."""

    speed_mm_s: float
    tremor_mm: float
    tremor_hz: float
    pause_rate_hz: float
    pressure: float
    pressure_cv: float
    spike_shape: float
    spike_scale: float


TRAITS = tuple(f.name for f in fields(Traits))

# Population median and standard deviation of the natural log of each trait. `pressure` is a fraction of
# the maximum force. `spike_shape` and `spike_scale` are the Gamma parameters of the micro-movement bump
# amplitudes, relative to the local speed.
_PRIOR: dict[str, tuple[float, float]] = {
    "speed_mm_s": (55.0, 0.25),
    "tremor_mm": (0.06, 0.35),
    "tremor_hz": (7.0, 0.12),
    "pause_rate_hz": (0.25, 0.35),
    "pressure": (0.35, 0.18),
    "pressure_cv": (0.12, 0.25),
    "spike_shape": (2.0, 0.25),
    "spike_scale": (0.12, 0.25),
}


def traits_from_scores(u: Mapping[str, float]) -> Traits:
    """Map standardized log-trait scores to traits."""
    values = {name: median * math.exp(sd * u[name]) for name, (median, sd) in _PRIOR.items()}
    values["pressure"] = min(values["pressure"], 0.95)
    return Traits(**values)


@dataclass(frozen=True)
class DeviceModel:
    """How one iPad and Pencil pair samples a stroke.

    `pressure_gamma` maps a pressure fraction to recorded force as `MAX_FORCE * pressure ** gamma`.
    `estimate_fraction` is the share of samples whose force arrives later in a `correction` line.
    `scale_bias` is the declared millimetres per point divided by the true value. `channels` is the
    planned channel set with every key of the 2.0 header; `channels["hover"]` gives hover lines between
    contacts.
    """

    model: str
    pencil: str
    rate_hz: float
    jitter_s: float
    drop: float
    position_noise_mm: float
    channels: Mapping[str, bool]
    pressure_gamma: float
    estimate_fraction: float
    correction_delay_s: float
    scale_bias: float = 1.0
    ppi: float = 264.0


IPAD_PRO_PENCIL_PRO = DeviceModel(
    "iPad16,3", "pro", 240.0, 0.0002, 0.0, 0.01, PENCIL_PRO_CHANNELS, 1.0, 0.02, 0.03
)
IPAD_PRO_PENCIL_2 = DeviceModel(
    "iPad14,3", "second", 240.0, 0.0003, 0.0005, 0.015, dict(PENCIL_PRO_CHANNELS, roll=False), 1.3, 0.03, 0.04
)
IPAD_AIR_PENCIL_USBC = DeviceModel(
    "iPad14,8",
    "usbC",
    240.0,
    0.0006,
    0.002,
    0.04,
    dict(PENCIL_PRO_CHANNELS, force=False, roll=False, hover=False),
    1.0,
    0.0,
    0.0,
)


@dataclass(frozen=True)
class Site:
    """A study site. `speed` and `pressure` multiply the traits of every session at the site."""

    code: str
    speed: float = 1.0
    pressure: float = 1.0


@dataclass(frozen=True)
class Outcome:
    """The planted truth: target prevalence of PHQ-9 total >= `threshold`, and trait loadings.

    `effect` maps a trait name to its loading on the latent depression score, in standard deviations of
    the log trait. The scenario decides whether the effect applies.
    """

    prevalence: float = 0.3
    effect: Mapping[str, float] = field(default_factory=lambda: dict[str, float]())
    threshold: int = 10

    def __post_init__(self) -> None:
        if not 0 < self.prevalence < 1:
            raise ValueError("The prevalence must be between 0 and 1.")
        unknown = set(self.effect) - set(TRAITS)
        if unknown:
            raise ValueError(f"Unknown traits in the effect: {sorted(unknown)}.")
        if not 1 <= self.threshold <= 27:
            raise ValueError("The PHQ-9 threshold must be between 1 and 27.")


class Scenario(StrEnum):
    NULL = "null"
    SIGNAL = "signal"
    DEVICE_CONFOUNDED = "device_confounded"
    FINGERPRINT = "fingerprint"
    MISSINGNESS = "missingness"


@dataclass(frozen=True)
class _Rules:
    effect: bool
    devices_by_case: bool
    quirk_sd: float
    min_visits: int
    missingness: bool


_SCENARIOS = {
    Scenario.NULL: _Rules(effect=False, devices_by_case=False, quirk_sd=0.0, min_visits=1, missingness=False),
    Scenario.SIGNAL: _Rules(
        effect=True, devices_by_case=False, quirk_sd=0.0, min_visits=1, missingness=False
    ),
    Scenario.DEVICE_CONFOUNDED: _Rules(
        effect=False, devices_by_case=True, quirk_sd=0.0, min_visits=1, missingness=False
    ),
    Scenario.FINGERPRINT: _Rules(
        effect=False, devices_by_case=False, quirk_sd=1.5, min_visits=2, missingness=False
    ),
    Scenario.MISSINGNESS: _Rules(
        effect=True, devices_by_case=False, quirk_sd=0.0, min_visits=1, missingness=True
    ),
}

# Graded response model: P(item >= k) = expit(a * (theta - b[k-1])) for k = 1, 2, 3.
PHQ9_ITEMS = (
    (2.2, (0.3, 1.3, 2.0)),
    (2.4, (0.4, 1.4, 2.1)),
    (1.6, (0.0, 1.0, 1.8)),
    (1.9, (-0.2, 0.9, 1.7)),
    (1.6, (0.5, 1.5, 2.2)),
    (2.0, (0.6, 1.6, 2.3)),
    (1.8, (0.5, 1.5, 2.3)),
    (1.7, (1.2, 2.1, 2.8)),
    (2.3, (1.8, 2.6, 3.2)),
)
GAD7_ITEMS = (
    (2.0, (0.2, 1.2, 2.0)),
    (2.1, (0.3, 1.3, 2.1)),
    (1.9, (0.1, 1.1, 1.9)),
    (1.8, (0.2, 1.2, 2.0)),
    (1.6, (0.6, 1.6, 2.4)),
    (1.7, (0.1, 1.1, 1.9)),
    (1.8, (0.7, 1.7, 2.5)),
)
ItemModel = tuple[tuple[float, tuple[float, float, float]], ...]


def _category_probabilities(theta: FloatArray, a: float, b: tuple[float, float, float]) -> FloatArray:
    at_least = expit(a * (theta[:, None] - np.asarray(b)[None, :]))
    edges = np.column_stack((np.ones(len(theta)), at_least, np.zeros(len(theta))))
    return edges[:, :-1] - edges[:, 1:]


def case_probability(latent_mean: float, threshold: int, items: ItemModel = PHQ9_ITEMS) -> float:
    """P(sum of items >= threshold) when theta ~ Normal(latent_mean, 1), by Gauss-Hermite quadrature."""
    nodes, weights = np.polynomial.hermite_e.hermegauss(80)
    theta = latent_mean + nodes
    total = np.ones((len(theta), 1))
    for a, b in items:
        categories = _category_probabilities(theta, a, b)
        grown = np.zeros((len(theta), total.shape[1] + 3))
        for k in range(4):
            grown[:, k : k + total.shape[1]] += total * categories[:, k : k + 1]
        total = grown
    return float(weights @ total[:, threshold:].sum(axis=1) / weights.sum())


def latent_mean(prevalence: float, threshold: int) -> float:
    """The latent mean that gives the target PHQ-9 prevalence of a total >= threshold."""

    def gap(mean: float) -> float:
        return case_probability(mean, threshold) - prevalence

    return float(root_scalar(gap, bracket=(-8.0, 8.0), method="brentq", xtol=1e-10).root)


def _answer(theta: float, items: ItemModel, rng: Rng) -> list[int]:
    draws = rng.random(len(items))
    return [
        int(sum(draw < expit(a * (theta - bk)) for bk in b))
        for (a, b), draw in zip(items, draws, strict=True)
    ]


@dataclass(frozen=True)
class SessionTruth:
    session_id: str
    export: str
    visit: int
    device: str
    site: str
    opened_at: str
    phq9: tuple[int | None, ...]
    declined: tuple[str, ...]
    trials: int


@dataclass(frozen=True)
class ParticipantTruth:
    """`case` is None when the first-visit PHQ-9 has a skipped item, because the label then refuses."""

    participant: str
    depression: float
    scores: Mapping[str, float]
    traits: Traits
    risk: float
    case: bool | None
    site: str
    age_band: str
    hand: str
    sessions: tuple[SessionTruth, ...]


@dataclass(frozen=True)
class Truth:
    scenario: Scenario
    outcome: Outcome
    applied_effect: Mapping[str, float]
    latent_mean: float
    participants: tuple[ParticipantTruth, ...]


@dataclass(frozen=True)
class Journal:
    export: str
    session_id: str
    data: bytes


@dataclass(frozen=True)
class Cohort:
    journals: tuple[Journal, ...]
    recordings: tuple[Recording, ...]
    truth: Truth
    oracle_auc: float

    @property
    def labels(self) -> tuple[Label, ...]:
        """Questionnaire labels scored from the `response` lines of every recording."""
        return tuple(label for r in self.recordings for label in labels(r))

    def write(self, folder: str | Path) -> list[Path]:
        """Write every session as a plaintext export folder and return the folders."""
        written: list[Path] = []
        for journal in self.journals:
            target = Path(folder) / journal.export
            target.mkdir(parents=True, exist_ok=True)
            (target / "journal.jsonl").write_bytes(journal.data)
            (target / "manifest.json").write_bytes(manifest_bytes(journal.session_id, journal.data))
            written.append(target)
        return written


def _polyline(points: FloatArray) -> FloatArray:
    keep = np.concatenate(([True], np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-9))
    return points[keep]


def _curve(
    fn: Callable[[FloatArray], tuple[FloatArray, FloatArray]], t0: float, t1: float, n: int
) -> FloatArray:
    t = np.linspace(t0, t1, n)
    x, y = fn(t)
    return _polyline(np.column_stack((x, y)))


def _clock() -> list[FloatArray]:
    cx = cy = 35.0

    def ring(t: FloatArray) -> tuple[FloatArray, FloatArray]:
        return cx + 30 * np.cos(t), cy - 30 * np.sin(t)

    paths = [_curve(ring, 0, 2 * math.pi, 400)]
    for k in range(12):
        angle = math.pi / 2 - k * math.pi / 6
        paths.append(
            np.array(
                [
                    [cx + 26 * math.cos(angle), cy - 26 * math.sin(angle)],
                    [cx + 30 * math.cos(angle), cy - 30 * math.sin(angle)],
                ]
            )
        )
    for angle, length in ((math.pi / 2 + math.pi / 6, 15.0), (math.pi / 6, 22.0)):
        paths.append(np.array([[cx, cy], [cx + length * math.cos(angle), cy - length * math.sin(angle)]]))
    return paths


def _sentence() -> list[FloatArray]:
    paths: list[FloatArray] = []
    x = 2.0
    for letters in (3, 5, 5, 3, 5, 4, 3, 4, 3):
        start = x

        def word(t: FloatArray, start: float = start) -> tuple[FloatArray, FloatArray]:
            return start + 1.0 + 2.5 * t / (2 * math.pi) + 1.0 * np.sin(t), 9.0 - 4.0 * (1 - np.cos(t)) / 2

        paths.append(_curve(word, 0.0, 2 * math.pi * letters, 60 * letters))
        x += 2.5 * letters + 4.0
    return paths


def _geometry(task: str) -> tuple[list[FloatArray], tuple[float, float], bool]:
    """The polylines a participant draws in frame millimetres (y down), the frame, and whether to trace."""
    if task == "clock":
        return _clock(), (70.0, 70.0), False
    if task == "sentence":
        return _sentence(), (110.0, 12.0), False
    spec = battery.task_spec(task)
    stimulus = spec["stimulus"]
    paths = [_polyline(np.asarray(p, dtype=float)) for p in stimulus["paths"]]
    return paths, (float(stimulus["frameMM"][0]), float(stimulus["frameMM"][1])), spec["mode"] == "trace"


def _bell(tau: FloatArray) -> FloatArray:
    inside = (tau > 0) & (tau < 1)
    return np.where(inside, 30 * tau**2 * (1 - tau) ** 2, 0.0)


def _smooth_noise(n: int, rate_hz: float, rng: Rng, tau_s: float = 0.08) -> FloatArray:
    rho = math.exp(-1 / (rate_hz * tau_s))
    e = rng.standard_normal(n)
    e[0] /= math.sqrt(1 - rho**2)
    return np.asarray(lfilter([math.sqrt(1 - rho**2)], [1, -rho], e), dtype=float)


@dataclass(frozen=True)
class _Session:
    """Traits after site, age, and session effects; the values one session draws with."""

    traits: Traits
    device: DeviceModel
    mm_per_point: float


@dataclass
class _Pen:
    """Writes one trial's contacts and hover lines through the builder."""

    b: JournalBuilder
    s: _Session
    rng: Rng
    pending: list[tuple[float, int, int, float]] = field(default_factory=lambda: [])

    def points(self, xy_mm: FloatArray) -> FloatArray:
        true_mm_per_point = self.s.mm_per_point / self.s.device.scale_bias
        return np.asarray(ORIGIN_POINTS) + xy_mm / true_mm_per_point

    def flush(self, until: float) -> None:
        while self.pending and self.pending[0][0] <= until:
            arrived, contact, update, force = self.pending.pop(0)
            self.b.correction(contact, update, round(arrived, 6), 0, force=force)

    def hover(self, start_mm: FloatArray, end_mm: FloatArray, t0: float, t1: float) -> None:
        if not self.s.device.channels["hover"] or t1 - t0 < 0.05:
            return
        times = np.arange(t0 + 0.01, t1 - 0.01, 1 / 120.0)
        frac = (times - times[0]) / max(times[-1] - times[0], 1e-9)
        xy = self.points(start_mm[None, :] + (end_mm - start_mm)[None, :] * frac[:, None])
        z = 0.1 + 0.3 * np.sin(np.pi * frac)
        for t, (x, y), height in zip(times, xy, z, strict=True):
            self.b.hover(round(float(t), 6), round(float(x), 3), round(float(y), 3), round(float(height), 4))

    def contact(self, path: FloatArray, arc: FloatArray, s_a: float, s_b: float, t0: float) -> float:
        """Draw the path from arc length s_a to s_b, starting at t0. Return the lift time."""
        traits, device, rng = self.s.traits, self.s.device, self.rng
        length = s_b - s_a
        duration = max(length / (traits.speed_mm_s * math.exp(0.08 * rng.standard_normal())), 0.2)
        width = min(0.16, duration)
        count = max(1, round(2 * (duration - width) / width) + 1)
        centres = np.linspace(width / 2, duration - width / 2, count)
        amplitudes = 1 + rng.gamma(traits.spike_shape, traits.spike_scale, count)
        fine = np.linspace(0.0, duration, int(duration * 1000) + 2)
        speed = sum(
            (a * _bell((fine - c) / width + 0.5) for a, c in zip(amplitudes, centres, strict=True)),
            np.zeros(len(fine)),
        )
        travelled = np.concatenate(([0.0], np.cumsum((speed[1:] + speed[:-1]) / 2 * np.diff(fine))))
        travelled *= length / travelled[-1]

        k = int(duration * device.rate_hz) + 1
        jitter = np.clip(rng.normal(0, device.jitter_s, k), -0.4 / device.rate_hz, 0.4 / device.rate_hz)
        times = np.clip(np.arange(k) / device.rate_hz + jitter, 0.0, duration)
        keep = rng.random(k) >= device.drop
        keep[0] = keep[-1] = True
        times = np.unique(np.round(t0 + times[keep], 6)) - t0
        at = s_a + np.interp(times, fine, travelled)

        segment = np.clip(np.searchsorted(arc, at, side="right") - 1, 0, len(arc) - 2)
        tangent = np.diff(path, axis=0)[segment]
        tangent /= np.linalg.norm(tangent, axis=1)[:, None]
        normal = np.column_stack((-tangent[:, 1], tangent[:, 0]))
        xy = np.column_stack((np.interp(at, arc, path[:, 0]), np.interp(at, arc, path[:, 1])))
        amplitude = traits.tremor_mm * math.exp(0.1 * rng.standard_normal())
        phase = rng.uniform(0, 2 * math.pi)
        xy += normal * (amplitude * np.sin(2 * math.pi * traits.tremor_hz * (t0 + times) + phase))[:, None]
        xy += rng.normal(0, device.position_noise_mm, xy.shape)
        points = self.points(xy)

        n = len(times)
        force = MAX_FORCE * traits.pressure**device.pressure_gamma
        forces = np.maximum(force * (1 + traits.pressure_cv * _smooth_noise(n, device.rate_hz, rng)), 0.001)
        altitude = 1.0 + 0.04 * _smooth_noise(n, device.rate_hz, rng)
        azimuth = 2.2 + 0.05 * _smooth_noise(n, device.rate_hz, rng)
        roll = 0.4 + 0.05 * _smooth_noise(n, device.rate_hz, rng)
        estimated = (rng.random(n) < device.estimate_fraction) & bool(device.channels["force"])

        contact = self.b.contact(round(t0 + float(times[0]), 6))
        for i in range(n):
            t = round(t0 + float(times[i]), 6)
            self.flush(t)
            true_force = round(float(forces[i]), 5)
            pending = None
            sample_force = true_force
            if estimated[i]:
                pending = (FORCE, i)
                sample_force = round(true_force * 0.85, 5)
                self.pending.append((t + device.correction_delay_s, contact, i, true_force))
            self.b.sample(
                contact,
                t,
                round(float(points[i, 0]), 3),
                round(float(points[i, 1]), 3),
                "began" if i == 0 else ("ended" if i == n - 1 else "moved"),
                force=sample_force,
                altitude=round(float(altitude[i]), 4),
                azimuth=round(float(azimuth[i]), 4),
                roll=round(float(roll[i]), 4),
                est=FORCE if estimated[i] else 0,
                pending=pending,
            )
        lift = round(t0 + float(times[-1]) + 1 / device.rate_hz, 6)
        self.b.lift(lift, contact)
        return lift

    def draw(self, paths: Sequence[FloatArray], t: float) -> float:
        """Draw every path with pen lifts at pauses. Return the last lift time."""
        traits, rng = self.s.traits, self.rng
        here: FloatArray | None = None
        for path in paths:
            arc = np.concatenate(([0.0], np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))))
            total = float(arc[-1])
            pauses = int(rng.poisson(traits.pause_rate_hz * total / traits.speed_mm_s))
            cuts = np.sort(rng.uniform(0.05 * total, 0.95 * total, pauses))
            stops = sorted({0.0, *map(float, cuts), total})
            if here is not None:
                travel = 0.15 + float(np.linalg.norm(path[0] - here)) / (2 * traits.speed_mm_s)
                self.hover(here, path[0], t, t + travel)
                t += travel
            for k, (s_a, s_b) in enumerate(itertools.pairwise(stops)):
                if k:
                    pause = 0.15 * math.exp(0.4 * rng.standard_normal()) + 0.1
                    start = np.array([np.interp(s_a, arc, path[:, 0]), np.interp(s_a, arc, path[:, 1])])
                    self.hover(start, start, t, t + pause)
                    t += pause
                t = self.contact(path, arc, s_a, s_b, t)
            here = path[-1]
        return t


def _placed(paths: list[FloatArray], frame: tuple[float, float], trace: bool, rng: Rng) -> list[FloatArray]:
    """Trace on the stimulus. Copy below it, with a small rotation and size change."""
    if trace:
        return paths
    centre = np.asarray(frame) / 2
    angle, size = rng.normal(0, 0.03), math.exp(rng.normal(0, 0.05))
    rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    offset = np.array([0.0, frame[1] + 10.0])
    return [(p - centre) @ rotation.T * size + centre + offset for p in paths]


def _later(rng: Rng, median_s: float) -> float:
    return median_s * math.exp(0.3 * rng.standard_normal())


def _write_session(
    b: JournalBuilder,
    session: _Session,
    tasks: Sequence[str],
    hand: str,
    declined: frozenset[str],
    answers: Mapping[str, Sequence[int | None]],
    rng: Rng,
) -> None:
    b.opened()
    t = b.opened_uptime + 5.0
    b.handoff(t, "participant")
    for position, task in enumerate(tasks, start=1):
        t += 2.0
        b.trial(task, 1, round(t, 6), practice=task == "practice", position=position, hand=hand)
        if task in declined:
            t += 3.0
            b.end(round(t, 6), "declined")
            continue
        t += _later(rng, 2.0)
        b.began(round(t, 6))
        t += _later(rng, 0.8)
        paths, frame, trace = _geometry(task)
        pen = _Pen(b, session, rng)
        t = pen.draw(_placed(paths, frame, trace, rng), t)
        pen.flush(math.inf)
        t += _later(rng, 0.8)
        b.end(round(t, 6), "participantDone")
    flagged = False
    for instrument, values in answers.items():
        t += 5.0
        items = [f"{instrument}-{k}" for k in range(1, len(values) + 1)]
        b.answer_all(instrument, round(t, 6), dict(zip(items, values, strict=True)))
        t += len(values) + 2.0
        flagged |= instrument == "phq9" and (values[-1] is None or values[-1] >= 1)
    t += 5.0
    b.handoff(t, "operator")
    if flagged:
        b.shown(t + 1.0, "operatorAlert")
        b.action(t + 30.0, "clinicianAssessment")
        t += 30.0
    b.shown(t + 2.0, "crisisList")
    b.close(t + 3.0)


def _uuid(rng: Rng) -> str:
    return str(uuid.UUID(bytes=rng.bytes(16), version=4)).upper()


def _read(data: bytes) -> Recording:
    return recording(replay(data.splitlines(keepends=True), closed=True)[1], hashlib.sha256(data).hexdigest())


def cohort(
    n: int,
    *,
    seed: int,
    scenario: Scenario | str = Scenario.SIGNAL,
    outcome: Outcome | None = None,
    devices: Sequence[DeviceModel] = (IPAD_PRO_PENCIL_PRO,),
    sites: Sequence[Site] = (Site("S1"),),
    tasks: Sequence[str] = MEASURED_TASKS,
    visits: int | None = None,
    crossed: bool = False,
    session_noise: Mapping[str, float] | None = None,
) -> Cohort:
    """Generate `n` participants. `seed` controls every random stream.

    `crossed` gives every participant one session on every device at each visit, for device reliability.
    Otherwise each participant keeps one device: chosen at random, or by case status in the
    `device_confounded` scenario. `session_noise` maps a trait to the standard deviation of its log
    between sessions (default 0.05 for every trait).
    """
    kind = Scenario(scenario)
    rules = _SCENARIOS[kind]
    truth_outcome = outcome or Outcome()
    visits = rules.min_visits if visits is None else visits
    noise = {name: 0.05 for name in TRAITS} | dict(session_noise or {})
    if n < 2 or not devices or not sites or visits < rules.min_visits:
        raise ValueError(f"The {kind} scenario needs n >= 2, devices, sites, and {rules.min_visits} visits.")
    if set(tasks) - {*MEASURED_TASKS, "practice"} or set(noise) - set(TRAITS):
        raise ValueError("Unknown task or trait.")
    if rules.devices_by_case and (len(devices) < 2 or crossed):
        raise ValueError("The device_confounded scenario needs two or more devices and no crossing.")
    effect = dict(truth_outcome.effect) if rules.effect else {}
    beta = np.array([effect.get(name, 0.0) for name in TRAITS])
    mean = latent_mean(truth_outcome.prevalence, truth_outcome.threshold)

    population, responses, drawing = np.random.SeedSequence(seed).spawn(3)
    pop = np.random.default_rng(population)
    depression = pop.standard_normal(n)
    quirks = pop.normal(0.0, 1.0, (n, len(TRAITS))) * rules.quirk_sd
    scores = pop.standard_normal((n, len(TRAITS))) + quirks
    scores += depression[:, None] * beta[None, :]
    risk = scores @ beta
    young = pop.random(n) < 0.4
    left = pop.random(n) < 0.1
    choice = pop.random(n)

    journals: list[Journal] = []
    recordings: list[Recording] = []
    participants: list[ParticipantTruth] = []
    for i, (answer_seq, draw_seq) in enumerate(zip(responses.spawn(n), drawing.spawn(n), strict=True)):
        answer_rng = np.random.default_rng(answer_seq)
        code = f"P{i + 1:05d}"
        site = sites[i % len(sites)]
        age_band = "13-17" if young[i] else "18-24"
        hand = "left" if left[i] else "right"
        theta = mean + depression[i]
        skip = expit(-5.0 + 1.5 * depression[i]) if rules.missingness else 0.0
        decline = expit(-3.5 + 1.2 * depression[i]) if rules.missingness else 0.0
        answers_by_visit: list[dict[str, list[int | None]]] = []
        for _ in range(visits):
            phq9: list[int | None] = list(_answer(theta, PHQ9_ITEMS, answer_rng))
            phq9 = [None if answer_rng.random() < skip else v for v in phq9]
            answers_by_visit.append({"phq9": phq9, "gad7": list(_answer(theta, GAD7_ITEMS, answer_rng))})
        first = answers_by_visit[0]["phq9"]
        case = None if None in first else sum(v or 0 for v in first) >= truth_outcome.threshold

        if crossed:
            order = list(devices) if i % 2 == 0 else list(reversed(devices))
        elif rules.devices_by_case and case is not None:
            usual = devices[-1] if case else devices[0]
            order = [usual if choice[i] < 0.85 else (devices[0] if case else devices[-1])]
        else:
            order = [devices[int(choice[i] * len(devices))]]

        draw_rng = np.random.default_rng(draw_seq)
        sessions: list[SessionTruth] = []
        for visit in range(1, visits + 1):
            for hour, device in enumerate(order):
                u = scores[i] + np.array([noise[name] for name in TRAITS]) * draw_rng.standard_normal(
                    len(TRAITS)
                )
                base = traits_from_scores(dict(zip(TRAITS, u, strict=True)))
                factor = site.speed * (0.9 if young[i] else 1.0)
                drawn = Traits(
                    **{
                        **{f.name: getattr(base, f.name) for f in fields(Traits)},
                        "speed_mm_s": base.speed_mm_s * factor,
                        "pressure": min(base.pressure * site.pressure, 0.95),
                    }
                )
                declined = frozenset(t for t in tasks if t != "practice" and draw_rng.random() < decline)
                opened = datetime(2026, 1, 5, 9, tzinfo=UTC) + timedelta(
                    days=i * 300 / n + 14 * (visit - 1), hours=hour
                )
                session_id = _uuid(draw_rng)
                mm_per_point = 25.4 * 2.0 / device.ppi
                b = JournalBuilder(
                    session_id,
                    code,
                    channels=dict(device.channels),
                    pencil=device.pencil,
                    scale={
                        "kind": "modelTable",
                        "mmPerPoint": mm_per_point,
                        "ppi": device.ppi,
                        "nativeScale": 2.0,
                    },
                    boot=_uuid(draw_rng),
                    opened_uptime=round(float(draw_rng.uniform(500.0, 90000.0)), 3),
                    age_band=age_band,
                    visit=visit,
                    device_model=device.model,
                    asset=f"{site.code}-{device.model.replace(',', '-')}",
                    site=site.code,
                    opened_at=opened.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
                answers = answers_by_visit[visit - 1]
                _write_session(
                    b, _Session(drawn, device, mm_per_point), tasks, hand, declined, answers, draw_rng
                )
                data = b.journal_bytes()
                export = f"{code}_{session_id[:8].lower()}"
                journals.append(Journal(export, session_id, data))
                recordings.append(_read(data))
                sessions.append(
                    SessionTruth(
                        session_id,
                        export,
                        visit,
                        device.model,
                        site.code,
                        opened.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        tuple(answers["phq9"]),
                        tuple(sorted(declined)),
                        len(tasks),
                    )
                )
        participants.append(
            ParticipantTruth(
                code,
                float(depression[i]),
                dict(zip(TRAITS, map(float, scores[i]), strict=True)),
                traits_from_scores(dict(zip(TRAITS, scores[i], strict=True))),
                float(risk[i]),
                case,
                site.code,
                age_band,
                hand,
                tuple(sessions),
            )
        )
    labelled = [p for p in participants if p.case is not None]
    oracle = auc(np.array([p.risk for p in labelled]), np.array([int(bool(p.case)) for p in labelled]))
    truth = Truth(kind, truth_outcome, effect, mean, tuple(participants))
    return Cohort(tuple(journals), tuple(recordings), truth, oracle)
