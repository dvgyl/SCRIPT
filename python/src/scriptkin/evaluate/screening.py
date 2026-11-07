"""Screening evaluation with a leakage witness and a permutation null (DESIGN section 7).

Each participant gives one row: the median over accepted attempts of each planned column. The outer
split follows the plan holdout. Inside each outer training set, a grouped inner search picks the model
setting, and the out-of-fold scores of that search fit a Platt calibrator, so calibration never sees the
outer test fold. A `FitWitness` at the head of every pipeline records the participants of each fit. The
harness reports metrics only after the witness shows that no test participant was in a fit of its fold
and the permutation null is centred at chance.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from scipy.special import expit, logit
from scipy.stats import fisher_exact
from scipy.stats import t as t_distribution
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold
from sklearn.neighbors import KNeighborsClassifier

from scriptkin.evaluate.metrics import (
    bootstrap,
    clipped_logit,
    fold_auc,
    fold_auc_null_sd,
    logistic_fit,
    wilson,
)
from scriptkin.evaluate.norms import NormRow, Norms, deviations, fit_norms, outside
from scriptkin.evaluate.participants import Participant, medians, participants, refused_trials
from scriptkin.evaluate.plan import (
    LOGISTIC_L2,
    AnalysisPlan,
    Holdout,
    Missing,
    ModelKind,
    ModelSpec,
    NormSpec,
    Target,
    column,
    freeze_features,
    plan_digest,
)
from scriptkin.evaluate.reliability import Agreement, GateStatus, reliability_gate
from scriptkin.evaluate.report import (
    AUC_STATEMENT,
    COUNTS_STATEMENT,
    DIGEST_STATEMENT,
    NORMS_STATEMENT,
    PPV_STATEMENT,
    TRIPOD_AI,
    Attrition,
    AucResult,
    Calibration,
    Caution,
    FoldRecord,
    GateRecord,
    NetBenefit,
    NullCheck,
    Refused,
    RefusedCode,
    Screening,
    Subgroup,
    ThresholdResult,
    TripodItem,
)
from scriptkin.features import FeatureTable
from scriptkin.labels import Label
from scriptkin.quality import Refusal
from scriptkin.recording import FloatArray, IntArray
from scriptkin.signal import Estimate

DECISION_GRID = tuple(round(t / 100, 2) for t in range(1, 51))


@dataclass(frozen=True)
class FitRecord:
    fold: int
    stage: str
    participants: frozenset[str]
    rows: frozenset[int] = frozenset()


@dataclass
class FitWitness:
    """The head of every pipeline. Column 0 of the input is the row index into `ids`; the rest passes."""

    log: list[FitRecord]
    ids: Sequence[str]
    fold: int
    stage: str

    def fit(self, x: FloatArray, y: IntArray) -> None:
        self.log.append(
            FitRecord(
                self.fold,
                self.stage,
                frozenset(self.ids[int(i)] for i in x[:, 0]),
                frozenset(int(i) for i in x[:, 0]),
            )
        )

    def transform(self, x: FloatArray) -> FloatArray:
        return x


class Step(Protocol):
    def fit(self, x: FloatArray, y: IntArray) -> None: ...

    def transform(self, x: FloatArray) -> FloatArray: ...


class Model(Protocol):
    def fit(self, x: FloatArray, y: IntArray) -> None: ...

    def score(self, x: FloatArray) -> FloatArray: ...


@dataclass
class _DropId:
    def fit(self, x: FloatArray, y: IntArray) -> None:
        pass

    def transform(self, x: FloatArray) -> FloatArray:
        return x[:, 1:]


@dataclass
class _Impute:
    """Median imputation with the training medians. A column with no training value becomes 0."""

    medians: FloatArray = field(default_factory=lambda: np.empty(0))

    def fit(self, x: FloatArray, y: IntArray) -> None:
        self.medians = np.array(
            [float(np.median(c[np.isfinite(c)])) if np.isfinite(c).any() else 0.0 for c in x.T]
        )

    def transform(self, x: FloatArray) -> FloatArray:
        return np.where(np.isfinite(x), x, self.medians[None, :])


@dataclass
class _Standardize:
    mean: FloatArray = field(default_factory=lambda: np.empty(0))
    sd: FloatArray = field(default_factory=lambda: np.empty(0))

    def fit(self, x: FloatArray, y: IntArray) -> None:
        self.mean = x.mean(axis=0)
        sd = x.std(axis=0)
        self.sd = np.where(sd > 0, sd, 1.0)

    def transform(self, x: FloatArray) -> FloatArray:
        return (x - self.mean) / self.sd


@dataclass
class _Deviation:
    """Replace each feature by its deviation from norms fitted on the rows of this fit only."""

    covariates: Sequence[Mapping[str, str]]
    columns: Sequence[str]
    spec: NormSpec
    norms: Norms | None = None

    def _rows(self, x: FloatArray) -> list[NormRow]:
        return [
            NormRow(self.covariates[int(r[0])], dict(zip(self.columns, map(float, r[1:]), strict=True)))
            for r in x
        ]

    def fit(self, x: FloatArray, y: IntArray) -> None:
        self.norms = fit_norms(self._rows(x), self.spec.covariates)

    def transform(self, x: FloatArray) -> FloatArray:
        assert self.norms is not None
        z = deviations(self.norms, self._rows(x))
        return np.column_stack([x[:, 0], *(z.get(c, np.full(len(x), np.nan)) for c in self.columns)])

    def count_outside(self, x: FloatArray) -> int:
        """The rows whose covariates are outside the range of the fitted norms."""
        assert self.norms is not None
        return sum(outside(self.norms, r) is not None for r in self._rows(x))


@dataclass
class _Logistic:
    """Minimizes mean log loss + |w|^2 / (2c). The penalty is per observation, so a value chosen on an inner
    training set gives coefficients of the same scale on the larger outer training set. The calibrator,
    fitted on inner out-of-fold scores, then matches the scores of the final model."""

    c: float
    model: LogisticRegression | None = None

    def fit(self, x: FloatArray, y: IntArray) -> None:
        self.model = LogisticRegression(C=self.c / len(y), max_iter=5000).fit(x, y)

    def score(self, x: FloatArray) -> FloatArray:
        assert self.model is not None
        return np.asarray(self.model.decision_function(x), dtype=float)


@dataclass
class _Neighbours:
    k: int
    used: int = 0
    model: KNeighborsClassifier | None = None

    def fit(self, x: FloatArray, y: IntArray) -> None:
        self.used = min(self.k, len(y))
        self.model = KNeighborsClassifier(n_neighbors=self.used).fit(x, y)

    def score(self, x: FloatArray) -> FloatArray:
        assert self.model is not None
        share = np.asarray(self.model.predict_proba(x), dtype=float)[:, 1]
        return clipped_logit((share * self.used + 0.5) / (self.used + 1))


@dataclass
class _Platt:
    """Logistic recalibration of one score column, with a small ridge against separation."""

    coefficients: FloatArray = field(default_factory=lambda: np.zeros(2))

    def fit(self, x: FloatArray, y: IntArray) -> None:
        self.coefficients = logistic_fit(np.column_stack((np.ones(len(x)), x[:, 0])), y, ridge=1e-4)

    def score(self, x: FloatArray) -> FloatArray:
        return self.coefficients[0] + self.coefficients[1] * x[:, 0]


@dataclass
class Pipeline:
    witness: FitWitness
    steps: list[Step]
    model: Model

    def fit(self, x: FloatArray, y: IntArray) -> None:
        self.witness.fit(x, y)
        for step in self.steps:
            step.fit(x, y)
            x = step.transform(x)
        self.model.fit(x, y)

    def score(self, x: FloatArray) -> FloatArray:
        for step in self.steps:
            x = step.transform(x)
        return self.model.score(x)


@dataclass(frozen=True)
class Design:
    """One row per participant: features with NaN for missing values, labels, and context for norms."""

    x: FloatArray
    y: IntArray
    ids: tuple[str, ...]
    columns: tuple[str, ...]
    covariates: tuple[Mapping[str, str], ...]


@dataclass(frozen=True)
class Fold:
    train: IntArray
    test: IntArray


Splits = Sequence[Sequence[Fold]]
# One plan for a permutation null: the design, the model, and the norms.
Arm = tuple[FloatArray, ModelSpec, NormSpec | None]


@dataclass(frozen=True)
class FoldFit:
    """The outer fit of one fold: its grid value, its training prevalence, and its test rows outside the
    covariate range of its norms."""

    chosen: float
    prevalence: float
    out_of_range: int


@dataclass(frozen=True)
class CrossFit:
    """Calibrated out-of-fold probabilities and the fold number of each row, one line per repeat.

    A row that a repeat did not test has probability NaN and fold -1. `fits` has one entry per fold number.
    """

    probabilities: FloatArray
    folds: IntArray
    fits: tuple[FoldFit, ...]
    log: tuple[FitRecord, ...]

    def auc(self, y: IntArray, rows: IntArray) -> float:
        return fold_auc(self.probabilities, self.folds, y, rows)

    def shifted(self, stated: float) -> FloatArray:
        """Each probability moved from the training prevalence of its own fold to `stated`.

        The calibrator of a fold learns that fold's training prevalence, so a pooled prevalence would move
        the risks of different folds by the wrong amounts.
        """
        prevalence = np.array([f.prevalence for f in self.fits])[np.maximum(self.folds, 0)]
        return np.where(self.folds >= 0, prior_shift(self.probabilities, prevalence, stated), np.nan)


def grouped_folds(y: IntArray, groups: Sequence[str], k: int, seed: int) -> list[tuple[IntArray, IntArray]]:
    """Stratified folds that keep each group in one fold.

    When no group repeats, every group is one row, so `StratifiedKFold` gives the same guarantee much faster
    than `StratifiedGroupKFold`.
    """
    state = seed % 2**32
    if len(set(groups)) == len(groups):
        split = StratifiedKFold(k, shuffle=True, random_state=state).split(np.zeros(len(y)), y)
    else:
        split = StratifiedGroupKFold(k, shuffle=True, random_state=state).split(np.zeros(len(y)), y, groups)
    return [(np.asarray(a), np.asarray(b)) for a, b in split]


def _model(spec: ModelSpec, value: float) -> Model:
    return _Logistic(value) if spec.kind == ModelKind.LOGISTIC_L2 else _Neighbours(int(value))


def crossfit(
    design: Design,
    model: ModelSpec,
    splits: Splits,
    *,
    inner_folds: int,
    seed: int,
    norms: NormSpec | None = None,
) -> CrossFit:
    """Fit and score every outer fold of every repeat. Fold numbers run over repeats in order."""
    n = len(design.y)
    rows = np.column_stack((np.arange(n, dtype=float), design.x))
    log: list[FitRecord] = []
    probabilities = np.full((len(splits), n), np.nan)
    fold_of = np.full((len(splits), n), -1, dtype=np.int64)
    fits: list[FoldFit] = []
    number = 0
    for r, folds in enumerate(splits):
        for fold in folds:

            def make(value: float, stage: str, number: int = number) -> Pipeline:
                steps: list[Step] = []
                if norms is not None:
                    steps.append(_Deviation(design.covariates, design.columns, norms))
                steps += [_DropId(), _Impute(), _Standardize()]
                return Pipeline(FitWitness(log, design.ids, number, stage), steps, _model(model, value))

            train, y_train = fold.train, design.y[fold.train]
            groups = [design.ids[i] for i in train]
            inner = grouped_folds(y_train, groups, inner_folds, seed + number)
            out_of_fold = np.full((len(model.grid), len(train)), np.nan)
            inner_of = np.full((1, len(train)), -1, dtype=np.int64)
            for k, (inner_train, inner_test) in enumerate(inner):
                inner_of[0, inner_test] = k
                for g, value in enumerate(model.grid):
                    pipeline = make(value, "search")
                    pipeline.fit(rows[train[inner_train]], y_train[inner_train])
                    out_of_fold[g, inner_test] = pipeline.score(rows[train[inner_test]])
            everyone = np.arange(len(train))
            best = int(np.nanargmax([fold_auc(o[None, :], inner_of, y_train, everyone) for o in out_of_fold]))
            calibrator = Pipeline(FitWitness(log, design.ids, number, "calibration"), [_DropId()], _Platt())
            calibrator.fit(np.column_stack((train.astype(float), out_of_fold[best])), y_train)
            final = make(model.grid[best], "final")
            final.fit(rows[train], y_train)
            raw = final.score(rows[fold.test])
            probabilities[r, fold.test] = expit(
                calibrator.score(np.column_stack((fold.test.astype(float), raw)))
            )
            fold_of[r, fold.test] = number
            out_of_range = sum(
                step.count_outside(rows[fold.test]) for step in final.steps if isinstance(step, _Deviation)
            )
            fits.append(FoldFit(model.grid[best], float(y_train.mean()), out_of_range))
            number += 1
    return CrossFit(probabilities, fold_of, tuple(fits), tuple(log))


def witness_refusal(cross: CrossFit, splits: Splits, ids: Sequence[str]) -> Refused | None:
    """Refuse when a test participant appears in any fit of its fold, or a fold has no witnessed final fit.

    The reason gives a count only. It goes into the report file, which must not name a participant.
    """
    tests = [frozenset(ids[i] for i in fold.test) for folds in splits for fold in folds]
    for record in cross.log:
        overlap = record.participants & tests[record.fold]
        if overlap:
            return Refused(
                RefusedCode.LEAKAGE,
                f"Fold {record.fold}: the {record.stage} fit includes {len(overlap)} of the fold's test"
                " participants.",
            )
    finals = {record.fold for record in cross.log if record.stage == "final"}
    if finals != set(range(len(tests))):
        return Refused(RefusedCode.LEAKAGE, "A fold has no witnessed final fit.")
    return None


def _first_label(
    labels: Iterable[Label], target: Target, order: Mapping[str, tuple[bool, str, str]]
) -> dict[str, Label]:
    """For each participant, the target label of its earliest session in the feature table."""
    chosen: dict[str, Label] = {}
    for label in labels:
        if (label.instrument, label.version, label.scale) != (
            target.instrument,
            target.version,
            target.scale,
        ):
            continue
        if label.session not in order:
            continue
        current = chosen.get(label.participant)
        if current is None or (order[label.session], label.block) < (order[current.session], current.block):
            chosen[label.participant] = label
    return chosen


def _permute_within(y: IntArray, strata: Sequence[str], rng: np.random.Generator) -> IntArray:
    """Permute the labels between participants of the same stratum."""
    permuted = y.copy()
    for stratum in sorted(set(strata)):
        rows = np.flatnonzero(np.asarray(strata) == stratum)
        permuted[rows] = y[rng.permutation(rows)]
    return permuted


def outer_splits(
    plan: AnalysisPlan, y: IntArray, people: Sequence[Participant], seed: int
) -> list[list[Fold]] | Refused:
    if plan.holdout == Holdout.PARTICIPANT:
        result: list[list[Fold]] = []
        for r in range(plan.repeats):
            result.append(
                [Fold(a, b) for a, b in grouped_folds(y, [p.id for p in people], plan.folds, seed + r)]
            )
        return result
    if plan.holdout == Holdout.TEMPORAL:
        if any(not p.first_opened for p in people):
            return Refused(
                RefusedCode.HOLDOUT, "A temporal holdout needs a session date for every participant."
            )
        order = np.argsort([p.first_opened for p in people], kind="stable")
        blocks = np.array_split(order, plan.folds)
        return [[Fold(np.sort(np.concatenate(blocks[:i])), np.sort(blocks[i])) for i in range(1, plan.folds)]]
    attribute = "site" if plan.holdout == Holdout.SITE else "device"
    levels = [getattr(p, attribute) for p in people]
    if any("+" in level for level in levels):
        return Refused(
            RefusedCode.HOLDOUT, f"A {attribute} holdout needs one {attribute} for each participant."
        )
    if len(set(levels)) < 2:
        return Refused(RefusedCode.HOLDOUT, f"A {attribute} holdout needs two or more {attribute}s.")
    return [
        [
            Fold(np.flatnonzero(np.asarray(levels) != level), np.flatnonzero(np.asarray(levels) == level))
            for level in sorted(set(levels))
        ]
    ]


def _check_sizes(splits: Splits, y: IntArray, inner_folds: int) -> Refused | None:
    for folds in splits:
        for fold in folds:
            cases = int(y[fold.train].sum())
            if min(cases, len(fold.train) - cases) < inner_folds:
                return Refused(
                    RefusedCode.TOO_FEW_PARTICIPANTS,
                    f"A training set has {cases} cases and {len(fold.train) - cases} controls;"
                    f" the inner search needs {inner_folds} of each.",
                )
    return None


def _mean_over_repeats(values: FloatArray) -> FloatArray:
    tested = np.isfinite(values)
    total = np.where(tested, values, 0.0).sum(axis=0)
    count = tested.sum(axis=0)
    return np.where(count > 0, total / np.maximum(count, 1), np.nan)


def _one_hot(values: Sequence[str]) -> FloatArray:
    levels = sorted(set(values))
    return np.array([[float(v == level) for level in levels] for v in values]).reshape(
        len(values), len(levels)
    )


def prior_shift(p: FloatArray, sample: FloatArray | float, stated: float) -> FloatArray:
    """Move risks from the prevalence they were calibrated at to the stated prevalence (Saerens et al. 2002).

    `sample` is one prevalence, or one for each risk.
    """
    return np.asarray(expit(clipped_logit(p) + logit(stated) - logit(sample)))


def _ppv(se: float, sp: float, prevalence: float) -> float:
    positive = se * prevalence + (1 - sp) * (1 - prevalence)
    return se * prevalence / positive if positive > 0 else math.nan


def _npv(se: float, sp: float, prevalence: float) -> float:
    negative = sp * (1 - prevalence) + (1 - se) * prevalence
    return sp * (1 - prevalence) / negative if negative > 0 else math.nan


def threshold_metrics(p: FloatArray, y: IntArray, threshold: float, prevalence: float) -> ThresholdResult:
    """Sensitivity and specificity with Wilson intervals; PPV and NPV at the prevalence by Bayes' rule.

    The PPV and NPV intervals combine the matching sensitivity and specificity bounds; both are monotone.
    """
    positive = p >= threshold
    se = wilson(float(np.sum(positive & (y == 1))), float(np.sum(y == 1)))
    sp = wilson(float(np.sum(~positive & (y == 0))), float(np.sum(y == 0)))
    return ThresholdResult(
        threshold,
        se,
        sp,
        Estimate(
            _ppv(se.point, sp.point, prevalence),
            _ppv(se.low, sp.low, prevalence),
            _ppv(se.high, sp.high, prevalence),
        ),
        Estimate(
            _npv(se.point, sp.point, prevalence),
            _npv(se.low, sp.low, prevalence),
            _npv(se.high, sp.high, prevalence),
        ),
    )


def calibration(p: FloatArray, y: IntArray, bins: int = 10) -> Calibration:
    lp = clipped_logit(p)
    slope = float(logistic_fit(np.column_stack((np.ones(len(p)), lp)), y)[1])
    intercept = float(logistic_fit(np.ones((len(p), 1)), y, offset=lp)[0])
    order = np.argsort(p, kind="stable")
    curve = tuple(
        (float(p[rows].mean()), float(y[rows].mean()), len(rows))
        for rows in np.array_split(order, min(bins, len(p)))
        if len(rows)
    )
    return Calibration(intercept, slope, float(np.mean((p - y) ** 2)), curve)


def net_benefit(p: FloatArray, y: IntArray, threshold: float, prevalence: float) -> NetBenefit:
    """Net benefit at the stated prevalence: sensitivity * pi - (1 - specificity) * (1 - pi) * t / (1 - t)."""
    odds = threshold / (1 - threshold)
    positive = p >= threshold
    se = float(np.mean(positive[y == 1]))
    sp = float(np.mean(~positive[y == 0]))
    return NetBenefit(
        threshold,
        se * prevalence - (1 - sp) * (1 - prevalence) * odds,
        prevalence - (1 - prevalence) * odds,
        0.0,
    )


def _subgroups(
    cross: CrossFit,
    p: FloatArray,
    y: IntArray,
    tested: IntArray,
    people: Sequence[Participant],
    count: int,
    rng: np.random.Generator,
) -> tuple[Subgroup, ...]:
    result: list[Subgroup] = []
    for attribute in ("age_band", "administration", "device", "site"):
        levels = np.asarray([getattr(people[i], attribute) for i in tested])
        for level in sorted(set(levels)):
            rows = tested[levels == level]
            result.append(
                Subgroup(
                    attribute,
                    str(level),
                    len(rows),
                    int(y[rows].sum()),
                    bootstrap(lambda r: cross.auc(y, r), y, rows, count, rng),
                    float(np.mean((p[rows] - y[rows]) ** 2)),
                    float(p[rows].mean()),
                    float(y[rows].mean()),
                )
            )
    return tuple(result)


def _fold_records(cross: CrossFit, splits: Splits, people: Sequence[Participant]) -> tuple[FoldRecord, ...]:
    by_id = {p.id: p for p in people}
    tests = [fold.test for folds in splits for fold in folds]
    fitted: list[set[str]] = [set() for _ in tests]
    for record in cross.log:
        fitted[record.fold] |= record.participants
    records: list[FoldRecord] = []
    for number, (test, fit, fold_fit) in enumerate(zip(tests, fitted, cross.fits, strict=True)):
        test_people = [people[i] for i in test]
        fit_people = [by_id[pid] for pid in fit]
        records.append(
            FoldRecord(
                number,
                len(test_people),
                len(fit_people),
                tuple(sorted({p.site for p in test_people})),
                tuple(sorted({p.site for p in fit_people})),
                tuple(sorted({p.device for p in test_people})),
                tuple(sorted({p.device for p in fit_people})),
                min(p.first_opened for p in test_people),
                max(p.first_opened for p in fit_people),
                fold_fit.prevalence,
                fold_fit.out_of_range,
            )
        )
    return tuple(records)


def _auc_result(boot: Estimate, null_aucs: Sequence[float], p_value: float, t975: float) -> AucResult:
    """The bootstrap interval widened to at least the point +/- t975 times the null standard deviation."""
    se_null = float(np.std(null_aucs, ddof=1))
    low = float(np.fmin(boot.low, boot.point - t975 * se_null))
    high = float(np.fmax(boot.high, boot.point + t975 * se_null))
    return AucResult(boot.point, max(0.0, low), min(1.0, high), se_null, p_value)


def _p_value(null_aucs: Sequence[float], observed: float) -> float:
    return (1 + sum(a >= observed for a in null_aucs)) / (1 + len(null_aucs))


def _baseline_caution(name: str, baseline: AucResult, model: AucResult, t975: float) -> Caution | None:
    """A baseline beyond its own null spread that reaches the model interval can explain the result."""
    if baseline.point - 0.5 > t975 * baseline.se_null and baseline.point >= model.low:
        return Caution(
            f"{name}_baseline",
            f"The {name}-only baseline reaches AUC {baseline.point:.3f}, more than {t975:.2f} null standard"
            f" deviations ({baseline.se_null:.3f}) above 0.5 and inside or above the model interval"
            f" {model.low:.3f} to {model.high:.3f}. The {name} can explain the result.",
        )
    return None


def _tripod(
    plan: AnalysisPlan,
    digest: str,
    used: Sequence[str],
    attrition: Attrition,
    tested: int,
    cases: int,
    auc_estimate: AucResult,
    cal: Calibration,
) -> tuple[TripodItem, ...]:
    target = plan.target
    known = {
        "7": f"QC rejected {attrition.trials_refused} of {attrition.trials} trials ({attrition.trial_codes})."
        " A participant value is the median over accepted, non-practice attempts.",
        "8a": f"{target.instrument} {target.version} {target.scale} score >= {target.cut}, from the earliest"
        " session of each participant that has this label.",
        "9a": f"{len(used)} planned columns: {', '.join(used)}.",
        "11": f"{plan.missing}: imputation, when used, takes training-fold medians.",
        "12a": f"Holdout {plan.holdout}, {plan.folds} folds,"
        f" {plan.repeats} repeats where the holdout allows.",
        "12b": "Training-fold median imputation and standardization"
        + (
            f"; deviations from norms on {', '.join(plan.norms.covariates)}, fitted without labels on all"
            " participants of the training fold, cases included, as a general-population reference"
            if plan.norms
            else ""
        )
        + ".",
        "12c": f"{plan.model.kind} with grid {list(plan.model.grid)}, chosen by grouped inner AUC over"
        f" {plan.inner_folds} folds; a FitWitness checks every fit.",
        "12d": "Results by site, device, age band, and administration.",
        "12e": "AUC with a participant bootstrap interval widened to the spread of its permutation null, and"
        " its permutation p-value; calibration intercept, slope, curve, Brier score, sensitivity,"
        " specificity, PPV, NPV, and net benefit.",
        "12f": "Platt recalibration cross-fitted inside each outer training set; prior shift from the"
        " training prevalence of each fold to the stated prevalence for thresholds.",
        "13": "No resampling. Risks are moved to the stated prevalence by the prior-shift adjustment.",
        "14": "Subgroup results are reported. No fairness constraint is applied.",
        "15": f"A calibrated probability; thresholds {list(plan.thresholds)} from the plan.",
        "18c": f"Analysis plan SHA-256 {digest}.",
        "20a": f"{attrition.participants} participants, {attrition.analysed} analysed,"
        f" excluded {attrition.excluded}.",
        "21": f"{tested} tested participants with {cases} cases, of {attrition.analysed} in the analysis"
        " set.",
        "23a": f"AUC {auc_estimate.point:.3f} ({auc_estimate.low:.3f} to {auc_estimate.high:.3f}),"
        f" permutation p {auc_estimate.p_value:.3g}; calibration slope {cal.slope:.3f}; subgroup results"
        " reported.",
        "23b": "Per-site and per-device results are reported.",
    }
    return tuple(TripodItem(item, topic, known.get(item)) for item, topic in TRIPOD_AI)


def _analysis_set(
    table: FeatureTable, labels: Iterable[Label], plan: AnalysisPlan, used: Sequence[str]
) -> tuple[Design, list[Participant], Attrition] | Refused:
    """Join each participant's first target label to its medians. Count who leaves and why."""
    everyone = participants(table)
    keys, values = medians(table, used, lambda row: row.participant)
    order = {r.session: (r.opened_at is None, r.opened_at or "", r.session) for r in table.rows}
    chosen = _first_label(labels, plan.target, order)
    accepted = {r.participant for r in table.rows if not r.qc}
    excluded: dict[str, int] = {}
    excluded_classes = [0, 0]
    kept: list[int] = []
    outcome: list[int] = []
    for i, pid in enumerate(keys):
        label = chosen.get(pid)
        if label is None:
            excluded["no_label"] = excluded.get("no_label", 0) + 1
            continue
        if isinstance(label.score, Refusal):
            reason = f"label_refused:{label.score.code}"
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        case = int(label.score >= plan.target.cut)
        reason = (
            "no_accepted_trial"
            if pid not in accepted
            else "no_feature_value"
            if not np.isfinite(values[i]).any()
            else "missing_feature"
            if plan.missing == Missing.COMPLETE_CASE and not np.isfinite(values[i]).all()
            else None
        )
        if reason is None:
            kept.append(i)
            outcome.append(case)
        else:
            excluded[reason] = excluded.get(reason, 0) + 1
            excluded_classes[case] += 1
    trials, refused, codes = refused_trials(table, {c.partition("/")[0] for c in used})
    attrition = Attrition(
        len(keys),
        len(kept),
        dict(sorted(excluded.items())),
        excluded_classes[1],
        excluded_classes[0],
        trials,
        refused,
        codes,
    )
    y = np.asarray(outcome, dtype=np.int64)
    cases = int(y.sum())
    if min(cases, len(y) - cases) < 2:
        return Refused(RefusedCode.TOO_FEW_PARTICIPANTS, f"{cases} cases and {len(y) - cases} controls.")
    people = [everyone[keys[i]] for i in kept]
    design = Design(
        values[kept],
        y,
        tuple(p.id for p in people),
        tuple(used),
        tuple({"age_band": p.age_band, "hand": p.hand, "administration": p.administration} for p in people),
    )
    return design, people, attrition


def null_check(
    null_aucs: Sequence[float], site_aucs: Sequence[float], folds: IntArray, y: IntArray
) -> NullCheck:
    """The mean null AUC, its band 0.5 +/- t(0.995, P - 1) * sd / sqrt(P), and the p-value resolution."""
    spread = max(fold_auc_null_sd(folds, y), float(np.std(null_aucs, ddof=1)))
    half = float(t_distribution.ppf(0.995, len(null_aucs) - 1)) * spread / math.sqrt(len(null_aucs))
    return NullCheck(
        tuple(null_aucs),
        float(np.mean(null_aucs)),
        spread,
        0.5 - half,
        0.5 + half,
        tuple(site_aucs),
        1 / (1 + len(site_aucs)),
    )


def screening(
    table: FeatureTable,
    labels: Iterable[Label],
    *,
    plan: AnalysisPlan,
    seed: int,
    reliability: Mapping[str, Agreement] | None = None,
) -> Screening | Refused:
    """Evaluate the plan on the table and labels. Return metrics only after the witness and the null pass."""
    present = {column(r.task, name) for r in table.rows for name in r.cells}
    unknown = [c for c in plan.features if c not in present]
    if unknown:
        return Refused(RefusedCode.UNKNOWN_FEATURE, f"The table has no {', '.join(unknown)}.")

    cautions: list[Caution] = []
    if reliability is None or plan.gate is None:
        why = "The plan has no reliability gate." if plan.gate is None else "No reliability data was given."
        gates = tuple(GateRecord(c, str(GateStatus.NOT_ESTABLISHED), why) for c in plan.features)
        used = list(plan.features)
        cautions.append(
            Caution(
                "reliability_not_established",
                f"Reliability is not established. Features used: {', '.join(used)}.",
            )
        )
    else:
        results = {c: reliability_gate(reliability.get(c), plan.gate) for c in plan.features}
        gates = tuple(GateRecord(c, str(r.status), r.reason) for c, r in results.items())
        used = [c for c in plan.features if results[c].status == GateStatus.PASS]
        if not used:
            return Refused(RefusedCode.NO_RELIABLE_FEATURE, "No planned feature passed the reliability gate.")

    analysis = _analysis_set(table, labels, plan, used)
    if isinstance(analysis, Refused):
        return analysis
    design, people, attrition = analysis
    y = design.y
    split_seq, null_seq, boot_seq = np.random.SeedSequence(seed).spawn(3)
    split_seed = int(split_seq.generate_state(1)[0])

    def run(
        labels_y: IntArray,
        x: FloatArray,
        spec: ModelSpec,
        norms: NormSpec | None,
        split_seed: int = split_seed,
    ) -> tuple[CrossFit, Splits] | Refused:
        splits = outer_splits(plan, labels_y, people, split_seed)
        if isinstance(splits, Refused):
            return splits
        small = _check_sizes(splits, labels_y, plan.inner_folds)
        if small is not None:
            return small
        trial_design = Design(x, labels_y, design.ids, design.columns, design.covariates)
        cross = crossfit(
            trial_design, spec, splits, inner_folds=plan.inner_folds, seed=split_seed, norms=norms
        )
        leak = witness_refusal(cross, splits, design.ids)
        return leak if leak is not None else (cross, splits)

    observed = run(y, design.x, plan.model, plan.norms)
    if isinstance(observed, Refused):
        return observed
    cross, splits = observed
    p_all = _mean_over_repeats(cross.probabilities)
    tested = np.flatnonzero(np.isfinite(p_all))
    p, y_tested = p_all[tested], y[tested]
    boot = np.random.default_rng(boot_seq)
    model_boot = bootstrap(lambda rows: cross.auc(y, rows), y, tested, plan.bootstrap, boot)
    if math.isnan(model_boot.point):
        return Refused(RefusedCode.TOO_FEW_PARTICIPANTS, "No test fold has both cases and controls.")

    baseline_designs = {
        "demographic": np.column_stack(
            (_one_hot([q.age_band for q in people]), _one_hot([q.administration for q in people]))
        ),
        "device": _one_hot([q.device for q in people]),
        "site": _one_hot([q.site for q in people]),
    }
    baseline_boot: dict[str, Estimate] = {}
    for name, x in baseline_designs.items():
        base = run(y, x, LOGISTIC_L2, None)
        if isinstance(base, Refused):
            return base
        baseline_boot[name] = bootstrap(
            lambda rows, b=base[0]: b.auc(y, rows), y, tested, plan.bootstrap, boot
        )
    # A constant design ties every pair under any labelling: its null is its observed AUC and needs no runs.
    varying = [name for name, x in baseline_designs.items() if np.ptp(x, axis=0).any()]

    def permutation_null(
        strata: Sequence[str], sequence: np.random.SeedSequence, arms: Sequence[Arm]
    ) -> list[list[float]] | Refused:
        """Run the plan of each arm on labels permuted inside each stratum, one AUC list per arm.

        Each permutation draws its own split seed: stratified fold assignment depends on the labels and on a
        fixed row order, so one split seed for every permutation would condition the null on that order. A
        permutation that leaves a training set too few cases (possible in a temporal holdout) is drawn again.
        Every arm of one permutation uses the same labels and splits.
        """
        aucs: list[list[float]] = [[] for _ in arms]
        for permutation_seq in sequence.spawn(plan.permutations):
            rng = np.random.default_rng(permutation_seq)
            result: tuple[CrossFit, Splits] | Refused = Refused(RefusedCode.TOO_FEW_PARTICIPANTS, "")
            permuted, split = y, split_seed
            for _ in range(20):
                permuted = _permute_within(y, strata, rng)
                split = int(rng.integers(2**32))
                result = run(permuted, *arms[0], split)
                if not (isinstance(result, Refused) and result.code == RefusedCode.TOO_FEW_PARTICIPANTS):
                    break
            for k, arm in enumerate(arms):
                arm_result = result if k == 0 else run(permuted, *arm, split)
                if isinstance(arm_result, Refused):
                    return Refused(arm_result.code, f"A permuted null run failed: {arm_result.reason}")
                aucs[k].append(arm_result[0].auc(permuted, tested))
        return aucs

    # The band needs an exchangeable null. With participant or temporal folds, a test fold mixes sites, so a
    # permutation inside sites would keep site prevalence that features can predict, and its null AUC would
    # not centre on 0.5. A site or device holdout scores pairs inside one held-out group, so permuting inside
    # that group keeps the fold sizes and still centres on 0.5. The model p-value permutes inside sites.
    sites = [q.site for q in people]
    groups = {Holdout.SITE: sites, Holdout.DEVICE: [q.device for q in people]}
    band_strata = groups.get(plan.holdout, ["all"] * len(people))
    band_seq, site_seq = null_seq.spawn(2)
    model_arm: Arm = (design.x, plan.model, plan.norms)
    band = permutation_null(
        band_strata, band_seq, [model_arm, *((baseline_designs[name], LOGISTIC_L2, None) for name in varying)]
    )
    if isinstance(band, Refused):
        return band
    null_aucs, *varying_nulls = band
    same = len(set(sites)) == 1 or list(band_strata) == sites
    site = [null_aucs] if same else permutation_null(sites, site_seq, [model_arm])
    if isinstance(site, Refused):
        return site
    site_aucs = site[0]
    null = null_check(null_aucs, site_aucs, cross.folds[0], y)
    if not null.low <= null.mean <= null.high:
        return Refused(
            RefusedCode.NULL_CHECK,
            f"The mean permutation AUC {null.mean:.4f} is outside {null.low:.4f} to {null.high:.4f}.",
        )
    t975 = float(t_distribution.ppf(0.975, len(null_aucs) - 1))
    auc_result = _auc_result(model_boot, null_aucs, _p_value(site_aucs, model_boot.point), t975)
    baseline_nulls = dict(zip(varying, varying_nulls, strict=True))
    baselines: dict[str, AucResult] = {}
    for name, b in baseline_boot.items():
        base_null = baseline_nulls.get(name, [b.point] * len(null_aucs))
        baselines[name] = _auc_result(b, base_null, _p_value(base_null, b.point), t975)
    cases_t = int(y_tested.sum())

    sample_prevalence = cases_t / len(y_tested)
    shifted = _mean_over_repeats(cross.shifted(plan.prevalence))[tested]
    for name in ("device", "site", "demographic"):
        caution = _baseline_caution(name, baselines[name], auc_result, t975)
        if caution is not None:
            cautions.append(caution)
    if null.resolution > 0.05:
        cautions.append(
            Caution(
                "p_value_resolution",
                f"With {len(site_aucs)} permutations the smallest possible p-value is {null.resolution:.3f},"
                " so the p-value cannot reach 0.05. Use at least 19 permutations.",
            )
        )
    out_of_range = sum(f.out_of_range for f in cross.fits)
    if out_of_range:
        cautions.append(
            Caution(
                "norms_out_of_range",
                f"Test rows with covariates outside the range of the norms of their training fold:"
                f" {out_of_range}, a participant counted once per repeat. Their deviations are unknown, so"
                " the training-fold median deviation replaces them. Each fold gives its count in"
                " `out_of_range`.",
            )
        )
    if abs(sample_prevalence - plan.prevalence) > 1e-12:
        cautions.append(
            Caution(
                "prevalence_shift",
                f"The sample prevalence {sample_prevalence:.3f} differs from the stated prevalence"
                f" {plan.prevalence}. Threshold metrics use risks moved from the training prevalence of each"
                " fold to the stated prevalence by the prior-shift adjustment, which assumes the same feature"
                " distribution within each class in the target population.",
            )
        )
    cases = int(y.sum())
    table_2x2 = [[attrition.excluded_cases, cases], [attrition.excluded_controls, len(y) - cases]]
    if (
        attrition.excluded_cases + attrition.excluded_controls
        and float(np.asarray(fisher_exact(table_2x2))[1]) < 0.05
    ):
        cautions.append(
            Caution(
                "selective_attrition",
                f"Exclusion differs between cases ({attrition.excluded_cases}) and controls"
                f" ({attrition.excluded_controls}), Fisher exact p < 0.05.",
            )
        )
    statements = [
        AUC_STATEMENT,
        PPV_STATEMENT,
        COUNTS_STATEMENT,
        DIGEST_STATEMENT,
        *([NORMS_STATEMENT] if plan.norms else []),
    ]
    cal = calibration(p, y_tested)
    digest = plan_digest(plan)
    return Screening(
        plan_digest=digest,
        feature_digest=freeze_features(table),
        seed=seed,
        columns=tuple(used),
        reliability=gates,
        tested=len(y_tested),
        cases=cases_t,
        sample_prevalence=sample_prevalence,
        stated_prevalence=plan.prevalence,
        auc=auc_result,
        calibration=cal,
        thresholds=tuple(threshold_metrics(shifted, y_tested, t, plan.prevalence) for t in plan.thresholds),
        decision_curve=tuple(
            net_benefit(shifted, y_tested, t, plan.prevalence)
            for t in sorted({*DECISION_GRID, *plan.thresholds})
        ),
        baselines=baselines,
        subgroups=_subgroups(cross, p_all, y, tested, people, plan.bootstrap, boot),
        null=null,
        attrition=attrition,
        folds=_fold_records(cross, splits, people),
        chosen=tuple(f.chosen for f in cross.fits),
        cautions=tuple(cautions),
        statements=tuple(statements),
        tripod=_tripod(plan, digest, used, attrition, len(y_tested), cases_t, auc_result, cal),
    )
