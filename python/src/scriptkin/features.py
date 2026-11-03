"""Feature definitions, explicit refusals, and long-format tables."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from scipy.spatial import KDTree

from scriptkin import __version__
from scriptkin.quality import DEFAULT_QC, Accepted, QcPolicy, Refusal, RefusalCode, Rejected, Segment, assess
from scriptkin.recording import (
    Channel,
    FloatArray,
    IntArray,
    ParticipantContext,
    PenState,
    Recording,
    Trial,
    TrialContext,
)
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
    state: PenState | None = PenState.CONTACT
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
    Feature("timing.duration_s", "s", state=None, definition="Trial end minus trial start in one boot."),
    Feature(
        "timing.initiation_latency_s",
        "s",
        state=None,
        definition="First contact sample time minus the Begin tap time; refused without a Begin tap.",
    ),
    Feature("timing.contact_s", "s", "sum", definition="Sum of accepted contact segment durations."),
    Feature(
        "timing.in_air_s",
        "s",
        "sum",
        state=PenState.IN_AIR,
        definition="Sum of accepted in-air segment durations.",
    ),
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
        definition=(
            "RMS of drawn-to-stimulus and stimulus-to-drawn polyline distances, each direction weighted"
            " one half, after a proper similarity ICP alignment that starts from matched centroids and"
            " spreads with no rotation; independent of stroke order and direction."
        ),
    ),
    Feature(
        "spectral.speed_band_power",
        "(mm/s)^2",
        min_values=3,
        millimetres=True,
        definition=(
            "Welch speed power in the configured band from the segment spectra weighted by window count;"
            " min_values counts Welch windows; trapezoid integration."
        ),
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


Edges = tuple[FloatArray, FloatArray]
_ICP_MAX_ITERATIONS = 200
_ICP_TOLERANCE = 1e-12


def _edges(paths: Sequence[FloatArray]) -> Edges:
    """Return the start point and the vector of every polyline edge."""
    return np.concatenate([p[:-1] for p in paths]), np.concatenate([np.diff(p, axis=0) for p in paths])


def _path_samples(paths: Sequence[FloatArray], n: int) -> FloatArray:
    starts, deltas = _edges(paths)
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


def _nearest(points: FloatArray, edges: Edges) -> tuple[IntArray, FloatArray]:
    """Return the nearest edge to each point and the clipped fraction along that edge.

    The edge with the nearest midpoint bounds the distance. Only an edge whose midpoint lies
    within that bound plus half the longest edge can be nearer, so only those edges are compared.
    """
    starts, deltas = edges
    lengths = np.linalg.norm(deltas, axis=1)
    divisors = np.where(lengths > 0, lengths**2, 1.0)

    def measure(rows: IntArray, candidates: IntArray) -> tuple[FloatArray, FloatArray]:
        offsets = points[rows] - starts[candidates]
        fractions = np.clip(np.sum(offsets * deltas[candidates], axis=1) / divisors[candidates], 0, 1)
        return fractions, np.sum((offsets - fractions[:, None] * deltas[candidates]) ** 2, axis=1)

    tree = KDTree(starts + deltas / 2)
    _, bound_squared = measure(np.arange(len(points)), np.asarray(tree.query(points)[1], dtype=np.int64))
    # The 1e-9 mm margin keeps a midpoint that lies exactly on the radius inside it despite rounding.
    groups = tree.query_ball_point(points, np.sqrt(bound_squared) + lengths.max() / 2 + 1e-9)
    index = np.empty(len(points), dtype=np.int64)
    fraction = np.empty(len(points))
    for block in np.array_split(np.arange(len(points)), max(1, (len(points) + 255) // 256)):
        rows = np.repeat(block, [len(groups[i]) for i in block])
        candidates = np.concatenate([groups[i] for i in block]).astype(np.int64)
        fractions, squared = measure(rows, candidates)
        first = np.lexsort((squared, rows))[np.searchsorted(rows, block)]
        index[block], fraction[block] = candidates[first], fractions[first]
    return index, fraction


def _on_edges(edges: Edges, index: IntArray, fraction: FloatArray) -> FloatArray:
    return edges[0][index] + edges[1][index] * fraction[:, None]


def _similarity(
    source: FloatArray, target: FloatArray, weights: FloatArray
) -> tuple[FloatArray, float, FloatArray]:
    """Fit weighted least-squares rotation, uniform scale, and shift for row vectors, without reflection."""
    source_mean, target_mean = weights @ source, weights @ target
    a, b = source - source_mean, target - target_mean
    u, singular, vt = np.linalg.svd(a.T @ (b * weights[:, None]))
    signs = np.array([1.0, np.sign(np.linalg.det(u @ vt))])
    rotation = u @ np.diag(signs) @ vt
    scale = float(np.sum(singular * signs) / (weights @ np.sum(a**2, axis=1)))
    return rotation, scale, target_mean - scale * source_mean @ rotation


def _copy_rmse(drawn: tuple[FloatArray, ...], paths: tuple[FloatArray, ...]) -> float:
    """Align the drawing to the stimulus by similarity ICP that pairs points in both directions.

    Drawn points pair with their nearest stimulus edge points. Stimulus arc-length samples pair with
    their nearest drawn edge points, so an omitted part adds error and the scale cannot collapse.
    The start matches centroids and spreads with no rotation.
    """
    points = np.concatenate(drawn)
    targets = _path_samples(paths, 256)
    source = _path_samples(drawn, 256)
    drawn_edges, stimulus_edges = _edges(drawn), _edges(paths)
    weights = np.concatenate(
        (np.full(len(points), 0.5 / len(points)), np.full(len(targets), 0.5 / len(targets)))
    )
    scale = float(np.sqrt(np.sum(np.var(targets, axis=0)) / np.sum(np.var(source, axis=0))))
    rotation, shift = np.eye(2), targets.mean(axis=0) - scale * source.mean(axis=0)
    cost = np.inf
    for _ in range(_ICP_MAX_ITERATIONS):
        moved = (drawn_edges[0] @ rotation * scale + shift, drawn_edges[1] @ rotation * scale)
        stimulus_pairs = _on_edges(
            stimulus_edges, *_nearest(points @ rotation * scale + shift, stimulus_edges)
        )
        source_pairs = np.concatenate((points, _on_edges(drawn_edges, *_nearest(targets, moved))))
        target_pairs = np.concatenate((stimulus_pairs, targets))
        residual = source_pairs @ rotation * scale + shift - target_pairs
        previous, cost = cost, float(weights @ np.sum(residual**2, axis=1))
        if previous - cost <= _ICP_TOLERANCE * max(cost, 1.0):
            break
        rotation, scale, shift = _similarity(source_pairs, target_pairs, weights)
    return float(np.sqrt(cost))


def _trace_rmse(xy: FloatArray, paths: tuple[FloatArray, ...]) -> float:
    edges = _edges(paths)
    nearest = _on_edges(edges, *_nearest(xy, edges))
    return float(np.sqrt(np.mean(np.sum((xy - nearest) ** 2, axis=1))))


def _spatial(ok: Accepted, segments: tuple[Segment, ...], *, copy: bool) -> Cell:
    paths = ok.trial.task.paths
    if not segments:
        return Refusal(RefusalCode.TOO_FEW_VALUES, "This trial has no accepted contact segment.")
    drawn = tuple(s.xy for s in segments)
    if not copy:
        return _trace_rmse(np.concatenate(drawn), paths)
    try:
        return _copy_rmse(drawn, paths)
    except ValueError as e:
        return Refusal(RefusalCode.UNDEFINED, f"Cannot align this path: {e}")


def _initiation_latency(trial: Trial) -> Cell:
    if trial.began is None:
        return Refusal(RefusalCode.UNDEFINED, "The trial has no Begin tap.")
    starts = [float(s.t[0]) for s in trial.strokes if s.state == PenState.CONTACT and len(s.t)]
    if not starts:
        return Refusal(RefusalCode.UNDEFINED, "The trial has no contact.")
    latency = min(starts) - trial.began
    if latency < 0:
        return Refusal(RefusalCode.UNDEFINED, "The first contact precedes the Begin tap.")
    return latency


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
        elif f.state is not None and f.state in ok.refused_states:
            refusal = ok.refused_states[f.state]
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
        elif name == "timing.initiation_latency_s":
            cell = _initiation_latency(trial)
        elif name == "timing.stroke_count":
            cell = float(len({s.stroke_index for s in contact}))
        elif name in ("timing.contact_s", "timing.in_air_s"):
            durations = [float(s.t[-1] - s.t[0]) for s in ok.segments if s.state == f.state]
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
            cell = welch_band_power(
                [k.speed for k in valid],
                settings.grid_hz,
                settings.band_hz,
                window_s=settings.welch_window_s,
                min_windows=f.min_values,
            )
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


_CSV_COLUMNS = (
    "participant",
    "session",
    "site",
    "administration",
    "age_band",
    "visit",
    "opened_at",
    "device",
    "task",
    "trial",
    "attempt",
    "hand",
    "accommodations",
    "qc",
    "feature",
    "value",
    "refusal",
    "detail",
)


@dataclass(frozen=True)
class FeatureRow:
    """One trial with the session context that an analysis stratifies, holds out, or orders by.

    `qc` holds the codes that rejected the trial and is empty for an accepted trial. A source without
    session context gives "unknown" text and None for `visit` and `opened_at`.
    """

    participant: str
    session: str
    site: str
    administration: str
    age_band: str
    visit: int | None
    opened_at: str | None
    device: str
    task: str
    trial: int
    attempt: int
    hand: str
    accommodations: tuple[str, ...]
    qc: tuple[RefusalCode, ...]
    cells: dict[str, Cell]


@dataclass(frozen=True)
class Provenance:
    library_version: str
    qc_policy: QcPolicy
    preprocessing: Preprocessing
    catalog_digest: str
    source_hashes: tuple[str, ...]
    reader_profiles: tuple[str, ...]


def _provenance_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".provenance.json")


@dataclass(frozen=True)
class FeatureTable:
    rows: tuple[FeatureRow, ...]
    provenance: Provenance

    def to_csv(self, path: str | Path) -> None:
        """Write one row per feature cell and a JSON provenance file beside it."""
        target = Path(path)
        with target.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(_CSV_COLUMNS)
            for row in self.rows:
                for name, cell in row.cells.items():
                    refused = isinstance(cell, Refusal)
                    writer.writerow(
                        (
                            row.participant,
                            row.session,
                            row.site,
                            row.administration,
                            row.age_band,
                            "" if row.visit is None else row.visit,
                            row.opened_at or "",
                            row.device,
                            row.task,
                            row.trial,
                            row.attempt,
                            row.hand,
                            ";".join(row.accommodations),
                            ";".join(row.qc),
                            name,
                            "" if refused else cell,
                            cell.code if refused else "",
                            cell.detail if refused else "",
                        )
                    )
        _provenance_path(target).write_text(
            json.dumps(asdict(self.provenance), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def _split(text: str) -> tuple[str, ...]:
    return tuple(text.split(";")) if text else ()


def read_feature_table(path: str | Path) -> FeatureTable:
    """Read a table and provenance file that `FeatureTable.to_csv` wrote. Invalid input raises ValueError."""
    source = Path(path)
    try:
        meta = json.loads(_provenance_path(source).read_text(encoding="utf-8"))
        settings: dict[str, Any] = dict(meta["preprocessing"])
        settings["band_hz"] = tuple(settings["band_hz"])
        provenance = Provenance(
            meta["library_version"],
            QcPolicy(**meta["qc_policy"]),
            Preprocessing(**settings),
            meta["catalog_digest"],
            tuple(meta["source_hashes"]),
            tuple(meta["reader_profiles"]),
        )
    except (OSError, KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Invalid or absent provenance for {source.name}: {e}") from e
    rows: dict[tuple[str, int, int, str], FeatureRow] = {}
    with source.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != _CSV_COLUMNS:
            raise ValueError(f"{source.name} does not have the feature table columns.")
        for line, record in enumerate(reader, start=2):
            try:
                key = (record["session"], int(record["trial"]), int(record["attempt"]), record["task"])
                cell: Cell = (
                    Refusal(RefusalCode(record["refusal"]), record["detail"])
                    if record["refusal"]
                    else float(record["value"])
                )
                if key not in rows:
                    rows[key] = FeatureRow(
                        record["participant"],
                        record["session"],
                        record["site"],
                        record["administration"],
                        record["age_band"],
                        int(record["visit"]) if record["visit"] else None,
                        record["opened_at"] or None,
                        record["device"],
                        record["task"],
                        key[1],
                        key[2],
                        record["hand"],
                        _split(record["accommodations"]),
                        tuple(RefusalCode(code) for code in _split(record["qc"])),
                        {},
                    )
                rows[key].cells[record["feature"]] = cell
            except ValueError as e:
                raise ValueError(f"{source.name}, line {line}: {e}") from e
    return FeatureTable(tuple(rows.values()), provenance)


def feature_table(
    recordings: Iterable[Recording],
    *,
    policy: QcPolicy = DEFAULT_QC,
    settings: Preprocessing = DEFAULT_PREPROCESSING,
) -> FeatureTable:
    """One row per measured trial. Practice trials, qualification sessions, and bench sessions never enter.

    Session context is "unknown" or None when the source format does not record it.
    """
    rows: list[FeatureRow] = []
    hashes: set[str] = set()
    profiles: set[str] = set()
    for recording in recordings:
        session = recording.context
        if session is not None and session.purpose != "study":
            continue
        person = session.subject if session and isinstance(session.subject, ParticipantContext) else None
        hashes.update(recording.source_hashes)
        profiles.add(recording.reader_profile)
        for trial in recording.trials:
            if trial.context is not None and trial.context.practice:
                continue
            qc = assess(trial, policy)
            if isinstance(qc, Rejected):
                cells: dict[str, Cell] = {f.name: qc.refusals[0] for f in CATALOG}
            else:
                cells = extract(qc, settings)
            participant = trial.context if isinstance(trial.context, TrialContext) else None
            rows.append(
                FeatureRow(
                    recording.participant or "unknown",
                    recording.session_id,
                    person.study.site if person else "unknown",
                    person.administration if person else "unknown",
                    person.age_band if person else "unknown",
                    person.visit.number if person else None,
                    session.opened_at if session else None,
                    trial.device.model,
                    trial.task.id,
                    trial.index,
                    trial.attempt,
                    participant.hand if participant else "unknown",
                    participant.accommodations if participant else (),
                    tuple(r.code for r in qc.refusals) if isinstance(qc, Rejected) else (),
                    cells,
                )
            )
    return FeatureTable(
        tuple(rows),
        Provenance(
            __version__, policy, settings, catalog_digest(), tuple(sorted(hashes)), tuple(sorted(profiles))
        ),
    )
