"""Measurement agreement between devices or sessions, and the reliability gate (DESIGN section 7)."""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from scipy.stats import f as f_distribution

from scriptkin.evaluate.metrics import Z95
from scriptkin.evaluate.participants import medians
from scriptkin.evaluate.plan import Between, GateCriteria, column
from scriptkin.features import FeatureRow, FeatureTable
from scriptkin.recording import FloatArray
from scriptkin.signal import Estimate


@dataclass(frozen=True)
class BlandAltman:
    """Differences `second - first` over participants: mean bias and 95% limits of agreement."""

    first: str
    second: str
    bias: float
    low: float
    high: float


@dataclass(frozen=True)
class Agreement:
    """Agreement of one column over `occasions` (devices or visits) for `n` participants with every occasion.

    `icc` is ICC(A,1): two-way random effects, absolute agreement, one measurement (McGraw and Wong 1996),
    with the F-based 95% interval. `sem` is the agreement standard error of measurement,
    sqrt(occasion variance + residual variance). `mdc95` is 1.96 * sqrt(2) * sem. `between_sd` is the
    between-participant standard deviation.
    """

    between: Between
    occasions: tuple[str, ...]
    n: int
    icc: Estimate
    sem: float
    mdc95: float
    between_sd: float
    bland_altman: tuple[BlandAltman, ...]


def icc_a1(y: FloatArray, alpha: float = 0.05) -> tuple[Estimate, float, float]:
    """ICC(A,1) with its interval, the agreement SEM, and the between-participant SD of an n by k matrix."""
    n, k = y.shape
    grand = float(y.mean())
    ms_rows = k * float(np.sum((y.mean(axis=1) - grand) ** 2)) / (n - 1)
    ms_cols = n * float(np.sum((y.mean(axis=0) - grand) ** 2)) / (k - 1)
    ss_error = float(np.sum((y - grand) ** 2)) - ms_rows * (n - 1) - ms_cols * (k - 1)
    ms_error = ss_error / ((n - 1) * (k - 1))
    sem = math.sqrt(max(0.0, (ms_cols - ms_error) / n) + max(ms_error, 0.0))
    between_sd = math.sqrt(max(0.0, (ms_rows - ms_error) / k))
    denominator = ms_rows + (k - 1) * ms_error + k * (ms_cols - ms_error) / n
    if denominator <= 0 or ms_error <= 0:
        return Estimate(math.nan, math.nan, math.nan), sem, between_sd
    icc = (ms_rows - ms_error) / denominator
    if icc >= 1:
        return Estimate(icc, math.nan, math.nan), sem, between_sd
    a, b = k * icc, n * (1 + (k - 1) * icc) - k * icc
    ratio = ms_cols / ms_error
    v = (k - 1) * (n - 1) * (a * ratio + b) ** 2 / ((n - 1) * (a * ratio) ** 2 + b**2)
    f_low = float(f_distribution.ppf(1 - alpha / 2, n - 1, v))
    f_high = float(f_distribution.ppf(1 - alpha / 2, v, n - 1))
    spread = k * ms_cols + (k * n - k - n) * ms_error
    low = n * (ms_rows - f_low * ms_error) / (f_low * spread + n * ms_rows)
    high = n * (f_high * ms_rows - ms_error) / (spread + n * f_high * ms_rows)
    return Estimate(icc, low, high), sem, between_sd


def _key(between: Between, row: FeatureRow) -> tuple[str, tuple[bool, int, str]]:
    """The participant and a sortable occasion: devices by name, visits by number, an unknown visit last."""
    if between == "device":
        return row.participant, (False, 0, row.device)
    return row.participant, (row.visit is None, row.visit or 0, f"visit {row.visit}")


def reliability(
    table: FeatureTable, between: Between, *, columns: Sequence[str] | None = None
) -> dict[str, Agreement]:
    """Agreement of each `task/feature` column between devices or between visits.

    The value of a participant on one occasion is the median over its accepted attempts. Only participants
    with a value on every occasion count. A column with fewer than two such participants is absent.
    """
    if columns is None:
        columns = sorted({column(r.task, name) for r in table.rows for name in r.cells})
    keys, values = medians(table, columns, lambda row: _key(between, row))
    order = sorted({occasion for _, occasion in keys})
    occasions = tuple(name for _, _, name in order)
    people = sorted({pid for pid, _ in keys})
    if len(occasions) < 2:
        return {}
    index = {key: i for i, key in enumerate(keys)}
    result: dict[str, Agreement] = {}
    for j, name in enumerate(columns):
        grid = np.full((len(people), len(occasions)), np.nan)
        for p, pid in enumerate(people):
            for o, occasion in enumerate(order):
                i = index.get((pid, occasion))
                if i is not None:
                    grid[p, o] = values[i, j]
        y = grid[np.isfinite(grid).all(axis=1)]
        if len(y) < 2:
            continue
        icc, sem, between_sd = icc_a1(y)
        pairs = []
        for a, b in itertools.combinations(range(len(occasions)), 2):
            d = y[:, b] - y[:, a]
            bias, sd = float(np.mean(d)), float(np.std(d, ddof=1))
            pairs.append(BlandAltman(occasions[a], occasions[b], bias, bias - Z95 * sd, bias + Z95 * sd))
        result[name] = Agreement(
            between, occasions, len(y), icc, sem, Z95 * math.sqrt(2) * sem, between_sd, tuple(pairs)
        )
    return result


class GateStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_ESTABLISHED = "not_established"


@dataclass(frozen=True)
class GateResult:
    status: GateStatus
    reason: str


def reliability_gate(agreement: Agreement | None, criteria: GateCriteria) -> GateResult:
    """Pass, fail, or not established for one feature, with the reason."""
    if agreement is None:
        return GateResult(GateStatus.NOT_ESTABLISHED, "No agreement data for this feature.")
    if agreement.between != criteria.between:
        return GateResult(
            GateStatus.NOT_ESTABLISHED,
            f"The agreement is between {agreement.between}s; the gate needs {criteria.between}s.",
        )
    if agreement.n < criteria.min_participants:
        return GateResult(
            GateStatus.NOT_ESTABLISHED,
            f"{agreement.n} participants; the gate needs {criteria.min_participants}.",
        )
    if math.isnan(agreement.icc.low):
        return GateResult(GateStatus.NOT_ESTABLISHED, "ICC(A,1) has no interval for these data.")
    if agreement.icc.low < criteria.min_icc_low:
        return GateResult(
            GateStatus.FAIL,
            f"ICC(A,1) {agreement.icc.point:.3f}, lower bound {agreement.icc.low:.3f}"
            f" < {criteria.min_icc_low}.",
        )
    worst = max(abs(pair.bias) for pair in agreement.bland_altman)
    if agreement.between_sd <= 0 or worst > criteria.max_bias_sd * agreement.between_sd:
        return GateResult(
            GateStatus.FAIL,
            f"Bias {worst:.4g} exceeds {criteria.max_bias_sd} between-participant SD"
            f" ({agreement.between_sd:.4g}).",
        )
    return GateResult(
        GateStatus.PASS,
        f"ICC(A,1) {agreement.icc.point:.3f} ({agreement.icc.low:.3f} to {agreement.icc.high:.3f}),"
        f" n {agreement.n}, MDC95 {agreement.mdc95:.4g}.",
    )
