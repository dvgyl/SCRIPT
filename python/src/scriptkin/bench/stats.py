"""The statistics of the bench analysis (protocol sections 11 and 12) as pure functions on numpy arrays.

The analysis and `python/studies/bench_precision.py` call the same functions, so the precision study
simulates the statistics of the real analysis.

Shapes. A run array holds the values of one device and one feature on the comparison scale (section
11.1). It has the shape (..., m, r): any leading axes, then the m mountings, then the run slots. The
leading axes are free, for example datasets and programs. A function of one program maps (..., m, r) to
(...). A function that pools over programs takes (..., P, m, r), with the P programs on the axis before
the mountings. It maps to (...), and its values per program have the shape (..., P). Mounting i of
device P pairs with mounting i of device W (section 11.4).

Missing values. NaN marks a run slot without a valid run: a failed run, a run without a valid camera
record, or an unused rerun slot. The count of valid runs comes from the NaN marks. A mounting mean is NaN
when the mounting has no valid run. A device comparison needs the program to be estimable on both
devices (section 6.4). Otherwise every comparison value of the program is NaN: the bias and its
interval, the Bland-Altman differences, and the held-out residual. Its TOST p-value is then 1. A
comparison value pooled over programs is NaN when one program is not estimable. That program therefore
removes the pooled limits of agreement and the correction of its feature, and the check of section 11.5
cannot pass for every program. The functions use NaN in place of a `Refusal` because they work on whole
arrays. The caller turns a NaN into a refusal at its output.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from scipy.stats import beta as beta_distribution
from scipy.stats import t as t_distribution

from scriptkin.bench import Design
from scriptkin.bench.track import BoolArray
from scriptkin.recording import FloatArray, IntArray

ALPHA = 0.05
MIN_VALID_RUNS = 4
VARIANCE_FLOOR = 1e-6
AGREEMENT_Z = 1.96
REFERENCE_SPEED_MM_S = 20.0


@dataclass(frozen=True)
class Interval:
    estimate: FloatArray
    lower: FloatArray
    upper: FloatArray

    def within(self, limit: float) -> BoolArray:
        """The interval lies inside [-limit, limit]. The limits are inclusive (section 10). NaN is outside."""
        return (self.lower >= -limit) & (self.upper <= limit)


def mounting_means(runs: FloatArray) -> FloatArray:
    """Section 11.2 step 1: the mean of the valid runs of each mounting, (..., m, r) to (..., m)."""
    valid = np.isfinite(runs)
    with np.errstate(invalid="ignore"):
        return np.where(valid, runs, 0.0).sum(axis=-1) / valid.sum(axis=-1)


def valid_counts(runs: FloatArray) -> IntArray:
    """The number of valid runs of each mounting (section 6.4), (..., m, r) to (..., m)."""
    return np.isfinite(runs).sum(axis=-1)


def estimable(runs: FloatArray) -> BoolArray:
    """Section 6.4: every mounting has at least `MIN_VALID_RUNS` valid runs, (..., m, r) to (...)."""
    return (valid_counts(runs) >= MIN_VALID_RUNS).all(axis=-1)


def _pairs(runs_p: FloatArray, runs_w: FloatArray) -> tuple[FloatArray, FloatArray]:
    """The mounting means of both devices. NaN in each mounting of a program not estimable on both devices."""
    if runs_p.shape[:-1] != runs_w.shape[:-1]:
        raise ValueError("Both devices need the same leading axes and the same number of mountings.")
    keep = (estimable(runs_p) & estimable(runs_w))[..., None]
    return np.where(keep, mounting_means(runs_p), np.nan), np.where(keep, mounting_means(runs_w), np.nan)


@dataclass(frozen=True)
class VarianceComponents:
    """The variance components of one device (section 11.2)."""

    run: FloatArray
    """sigma^2_run, the mean square within mountings."""
    mount: FloatArray
    """sigma^2_mount, the mounting variance, never below 0."""
    mean_count: FloatArray
    """n-bar, the mean number of valid runs per mounting."""

    @property
    def error_variance(self) -> FloatArray:
        """The variance of a mounting mean, sigma^2_mount + sigma^2_run / n-bar (section 11.5)."""
        return self.mount + self.run / self.mean_count


@dataclass(frozen=True)
class _Anova:
    ss_within: FloatArray
    df_within: FloatArray
    mount: FloatArray
    """(MS_between - MS_within) / n0 before the floor at 0."""
    mean_count: FloatArray


def _anova(runs: FloatArray) -> _Anova:
    """The one-way analysis of variance with mounting as the factor, over the mountings with a valid run."""
    valid = np.isfinite(runs)
    counts = valid.sum(axis=-1)
    means = mounting_means(runs)
    n = counts.sum(axis=-1)
    k = (counts > 0).sum(axis=-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        grand = np.where(valid, runs, 0.0).sum(axis=(-2, -1)) / n
        ss_within = np.where(valid, (runs - means[..., None]) ** 2, 0.0).sum(axis=(-2, -1))
        ss_between = np.where(counts > 0, counts * (means - grand[..., None]) ** 2, 0.0).sum(axis=-1)
        n0 = (n - (counts**2).sum(axis=-1) / n) / (k - 1)
        mount = (ss_between / (k - 1) - ss_within / (n - k)) / n0
    return _Anova(ss_within, (n - k).astype(float), mount, counts.mean(axis=-1))


def variance_components(runs: FloatArray) -> VarianceComponents:
    """Section 11.2: sigma^2_run = MS_within and sigma^2_mount = max(0, (MS_between - MS_within) / n0).

    (..., m, r) to (...). n0 = (N - sum n_i^2 / N) / (k - 1) over the k mountings with a valid run. A
    program needs 2 such mountings and more valid runs than mountings, or its components are NaN.
    """
    a = _anova(runs)
    with np.errstate(divide="ignore", invalid="ignore"):
        return VarianceComponents(a.ss_within / a.df_within, np.maximum(0.0, a.mount), a.mean_count)


def pooled_variance_components(runs: FloatArray) -> VarianceComponents:
    """The components of section 11.2 pooled over programs (sections 6.6, 11.4, 11.5), (..., P, m, r).

    sigma^2_run is the pooled within-mounting mean square, the sum of the squares over the sum of the
    degrees of freedom. sigma^2_mount is the mean over programs of (MS_between - MS_within) / n0 of each
    program, with the floor at 0 applied once to the mean. With equal counts this is the analysis of
    variance on the mean squares averaged over programs. n-bar is the mean over programs and mountings.
    """
    a = _anova(runs)
    with np.errstate(divide="ignore", invalid="ignore"):
        run = a.ss_within.sum(axis=-1) / a.df_within.sum(axis=-1)
    return VarianceComponents(run, np.maximum(0.0, a.mount.mean(axis=-1)), a.mean_count.mean(axis=-1))


@dataclass(frozen=True)
class Bias:
    """The bias of section 11.2 per program, the deliverable of section 12. NaN where it is not estimable."""

    estimate: FloatArray
    se: FloatArray
    """sqrt(s^2_P / m + s^2_W / m) from the variances of the mounting means."""
    df: FloatArray
    """The Welch-Satterthwaite degrees of freedom, 2 (m - 1) when both variances are 0."""

    @property
    def estimable(self) -> BoolArray:
        return np.isfinite(self.estimate)

    @property
    def half_width(self) -> FloatArray:
        """The half-width of the 90 percent Welch interval: the 95th t percentile times the SE."""
        return np.asarray(t_distribution.ppf(0.95, self.df)) * self.se

    @property
    def interval(self) -> Interval:
        half = self.half_width
        return Interval(self.estimate, self.estimate - half, self.estimate + half)


def bias(runs_p: FloatArray, runs_w: FloatArray) -> Bias:
    """Section 11.2: the device P mounting means minus the device W mounting means, (..., m, r) to (...)."""
    p, w = _pairs(runs_p, runs_w)
    m = p.shape[-1]
    vp, vw = np.var(p, axis=-1, ddof=1) / m, np.var(w, axis=-1, ddof=1) / m
    with np.errstate(divide="ignore", invalid="ignore"):
        df = (vp + vw) ** 2 / (vp**2 / (m - 1) + vw**2 / (m - 1))
    df = np.where((vp == 0) & (vw == 0), 2.0 * (m - 1), df)
    return Bias(p.mean(axis=-1) - w.mean(axis=-1), np.sqrt(vp + vw), df)


def factor(bias: Bias) -> Interval:
    """Section 12: the bias factor exp(bias) of a logarithmic feature with its 90 percent interval."""
    interval = bias.interval
    return Interval(np.exp(interval.estimate), np.exp(interval.lower), np.exp(interval.upper))


def tost_p(bias: Bias, limit: float) -> FloatArray:
    """Section 11.3: the larger one-sided Welch p-value against -limit and against limit, (...).

    The program is equivalent at alpha when the p-value is at most alpha, so the 90 percent interval lies
    inside [-limit, limit] at alpha 0.05. A program that is not estimable has p = 1. A standard error of 0
    makes the interval a point: the p-value is then 0 inside the limits and 1 outside.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        lower = np.asarray(t_distribution.sf((bias.estimate + limit) / bias.se, bias.df))
        upper = np.asarray(t_distribution.sf((limit - bias.estimate) / bias.se, bias.df))
    point = np.where(np.abs(bias.estimate) <= limit, 0.0, 1.0)
    return np.where(bias.estimable, np.where(bias.se == 0, point, np.maximum(lower, upper)), 1.0)


def feature_p(program_p: FloatArray) -> FloatArray:
    """Section 11.3: the p-value of a feature is the largest p-value over its program set, (..., P) to (...).

    This is an intersection-union test. A program that is not estimable has p = 1, so its feature is not
    shown equivalent.
    """
    return program_p.max(axis=-1)


def holm_thresholds(alpha: float, k: int) -> FloatArray:
    """Section 11.3: the Holm level of each rank from the smallest p-value, alpha / (k + 1 - rank).

    For the six features of the protocol at alpha 0.05 this is 0.05 / (7 - rank).
    """
    return alpha / (k - np.arange(k))


@dataclass(frozen=True)
class Decision:
    """The feature decisions of section 11.3. True means bench-equivalent, False not shown equivalent."""

    adjusted: BoolArray
    """The Holm procedure across the features."""
    unadjusted: BoolArray
    """Each feature p-value at most alpha."""


def holm(feature_p: FloatArray, alpha: float = ALPHA) -> Decision:
    """Section 11.3: the Holm procedure across the k features on the last axis, (..., k) to (..., k).

    A feature is declared equivalent when every p-value up to its rank, from the smallest, is at most the
    threshold of its own rank (`holm_thresholds`). Tied p-values keep their order in the input.
    """
    k = feature_p.shape[-1]
    order = np.argsort(feature_p, axis=-1, kind="stable")
    ranked = np.take_along_axis(feature_p, order, axis=-1)
    passed = np.logical_and.accumulate(ranked <= holm_thresholds(alpha, k), axis=-1)
    adjusted = np.empty_like(passed)
    np.put_along_axis(adjusted, order, passed, axis=-1)
    return Decision(adjusted, feature_p <= alpha)


@dataclass(frozen=True)
class LimitsOfAgreement:
    mean: FloatArray
    sd: FloatArray

    @property
    def lower(self) -> FloatArray:
        return self.mean - AGREEMENT_Z * self.sd

    @property
    def upper(self) -> FloatArray:
        return self.mean + AGREEMENT_Z * self.sd


@dataclass(frozen=True)
class BlandAltman:
    """Section 11.4 for one program set."""

    difference: FloatArray
    """d_pi, the device P mounting mean minus the device W mounting mean of pair i, (..., P, m)."""
    average: FloatArray
    """a_pi, the mean of the two mounting means, (..., P, m)."""
    pooled: LimitsOfAgreement
    """For a mounting mean over the program set, (...)."""
    program: LimitsOfAgreement
    """From the m differences of each program, (..., P)."""
    single_run: LimitsOfAgreement
    """For a single run over the program set, (...)."""


def bland_altman(runs_p: FloatArray, runs_w: FloatArray) -> BlandAltman:
    """Section 11.4 with repeated measures, (..., P, m, r) to (...).

    s_d^2 = sigma^2_within + sigma^2_between from the one-way analysis of variance of d_pi with program as
    the factor. The single-run limits add (1 - 1 / n-bar) sigma^2_run for each device, from the pooled
    components of that device. With equal counts on both devices this is the factor of the protocol.
    """
    programs, m = runs_p.shape[-3:-1]
    if programs < 2 or m < 2:
        raise ValueError("The limits of agreement need 2 programs and 2 mountings.")
    p, w = _pairs(runs_p, runs_w)
    d = p - w
    program_mean, program_var = d.mean(axis=-1), np.var(d, axis=-1, ddof=1)
    within = program_var.mean(axis=-1)
    between = np.maximum(0.0, (m * np.var(program_mean, axis=-1, ddof=1) - within) / m)
    mean = d.mean(axis=(-2, -1))
    sd2 = within + between
    single = sd2
    for runs in (runs_p, runs_w):
        components = pooled_variance_components(runs)
        single = single + (1 - 1 / components.mean_count) * components.run
    return BlandAltman(
        d,
        (p + w) / 2,
        LimitsOfAgreement(mean, np.sqrt(sd2)),
        LimitsOfAgreement(program_mean, np.sqrt(program_var)),
        LimitsOfAgreement(mean, np.sqrt(single)),
    )


def covariates(programs: Sequence[Design]) -> tuple[FloatArray, FloatArray]:
    """Section 11.5: u = ln(speed / 20 mm/s) and v = ln(scale) of each program."""
    u = np.array([math.log(d.speed_mm_s / REFERENCE_SPEED_MM_S) for d in programs])
    return u, np.array([math.log(d.scale) for d in programs])


class CorrectionModel(StrEnum):
    """The model of the correction of section 11.5 for one program set."""

    COVARIATES = "covariates"
    """P = alpha + beta W + gamma_u u + gamma_v v, as section 11.5 writes it. For the 12 main programs."""
    SLOPE = "slope"
    """P = alpha + beta W, with the Deming slope on the centred values. For the 5 loops programs."""
    SHIFT = "shift"
    """P = alpha + W, with alpha the mean of P - W over the pairs. For the 3 line programs."""

    @property
    def fits_slope(self) -> bool:
        return self is not CorrectionModel.SHIFT

    @property
    def fits_covariates(self) -> bool:
        return self is CorrectionModel.COVARIATES


@dataclass(frozen=True)
class Coefficients:
    """The correction P = alpha + beta W + gamma_u u + gamma_v v of section 11.5."""

    alpha: FloatArray
    beta: FloatArray
    gamma_u: FloatArray
    gamma_v: FloatArray

    def corrected(self, w: FloatArray, u: FloatArray, v: FloatArray) -> FloatArray:
        """The corrected value of the device W values w, (..., P), at the covariates u and v, (P,)."""
        alpha, beta = self.alpha[..., None], self.beta[..., None]
        return alpha + beta * w + self.gamma_u[..., None] * u + self.gamma_v[..., None] * v


@dataclass(frozen=True)
class Correction:
    """Section 11.5 for one program set."""

    model: CorrectionModel
    fit: Coefficients
    """The fit on all pairs, (...). A term outside the model keeps its fixed value: beta 1, gamma 0."""
    se: Coefficients
    """The jackknife standard errors over the held-out fits, (...). NaN for a term outside the model."""
    residual: Interval
    """The residual bias over the held-out mountings with its 90 percent t interval, (..., P)."""


def _fit(
    model: CorrectionModel,
    p: FloatArray,
    w: FloatArray,
    runs_p: FloatArray,
    runs_w: FloatArray,
    z: FloatArray,
) -> Coefficients:
    fixed = np.zeros(p.shape[:-2])
    if not model.fits_slope:
        return Coefficients((p - w).mean(axis=(-2, -1)), fixed + 1.0, fixed, fixed)
    design = z if model.fits_covariates else z[:, :1]
    # Every program has the same m pairs, so the least squares fit on the design of the pairs equals the
    # fit of the program means.
    project = np.linalg.pinv(design).T
    cp, cw = p.mean(axis=-1) @ project, w.mean(axis=-1) @ project
    ep, ew = p - (cp @ design.T)[..., None], w - (cw @ design.T)[..., None]
    error_p, error_w = (
        np.maximum(pooled_variance_components(runs).error_variance, VARIANCE_FLOOR)
        for runs in (runs_p, runs_w)
    )
    lam = error_p / error_w
    sxx, syy, sxy = (ew**2).sum(axis=(-2, -1)), (ep**2).sum(axis=(-2, -1)), (ew * ep).sum(axis=(-2, -1))
    d = syy - lam * sxx
    slope = (d + np.sqrt(d**2 + 4 * lam * sxy**2)) / (2 * sxy)
    c = cp - slope[..., None] * cw
    if not model.fits_covariates:
        return Coefficients(c[..., 0], slope, fixed, fixed)
    return Coefficients(c[..., 0], slope, c[..., 1], c[..., 2])


def correction(
    runs_p: FloatArray, runs_w: FloatArray, u: FloatArray, v: FloatArray, *, model: CorrectionModel
) -> Correction:
    """Section 11.5: the correction, the held-out check, and the jackknife, (..., P, m, r).

    `COVARIATES` removes (1, u, v) from both devices by least squares and `SLOPE` removes the mean. Both
    then fit the Deming slope on the residuals with lambda = the error variance of device P over that of
    device W, from the pooled components of the fitted mountings. The floor of 1e-6 applies to each of
    the two error variances. Each held-out fit leaves out one mounting index on both devices. A program
    passes the check when `residual.within(limit)`.

    Only the W differences between programs that the removed terms do not explain identify the slope.
    On the loops programs and on the line programs, u and v explain almost all of them, so these sets
    take `SLOPE` and `SHIFT`. `COVARIATES` refuses a program set on which u and v do not vary
    independently, because the fit would return a coefficient that no data determine.
    """
    z = np.column_stack((np.ones_like(u), u, v))
    if model.fits_covariates and np.linalg.matrix_rank(z) < 3:
        raise ValueError("The covariate model needs u and v that vary independently over the program set.")
    p, w = _pairs(runs_p, runs_w)
    m = p.shape[-1]

    def fit(keep: list[int]) -> Coefficients:
        return _fit(model, p[..., keep], w[..., keep], runs_p[..., keep, :], runs_w[..., keep, :], z)

    folds = [fit([i for i in range(m) if i != k]) for k in range(m)]
    residuals = np.stack([p[..., k] - f.corrected(w[..., k], u, v) for k, f in enumerate(folds)], axis=-1)
    mean = residuals.mean(axis=-1)
    half = float(t_distribution.ppf(0.95, m - 1)) * np.std(residuals, axis=-1, ddof=1) / math.sqrt(m)

    def jackknife(values: list[FloatArray], fitted: bool) -> FloatArray:
        s = np.stack(values, axis=-1)
        se = np.sqrt((m - 1) / m * ((s - s.mean(axis=-1, keepdims=True)) ** 2).sum(axis=-1))
        return se if fitted else np.full_like(se, np.nan)

    se = Coefficients(
        jackknife([f.alpha for f in folds], True),
        jackknife([f.beta for f in folds], model.fits_slope),
        jackknife([f.gamma_u for f in folds], model.fits_covariates),
        jackknife([f.gamma_v for f in folds], model.fits_covariates),
    )
    return Correction(model, fit(list(range(m))), se, Interval(mean, mean - half, mean + half))


def clopper_pearson(successes: int | IntArray, total: int | IntArray) -> Interval:
    """Section 11.7: the share successes / total with its exact 95 percent Clopper-Pearson interval."""
    k, n = np.asarray(successes), np.asarray(total)
    if np.any(n <= 0) or np.any(k < 0) or np.any(k > n):
        raise ValueError("A share needs 0 <= successes <= total and total > 0.")
    lower = np.where(k == 0, 0.0, beta_distribution.ppf(0.025, np.maximum(k, 1), n - k + 1))
    upper = np.where(k == n, 1.0, beta_distribution.ppf(0.975, k + 1, np.maximum(n - k, 1)))
    return Interval(k / n, lower, upper)
