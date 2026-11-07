"""Fold-local AUC, weighted calibration, and paired Monte Carlo summaries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from scipy.stats import t

from scriptkin.consequence.validation import PredictionSet
from scriptkin.evaluate.metrics import clipped_logit, fold_auc, wilson
from scriptkin.evaluate.screening import calibration, net_benefit
from scriptkin.recording import FloatArray, IntArray

MATERIAL = {"auc": 0.05, "intercept": 0.20, "slope": 0.20, "mean_nb": 0.01}


@dataclass(frozen=True)
class MetricSet:
    values: Mapping[str, float]
    curves: tuple[dict[str, float], ...]
    calibration: tuple[dict[str, float], ...]


def weighted_auc(p: FloatArray, y: IntArray, folds: IntArray, weights: FloatArray) -> float:
    if np.all(weights == 1):
        return fold_auc(p[None, :], folds[None, :], y, np.arange(len(y)))
    total = pairs = 0.0
    for fold in np.unique(folds[folds >= 0]):
        a, b = np.flatnonzero((folds == fold) & (y == 1)), np.flatnonzero((folds == fold) & (y == 0))
        w = weights[a, None] * weights[b]
        total += float(np.sum(w * ((p[a, None] > p[b]) + 0.5 * (p[a, None] == p[b]))))
        pairs += float(w.sum())
    return total / pairs if pairs else float("nan")


def weighted_calibration(p: FloatArray, y: IntArray, w: FloatArray) -> tuple[float, float]:
    lp = clipped_logit(p)

    def fit(x: FloatArray, offset: FloatArray) -> FloatArray:
        def objective(beta: FloatArray) -> tuple[float, FloatArray]:
            z = offset + x @ beta
            return float(np.sum(w * (np.logaddexp(0, z) - y * z))), x.T @ (w * (expit(z) - y))

        result = minimize(objective, np.zeros(x.shape[1]), jac=True, method="BFGS", tol=1e-9)
        return np.asarray(result.x, dtype=float)

    intercept = float(fit(np.ones((len(y), 1)), lp)[0])
    slope = float("nan")
    if np.ptp(lp) > 1e-10:
        coefficient = fit(np.column_stack((np.ones(len(y)), lp)), np.zeros(len(y)))
        if np.max(np.abs(coefficient)) < 30:
            slope = float(coefficient[1])
    return intercept, slope


def measure(predictions: PredictionSet, *, prevalence: float, thresholds: FloatArray) -> MetricSet:
    cross = predictions.cross
    keep = cross.folds[0] >= 0
    p, y, w = cross.probabilities[0, keep], predictions.y[keep], predictions.weights[keep]
    intercept, slope = weighted_calibration(p, y, w)
    if np.all(w == 1) and np.isfinite(slope):
        c = calibration(p, y)
        intercept, slope = c.intercept, c.slope
    values = {
        "auc": weighted_auc(p, y, cross.folds[0, keep], w),
        "intercept": intercept,
        "slope": slope,
        "brier": float(np.average((p - y) ** 2, weights=w)),
    }
    shifted = cross.shifted(prevalence)[0, keep]
    curves = []
    for threshold in thresholds:
        positive = shifted >= threshold
        se = float(np.average(positive[y == 1], weights=w[y == 1]))
        fp = float(np.average(positive[y == 0], weights=w[y == 0]))
        nb = se * prevalence - fp * (1 - prevalence) * threshold / (1 - threshold)
        if np.all(w == 1):
            nb = net_benefit(shifted, y, float(threshold), prevalence).model
        curves.append(
            {
                "threshold": float(threshold),
                "model_nb": float(nb),
                "all_nb": float(prevalence - (1 - prevalence) * threshold / (1 - threshold)),
                "none_nb": 0.0,
            }
        )
    values["mean_nb"] = float(np.mean([r["model_nb"] for r in curves]))
    bins = []
    for number, rows in enumerate(np.array_split(np.argsort(p, kind="stable"), min(10, len(p)))):
        bins.append(
            {
                "bin": float(number),
                "mean_probability": float(np.average(p[rows], weights=w[rows])),
                "observed_share": float(np.average(y[rows], weights=w[rows])),
                "participant_weight": float(w[rows].sum()),
                "intercept": intercept,
                "slope": slope,
                "brier": values["brier"],
            }
        )
    return MetricSet(values, tuple(curves), tuple(bins))


def paired_contrasts(versions: Mapping[str, MetricSet]) -> tuple[dict[str, object], ...]:
    rows = []
    for metric, reference in versions["reference"].values.items():
        raw = versions["uncorrected"].values[metric]
        corrected = versions["corrected"].values[metric] if "corrected" in versions else float("nan")
        d_raw, d_corrected = raw - reference, corrected - reference
        rows.append(
            {
                "metric": metric,
                "reference": reference,
                "uncorrected": raw,
                "corrected": corrected,
                "D_raw": d_raw,
                "D_corrected": d_corrected,
                "removed": d_raw - d_corrected,
                "absolute_reduction": abs(d_raw) - abs(d_corrected),
                "material_raw": bool(abs(d_raw) >= MATERIAL.get(metric, float("inf")))
                if np.isfinite(d_raw)
                else None,
            }
        )
    return tuple(rows)


def summarize(rows: Sequence[Mapping[str, object]], *, planned: int = 200) -> dict[str, object]:
    ids = [r["replicate"] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("A summary repeats a replicate identifier.")
    values = np.array([float(str(r["estimate"])) for r in rows if r["estimate"] is not None])
    values = values[np.isfinite(values)]
    n = len(values)
    result: dict[str, object] = {
        "planned_R": planned,
        "usable_R": n,
        "status": "ok" if n >= 0.95 * planned else "incomplete",
    }
    for key in (
        "mean",
        "sd",
        "q025",
        "q975",
        "mcse",
        "mean_lower",
        "mean_upper",
        "material_share",
        "wilson_lower",
        "wilson_upper",
    ):
        result[key] = None
    if n:
        mean = float(values.mean())
        sd = float(values.std(ddof=1)) if n > 1 else float("nan")
        se = sd / np.sqrt(n)
        half = float(t.ppf(0.975, n - 1)) * se if n > 1 else float("nan")
        metric = str(rows[0].get("metric", ""))
        share = wilson(float(np.sum(np.abs(values) >= MATERIAL.get(metric, float("inf")))), n)
        result.update(
            mean=mean,
            sd=sd,
            q025=float(np.quantile(values, 0.025)),
            q975=float(np.quantile(values, 0.975)),
            mcse=se,
            mean_lower=mean - half,
            mean_upper=mean + half,
            material_share=share.point,
            wilson_lower=share.low,
            wilson_upper=share.high,
        )
    return result
