"""Rank, proportion, and calibration statistics shared by the evaluation modules."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
from scipy.special import expit, logit
from scipy.stats import norm, rankdata

from scriptkin.recording import FloatArray, IntArray
from scriptkin.signal import Estimate

Z95 = float(norm.ppf(0.975))


def auc(scores: FloatArray, y: IntArray) -> float:
    """The Mann-Whitney AUC with average ranks for ties. NaN when a class is absent."""
    positives = int(np.sum(y))
    negatives = len(y) - positives
    if positives == 0 or negatives == 0:
        return math.nan
    ranks = rankdata(scores)
    return float((np.sum(ranks[y == 1]) - positives * (positives + 1) / 2) / (positives * negatives))


def fold_auc(scores: FloatArray, folds: IntArray, y: IntArray, rows: IntArray) -> float:
    """AUC over case-control pairs that share a test fold, averaged over repeats.

    `scores` and `folds` have one line per repeat; a fold of -1 marks a row that was not tested. Each fold
    has its own fitted model, so a pair from two folds compares two models. Such pairs carry the training
    prevalence of each fold, which is negatively related to the test prevalence, and bias a pooled AUC
    below 0.5 when nothing is learnt. Pairs inside one fold do not. `rows` may repeat, as in a bootstrap.
    """
    per_repeat: list[float] = []
    for score, fold in zip(scores, folds, strict=True):
        s, f, labels = score[rows], fold[rows], y[rows]
        u = pairs = 0.0
        for number in np.unique(f[f >= 0]):
            inside = f == number
            positives = int(labels[inside].sum())
            negatives = int(inside.sum()) - positives
            if positives and negatives:
                ranks = rankdata(s[inside])
                u += float(ranks[labels[inside] == 1].sum()) - positives * (positives + 1) / 2
                pairs += positives * negatives
        if pairs:
            per_repeat.append(u / pairs)
    return float(np.mean(per_repeat)) if per_repeat else math.nan


def fold_auc_null_sd(folds: IntArray, y: IntArray) -> float:
    """The null standard deviation of `fold_auc` for one repeat with untied scores.

    Each fold AUC has variance (n1 + n0 + 1) / (12 n1 n0) under the null, and the folds are independent
    given their class counts, so the pair-weighted mean has the weighted sum of those variances.
    """
    weights: list[float] = []
    variances: list[float] = []
    for number in np.unique(folds[folds >= 0]):
        inside = folds == number
        positives = int(y[inside].sum())
        negatives = int(inside.sum()) - positives
        if positives and negatives:
            weights.append(positives * negatives)
            variances.append((positives + negatives + 1) / (12 * positives * negatives))
    w = np.asarray(weights) / np.sum(weights)
    return math.sqrt(float(np.sum(w**2 * np.asarray(variances))))


def bootstrap(
    statistic: Callable[[IntArray], float], y: IntArray, rows: IntArray, count: int, rng: np.random.Generator
) -> Estimate:
    """A statistic of `rows` with a 95% percentile interval from resampling participants within each class.

    The predictions stay fixed, so the interval is conditional on the fitted procedure.
    """
    point = statistic(rows)
    cases, controls = rows[y[rows] == 1], rows[y[rows] == 0]
    if math.isnan(point) or not len(cases) or not len(controls):
        return Estimate(point, math.nan, math.nan)
    draws = np.array(
        [
            statistic(np.concatenate((rng.choice(cases, len(cases)), rng.choice(controls, len(controls)))))
            for _ in range(count)
        ]
    )
    draws = draws[np.isfinite(draws)]
    if not len(draws):
        return Estimate(point, math.nan, math.nan)
    low, high = np.quantile(draws, (0.025, 0.975))
    return Estimate(point, float(low), float(high))


def wilson(successes: float, n: float, z: float = Z95) -> Estimate:
    """The Wilson score interval. `successes` and `n` may be expected counts."""
    if n <= 0:
        return Estimate(math.nan, math.nan, math.nan)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z / (1 + z * z / n) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return Estimate(p, max(0.0, centre - half), min(1.0, centre + half))


def logistic_fit(
    x: FloatArray, y: IntArray, offset: FloatArray | None = None, ridge: float = 0.0
) -> FloatArray:
    """Logistic regression of y on the columns of x by Newton steps. `ridge` penalizes all but column 0."""
    beta = np.zeros(x.shape[1])
    base = np.zeros(len(y)) if offset is None else offset
    penalty = ridge * np.eye(len(beta))
    penalty[0, 0] = 0.0
    for _ in range(100):
        p = expit(base + x @ beta)
        gradient = x.T @ (y - p) - penalty @ beta
        hessian = x.T @ (x * (p * (1 - p))[:, None]) + penalty
        step = np.linalg.solve(hessian + 1e-10 * np.eye(len(beta)), gradient)
        beta += step
        if np.max(np.abs(step)) < 1e-10:
            break
    return beta


def clipped_logit(p: FloatArray, eps: float = 1e-6) -> FloatArray:
    return np.asarray(logit(np.clip(p, eps, 1 - eps)))
