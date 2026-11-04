from __future__ import annotations

import numpy as np
import pytest
import test_bench_features as features_tests
import test_bench_moves as moves_tests

from scriptkin.bench.camera import (
    CameraRule,
    ExecutedMovement,
    PathFit,
    SpatialAccuracy,
    TimeAlignment,
    camera_record,
    executed_movement,
    led_count_agrees,
    spatial_accuracy,
    time_alignment,
    trace_error,
)
from scriptkin.bench.features import Motion, commanded_motion, run_features
from scriptkin.bench.moves import MoveList
from scriptkin.bench.track import FrameTimes, Track, TrackFacts
from scriptkin.quality import Accepted, Refusal, assess

programs_folder = moves_tests.programs_folder
move_lists = moves_tests.move_lists
device_run = features_tests.device_run
rotated = features_tests.rotated

RATE = 239.987


def camera_track(
    move_list: MoveList,
    *,
    true_rate_hz: float = RATE,
    lead_s: float = 0.6,
    frame_times: FrameTimes = FrameTimes.PLAYBACK,
    rotation_deg: float = 0.0,
    shift_mm: tuple[float, float] = (0.0, 0.0),
    noise_mm: float = 0.0,
    speed_factor: float = 1.0,
    bump: tuple[float, float] | None = None,
    duplicate_at: int | None = None,
    stretch_at: int | None = None,
    drop_frames: tuple[int, ...] = (),
    missing_share: float = 0.0,
    seed: int = 1,
) -> Track:
    """A track of the commanded movement: still for `lead_s`, the movement at `speed_factor` times the
    commanded speed, then still. The marker stays visible in the air. `bump` moves the marker sideways
    by the given millimetres at the given time of the movement over five frames. The facts name the
    measured rate `RATE`; `true_rate_hz` is the rate of the clip."""
    rng = np.random.default_rng(seed)
    path = move_list.path()
    duration = path.duration_s / speed_factor
    count = int((lead_s + duration + 0.4) * true_rate_hz) + 1
    t_frame = np.arange(count) / true_rate_hz
    motion_t = np.clip((t_frame - lead_s) * speed_factor, 0.0, path.duration_s)
    xy = rotated(path.position(motion_t) * [1.0, -1.0], rotation_deg) + shift_mm
    xy = xy + rng.normal(0.0, noise_mm, xy.shape)
    if bump is not None:
        at = int(np.argmin(np.abs(motion_t - bump[0])))
        tangent = xy[at + 1] - xy[at - 1]
        normal = np.array([-tangent[1], tangent[0]]) / np.linalg.norm(tangent)
        xy[at : at + 5] += bump[1] * normal
    led = np.mod(t_frame - 0.3, 1.0) < 0.5
    duplicate = np.zeros(count, dtype=bool)
    if duplicate_at is not None:
        duplicate[duplicate_at] = True
        xy[duplicate_at] = xy[duplicate_at - 1]
    if stretch_at is not None:
        t_frame = t_frame.copy()
        t_frame[stretch_at:] += 1 / true_rate_hz
    keep = np.ones(count, dtype=bool)
    keep[list(drop_frames)] = False
    t_frame, xy, led, duplicate = t_frame[keep], xy[keep], led[keep], duplicate[keep]
    if frame_times == FrameTimes.PLAYBACK:
        t_frame = np.arange(len(t_frame)) / RATE
    t = t_frame.copy()
    missing = rng.choice(len(t), round(missing_share * len(t)), replace=False)
    t[missing] = np.nan
    xy[missing] = np.nan
    facts = TrackFacts(
        run_id=f"P-M1-{move_list.program.id}-r1",
        video_sha256="a" * 64,
        frames_sha256="b" * 64,
        calibration_sha256="c" * 64,
        frame_times=frame_times,
        frame_rate_hz=RATE,
        row_time_s=0.0,
        tracker_version="test",
    )
    return Track(facts, np.arange(len(t), dtype=np.int64), t_frame, t, xy, duplicate, led)


def device_motion(move_list: MoveList, **changes: object) -> Motion:
    qc = assess(device_run(move_list, **changes))  # type: ignore[arg-type]
    assert isinstance(qc, Accepted)
    found = run_features(qc).motion
    assert isinstance(found, Motion)
    return found


def test_the_track_as_a_recording_gives_the_features_of_the_movement(move_lists: dict[str, MoveList]) -> None:
    # Detects a camera record in the wrong frame, unit, or clock, or one that drops the still frames
    # into the motion interval.
    ml = move_lists["loops-l-v20"]
    record = camera_record(camera_track(ml))
    assert record.failed == ()
    assert record.valid
    device = features_tests.features_of(device_run(ml))
    cells = {k: v for k, v in record.run.cells.items() if not isinstance(v, Refusal)}
    assert cells["bench.motion_s"] == pytest.approx(device["bench.motion_s"], abs=0.015)
    assert cells["bench.motion_start_s"] == pytest.approx(device["bench.motion_start_s"] + 0.6, abs=0.01)
    assert cells["bench.path_length_mm"] == pytest.approx(device["bench.path_length_mm"], abs=0.5)
    assert cells["bench.speed_mean_mm_s"] == pytest.approx(device["bench.speed_mean_mm_s"], abs=0.1)
    assert cells["bench.loop_height_mm"] == pytest.approx(20.0, abs=0.05)
    assert cells["sampling.median_s"] == pytest.approx(1 / RATE)


@pytest.mark.parametrize(
    ("changes", "failed"),
    [
        ({}, ()),
        ({"duplicate_at": 500}, (CameraRule.DUPLICATE_FRAME,)),
        ({"duplicate_at": 20}, ()),
        ({"frame_times": FrameTimes.CAPTURE, "stretch_at": 500}, (CameraRule.FRAME_INTERVAL,)),
        ({"frame_times": FrameTimes.CAPTURE, "stretch_at": 20}, ()),
        ({"frame_times": FrameTimes.CAPTURE, "drop_frames": (500,)}, (CameraRule.FRAME_INTERVAL,)),
        ({"true_rate_hz": 239.0}, (CameraRule.LED_COUNT,)),
        ({"drop_frames": (500, 510)}, (CameraRule.LED_COUNT,)),
        ({"missing_share": 0.03}, (CameraRule.MARKER_MISSING,)),
        ({"missing_share": 0.01}, ()),
    ],
)
def test_each_validity_rule_fails_on_its_defect_and_passes_a_clean_track(
    move_lists: dict[str, MoveList], changes: dict[str, object], failed: tuple[CameraRule, ...]
) -> None:
    # Detects a rule that passes its defect inside the motion interval, fails a defect outside it, or
    # fails a clean track. The movement starts at frame 144; frame 20 and 500 are outside and inside.
    track = camera_track(move_lists["loops-s-v40"], **changes)  # type: ignore[arg-type]
    assert camera_record(track).failed == failed


def test_the_led_rule_allows_one_frame_and_fails_a_rate_error() -> None:
    # Detects an LED rule that fails the natural short period of a non-integer rate, or passes a rate
    # that is 0.5 percent off over the interval while every period is within one frame.
    periods = np.array([240] * 9 + [239] + [240] * 9)
    led = np.concatenate([np.arange(n) < n / 2 for n in periods])
    assert led_count_agrees(led, 239.987)
    assert not led_count_agrees(
        np.concatenate([np.arange(n) < n / 2 for n in [240] * 9 + [238, 240]]), 239.987
    )
    assert not led_count_agrees(np.concatenate([np.arange(n) < n / 2 for n in [239] * 19]), 240.2)
    assert not led_count_agrees(led[:300], 239.987)


def test_the_time_alignment_recovers_a_known_lag_below_the_frame(move_lists: dict[str, MoveList]) -> None:
    # Detects a lag with the wrong sign, one without the sub-sample refinement, or one that ignores the
    # motion starts of the two records.
    ml = move_lists["loops-s-v40"]
    device = device_motion(ml, noise_mm=0.01, jitter_s=0.0005, seed=3)
    for lead in (0.6, 0.6037):
        record = camera_record(camera_track(ml, lead_s=lead, noise_mm=0.01))
        assert isinstance(record.run.motion, Motion)
        alignment = time_alignment(device, record.run.motion)
        assert isinstance(alignment, TimeAlignment)
        assert alignment.lag_s == pytest.approx(lead, abs=0.001)
        assert alignment.correlation > 0.99


def test_the_spatial_accuracy_recovers_the_rotation_and_the_shift(move_lists: dict[str, MoveList]) -> None:
    # Detects a fit that reflects, scales, or ignores the time alignment.
    ml = move_lists["loops-l-v20"]
    device = device_motion(ml, noise_mm=0.01, seed=4)
    record = camera_record(camera_track(ml, rotation_deg=0.5, shift_mm=(1.0, 0.3), noise_mm=0.01))
    assert isinstance(record.run.motion, Motion)
    alignment = time_alignment(device, record.run.motion)
    assert isinstance(alignment, TimeAlignment)
    accuracy = spatial_accuracy(device, record.run.motion, alignment)
    assert isinstance(accuracy, SpatialAccuracy)
    assert accuracy.rotation_deg == pytest.approx(0.5, abs=0.01)
    assert accuracy.rms_mm < 0.03
    assert accuracy.p95_mm < 0.05
    assert accuracy.matched > 1000
    wrong = spatial_accuracy(device, record.run.motion, TimeAlignment(alignment.lag_s + 0.1, 0.0))
    assert isinstance(wrong, SpatialAccuracy)
    assert wrong.rms_mm > 1.0


@pytest.mark.parametrize("pid", ["spiral-s-v40", "loops-l-v20", "line-osc8"])
def test_the_path_fit_recovers_a_shift_and_a_rotation(move_lists: dict[str, MoveList], pid: str) -> None:
    # Detects a fit that cannot undo 1 mm and 0.5 degrees, one that turns the loops by 180 degrees onto
    # their mirror image, or a path that is not mirrored into the reader frame.
    ml = move_lists[pid]
    device = device_motion(ml, rotation_deg=0.5, shift_mm=(1.0, 0.0), noise_mm=0.005, seed=5)
    fit = trace_error(device, ml)
    assert isinstance(fit, PathFit)
    assert fit.rotation_deg == pytest.approx(-0.5, abs=0.02)
    assert fit.rms_mm < 0.01
    assert fit.max_mm < 0.05
    assert fit.iterations < 100


def test_the_executed_movement_check_fails_a_bump_and_a_slow_run(move_lists: dict[str, MoveList]) -> None:
    # Detects a check that passes a 0.3 mm departure from the path, passes a run 1.5 percent too slow,
    # or takes the commanded duration from the sum of the moves instead of the motion interval rule.
    ml = move_lists["loops-s-v40"]
    commanded = commanded_motion(ml)
    assert isinstance(commanded, Motion)
    clean = executed_movement(camera_record(camera_track(ml, noise_mm=0.005)), ml)
    assert isinstance(clean, ExecutedMovement)
    assert not clean.failure
    assert clean.fit.max_mm < 0.1
    assert clean.commanded_s == pytest.approx(commanded.interval.duration_s)
    assert clean.commanded_s < ml.duration_s
    assert clean.motion_s == pytest.approx(clean.commanded_s, abs=0.015)
    bumped = executed_movement(camera_record(camera_track(ml, noise_mm=0.005, bump=(3.0, 0.3))), ml)
    assert isinstance(bumped, ExecutedMovement)
    assert bumped.failure
    assert bumped.fit.max_mm == pytest.approx(0.3, abs=0.05)
    slow = executed_movement(camera_record(camera_track(ml, noise_mm=0.005, speed_factor=1 / 1.015)), ml)
    assert isinstance(slow, ExecutedMovement)
    assert slow.failure
    assert slow.fit.max_mm < 0.1
    assert slow.motion_s == pytest.approx(clean.commanded_s * 1.015, abs=0.02)
