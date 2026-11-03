"""The evaluation harness: plans, screening, reliability, norms, and study size (DESIGN section 7)."""

from scriptkin.evaluate.norms import NormRow, Norms, centiles, fit_norms
from scriptkin.evaluate.plan import (
    KNN,
    LOGISTIC_L2,
    AnalysisPlan,
    GateCriteria,
    Holdout,
    Missing,
    ModelSpec,
    NormSpec,
    Target,
    column,
    freeze_features,
    plan_digest,
)
from scriptkin.evaluate.planning import precision, smallest_enrolment
from scriptkin.evaluate.reliability import Agreement, GateResult, GateStatus, reliability, reliability_gate
from scriptkin.evaluate.report import Refused, Screening
from scriptkin.evaluate.screening import screening

__all__ = [
    "KNN",
    "LOGISTIC_L2",
    "Agreement",
    "AnalysisPlan",
    "GateCriteria",
    "GateResult",
    "GateStatus",
    "Holdout",
    "Missing",
    "ModelSpec",
    "NormRow",
    "NormSpec",
    "Norms",
    "Refused",
    "Screening",
    "Target",
    "centiles",
    "column",
    "fit_norms",
    "freeze_features",
    "plan_digest",
    "precision",
    "reliability",
    "reliability_gate",
    "screening",
    "smallest_enrolment",
]
