from __future__ import annotations

import math

import pytest

from scriptkin.evaluate.planning import MEASURES, Assumptions, precision, smallest_enrolment

Z = 1.959963984540054


def wilson_bounds_half_width(p: float, n: float) -> float:
    centre = (p + Z * Z / (2 * n)) / (1 + Z * Z / n)
    spread = Z / (1 + Z * Z / n) * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))
    return ((centre + spread) - (centre - spread)) / 2


def test_planner_equals_hand_computed_wilson_half_widths() -> None:
    # Detects a planner whose expected counts or Wilson half-widths are wrong.
    result = precision(Assumptions(prevalence=0.1, sensitivity=0.8, specificity=0.9, attrition=0.2), 1000)
    assert (result.usable, result.positives, result.negatives) == pytest.approx((800, 80, 720))
    assert result.ppv == pytest.approx(0.08 / 0.17)
    assert result.npv == pytest.approx(0.81 / 0.83)
    # Hand computed from the Wilson bounds: p = 0.8 with 80 cases, 0.9 with 720 controls,
    # 0.81 / 0.83 with 664 test negatives.
    assert result.half_widths["sensitivity"] == pytest.approx(0.0867170, abs=1e-6)
    assert result.half_widths["specificity"] == pytest.approx(0.0219577, abs=1e-6)
    assert result.half_widths["npv"] == pytest.approx(0.0119481, abs=1e-6)
    assert result.half_widths["ppv"] == pytest.approx(wilson_bounds_half_width(0.08 / 0.17, 136), rel=1e-9)


def test_smallest_enrolment_reaches_the_target_and_one_fewer_does_not() -> None:
    # Detects an enrolment that misses the target half-width or is larger than the smallest N.
    assumptions = Assumptions(prevalence=0.08, sensitivity=0.75, specificity=0.85, attrition=0.15)
    smallest = smallest_enrolment(assumptions, 0.1)
    for measure in MEASURES:
        n = smallest[measure]
        assert (
            precision(assumptions, n).half_widths[measure]
            <= 0.1
            < precision(assumptions, n - 1).half_widths[measure]
        )
    assert smallest["all"] == max(smallest[m] for m in MEASURES) == smallest["sensitivity"]
