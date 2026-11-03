"""The screening report and its JSON form."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from scriptkin.signal import Estimate

AUC_STATEMENT = (
    "The AUC compares cases and controls inside each test fold and averages over repeats. Significance comes"
    " from the permutation p-value, not from the interval. The interval is a participant bootstrap of fixed"
    " out-of-fold predictions, widened to at least the AUC +/- t(0.975, P - 1) times se_null, the standard"
    " deviation of the permutation null AUCs. The bootstrap omits the variation from refitting; the widening"
    " adds the null spread of the full procedure."
)
COUNTS_STATEMENT = (
    "tested counts the participants that a test fold scored, and cases counts the cases among them."
    " attrition.analysed counts the participants in the analysis set. A temporal holdout never tests its"
    " first block, so tested can be smaller than analysed."
)
DIGEST_STATEMENT = (
    "plan_digest and feature_digest identify the plan and the feature table that produced this report."
    " They do not prove when the plan was fixed. To show that the plan was fixed before the labels joined,"
    " register plan_digest before the analysis and give it to `scriptkin evaluate --expect-plan-digest`."
)
NORMS_STATEMENT = "A deviation from the normative model is not a diagnosis."
PPV_STATEMENT = (
    "PPV and NPV bounds combine the matching sensitivity and specificity bounds. Both measures rise with"
    " sensitivity and specificity, so the bounds are conservative and wider than a 95% interval."
)


class RefusedCode(StrEnum):
    LEAKAGE = "leakage"
    NULL_CHECK = "null_check"
    UNKNOWN_FEATURE = "unknown_feature"
    NO_RELIABLE_FEATURE = "no_reliable_feature"
    TOO_FEW_PARTICIPANTS = "too_few_participants"
    HOLDOUT = "holdout"


@dataclass(frozen=True)
class Refused:
    code: RefusedCode
    reason: str


@dataclass(frozen=True)
class Calibration:
    """Intercept from the model with logit(p) as offset; slope from logit(y) = a + b logit(p)."""

    intercept: float
    slope: float
    brier: float
    curve: tuple[tuple[float, float, int], ...]


@dataclass(frozen=True)
class ThresholdResult:
    """Metrics when risk at the stated prevalence is at least `threshold`. PPV and NPV use that prevalence."""

    threshold: float
    sensitivity: Estimate
    specificity: Estimate
    ppv: Estimate
    npv: Estimate


@dataclass(frozen=True)
class NetBenefit:
    threshold: float
    model: float
    treat_all: float
    treat_none: float


@dataclass(frozen=True)
class Subgroup:
    attribute: str
    level: str
    tested: int
    cases: int
    auc: Estimate
    brier: float
    mean_risk: float
    observed: float


@dataclass(frozen=True)
class AucResult:
    """An AUC with its 95% interval, the spread of its permutation null, and its permutation p-value.

    `se_null` is the standard deviation of the P null AUCs of the same plan. The interval is the participant
    bootstrap interval, widened to at least `point +/- t(0.975, P - 1) * se_null`. Under the null, the
    observed AUC and the null AUCs are exchangeable, so the widened interval covers 0.5 at about the nominal
    rate. `p_value` is (1 + the number of null AUCs at least `point`) / (1 + P). For the model, its null
    permutes inside sites (`NullCheck.site_aucs`); for a baseline, it is the null that gives `se_null`.
    """

    point: float
    low: float
    high: float
    se_null: float
    p_value: float


@dataclass(frozen=True)
class NullCheck:
    """Two permutation nulls of the full plan.

    `aucs` permute the labels between all participants, or inside each held-out site or device for a site
    or device holdout. They test that the pipeline is sound: their mean must be near 0.5. `site_aucs` permute
    inside each site and give the p-value of the model AUC; with one site they equal `aucs`.

    The check passes when the mean null AUC is inside 0.5 +/- t(0.995, P - 1) * sd / sqrt(P) for P
    permutations. `sd` is the larger of the standard deviation of the null AUCs and sigma0, the null standard
    deviation that the fold sizes imply for one repeat of fixed untied scores: the pair-weighted root sum of
    (n1 + n0 + 1) / (12 n1 n0) over the folds. The floor keeps the band from narrowing when a few skewed null
    AUCs happen to spread little, which would refuse sound plans. With several repeats the null can spread
    less than sigma0, so the check is then slightly loose. The band narrows as P grows, so a small P gives a
    weak check.

    `resolution` is 1 / (P + 1), the smallest p-value that the site permutations can give.
    """

    aucs: tuple[float, ...]
    mean: float
    sd: float
    low: float
    high: float
    site_aucs: tuple[float, ...]
    resolution: float


@dataclass(frozen=True)
class FoldRecord:
    """What the fits of one outer fold saw, taken from the witness log. Dates are first-session dates.

    `train_prevalence` is the case share of the outer training set; the prior shift starts from it.
    `out_of_range` counts the test participants whose covariates are outside the range of the norms fitted
    on the training set. Their deviations are unknown, so the training-fold median imputes them.
    """

    fold: int
    test_participants: int
    fit_participants: int
    test_sites: tuple[str, ...]
    fit_sites: tuple[str, ...]
    test_devices: tuple[str, ...]
    fit_devices: tuple[str, ...]
    test_earliest: str
    fit_latest: str
    train_prevalence: float
    out_of_range: int


@dataclass(frozen=True)
class Attrition:
    """Who left the analysis and why. `excluded` maps a reason to a participant count."""

    participants: int
    analysed: int
    excluded: dict[str, int]
    excluded_cases: int
    excluded_controls: int
    trials: int
    trials_refused: int
    trial_codes: dict[str, int]


@dataclass(frozen=True)
class GateRecord:
    column: str
    status: str
    reason: str


@dataclass(frozen=True)
class Caution:
    code: str
    detail: str


@dataclass(frozen=True)
class TripodItem:
    item: str
    topic: str
    content: str | None

    @property
    def author_supplied(self) -> bool:
        return self.content is None


# TRIPOD+AI (Collins et al., BMJ 2024;385:e078378) items with short topic names.
TRIPOD_AI = (
    ("1", "Title"),
    ("2", "Abstract"),
    ("3a", "Background: healthcare context and rationale"),
    ("3b", "Background: target population and intended purpose"),
    ("3c", "Background: known health inequalities"),
    ("4", "Objectives"),
    ("5a", "Data: sources"),
    ("5b", "Data: dates of collection"),
    ("6a", "Participants: setting"),
    ("6b", "Participants: eligibility"),
    ("6c", "Participants: treatments"),
    ("7", "Data preparation and quality checking"),
    ("8a", "Outcome: definition"),
    ("8b", "Outcome: assessor qualifications"),
    ("8c", "Outcome: blinding"),
    ("9a", "Predictors: choice and pre-selection"),
    ("9b", "Predictors: definition and measurement"),
    ("9c", "Predictors: assessor qualifications"),
    ("10", "Sample size"),
    ("11", "Missing data"),
    ("12a", "Analytical methods: data use and partitioning"),
    ("12b", "Analytical methods: predictor handling"),
    ("12c", "Analytical methods: model type, tuning, and internal validation"),
    ("12d", "Analytical methods: heterogeneity across clusters"),
    ("12e", "Analytical methods: performance measures"),
    ("12f", "Analytical methods: model updating"),
    ("12g", "Analytical methods: how predictions were calculated"),
    ("13", "Class imbalance"),
    ("14", "Fairness"),
    ("15", "Model output"),
    ("16", "Training versus evaluation"),
    ("17", "Ethical approval"),
    ("18a", "Funding"),
    ("18b", "Conflicts of interest"),
    ("18c", "Protocol"),
    ("18d", "Registration"),
    ("18e", "Data sharing"),
    ("18f", "Code sharing"),
    ("19", "Patient and public involvement"),
    ("20a", "Results: participant flow"),
    ("20b", "Results: participant characteristics"),
    ("20c", "Results: development and evaluation data compared"),
    ("21", "Model development: participants and events in each analysis"),
    ("22", "Model specification"),
    ("23a", "Model performance with intervals and subgroups"),
    ("23b", "Model performance heterogeneity across clusters"),
    ("24", "Model updating results"),
    ("25", "Interpretation"),
    ("26", "Limitations"),
    ("27a", "Usability: poor or unavailable input data"),
    ("27b", "Usability: user interaction and expertise"),
    ("27c", "Next steps"),
)


@dataclass(frozen=True)
class Screening:
    plan_digest: str
    feature_digest: str
    seed: int
    columns: tuple[str, ...]
    reliability: tuple[GateRecord, ...]
    tested: int
    cases: int
    sample_prevalence: float
    stated_prevalence: float
    auc: AucResult
    calibration: Calibration
    thresholds: tuple[ThresholdResult, ...]
    decision_curve: tuple[NetBenefit, ...]
    baselines: dict[str, AucResult]
    subgroups: tuple[Subgroup, ...]
    null: NullCheck
    attrition: Attrition
    folds: tuple[FoldRecord, ...]
    chosen: tuple[float, ...]
    cautions: tuple[Caution, ...]
    statements: tuple[str, ...]
    tripod: tuple[TripodItem, ...]

    def caution(self, code: str) -> Caution | None:
        return next((c for c in self.cautions if c.code == code), None)

    def to_json(self) -> dict[str, Any]:
        """A JSON-safe form. NaN becomes null."""
        return _clean(
            {
                "kind": "screening",
                **asdict(self),
                "tripod": [{**asdict(t), "authorSupplied": t.author_supplied} for t in self.tripod],
            }
        )


def _clean(value: Any) -> Any:
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    return value


def refused_json(refused: Refused) -> dict[str, Any]:
    return {"kind": "refused", "code": str(refused.code), "reason": refused.reason}
