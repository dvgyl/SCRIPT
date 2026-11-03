"""Sample size for prevalence-based screening studies, in closed form (DESIGN section 7).

Counts are expected counts: N enrolled, (1 - attrition) N usable, prevalence * usable positives. Each
interval is a Wilson score interval. Its half-width is (upper - lower) / 2, which is
z * sqrt(p (1 - p) n + z^2 / 4) / (n + z^2) for n observations at proportion p. The half-width falls as n
grows, so the smallest n for a target half-width w is the positive root of
w^2 n^2 + z^2 (2 w^2 - p (1 - p)) n + z^4 (w^2 - 1/4) = 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import norm

MEASURES = ("sensitivity", "specificity", "ppv", "npv")


def wilson_half_width(p: float, n: float, z: float) -> float:
    return z * math.sqrt(p * (1 - p) * n + z * z / 4) / (n + z * z)


def count_for_half_width(p: float, width: float, z: float) -> float:
    """The (real) number of observations at which the Wilson half-width equals `width`."""
    if not 0 < width < 0.5:
        raise ValueError("A Wilson half-width target must be between 0 and 0.5.")
    a = width**2
    b = z * z * (2 * width**2 - p * (1 - p))
    c = z**4 * (width**2 - 0.25)
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


@dataclass(frozen=True)
class Assumptions:
    """Assumed accuracy at a stated prevalence, and the share of enrolled participants that QC loses."""

    prevalence: float
    sensitivity: float
    specificity: float
    attrition: float = 0.0
    confidence: float = 0.95

    def __post_init__(self) -> None:
        for name in ("prevalence", "sensitivity", "specificity", "confidence"):
            if not 0 < getattr(self, name) < 1:
                raise ValueError(f"{name} must be between 0 and 1.")
        if not 0 <= self.attrition < 1:
            raise ValueError("attrition must be at least 0 and below 1.")

    @property
    def z(self) -> float:
        return float(norm.ppf((1 + self.confidence) / 2))

    @property
    def ppv(self) -> float:
        pi, se, sp = self.prevalence, self.sensitivity, self.specificity
        return se * pi / (se * pi + (1 - sp) * (1 - pi))

    @property
    def npv(self) -> float:
        pi, se, sp = self.prevalence, self.sensitivity, self.specificity
        return sp * (1 - pi) / (sp * (1 - pi) + (1 - se) * pi)

    def share(self, measure: str) -> tuple[float, float]:
        """The proportion that a measure estimates, and the share of enrolled people that it counts."""
        pi, se, sp, usable = self.prevalence, self.sensitivity, self.specificity, 1 - self.attrition
        test_positive = se * pi + (1 - sp) * (1 - pi)
        return {
            "sensitivity": (se, usable * pi),
            "specificity": (sp, usable * (1 - pi)),
            "ppv": (self.ppv, usable * test_positive),
            "npv": (self.npv, usable * (1 - test_positive)),
        }[measure]


@dataclass(frozen=True)
class Precision:
    """Expected counts and Wilson half-widths for N enrolled participants."""

    enrolled: int
    usable: float
    positives: float
    negatives: float
    ppv: float
    npv: float
    half_widths: dict[str, float]


def precision(assumptions: Assumptions, enrolled: int) -> Precision:
    usable = enrolled * (1 - assumptions.attrition)
    half_widths = {}
    for measure in MEASURES:
        p, share = assumptions.share(measure)
        half_widths[measure] = wilson_half_width(p, enrolled * share, assumptions.z)
    return Precision(
        enrolled,
        usable,
        usable * assumptions.prevalence,
        usable * (1 - assumptions.prevalence),
        assumptions.ppv,
        assumptions.npv,
        half_widths,
    )


def smallest_enrolment(assumptions: Assumptions, half_width: float) -> dict[str, int]:
    """The smallest N enrolled for each measure to reach the target half-width, and the largest as `all`."""
    result: dict[str, int] = {}
    for measure in MEASURES:
        p, share = assumptions.share(measure)
        n = math.ceil(count_for_half_width(p, half_width, assumptions.z) / share - 1e-9)
        while wilson_half_width(p, n * share, assumptions.z) > half_width:
            n += 1
        while n > 1 and wilson_half_width(p, (n - 1) * share, assumptions.z) <= half_width:
            n -= 1
        result[measure] = n
    result["all"] = max(result.values())
    return result
