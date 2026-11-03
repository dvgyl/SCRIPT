from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from conftest import make_trial

from scriptkin import (
    CATALOG,
    Accepted,
    Channel,
    Device,
    PenState,
    QcPolicy,
    Recording,
    Refusal,
    Rejected,
    Stroke,
    TaskRef,
    assess,
    extract,
    feature_table,
)
from scriptkin.features import catalog_digest


def test_unknown_scale_refuses_distance_but_keeps_time() -> None:
    # Detects millimetre features computed from uncalibrated source coordinates.
    t = np.arange(101, dtype=float) / 100
    qc = assess(make_trial(t, device=Device("unknown", "pencil", spatial_unit="source")))
    assert isinstance(qc, Accepted)
    cells = extract(qc)
    refused = cells["kinematic.speed_mean"]
    assert isinstance(refused, Refusal) and refused.code == "unknown_scale"
    assert cells["timing.contact_s"] == 1
    assert cells["timing.duration_s"] == 1


def test_simulator_refuses_pen_channels() -> None:
    # Detects a Pencil-only feature computed from simulated direct-touch input.
    t = np.arange(101, dtype=float) / 100
    qc = assess(make_trial(t, device=Device("simulator", "directTouchAsPen")))
    assert isinstance(qc, Accepted)
    cell = extract(qc)["pressure.force_mean"]
    assert isinstance(cell, Refusal) and cell.code == "simulated_input"


@pytest.mark.parametrize(
    "defect", ["low_rate", "duplicate_times", "nonmonotonic_times", "unresolved_estimates"]
)
def test_qc_refuses_bad_acquisition(defect: str) -> None:
    # Detects acceptance of low rate, invalid time order, or unresolved sample estimates.
    t = np.arange(101, dtype=float) / (10 if defect == "low_rate" else 100)
    unresolved = np.zeros(len(t), dtype=np.int64)
    if defect == "duplicate_times":
        t[5] = t[4]
    elif defect == "nonmonotonic_times":
        t[5] = t[4] - 0.001
    elif defect == "unresolved_estimates":
        unresolved[5] = 1
    stroke = Stroke(t, np.column_stack((t, t)), unresolved=unresolved)
    result = assess(make_trial(t, strokes=(stroke,)))
    assert isinstance(result, Rejected)
    assert defect in {r.code for r in result.refusals}


def test_qc_reports_planned_channel_absent() -> None:
    # Detects a missing planned channel omitted from both QC and feature refusals.
    t = np.arange(101, dtype=float) / 100
    qc = assess(make_trial(t, device=Device("pencil", "pencil", frozenset({Channel.FORCE}))))
    assert isinstance(qc, Accepted)
    assert "missing_channel" in {r.code for r in qc.report.findings}
    cell = extract(qc)["pressure.force_mean"]
    assert isinstance(cell, Refusal) and cell.code == "missing_channel"
    assert extract(qc)["timing.duration_s"] == 1


def test_qc_splits_at_nonfinite_position() -> None:
    # Detects a segment that bridges an invalid position.
    t = np.arange(101, dtype=float) / 100
    xy = np.column_stack((t, t))
    xy[50] = np.nan
    qc = assess(make_trial(t, xy))
    assert isinstance(qc, Accepted)
    assert len(qc.segments) == 2
    assert all(np.isfinite(s.xy).all() for s in qc.segments)
    assert qc.segments[0].t[-1] == 0.49
    assert qc.segments[1].t[0] == 0.51


def test_qc_never_joins_strokes_or_boot_clocks() -> None:
    # Detects an accepted segment built from two source strokes or different boot clocks.
    t = np.arange(101, dtype=float) / 100
    a = Stroke(t + 100, np.column_stack((t, t)), boot="a")
    b = Stroke(t.copy(), np.column_stack((t + 100, t)), boot="b")
    qc = assess(make_trial(t, strokes=(a, b)))
    assert isinstance(qc, Accepted)
    assert [s.boot for s in qc.segments] == ["a", "b"]
    assert [s.stroke_index for s in qc.segments] == [0, 1]


def test_spatial_trace_uses_line_segments() -> None:
    # Detects distance to stimulus vertices instead of distance to the polyline.
    t = np.arange(101, dtype=float) / 100
    xy = np.column_stack((t * 10, np.full(len(t), 2.0)))
    task = TaskRef("line", "trace", (np.array([[0.0, 0.0], [10.0, 0.0]]),))
    qc = assess(make_trial(t, xy, task=task))
    assert isinstance(qc, Accepted)
    assert extract(qc)["spatial.trace_rmse"] == pytest.approx(2)


def test_copy_spatial_error_removes_similarity_transform() -> None:
    # Detects copy error computed before translation, rotation, and scale alignment.
    t = np.linspace(0, 1, 201)
    shape = np.column_stack((t * 10, 5 * np.sin(t * np.pi)))
    rotation = np.array([[0.0, -1.0], [1.0, 0.0]])
    drawn = shape @ rotation * 2 + [20, 35]
    task = TaskRef("curve", "copy", (shape,))
    qc = assess(make_trial(t, drawn, task=task))
    assert isinstance(qc, Accepted)
    assert extract(qc)["spatial.copy_rmse"] == pytest.approx(0, abs=1e-10)


@pytest.mark.parametrize("task", [TaskRef("clock", "free"), TaskRef("sentence", "copy")])
def test_spatial_features_refuse_nonpath_tasks(task: TaskRef) -> None:
    # Detects spatial error assigned to a clock or sentence without path geometry.
    t = np.arange(101, dtype=float) / 100
    qc = assess(make_trial(t, task=task))
    assert isinstance(qc, Accepted)
    for name in ("spatial.trace_rmse", "spatial.copy_rmse"):
        cell = extract(qc)[name]
        assert isinstance(cell, Refusal) and cell.code == "not_applicable"


def test_feature_table_retains_refusals_and_provenance(tmp_path: Path) -> None:
    # Detects a CSV that drops refused cells or loses the settings and source provenance.
    t = np.arange(101, dtype=float) / 100
    trial = make_trial(t)
    recording = Recording("session", "participant", trial.device, (trial,), ("a" * 64,))
    table = feature_table([recording])
    path = tmp_path / "features.csv"
    table.to_csv(path)
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(CATALOG)
    assert {r["participant"] for r in rows} == {"participant"}
    force = next(r for r in rows if r["feature"] == "pressure.force_mean")
    assert force["value"] == "" and force["refusal"] == "missing_channel"
    provenance = json.loads(path.with_suffix(".csv.provenance.json").read_text())
    assert provenance["source_hashes"] == ["a" * 64]
    assert provenance["catalog_digest"] == catalog_digest()
    assert len(catalog_digest()) == 64
    assert provenance["qc_policy"]["max_gap_s"] == 0.05
    assert provenance["preprocessing"]["grid_hz"] == 200
    assert provenance["library_version"] == "0.1.0"


def test_rejected_trial_remains_in_feature_table() -> None:
    # Detects a rejected trial silently removed from the feature table.
    t = np.arange(101, dtype=float) / 10
    trial = make_trial(t)
    rec = Recording("s", "p", trial.device, (trial,))
    table = feature_table([rec])
    assert len(table.rows) == 1
    assert all(isinstance(cell, Refusal) and cell.code == "low_rate" for cell in table.rows[0].cells.values())


def test_hover_segments_stay_separate_from_contact_features() -> None:
    # Detects hover motion included in contact speed or contact duration.
    t = np.arange(101, dtype=float) / 100
    contact = Stroke(t, np.column_stack((t * 10, np.zeros(len(t)))))
    hover = Stroke(t + 2, np.column_stack((t * 100, np.zeros(len(t)))), PenState.IN_AIR)
    qc = assess(make_trial(t, strokes=(contact, hover)))
    assert isinstance(qc, Accepted)
    cells = extract(qc)
    assert cells["kinematic.speed_mean"] == pytest.approx(10)
    assert cells["timing.contact_s"] == 1
    assert cells["timing.in_air_s"] == 1


def test_unresolved_channel_refuses_when_policy_allows_timing() -> None:
    # Detects use of unresolved force when the QC policy permits timing analysis.
    t = np.arange(101, dtype=float) / 100
    stroke = Stroke(
        t,
        np.column_stack((t, t)),
        channels={Channel.FORCE: np.ones(len(t))},
        unresolved=np.ones(len(t), dtype=np.int64),
    )
    qc = assess(make_trial(t, strokes=(stroke,)), QcPolicy(reject_unresolved=False))
    assert isinstance(qc, Accepted)
    cells = extract(qc)
    force = cells["pressure.force_mean"]
    assert isinstance(force, Refusal) and force.code == "unresolved_estimates"
    assert cells["timing.duration_s"] == 1


def test_feature_catalog_digest_changes_with_definition(monkeypatch: pytest.MonkeyPatch) -> None:
    # Detects a catalog digest that omits a feature requirement.
    from scriptkin import features

    original = catalog_digest()
    monkeypatch.setattr(features, "CATALOG", (replace(CATALOG[0], min_values=9), *CATALOG[1:]))
    assert catalog_digest() != original


def test_spectral_feature_counts_only_full_welch_segments() -> None:
    # Detects a spectral feature that counts a short third segment as a finite pool value.
    times = [np.arange(n, dtype=float) / 200 for n in (601, 601, 101)]
    strokes = tuple(
        Stroke(t, np.column_stack((10 * t - np.cos(16 * np.pi * t) / (16 * np.pi), np.zeros(len(t)))))
        for t in times
    )
    qc = assess(make_trial(times[0], strokes=strokes))
    assert isinstance(qc, Accepted)
    cell = extract(qc)["spectral.speed_band_power"]
    assert isinstance(cell, Refusal) and cell.code == "too_few_values"


def test_spectral_feature_computes_with_three_full_segments() -> None:
    # Detects refusal of a spectral feature with three full finite Welch segments.
    t = np.arange(601, dtype=float) / 200
    xy = np.column_stack((10 * t - np.cos(16 * np.pi * t) / (16 * np.pi), np.zeros(len(t))))
    strokes = tuple(Stroke(t.copy(), xy.copy()) for _ in range(3))
    qc = assess(make_trial(t, strokes=strokes))
    assert isinstance(qc, Accepted)
    cell = extract(qc)["spectral.speed_band_power"]
    assert isinstance(cell, float) and 0.4 < cell < 0.6
