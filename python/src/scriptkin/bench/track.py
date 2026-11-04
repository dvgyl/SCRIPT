"""The camera track of one bench run: what the tracker observes in each frame of the clip.

A track is two files beside the clip (protocol section 15). `<run id>.track.csv` has one row per decoded
frame. `<run id>.track.json` has the facts of the clip and of the tracker. The tracker only reports its
observations. The analysis applies the validity rules of protocol section 7.3, because they hold inside
the motion interval, and only the analysis finds the motion interval.

Positions are in millimetres in the marker plane, with the marker offset subtracted. The x axis is the
device x axis, and y is up, as in every reader output of `scriptkin.recording`.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from scriptkin.formats import FormatError
from scriptkin.recording import FloatArray, IntArray

BoolArray = NDArray[np.bool_]

TRACK_FORMAT = "script-bench-track"
TRACK_VERSION = 2
COLUMNS = ("frame", "t_frame_s", "t_s", "x_mm", "y_mm", "duplicate", "led")
_SHA256_LENGTH = 64


class TrackError(FormatError):
    pass


class FrameTimes(StrEnum):
    CAPTURE = "capture"
    """The presentation timestamps of the file are capture times, and `t_frame_s` comes from them."""
    PLAYBACK = "playback"
    """The file holds playback times, so `t_frame_s` is the frame index divided by the measured rate."""


class ReadoutDirection(StrEnum):
    """The row of the decoded frame that a rolling shutter reads first (protocol section 7.3)."""

    TOP_FIRST = "top-first"
    BOTTOM_FIRST = "bottom-first"
    """The last row comes first, as in a clip that the camera stores turned by a half turn."""


@dataclass(frozen=True)
class TrackFacts:
    run_id: str
    video_sha256: str
    frames_sha256: str
    """The SHA-256 of the `ffprobe` frames file of the clip."""
    calibration_sha256: str
    """The SHA-256 of the calibration file of the mounting: the lens, the plane, and the marker offset."""
    frame_times: FrameTimes
    frame_rate_hz: float
    """The capture rate that the frame clock check measured (protocol section 7.3)."""
    row_time_s: float
    """The row readout time of a rolling shutter. It is 0 for a global shutter."""
    tracker_version: str
    readout_direction: ReadoutDirection = ReadoutDirection.TOP_FIRST
    """A global shutter records top-first, which has no effect with a row time of 0."""

    def __post_init__(self) -> None:
        for name in ("video_sha256", "frames_sha256", "calibration_sha256"):
            value = getattr(self, name)
            if len(value) != _SHA256_LENGTH or any(c not in "0123456789abcdef" for c in value):
                raise TrackError(f"{name} must be a lower-case SHA-256.")
        if not self.run_id or not self.tracker_version:
            raise TrackError("The run id and the tracker version must not be empty.")
        if not (math.isfinite(self.frame_rate_hz) and self.frame_rate_hz > 0):
            raise TrackError("The frame rate must be positive.")
        if not (math.isfinite(self.row_time_s) and self.row_time_s >= 0):
            raise TrackError("The row time must not be negative.")


@dataclass(frozen=True)
class Track:
    """The rows of one clip in frame order. `t` and `xy` are NaN in a frame without a marker."""

    facts: TrackFacts
    frame: IntArray
    t_frame: FloatArray
    """The capture time of each frame, in seconds from the first frame."""
    t: FloatArray
    """The time of the marker centroid: `t_frame` plus the row correction of protocol section 7.3."""
    xy: FloatArray
    duplicate: BoolArray
    """The frame equals the previous frame in every pixel."""
    led: BoolArray
    """The clock LED is on in the frame."""

    def __post_init__(self) -> None:
        n = len(self.frame)
        if (
            self.t_frame.shape != (n,)
            or self.t.shape != (n,)
            or self.xy.shape != (n, 2)
            or self.duplicate.shape != (n,)
            or self.led.shape != (n,)
        ):
            raise TrackError("Track arrays must have shapes (n,) and (n, 2).")
        if not np.array_equal(self.frame, np.arange(n)):
            raise TrackError("A track has one row for each frame, from frame 0 in order.")
        if n and (not np.isfinite(self.t_frame).all() or np.any(np.diff(self.t_frame) <= 0)):
            raise TrackError("Frame times must be finite and increasing.")
        found = np.isfinite(self.t)
        if not np.array_equal(np.isfinite(self.xy), np.column_stack((found, found))):
            raise TrackError("A frame has a time and both coordinates of the marker, or none of them.")
        for a in (self.frame, self.t_frame, self.t, self.xy, self.duplicate, self.led):
            a.setflags(write=False)

    @property
    def found(self) -> BoolArray:
        return np.isfinite(self.t)


def track_paths(folder: str | Path, run_id: str) -> tuple[Path, Path]:
    """The rows file and the facts file of one run."""
    return Path(folder) / f"{run_id}.track.csv", Path(folder) / f"{run_id}.track.json"


def _number(value: float) -> str:
    return "" if math.isnan(value) else repr(float(value))


def write_track(track: Track, folder: str | Path) -> tuple[Path, Path]:
    rows_path, facts_path = track_paths(folder, track.facts.run_id)
    with rows_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(COLUMNS)
        for i in range(len(track.frame)):
            writer.writerow(
                (
                    int(track.frame[i]),
                    _number(track.t_frame[i]),
                    _number(track.t[i]),
                    _number(track.xy[i, 0]),
                    _number(track.xy[i, 1]),
                    int(track.duplicate[i]),
                    int(track.led[i]),
                )
            )
    facts = {"format": TRACK_FORMAT, "version": TRACK_VERSION, **asdict(track.facts)}
    facts_path.write_text(json.dumps(facts, indent=1) + "\n", encoding="utf-8")
    return rows_path, facts_path


def _float(text: str, *, empty: bool) -> float:
    if text == "" and empty:
        return math.nan
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(text)
    return value


def _flag(text: str) -> bool:
    if text not in ("0", "1"):
        raise ValueError(text)
    return text == "1"


def read_track(rows_path: str | Path) -> Track:
    """Read `<run id>.track.csv` and the facts file beside it."""
    rows_path = Path(rows_path)
    if not rows_path.name.endswith(".track.csv"):
        raise TrackError(f"{rows_path.name}: a track file name ends with .track.csv.")
    run_id = rows_path.name.removesuffix(".track.csv")
    facts_path = track_paths(rows_path.parent, run_id)[1]
    try:
        raw = json.loads(facts_path.read_text(encoding="utf-8"))
        if raw.pop("format") != TRACK_FORMAT or raw.pop("version") != TRACK_VERSION:
            raise TrackError(f"{facts_path.name}: not a {TRACK_FORMAT} file of version {TRACK_VERSION}.")
        fields: dict[str, Any] = {
            **raw,
            "frame_times": FrameTimes(raw["frame_times"]),
            "readout_direction": ReadoutDirection(raw["readout_direction"]),
        }
        facts = TrackFacts(**fields)
    except TrackError:
        raise
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise TrackError(f"{facts_path.name}: {e}") from e
    if facts.run_id != run_id:
        raise TrackError(f"{facts_path.name} names run {facts.run_id}.")
    with rows_path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        if tuple(next(reader, ())) != COLUMNS:
            raise TrackError(f"{rows_path.name}: the header must be {','.join(COLUMNS)}.")
        rows = list(reader)
    try:
        if any(len(row) != len(COLUMNS) for row in rows):
            raise ValueError("a row has the wrong number of fields")
        frame = np.array([int(r[0]) for r in rows], dtype=np.int64)
        t_frame = np.array([_float(r[1], empty=False) for r in rows], dtype=float)
        t = np.array([_float(r[2], empty=True) for r in rows], dtype=float)
        xy = np.array([[_float(r[3], empty=True), _float(r[4], empty=True)] for r in rows], dtype=float)
        duplicate = np.array([_flag(r[5]) for r in rows], dtype=bool)
        led = np.array([_flag(r[6]) for r in rows], dtype=bool)
    except ValueError as e:
        raise TrackError(f"{rows_path.name}: {e}") from e
    return Track(facts, frame, t_frame, t, xy.reshape(-1, 2), duplicate, led)
