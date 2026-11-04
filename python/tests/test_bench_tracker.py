from __future__ import annotations

import functools
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import bench_camera_render as render
import cv2
import numpy as np
import pytest

from scriptkin.bench import program
from scriptkin.bench.camerafiles import (
    Calibration,
    CameraFileError,
    Clock,
    Frame,
    Readout,
    Region,
    read_camera_file,
    read_frame_times,
    sha256_file,
    write_camera_file,
)
from scriptkin.bench.clock import led_levels, measure_clock, measure_readout
from scriptkin.bench.track import FrameTimes, ReadoutDirection, Track, TrackFacts, read_track
from scriptkin.bench.tracker import TRACKER_VERSION, find_marker, read_video, track_frames
from scriptkin.cli import main
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


def _track(
    background: render.Background,
    motion: render.Motion,
    duration: float,
    readout: ReadoutDirection = ReadoutDirection.TOP_FIRST,
    **frames: Any,
) -> Track:
    scene = render.Scene(render.main_camera(), background, readout=readout)
    n = round(duration * render.RATE_HZ)
    start = motion(np.zeros(1))[0]
    return track_frames(
        scene.frames(motion, n, **frames),
        np.arange(n) / render.RATE_HZ,
        render.estimated_calibration(background),
        (float(start[0]), float(start[1])),
        replace(FACTS, readout_direction=readout),
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


def _placed(program_id: str) -> tuple[render.Motion, float]:
    """The registered path of a program in the plane, with the nadir of the camera at the centre of the
    largest frame, 132 x 104 mm (protocol section 7.1)."""
    times, xy, _ = _study().timed_path(program(program_id))
    origin = render.main_camera().centre[:2] + np.array([-66.0, 52.0])
    return render.sampled(times, origin + xy * (1.0, -1.0), hold_s=0.0)


@pytest.mark.parametrize("lift_mm", [10.0, 17.0])
@pytest.mark.parametrize(("program_id", "background"), [("loops-l-v20", "wacom"), ("line-osc5", "ipad")])
def test_marker_is_followed_from_the_lift_height_through_the_run(
    program_id: str, background: render.Background, lift_mm: float
) -> None:
    # Detects a first-frame search that misses the marker, which the perspective of the lift height moves
    # away from the start point (protocol section 5.2 step 3), or a tracker that loses it on the way down.
    motion, duration = _placed(program_id)
    tip, height, total = render.bench_run(motion, duration, lift_mm)
    track = _track(background, tip, total, height=height)
    assert int((~track.found).sum()) == 0
    offset = np.array(render.MARKER_OFFSET_MM)
    seen = render.main_camera().seen(tip(track.t) + offset, height(track.t))[0] - offset
    assert np.linalg.norm(track.xy - seen, axis=1).max() <= 0.1


@pytest.mark.parametrize("readout", list(ReadoutDirection))
def test_row_correction_reduces_the_error_on_the_fast_program(readout: ReadoutDirection) -> None:
    # Detects a tracker that ignores the row time of a rolling shutter, applies it with the wrong sign, or
    # ignores the readout direction, which doubles the error of a bottom-first sensor (protocol 7.3).
    motion, duration = _program("loops-l-v80")
    track = _track("wacom", motion, duration, readout)
    corrected = _rms(_errors(track, motion, track.t))
    assert corrected <= 0.01
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


def _write_clip(path: Path, frames: list[Frame], *, playback_hz: float) -> None:
    """A lossless clip, and a frames file in the shape that ffprobe writes, with playback times."""
    h, w = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.CAP_FFMPEG, cv2.VideoWriter.fourcc(*"FFV1"), 240.0, (w, h))
    for frame in frames:
        writer.write(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))
    writer.release()
    listed = [
        {"media_type": "video", "stream_index": 0, "pts_time": f"{k / playback_hz:.6f}"}
        for k in range(len(frames))
    ]
    path.with_name(f"{path.stem}.frames.json").write_text(json.dumps({"frames": listed}))


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


def _band_frames(
    row_time: float,
    exposure: float,
    count: int = 20,
    readout: ReadoutDirection = ReadoutDirection.TOP_FIRST,
) -> list[Frame]:
    """Frames of an LED at 1 kHz with a 50 percent duty cycle that lights every row."""

    def lit(t: FloatArray) -> FloatArray:
        cycles = t * 1000.0
        return (np.floor(cycles) * 0.5 + np.minimum(cycles - np.floor(cycles), 0.5)) / 1000.0

    rng = np.random.default_rng(9)
    rows = np.arange(1080, dtype=np.float64)
    first = rows if readout is ReadoutDirection.TOP_FIRST else 1079 - rows
    frames: list[Frame] = []
    for k in range(count):
        start = k / render.RATE_HZ + first * row_time
        share = (lit(start + exposure) - lit(start)) / exposure
        level = 20.0 + 200.0 * share[:, None] + rng.normal(0.0, 2.0, (1080, 32))
        frames.append(np.clip(np.rint(level), 0, 255).astype(np.uint8))
    return frames


BAND_REGION = Region(0, 0, 32, 1080)


@pytest.mark.parametrize("direction", list(ReadoutDirection))
def test_readout_recovers_the_row_time_and_the_direction(direction: ReadoutDirection) -> None:
    # Detects a readout that misreads the band pitch or the direction of the bands between frames, so that
    # the row correction uses a wrong row time or reads the rows the wrong way.
    frames = _band_frames(render.ROW_TIME_S, 1e-4, readout=direction)
    readout = measure_readout(frames, BAND_REGION, led_hz=1000.0, nominal_hz=240.0)
    assert not isinstance(readout, Refusal)
    assert abs(readout.row_time_s / render.ROW_TIME_S - 1) < 2e-3
    assert readout.direction is direction
    assert readout.frames == 20


@pytest.mark.parametrize(("row_time", "exposure"), [(0.0, 1e-4), (render.ROW_TIME_S, 1e-3)])
def test_readout_refuses_frames_without_bands(row_time: float, exposure: float) -> None:
    # Detects a readout that reports a row time for a global shutter or for an exposure of one LED period.
    readout = measure_readout(_band_frames(row_time, exposure), BAND_REGION, led_hz=1000.0, nominal_hz=240.0)
    assert isinstance(readout, Refusal) and readout.code is RefusalCode.TOO_FEW_VALUES


def test_readout_refuses_a_frame_rate_at_which_the_bands_stand_still() -> None:
    # Detects a readout that names a direction when the LED phase moves by a whole period per frame.
    frames = _band_frames(render.ROW_TIME_S, 1e-4)
    readout = measure_readout(frames, BAND_REGION, led_hz=1000.0, nominal_hz=250.0)
    assert isinstance(readout, Refusal) and readout.code is RefusalCode.OUT_OF_RANGE


def test_clock_and_readout_commands_read_their_clips(tmp_path: Path) -> None:
    # Detects a clock or readout command that misreads its clip, its frames file, or its region.
    _write_clip(tmp_path / "clock.avi", _led_clip(239.8, 60.5), playback_hz=30.0)
    bands = _band_frames(render.ROW_TIME_S, 1e-4, readout=ReadoutDirection.BOTTOM_FIRST)
    _write_clip(tmp_path / "bands.avi", bands, playback_hz=30.0)
    clock, readout = tmp_path / "clock.json", tmp_path / "readout.json"
    led = ["--led", "0", "0", "16", "16"]
    assert main(["bench", "clock", *led, "--out", str(clock), str(tmp_path / "clock.avi")]) == 0
    region = ["--region", "0", "0", "32", "1080"]
    assert main(["bench", "readout", *region, "--out", str(readout), str(tmp_path / "bands.avi")]) == 0
    measured = read_camera_file(Clock, clock)
    assert measured.frame_times is FrameTimes.PLAYBACK
    assert abs(measured.frame_rate_hz / 239.8 - 1) < 1 / (59 * 240)
    measured_readout = read_camera_file(Readout, readout)
    assert abs(measured_readout.row_time_s / render.ROW_TIME_S - 1) < 2e-3
    assert measured_readout.direction is ReadoutDirection.BOTTOM_FIRST
    names = [s["name"] for s in json.loads(clock.read_text())["sources"]]
    assert names == ["clock.avi", "clock.frames.json"]


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


def test_track_command_tracks_the_real_clip(tmp_path: Path) -> None:
    # Detects a track command that loses a frame observation of a real clip or names the wrong input files.
    camera = render.fixture_camera()
    calibration = write_camera_file(
        Calibration(camera.plane(), (0.0, 0.0), render.FIXTURE_LED), tmp_path / "c.json"
    )
    clock = write_camera_file(Clock(240.0, FrameTimes.CAPTURE, 1.0, 59, 1.0, 240.0), tmp_path / "clock.json")
    command = ["bench", "track", str(CLIP), "--calibration", str(calibration), "--clock", str(clock)]
    assert main([*command, "--global-shutter", "--start-mm", "-2.5", "0.4", "--out-dir", str(tmp_path)]) == 0
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


def test_track_command_applies_the_direction_of_the_readout_file(tmp_path: Path) -> None:
    # Detects a track command that drops the readout direction from the row correction or the track facts.
    camera = render.fixture_camera()
    calibration = write_camera_file(
        Calibration(camera.plane(), (0.0, 0.0), render.FIXTURE_LED), tmp_path / "c.json"
    )
    clock = write_camera_file(Clock(240.0, FrameTimes.CAPTURE, 1.0, 59, 1.0, 240.0), tmp_path / "clock.json")
    row_time = 1e-5
    readout = write_camera_file(
        Readout(row_time, ReadoutDirection.BOTTOM_FIRST, 1 / (row_time * 1000.0), 1000.0, 10),
        tmp_path / "readout.json",
    )
    files = ["--calibration", str(calibration), "--clock", str(clock), "--readout", str(readout)]
    command = ["bench", "track", str(CLIP), *files, "--start-mm", "-2.5", "0.4", "--out-dir", str(tmp_path)]
    assert main(command) == 0
    track = read_track(tmp_path / f"{render.FIXTURE_RUN}.track.csv")
    assert track.facts.readout_direction is ReadoutDirection.BOTTOM_FIRST
    assert track.facts.row_time_s == row_time
    found = track.found
    rows = camera.project(render.fixture_motion(track.t_frame[found]))[:, 1]
    rows_before = (track.t - track.t_frame)[found] / row_time
    np.testing.assert_allclose(rows_before, camera.size[1] - 1 - rows, atol=0.05)
