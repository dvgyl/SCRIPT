from __future__ import annotations

import importlib.util
import inspect
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scipy.stats import binomtest, t, ttest_ind

from scriptkin.bench import MAIN, stats
from scriptkin.recording import FloatArray

U, V = stats.covariates(MAIN)
GEOMETRY_OFFSET = {"loops": 0.0, "spiral": 1.0, "line": -1.0}
TRUTH_W = 8 * U - 6 * V + np.array([GEOMETRY_OFFSET[d.geometry] for d in MAIN])
SPREAD = np.array([-0.2, -0.1, 0.0, 0.1, 0.2])


def _runs(means: FloatArray, effects: FloatArray, spread: FloatArray = SPREAD) -> FloatArray:
    """Runs (P, m, r): a program mean plus a mounting effect plus a fixed spread of the runs."""
    return means[:, None, None] + effects[None, :, None] + spread[None, None, :]


def _random_runs(rng: np.random.Generator, shift: float, sigma_mount: float, sigma_run: float) -> FloatArray:
    return shift + rng.normal(0, sigma_mount, (6, 1)) + rng.normal(0, sigma_run, (6, 5))


def test_mounting_means_skip_missing_runs_and_mark_an_empty_mounting() -> None:
    # Detects a missing run counted as 0, or a mounting without a valid run that gets a mean.
    runs = np.array([[1.0, 3.0, np.nan], [4.0, 6.0, 8.0], [np.nan, np.nan, np.nan]])
    assert np.array_equal(stats.mounting_means(runs), [2.0, 6.0, np.nan], equal_nan=True)
    assert stats.valid_counts(runs).tolist() == [2, 3, 0]


def test_variance_components_use_n0_for_unequal_counts() -> None:
    # Detects the mean count or the largest count in place of n0 for unequal counts.
    # Counts 2, 3, 1. MS_within = 10 / 3, MS_between = (408 / 9) / 2, n0 = (6 - 14 / 6) / 2 = 11 / 6.
    runs = np.array([[1.0, 3.0, np.nan], [4.0, 6.0, 8.0], [10.0, np.nan, np.nan]])
    c = stats.variance_components(runs)
    assert math.isclose(c.run, 10 / 3)
    assert math.isclose(c.mount, (204 / 9 - 10 / 3) / (11 / 6))
    assert math.isclose(c.mean_count, 2.0)


def test_pooled_variance_components_pool_the_squares_and_average_the_mounting_estimates() -> None:
    # Detects a pooled run variance that averages the mean squares, which weights a program with few
    # degrees of freedom too much, or a program whose empty mounting enters the analysis of variance.
    first = [[1.0, 3.0, np.nan], [4.0, 6.0, 8.0], [10.0, np.nan, np.nan]]
    second = [[0.0, 2.0, np.nan], [4.0, 6.0, np.nan], [np.nan, np.nan, np.nan]]
    runs = np.array([first, second])
    single = stats.variance_components(runs)
    assert np.allclose(single.run, [10 / 3, 2.0])
    assert np.allclose(single.mount, [116 / 11, 7.0])
    pooled = stats.pooled_variance_components(runs)
    assert math.isclose(pooled.run, 14 / 5)
    assert math.isclose(pooled.mount, (116 / 11 + 7.0) / 2)
    assert math.isclose(pooled.mean_count, 10 / 6)


def test_the_mounting_variance_is_never_negative() -> None:
    # Detects a negative mounting variance when the mounting means vary less than the runs predict.
    runs = np.array([[0.0, 2.0], [2.0, 0.0], [1.0, 1.0]])
    assert stats.variance_components(runs).mount == 0.0


def test_the_welch_interval_matches_scipy() -> None:
    # Detects a wrong standard error or Welch-Satterthwaite degrees of freedom, or a pooled variance.
    rng = np.random.default_rng(1)
    p, w = _random_runs(rng, 0.3, 0.4, 0.2), _random_runs(rng, 0.0, 0.1, 0.2)
    b = stats.bias(p, w)
    expected: Any = ttest_ind(p.mean(axis=1), w.mean(axis=1), equal_var=False)
    assert math.isclose(b.estimate, p.mean() - w.mean())
    assert math.isclose(b.estimate / b.se, expected.statistic)
    assert math.isclose(b.df, expected.df)
    interval = b.interval
    assert math.isclose(interval.upper - interval.estimate, b.half_width)
    assert math.isclose(b.half_width, b.se * t.ppf(0.95, expected.df))


def test_the_bias_of_a_program_does_not_depend_on_another_program() -> None:
    # Detects a variance pooled across programs, which protocol section 11.2 excludes.
    rng = np.random.default_rng(7)
    p, w = rng.normal(0, 0.2, (2, 6, 5)), rng.normal(0, 0.2, (2, 6, 5))
    before = stats.bias(p, w)
    p[1] *= 5
    after = stats.bias(p, w)
    assert after.estimate[0] == before.estimate[0] and after.se[0] == before.se[0]
    assert after.df[0] == before.df[0] and after.se[1] != before.se[1]


def test_the_tost_p_value_matches_two_one_sided_scipy_tests() -> None:
    # Detects a TOST that tests against one limit, takes the smaller p-value, or swaps the two sides.
    rng = np.random.default_rng(2)
    for shift, limit in ((0.2, 1.0), (-0.6, 1.0), (0.9, 0.5)):
        p, w = _random_runs(rng, shift, 0.2, 0.2), _random_runs(rng, 0.0, 0.2, 0.2)
        pm, wm = p.mean(axis=1), w.mean(axis=1)
        above: Any = ttest_ind(pm + limit, wm, equal_var=False, alternative="greater")
        below: Any = ttest_ind(pm - limit, wm, equal_var=False, alternative="less")
        assert math.isclose(stats.tost_p(stats.bias(p, w), limit), max(above.pvalue, below.pvalue))


def test_two_zero_variances_give_2_m_minus_1_degrees_of_freedom_and_a_point_interval() -> None:
    # Detects NaN degrees of freedom when both sets of mounting means are constant, or a point interval
    # on the limit that is not equivalent although the limits are inclusive.
    p, w = np.full((6, 5), 1.5), np.zeros((6, 5))
    b = stats.bias(p, w)
    assert b.df == 10.0 and b.se == 0.0 and b.half_width == 0.0
    assert stats.tost_p(b, 2.0) == 0.0
    assert stats.tost_p(b, 1.5) == 0.0
    assert stats.tost_p(b, 1.0) == 1.0


def test_a_program_with_three_valid_runs_in_one_mounting_is_not_estimable() -> None:
    # Detects an estimability rule that counts the runs of the program instead of each mounting, or that
    # needs more than 4 valid runs.
    w = np.zeros((6, 5))
    four = np.zeros((6, 5))
    four[2, :1] = np.nan
    three = np.zeros((6, 5))
    three[2, :2] = np.nan
    assert stats.bias(four, w).estimable
    b = stats.bias(three, w)
    assert not b.estimable and np.isnan(b.estimate) and np.isnan(b.interval.lower)
    assert stats.tost_p(b, 1.0) == 1.0


def test_a_program_that_is_not_estimable_makes_its_feature_not_shown_equivalent() -> None:
    # Detects a feature decision that skips a program that is not estimable instead of failing on it.
    w = 0.01 * np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0])[None, :, None] + SPREAD + np.zeros((3, 1, 1))
    p = w + 0.01
    p[1, 4, 1:] = np.nan
    program_p = stats.tost_p(stats.bias(p, w), 1.0)
    assert program_p[0] < 1e-6 and program_p[2] < 1e-6 and program_p[1] == 1.0
    features = np.array([1e-6, 1e-6, stats.feature_p(program_p)])
    decision = stats.holm(features)
    assert decision.adjusted.tolist() == [True, True, False]
    assert decision.unadjusted.tolist() == [True, True, False]


def test_holm_thresholds_follow_the_rank() -> None:
    # Detects a Holm level that does not give 0.05 / (7 - rank) for the six features of the protocol.
    assert np.allclose(stats.holm_thresholds(0.05, 6), [0.05 / (7 - rank) for rank in range(1, 7)])
    assert np.allclose(stats.holm_thresholds(0.05, 3), [0.05 / 3, 0.05 / 2, 0.05])


@pytest.mark.parametrize(
    ("p", "adjusted"),
    [
        ([0.04, 0.001, 0.2, 0.02, 0.045, 0.03], [False, True, False, False, False, False]),
        ([0.011, 0.5, 0.005, 0.009, 0.3, 0.04], [True, False, True, True, False, False]),
        ([0.009, 0.0095, 0.6, 0.6, 0.6, 0.6], [False, False, False, False, False, False]),
    ],
)
def test_holm_stops_at_the_first_failed_rank(p: list[float], adjusted: list[bool]) -> None:
    # Detects a Holm procedure that compares each p-value with its own level only, without the steps
    # before it, or that returns the decisions in rank order instead of feature order.
    decision = stats.holm(np.array(p))
    assert decision.adjusted.tolist() == adjusted
    assert decision.unadjusted.tolist() == [value <= 0.05 for value in p]


def test_bland_altman_limits_match_the_values_computed_by_hand() -> None:
    # Detects pooled limits without the between-program variance, or single-run limits without the run
    # variance of one device.
    # Differences 1, 2, 3 and 3, 4, 5: MS_within 1, MS_between 6, s_d^2 = 1 + (6 - 1) / 3 = 8 / 3.
    # Run variances 2.5 on device P and 0.5 on device W with 5 runs: s^2 = 8 / 3 + 0.8 (2.5 + 0.5).
    d = np.array([[1.0, 2.0, 3.0], [3.0, 4.0, 5.0]])
    w = np.zeros((2, 3, 1)) + np.array([-1.0, 0.0, 1.0, 0.0, 0.0])
    p = d[..., None] + np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
    ba = stats.bland_altman(p, w)
    assert np.allclose(ba.difference, d) and np.allclose(ba.average, d / 2)
    assert math.isclose(ba.pooled.mean, 3.0) and math.isclose(ba.pooled.sd**2, 8 / 3)
    assert math.isclose(ba.pooled.lower, 3.0 - 1.96 * math.sqrt(8 / 3))
    assert np.allclose(ba.program.mean, [2.0, 4.0]) and np.allclose(ba.program.sd, [1.0, 1.0])
    assert math.isclose(ba.single_run.sd**2, 8 / 3 + 0.8 * (2.5 + 0.5))
    assert math.isclose(ba.single_run.upper, 3.0 + 1.96 * math.sqrt(76 / 15))


def test_the_correction_recovers_known_coefficients_from_noise_free_means() -> None:
    # Detects a covariate fit, a Deming slope, or coefficients c_P - beta c_W that do not invert the model,
    # or a lambda without the variance floor, which divides by 0 when no run of device W varies.
    # Multiples of 1/8 make the device W means exact, so its error variance is exactly 0.
    truth_w = np.round(8 * TRUTH_W) / 8
    truth_p = 0.5 + 1.05 * truth_w + 0.3 * U - 0.2 * V
    flat, equal = np.zeros(6), np.zeros(5)
    c = stats.correction(_runs(truth_p, flat, equal), _runs(truth_w, flat, equal), U, V)
    assert stats.pooled_variance_components(_runs(truth_w, flat, equal)).error_variance == 0.0
    assert np.allclose([c.fit.alpha, c.fit.beta, c.fit.gamma_u, c.fit.gamma_v], [0.5, 1.05, 0.3, -0.2])
    assert np.allclose(c.residual.estimate, 0.0) and np.allclose(c.se.beta, 0.0, atol=1e-12)
    assert np.allclose(c.fit.corrected(truth_w, U, V), truth_p)


def test_the_held_out_check_passes_the_linear_truth_and_fails_the_program_truth() -> None:
    # Detects a held-out check without the interval of the mounting variation, or one that passes a
    # program difference that the covariates cannot represent (protocol section 11.8).
    effects_p = 0.25 * np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0])
    effects_w = 0.1 * np.array([-1.0, -1.0, 1.0, 1.0, -1.0, 1.0])
    offsets = 0.5 * np.resize([1.0, -1.0], len(MAIN))
    linear = 0.5 + 1.05 * TRUTH_W
    fits = stats.correction(_runs(linear, effects_p), _runs(TRUTH_W, effects_w), U, V)
    misfit = stats.correction(_runs(linear + offsets, effects_p), _runs(TRUTH_W, effects_w), U, V)
    assert fits.residual.within(1.0).all()
    assert not misfit.residual.within(1.0).all()
    # The residual bias alone stays inside the limit, so only the interval fails the misfit.
    assert np.abs(misfit.residual.estimate).max() < 1.0


def test_the_deming_slope_matches_an_independent_fit() -> None:
    # Detects a Deming slope with lambda inverted, or a slope on data without the covariates removed.
    rng = np.random.default_rng(3)
    shape = (len(MAIN), 6, 5)
    w = TRUTH_W[:, None, None] + rng.normal(0, 0.1, shape[:2])[..., None] + rng.normal(0, 0.1, shape)
    p = 0.5 + 1.05 * w + rng.normal(0, 0.3, shape[:2])[..., None] + rng.normal(0, 0.4, shape)
    c = stats.correction(p, w, U, V)
    z = np.column_stack((np.ones(len(MAIN)), U, V))
    design = np.repeat(z, 6, axis=0)

    def residual(runs: FloatArray) -> FloatArray:
        y = runs.mean(axis=-1).ravel()
        return y - design @ np.linalg.lstsq(design, y, rcond=None)[0]

    def error_variance(runs: FloatArray) -> float:
        means = runs.mean(axis=-1)
        within = runs.var(axis=-1, ddof=1).mean()
        mount = max(0.0, float((5 * means.var(axis=-1, ddof=1)).mean() - within) / 5)
        return max(mount + within / 5, 1e-6)

    lam = error_variance(p) / error_variance(w)
    x, y = residual(w), residual(p) / math.sqrt(lam)
    _, vectors = np.linalg.eigh(np.cov(np.vstack((x, y)), bias=True))
    major = vectors[:, -1]
    assert math.isclose(c.fit.beta, major[1] / major[0] * math.sqrt(lam), rel_tol=1e-9)
    assert not math.isclose(c.fit.beta, major[1] / major[0] / math.sqrt(lam), rel_tol=1e-3)


def test_the_held_out_residuals_and_the_jackknife_use_the_fits_without_each_mounting() -> None:
    # Detects a held-out residual from the fit on all mountings, or a jackknife without its factor.
    rng = np.random.default_rng(4)
    shape = (len(MAIN), 6, 5)
    w = TRUTH_W[:, None, None] + rng.normal(0, 0.2, shape[:2])[..., None] + rng.normal(0, 0.2, shape)
    p = 0.5 + 1.05 * w + rng.normal(0, 0.2, shape[:2])[..., None] + rng.normal(0, 0.2, shape)
    c = stats.correction(p, w, U, V)
    folds = [
        stats.correction(p[:, keep], w[:, keep], U, V).fit
        for keep in ([i for i in range(6) if i != k] for k in range(6))
    ]
    residuals = np.array(
        [p[:, k].mean(axis=-1) - f.corrected(w[:, k].mean(axis=-1), U, V) for k, f in enumerate(folds)]
    )
    assert np.allclose(c.residual.estimate, residuals.mean(axis=0))
    half = c.residual.upper - c.residual.estimate
    assert np.allclose(half, t.ppf(0.95, 5) * residuals.std(axis=0, ddof=1) / math.sqrt(6))
    slopes = np.array([f.beta for f in folds])
    assert math.isclose(c.se.beta, math.sqrt(5 / 6 * float(((slopes - slopes.mean()) ** 2).sum())))


def test_every_function_keeps_the_leading_axes() -> None:
    # Detects a reduction over a leading axis, which mixes the datasets of the precision study.
    rng = np.random.default_rng(5)
    shape = (3, len(MAIN), 6, 5)
    w = TRUTH_W[:, None, None] + rng.normal(0, 0.2, shape)
    p = 0.5 + 1.05 * w + rng.normal(0, 0.2, shape)
    p[1, 2, 3, :2] = np.nan
    b, c, ba = stats.bias(p, w), stats.correction(p, w, U, V), stats.bland_altman(p, w)
    for i in range(3):
        one = stats.bias(p[i], w[i])
        assert np.array_equal(b.estimate[i], one.estimate, equal_nan=True)
        assert np.array_equal(b.df[i], one.df, equal_nan=True)
        assert np.array_equal(stats.tost_p(b, 1.0)[i], stats.tost_p(one, 1.0))
        ci = stats.correction(p[i], w[i], U, V)
        assert np.allclose(c.fit.beta[i], ci.fit.beta, equal_nan=True)
        assert np.allclose(c.residual.upper[i], ci.residual.upper, equal_nan=True)
        assert np.allclose(ba.pooled.sd[i], stats.bland_altman(p[i], w[i]).pooled.sd, equal_nan=True)
    assert np.isnan(c.fit.beta[1]) and np.isnan(ba.pooled.sd[1]) and np.isfinite(c.fit.beta[[0, 2]]).all()


def test_the_bias_factor_is_the_exponential_of_the_interval() -> None:
    # Detects a factor interval built from the exponential of the half-width instead of each limit.
    rng = np.random.default_rng(6)
    b = stats.bias(_random_runs(rng, 0.03, 0.01, 0.01), _random_runs(rng, 0.0, 0.01, 0.01))
    f, interval = stats.factor(b), b.interval
    assert math.isclose(f.estimate, math.exp(b.estimate))
    assert math.isclose(f.lower, math.exp(interval.lower)) and math.isclose(f.upper, math.exp(interval.upper))


@pytest.mark.parametrize(("successes", "total"), [(18, 18), (0, 18), (17, 18), (1, 1), (690, 720)])
def test_clopper_pearson_matches_scipy(successes: int, total: int) -> None:
    # Detects a normal approximation or a one-sided bound in place of the exact two-sided interval.
    expected = binomtest(successes, total).proportion_ci(confidence_level=0.95, method="exact")
    share = stats.clopper_pearson(successes, total)
    assert math.isclose(share.estimate, successes / total)
    assert math.isclose(share.lower, expected.low, abs_tol=1e-12)
    assert math.isclose(share.upper, expected.high, abs_tol=1e-12)


def test_eighteen_of_eighteen_gives_the_lower_bound_of_the_protocol() -> None:
    # Detects an interval that differs from the value that protocol section 11.7 quotes.
    assert round(float(stats.clopper_pearson(18, 18).lower), 3) == 0.815


def _precision_script(monkeypatch: pytest.MonkeyPatch) -> Any:
    path = Path(__file__).resolve().parents[1] / "studies" / "bench_precision.py"
    spec = importlib.util.spec_from_file_location("bench_precision_study", path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    # The script's dataclasses resolve their string annotations through sys.modules.
    monkeypatch.setitem(sys.modules, spec.name, script)
    spec.loader.exec_module(script)
    return script


def test_the_precision_study_takes_its_statistics_from_this_module(monkeypatch: pytest.MonkeyPatch) -> None:
    # Detects a statistic computed in bench_precision.py itself, so that the precision study of protocol
    # section 11.8 simulates other statistics than the analysis computes.
    script = _precision_script(monkeypatch)
    public = {
        name
        for name, value in vars(stats).items()
        if inspect.isfunction(value) and value.__module__ == stats.__name__ and not name.startswith("_")
    }
    own = {
        name
        for name, value in vars(script).items()
        if inspect.isfunction(value) and value.__module__ == script.__name__
    }
    assert not public & own
    origins = {
        getattr(value, "__module__", None) or getattr(value, "__name__", "")
        for value in vars(script).values()
    }
    assert not [origin for origin in origins if isinstance(origin, str) and origin.startswith("scipy")]
    assert script.stats is stats
