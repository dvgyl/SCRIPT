"""The fixed synthetic experiment, independent of the human feature catalog."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from itertools import product

import numpy as np

from scriptkin.bench.analysis import LIMITS as LIMITS
from scriptkin.bench.runs import FEATURE_SET as FEATURES


@dataclass(frozen=True)
class ProgramTarget:
    geometry: str = "loops"
    scale: float = 1.0
    speed_mm_s: float = 20.0
    oscillation_hz: float = 0.0


@dataclass(frozen=True)
class SeedKey:
    effect: float
    n: int
    prevalence: float
    replicate: int
    stream: int
    feature: int = 0
    model: int = 0


def seed_for(master: int, key: SeedKey) -> int:
    payload = json.dumps(asdict(key), sort_keys=True, separators=(",", ":")).encode()
    words = np.frombuffer(hashlib.sha256(payload).digest(), dtype="<u4").tolist()
    return int(np.random.SeedSequence([master, *words]).generate_state(1)[0])


@dataclass(frozen=True)
class CellSpec:
    features: tuple[str, ...] = (FEATURES[0],)
    scenario: str = "all_w"
    effect: float = 0.0
    bias_multiple: float = 0.0
    noise_id: str = "nominal"
    n: int = 600
    prevalence: float = 0.30
    participant_share: float = 0.80
    sensitivity: str = "primary"
    model: str = "logistic_l2"
    association: float = 0.85

    def __post_init__(self) -> None:
        if (
            not self.features
            or len(set(self.features)) != len(self.features)
            or set(self.features) - set(FEATURES)
        ):
            raise ValueError("The cell needs distinct registered features.")
        if self.scenario not in ("all_w", "transfer", "confounded"):
            raise ValueError("Unknown consequence scenario.")

    @property
    def id(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class ConsequencePlan:
    seed: int = 20261107
    replicates: int = 200
    sensitivity_replicates: int = 100
    folds: int = 5
    inner_folds: int = 3
    thresholds: tuple[float, ...] = tuple(t / 100 for t in range(5, 31))
    source: str = "grid"
    cell_specs: tuple[CellSpec, ...] | None = None


def cells(plan: ConsequencePlan) -> tuple[CellSpec, ...]:
    if plan.cell_specs is not None:
        return plan.cell_specs
    result: list[CellSpec] = []
    biases = (-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0) if plan.source == "grid" else (0.0,)
    noises = ("zero", "nominal", "stress") if plan.source == "grid" else ("measured",)
    for f, scenario, effect, k, noise in product(
        FEATURES, ("all_w", "transfer", "confounded"), (0.0, 0.8), biases, noises
    ):
        base = CellSpec((f,), scenario, effect, k, noise)
        result.append(base)
        if plan.source == "measured" or (k in (-1, 0, 1) and noise == "nominal"):
            for name, changes in (
                ("n200", {"n": 200}),
                ("n1200", {"n": 1200}),
                ("prevalence008", {"prevalence": 0.08}),
                ("share095", {"participant_share": 0.95}),
            ):
                result.append(replace(base, sensitivity=name, **changes))
            if effect == 0.8:
                result.append(replace(base, effect=0.4, sensitivity="effect04"))
            result.append(replace(base, model="knn", sensitivity="knn"))
            if plan.source == "grid":
                result.append(replace(base, sensitivity="misspecified"))
            if f == "bench.speed_cv":
                result.append(replace(base, sensitivity="floor_cv"))
            if scenario == "confounded":
                result.extend(replace(base, association=a, sensitivity=f"association{a}") for a in (0.5, 1.0))
            if f == FEATURES[0]:
                result.append(replace(base, features=FEATURES, sensitivity="joint"))
            if plan.source == "measured":
                result.extend(replace(base, sensitivity=s) for s in ("bias_lower", "bias_upper"))
    return tuple(result)


def planned_replicates(plan: ConsequencePlan, cell: CellSpec) -> int:
    return plan.replicates if cell.sensitivity == "primary" else plan.sensitivity_replicates


def plan_digest(plan: ConsequencePlan) -> str:
    payload = {"plan": asdict(plan), "cells": [asdict(c) for c in cells(plan)], "limits": LIMITS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
