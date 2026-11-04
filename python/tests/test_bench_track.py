from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from scriptkin.bench.track import (
    FrameTimes,
    Track,
    TrackError,
    TrackFacts,
    read_track,
    track_paths,
    write_track,
)

RUN = "P-M3-loops-s-v40-r2"


def _facts(**changes: Any) -> TrackFacts:
    facts = TrackFacts(
        run_id=RUN,
        video_sha256="a" * 64,
        frames_sha256="b" * 64,
        calibration_sha256="c" * 64,
        frame_times=FrameTimes.PLAYBACK,
        frame_rate_hz=239.987,
        row_time_s=1.7e-5,
        tracker_version="1",
    )
    return replace(facts, **changes)


def _track() -> Track:
    n = 6
    t_frame = np.arange(n) / 239.987
    rows = np.array([0.0, 120.0, 340.0, 0.0, 560.0, 1079.0])
    t = t_frame + rows * 1.7e-5
    xy = np.column_stack((10 + np.arange(n) / 3, -4.0 + np.arange(n) / 7))
    t[3] = np.nan
    xy[3] = np.nan
    duplicate = np.array([False, False, True, False, False, False])
    led = np.array([True, True, True, False, False, False])
    return Track(_facts(), np.arange(n, dtype=np.int64), t_frame, t, xy, duplicate, led)


def test_round_trip_keeps_every_value_and_the_missing_frame(tmp_path: Path) -> None:
    # Detects a lossy number format, or a missing marker read back as a position.
    track = _track()
    rows_path, _ = write_track(track, tmp_path)
    back = read_track(rows_path)
    assert back.facts == track.facts
    assert np.array_equal(back.t_frame, track.t_frame)
    assert np.array_equal(back.t, track.t, equal_nan=True)
    assert np.array_equal(back.xy, track.xy, equal_nan=True)
    assert back.found.tolist() == [True, True, True, False, True, True]
    assert back.duplicate.tolist() == track.duplicate.tolist()
    assert back.led.tolist() == track.led.tolist()


def test_a_track_of_another_run_is_refused(tmp_path: Path) -> None:
    # Detects a facts file copied from another run beside a rows file.
    write_track(_track(), tmp_path)
    rows_path, facts_path = track_paths(tmp_path, RUN)
    other = tmp_path / "P-M3-loops-s-v40-r3.track.csv"
    other.write_bytes(rows_path.read_bytes())
    other.with_name("P-M3-loops-s-v40-r3.track.json").write_bytes(facts_path.read_bytes())
    with pytest.raises(TrackError, match="names run"):
        read_track(other)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [("frame,t_frame_s", "frame,t_s_frame", "header"), (",1,1\n", ",1,yes\n", "yes")],
)
def test_damaged_rows_are_refused(tmp_path: Path, old: str, new: str, message: str) -> None:
    # Detects a reader that accepts a changed header, or a flag that is not 0 or 1.
    rows_path, _ = write_track(_track(), tmp_path)
    rows_path.write_text(rows_path.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
    with pytest.raises(TrackError, match=message):
        read_track(rows_path)


def test_a_lost_frame_row_is_refused(tmp_path: Path) -> None:
    # Detects a reader that closes the hole of a missing frame, which the gap check of the analysis needs.
    rows_path, _ = write_track(_track(), tmp_path)
    lines = rows_path.read_text(encoding="utf-8").split("\n")
    rows_path.write_text("\n".join(lines[:3] + lines[4:]), encoding="utf-8")
    with pytest.raises(TrackError, match="frame 0 in order"):
        read_track(rows_path)


def test_a_marker_time_without_a_position_is_refused() -> None:
    # Detects a frame whose marker has a time but no coordinates.
    track = _track()
    xy = track.xy.copy()
    xy[1, 1] = np.nan
    with pytest.raises(TrackError, match="both coordinates"):
        replace(track, xy=xy)


def test_frame_times_must_increase() -> None:
    # Detects a duplicated presentation time accepted as a frame time.
    track = _track()
    t_frame = track.t_frame.copy()
    t_frame[2] = t_frame[1]
    with pytest.raises(TrackError, match="increasing"):
        replace(track, t_frame=t_frame)


@pytest.mark.parametrize(
    "changes",
    [{"video_sha256": "A" * 64}, {"frame_rate_hz": 0.0}, {"row_time_s": -1e-6}, {"tracker_version": ""}],
)
def test_invalid_facts_are_refused(changes: dict[str, Any]) -> None:
    # Detects an upper-case digest, a zero frame rate, a negative row time, or an unnamed tracker.
    with pytest.raises(TrackError):
        _facts(**changes)


def test_a_facts_file_of_another_version_is_refused(tmp_path: Path) -> None:
    # Detects a reader that reads a later track format with the rules of version 1.
    rows_path, facts_path = write_track(_track(), tmp_path)
    facts_path.write_text(facts_path.read_text(encoding="utf-8").replace('"version": 1', '"version": 2'))
    with pytest.raises(TrackError, match="version 1"):
        read_track(rows_path)
