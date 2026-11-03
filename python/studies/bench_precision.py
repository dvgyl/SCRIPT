"""Precision of the bench design in `docs/BENCH_PROTOCOL.md`: mountings, runs, feature decisions, correction.

Run it from the repository root:

    UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen \\
        python -I python/studies/bench_precision.py

Options. `--datasets N` (default 20000) and `--seed S` (default 20261103). `--sigma-run X` and
`--sigma-mount Y`, in units of L, replace the grids by one value each, for the rerun with the interim
estimates of protocol section 6.6. `--fail-share Q` replaces the grid of failed-run shares. The script
writes `studies/bench_precision.json` into the build folder of `./check` (`$SCRIPT_BUILD`, or
`~/Library/Caches/SCRIPT/<checkout>-<hash>/`) and prints the tables that the protocol quotes.

Model. Every quantity is in units of the equivalence limit L of one feature on its comparison scale, so
one table serves every feature. A run value is mu_d + b_dmp + e_dmpr: the device mean, a mounting
effect b ~ N(0, sigma_mount^2), and run noise e ~ N(0, sigma_run^2). The mounting effect is either
shared by all programs of one mounting (structure `shared`) or drawn per program (structure
`specific`). Device P has mu_P = mu_W + bias in every program. The analysis takes the mounting means
per program, forms the Welch interval of the P minus W difference, and tests equivalence by the two
one-sided tests (TOST), as protocol sections 11.2 and 11.3 define.

Decisions. A program is equivalent at level alpha when its TOST p-value is at most alpha, which is the
(1 - 2 alpha) interval inside [-L, L]. A feature is equivalent when every program of its set is
estimable and equivalent. The sets are every main program, the loops programs, and the oscillation
programs, read from `bench_programs.py`. The table gives each feature decision at alpha 0.05 and at
alpha 0.05 / 6, the first Holm step across the six features of the equivalence set, so the two
columns bound the power of the Holm procedure.

Failed runs. With a fail share q, each planned run fails independently with probability q. A failed run
gets one rerun, with at most RERUN_CAP reruns per mounting and program, and a rerun fails with the
same probability. A program is estimable when every mounting keeps at least MIN_VALID_RUNS valid runs.

Correction. The correction table simulates the correction of protocol section 11.5 with
leave-one-mounting-out validation on the main programs. The W truth of a program is a function of its
covariates u = ln(speed / 20 mm/s) and v = ln(scale). The P truth is alpha + beta W + gamma u +
delta_p with three truths: `linear` (gamma 0, delta 0), `speed` (gamma 0.5 / ln 2, a difference that
grows by 0.5 L per doubling of the speed), and `program` (delta_p alternating +/- 0.5 L, which no
covariate explains). The fit removes the covariates from both devices by least squares, fits the
Deming slope on the residuals with lambda from the variance components, and predicts the held-out
mounting. The table reports the share of datasets whose held-out residual interval lies inside the
limit for every program, the largest true residual bias of the fit on all mountings, and the
jackknife standard error of the slope over the folds.

Seeds. `numpy.random.SeedSequence(seed)` spawns one child per cell in the order of the loops below, so
a cell gives the same numbers whichever cells run with it.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
from scipy.stats import t as t_distribution

from scriptkin.recording import FloatArray

SIGMA_RUN = (0.25, 0.5)
SIGMA_MOUNT = (0.1, 0.25, 0.5)
BIAS = (0.0, 0.5, 1.0)
DESIGNS = ((6, 5), (8, 5))
STRUCTURES = ("shared", "specific")
FEATURES = 6
ALPHAS = {"alpha": 0.05, "holm": 0.05 / FEATURES}
FAIL_SHARES = (0.05, 0.1)
MIN_VALID_RUNS = 4
RERUN_CAP = 2
ALPHA_TRUE, BETA_TRUE = 0.5, 1.05
TRUTHS = {"linear": (0.0, 0.0), "speed": (0.5 / math.log(2), 0.0), "program": (0.0, 0.5)}
CORRECTION_SIGMA_MOUNT = (0.1, 0.25, 0.5)
VARIANCE_FLOOR = 1e-6


def _load_programs() -> ModuleType:
    path = Path(__file__).with_name("bench_programs.py")
    spec = importlib.util.spec_from_file_location("bench_programs", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_programs"] = module
    spec.loader.exec_module(module)
    return module


PROGRAMS = _load_programs().MAIN
COVARIATE_U = np.array([math.log(d.speed_mm_s / 20.0) for d in PROGRAMS])
COVARIATE_V = np.array([math.log(d.scale) for d in PROGRAMS])
GEOMETRY_OFFSET = {"loops": 0.0, "spiral": 1.0, "line": -1.0}
TRUTH_W = 8 * COVARIATE_U - 6 * COVARIATE_V + np.array([GEOMETRY_OFFSET[d.geometry] for d in PROGRAMS])
FEATURE_SETS = {
    "all": [i for i, _ in enumerate(PROGRAMS)],
    "loops": [i for i, d in enumerate(PROGRAMS) if d.geometry == "loops"],
    "oscillation": [i for i, d in enumerate(PROGRAMS) if d.geometry == "line"],
}


@dataclass(frozen=True)
class Cell:
    mountings: int
    runs: int
    sigma_run: float
    sigma_mount: float
    bias: float
    structure: str
    fail_share: float
    half_width_median: float
    per_program: float
    estimable: float
    decisions: dict[str, dict[str, float]]


@dataclass(frozen=True)
class CorrectionCell:
    mountings: int
    runs: int
    sigma_run: float
    sigma_mount: float
    truth: str
    uncorrected_bias_max: float
    held_out_pass_all_programs: float
    true_residual_max_median: float
    slope_jackknife_se_median: float


@dataclass(frozen=True)
class Draw:
    """Mounting means (n, m, P), valid run counts (n, m, P), runs (n, m, P, r), and their validity."""

    means: FloatArray
    counts: FloatArray
    runs: FloatArray
    valid: FloatArray


def draw(
    rng: np.random.Generator,
    n: int,
    m: int,
    r: int,
    sigma_run: float,
    sigma_mount: float,
    shift: FloatArray | float,
    structure: str,
    fail_share: float,
) -> Draw:
    programs = len(PROGRAMS)
    effects = rng.normal(0.0, sigma_mount, (n, m, 1 if structure == "shared" else programs))
    runs = rng.normal(0.0, sigma_run, (n, m, programs, r + RERUN_CAP))
    if fail_share > 0:
        fails = rng.random(runs.shape) < fail_share
        planned = ~fails[..., :r]
        failed = r - planned.sum(axis=-1)
        rerun = np.arange(RERUN_CAP) < np.minimum(failed, RERUN_CAP)[..., None]
        valid = np.concatenate((planned, rerun & ~fails[..., r:]), axis=-1)
    else:
        valid = np.concatenate(
            (np.ones((n, m, programs, r), bool), np.zeros((n, m, programs, RERUN_CAP), bool)), axis=-1
        )
    counts = valid.sum(axis=-1)
    total = (runs * valid).sum(axis=-1)
    means = np.where(counts > 0, total / np.maximum(counts, 1), np.nan) + effects + shift
    return Draw(means, counts.astype(float), runs, valid.astype(float))


def welch(p: FloatArray, w: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Difference of means, its standard error, and the Welch degrees of freedom over axis 1."""
    m = p.shape[1]
    vp, vw = np.var(p, axis=1, ddof=1) / m, np.var(w, axis=1, ddof=1) / m
    se = np.sqrt(vp + vw)
    with np.errstate(divide="ignore", invalid="ignore"):
        df = (vp + vw) ** 2 / (vp**2 / (m - 1) + vw**2 / (m - 1))
    return p.mean(axis=1) - w.mean(axis=1), se, np.where(np.isfinite(df), df, 2 * (m - 1))


def tost_p(diff: FloatArray, se: FloatArray, df: FloatArray) -> FloatArray:
    """The TOST p-value against the limits -1 and 1: the larger of the two one-sided p-values."""
    with np.errstate(divide="ignore", invalid="ignore"):
        lower = np.asarray(t_distribution.sf((diff + 1.0) / se, df))
        upper = np.asarray(t_distribution.sf((1.0 - diff) / se, df))
    return np.maximum(lower, upper)


def equivalence_cell(
    seed: np.random.SeedSequence,
    n: int,
    m: int,
    r: int,
    sigma_run: float,
    sigma_mount: float,
    bias: float,
    structure: str,
    fail_share: float,
) -> Cell:
    rng = np.random.default_rng(seed)
    w = draw(rng, n, m, r, sigma_run, sigma_mount, 0.0, structure, fail_share)
    p = draw(rng, n, m, r, sigma_run, sigma_mount, bias, structure, fail_share)
    estimable = ((w.counts >= MIN_VALID_RUNS) & (p.counts >= MIN_VALID_RUNS)).all(axis=1)
    diff, se, df = welch(np.nan_to_num(p.means), np.nan_to_num(w.means))
    pvalue = tost_p(diff, se, df)
    half = np.asarray(t_distribution.ppf(0.95, df)) * se
    decisions = {
        name: {
            level: float(np.mean(np.all((pvalue[:, members] <= alpha) & estimable[:, members], axis=1)))
            for level, alpha in ALPHAS.items()
        }
        for name, members in FEATURE_SETS.items()
    }
    per_program = float(np.mean((pvalue[:, 0] <= ALPHAS["alpha"]) & estimable[:, 0]))
    return Cell(
        m,
        r,
        sigma_run,
        sigma_mount,
        bias,
        structure,
        fail_share,
        float(np.median(half[estimable[:, 0], 0])),
        per_program,
        float(np.mean(estimable.all(axis=1))),
        decisions,
    )


def error_variance(runs: FloatArray, means: FloatArray, r: int) -> FloatArray:
    """The variance of a mounting mean, sigma_mount^2 + sigma_run^2 / r, from the one-way ANOVA."""
    ms_within = runs.var(axis=3, ddof=1).mean(axis=(1, 2))
    ms_between = (r * means.var(axis=1, ddof=1)).mean(axis=1)
    sigma_mount2 = np.maximum(0.0, (ms_between - ms_within) / r)
    return sigma_mount2 + ms_within / r


@dataclass(frozen=True)
class Fit:
    """Covariate coefficients of each device (n, 3) and the Deming slope (n,)."""

    cw: FloatArray
    cp: FloatArray
    slope: FloatArray


def fit_correction(w: Draw, p: Draw, keep: list[int], r: int, z: FloatArray) -> Fit:
    """Partial the covariates out of both devices, then fit the Deming slope on the residuals."""
    n = w.means.shape[0]
    design = np.tile(z, (len(keep), 1))
    pseudo_inverse = np.linalg.pinv(design)
    wm, pm = w.means[:, keep].reshape(n, -1), p.means[:, keep].reshape(n, -1)
    cw, cp = wm @ pseudo_inverse.T, pm @ pseudo_inverse.T
    ew, ep = wm - cw @ design.T, pm - cp @ design.T
    lam = np.maximum(
        error_variance(p.runs[:, keep, :, :r], p.means[:, keep], r), VARIANCE_FLOOR
    ) / np.maximum(error_variance(w.runs[:, keep, :, :r], w.means[:, keep], r), VARIANCE_FLOOR)
    sxx, syy = np.sum(ew**2, axis=1), np.sum(ep**2, axis=1)
    sxy = np.sum(ew * ep, axis=1)
    d = syy - lam * sxx
    return Fit(cw, cp, (d + np.sqrt(d**2 + 4 * lam * sxy**2)) / (2 * sxy))


def predict(fit: Fit, w: FloatArray, z: FloatArray) -> FloatArray:
    """The corrected value of W values (n, P) at the covariates z (P, 3)."""
    return fit.cp @ z.T + fit.slope[:, None] * (w - fit.cw @ z.T)


def correction_cell(
    seed: np.random.SeedSequence, n: int, m: int, r: int, sigma_run: float, sigma_mount: float, truth: str
) -> CorrectionCell:
    rng = np.random.default_rng(seed)
    gamma, delta = TRUTHS[truth]
    programs = len(PROGRAMS)
    truth_p = (
        ALPHA_TRUE + BETA_TRUE * TRUTH_W + gamma * COVARIATE_U + delta * np.resize([1.0, -1.0], programs)
    )
    w = draw(rng, n, m, r, sigma_run, sigma_mount, TRUTH_W, "shared", 0.0)
    p = draw(rng, n, m, r, sigma_run, sigma_mount, truth_p, "shared", 0.0)
    z = np.column_stack((np.ones(programs), COVARIATE_U, COVARIATE_V))
    residuals, slopes = np.empty((n, m, programs)), np.empty((n, m))
    for k in range(m):
        fit = fit_correction(w, p, [i for i in range(m) if i != k], r, z)
        residuals[:, k] = p.means[:, k] - predict(fit, w.means[:, k], z)
        slopes[:, k] = fit.slope
    mean = residuals.mean(axis=1)
    half = float(t_distribution.ppf(0.95, m - 1)) * residuals.std(axis=1, ddof=1) / math.sqrt(m)
    inside = np.all((mean - half >= -1.0) & (mean + half <= 1.0), axis=1)
    full = fit_correction(w, p, list(range(m)), r, z)
    true_residual = truth_p - predict(full, np.broadcast_to(TRUTH_W, (n, programs)), z)
    jackknife = np.sqrt((m - 1) / m * np.sum((slopes - slopes.mean(axis=1, keepdims=True)) ** 2, axis=1))
    return CorrectionCell(
        m,
        r,
        sigma_run,
        sigma_mount,
        truth,
        float(np.max(np.abs(truth_p - TRUTH_W))),
        float(np.mean(inside)),
        float(np.median(np.max(np.abs(true_residual), axis=1))),
        float(np.median(jackknife)),
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
        description="Precision of the bench design: mountings, runs, feature decisions, correction."
    )
    parser.add_argument("--datasets", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=20261103)
    parser.add_argument("--sigma-run", type=float, help="one run SD in units of L in place of the grid")
    parser.add_argument(
        "--sigma-mount", type=float, help="one mounting SD in units of L in place of the grid"
    )
    parser.add_argument("--fail-share", type=float, help="one failed-run share in place of the grid")
    args = parser.parse_args()
    sigma_runs = (args.sigma_run,) if args.sigma_run is not None else SIGMA_RUN
    sigma_mounts = (args.sigma_mount,) if args.sigma_mount is not None else SIGMA_MOUNT
    fail_shares = (args.fail_share,) if args.fail_share is not None else FAIL_SHARES
    seeds = iter(np.random.SeedSequence(args.seed).spawn(10_000))
    cells = [
        equivalence_cell(next(seeds), args.datasets, m, r, sigma_run, sigma_mount, bias, structure, 0.0)
        for m, r in DESIGNS
        for sigma_run in sigma_runs
        for sigma_mount in sigma_mounts
        for bias in BIAS
        for structure in STRUCTURES
    ]
    cells += [
        equivalence_cell(
            next(seeds), args.datasets, 6, 5, sigma_runs[0], sigma_mounts[0], bias, "specific", q
        )
        for q in fail_shares
        for bias in (0.0, 0.5)
    ]
    corrections = [
        correction_cell(next(seeds), args.datasets // 4, 6, 5, sigma_runs[0], sigma_mount, truth)
        for sigma_mount in (sigma_mounts if args.sigma_mount is not None else CORRECTION_SIGMA_MOUNT)
        for truth in TRUTHS
    ]
    report: dict[str, Any] = {
        "seed": args.seed,
        "datasets": args.datasets,
        "units": "equivalence limit L",
        "programs": [d.id for d in PROGRAMS],
        "feature_sets": FEATURE_SETS,
        "equivalence": [asdict(c) for c in cells],
        "correction": [asdict(c) for c in corrections],
    }
    out = build_folder() / "studies" / "bench_precision.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    sets = "\t".join(f"{name} {level}" for name in FEATURE_SETS for level in ALPHAS)
    print("Share of datasets declared equivalent; values in units of L; alpha 0.05, holm 0.05 / 6")
    print(f"m x r\tsigma_run\tsigma_mount\tbias\tstructure\tfail\thalf-width\testimable\tper program\t{sets}")
    for c in cells:
        decisions = "\t".join(f"{c.decisions[name][level]:.3f}" for name in FEATURE_SETS for level in ALPHAS)
        print(
            f"{c.mountings} x {c.runs}\t{c.sigma_run:g}\t{c.sigma_mount:g}\t{c.bias:g}\t{c.structure}\t"
            f"{c.fail_share:g}\t{c.half_width_median:.3f}\t{c.estimable:.3f}\t{c.per_program:.3f}\t{decisions}"
        )
    print("\nCorrection with leave-one-mounting-out validation (alpha 0.5 L, beta 1.05, covariates u and v)")
    print(
        "m x r\tsigma_run\tsigma_mount\ttruth\tuncorrected max bias\theld-out pass\ttrue residual\tslope SE"
    )
    for c in corrections:
        print(
            f"{c.mountings} x {c.runs}\t{c.sigma_run:g}\t{c.sigma_mount:g}\t{c.truth}\t"
            f"{c.uncorrected_bias_max:.2f}\t{c.held_out_pass_all_programs:.3f}\t"
            f"{c.true_residual_max_median:.3f}\t{c.slope_jackknife_se_median:.4f}"
        )
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
