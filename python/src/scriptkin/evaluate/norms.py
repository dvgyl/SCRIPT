"""Normative models: a covariate-conditional location and scale for each feature (DESIGN section 7).

The location and the log variance are both linear in the same design: an intercept, a restricted cubic
spline of age in years when `age` is a covariate, and indicator columns for each categorical covariate.
The fit alternates weighted least squares for the location with a regression of the log squared residual
for the scale. A centile is 100 * Phi((value - location) / scale). A row outside the covariate range of
the fitted sample gives a refusal. A deviation from the normative model is not a diagnosis.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from scriptkin.features import Cell
from scriptkin.quality import Refusal, RefusalCode
from scriptkin.recording import FloatArray

AGE = "age"
# E[log(chi-square with 1 degree of freedom)] = digamma(1/2) + log(2).
_LOG_CHI2_MEAN = -1.2703628454614782


@dataclass(frozen=True)
class NormRow:
    covariates: Mapping[str, float | str]
    values: Mapping[str, float]


@dataclass(frozen=True)
class Support:
    """The covariate range of the fitted sample: the age interval and the categorical levels."""

    age: tuple[float, float] | None
    levels: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class FeatureNorm:
    location: FloatArray
    log_variance: FloatArray
    n: int


@dataclass(frozen=True)
class Norms:
    covariates: tuple[str, ...]
    support: Support
    knots: tuple[float, ...]
    features: Mapping[str, FeatureNorm]


def _spline(age: FloatArray, knots: Sequence[float]) -> FloatArray:
    """Restricted cubic spline basis (Harrell): linear beyond the outer knots."""
    if len(knots) < 3:
        return age[:, None]
    t = np.asarray(knots)
    last, before = t[-1], t[-2]

    def cube(v: FloatArray) -> FloatArray:
        return np.maximum(v, 0.0) ** 3

    columns = [age]
    for tj in t[:-2]:
        term = (
            cube(age - tj)
            - cube(age - before) * (last - tj) / (last - before)
            + cube(age - last) * (before - tj) / (last - before)
        )
        columns.append(term / (last - t[0]) ** 2)
    return np.column_stack(columns)


def _design(
    norms_covariates: Sequence[str], support: Support, knots: Sequence[float], rows: Sequence[NormRow]
) -> FloatArray:
    parts: list[FloatArray] = [np.ones((len(rows), 1))]
    for name in norms_covariates:
        if name == AGE:
            parts.append(_spline(np.array([float(r.covariates[AGE]) for r in rows]), knots))
        else:
            levels = support.levels[name]
            parts.append(
                np.array(
                    [[float(r.covariates[name] == level) for level in levels[1:]] for r in rows]
                ).reshape(len(rows), -1)
            )
    return np.column_stack(parts)


def _fit(x: FloatArray, y: FloatArray) -> tuple[FloatArray, FloatArray]:
    n, p = x.shape
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    gamma = np.zeros(p)
    floor = 1e-12 * max(float(np.var(y)), 1e-300)
    for _ in range(4):
        residual = y - x @ beta
        gamma = np.linalg.lstsq(x, np.log(residual**2 + floor), rcond=None)[0]
        gamma[0] += math.log(n / (n - p)) - _LOG_CHI2_MEAN
        weight = np.exp(-(x @ gamma) / 2)
        beta = np.linalg.lstsq(x * weight[:, None], y * weight, rcond=None)[0]
    return beta, gamma


def fit_norms(rows: Sequence[NormRow], covariates: Sequence[str]) -> Norms:
    """Fit one normative model for every feature in the rows. Fit only on training rows."""
    support_levels: dict[str, tuple[str, ...]] = {}
    age_range: tuple[float, float] | None = None
    knots: tuple[float, ...] = ()
    for name in covariates:
        if name == AGE:
            ages = np.array([float(r.covariates[AGE]) for r in rows])
            age_range = (float(ages.min()), float(ages.max()))
            if len(rows) >= 30 and len(np.unique(ages)) >= 8:
                knots = tuple(float(q) for q in np.quantile(ages, (0.05, 0.35, 0.65, 0.95)))
        else:
            support_levels[name] = tuple(sorted({str(r.covariates[name]) for r in rows}))
    support = Support(age_range, support_levels)
    x = _design(covariates, support, knots, rows)
    features: dict[str, FeatureNorm] = {}
    for feature in sorted({f for r in rows for f in r.values}):
        y = np.array([r.values.get(feature, math.nan) for r in rows], dtype=float)
        finite = np.isfinite(y)
        if finite.sum() <= 2 * x.shape[1] + 2:
            continue
        beta, gamma = _fit(x[finite], y[finite])
        features[feature] = FeatureNorm(beta, gamma, int(finite.sum()))
    return Norms(tuple(covariates), support, knots, features)


def _outside(norms: Norms, row: NormRow) -> Refusal | None:
    for name in norms.covariates:
        value = row.covariates.get(name)
        if value is None:
            return Refusal(RefusalCode.UNDEFINED, f"The row has no {name}.")
        if name == AGE:
            low, high = norms.support.age or (math.nan, math.nan)
            if not low <= float(value) <= high:
                return Refusal(
                    RefusalCode.OUT_OF_RANGE, f"Age {value} is outside the normative range {low} to {high}."
                )
        elif str(value) not in norms.support.levels[name]:
            return Refusal(RefusalCode.OUT_OF_RANGE, f"{name} {value!r} is not in the normative sample.")
    return None


def deviations(norms: Norms, rows: Sequence[NormRow]) -> dict[str, FloatArray]:
    """Standardized deviations (value - location) / scale. NaN where a refusal applies."""
    inside = np.array([_outside(norms, r) is None for r in rows], dtype=bool)
    usable = [r for r, ok in zip(rows, inside, strict=True) if ok]
    result: dict[str, FloatArray] = {}
    x = _design(norms.covariates, norms.support, norms.knots, usable) if usable else np.empty((0, 0))
    for feature, model in norms.features.items():
        z = np.full(len(rows), np.nan)
        if usable:
            y = np.array([r.values.get(feature, math.nan) for r in usable], dtype=float)
            z[inside] = (y - x @ model.location) / np.exp(x @ model.log_variance / 2)
        result[feature] = z
    return result


def centiles(norms: Norms, rows: Sequence[NormRow]) -> list[dict[str, Cell]]:
    """The centile (0 to 100) of each feature value of each row, or a refusal."""
    z = deviations(norms, rows)
    result: list[dict[str, Cell]] = []
    for i, row in enumerate(rows):
        refusal = _outside(norms, row)
        cells: dict[str, Cell] = {}
        for feature in row.values:
            if refusal is not None:
                cells[feature] = refusal
            elif feature not in norms.features:
                cells[feature] = Refusal(RefusalCode.TOO_FEW_VALUES, "No normative model for this feature.")
            elif not math.isfinite(z[feature][i]):
                cells[feature] = Refusal(RefusalCode.UNDEFINED, "The row has no finite value.")
            else:
                cells[feature] = 100.0 * float(norm.cdf(z[feature][i]))
        result.append(cells)
    return result
