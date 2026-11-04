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
`specific`). Device P has mu_P = mu_W + bias in every program. The script simulates the data only. Every
statistic comes from `scriptkin.bench.stats`, the module of the analysis: the mounting means, the bias
with its Welch interval, the two one-sided tests (TOST), the feature p-value, and the correction.

Decisions. A program is equivalent at level alpha when its TOST p-value is at most alpha, which is the
(1 - 2 alpha) interval inside [-L, L]. A feature is equivalent when its p-value, the largest over the
programs of its set, is at most alpha. A program that is not estimable has p = 1. The sets are every main
program, the loops programs, and the oscillation programs, read from `bench_programs.py`. The table
gives each feature decision at alpha 0.05 and at the first Holm level across the six features of the
equivalence set, 0.05 / 6, so the two columns bound the power of the Holm procedure.

Failed runs. With a fail share q, each planned run fails independently with probability q. A failed run
gets one rerun, with at most RERUN_CAP reruns per mounting and program, and a rerun fails with the
same probability. A program is estimable when every mounting keeps at least `stats.MIN_VALID_RUNS` valid
runs.

Correction. The correction tables simulate the correction of protocol section 11.5 with
leave-one-mounting-out validation. Each program set has its model: `covariates` on every main program,
`slope` on the loops programs, and `shift` on the line programs. The W truth of a program is a function
of its covariates u = ln(speed / 20 mm/s) and v = ln(scale) and of its geometry. The P truth is alpha +
beta W + gamma u + delta_p with three truths: `linear` (gamma 0, delta 0), `speed` (gamma 0.5 / ln 2, a
difference that grows by 0.5 L per doubling of the speed), and `program` (delta_p alternating +/- 0.5 L
over the programs of the set, which no covariate explains). On the line programs u = 0, so the speed
truth equals the linear truth there, and the line set runs the linear and the program truths only. The
tables report the share of datasets whose held-out residual interval lies inside the limit for every
program, the largest true residual bias of the fit on all mountings, and the jackknife standard error
of the slope over the folds. The second table also gives the median and the quartiles of the slope.

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

from scriptkin.bench import stats
from scriptkin.recording import FloatArray

LIMIT = 1.0
SIGMA_RUN = (0.25, 0.5)
SIGMA_MOUNT = (0.1, 0.25, 0.5)
BIAS = (0.0, 0.5, 1.0)
DESIGNS = ((6, 5), (8, 5))
STRUCTURES = ("shared", "specific")
FEATURES = 6
ALPHAS = {"alpha": stats.ALPHA, "holm": float(stats.holm_thresholds(stats.ALPHA, FEATURES)[0])}
FAIL_SHARES = (0.05, 0.1)
RERUN_CAP = 2
ALPHA_TRUE, BETA_TRUE = 0.5, 1.05
TRUTHS = {"linear": (0.0, 0.0), "speed": (0.5 / math.log(2), 0.0), "program": (0.0, 0.5)}
CORRECTION_SIGMA_MOUNT = (0.1, 0.25, 0.5)
CORRECTION_MODELS = {
    "all": stats.CorrectionModel.COVARIATES,
    "loops": stats.CorrectionModel.SLOPE,
    "oscillation": stats.CorrectionModel.SHIFT,
}
CORRECTION_TRUTHS = {"all": tuple(TRUTHS), "loops": tuple(TRUTHS), "oscillation": ("linear", "program")}


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
COVARIATE_U, COVARIATE_V = stats.covariates(PROGRAMS)
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
    slope_jackknife_se_median: float | None
    """None for a model with the slope fixed at 1."""
    program_set: str
    model: str
    slope_quartiles: tuple[float, float, float] | None


def draw(
    rng: np.random.Generator,
    n: int,
    m: int,
    r: int,
    sigma_run: float,
    sigma_mount: float,
    shift: FloatArray,
    structure: str,
    fail_share: float,
) -> FloatArray:
    """The run values (n, P, m, r + RERUN_CAP) of one device for the P programs of shift (P,).

    NaN marks a failed run or an unused rerun.
    """
    programs = len(shift)
    effects = rng.normal(0.0, sigma_mount, (n, m, 1 if structure == "shared" else programs))
    noise = rng.normal(0.0, sigma_run, (n, m, programs, r + RERUN_CAP))
    if fail_share > 0:
        fails = rng.random(noise.shape) < fail_share
        planned = ~fails[..., :r]
        failed = r - planned.sum(axis=-1)
        rerun = np.arange(RERUN_CAP) < np.minimum(failed, RERUN_CAP)[..., None]
        valid = np.concatenate((planned, rerun & ~fails[..., r:]), axis=-1)
    else:
        valid = np.arange(r + RERUN_CAP) < r
    values = noise + effects[..., None] + shift[:, None]
    return np.where(valid, values, np.nan).transpose(0, 2, 1, 3)


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
    w = draw(rng, n, m, r, sigma_run, sigma_mount, np.zeros(len(PROGRAMS)), structure, fail_share)
    p = draw(rng, n, m, r, sigma_run, sigma_mount, np.full(len(PROGRAMS), bias), structure, fail_share)
    difference = stats.bias(p, w)
    pvalue = stats.tost_p(difference, LIMIT)
    decisions = {
        name: {
            level: float(np.mean(stats.feature_p(pvalue[:, members]) <= alpha))
            for level, alpha in ALPHAS.items()
        }
        for name, members in FEATURE_SETS.items()
    }
    estimable = difference.estimable
    return Cell(
        m,
        r,
        sigma_run,
        sigma_mount,
        bias,
        structure,
        fail_share,
        float(np.median(difference.half_width[estimable[:, 0], 0])),
        float(np.mean(pvalue[:, 0] <= ALPHAS["alpha"])),
        float(np.mean(estimable.all(axis=1))),
        decisions,
    )


def correction_cell(
    seed: np.random.SeedSequence,
    n: int,
    m: int,
    r: int,
    sigma_run: float,
    sigma_mount: float,
    truth: str,
    program_set: str,
) -> CorrectionCell:
    rng = np.random.default_rng(seed)
    gamma, delta = TRUTHS[truth]
    members = FEATURE_SETS[program_set]
    u, v, truth_w = COVARIATE_U[members], COVARIATE_V[members], TRUTH_W[members]
    truth_p = ALPHA_TRUE + BETA_TRUE * truth_w + gamma * u + delta * np.resize([1.0, -1.0], len(members))
    w = draw(rng, n, m, r, sigma_run, sigma_mount, truth_w, "shared", 0.0)
    p = draw(rng, n, m, r, sigma_run, sigma_mount, truth_p, "shared", 0.0)
    model = CORRECTION_MODELS[program_set]
    correction = stats.correction(p, w, u, v, model=model)
    true_residual = truth_p - correction.fit.corrected(truth_w, u, v)
    q1, median, q3 = (float(q) for q in np.percentile(correction.fit.beta, [25, 50, 75]))
    return CorrectionCell(
        m,
        r,
        sigma_run,
        sigma_mount,
        truth,
        float(np.max(np.abs(truth_p - truth_w))),
        float(np.mean(correction.residual.within(LIMIT).all(axis=-1))),
        float(np.median(np.max(np.abs(true_residual), axis=1))),
        float(np.median(correction.se.beta)) if model.fits_slope else None,
        program_set,
        model.value,
        (q1, median, q3) if model.fits_slope else None,
    )


def _slope_se(c: CorrectionCell) -> str:
    return "fixed" if c.slope_jackknife_se_median is None else f"{c.slope_jackknife_se_median:.4f}"


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
        correction_cell(next(seeds), args.datasets // 4, 6, 5, sigma_runs[0], sigma_mount, truth, program_set)
        for program_set in FEATURE_SETS
        for sigma_mount in (sigma_mounts if args.sigma_mount is not None else CORRECTION_SIGMA_MOUNT)
        for truth in CORRECTION_TRUTHS[program_set]
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
        if c.program_set == "all":
            print(
                f"{c.mountings} x {c.runs}\t{c.sigma_run:g}\t{c.sigma_mount:g}\t{c.truth}\t"
                f"{c.uncorrected_bias_max:.2f}\t{c.held_out_pass_all_programs:.3f}\t"
                f"{c.true_residual_max_median:.3f}\t{_slope_se(c)}"
            )
    print("\nCorrection on the loops programs (model slope) and on the line programs (model shift)")
    print(
        "set\tmodel\tm x r\tsigma_run\tsigma_mount\ttruth\tuncorrected max bias\theld-out pass\t"
        "true residual\tslope median\tslope quartiles\tslope SE"
    )
    for c in corrections:
        if c.program_set != "all":
            q = c.slope_quartiles
            median, quartiles = (
                ("fixed", "fixed") if q is None else (f"{q[1]:.3f}", f"{q[0]:.3f} to {q[2]:.3f}")
            )
            print(
                f"{c.program_set}\t{c.model}\t{c.mountings} x {c.runs}\t{c.sigma_run:g}\t{c.sigma_mount:g}\t"
                f"{c.truth}\t{c.uncorrected_bias_max:.2f}\t{c.held_out_pass_all_programs:.3f}\t"
                f"{c.true_residual_max_median:.3f}\t{median}\t{quartiles}\t{_slope_se(c)}"
            )
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
