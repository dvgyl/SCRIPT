"""Precision of the bench design in `docs/BENCH_PROTOCOL.md`: mountings, runs, and the equivalence test.

Run it from the repository root:

    UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen \\
        python -I python/studies/bench_precision.py

Options: `--datasets N` (default 20000) and `--seed S` (default 20261103). The script writes
`studies/bench_precision.json` into the build folder of `./check` (`$SCRIPT_BUILD`, or
`~/Library/Caches/SCRIPT/<checkout>-<hash>/`) and prints the tables that the protocol quotes.

Model. Every quantity is in units of the equivalence limit L of one feature on its comparison scale,
so one table serves every feature. A run value is mu_d + b_dm + e_dmr: the device mean, a mounting
effect b_dm ~ N(0, sigma_mount^2) shared by all runs of that mounting, and run noise
e_dmr ~ N(0, sigma_run^2). Device P has mu_P = mu_W + bias. The analysis takes the mounting means,
forms the Welch 90% confidence interval of the P minus W difference, and declares equivalence when the
interval lies inside [-L, L] (the two one-sided tests procedure at alpha 0.05).

Variance components. The grid covers sigma_run in {0.1, 0.25, 0.5} L and sigma_mount in
{0, 0.1, 0.25, 0.5} L. The protocol states the rationale: a robot repeats a run far better than a
person, so run noise is a fraction of a human measurement error, and a remount can move a feature
by a comparable fraction. The pilot measures both before the lock and the script is rerun with the
measured values.

Correction. The second table simulates the per-feature Deming correction with leave-one-mounting-out
validation on eight programs whose true W values spread over +/- 8 L (two speeds and two sizes), with
P = alpha + beta * W (alpha 0.5 L, beta 1.05), and reports the residual bias of the held-out mountings.

Seeds. `numpy.random.SeedSequence(seed)` spawns one child per cell in the order of the loops below,
so a cell gives the same numbers whichever cells run with it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import t as t_distribution

from scriptkin.recording import FloatArray

SIGMA_RUN = (0.1, 0.25, 0.5)
SIGMA_MOUNT = (0.0, 0.1, 0.25, 0.5)
BIAS = (0.0, 0.25, 0.5, 1.0)
DESIGNS = ((3, 5), (6, 3), (6, 5), (8, 5), (6, 8))
PROGRAM_TRUE_W = (-8.0, -8.0, -7.0, -7.0, 7.0, 7.0, 8.0, 8.0)
ALPHA, BETA = 0.5, 1.05
CORRECTION_DESIGNS = ((3, 5), (6, 5))
CORRECTION_SIGMA_RUN = (0.25, 0.5)
CORRECTION_SIGMA_MOUNT = (0.1, 0.25, 0.5)


@dataclass(frozen=True)
class Cell:
    mountings: int
    runs: int
    sigma_run: float
    sigma_mount: float
    bias: float
    equivalent: float
    half_width_median: float
    half_width_formula: float


@dataclass(frozen=True)
class CorrectionCell:
    mountings: int
    runs: int
    sigma_run: float
    sigma_mount: float
    uncorrected_bias_max: float
    residual_sd: float
    residual_equivalent_all_programs: float


def welch_interval(p: FloatArray, w: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Return the difference of means and the 90% half-width from mounting means of shape (N, m)."""
    m = p.shape[1]
    vp, vw = np.var(p, axis=1, ddof=1) / m, np.var(w, axis=1, ddof=1) / m
    se = np.sqrt(vp + vw)
    with np.errstate(divide="ignore", invalid="ignore"):
        df = (vp + vw) ** 2 / (vp**2 / (m - 1) + vw**2 / (m - 1))
    df = np.where(np.isfinite(df), df, 2 * (m - 1))
    half = np.asarray(t_distribution.ppf(0.95, df)) * se
    return p.mean(axis=1) - w.mean(axis=1), half


def mounting_means(
    rng: np.random.Generator, n: int, m: int, r: int, sigma_run: float, sigma_mount: float, shift: float
) -> FloatArray:
    runs = rng.normal(0.0, sigma_run, size=(n, m, r)).mean(axis=2)
    return runs + rng.normal(0.0, sigma_mount, size=(n, m)) + shift


def equivalence_cell(
    seed: np.random.SeedSequence, n: int, m: int, r: int, sigma_run: float, sigma_mount: float, bias: float
) -> Cell:
    rng = np.random.default_rng(seed)
    w = mounting_means(rng, n, m, r, sigma_run, sigma_mount, 0.0)
    p = mounting_means(rng, n, m, r, sigma_run, sigma_mount, bias)
    diff, half = welch_interval(p, w)
    equivalent = float(np.mean((diff - half >= -1.0) & (diff + half <= 1.0)))
    formula = float(t_distribution.ppf(0.95, 2 * (m - 1))) * float(
        np.sqrt(2 * (sigma_mount**2 + sigma_run**2 / r) / m)
    )
    return Cell(m, r, sigma_run, sigma_mount, bias, equivalent, float(np.median(half)), formula)


def deming(x: FloatArray, y: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Deming slope and intercept with equal error variances, for rows of paired points."""
    xm, ym = x.mean(axis=1, keepdims=True), y.mean(axis=1, keepdims=True)
    sxx, syy = np.sum((x - xm) ** 2, axis=1), np.sum((y - ym) ** 2, axis=1)
    sxy = np.sum((x - xm) * (y - ym), axis=1)
    slope = (syy - sxx + np.sqrt((syy - sxx) ** 2 + 4 * sxy**2)) / (2 * sxy)
    return slope, ym[:, 0] - slope * xm[:, 0]


def correction_cell(
    seed: np.random.SeedSequence, n: int, m: int, r: int, sigma_run: float, sigma_mount: float
) -> CorrectionCell:
    rng = np.random.default_rng(seed)
    truth_w = np.asarray(PROGRAM_TRUE_W)
    truth_p = ALPHA + BETA * truth_w
    programs = len(truth_w)
    # Mounting effects are shared by all programs of one device mounting.
    mount_w = rng.normal(0.0, sigma_mount, size=(n, m, 1))
    mount_p = rng.normal(0.0, sigma_mount, size=(n, m, 1))
    w = truth_w + mount_w + rng.normal(0.0, sigma_run, size=(n, m, programs, r)).mean(axis=3)
    p = truth_p + mount_p + rng.normal(0.0, sigma_run, size=(n, m, programs, r)).mean(axis=3)
    residuals = np.empty((n, m, programs))
    for k in range(m):
        keep = [i for i in range(m) if i != k]
        slope, intercept = deming(w[:, keep, :].reshape(n, -1), p[:, keep, :].reshape(n, -1))
        residuals[:, k, :] = p[:, k, :] - (intercept[:, None] + slope[:, None] * w[:, k, :])
    mean = residuals.mean(axis=1)
    half = float(t_distribution.ppf(0.95, m - 1)) * np.std(residuals, axis=1, ddof=1) / np.sqrt(m)
    inside = np.all((mean - half >= -1.0) & (mean + half <= 1.0), axis=1)
    return CorrectionCell(
        m,
        r,
        sigma_run,
        sigma_mount,
        float(np.max(np.abs(truth_p - truth_w))),
        float(np.std(mean)),
        float(np.mean(inside)),
    )


def build_folder() -> Path:
    """The build folder of `./check`."""
    configured = os.environ.get("SCRIPT_BUILD")
    if configured:
        return Path(configured)
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha1(str(root).encode(), usedforsecurity=False).hexdigest()[:8]
    return Path.home() / "Library" / "Caches" / "SCRIPT" / f"{root.name}-{digest}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Precision of the bench design: mountings, runs, equivalence."
    )
    parser.add_argument("--datasets", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=20261103)
    args = parser.parse_args()
    cells: list[Cell] = []
    corrections: list[CorrectionCell] = []
    seeds = iter(np.random.SeedSequence(args.seed).spawn(10_000))
    for m, r in DESIGNS:
        for sigma_run in SIGMA_RUN:
            for sigma_mount in SIGMA_MOUNT:
                for bias in BIAS:
                    cells.append(
                        equivalence_cell(next(seeds), args.datasets, m, r, sigma_run, sigma_mount, bias)
                    )
    for m, r in CORRECTION_DESIGNS:
        for sigma_run in CORRECTION_SIGMA_RUN:
            for sigma_mount in CORRECTION_SIGMA_MOUNT:
                corrections.append(
                    correction_cell(next(seeds), args.datasets // 4, m, r, sigma_run, sigma_mount)
                )
    report: dict[str, Any] = {
        "seed": args.seed,
        "datasets": args.datasets,
        "units": "equivalence limit L",
        "equivalence": [asdict(c) for c in cells],
        "correction": [asdict(c) for c in corrections],
    }
    out = build_folder() / "studies" / "bench_precision.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print("Equivalence declared (share of datasets), by design and bias; values in units of L")
    print("mountings x runs\tsigma_run\tsigma_mount\thalf-width\t" + "\t".join(f"bias {b:g}" for b in BIAS))
    for m, r in DESIGNS:
        for sigma_run in SIGMA_RUN:
            for sigma_mount in SIGMA_MOUNT:
                row = [
                    c
                    for c in cells
                    if (c.mountings, c.runs, c.sigma_run, c.sigma_mount) == (m, r, sigma_run, sigma_mount)
                ]
                print(
                    f"{m} x {r}\t{sigma_run:g}\t{sigma_mount:g}\t{row[0].half_width_median:.3f}\t"
                    + "\t".join(f"{c.equivalent:.3f}" for c in row)
                )
    print("\nDeming correction with leave-one-mounting-out validation (alpha 0.5 L, beta 1.05)")
    print(
        "mountings x runs\tsigma_run\tsigma_mount\tuncorrected max bias\tresidual SD\tall programs equivalent"
    )
    for c in corrections:
        print(
            f"{c.mountings} x {c.runs}\t{c.sigma_run:g}\t{c.sigma_mount:g}\t{c.uncorrected_bias_max:.2f}\t"
            f"{c.residual_sd:.3f}\t{c.residual_equivalent_all_programs:.3f}"
        )
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
