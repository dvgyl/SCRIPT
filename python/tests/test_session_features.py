from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from conftest import fixture_lines, make_trial

from scriptkin import Accepted, Recording, Refusal, Stroke, assess, extract, feature_table, fold


def test_practice_trials_never_enter_the_feature_table() -> None:
    # Detects a practice trial measured as if it were a study task.
    table = feature_table([fold(fixture_lines("multi_trial")), fold(fixture_lines("self_administered"))])
    assert "practice" not in {row.task for row in table.rows}
    assert len(table.rows) == 5 + 1


def test_qualification_sessions_never_enter_the_feature_table() -> None:
    # Detects device-qualification strokes mixed into participant features or their provenance.
    qualification = fold(fixture_lines("qualification"))
    study = fold(fixture_lines("basic"))
    table = feature_table([qualification, study])
    assert {row.session for row in table.rows} == {study.session_id}
    assert table.provenance.source_hashes == study.source_hashes


def test_bench_sessions_never_enter_the_feature_table() -> None:
    # Detects bench runs measured as participant trials, or their digests in the provenance.
    bench = [fold(fixture_lines(name)) for name in ("bench_ipad", "bench_wacom")]
    study = fold(fixture_lines("basic"))
    table = feature_table([*bench, study])
    assert {row.session for row in table.rows} == {study.session_id}
    assert table.provenance.source_hashes == study.source_hashes


def latency(began: float | None, started: float = 0.5) -> float | Refusal:
    t = np.arange(1.0, 2.001, 0.01)
    trial = replace(make_trial(t), started=started, began=began)
    qc = assess(trial)
    assert isinstance(qc, Accepted)
    return extract(qc)["timing.initiation_latency_s"]


def test_initiation_latency_runs_from_the_begin_tap_to_first_contact() -> None:
    # Detects a latency measured from trial start, or a number when the trial has no Begin tap.
    assert latency(0.75) == pytest.approx(0.25)
    refusal = latency(None)
    assert isinstance(refusal, Refusal) and refusal.code == "undefined" and "Begin" in refusal.detail
    assert isinstance(latency(1.5), Refusal)


def test_initiation_latency_uses_the_earliest_contact() -> None:
    # Detects a latency taken from a later stroke when strokes are not in time order.
    t = np.arange(1.0, 2.001, 0.01)
    late = Stroke(t + 5, np.column_stack((t, np.zeros(len(t)))))
    early = Stroke(t, np.column_stack((t, np.ones(len(t)))))
    qc = assess(replace(make_trial(t, strokes=(late, early)), began=0.5))
    assert isinstance(qc, Accepted)
    assert extract(qc)["timing.initiation_latency_s"] == pytest.approx(0.5)


def test_rows_carry_administration_hand_and_accommodations(tmp_path: Path) -> None:
    # Detects a feature table that cannot be stratified by administration, hand, or accommodations.
    recordings: list[Recording] = [
        fold(fixture_lines(n)) for n in ("usbc_no_force", "multi_trial", "self_administered")
    ]
    table = feature_table(recordings)
    keys = {
        (row.session[-2:], row.task, row.administration, row.hand, row.accommodations) for row in table.rows
    }
    assert ("04", "pentagons", "supervised", "left", ()) in keys
    assert ("02", "house", "supervised", "right", ("readAloud",)) in keys
    assert ("0B", "loops", "self", "right", ()) in keys
    table.to_csv(tmp_path / "features.csv")
    with (tmp_path / "features.csv").open() as stream:
        house = next(r for r in csv.DictReader(stream) if r["task"] == "house")
    assert (house["administration"], house["hand"], house["accommodations"]) == (
        "supervised",
        "right",
        "readAloud",
    )
