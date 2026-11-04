"""The marker offset and the tracker of protocol sections 7.2 and 7.3.

Every frame searches a square in the marker plane. Until the tracker first finds the marker, the square
has the side `FIRST_SEARCH_MM` and its centre is the commanded start point plus the marker offset. After
that, the square has the side `SEARCH_MM` and its centre is the last marker position that the tracker
found. The protocol names the 10 mm square for the first frame only. The tracker uses it in every later
frame, so that a partly cut marker never gives a centroid.

The first square is larger than protocol section 7.3 says. The clip starts with the stylus at the lift
height (section 5.2), and the camera sees a raised marker along its ray. So the marker appears moved away
from the nadir by X z / (D - z): X is its distance from the nadir, z the height, and D the camera height.
A 10 mm square holds the marker only when that shift is about 3.4 mm or less on each axis. On the test
camera, 144 mm above the plane, a lift of 17 mm shifts the marker of the line start point by 9.4 mm
along x. At that lift, the 24 mm square holds a shift of 10.3 mm on each axis, or 14.5 mm along a
diagonal.

Inside the square, Otsu thresholding separates the bright pixels. A bright region counts only when it
lies completely inside the square and its area is at least half the area of the 3 mm marker dot. The two
Otsu classes must differ by `MARKER_MIN_CONTRAST` grey levels. A frame without such a region has no
marker, and the tracker records NaN. The marker is the largest region that counts, and its position is
the intensity-weighted centroid of its pixels.

The row of a centroid is its raw image row, before the distortion is removed, because the sensor reads
raw rows. The time of the centroid is `t_frame + row * row_time` when the sensor reads row 0 first, and
`t_frame + (height - 1 - row) * row_time` when it reads the last row first (protocol section 7.3). So
`t_frame` is the time of the first row that the sensor reads.

The tracker reports observations only. It keeps every decoded frame, in order, and the analysis applies
the validity rules.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

import cv2
import numpy as np

from scriptkin.bench.calibration import grey, to_image, to_plane
from scriptkin.bench.camerafiles import (
    Calibration,
    CameraFileError,
    Clock,
    Frame,
    Plane,
    Point,
    Readout,
    Region,
    frames_path,
    read_camera_file,
    read_frame_times,
    sha256_file,
)
from scriptkin.bench.clock import led_level, led_states
from scriptkin.bench.track import FrameTimes, ReadoutDirection, Track, TrackFacts, write_track
from scriptkin.quality import Refusal, RefusalCode
from scriptkin.recording import FloatArray

TRACKER_VERSION = "2"
SEARCH_MM = 10.0
FIRST_SEARCH_MM = 24.0
MARKER_DIAMETER_MM = 3.0
MARKER_MIN_CONTRAST = 32.0
_MIN_AREA_SHARE = 0.5
_EDGE_POINTS = 8
_BORDER_KERNEL = np.ones((3, 3), np.uint8)


def search_outline(centre_mm: Point | FloatArray, plane: Plane, side_mm: float = SEARCH_MM) -> FloatArray:
    """The outline of the search square around a plane point, in raw pixels, with points along each edge
    so that the outline follows the lens distortion."""
    h = side_mm / 2
    s = np.linspace(-h, h, _EDGE_POINTS + 1)[:-1]
    edges = np.concatenate(
        (
            np.column_stack((s, np.full_like(s, -h))),
            np.column_stack((np.full_like(s, h), s)),
            np.column_stack((-s, np.full_like(s, h))),
            np.column_stack((np.full_like(s, -h), -s)),
        )
    )
    return to_image(edges + np.asarray(centre_mm, dtype=np.float64), plane)


def _polygon_area(outline: FloatArray) -> float:
    x, y = outline[:, 0], outline[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


def find_marker(frame: Frame, outline_px: FloatArray, side_mm: float = SEARCH_MM) -> Point | None:
    """The raw pixel position of the marker inside the outline of a search square of `side_mm`, or None."""
    height, width = frame.shape[:2]
    left, top = np.floor(outline_px.min(axis=0)).astype(int)
    right, bottom = np.ceil(outline_px.max(axis=0)).astype(int) + 1
    left, top, right, bottom = max(left, 0), max(top, 0), min(right, width), min(bottom, height)
    if right - left < 3 or bottom - top < 3:
        return None
    window = grey(frame[top:bottom, left:right])
    mask = np.zeros(window.shape, np.uint8)
    cv2.fillPoly(mask, [np.rint(outline_px - (left, top)).astype(np.int32)], 1)
    inside = mask.astype(bool)
    values = window[inside]
    threshold = float(cv2.threshold(values.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[0])
    bright = (window > threshold) & inside
    low, high = window[inside & ~bright], window[bright]
    if low.size == 0 or high.size == 0 or float(high.mean()) - float(low.mean()) < MARKER_MIN_CONTRAST:
        return None
    count, labels, stats, _ = cv2.connectedComponentsWithStats(bright.astype(np.uint8), connectivity=8)
    eroded = cv2.erode(mask, _BORDER_KERNEL, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    border = inside & ~eroded.astype(bool)
    touching = set(np.unique(labels[border & bright]).tolist())
    dot_area_mm2 = np.pi * (MARKER_DIAMETER_MM / 2) ** 2
    min_area = _MIN_AREA_SHARE * dot_area_mm2 * _polygon_area(outline_px) / side_mm**2
    areas = stats[:, cv2.CC_STAT_AREA]
    counted = [k for k in range(1, count) if k not in touching and areas[k] >= min_area]
    if not counted:
        return None
    marker = max(counted, key=lambda k: int(areas[k]))
    rows, cols = np.nonzero(labels == marker)
    weights = window[rows, cols].astype(np.float64)
    return float(left + np.average(cols, weights=weights)), float(top + np.average(rows, weights=weights))


def measure_marker_offset(image: Frame, plane: Plane, near_mm: Point, led: Region) -> Calibration | Refusal:
    """The calibration of a mounting, from a still image with the stylus tip on the marked grid point.

    The tip is at the plane origin, so the offset is the marker position. `near_mm` is the operator's
    estimate of the marker position. The marker must lie completely inside the search square around it.
    """
    if (image.shape[1], image.shape[0]) != plane.lens.image_size:
        return Refusal(RefusalCode.OUT_OF_RANGE, "The still image size differs from the lens image size.")
    if not led.fits(plane.lens.image_size):
        return Refusal(RefusalCode.OUT_OF_RANGE, "The LED region lies outside the frame.")
    found = find_marker(image, search_outline(near_mm, plane))
    if found is None:
        return Refusal(
            RefusalCode.UNDEFINED, f"No marker lies completely inside the {SEARCH_MM:g} mm square."
        )
    x, y = to_plane(np.array([found]), plane)[0]
    return Calibration(plane, (float(x), float(y)), led)


def frame_times(clock: Clock, pts_s: FloatArray) -> FloatArray:
    """The capture time of each frame from the first frame: the presentation times when they are capture
    times, else the frame index divided by the measured rate (protocol section 7.3)."""
    if clock.frame_times is FrameTimes.CAPTURE:
        return pts_s - pts_s[0]
    return np.arange(len(pts_s)) / clock.frame_rate_hz


def _rows_read_before(row: FloatArray, height: int, direction: ReadoutDirection) -> FloatArray:
    return row if direction is ReadoutDirection.TOP_FIRST else height - 1 - row


def track_frames(
    frames: Iterable[Frame], t_frame: FloatArray, calibration: Calibration, start_mm: Point, facts: TrackFacts
) -> Track:
    """Track the marker in every frame. `start_mm` is the commanded start point of the stylus tip, in plane
    millimetres. The row correction uses `facts.row_time_s` and `facts.readout_direction`."""
    plane = calibration.plane
    offset = np.array(calibration.marker_offset_mm)
    centre = np.array(start_mm, dtype=np.float64) + offset
    side = FIRST_SEARCH_MM
    n = len(t_frame)
    row = np.full(n, np.nan)
    marker = np.full((n, 2), np.nan)
    duplicate = np.zeros(n, dtype=bool)
    levels = np.zeros(n)
    previous: Frame | None = None
    decoded = 0
    for k, frame in enumerate(frames):
        if k >= n:
            raise ValueError("The clip has more frames than its frames file.")
        if (frame.shape[1], frame.shape[0]) != plane.lens.image_size:
            raise ValueError(f"Frame {k} has another size than the lens calibration.")
        duplicate[k] = previous is not None and np.array_equal(frame, previous)
        previous = frame.copy()
        levels[k] = led_level(frame, calibration.led)
        found = find_marker(frame, search_outline(centre, plane, side), side)
        if found is not None:
            row[k] = found[1]
            marker[k] = centre = to_plane(np.array([found]), plane)[0]
            side = SEARCH_MM
        decoded = k + 1
    if decoded != n:
        raise ValueError(f"The clip has {decoded} frames and its frames file lists {n}.")
    rows_before = _rows_read_before(row, plane.lens.image_size[1], facts.readout_direction)
    return Track(
        facts,
        np.arange(n, dtype=np.int64),
        t_frame,
        t_frame + rows_before * facts.row_time_s,
        marker - offset,
        duplicate,
        led_states(levels),
    )


def read_video(path: str | Path) -> Iterator[Frame]:
    """The decoded frames of a clip, in order, through the FFmpeg backend of OpenCV."""
    capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise CameraFileError(f"{Path(path).name}: OpenCV cannot open the clip.")
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                return
            yield np.asarray(frame, dtype=np.uint8)
    finally:
        capture.release()


def track_clip(
    clip: str | Path,
    *,
    calibration: str | Path,
    clock: str | Path,
    readout: str | Path | None,
    start_mm: Point,
    out_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    """Track `<run id>.<suffix>` with its frames file beside it, and write the track files.

    `readout` is None for a global shutter. The track files go beside the clip unless `out_dir` names a
    folder.
    """
    clip = Path(clip)
    frames_file = frames_path(clip)
    mounting = read_camera_file(Calibration, calibration)
    camera_clock = read_camera_file(Clock, clock)
    shutter = None if readout is None else read_camera_file(Readout, readout)
    pts = read_frame_times(frames_file)
    facts = TrackFacts(
        run_id=clip.stem,
        video_sha256=sha256_file(clip),
        frames_sha256=sha256_file(frames_file),
        calibration_sha256=sha256_file(calibration),
        frame_times=camera_clock.frame_times,
        frame_rate_hz=camera_clock.frame_rate_hz,
        row_time_s=0.0 if shutter is None else shutter.row_time_s,
        tracker_version=TRACKER_VERSION,
        readout_direction=ReadoutDirection.TOP_FIRST if shutter is None else shutter.direction,
    )
    track = track_frames(read_video(clip), frame_times(camera_clock, pts), mounting, start_mm, facts)
    return write_track(track, clip.parent if out_dir is None else out_dir)
