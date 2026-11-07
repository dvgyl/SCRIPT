"""Bench input validation and deterministic program selection."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from scriptkin import bench
from scriptkin.bench.analysis import correction_model, decided_on
from scriptkin.bench.features import run_features
from scriptkin.bench.moves import MoveList, commanded_trial
from scriptkin.bench.runs import CAMERA_FEATURES, LOG_SCALE
from scriptkin.consequence.plan import FEATURES, LIMITS, CellSpec, ConsequencePlan, ProgramTarget
from scriptkin.quality import Accepted, assess


@dataclass(frozen=True)
class Selection:
    program: bench.Design
    distance: float
    in_range: bool


def select_program(target: ProgramTarget, programs: Sequence[bench.Design]) -> Selection:
    eligible = [p for p in programs if p.geometry == target.geometry]
    if not eligible:
        raise ValueError("The target geometry has no program.")

    def distance(p: bench.Design) -> float:
        return abs(math.log(target.scale / p.scale)) + abs(math.log(target.speed_mm_s / p.speed_mm_s))

    p = min(eligible, key=lambda p: (abs(p.oscillation_hz - target.oscillation_hz), distance(p), p.id))
    within = all(
        min(values) <= value <= max(values)
        for value, values in (
            (target.scale, [p.scale for p in eligible]),
            (target.speed_mm_s, [p.speed_mm_s for p in eligible]),
            (target.oscillation_hz, [p.oscillation_hz for p in eligible]),
        )
    )
    return Selection(p, distance(p), within)


@dataclass(frozen=True)
class Coefficients:
    alpha: float
    beta: float = 1.0
    gamma_u: float = 0.0
    gamma_v: float = 0.0


@dataclass(frozen=True)
class FeatureInputs:
    feature: str
    selection: Selection
    center: float
    bias: float
    lower: float
    upper: float
    run_p: float
    mount_p: float
    run_w: float
    mount_w: float
    correction: Coefficients | None
    support: str
    bias_basis: str
    reason: str = ""


@dataclass(frozen=True)
class BenchInputs:
    features: tuple[FeatureInputs, ...]
    source: str
    digest: str
    post_hoc: bool = False
    raw: str = ""


Centers = Mapping[tuple[str, str], float]


def center(moves: MoveList, feature: str) -> float:
    accepted = assess(commanded_trial(moves))
    if not isinstance(accepted, Accepted):
        raise ValueError("The noiseless program failed quality checks.")
    value = run_features(accepted).cells[feature]
    if not isinstance(value, float):
        raise ValueError(f"The noiseless program cannot define {feature}.")
    return math.log(value) if feature in LOG_SCALE else value


def centers_for(move_lists: Mapping[str, MoveList]) -> dict[tuple[str, str], float]:
    return {
        (name, pid): center(moves, name)
        for pid, moves in move_lists.items()
        if moves.program.fault is None
        for name in FEATURES
        if moves.program.geometry in decided_on(name)
    }


def selected(feature: str, programs: Sequence[bench.Design] = bench.MAIN) -> Selection:
    target = (
        ProgramTarget("line", oscillation_hz=8.0) if feature == "bench.band_power_mm2_s2" else ProgramTarget()
    )
    return select_program(target, programs)


def grid_inputs(plan: ConsequencePlan, cell: CellSpec, centers: Centers) -> BenchInputs:
    del plan
    noise = {
        "zero": (0.0, 0.0, 0.0, 0.0),
        "nominal": (0.25, 0.10, 0.25, 0.25),
        "stress": (0.25, 0.25, 0.50, 0.50),
    }[cell.noise_id]
    rows = []
    for feature in cell.features:
        limit = LIMITS[feature]
        bias = cell.bias_multiple * limit
        alpha = bias + (0.5 * limit if cell.sensitivity == "misspecified" else 0)
        beta = 1.05 if cell.sensitivity == "misspecified" and feature != "bench.band_power_mm2_s2" else 1.0
        selection = selected(feature)
        rows.append(
            FeatureInputs(
                feature,
                selection,
                centers[(feature, selection.program.id)],
                bias,
                bias,
                bias,
                run_p=float((noise[0] * limit) ** 2),
                mount_p=float((noise[1] * limit) ** 2),
                run_w=float((noise[2] * limit) ** 2),
                mount_w=float((noise[3] * limit) ** 2),
                correction=Coefficients(alpha, beta),
                support="oracle_control",
                bias_basis="camera_referenced" if feature in CAMERA_FEATURES else "raw_device",
            )
        )
    return BenchInputs(tuple(rows), "grid", hashlib.sha256(repr(rows).encode()).hexdigest())


def _finite(value: Any) -> float:
    if isinstance(value, bool) or value is None or not math.isfinite(float(value)):
        raise ValueError("A finite number is required.")
    return float(value)


def _interval(raw: Any) -> tuple[float, float, float]:
    estimate, lower, upper = (_finite(raw[k]) for k in ("estimate", "lower", "upper"))
    if not lower <= estimate <= upper:
        raise ValueError("The interval is not ordered.")
    return estimate, lower, upper


def read_deliverables(path: Path, centers: Centers, *, expected_protocol: str) -> BenchInputs:
    if path.name.startswith("._"):
        raise ValueError("Metadata files are excluded.")
    payload = path.read_bytes()
    raw: Any = json.loads(payload)
    try:
        if raw["protocol"]["version"] != expected_protocol or raw["analysis"] != "main":
            raise ValueError("The bench protocol or analysis differs.")
        for key in ("lock", "postHoc", "library"):
            raw[key]
        if set(raw["features"]) != set(FEATURES):
            raise ValueError("The bench feature set differs.")
        rows: list[FeatureInputs] = []
        for feature in FEATURES:
            f = raw["features"][feature]
            basis = "camera_referenced" if feature in CAMERA_FEATURES else "raw_device"
            if f["limit"] != LIMITS[feature] or f["scale"] != ("ln" if feature in LOG_SCALE else "absolute"):
                raise ValueError("The feature limit or scale differs.")
            if f["values"] != {"bias": basis, "correction": "raw_device"}:
                raise ValueError("The feature value basis differs.")
            for key in ("featureP", "equivalent", "range"):
                f[key]
            # Select before reading estimates. Missing nearest inputs must not change the selection.
            selection = selected(feature, [bench.program(p) for p in f["programs"]])
            p = f["programs"][selection.program.id]
            required = [p["bias"][k] for k in ("estimate", "lower", "upper")]
            required.extend(p["components"][d][c] for d in ("P", "W") for c in ("run", "mount"))
            if any(v is None for v in required):
                rows.append(
                    FeatureInputs(
                        feature,
                        selection,
                        centers[(feature, selection.program.id)],
                        float("nan"),
                        float("nan"),
                        float("nan"),
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        None,
                        "unavailable",
                        basis,
                        "missing_selected_program_value",
                    )
                )
                continue
            bias, lower, upper = _interval(p["bias"])
            if feature in LOG_SCALE:
                factor = _interval(p["factor"])
                if any(
                    a <= 0 or not math.isclose(a, math.exp(b), rel_tol=1e-10)
                    for a, b in zip(factor, (bias, lower, upper), strict=True)
                ):
                    raise ValueError("The factor differs from exp(bias).")
            variances = tuple(_finite(p["components"][d][c]) for d in ("P", "W") for c in ("run", "mount"))
            if min(variances) < 0:
                raise ValueError("A variance is negative.")
            correction = f["correction"]
            terms = None
            support = "unavailable"
            if correction is not None:
                model = correction_model(feature)
                if correction["model"] != model.value:
                    raise ValueError("The correction model differs.")
                fixed = {"beta": 1.0, "gamma_u": 0.0, "gamma_v": 0.0}
                estimates = correction["coefficients"]
                for term, value in fixed.items():
                    fitted = model.fits_slope if term == "beta" else model.fits_covariates
                    if estimates[term]["fitted"] != fitted or (
                        not fitted and estimates[term]["estimate"] != value
                    ):
                        raise ValueError("A fixed correction term differs.")
                try:
                    terms = Coefficients(
                        *(_finite(estimates[t]["estimate"]) for t in ("alpha", "beta", "gamma_u", "gamma_v"))
                    )
                except ValueError:
                    terms = None
                if terms is not None:
                    residual = correction["residual"]
                    support = (
                        "bench_supported"
                        if set(residual) == set(f["programs"])
                        and all(
                            r["withinLimit"]
                            and -LIMITS[feature] < _interval(r)[1]
                            and _interval(r)[2] < LIMITS[feature]
                            for r in residual.values()
                        )
                        else "failed_lack_of_fit"
                    )
            rows.append(
                FeatureInputs(
                    feature,
                    selection,
                    centers[(feature, selection.program.id)],
                    bias,
                    lower,
                    upper,
                    run_p=variances[0],
                    mount_p=variances[1],
                    run_w=variances[2],
                    mount_w=variances[3],
                    correction=terms,
                    support=support,
                    bias_basis=basis,
                )
            )
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError(f"Invalid bench deliverables: {error}") from error
    return BenchInputs(
        tuple(rows),
        "measured",
        hashlib.sha256(payload).hexdigest(),
        raw["postHoc"] is not None,
        payload.decode(),
    )


def for_cell(inputs: BenchInputs, cell: CellSpec) -> BenchInputs:
    rows = []
    by_feature = {f.feature: f for f in inputs.features}
    for feature in cell.features:
        row = by_feature[feature]
        if cell.sensitivity in ("bias_lower", "bias_upper"):
            row = replace(row, bias=row.lower if cell.sensitivity == "bias_lower" else row.upper)
        rows.append(row)
    return replace(inputs, features=tuple(rows))
