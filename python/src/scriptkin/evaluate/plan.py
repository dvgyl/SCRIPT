"""Analysis plans and the digests that make an analysis outcome-blind (DESIGN section 7).

A plan names the target, the features, the reliability gate, the model, the thresholds, the prevalence,
and the validation. Its digest is the SHA-256 of canonical JSON. `freeze_features` gives the digest of a
feature table. A screening report records both, so a reader can prove which plan and table produced it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Literal

from scriptkin.features import CATALOG, FeatureTable
from scriptkin.quality import Refusal

Between = Literal["device", "session"]
COVARIATES = ("age_band", "hand", "administration")


class Holdout(StrEnum):
    PARTICIPANT = "participant"
    SITE = "site"
    DEVICE = "device"
    TEMPORAL = "temporal"


class Missing(StrEnum):
    IMPUTE_IN_FOLD = "impute_in_fold"
    COMPLETE_CASE = "complete_case"


class ModelKind(StrEnum):
    LOGISTIC_L2 = "logistic_l2"
    KNN = "knn"


@dataclass(frozen=True)
class Target:
    """A participant is a case when the `scale` score of `instrument` is at least `cut`."""

    instrument: str = "phq9"
    version: str = "1.0"
    scale: str = "total"
    cut: float = 10.0


@dataclass(frozen=True)
class ModelSpec:
    """`grid` holds c for a logistic model, which minimizes mean log loss + |w|^2 / (2c) on standardized
    features, or the neighbour count for k-NN.

    The inner search tries the grid in order and keeps the first best value, so order the grid from the
    simplest model to the most flexible.
    """

    kind: ModelKind = ModelKind.LOGISTIC_L2
    grid: tuple[float, ...] = (1.0, 10.0, 100.0, 1000.0)

    def __post_init__(self) -> None:
        if not self.grid or any(v <= 0 for v in self.grid):
            raise ValueError("The model grid needs positive values.")
        if self.kind == ModelKind.KNN and any(v != int(v) for v in self.grid):
            raise ValueError("k-NN grid values are whole neighbour counts.")


LOGISTIC_L2 = ModelSpec()
KNN = ModelSpec(ModelKind.KNN, (15.0, 5.0, 1.0))


@dataclass(frozen=True)
class GateCriteria:
    """A feature passes when the lower 95% bound of ICC(A,1) reaches `min_icc_low` and every Bland-Altman
    bias is at most `max_bias_sd` between-participant standard deviations, with `min_participants`."""

    between: Between = "device"
    min_icc_low: float = 0.75
    max_bias_sd: float = 0.5
    min_participants: int = 20


@dataclass(frozen=True)
class NormSpec:
    """Replace each feature by its deviation from a normative model fitted in each training fold."""

    covariates: tuple[str, ...] = ("age_band",)

    def __post_init__(self) -> None:
        if not self.covariates or set(self.covariates) - set(COVARIATES):
            raise ValueError(f"Norm covariates come from {COVARIATES}.")


def column(task: str, feature: str) -> str:
    """The name of one task-feature column, for example `spiral-trace/kinematic.speed_mean`."""
    return f"{task}/{feature}"


_FEATURES = {f.name for f in CATALOG}


@dataclass(frozen=True)
class AnalysisPlan:
    target: Target
    features: tuple[str, ...]
    model: ModelSpec = LOGISTIC_L2
    prevalence: float = 0.08
    thresholds: tuple[float, ...] = (0.05, 0.1, 0.2)
    holdout: Holdout = Holdout.PARTICIPANT
    missing: Missing = Missing.IMPUTE_IN_FOLD
    gate: GateCriteria | None = None
    norms: NormSpec | None = None
    folds: int = 5
    repeats: int = 5
    inner_folds: int = 3
    permutations: int = 200
    bootstrap: int = 2000

    def __post_init__(self) -> None:
        if not self.features or len(set(self.features)) != len(self.features):
            raise ValueError("A plan needs distinct features.")
        for name in self.features:
            task, _, feature = name.partition("/")
            if not task or feature not in _FEATURES:
                raise ValueError(f"{name!r} is not a task/feature column of the catalog.")
        if not 0 < self.prevalence < 1 or not all(0 < t < 1 for t in self.thresholds):
            raise ValueError("The prevalence and thresholds must be between 0 and 1.")
        if self.folds < 2 or self.inner_folds < 2 or self.repeats < 1:
            raise ValueError("A plan needs at least 2 folds, 2 inner folds, and 1 repeat.")
        if self.permutations < 10 or self.bootstrap < 1:
            raise ValueError("A plan needs at least 10 permutations, so that the null check can fail.")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> AnalysisPlan:
        """Parse a plan. Unknown keys raise ValueError, because a misspelled key would fall back silently."""
        known = {f for f in cls.__dataclass_fields__}
        if set(data) - known:
            raise ValueError(f"Unknown plan keys: {sorted(set(data) - known)}.")
        try:
            values: dict[str, Any] = dict(data)
            values["target"] = Target(**data["target"])
            values["features"] = tuple(data["features"])
            if "model" in data:
                values["model"] = ModelSpec(ModelKind(data["model"]["kind"]), tuple(data["model"]["grid"]))
            if "thresholds" in data:
                values["thresholds"] = tuple(data["thresholds"])
            for name, kind in (("holdout", Holdout), ("missing", Missing)):
                if name in data:
                    values[name] = kind(data[name])
            if data.get("gate") is not None:
                values["gate"] = GateCriteria(**data["gate"])
            if data.get("norms") is not None:
                values["norms"] = NormSpec(tuple(data["norms"]["covariates"]))
            return cls(**values)
        except (KeyError, TypeError) as e:
            raise ValueError(f"Invalid plan: {e}") from e


def _digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def plan_digest(plan: AnalysisPlan) -> str:
    """The SHA-256 of the canonical JSON of the plan."""
    return _digest(plan.to_json())


def freeze_features(table: FeatureTable) -> str:
    """The SHA-256 of the canonical JSON of every row, cell, and the provenance of a feature table."""
    rows = sorted(table.rows, key=lambda r: (r.session, r.trial, r.attempt, r.task))
    payload = {
        "provenance": asdict(table.provenance),
        "rows": [
            {
                **{k: v for k, v in asdict(row).items() if k != "cells"},
                "cells": {
                    name: [cell.code, cell.detail] if isinstance(cell, Refusal) else cell
                    for name, cell in sorted(row.cells.items())
                },
            }
            for row in rows
        ],
    }
    return _digest(payload)
