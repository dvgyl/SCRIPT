"""Signal calculations within QC segments. All time units are seconds."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.signal import find_peaks, savgol_filter, welch
from scipy.spatial import KDTree
from scipy.special import polygamma
from scipy.stats import gamma, norm

from scriptkin.quality import Refusal, RefusalCode, Segment
from scriptkin.recording import FloatArray


@dataclass(frozen=True)
class Preprocessing:
    grid_hz: float = 200.0
    window: int = 11
    polynomial: int = 3
    welch_window_s: float = 2.0
    band_hz: tuple[float, float] = (4.0, 12.0)
    entropy_m: int = 2
    entropy_r: float = 0.2
    entropy_scale: int = 1

    def __post_init__(self) -> None:
        if not all(
            np.isfinite(x) and x > 0
            for x in (
                self.grid_hz,
                self.welch_window_s,
                self.entropy_r,
            )
        ):
            raise ValueError("Preprocessing rates, durations, and tolerances must be positive.")
        if self.polynomial < 3 or self.window <= self.polynomial or self.window % 2 != 1:
            raise ValueError("Use an odd window larger than a polynomial of order 3 or more.")
        if self.entropy_m < 1 or self.entropy_scale < 1:
            raise ValueError("Entropy dimension and scale must be positive integers.")
        lo, hi = self.band_hz
        if not (0 <= lo < hi <= self.grid_hz / 2):
            raise ValueError("The band must be inside the analysis frequency range.")


DEFAULT_PREPROCESSING = Preprocessing()


@dataclass(frozen=True)
class Resampled:
    t: FloatArray
    xy: FloatArray


@dataclass(frozen=True)
class Kinematics:
    t: FloatArray
    xy: FloatArray
    velocity: FloatArray
    acceleration: FloatArray
    jerk: FloatArray
    speed: FloatArray


def resample(segment: Segment, grid_hz: float = 200.0) -> Resampled:
    """Use a uniform grid inside this segment, without extrapolation."""
    if not np.isfinite(grid_hz) or grid_hz <= 0:
        raise ValueError("grid_hz must be positive and finite.")
    count = int(np.floor((segment.t[-1] - segment.t[0]) * grid_hz)) + 1
    t = segment.t[0] + np.arange(count, dtype=float) / grid_hz
    t = t[t <= segment.t[-1]]
    xy = np.column_stack([np.interp(t, segment.t, segment.xy[:, k]) for k in range(2)])
    return Resampled(t, xy)


def derivatives(
    values: FloatArray, dt: float, *, window: int = 11, polynomial: int = 3, order: int = 1
) -> FloatArray:
    """Use Savitzky-Golay derivatives, including polynomial endpoint fits."""
    if not np.isfinite(dt) or dt <= 0 or window % 2 != 1 or not 0 <= order <= polynomial < window:
        raise ValueError("Invalid derivative settings.")
    if len(values) < window or not np.isfinite(values).all():
        raise ValueError("The derivative needs one finite full window.")
    return np.asarray(savgol_filter(values, window, polynomial, deriv=order, delta=dt, axis=0, mode="interp"))


def kinematics(segment: Segment, settings: Preprocessing = DEFAULT_PREPROCESSING) -> Kinematics | Refusal:
    grid = resample(segment, settings.grid_hz)
    if len(grid.t) < settings.window:
        return Refusal(RefusalCode.TOO_FEW_VALUES, "The segment is shorter than the derivative window.")
    velocity, acceleration, jerk = (
        derivatives(
            grid.xy, 1 / settings.grid_hz, window=settings.window, polynomial=settings.polynomial, order=order
        )
        for order in (1, 2, 3)
    )
    return Kinematics(grid.t, grid.xy, velocity, acceleration, jerk, np.linalg.norm(velocity, axis=1))


def pool(
    values: Iterable[float | Refusal],
    *,
    rule: Literal["mean", "median", "max", "sum"] = "mean",
    min_values: int = 1,
) -> float | Refusal:
    """Count finite values before applying the declared segment pool rule."""
    if min_values < 1:
        raise ValueError("min_values must be positive.")
    finite = [float(v) for v in values if not isinstance(v, Refusal) and np.isfinite(v)]
    if len(finite) < min_values:
        return Refusal(RefusalCode.TOO_FEW_VALUES, f"Need {min_values} finite values; found {len(finite)}.")
    return float({"mean": np.mean, "median": np.median, "max": np.max, "sum": np.sum}[rule](finite))


def band_power(freqs: FloatArray, psd: FloatArray, lo: float, hi: float) -> float:
    """Integrate by trapezoids. Insert exact band edges by interpolation."""
    if (
        freqs.ndim != 1
        or freqs.shape != psd.shape
        or len(freqs) < 2
        or not np.isfinite(freqs).all()
        or not np.isfinite(psd).all()
        or not np.all(np.diff(freqs) > 0)
        or not freqs[0] <= lo < hi <= freqs[-1]
    ):
        raise ValueError("The finite spectrum must cover the full band.")
    inside = (freqs > lo) & (freqs < hi)
    f = np.concatenate(([lo], freqs[inside], [hi]))
    p = np.concatenate(([np.interp(lo, freqs, psd)], psd[inside], [np.interp(hi, freqs, psd)]))
    return float(np.trapezoid(p, f))


def welch_band_power(
    segments: Sequence[FloatArray],
    grid_hz: float,
    band: tuple[float, float],
    *,
    window_s: float = 2.0,
    min_windows: int = 1,
) -> float | Refusal:
    """Average the Welch spectra of all segments, weighted by window count, and integrate the band.

    The result equals the mean periodogram of every half-overlapping window. A segment shorter
    than one window adds no window. The window length never shrinks.
    """
    if not np.isfinite(grid_hz) or grid_hz <= 0 or not np.isfinite(window_s) or window_s <= 0:
        raise ValueError("Welch rate and window duration must be positive and finite.")
    if not 0 <= band[0] < band[1] <= grid_hz / 2:
        raise ValueError("The band must be inside the sampled frequency range.")
    if min_windows < 1:
        raise ValueError("min_windows must be positive.")
    nperseg = max(2, round(grid_hz * window_s))
    step = nperseg - nperseg // 2
    eligible = [x for x in segments if len(x) >= nperseg]
    if not all(np.isfinite(x).all() for x in eligible):
        return Refusal(RefusalCode.NONFINITE, "The spectrum needs finite values.")
    windows = [1 + (len(x) - nperseg) // step for x in eligible]
    if sum(windows) < min_windows:
        return Refusal(RefusalCode.TOO_FEW_VALUES, f"Need {min_windows} Welch windows; found {sum(windows)}.")
    spectra = [
        welch(x, fs=grid_hz, nperseg=nperseg, noverlap=nperseg // 2, detrend="constant") for x in eligible
    ]
    psd = np.average([np.asarray(p) for _, p in spectra], axis=0, weights=windows)
    return band_power(np.asarray(spectra[0][0]), psd, *band)


def sample_entropy_counts(segments: Sequence[FloatArray], m: int, r: float) -> tuple[int, int]:
    """Return unordered (B, A) match counts, excluding self-matches.

    B compares m points. A compares m+1 points over the same template starts.
    Templates from different segments can match. Each template stays in its
    source segment. Chebyshev distance must be at most r.
    """
    if m < 1 or not np.isfinite(r) or r <= 0:
        raise ValueError("m and r must be positive.")
    if any(x.ndim != 1 or not np.isfinite(x).all() for x in segments):
        raise ValueError("Entropy needs finite one-dimensional segments.")
    windows = [np.lib.stride_tricks.sliding_window_view(x, m + 1) for x in segments if len(x) > m]
    if not windows:
        return 0, 0
    full = np.concatenate(windows)

    def count(points: FloatArray) -> int:
        tree = KDTree(points)
        return (int(tree.count_neighbors(tree, r, p=np.inf)) - len(points)) // 2

    return count(full[:, :m]), count(full)


def coarse_grain(segments: Sequence[FloatArray], scale: int) -> tuple[FloatArray, ...]:
    """Average complete blocks in each segment. Discard each remaining tail."""
    if scale < 1:
        raise ValueError("scale must be a positive integer.")
    return tuple(
        x[: len(x) // scale * scale].reshape(-1, scale).mean(axis=1) for x in segments if len(x) >= scale
    )


def micro_spikes(speed: FloatArray) -> FloatArray:
    """Normalize a speed peak by peak plus mean speed between its minima.

    The mean includes both minima and the peak. A boundary is a minimum only
    when it is no greater than its neighbour. Ignore peaks without two minima.
    """
    if speed.ndim != 1 or not np.isfinite(speed).all() or np.any(speed < 0):
        raise ValueError("Speed must be finite, nonnegative, and one-dimensional.")
    if len(speed) < 3:
        return np.empty(0)
    peaks = find_peaks(speed)[0]
    minima = list(find_peaks(-speed)[0])
    if speed[0] <= speed[1]:
        minima.insert(0, 0)
    if speed[-1] <= speed[-2]:
        minima.append(len(speed) - 1)
    spikes: list[float] = []
    for peak in peaks:
        left = [i for i in minima if i < peak]
        right = [i for i in minima if i > peak]
        if left and right:
            mean = float(np.mean(speed[left[-1] : right[0] + 1]))
            spikes.append(float(speed[peak] / (speed[peak] + mean)))
    return np.asarray(spikes)


@dataclass(frozen=True)
class Estimate:
    point: float
    low: float
    high: float


@dataclass(frozen=True)
class GammaFit:
    shape: Estimate
    scale: Estimate
    covariance: FloatArray
    location: float = 0.0


def gamma_fit(values: FloatArray, *, confidence: float = 0.95, min_values: int = 20) -> GammaFit | Refusal:
    """Fit Gamma shape and scale by maximum likelihood with location fixed at 0.

    Invert the observed negative log-likelihood Hessian. Use its delta-method
    standard errors on log parameters to obtain positive marginal intervals.
    """
    if not 0 < confidence < 1 or min_values < 3:
        raise ValueError("Invalid Gamma confidence or minimum count.")
    if len(values) < min_values:
        return Refusal(RefusalCode.TOO_FEW_VALUES, f"Gamma fit needs {min_values} values.")
    if not np.isfinite(values).all() or np.any(values <= 0):
        return Refusal(RefusalCode.UNDEFINED, "Gamma observations must be positive and finite.")
    if np.log(np.mean(values)) - np.mean(np.log(values)) <= 16 * np.finfo(float).eps:
        return Refusal(RefusalCode.UNDEFINED, "Constant observations have no finite Gamma fit.")
    try:
        shape, _, scale = gamma.fit(values, floc=0)
        information = len(values) * np.array(
            [[polygamma(1, shape), 1 / scale], [1 / scale, shape / scale**2]], dtype=float
        )
        covariance = np.linalg.inv(information)
        errors = np.sqrt(np.diag(covariance))
        z = float(norm.ppf((1 + confidence) / 2))
        if not np.isfinite(covariance).all() or np.any(errors <= 0):
            raise ValueError("Invalid observed information.")
        estimates = [
            Estimate(float(p), float(p * np.exp(-z * se / p)), float(p * np.exp(z * se / p)))
            for p, se in zip((shape, scale), errors, strict=True)
        ]
    except (ValueError, RuntimeError, np.linalg.LinAlgError) as e:
        return Refusal(RefusalCode.UNDEFINED, f"Gamma fit failed: {e}")
    covariance.setflags(write=False)
    return GammaFit(estimates[0], estimates[1], covariance)
