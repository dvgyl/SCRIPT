"""Feature definitions, explicit refusals, and long-format tables."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from scriptkin import __version__
from scriptkin.quality import DEFAULT_QC, Accepted, QcPolicy, Refusal, RefusalCode, Rejected, Segment, assess
from scriptkin.recording import Channel, FloatArray, PenState, Recording
from scriptkin.signal import (
    DEFAULT_PREPROCESSING,
    GammaFit,
    Kinematics,
    Preprocessing,
    coarse_grain,
    gamma_fit,
    kinematics,
    micro_spikes,
    pool,
    sample_entropy_counts,
    welch_band_power,
)

Cell = float | Refusal
PoolRule = Literal["mean", "median", "max", "sum"]


@dataclass(frozen=True)
class Feature:
    name: str
    unit: str
    pool: PoolRule = "mean"
    min_values: int = 1
    millimetres: bool = False
    channel: Channel | None = None
    mode: str | None = None
    definition: str = ""


CATALOG = (
    Feature("kinematic.speed_mean", "mm/s", millimetres=True, definition="Mean segment speed."),
    Feature("kinematic.speed_max", "mm/s", "max", millimetres=True, definition="Maximum segment speed."),
    Feature(
        "kinematic.acceleration_mean",
        "mm/s^2",
        millimetres=True,
        definition="Mean norm of the second position derivative.",
    ),
    Feature(
        "kinematic.jerk_mean",
        "mm/s^3",
        millimetres=True,
        definition="Mean norm of the third position derivative.",
    ),
    Feature(
        "kinematic.path_length",
        "mm",
        "sum",
        millimetres=True,
        definition="Sum of contact segment chord lengths.",
    ),
    Feature("timing.duration_s", "s", definition="Trial end minus trial start in one boot."),
    Feature("timing.contact_s", "s", "sum", definition="Sum of accepted contact segment durations."),
    Feature("timing.in_air_s", "s", "sum", definition="Sum of accepted in-air segment durations."),
    Feature("timing.stroke_count", "count", definition="Count of contact strokes with accepted segments."),
    Feature("pressure.force_mean", "source", channel=Channel.FORCE, definition="Mean contact force."),
    Feature(
        "pressure.force_cv",
        "1",
        channel=Channel.FORCE,
        definition="Force standard deviation divided by mean.",
    ),
    Feature("tilt.altitude_mean", "rad", channel=Channel.ALTITUDE, definition="Mean contact altitude."),
    Feature(
        "tilt.azimuth_variance",
        "1",
        channel=Channel.AZIMUTH,
        definition="One minus the mean resultant length of contact azimuth.",
    ),
    Feature(
        "tilt.roll_variance",
        "1",
        channel=Channel.ROLL,
        definition="One minus the mean resultant length of contact roll.",
    ),
    Feature(
        "spatial.trace_rmse",
        "mm",
        millimetres=True,
        mode="trace",
        definition="Root mean square nearest distance to all stimulus polyline edges.",
    ),
    Feature(
        "spatial.copy_rmse",
        "mm",
        millimetres=True,
        mode="copy",
        definition="Polyline RMSE after a proper similarity Procrustes alignment by arc length.",
    ),
    Feature(
        "spectral.speed_band_power",
        "(mm/s)^2",
        min_values=3,
        millimetres=True,
        definition="Mean segment Welch speed power in the configured band; trapezoid integration.",
    ),
    Feature(
        "entropy.speed_sample_entropy",
        "1",
        millimetres=True,
        definition="Negative log(A/B); pooled valid templates, no self-matches, Chebyshev distance.",
    ),
    Feature(
        "micro.spike_mean",
        "1",
        millimetres=True,
        definition="Mean Torres normalized peaks; interval means include both minima.",
    ),
    *(
        Feature(
            f"micro.gamma_{parameter}{suffix}",
            "1",
            millimetres=True,
            definition=f"Gamma {parameter}{suffix}: MLE, location 0, observed-information log interval.",
        )
        for parameter in ("shape", "scale")
        for suffix in ("", "_low", "_high")
    ),
)


def catalog_digest() -> str:
    payload = json.dumps([asdict(f) for f in CATALOG], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _path_samples(paths: tuple[FloatArray, ...], n: int) -> FloatArray:
    starts = np.concatenate([p[:-1] for p in paths])
    deltas = np.concatenate([np.diff(p, axis=0) for p in paths])
    lengths = np.linalg.norm(deltas, axis=1)
    positive = lengths > 0
    starts, deltas, lengths = starts[positive], deltas[positive], lengths[positive]
    if not len(lengths):
        raise ValueError("A path needs positive length.")
    cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
    distances = np.linspace(0, cumulative[-1], n)
    indices = np.minimum(np.searchsorted(cumulative, distances, side="right") - 1, len(lengths) - 1)
    fractions = (distances - cumulative[indices]) / lengths[indices]
    return starts[indices] + deltas[indices] * fractions[:, None]


def _polyline_rmse(xy: FloatArray, paths: tuple[FloatArray, ...]) -> float:
    starts = np.concatenate([p[:-1] for p in paths])
    deltas = np.concatenate([np.diff(p, axis=0) for p in paths])
    lengths2 = np.sum(deltas**2, axis=1)
    lengths2[lengths2 == 0] = 1
    squared: list[FloatArray] = []
    for block in np.array_split(xy, max(1, (len(xy) + 255) // 256)):
        offsets = block[:, None, :] - starts[None, :, :]
        fractions = np.clip(np.sum(offsets * deltas, axis=2) / lengths2, 0, 1)
        residual = offsets - fractions[:, :, None] * deltas
        squared.append(np.min(np.sum(residual**2, axis=2), axis=1))
    return float(np.sqrt(np.mean(np.concatenate(squared))))


def _spatial(ok: Accepted, segments: tuple[Segment, ...], *, copy: bool) -> Cell:
    paths = ok.trial.task.paths
    if not segments:
        return Refusal(RefusalCode.TOO_FEW_VALUES, "This trial has no accepted contact segment.")
    xy = np.concatenate([s.xy for s in segments])
    if copy:
        try:
            source = _path_samples(tuple(s.xy for s in segments), 256)
            target = _path_samples(paths, 256)
            a, b = source - source.mean(axis=0), target - target.mean(axis=0)
            u, singular, vt = np.linalg.svd(a.T @ b)
            signs = np.array([1.0, np.linalg.det(u @ vt)])
            rotation = u @ np.diag(signs) @ vt
            scale = float(np.sum(singular * signs) / np.sum(a**2))
            xy = (xy - source.mean(axis=0)) @ rotation * scale + target.mean(axis=0)
        except (ValueError, np.linalg.LinAlgError) as e:
            return Refusal(RefusalCode.UNDEFINED, f"Cannot align this path: {e}")
    return _polyline_rmse(xy, paths)


def extract(ok: Accepted, settings: Preprocessing = DEFAULT_PREPROCESSING) -> dict[str, Cell]:
    """Calculate all catalog cells from QC output. Return a refusal for each failed requirement."""
    trial = ok.trial
    contact = tuple(s for s in ok.segments if s.state == PenState.CONTACT)
    movements = [kinematics(s, settings) for s in contact]
    valid = [k for k in movements if isinstance(k, Kinematics)]
    spikes = [micro_spikes(k.speed) for k in valid]
    all_spikes = np.concatenate(spikes) if spikes else np.empty(0)
    fitted = gamma_fit(all_spikes)
    result: dict[str, Cell] = {}
    for f in CATALOG:
        name = f.name
        refusal: Refusal | None = None
        if f.mode and (
            trial.task.mode != f.mode or trial.task.id in ("clock", "sentence") or not trial.task.paths
        ):
            refusal = Refusal(RefusalCode.NOT_APPLICABLE, "This spatial feature does not apply to the task.")
        elif f.millimetres and trial.device.spatial_unit != "mm":
            refusal = Refusal(RefusalCode.UNKNOWN_SCALE, "A physical position scale is required.")
        elif f.channel and trial.device.input == "directTouchAsPen":
            refusal = Refusal(RefusalCode.SIMULATED_INPUT, "This channel requires a pen.")
        elif f.channel and (
            not contact
            or any(
                f.channel not in s.channels or not np.isfinite(s.channels[f.channel]).all() for s in contact
            )
        ):
            refusal = Refusal(
                RefusalCode.MISSING_CHANNEL, f"The {f.channel} channel is absent or incomplete."
            )
        bits = {Channel.FORCE: 1, Channel.AZIMUTH: 2, Channel.ALTITUDE: 4, Channel.ROLL: 16}
        mask = bits[f.channel] if f.channel else (8 if f.millimetres else 0)
        if refusal is None and mask and any(np.any(s.unresolved & mask) for s in contact):
            refusal = Refusal(RefusalCode.UNRESOLVED_ESTIMATES, "A required value is still estimated.")
        if refusal is not None:
            result[name] = refusal
            continue
        values: list[Cell] = []
        cell: Cell
        if name == "timing.duration_s":
            cell = (
                float(trial.ended - trial.started)
                if trial.ended is not None and trial.started is not None and trial.ended >= trial.started
                else Refusal(RefusalCode.UNDEFINED, "No trial duration.")
            )
        elif name == "timing.stroke_count":
            cell = float(len({s.stroke_index for s in contact}))
        elif name in ("timing.contact_s", "timing.in_air_s"):
            state = PenState.CONTACT if name == "timing.contact_s" else PenState.IN_AIR
            durations = [float(s.t[-1] - s.t[0]) for s in ok.segments if s.state == state]
            cell = (
                pool(durations, rule="sum")
                if durations
                else Refusal(RefusalCode.MISSING_CHANNEL, "No accepted samples for this pen state.")
            )
        elif name == "kinematic.path_length":
            values = [float(np.linalg.norm(np.diff(s.xy, axis=0), axis=1).sum()) for s in contact]
            cell = pool(values, rule=f.pool, min_values=f.min_values)
        elif name.startswith("kinematic."):
            for k in valid:
                arrays = {
                    "speed_mean": k.speed,
                    "speed_max": k.speed,
                    "acceleration_mean": np.linalg.norm(k.acceleration, axis=1),
                    "jerk_mean": np.linalg.norm(k.jerk, axis=1),
                }
                a = arrays[name.split(".")[1]]
                values.append(float(np.max(a) if name.endswith("_max") else np.mean(a)))
            cell = pool(values, rule=f.pool, min_values=f.min_values)
        elif f.channel:
            for s in contact:
                a = s.channels[f.channel]
                if name.endswith("_cv"):
                    mean = float(np.mean(a))
                    values.append(
                        float(np.std(a) / mean)
                        if mean > 0
                        else Refusal(RefusalCode.UNDEFINED, "Mean force is zero.")
                    )
                elif name.endswith("_variance"):
                    values.append(float(1 - abs(np.mean(np.exp(1j * a)))))
                else:
                    values.append(float(np.mean(a)))
            cell = pool(values, rule=f.pool, min_values=f.min_values)
        elif name.startswith("spatial."):
            cell = _spatial(ok, contact, copy=f.mode == "copy")
        elif name.startswith("spectral."):
            values = [
                welch_band_power(
                    k.speed, settings.grid_hz, settings.band_hz, window_s=settings.welch_window_s
                )
                for k in valid
            ]
            cell = pool(values, rule=f.pool, min_values=f.min_values)
        elif name.startswith("entropy."):
            series = coarse_grain([k.speed for k in valid], settings.entropy_scale)
            tolerance = (settings.entropy_r * float(np.std(np.concatenate(series)))) if series else 0.0
            b, a = sample_entropy_counts(series, settings.entropy_m, max(tolerance, np.finfo(float).eps))
            cell = (
                float(-np.log(a / b))
                if a and b
                else Refusal(RefusalCode.TOO_FEW_VALUES, "No matching entropy template extensions.")
            )
        elif name == "micro.spike_mean":
            cell = pool((float(x) for x in all_spikes), min_values=f.min_values)
        else:
            if isinstance(fitted, GammaFit):
                parameter, *bound = name.removeprefix("micro.gamma_").split("_")
                estimate = fitted.shape if parameter == "shape" else fitted.scale
                cell = getattr(estimate, bound[0] if bound else "point")
            else:
                cell = fitted
        result[name] = (
            cell
            if isinstance(cell, Refusal) or np.isfinite(cell)
            else Refusal(RefusalCode.UNDEFINED, "The feature is not finite.")
        )
    return result


@dataclass(frozen=True)
class FeatureRow:
    participant: str
    session: str
    device: str
    task: str
    trial: int
    attempt: int
    cells: dict[str, Cell]


@dataclass(frozen=True)
class Provenance:
    library_version: str
    qc_policy: QcPolicy
    preprocessing: Preprocessing
    catalog_digest: str
    source_hashes: tuple[str, ...]


@dataclass(frozen=True)
class FeatureTable:
    rows: tuple[FeatureRow, ...]
    provenance: Provenance

    def to_csv(self, path: str | Path) -> None:
        """Write one row per feature cell and a JSON provenance file beside it."""
        target = Path(path)
        with target.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                (
                    "participant",
                    "session",
                    "device",
                    "task",
                    "trial",
                    "attempt",
                    "feature",
                    "value",
                    "refusal",
                    "detail",
                )
            )
            for row in self.rows:
                for name, cell in row.cells.items():
                    refused = isinstance(cell, Refusal)
                    writer.writerow(
                        (
                            row.participant,
                            row.session,
                            row.device,
                            row.task,
                            row.trial,
                            row.attempt,
                            name,
                            "" if refused else cell,
                            cell.code if refused else "",
                            cell.detail if refused else "",
                        )
                    )
        target.with_suffix(target.suffix + ".provenance.json").write_text(
            json.dumps(asdict(self.provenance), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def feature_table(
    recordings: Iterable[Recording],
    *,
    policy: QcPolicy = DEFAULT_QC,
    settings: Preprocessing = DEFAULT_PREPROCESSING,
) -> FeatureTable:
    rows: list[FeatureRow] = []
    hashes: set[str] = set()
    for recording in recordings:
        hashes.update(recording.source_hashes)
        for trial in recording.trials:
            qc = assess(trial, policy)
            if isinstance(qc, Rejected):
                cells: dict[str, Cell] = {f.name: qc.refusals[0] for f in CATALOG}
            else:
                cells = extract(qc, settings)
            rows.append(
                FeatureRow(
                    recording.participant,
                    recording.session_id,
                    trial.device.model,
                    trial.task.id,
                    trial.index,
                    trial.attempt,
                    cells,
                )
            )
    return FeatureTable(
        tuple(rows), Provenance(__version__, policy, settings, catalog_digest(), tuple(sorted(hashes)))
    )
