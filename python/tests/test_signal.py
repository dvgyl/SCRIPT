from __future__ import annotations

from itertools import combinations

import numpy as np
import pytest
from conftest import make_trial

from scriptkin import Accepted, Refusal, assess, extract
from scriptkin.recording import FloatArray
from scriptkin.signal import (
    GammaFit,
    Preprocessing,
    band_power,
    coarse_grain,
    derivatives,
    gamma_fit,
    kinematics,
    micro_spikes,
    pool,
    resample,
    sample_entropy_counts,
    welch_band_power,
)


def test_gap_does_not_increase_maximum_speed() -> None:
    # Detects speed calculated across a 200 ms gap and a position jump.
    first = np.arange(101, dtype=float) / 100
    t = np.concatenate((first, first + 1.2))
    xy = np.column_stack((np.concatenate((first * 10, first * 10 + 1000)), np.zeros(len(t))))
    qc = assess(make_trial(t, xy))
    assert isinstance(qc, Accepted)
    assert len(qc.segments) == 2
    cells = extract(qc)
    assert cells["kinematic.speed_max"] == pytest.approx(10, abs=1e-8)
    base = assess(make_trial(first))
    assert isinstance(base, Accepted)
    assert cells["kinematic.speed_max"] == pytest.approx(extract(base)["kinematic.speed_max"])


def test_resample_never_extrapolates() -> None:
    # Detects a resampling grid that extends beyond the last source point.
    t = np.arange(107, dtype=float) / 137
    qc = assess(make_trial(t))
    assert isinstance(qc, Accepted)
    grid = resample(qc.segments[0], 200)
    assert grid.t[0] >= t[0]
    assert grid.t[-1] <= t[-1]
    np.testing.assert_allclose(grid.xy[:, 0], grid.t * 10)
    np.testing.assert_allclose(np.diff(grid.t), 0.005)


@pytest.mark.parametrize("order", [1, 2, 3])
def test_derivative_endpoints_preserve_polynomial(order: int) -> None:
    # Detects incorrect endpoint derivative order, including the old gradient endpoint defect.
    t = np.arange(101, dtype=float) / 100
    expected = {1: 3 * t**2, 2: 6 * t, 3: np.full(len(t), 6.0)}[order]
    actual = derivatives(t**3, 0.01, order=order)
    np.testing.assert_allclose(actual, expected, atol=1e-7)


def test_kinematics_uses_segment_derivatives() -> None:
    # Detects velocity axes or units inconsistent with a straight-line trajectory.
    t = np.arange(101, dtype=float) / 100
    qc = assess(make_trial(t, np.column_stack((3 * t, 4 * t))))
    assert isinstance(qc, Accepted)
    k = kinematics(qc.segments[0])
    assert not isinstance(k, Refusal)
    np.testing.assert_allclose(k.speed, 5, atol=1e-8)
    np.testing.assert_allclose(k.acceleration, 0, atol=1e-8)


def test_pool_counts_only_finite_values() -> None:
    # Detects a pool that counts non-finite values toward min_values.
    result = pool([1.0, 2.0, np.nan, np.inf], min_values=3)
    assert isinstance(result, Refusal)
    assert result.code == "too_few_values"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_entropy_counts_equal_independent_loops(seed: int) -> None:
    # Detects entropy template matches or self-matches counted incorrectly.
    rng = np.random.default_rng(seed)
    segments = [rng.normal(size=n) for n in (9, 3, 12, 1)]
    m, r = 2, 0.9
    windows = [tuple(float(x) for x in s[i : i + m + 1]) for s in segments for i in range(len(s) - m)]
    b = sum(
        max(abs(x - y) for x, y in zip(a[:m], c[:m], strict=True)) <= r for a, c in combinations(windows, 2)
    )
    a = sum(max(abs(x - y) for x, y in zip(a, c, strict=True)) <= r for a, c in combinations(windows, 2))
    assert sample_entropy_counts(segments, m, r) == (b, a)


def test_entropy_templates_do_not_cross_segment_ends() -> None:
    # Detects entropy templates constructed across a segment boundary.
    segments = [np.array([1.0, 2.0]), np.array([1.0, 2.0]), np.array([1.0])]
    assert sample_entropy_counts(segments, 2, 0.1) == (0, 0)
    assert sample_entropy_counts([np.zeros(3), np.zeros(3)], 2, 0.1) == (1, 1)


def test_coarse_grain_discards_each_segment_tail() -> None:
    # Detects coarse-grained blocks that combine two source segments.
    actual = coarse_grain([np.array([1.0, 3.0, 99.0]), np.array([5.0, 7.0, 88.0])], 2)
    assert len(actual) == 2
    np.testing.assert_array_equal(actual[0], [2])
    np.testing.assert_array_equal(actual[1], [6])


@pytest.mark.parametrize("amplitude", [0.5, 2.0, 5.0])
def test_sinusoid_band_power_is_half_squared_amplitude(amplitude: float) -> None:
    # Detects Welch scaling or band integration that gives incorrect sinusoid power.
    t = np.arange(2000, dtype=float) / 200
    result = welch_band_power([amplitude * np.sin(2 * np.pi * 8 * t)], 200, (4, 12))
    assert result == pytest.approx(amplitude**2 / 2, rel=0.01)


def test_welch_weights_segment_spectra_by_window_count() -> None:
    # Detects segment spectra averaged with equal weight, or a short segment counted as a window.
    t = np.arange(2001, dtype=float) / 200
    long, short = np.sin(2 * np.pi * 8 * t), 2 * np.sin(2 * np.pi * 8 * t[:601])
    a, b = welch_band_power([long], 200, (4, 12)), welch_band_power([short], 200, (4, 12))
    assert isinstance(a, float) and isinstance(b, float)
    pooled = welch_band_power([long, short, np.zeros(399)], 200, (4, 12))
    assert pooled == pytest.approx((9 * a + 2 * b) / 11, rel=1e-12)


def test_band_power_interpolates_edges() -> None:
    # Detects band boundaries silently moved to frequency bins.
    assert band_power(np.array([0.0, 1.0, 2.0]), np.array([0.0, 1.0, 2.0]), 0.25, 1.75) == 1.5


def test_welch_refuses_a_short_segment() -> None:
    # Detects automatic Welch window shortening that changes frequency resolution.
    result = welch_band_power([np.zeros(50)], 200, (4, 12))
    assert isinstance(result, Refusal)
    assert result.code == "too_few_values"


def test_micro_spikes_use_neighbouring_minima_mean() -> None:
    # Detects peak normalization by peak height alone or by a global mean.
    speed = np.array([1.0, 4.0, 2.0, 3.0, 8.0, 1.0])
    np.testing.assert_allclose(micro_spikes(speed), [4 / (4 + 7 / 3), 8 / (8 + 14 / 4)])


def test_gamma_intervals_have_seeded_marginal_coverage() -> None:
    # Detects Gamma intervals from the wrong parameterization or information matrix.
    rng = np.random.default_rng(720)
    covered_shape, covered_scale = 0, 0
    for _ in range(500):
        fit = gamma_fit(rng.gamma(2.5, 0.3, size=600))
        assert isinstance(fit, GammaFit)
        assert fit.location == 0
        covered_shape += fit.shape.low <= 2.5 <= fit.shape.high
        covered_scale += fit.scale.low <= 0.3 <= fit.scale.high
    assert 0.90 <= covered_shape / 500 <= 0.99
    assert 0.90 <= covered_scale / 500 <= 0.99


def test_gamma_recovers_known_parameters() -> None:
    # Detects Gamma shape and scale exchanged in the output.
    fit = gamma_fit(np.random.default_rng(10).gamma(4, 0.2, size=10000))
    assert isinstance(fit, GammaFit)
    assert fit.shape.point == pytest.approx(4, rel=0.06)
    assert fit.scale.point == pytest.approx(0.2, rel=0.06)


@pytest.mark.parametrize("values", [np.ones(30), np.zeros(30), np.full(30, np.nan), np.array([1.0, 2.0])])
def test_gamma_returns_refusal_for_undefined_fit(values: FloatArray) -> None:
    # Detects an undefined Gamma fit returned as a number or an uncaught exception.
    assert isinstance(gamma_fit(values), Refusal)


def test_preprocessing_refuses_invalid_derivative_order() -> None:
    # Detects a preprocessing polynomial too low for the requested jerk derivative.
    with pytest.raises(ValueError):
        Preprocessing(polynomial=2)
