"""Paired feature matrices with fixed participant labels and mounting blocks."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from scriptkin.consequence.inputs import BenchInputs, Coefficients
from scriptkin.consequence.plan import LIMITS, CellSpec
from scriptkin.recording import FloatArray, IntArray


@dataclass(frozen=True)
class MatrixCohort:
    x: FloatArray
    y: IntArray
    participant: tuple[str, ...]
    site: IntArray
    device: IntArray
    block: IntArray


@dataclass(frozen=True)
class ObservationVersions:
    reference: FloatArray
    uncorrected: FloatArray
    corrected: FloatArray | None
    negative_cv: int


def generate_cohort(cell: CellSpec, *, seed: int) -> MatrixCohort:
    streams = [np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(5)]
    labels, traits, sessions, assignment, mounting = streams
    y = (labels.random(cell.n) < cell.prevalence).astype(np.int64)
    scales = np.array([LIMITS[feature] / (np.sqrt(2) * norm.ppf(0.55)) for feature in cell.features])
    x = scales * (
        cell.effect * (y[:, None, None] - cell.prevalence)
        + np.sqrt(cell.participant_share) * traits.normal(size=(cell.n, 1, len(scales)))
        + np.sqrt(1 - cell.participant_share) * sessions.normal(size=(cell.n, 2, len(scales)))
    )
    probability = np.full(cell.n, 0.5)
    if cell.scenario == "all_w":
        probability[:] = 1
    elif cell.scenario == "confounded":
        probability = np.where(y == 1, cell.association, 1 - cell.association)
    device = (assignment.random(cell.n) < probability).astype(np.int64)
    site = np.arange(cell.n, dtype=np.int64) % 3
    block = np.empty(cell.n, dtype=np.int64)
    for s in range(3):
        rows = np.flatnonzero(site == s)
        block[mounting.permutation(rows)] = np.arange(len(rows)) % 6
    return MatrixCohort(x, y, tuple(f"p{i:05d}" for i in range(cell.n)), site, device, block)


def correct_w(z: FloatArray, device: IntArray, terms: Coefficients, u: float, v: float) -> FloatArray:
    return np.where(
        device[:, None] == 1, terms.alpha + terms.beta * z + terms.gamma_u * u + terms.gamma_v * v, z
    )


def observe(
    cohort: MatrixCohort, inputs: BenchInputs, *, seed: int, floor_cv: bool = False
) -> ObservationVersions:
    rng = np.random.default_rng(seed)
    reference = np.empty_like(cohort.x)
    raw = np.empty_like(cohort.x)
    corrected = np.empty_like(cohort.x)
    negative = 0
    available = True
    for j, f in enumerate(inputs.features):
        run = rng.normal(size=cohort.x.shape[:2])
        mount = rng.normal(size=(2, 6))
        ideal = cohort.x[:, :, j] + f.center
        p = ideal + np.sqrt(f.mount_p) * mount[0, cohort.block, None] + np.sqrt(f.run_p) * run
        w = ideal - f.bias + np.sqrt(f.mount_w) * mount[1, cohort.block, None] + np.sqrt(f.run_w) * run
        reference[:, :, j] = p
        raw[:, :, j] = np.where(cohort.device[:, None] == 1, w, p)
        if f.feature == "bench.speed_cv":
            negative += int(np.sum(raw[:, :, j] < 0))
            if floor_cv:
                reference[:, :, j] = np.maximum(p, 0)
                raw[:, :, j] = np.maximum(raw[:, :, j], 0)
        if f.correction is None:
            available = False
        else:
            program = f.selection.program
            corrected[:, :, j] = correct_w(
                raw[:, :, j],
                cohort.device,
                f.correction,
                float(np.log(program.speed_mm_s / 20)),
                float(np.log(program.scale)),
            )
    return ObservationVersions(reference, raw, corrected if available else None, negative)
