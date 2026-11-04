from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import test_bench_moves as moves_tests
from conftest import make_trial

from scriptkin.bench import MAIN
from scriptkin.bench.features import (
    CATALOG,
    Motion,
    MotionInterval,
    Points,
    commanded_motion,
    feature_cells,
    motion_interval,
    run_features,
)
from scriptkin.bench.moves import MoveList, commanded_trial
from scriptkin.features import extract
from scriptkin.quality import Accepted, Refusal, RefusalCode, assess
from scriptkin.recording import Device, FloatArray, Stroke, Trial
from scriptkin.signal import Kinematics

programs_folder = moves_tests.programs_folder
move_lists = moves_tests.move_lists

# The speed CV of table 5.1 is the analytic speed over the whole run. The bench feature is the
# Savitzky-Golay grid speed over the motion interval, which attenuates the line oscillation.
CV_TOLERANCE = {"loops": 0.01, "spiral": 0.02, "line": 0.04}


def rotated(xy: FloatArray, degrees: float) -> FloatArray:
    """Rotate row vectors counter-clockwise about the origin."""
    c, s = np.cos(np.radians(degrees)), np.sin(np.radians(degrees))
    return xy @ np.array([[c, s], [-s, c]])


def device_run(
    move_list: MoveList,
    *,
    rate_hz: float = 240.0,
    jitter_s: float = 0.0,
    noise_mm: float = 0.0,
    rotation_deg: float = 0.0,
    shift_mm: tuple[float, float] = (0.0, 0.0),
    drop: tuple[float, float] | None = None,
    seed: int = 0,
) -> Trial:
    """A synthetic device run of a program: the commanded path at a device rate with timing jitter,
    position noise, a known rotation and translation, and an optional gap in the samples."""
    rng = np.random.default_rng(seed)
    duration = move_list.duration_s
    t = np.arange(int(duration * rate_hz) + 1) / rate_hz
    t = t + rng.uniform(-jitter_s, jitter_s, len(t))
    t[0] = 0.0
    t = np.clip(np.sort(t), 0.0, duration)
    if drop is not None:
        t = t[(t <= drop[0]) | (t >= drop[1])]
    trial = commanded_trial(move_list, t)
    strokes = tuple(
        Stroke(s.t, rotated(s.xy, rotation_deg) + shift_mm + rng.normal(0.0, noise_mm, s.xy.shape))
        for s in trial.strokes
    )
    return replace(trial, strokes=strokes, device=Device("synthetic", "pencil"))


def features_of(trial: Trial) -> dict[str, float]:
    qc = assess(trial)
    assert isinstance(qc, Accepted)
    cells = run_features(qc).cells
    assert set(cells) == {f.name for f in CATALOG}
    return {k: v for k, v in cells.items() if not isinstance(v, Refusal)}


def grid(t: FloatArray, speed: FloatArray) -> Kinematics:
    """A grid with the given speeds and no other content, for the motion interval rule."""
    zeros = np.zeros((len(t), 2))
    return Kinematics(t, zeros, zeros, zeros, zeros, speed)


@pytest.mark.parametrize("pid", [d.id for d in MAIN])
def test_the_commanded_motion_starts_within_the_ramp_tolerance(
    move_lists: dict[str, MoveList], pid: str
) -> None:
    # Detects a motion interval rule that misses the ramp, or a commanded path with a wrong clock.
    # Section 7.4 claims 0.02 s. The loops programs at 20 mm/s ramp to the local speed of the first
    # loop bottom, about 15 mm/s, and cross 2 mm/s at 0.027 s, so their grid start is 0.030 s.
    # The grid of 5 ms adds up to one step at each end.
    ml = move_lists[pid]
    found = commanded_motion(ml)
    assert isinstance(found, Motion)
    limit = (0.03 if pid.startswith("loops") and pid.endswith("v20") else 0.02) + 0.005
    assert 0.0 < found.interval.start_s <= limit + 1e-9
    assert ml.duration_s - limit - 1e-9 <= found.interval.end_s < ml.duration_s


@pytest.mark.parametrize("pid", [d.id for d in MAIN])
def test_a_noise_free_run_gives_the_commanded_values(
    move_lists: dict[str, MoveList], programs_folder: Path, pid: str
) -> None:
    # Detects a feature that is off its definition: a wrong integral, pool, divisor, frame, or interval.
    # The commanded values are the summary that the study script writes beside the moves. The motion
    # interval drops the ramp ends below 2 mm/s: up to 0.032 s at each end, which raises the mean
    # speed by up to 0.6 percent on the shortest line program.
    ml = move_lists[pid]
    summary = json.loads((programs_folder / f"{pid}.json").read_text(encoding="utf-8"))["program"]
    cells = features_of(device_run(ml))
    assert cells["bench.motion_s"] == pytest.approx(summary["duration_s"], abs=0.07)
    assert cells["bench.path_length_mm"] == pytest.approx(summary["path_length_mm"], rel=0.002)
    assert cells["bench.chord_length_mm"] == pytest.approx(summary["path_length_mm"], rel=0.002)
    assert cells["bench.speed_mean_mm_s"] == pytest.approx(summary["mean_speed_mm_s"], rel=0.01)
    assert cells["bench.speed_cv"] == pytest.approx(
        summary["speed_cv"], abs=CV_TOLERANCE[ml.program.geometry]
    )
    assert cells["bench.ldlj"] == pytest.approx(summary["ldlj_command"], abs=0.15)
    if ml.program.geometry == "loops":
        assert cells["bench.loop_height_mm"] == pytest.approx(20.0 * ml.program.scale, abs=0.05)
    else:
        assert "bench.loop_height_mm" not in cells


def test_the_band_power_of_the_line_is_commanded_and_of_the_spiral_is_near_zero(
    move_lists: dict[str, MoveList], programs_folder: Path
) -> None:
    # Detects a band, a window, or a unit error in the band power. The move list samples the
    # oscillation every 10 ms and a 240 Hz run samples it again, so the run holds 95 percent of
    # the analytic power of table 5.1 at 8 Hz.
    summary = json.loads((programs_folder / "line-osc8.json").read_text(encoding="utf-8"))["program"]
    line = features_of(device_run(move_lists["line-osc8"]))["bench.band_power_mm2_s2"]
    assert line == pytest.approx(summary["band_power_command_mm2_s2"], rel=0.10)
    assert line == pytest.approx(74.3, abs=0.5)
    assert features_of(device_run(move_lists["spiral-l-v20"]))["bench.band_power_mm2_s2"] < 0.01


def test_a_gap_a_lift_a_dwell_and_a_truncation_give_what_section_5_3_expects(
    move_lists: dict[str, MoveList],
) -> None:
    # Detects a motion interval that excludes a gap or a dwell, a lift that keeps one stroke, or a
    # truncation that keeps the full path.
    clean = features_of(device_run(move_lists["loops-l-v20"]))
    split = device_run(move_lists["loops-l-v20"], drop=(5.0, 5.12))
    qc = assess(split)
    assert isinstance(qc, Accepted)
    assert len(qc.segments) == 2
    assert extract(qc)["timing.stroke_count"] == 1.0
    assert features_of(split)["bench.motion_s"] == pytest.approx(clean["bench.motion_s"], abs=0.01)
    lift = device_run(move_lists["fault-lift"])
    lift_qc = assess(lift)
    assert isinstance(lift_qc, Accepted)
    assert extract(lift_qc)["timing.stroke_count"] == 2.0
    assert features_of(lift)["bench.motion_s"] == pytest.approx(clean["bench.motion_s"] + 0.7, abs=0.01)
    dwell = features_of(device_run(move_lists["fault-dwell"]))
    assert dwell["bench.motion_s"] == pytest.approx(clean["bench.motion_s"] + 0.5, rel=0.02)
    assert dwell["bench.motion_s"] == pytest.approx(clean["bench.motion_s"] + 0.5, abs=0.01)
    truncated = features_of(device_run(move_lists["fault-truncated"]))
    assert truncated["bench.motion_s"] <= 0.6 * clean["bench.motion_s"]
    assert truncated["bench.path_length_mm"] <= 0.6 * clean["bench.path_length_mm"]


def test_the_motion_interval_needs_six_consecutive_samples_in_one_segment() -> None:
    # Detects a rule that accepts five fast samples, or a run of six that spans two segments.
    t = np.arange(20) / 200
    five = np.where((t >= 0.02) & (t < 0.045), 5.0, 0.0)
    assert isinstance(motion_interval([grid(t, five)]), Refusal)
    six = np.where((t >= 0.02) & (t < 0.05), 5.0, 0.0)
    found = motion_interval([grid(t, six)])
    assert found == MotionInterval(0.02, 0.045)
    first, second = grid(t[:10], np.full(10, 5.0)), grid(t[10:], np.full(10, 5.0))
    assert motion_interval([grid(t[7:10], np.full(3, 5.0)), grid(t[10:13], np.full(3, 5.0))]) == Refusal(
        RefusalCode.UNDEFINED, "No run of 6 grid samples reaches 2 mm/s."
    )
    both = motion_interval([first, second])
    assert both == MotionInterval(0.0, 0.095)
    assert motion_interval([grid(t, np.full(20, 1.99))]) == Refusal(
        RefusalCode.UNDEFINED, "No run of 6 grid samples reaches 2 mm/s."
    )


def test_the_loop_height_is_measured_in_the_frame_where_y_goes_down() -> None:
    # Detects a loop height whose baseline points are the tops of the loops, as the y-up frame gives.
    # With bottoms at 24 mm for five loops and at 22 mm after, the baseline frame gives 19.5 mm and
    # the top frame gives 20 mm.
    bottoms = np.array([24.0, 24.0, 24.0, 24.0, 24.0, 22.0, 22.0, 22.0, 22.0])
    top = 4.0
    t = np.linspace(0.0, 2 * np.pi * 8, 8 * 400 + 1)
    loop = np.minimum((t / (2 * np.pi)).astype(int), 7)
    phase = t - 2 * np.pi * loop
    down = top + (bottoms[loop] - top) * (1 + np.cos(phase)) / 2
    up = top + (bottoms[loop + 1] - top) * (1 + np.cos(phase)) / 2
    y_down = np.where(phase <= np.pi, down, up)
    seconds = t / (2 * np.pi) * 1.2
    xy = np.column_stack((12 * seconds, -y_down))
    cells = features_of(make_trial(seconds, xy))
    assert cells["bench.loop_height_mm"] == pytest.approx(19.5, abs=0.05)


def test_the_speed_cv_uses_the_sample_standard_deviation() -> None:
    # Detects a speed CV with the divisor n instead of n - 1, or one pooled per segment.
    t = np.arange(6) / 200
    speeds = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    one = Motion(MotionInterval(0.0, t[-1]), (grid(t, speeds),), (Points(t, np.zeros((6, 2))),))
    assert feature_cells(one)["bench.speed_cv"] == pytest.approx(np.std(speeds, ddof=1) / 3.5)
    two = Motion(MotionInterval(0.0, 1.0), (grid(t[:3], speeds[:3]), grid(t[3:] + 1, speeds[3:])), ())
    assert feature_cells(two)["bench.speed_cv"] == pytest.approx(np.std(speeds, ddof=1) / 3.5)
    single = Motion(MotionInterval(0.0, 0.0), (grid(t[:1], speeds[:1]),), ())
    cell = feature_cells(single)["bench.speed_cv"]
    assert isinstance(cell, Refusal)
    assert cell.code == RefusalCode.TOO_FEW_VALUES


def test_the_sampling_cells_count_the_intervals_and_both_gap_shares() -> None:
    # Detects a gap share with the wrong threshold, or intervals that cross strokes. Each stroke
    # holds 100 intervals: 90 of 4 ms, 5 of 40 ms, and 5 of 60 ms, in five groups.
    first = np.concatenate(([0.0], np.cumsum(np.tile([*([0.004] * 18), 0.040, 0.060], 5))))
    second = first + 10.0
    xy = np.column_stack((10 * first, np.zeros(101)))
    trial = make_trial(first, strokes=(Stroke(first, xy), Stroke(second, xy)))
    cells = features_of(trial)
    assert cells["sampling.count"] == 200
    assert cells["sampling.median_s"] == pytest.approx(0.004)
    assert cells["sampling.p5_s"] == pytest.approx(0.004)
    assert cells["sampling.p95_s"] == pytest.approx(0.041)
    assert cells["sampling.share_above_30ms"] == pytest.approx(0.10)
    assert cells["sampling.share_above_max_gap"] == pytest.approx(0.05)


def test_a_run_without_millimetres_is_refused(move_lists: dict[str, MoveList]) -> None:
    # Detects bench features computed on positions in source units.
    trial = device_run(move_lists["line-osc5"])
    qc = assess(replace(trial, device=Device("synthetic", "pencil", spatial_unit="source")))
    assert isinstance(qc, Accepted)
    result = run_features(qc)
    assert isinstance(result.motion, Refusal)
    assert result.motion.code == RefusalCode.UNKNOWN_SCALE
    assert all(
        isinstance(c, Refusal) and c.code == RefusalCode.UNKNOWN_SCALE
        for name, c in result.cells.items()
        if name.startswith("bench.")
    )
    assert not isinstance(result.cells["sampling.count"], Refusal)
