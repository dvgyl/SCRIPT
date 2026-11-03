from __future__ import annotations

import csv
import itertools
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from conftest import fixture_lines, make_trial

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
    battery,
    extract,
    feature_table,
    fold,
)
from scriptkin.features import catalog_digest
from scriptkin.recording import FloatArray
from scriptkin.signal import GammaFit, Kinematics, gamma_fit, kinematics


def accepted(*strokes: Stroke, task: TaskRef | None = None) -> Accepted:
    qc = assess(make_trial(strokes[0].t, strokes=strokes, task=task))
    assert isinstance(qc, Accepted)
    return qc


def speeds(qc: Accepted) -> list[FloatArray]:
    result: list[FloatArray] = []
    for segment in qc.segments:
        k = kinematics(segment)
        assert isinstance(k, Kinematics)
        result.append(k.speed)
    return result


def stimulus(task: str) -> tuple[FloatArray, ...]:
    return tuple(np.asarray(p, dtype=float) * [1, -1] for p in battery.task_spec(task)["stimulus"]["paths"])


def copy_cell(drawn: tuple[FloatArray, ...], task: str) -> float:
    """Draw each polyline as one 200 Hz contact stroke and return the copy RMSE."""
    strokes: list[Stroke] = []
    for i, xy in enumerate(drawn):
        strokes.append(Stroke(10 * i + np.arange(len(xy), dtype=float) / 200, xy))
    cell = extract(accepted(*strokes, task=TaskRef(task, "copy", stimulus(task))))["spatial.copy_rmse"]
    assert isinstance(cell, float)
    return cell


def turned(paths: tuple[FloatArray, ...], degrees: float, scale: float) -> tuple[FloatArray, ...]:
    a = np.radians(degrees)
    rotation = np.array([[np.cos(a), np.sin(a)], [-np.sin(a), np.cos(a)]])
    return tuple(p @ rotation * scale + [20.0, -35.0] for p in paths)


def wave_strokes(*seconds: float) -> tuple[Stroke, ...]:
    """Contact strokes at 200 Hz with speed 10 + sin(16 pi t) mm/s: an 8 Hz wave with power 0.5."""
    strokes: list[Stroke] = []
    for s in seconds:
        t = np.arange(round(s * 200) + 1, dtype=float) / 200
        x = 10 * t - np.cos(16 * np.pi * t) / (16 * np.pi)
        strokes.append(Stroke(t, np.column_stack((x, np.zeros(len(t))))))
    return tuple(strokes)


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


def test_tablet_input_computes_kinematics_and_refuses_pen_tilt_features() -> None:
    # Detects a tablet treated as simulated input, or a Pencil tilt feature computed without its channel.
    t = np.arange(101, dtype=float) / 100
    planned = frozenset({Channel.FORCE, Channel.TILT_X, Channel.TILT_Y, Channel.HOVER})
    channels = {
        Channel.FORCE: np.full(len(t), 0.5),
        Channel.TILT_X: np.full(len(t), 0.1),
        Channel.TILT_Y: np.full(len(t), -0.2),
    }
    stroke = Stroke(t, np.column_stack((t * 10, np.zeros(len(t)))), channels=channels)
    device = Device("Wacom Intuos Pro Medium (2025)", "tablet", planned, planned)
    qc = assess(make_trial(t, strokes=(stroke,), device=device))
    assert isinstance(qc, Accepted)
    cells = extract(qc)
    assert cells["kinematic.speed_mean"] == pytest.approx(10)
    assert cells["pressure.force_mean"] == pytest.approx(0.5)
    for name in ("tilt.altitude_mean", "tilt.azimuth_variance", "tilt.roll_variance"):
        cell = cells[name]
        assert isinstance(cell, Refusal) and cell.code == "missing_channel"


def test_planned_hover_is_observed_from_in_air_strokes_without_height() -> None:
    # Detects QC that reports the planned hover channel absent for a tablet whose hover lines have no height.
    rec = fold(fixture_lines("bench_wacom"))
    assert Channel.HOVER in rec.device.planned_channels
    assert Channel.HOVER in rec.device.observed_channels
    findings = {r.code for r in assess(rec.trials[0]).report.findings}
    assert "missing_channel" not in findings


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
    (drawn,) = turned((shape,), 30, 2)
    task = TaskRef("curve", "copy", (shape,))
    qc = assess(make_trial(t, drawn, task=task))
    assert isinstance(qc, Accepted)
    assert extract(qc)["spatial.copy_rmse"] == pytest.approx(0, abs=1e-4)


@pytest.mark.parametrize("task", ["house", "pentagons", "loops"])
@pytest.mark.parametrize("change", ["stroke order", "direction"])
def test_copy_rmse_ignores_stroke_order_and_direction(task: str, change: str) -> None:
    # Detects a copy error that pairs drawn and stimulus points in drawing order or direction.
    paths = stimulus(task)
    if change == "stroke order":
        halves = [(p[: len(p) // 2 + 1], p[len(p) // 2 :]) for p in paths]
        drawn = tuple(half for pair in halves for half in pair)[::-1]
    else:
        drawn = tuple(p[::-1] for p in paths)
    assert copy_cell(drawn, task) < 0.05


@pytest.mark.parametrize("task", ["house", "pentagons", "loops"])
def test_copy_rmse_removes_scale_and_rotation(task: str) -> None:
    # Detects a copy error that keeps a uniform scale or a small rotation of a complete copy.
    assert copy_cell(turned(stimulus(task)[::-1], 10, 1.2), task) == pytest.approx(0, abs=1e-4)


def test_copy_rmse_counts_an_omitted_wall() -> None:
    # Detects a copy error that measures only the drawn points and so ignores an omitted part.
    body, *others = stimulus("house")
    wall = np.flatnonzero(body[:, 0] == 50)
    drawn = (body[: wall[0] + 1], body[wall[-1] :], *others)
    assert copy_cell(drawn, "house") > 1


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
    recording = Recording("session", "participant", trial.device, (trial,), "profile", ("a" * 64,))
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
    assert provenance["reader_profiles"] == ["profile"]
    assert provenance["catalog_digest"] == catalog_digest()
    assert len(catalog_digest()) == 64
    assert provenance["qc_policy"]["max_gap_s"] == 0.05
    assert provenance["preprocessing"]["grid_hz"] == 200
    assert provenance["library_version"] == "0.1.0"


def test_rejected_trial_remains_in_feature_table() -> None:
    # Detects a rejected trial silently removed from the feature table.
    t = np.arange(101, dtype=float) / 10
    trial = make_trial(t)
    rec = Recording("s", "p", trial.device, (trial,), "profile")
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


@pytest.mark.parametrize("defect", ["low_rate", "duplicate_times"])
def test_hover_defect_refuses_only_in_air_features(defect: str) -> None:
    # Detects a hover timing defect that rejects the contact features of the trial.
    t = np.arange(101, dtype=float) / 100
    contact = Stroke(t, np.column_stack((t * 10, np.zeros(len(t)))))
    air_t = 2 + np.arange(31, dtype=float) / (30 if defect == "low_rate" else 100)
    if defect == "duplicate_times":
        air_t[5] = air_t[4]
    hover = Stroke(air_t, np.column_stack((air_t, np.zeros(len(air_t)))), PenState.IN_AIR)
    cells = extract(accepted(contact, hover))
    assert cells["kinematic.speed_mean"] == pytest.approx(10)
    assert cells["timing.contact_s"] == 1
    in_air = cells["timing.in_air_s"]
    assert isinstance(in_air, Refusal) and in_air.code == defect


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


def test_spectral_feature_computes_from_one_long_contact() -> None:
    # Detects a spectral minimum applied to segments: one 10 s contact holds 9 Welch windows.
    cell = extract(accepted(*wave_strokes(10)))["spectral.speed_band_power"]
    assert cell == pytest.approx(0.5, rel=0.02)


def test_spectral_feature_counts_windows_not_segments() -> None:
    # Detects a spectral feature computed from fewer than three Welch windows in total.
    cell = extract(accepted(*wave_strokes(3, 0.5, 0.5, 0.5)))["spectral.speed_band_power"]
    assert isinstance(cell, Refusal) and cell.code == "too_few_values"
    assert cell.detail == "Need 3 Welch windows; found 2."
    assert extract(accepted(*wave_strokes(3, 2, 0.5)))["spectral.speed_band_power"] == pytest.approx(
        0.5, rel=0.02
    )


def test_force_mean_and_cv_pool_segment_values() -> None:
    # Detects force mean and coefficient of variation exchanged, pooled over samples, or with ddof 1.
    t = np.arange(200, dtype=float) / 200
    xy = np.column_stack((t * 10, np.zeros(len(t))))
    varied = Stroke(t, xy, channels={Channel.FORCE: np.tile([0.2, 0.6], 100)})
    steady = Stroke(t + 2, xy, channels={Channel.FORCE: np.full(len(t), 0.8)})
    cells = extract(accepted(varied, steady))
    assert cells["pressure.force_mean"] == pytest.approx(0.6)
    assert cells["pressure.force_cv"] == pytest.approx((0.2 / 0.4 + 0) / 2)


def test_tilt_uses_circular_spread_and_mean_altitude() -> None:
    # Detects a linear angle spread, exchanged channels, or a mean resultant length given as variance.
    t = np.arange(200, dtype=float) / 200
    channels = {
        Channel.AZIMUTH: np.tile([np.pi / 4, 2 * np.pi - np.pi / 4], 100),
        Channel.ROLL: np.tile([1 - np.pi / 6, 1 + np.pi / 6], 100),
        Channel.ALTITUDE: np.tile([0.8, 1.0], 100),
    }
    cells = extract(accepted(Stroke(t, np.column_stack((t * 10, np.zeros(len(t)))), channels=channels)))
    assert cells["tilt.azimuth_variance"] == pytest.approx(1 - np.cos(np.pi / 4))
    assert cells["tilt.roll_variance"] == pytest.approx(1 - np.cos(np.pi / 6))
    assert cells["tilt.altitude_mean"] == pytest.approx(0.9)


def test_path_length_sums_chords_and_stroke_count_counts_contacts() -> None:
    # Detects a path length from displacement, a mean, or a gap chord, and strokes counted by segment.
    corners = np.array([[0, 0], [3, 4], [6, 0], [9, 4], [12, 0]], dtype=float)
    zigzag = np.concatenate(
        [np.linspace(a, b, 21)[:-1] for a, b in itertools.pairwise(corners)] + [corners[-1:]]
    )
    line = np.arange(101, dtype=float) / 100
    gapped = np.concatenate((line, line + 1.2))
    strokes = (
        Stroke(np.arange(len(zigzag), dtype=float) / 100, zigzag),
        Stroke(5 + gapped, np.column_stack((10 * gapped, np.zeros(len(gapped))))),
        Stroke(10 + line, np.column_stack((50 * line, np.zeros(len(line)))), PenState.IN_AIR),
    )
    qc = accepted(*strokes)
    assert len(qc.segments) == 4
    cells = extract(qc)
    assert cells["kinematic.path_length"] == pytest.approx(20 + 10 + 10)
    assert cells["timing.stroke_count"] == 2


def test_acceleration_and_jerk_are_derivative_norms() -> None:
    # Detects exchanged derivative orders, one axis only, or a missing norm in acceleration or jerk.
    t = np.arange(201, dtype=float) / 200
    cells = extract(accepted(Stroke(t, np.column_stack((3 * t**3, 4 * t**3)))))
    assert cells["kinematic.acceleration_mean"] == pytest.approx(30 * 0.5)
    assert cells["kinematic.jerk_mean"] == pytest.approx(30)


def test_sample_entropy_matches_brute_force_counts() -> None:
    # Detects exchanged template counts, a wrong tolerance, or self-matches in the entropy feature.
    rng = np.random.default_rng(4)
    strokes: list[Stroke] = []
    for start in (0.0, 5.0):
        t = start + np.arange(121, dtype=float) / 200
        x = np.cumsum(rng.uniform(0.02, 0.08, len(t)))
        strokes.append(Stroke(t, np.column_stack((x, np.zeros(len(t))))))
    qc = accepted(*strokes)
    series = speeds(qc)
    m, r = 2, 0.2 * float(np.std(np.concatenate(series)))
    windows = np.array([x[i : i + m + 1] for x in series for i in range(len(x) - m)])
    gaps = np.abs(windows[:, None, :] - windows[None, :, :])
    pairs = np.triu(np.ones((len(windows), len(windows)), dtype=bool), 1)
    b = int(np.sum((gaps[:, :, :m].max(axis=2) <= r) & pairs))
    a = int(np.sum((gaps.max(axis=2) <= r) & pairs))
    assert 0 < a < b
    assert extract(qc)["entropy.speed_sample_entropy"] == pytest.approx(-np.log(a / b))


def torres_spikes(speed: FloatArray) -> list[float]:
    n = len(speed)

    def is_minimum(i: int) -> bool:
        if i == 0:
            return speed[0] <= speed[1]
        if i == n - 1:
            return speed[-1] <= speed[-2]
        return speed[i - 1] > speed[i] < speed[i + 1]

    peaks = [i for i in range(1, n - 1) if speed[i - 1] < speed[i] > speed[i + 1]]
    minima = [i for i in range(n) if is_minimum(i)]
    spikes: list[float] = []
    for p in peaks:
        left = [i for i in minima if i < p]
        right = [i for i in minima if i > p]
        if left and right:
            spikes.append(float(speed[p] / (speed[p] + np.mean(speed[left[-1] : right[0] + 1]))))
    return spikes


def test_micro_spikes_and_gamma_fit_use_torres_peaks() -> None:
    # Detects a wrong spike normalization, or Gamma shape, scale, or interval bounds exchanged.
    t = np.arange(1201, dtype=float) / 200
    amplitude = 3 + 2 * np.sin(2 * np.pi * 0.37 * t)
    x = 10 * t + amplitude * np.sin(2 * np.pi * 5 * t) / (2 * np.pi * 5)
    qc = accepted(Stroke(t, np.column_stack((x, np.zeros(len(t))))))
    spikes = [s for speed in speeds(qc) for s in torres_spikes(speed)]
    assert len(spikes) >= 25
    fit = gamma_fit(np.asarray(spikes))
    assert isinstance(fit, GammaFit)
    cells = extract(qc)
    assert cells["micro.spike_mean"] == pytest.approx(np.mean(spikes))
    for name, estimate in (("shape", fit.shape), ("scale", fit.scale)):
        assert cells[f"micro.gamma_{name}"] == estimate.point
        assert cells[f"micro.gamma_{name}_low"] == estimate.low
        assert cells[f"micro.gamma_{name}_high"] == estimate.high
    assert fit.shape.point > 10 * fit.scale.point
