from __future__ import annotations

import functools
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import bench_camera_render as render
import numpy as np
import pytest

from scriptkin.bench import program
from scriptkin.bench.camera import (
    Calibration,
    CameraFileError,
    Clock,
    Frame,
    Region,
    read_frame_times,
    sha256_file,
    write_camera_file,
)
from scriptkin.bench.clock import led_levels, measure_clock, measure_readout
from scriptkin.bench.track import FrameTimes, Track, TrackFacts, read_track
from scriptkin.bench.tracker import TRACKER_VERSION, find_marker, read_video, track_clip, track_frames
from scriptkin.quality import Refusal, RefusalCode
from scriptkin.recording import FloatArray

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bench_camera"
CLIP = FIXTURES / f"{render.FIXTURE_RUN}.avi"
FACTS = TrackFacts(
    "synthetic",
    "0" * 64,
    "0" * 64,
    "0" * 64,
    FrameTimes.PLAYBACK,
    render.RATE_HZ,
    render.ROW_TIME_S,
    TRACKER_VERSION,
)


@functools.cache
def _study() -> Any:
    path = Path(__file__).resolve().parents[1] / "studies" / "bench_programs.py"
    spec = importlib.util.spec_from_file_location("bench_programs_tracker_test", path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = script
    spec.loader.exec_module(script)
    return script


def _program(program_id: str) -> tuple[render.Motion, float]:
    """The registered path of a program, centred on the plane point (40, 40)."""
    times, xy, _ = _study().timed_path(program(program_id))
    return render.sampled(times, xy - (xy.min(axis=0) + xy.max(axis=0)) / 2 + (40.0, 40.0))


def _track(background: render.Background, motion: render.Motion, duration: float, **frames: Any) -> Track:
    scene = render.Scene(render.main_camera(), background)
    n = round(duration * render.RATE_HZ)
    start = motion(np.zeros(1))[0]
    return track_frames(
        scene.frames(motion, n, **frames),
        np.arange(n) / render.RATE_HZ,
        render.estimated_calibration(background),
        (float(start[0]), float(start[1])),
        FACTS,
    )


def _errors(track: Track, motion: render.Motion, times: FloatArray) -> FloatArray:
    use = track.found & ~track.duplicate
    return np.linalg.norm(track.xy[use] - motion(times[use]), axis=1)


def _rms(errors: FloatArray) -> float:
    return float(np.sqrt(np.mean(errors**2)))


def _best_lag_rms(track: Track, motion: render.Motion) -> float:
    """The error without the row correction after the best constant time shift, which the alignment of
    protocol section 7.4 removes."""
    return min(_rms(_errors(track, motion, track.t_frame + lag)) for lag in np.arange(-5e-3, 5e-3, 5e-6))


@pytest.mark.parametrize("background", ["ipad", "wacom"])
def test_static_grid_is_within_0_05_mm(background: render.Background) -> None:
    # Detects a calibration or centroid error that moves a still marker past the limit of protocol 7.5 step 2.
    differences = []
    for x in (-10.0, 15.0, 40.0, 65.0, 90.0):
        for y in (-10.0, 15.0, 40.0, 65.0, 90.0):
            track = _track(background, render.still((x, y)), 4 / render.RATE_HZ)
            assert track.found.all()
            differences.append(track.xy.mean(axis=0) - (x, y))
    assert _rms(np.linalg.norm(differences, axis=1)) <= 0.05


@pytest.mark.parametrize(
    ("background", "direction", "speed"),
    [("ipad", (1.0, 0.0), 20.0), ("wacom", (1.0, 0.0), 40.0), ("ipad", (0.0, 1.0), 40.0)],
)
def test_line_speed_and_length_meet_the_speed_test(
    background: render.Background, direction: tuple[float, float], speed: float
) -> None:
    # Detects a scale or clock error that fails protocol 7.5 step 3: speed within 1 %, length within 0.5 mm.
    start = (-10.0, 40.0) if direction[0] else (40.0, -10.0)
    motion, duration = render.line(start, direction, speed)
    track = _track(background, motion, duration)
    assert track.found.all()
    t, xy = track.t, track.xy
    first, last = t < 0.35, t > duration - 0.35
    length = float(np.linalg.norm(xy[last].mean(axis=0) - xy[first].mean(axis=0)))
    cruise = (t > 0.65) & (t < duration - 0.65)
    along = (xy[cruise] - start) @ np.array(direction)
    measured = float(np.polyfit(t[cruise], along, 1)[0])
    assert abs(measured / speed - 1) <= 0.01
    assert abs(length - 100.0) <= 0.5


@pytest.mark.parametrize("background", ["ipad", "wacom"])
def test_registered_program_path_is_reproduced(background: render.Background) -> None:
    # Detects a tracker that loses or misplaces the marker on loops-s-v40 on either device background.
    motion, duration = _program("loops-s-v40")
    track = _track(background, motion, duration)
    assert track.found.all() and not track.duplicate.any()
    errors = _errors(track, motion, track.t)
    assert _rms(errors) <= 0.05
    assert errors.max() <= 0.1


def test_row_correction_reduces_the_error_on_the_fast_program() -> None:
    # Detects a tracker that ignores the row time of a rolling shutter or applies it with the wrong sign.
    motion, duration = _program("loops-l-v80")
    track = _track("wacom", motion, duration)
    corrected = _rms(_errors(track, motion, track.t))
    assert corrected <= 0.02
    assert _rms(_errors(track, motion, track.t_frame)) > 5 * corrected
    assert _best_lag_rms(track, motion) > 2 * corrected


def test_duplicates_led_states_and_frames_without_the_marker_are_reported_where_they_are() -> None:
    # Detects a tracker that drops, fills, or moves a duplicate, an LED state, or a frame without the dot.
    motion, duration = render.line((-10.0, 40.0), (1.0, 0.0), 40.0)
    duplicates, missing = {100, 101, 400}, {300, 301, 302, 600}
    track = _track("ipad", motion, duration, duplicates=frozenset(duplicates), missing=frozenset(missing))
    assert set(np.flatnonzero(track.duplicate).tolist()) == duplicates
    assert set(np.flatnonzero(~track.found).tolist()) == missing
    assert np.array_equal(track.xy[101], track.xy[99])
    assert _errors(track, motion, track.t).max() <= 0.05
    source = np.arange(len(track.frame))
    for k in sorted(duplicates):
        source[k] = source[k - 1]
    t0 = source / render.RATE_HZ
    top = render.led_on(t0 + render.LED.top * render.ROW_TIME_S)
    bottom = render.led_on(t0 + (render.LED.top + render.LED.height - 1) * render.ROW_TIME_S)
    clear = top == bottom
    assert np.array_equal(track.led[clear], top[clear])
    assert track.led.any() and not track.led.all()


def test_a_bright_region_on_the_square_edge_does_not_count() -> None:
    # Detects a marker search that takes a region cut by the search square, such as a lit device edge.
    frame = np.full((200, 200), 15, np.uint8)
    frame[:, 150:] = 220
    yy, xx = np.mgrid[0:200, 0:200]
    frame[(yy - 100.0) ** 2 + (xx - 80.0) ** 2 <= 13.0**2] = 235
    outline = np.array([[57.0, 57.0], [157.0, 57.0], [157.0, 157.0], [57.0, 157.0]])
    found = find_marker(frame, outline)
    assert found is not None
    np.testing.assert_allclose(found, (80.0, 100.0), atol=0.05)


def test_a_dim_smudge_without_the_dot_is_not_a_marker() -> None:
    # Detects a marker search that turns a faint blob on the black field into a marker.
    frame = np.random.default_rng(3).normal(18.0, 2.0, (200, 200))
    yy, xx = np.mgrid[0:200, 0:200]
    frame[(yy - 100.0) ** 2 + (xx - 100.0) ** 2 <= 15.0**2] += 8.0
    outline = np.array([[57.0, 57.0], [157.0, 57.0], [157.0, 157.0], [57.0, 157.0]])
    assert find_marker(np.clip(np.rint(frame), 0, 255).astype(np.uint8), outline) is None


def _led_clip(rate: float, seconds: float) -> list[Frame]:
    rng = np.random.default_rng(5)
    on = render.led_on(np.arange(round(seconds * rate)) / rate)
    return [
        np.full((16, 16), 240 if s else 20, np.uint8) + rng.integers(0, 3, (16, 16), dtype=np.uint8)
        for s in on
    ]


@pytest.mark.parametrize("rate", [render.RATE_HZ, 239.8])
def test_clock_recovers_the_rate_and_tells_capture_from_playback_times(rate: float) -> None:
    # Detects a frame clock that misses the LED rate, or that takes playback times for capture times.
    levels = led_levels(_led_clip(rate, 60.5), Region(0, 0, 16, 16))
    k = np.arange(len(levels))
    capture = measure_clock(levels, np.round(k / rate, 6), led_hz=1.0, nominal_hz=240.0)
    playback = measure_clock(levels, np.round(k / 30.0, 6), led_hz=1.0, nominal_hz=240.0)
    assert isinstance(capture, Clock) and isinstance(playback, Clock)
    # One frame over the 59 or more periods between the first and the last edge is the resolution.
    assert abs(capture.frame_rate_hz / rate - 1) < 1 / (59 * 240)
    assert capture.frame_times is FrameTimes.CAPTURE
    assert playback.frame_times is FrameTimes.PLAYBACK
    assert playback.frame_rate_hz == capture.frame_rate_hz
    assert capture.led_periods >= 59


def test_clock_refuses_a_rate_off_the_nominal_rate_and_a_short_clip() -> None:
    # Detects a frame clock that accepts a mode 0.2 percent off 240 Hz, or a clip shorter than 59 LED periods.
    region = Region(0, 0, 16, 16)
    slow = led_levels(_led_clip(239.5, 60.5), region)
    refused = measure_clock(slow, np.arange(len(slow)) / 239.5, led_hz=1.0, nominal_hz=240.0)
    assert isinstance(refused, Refusal) and refused.code is RefusalCode.OUT_OF_RANGE
    short = led_levels(_led_clip(render.RATE_HZ, 30.0), region)
    refused = measure_clock(short, np.arange(len(short)) / render.RATE_HZ, led_hz=1.0, nominal_hz=240.0)
    assert isinstance(refused, Refusal) and refused.code is RefusalCode.TOO_FEW_VALUES


def _band_frames(row_time: float, exposure: float, count: int = 20) -> list[Frame]:
    """Frames of an LED at 1 kHz with a 50 percent duty cycle that lights every row."""

    def lit(t: FloatArray) -> FloatArray:
        cycles = t * 1000.0
        return (np.floor(cycles) * 0.5 + np.minimum(cycles - np.floor(cycles), 0.5)) / 1000.0

    rng = np.random.default_rng(9)
    frames: list[Frame] = []
    for k in range(count):
        start = k / render.RATE_HZ + np.arange(1080, dtype=np.float64) * row_time
        share = (lit(start + exposure) - lit(start)) / exposure
        level = 20.0 + 200.0 * share[:, None] + rng.normal(0.0, 2.0, (1080, 32))
        frames.append(np.clip(np.rint(level), 0, 255).astype(np.uint8))
    return frames


def test_readout_recovers_the_row_time() -> None:
    # Detects a readout that misreads the band pitch, so that the row correction uses a wrong row time.
    readout = measure_readout(_band_frames(render.ROW_TIME_S, 1e-4), Region(0, 0, 32, 1080), led_hz=1000.0)
    assert not isinstance(readout, Refusal)
    assert abs(readout.row_time_s / render.ROW_TIME_S - 1) < 2e-3
    assert readout.frames == 20


@pytest.mark.parametrize(("row_time", "exposure"), [(0.0, 1e-4), (render.ROW_TIME_S, 1e-3)])
def test_readout_refuses_frames_without_bands(row_time: float, exposure: float) -> None:
    # Detects a readout that reports a row time for a global shutter or for an exposure of one LED period.
    readout = measure_readout(_band_frames(row_time, exposure), Region(0, 0, 32, 1080), led_hz=1000.0)
    assert isinstance(readout, Refusal) and readout.code is RefusalCode.TOO_FEW_VALUES


def test_frames_file_reader_reads_the_real_ffprobe_output() -> None:
    # Detects a frames-file reader that misreads the presentation times that ffprobe writes.
    pts = read_frame_times(FIXTURES / f"{render.FIXTURE_RUN}.frames.json")
    np.testing.assert_allclose(pts, np.arange(render.FIXTURE_FRAMES) / 240.0, atol=5e-7)


def test_frames_file_reader_refuses_two_video_streams_and_a_frame_without_a_time(tmp_path: Path) -> None:
    # Detects a frames-file reader that mixes video streams or invents a time for a frame.
    frames = json.loads((FIXTURES / f"{render.FIXTURE_RUN}.frames.json").read_text())["frames"]
    audio = {"media_type": "audio", "stream_index": 1, "pts_time": "0.000000"}
    path = tmp_path / "x.frames.json"
    path.write_text(json.dumps({"frames": [*frames, audio]}))
    assert len(read_frame_times(path)) == render.FIXTURE_FRAMES
    path.write_text(json.dumps({"frames": [*frames, {**frames[0], "stream_index": 2}]}))
    with pytest.raises(CameraFileError, match="one video stream"):
        read_frame_times(path)
    path.write_text(json.dumps({"frames": [{k: v for k, v in frames[0].items() if k != "pts_time"}]}))
    with pytest.raises(CameraFileError, match="pts_time"):
        read_frame_times(path)


def test_video_boundary_decodes_every_frame_of_the_real_clip_unchanged() -> None:
    # Detects a video boundary that drops, reorders, or alters the frames that the codec stores.
    decoded = list(read_video(CLIP))
    expected = render.fixture_frames()
    assert len(decoded) == len(expected) == render.FIXTURE_FRAMES
    assert all(np.array_equal(a, b) for a, b in zip(decoded, expected, strict=True))


def test_tracker_tracks_the_real_clip(tmp_path: Path) -> None:
    # Detects a tracker that loses a frame observation of a real clip or names the wrong input files.
    camera = render.fixture_camera()
    calibration = write_camera_file(
        Calibration(camera.plane(), (0.0, 0.0), render.FIXTURE_LED), tmp_path / "c.json"
    )
    clock = write_camera_file(Clock(240.0, FrameTimes.CAPTURE, 1.0, 59, 1.0, 240.0), tmp_path / "clock.json")
    track_clip(
        CLIP, calibration=calibration, clock=clock, readout=None, start_mm=(-2.5, 0.4), out_dir=tmp_path
    )
    track = read_track(tmp_path / f"{render.FIXTURE_RUN}.track.csv")
    facts = track.facts
    assert facts.run_id == render.FIXTURE_RUN
    assert facts.video_sha256 == sha256_file(CLIP)
    assert facts.frames_sha256 == sha256_file(FIXTURES / f"{render.FIXTURE_RUN}.frames.json")
    assert facts.calibration_sha256 == sha256_file(calibration)
    assert (facts.frame_times, facts.frame_rate_hz, facts.row_time_s) == (FrameTimes.CAPTURE, 240.0, 0.0)
    np.testing.assert_allclose(track.t_frame, np.arange(render.FIXTURE_FRAMES) / 240.0, atol=5e-7)
    assert np.flatnonzero(track.duplicate).tolist() == [render.FIXTURE_DUPLICATE]
    assert np.flatnonzero(~track.found).tolist() == [render.FIXTURE_MISSING]
    assert np.array_equal(track.led, np.arange(render.FIXTURE_FRAMES) < render.FIXTURE_LED_FRAMES)
    assert _errors(track, render.fixture_motion, track.t).max() < 0.01
