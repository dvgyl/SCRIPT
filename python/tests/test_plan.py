from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import Analysed, quick_plan

from scriptkin.evaluate.plan import (
    AnalysisPlan,
    GateCriteria,
    Holdout,
    NormSpec,
    freeze_features,
    plan_digest,
)
from scriptkin.features import FeatureTable, read_feature_table


def test_a_changed_plan_changes_the_plan_digest() -> None:
    # Detects a plan digest that ignores a field, so a changed analysis could claim the frozen plan.
    plan = quick_plan(gate=GateCriteria(), norms=NormSpec(("age_band",)))
    assert plan_digest(plan) == plan_digest(AnalysisPlan.from_json(json.loads(json.dumps(plan.to_json()))))
    changed = [
        replace(plan, thresholds=(0.05, 0.1, 0.25)),
        replace(plan, prevalence=0.31),
        replace(plan, holdout=Holdout.SITE),
        replace(plan, gate=GateCriteria(min_icc_low=0.7)),
        replace(plan, features=plan.features[:-1]),
        replace(plan, permutations=11),
    ]
    assert len({plan_digest(p) for p in [plan, *changed]}) == len(changed) + 1


def test_a_changed_feature_table_changes_the_feature_digest(null_cohort: Analysed, tmp_path: Path) -> None:
    # Detects a feature digest that misses a changed cell or context field, or that a CSV round trip changes.
    table = null_cohort.table
    digest = freeze_features(table)
    table.to_csv(tmp_path / "features.csv")
    assert freeze_features(read_feature_table(tmp_path / "features.csv")) == digest
    first = table.rows[0]
    speed = first.cells["kinematic.speed_mean"]
    assert isinstance(speed, float)
    cell = {**first.cells, "kinematic.speed_mean": speed + 1e-9}
    for row in (replace(first, cells=cell), replace(first, site="OTHER")):
        assert freeze_features(FeatureTable((row, *table.rows[1:]), table.provenance)) != digest


def test_plan_json_rejects_an_unknown_key() -> None:
    # Detects a misspelled plan key that would silently fall back to a default.
    data = quick_plan().to_json() | {"permutation": 5}
    with pytest.raises(ValueError, match="Unknown plan keys"):
        AnalysisPlan.from_json(data)


def test_a_plan_needs_enough_permutations_for_the_null_check_to_fail() -> None:
    # Detects a plan whose null band is so wide (t(0.995, 1) = 63.7 at two permutations) that it cannot fail.
    with pytest.raises(ValueError, match="10 permutations"):
        quick_plan(permutations=9)
    assert quick_plan(permutations=10).permutations == 10
