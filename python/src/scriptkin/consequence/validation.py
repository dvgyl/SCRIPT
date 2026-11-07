"""Explicit matrix adapter for screening.crossfit. Record splits retain true participant IDs."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from sklearn.model_selection import StratifiedKFold

from scriptkin.consequence.generate import MatrixCohort
from scriptkin.consequence.plan import ConsequencePlan
from scriptkin.evaluate.plan import ModelKind, ModelSpec
from scriptkin.evaluate.screening import CrossFit, Design, Fold, crossfit, grouped_folds
from scriptkin.recording import FloatArray, IntArray


@dataclass(frozen=True)
class AnalysisMatrix:
    design: Design
    device: IntArray
    site: IntArray
    row_ids: tuple[str, ...]


@dataclass(frozen=True)
class SplitPlan:
    folds: tuple[Fold, ...]
    scheme: str
    direction: str = "internal"
    reason: str = ""


@dataclass(frozen=True)
class FitAudit:
    overlaps: tuple[int, ...]
    tested: tuple[int, ...]
    records: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class PredictionSet:
    cross: CrossFit
    y: IntArray
    weights: FloatArray
    audit: FitAudit


@dataclass(frozen=True)
class Failure:
    reason: str


def matrix(cohort: MatrixCohort, x: FloatArray, scheme: str) -> AnalysisMatrix:
    record = scheme == "record"
    ids = tuple(p for p in cohort.participant for _ in range(2)) if record else cohort.participant
    design = Design(
        x.reshape(-1, x.shape[-1]) if record else np.median(x, axis=1),
        np.repeat(cohort.y, 2) if record else cohort.y,
        ids,
        tuple(f"feature{i}" for i in range(x.shape[-1])),
        tuple({} for _ in ids),
    )
    return AnalysisMatrix(
        design,
        np.repeat(cohort.device, 2) if record else cohort.device,
        np.repeat(cohort.site, 2) if record else cohort.site,
        tuple(f"{p}-s{i % 2}" if record else p for i, p in enumerate(ids)),
    )


def make_splits(
    data: AnalysisMatrix, scheme: str, *, seed: int, folds: int = 5, direction: str = "W-to-P"
) -> SplitPlan:
    d = data.design
    if scheme == "device":
        test_device = 0 if direction == "W-to-P" else 1
        if len(np.unique(data.device)) < 2:
            return SplitPlan((), scheme, direction, "not_applicable_one_device")
        pairs = [(np.flatnonzero(data.device != test_device), np.flatnonzero(data.device == test_device))]
    elif scheme == "record":
        pairs = list(StratifiedKFold(folds, shuffle=True, random_state=seed).split(d.x, d.y))
    else:
        pairs = grouped_folds(d.y, d.ids, folds, seed)
    return SplitPlan(
        tuple(Fold(np.asarray(a), np.asarray(b)) for a, b in pairs),
        scheme,
        direction if scheme == "device" else "internal",
    )


def participant_weights(ids: tuple[str, ...], tested: IntArray) -> FloatArray:
    counts: dict[str, int] = {}
    for i in tested:
        counts[ids[i]] = counts.get(ids[i], 0) + 1
    tested_rows = set(tested.tolist())
    return np.array([1 / counts[ids[i]] if i in tested_rows else 0 for i in range(len(ids))])


def audit_fits(cross: CrossFit, data: AnalysisMatrix, splits: SplitPlan) -> FitAudit:
    overlaps, tested, records = [], [], []
    for number, fold in enumerate(splits.folds):
        train_ids = {data.design.ids[i] for i in fold.train}
        test_ids = {data.design.ids[i] for i in fold.test}
        overlap = len(train_ids & test_ids)
        if set(fold.train) & set(fold.test) or (overlap and splits.scheme != "record"):
            raise ValueError("An outer split contains forbidden overlap.")
        if splits.scheme == "device" and set(data.device[fold.train]) & set(data.device[fold.test]):
            raise ValueError("A device crosses the outer split.")
        for fit in cross.log:
            if fit.fold != number:
                continue
            if not fit.rows <= set(fold.train):
                raise ValueError("A fit used an outer test row.")
            if not fit.participants <= train_ids:
                raise ValueError("A fit used an outer test participant.")
            records.append(
                {
                    "fold": number,
                    "stage": fit.stage,
                    "training_participants": sorted(fit.participants),
                    "training_rows": [data.row_ids[i] for i in sorted(fit.rows)],
                    "test_overlap": len(fit.participants & test_ids),
                }
            )
        if not any(r.fold == number and r.stage == "final" for r in cross.log):
            raise ValueError("A fold has no final fit record.")
        overlaps.append(overlap)
        tested.append(len(test_ids))
    return FitAudit(tuple(overlaps), tuple(tested), tuple(records))


def evaluate_matrix(
    data: AnalysisMatrix, plan: ConsequencePlan, splits: SplitPlan, *, seed: int, model: str = "logistic_l2"
) -> PredictionSet | Failure:
    if splits.reason:
        return Failure(splits.reason)
    for fold in splits.folds:
        if len(np.unique(data.design.y[fold.test])) < 2:
            return Failure("one_class_holdout")
        groups = data.design.ids
        for label in (0, 1):
            if len({groups[i] for i in fold.train if data.design.y[i] == label}) < plan.inner_folds:
                return Failure("too_few_training_participants")
    spec = ModelSpec(ModelKind(model), (10.0,) if model == "logistic_l2" else (15.0, 5.0, 1.0))
    try:
        cross = crossfit(data.design, spec, [splits.folds], inner_folds=plan.inner_folds, seed=seed)
    except (ValueError, np.linalg.LinAlgError) as error:
        return Failure(f"fit_not_estimable: {error}")
    audit = audit_fits(cross, data, splits)
    tested = np.flatnonzero(cross.folds[0] >= 0)
    return PredictionSet(cross, data.design.y, participant_weights(data.design.ids, tested), audit)


def permute_participants(data: AnalysisMatrix, *, seed: int, within_device: bool = False) -> AnalysisMatrix:
    rng = np.random.default_rng(seed)
    ids = data.design.ids
    unique = sorted(set(ids))
    first = np.array([ids.index(p) for p in unique])
    labels = data.design.y[first].copy()
    strata = data.device[first] if within_device else np.zeros(len(first), dtype=np.int64)
    for s in np.unique(strata):
        rows = np.flatnonzero(strata == s)
        labels[rows] = labels[rng.permutation(rows)]
    mapping = dict(zip(unique, labels, strict=True))
    return replace(data, design=replace(data.design, y=np.array([mapping[p] for p in ids])))
