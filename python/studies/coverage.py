"""Null interval coverage, rejection rate, and false refusals of `screening` on null matrix cohorts.

DESIGN section 7.4 quotes the numbers that this script prints. Run it from the repository root:

    UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen python -I python/studies/coverage.py

UV_PROJECT_ENVIRONMENT keeps the Python environment out of the repository; `./check` uses the folder
`<build folder>/venv`. The script writes `studies/coverage.json` into the build folder of `./check`:
`$SCRIPT_BUILD`, or `~/Library/Caches/SCRIPT/<checkout>-<hash>/`. It runs the cohorts in parallel on every
core. Two runs on an 8-core Apple silicon Mac took 5.4 and 6.8 minutes and gave the same numbers.

Cohorts and seeds. A null matrix cohort has 120 participants at one site, three independent standard
normal features, and labels that are cases with probability 0.3, independent of the features. Cohort i
draws its features and then its labels from `numpy.random.default_rng(i)`, and `screening` gets seed i,
for i = 0 to 159. Both designs use the same 160 cohorts.

Plan. Five folds, three inner folds, a logistic model with the grid (10, 100), 20 permutations, and 300
bootstrap draws, with 1 repeat and with 5 repeats.

Measures for each design:

- Refusals. A null cohort that the null check refuses is a false refusal. The script counts the refusals
  of the harness band, which uses the larger of sigma0 and the spread of the null AUCs, and of a band from
  the spread alone. A cohort outside the harness band is also outside the narrower band of the spread alone.
- Coverage of 0.5 by the widened AUC interval of the report, and by the participant bootstrap of fixed
  out-of-fold predictions before the widening. The site subgroup holds every tested participant of a
  one-site cohort, so its interval is that bootstrap, drawn from the same bootstrap stream.
- Rejection rate: the share of cohorts with a permutation p-value of 0.05 or less.
- Skewness of the null AUCs, each centred on the mean of its cohort and then pooled.
- Two spreads over sigma0, the null standard deviation for one repeat that the fold class counts give.
  Stratified folds have the same class counts for every split seed. The first is the standard deviation
  of the null AUCs of a cohort, averaged over the cohorts. The second is the standard deviation of the
  observed AUC over the cohorts, which are all null, so it is the true null spread of the design.

Coverage, the rejection rate, the skewness, and the spreads use the cohorts that the harness does not
refuse.
"""

from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import skew
from scipy.stats import t as t_distribution

from scriptkin import __version__
from scriptkin.evaluate.metrics import fold_auc_null_sd
from scriptkin.evaluate.plan import AnalysisPlan, ModelSpec, Target, column
from scriptkin.evaluate.report import Refused, RefusedCode
from scriptkin.evaluate.screening import grouped_folds, screening
from scriptkin.features import FeatureRow, FeatureTable, Provenance, catalog_digest
from scriptkin.labels import Label
from scriptkin.quality import DEFAULT_QC
from scriptkin.recording import BlockOutcome, IntArray
from scriptkin.signal import DEFAULT_PREPROCESSING

COHORTS = range(160)
REPEATS = (1, 5)
PARTICIPANTS = 120
PREVALENCE = 0.3
FEATURES = ("kinematic.speed_mean", "kinematic.jerk_mean", "spatial.trace_rmse")
TASK = "spiral-trace"


def plan(repeats: int) -> AnalysisPlan:
    return AnalysisPlan(
        Target(),
        tuple(column(TASK, f) for f in FEATURES),
        model=ModelSpec(grid=(10.0, 100.0)),
        prevalence=PREVALENCE,
        repeats=repeats,
        permutations=20,
        bootstrap=300,
    )


def cohort(seed: int) -> tuple[FeatureTable, list[Label], IntArray]:
    """One null matrix cohort: a feature table with one spiral trial per participant, its labels, and y."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((PARTICIPANTS, len(FEATURES)))
    y = (rng.random(PARTICIPANTS) < PREVALENCE).astype(np.int64)
    rows = tuple(
        FeatureRow(
            participant=f"P{i:05d}",
            session=f"S{i:05d}",
            site="S1",
            administration="supervised",
            age_band="18-24",
            visit=1,
            opened_at="2026-01-05T09:00:00Z",
            device="iPad16,3",
            task=TASK,
            trial=0,
            attempt=1,
            hand="right",
            accommodations=(),
            qc=(),
            cells=dict(zip(FEATURES, map(float, x[i]), strict=True)),
        )
        for i in range(PARTICIPANTS)
    )
    provenance = Provenance("coverage study", DEFAULT_QC, DEFAULT_PREPROCESSING, catalog_digest(), (), ())
    labels = [
        Label(
            participant=f"P{i:05d}",
            session=f"S{i:05d}",
            instrument="phq9",
            version="1.0",
            locale="en",
            administration="supervised",
            block=0,
            block_time=0.0,
            boot="B",
            outcome=BlockOutcome.COMPLETED,
            scale="total",
            score=15.0 if y[i] else 3.0,
        )
        for i in range(PARTICIPANTS)
    ]
    return FeatureTable(rows, provenance), labels, y


def sigma0(y: IntArray, folds: int) -> float:
    numbers = np.full(len(y), -1, dtype=np.int64)
    for number, (_, test) in enumerate(grouped_folds(y, [str(i) for i in range(len(y))], folds, 0)):
        numbers[test] = number
    return fold_auc_null_sd(numbers, y)


@dataclass(frozen=True)
class Run:
    seed: int
    repeats: int
    refused: bool
    auc: float
    null_aucs: tuple[float, ...]
    sigma0: float
    bootstrap_covers: bool
    widened_covers: bool
    rejected: bool


def run(task: tuple[int, int]) -> Run:
    seed, repeats = task
    table, labels, y = cohort(seed)
    the_plan = plan(repeats)
    floor = sigma0(y, the_plan.folds)
    result = screening(table, labels, plan=the_plan, seed=seed)
    if isinstance(result, Refused):
        if result.code != RefusedCode.NULL_CHECK:
            raise RuntimeError(f"cohort {seed}, {repeats} repeats: {result}")
        return Run(seed, repeats, True, math.nan, (), floor, False, False, False)
    spread = float(np.std(result.null.aucs, ddof=1))
    if result.null.sd > spread and not math.isclose(result.null.sd, floor, rel_tol=1e-9):
        raise RuntimeError(f"cohort {seed}: sigma0 {floor} differs from the report floor {result.null.sd}.")
    site = next(s for s in result.subgroups if s.attribute == "site")
    if site.tested != result.tested or site.auc.point != result.auc.point:
        raise RuntimeError(f"cohort {seed}: the site subgroup is not the whole cohort.")
    return Run(
        seed,
        repeats,
        False,
        result.auc.point,
        result.null.aucs,
        floor,
        bool(site.auc.low <= 0.5 <= site.auc.high),
        bool(result.auc.low <= 0.5 <= result.auc.high),
        result.auc.p_value <= 0.05,
    )


def outside_spread_band(aucs: tuple[float, ...]) -> bool:
    """Whether the mean null AUC is outside 0.5 +/- t(0.995, P - 1) * sd / sqrt(P) with sd from the AUCs."""
    count = len(aucs)
    half = float(t_distribution.ppf(0.995, count - 1)) * float(np.std(aucs, ddof=1)) / math.sqrt(count)
    return abs(float(np.mean(aucs)) - 0.5) > half


def share(flags: list[bool]) -> dict[str, Any]:
    return {"count": sum(flags), "of": len(flags), "share": sum(flags) / len(flags)}


def summary(runs: list[Run]) -> dict[str, Any]:
    kept = [r for r in runs if not r.refused]
    centred = np.concatenate([np.asarray(r.null_aucs) - np.mean(r.null_aucs) for r in kept])
    return {
        "refusedByTheHarnessBand": share([r.refused for r in runs]),
        "refusedByTheSpreadAlone": share([r.refused or outside_spread_band(r.null_aucs) for r in runs]),
        "bootstrapCovers": share([r.bootstrap_covers for r in kept]),
        "widenedCovers": share([r.widened_covers for r in kept]),
        "rejected": share([r.rejected for r in kept]),
        "nullSkewness": float(skew(centred)),
        "nullSdOverSigma0": float(np.mean([np.std(r.null_aucs, ddof=1) / r.sigma0 for r in kept])),
        "aucSdOverSigma0": float(np.std([r.auc for r in kept], ddof=1) / np.mean([r.sigma0 for r in kept])),
    }


def build_folder() -> Path:
    """The build folder of `./check`."""
    configured = os.environ.get("SCRIPT_BUILD")
    if configured:
        return Path(configured)
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha1(str(root).encode(), usedforsecurity=False).hexdigest()[:8]
    return Path.home() / "Library" / "Caches" / "SCRIPT" / f"{root.name}-{digest}"


def percent(value: dict[str, Any]) -> str:
    return f"{value['share']:.0%} ({value['count']} of {value['of']})"


def main() -> None:
    started = time.monotonic()
    tasks = [(seed, repeats) for repeats in REPEATS for seed in COHORTS]
    with ProcessPoolExecutor(mp_context=multiprocessing.get_context("spawn")) as pool:
        runs = list(pool.map(run, tasks, chunksize=4))
    results = {repeats: summary([r for r in runs if r.repeats == repeats]) for repeats in REPEATS}
    seconds = time.monotonic() - started
    report = {
        "library": f"scriptkin {__version__}",
        "cohorts": f"seeds {COHORTS.start} to {COHORTS.stop - 1}",
        "plans": {str(repeats): plan(repeats).to_json() for repeats in REPEATS},
        "results": {str(repeats): result for repeats, result in results.items()},
        "runs": [asdict(r) for r in runs],
        "seconds": round(seconds),
        "cores": os.cpu_count(),
    }
    out = build_folder() / "studies" / "coverage.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    for repeats, result in results.items():
        print(f"{repeats} repeat(s), {len(COHORTS)} null cohorts")
        print(f"  refused by the harness band:         {percent(result['refusedByTheHarnessBand'])}")
        print(f"  refused by the null spread alone:    {percent(result['refusedByTheSpreadAlone'])}")
        print(f"  bootstrap interval covers 0.5:       {percent(result['bootstrapCovers'])}")
        print(f"  widened interval covers 0.5:         {percent(result['widenedCovers'])}")
        print(f"  permutation p-value 0.05 or less:    {percent(result['rejected'])}")
        print(f"  skewness of the null AUCs:           {result['nullSkewness']:.2f}")
        print(f"  null standard deviation / sigma0:    {result['nullSdOverSigma0']:.3f}")
        print(f"  AUC standard deviation / sigma0:     {result['aucSdOverSigma0']:.3f}")
    print(f"{seconds / 60:.1f} minutes on {os.cpu_count()} cores. Report: {out}")


if __name__ == "__main__":
    main()
