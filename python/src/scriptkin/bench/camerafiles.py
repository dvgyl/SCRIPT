"""The camera files of the bench (protocol section 7): the lens, the plane, the calibration of a mounting,
the frame clock, the row readout, and the `ffprobe` frames file of a clip.

Each camera step writes one JSON file with a format name and a version, and a later step reads it. A file
also lists the SHA-256 of each input file of its step. This module does not import OpenCV, so a reader
of the files does not need the `camera` extra.

Pixel coordinates are OpenCV coordinates of the decoded frame: x to the right, y down, and the centre of
the top-left pixel at (0, 0). Plane coordinates are millimetres in the marker plane. The x axis is the
board x axis, which is the device x axis, and y is up. The origin is the board origin on the marked grid
point.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol, Self

import numpy as np
from numpy.typing import NDArray

from scriptkin.bench.track import FrameTimes
from scriptkin.formats import FormatError
from scriptkin.recording import FloatArray

Frame = NDArray[np.uint8]
"""A decoded frame or still image: (height, width) grey, or (height, width, 3) BGR."""
Point = tuple[float, float]

VERSION = 1
MIN_LENS_VIEWS = 20
MAX_PLANE_RESIDUAL_MM = 0.03
CLOCK_TOLERANCE = 0.001
"""The largest relative difference that protocol section 7.3 allows between two clocks."""
MIN_CLOCK_PERIODS = 59
"""A 60 s clip of the 1 Hz LED holds at least 59 whole periods between its first and last rising edge."""
_DISTORTION_LENGTHS = (4, 5, 8, 12, 14)


class CameraFileError(FormatError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


def _array(value: Any, shape: tuple[int, ...] | None = None) -> FloatArray:
    a = np.array(value, dtype=np.float64)
    if (shape is not None and a.shape != shape) or not np.isfinite(a).all():
        raise CameraFileError(f"Expected finite numbers of shape {shape}, not {a.shape}.")
    a.setflags(write=False)
    return a


@dataclass(frozen=True)
class Region:
    """A pixel rectangle of the decoded frame."""

    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.left < 0 or self.top < 0 or self.width < 1 or self.height < 1:
            raise CameraFileError("A region has a non-negative corner and a positive size.")

    def fits(self, image_size: tuple[int, int]) -> bool:
        return self.left + self.width <= image_size[0] and self.top + self.height <= image_size[1]

    def crop(self, frame: Frame) -> Frame:
        return frame[self.top : self.top + self.height, self.left : self.left + self.width]

    def to_json(self) -> dict[str, Any]:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Self:
        return cls(int(raw["left"]), int(raw["top"]), int(raw["width"]), int(raw["height"]))


@dataclass(frozen=True)
class Lens:
    """The intrinsic calibration of the camera in one video mode."""

    FORMAT: ClassVar[str] = "script-bench-lens"
    image_size: tuple[int, int]
    """The width and the height of the frames, in pixels."""
    matrix: FloatArray
    distortion: FloatArray
    """The OpenCV distortion coefficients: k1, k2, p1, p2, k3, and any further terms."""
    reprojection_rms_px: float
    views: int

    def __post_init__(self) -> None:
        m = self.matrix
        if (
            self.image_size[0] < 1
            or self.image_size[1] < 1
            or m.shape != (3, 3)
            or not np.isfinite(m).all()
            or m[0, 0] <= 0
            or m[1, 1] <= 0
            or not np.array_equal(m[2], [0.0, 0.0, 1.0])
        ):
            raise CameraFileError(
                "A lens has a positive image size and a camera matrix with positive focals."
            )
        if self.distortion.shape not in [(n,) for n in _DISTORTION_LENGTHS]:
            raise CameraFileError(f"A lens has {_DISTORTION_LENGTHS} distortion coefficients.")
        if not _finite(self.reprojection_rms_px) or self.reprojection_rms_px < 0:
            raise CameraFileError("The reprojection error must be finite and not negative.")
        if self.views < MIN_LENS_VIEWS:
            raise CameraFileError(f"An intrinsic calibration needs {MIN_LENS_VIEWS} or more views.")

    def to_json(self) -> dict[str, Any]:
        return {
            "image_size": list(self.image_size),
            "matrix": self.matrix.tolist(),
            "distortion": self.distortion.tolist(),
            "reprojection_rms_px": self.reprojection_rms_px,
            "views": self.views,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Self:
        width, height = raw["image_size"]
        return cls(
            (int(width), int(height)),
            _array(raw["matrix"], (3, 3)),
            _array(raw["distortion"]),
            float(raw["reprojection_rms_px"]),
            int(raw["views"]),
        )


@dataclass(frozen=True)
class Plane:
    """The plane calibration of one mounting: a homography from undistorted pixels to plane millimetres.

    A plane exists only when its residual is `MAX_PLANE_RESIDUAL_MM` or less (protocol section 7.2).
    """

    FORMAT: ClassVar[str] = "script-bench-plane"
    lens: Lens
    homography: FloatArray
    residual_rms_mm: float
    pattern: tuple[int, int]
    """The inner corners of the board along x and along y."""
    pitch_mm: float
    """The measured pitch of the board."""

    def __post_init__(self) -> None:
        h = self.homography
        if h.shape != (3, 3) or not np.isfinite(h).all() or abs(float(np.linalg.det(h))) < 1e-300:
            raise CameraFileError("A homography is a finite, invertible 3 x 3 matrix.")
        if not _finite(self.residual_rms_mm) or not 0 <= self.residual_rms_mm <= MAX_PLANE_RESIDUAL_MM:
            raise CameraFileError(f"A plane residual is {MAX_PLANE_RESIDUAL_MM} mm or less.")
        cols, rows = self.pattern
        if cols < 2 or rows < 2 or cols == rows:
            raise CameraFileError(
                "A board has 2 or more inner corners on each axis and a different count on each."
            )
        if not _finite(self.pitch_mm) or self.pitch_mm <= 0:
            raise CameraFileError("The board pitch must be positive.")

    def to_json(self) -> dict[str, Any]:
        return {
            "lens": self.lens.to_json(),
            "homography": self.homography.tolist(),
            "residual_rms_mm": self.residual_rms_mm,
            "pattern": list(self.pattern),
            "pitch_mm": self.pitch_mm,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Self:
        cols, rows = raw["pattern"]
        return cls(
            Lens.from_json(raw["lens"]),
            _array(raw["homography"], (3, 3)),
            float(raw["residual_rms_mm"]),
            (int(cols), int(rows)),
            float(raw["pitch_mm"]),
        )


@dataclass(frozen=True)
class Calibration:
    """The calibration of one mounting, which a track names by its SHA-256: the plane with its lens, the
    marker offset, and the place of the clock LED in the frame."""

    FORMAT: ClassVar[str] = "script-bench-calibration"
    plane: Plane
    marker_offset_mm: Point
    """The marker position minus the stylus tip position, in plane millimetres."""
    led: Region

    def __post_init__(self) -> None:
        if not _finite(*self.marker_offset_mm):
            raise CameraFileError("The marker offset must be finite.")
        if not self.led.fits(self.plane.lens.image_size):
            raise CameraFileError("The LED region must lie inside the frame.")

    def to_json(self) -> dict[str, Any]:
        return {
            "plane": self.plane.to_json(),
            "marker_offset_mm": list(self.marker_offset_mm),
            "led": self.led.to_json(),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Self:
        x, y = raw["marker_offset_mm"]
        return cls(Plane.from_json(raw["plane"]), (float(x), float(y)), Region.from_json(raw["led"]))


@dataclass(frozen=True)
class Clock:
    """The frame clock of the camera mode, from a clip of the crystal-timed LED (protocol section 7.3)."""

    FORMAT: ClassVar[str] = "script-bench-clock"
    frame_rate_hz: float
    """The captured frames per LED second."""
    frame_times: FrameTimes
    timestamp_ratio: float
    """The presentation-time span of the LED periods divided by their LED time."""
    led_periods: int
    led_hz: float
    nominal_hz: float

    def __post_init__(self) -> None:
        if (
            not _finite(self.frame_rate_hz, self.timestamp_ratio, self.led_hz, self.nominal_hz)
            or min(self.frame_rate_hz, self.timestamp_ratio, self.led_hz, self.nominal_hz) <= 0
        ):
            raise CameraFileError("The rates and the timestamp ratio must be positive.")
        if abs(self.frame_rate_hz / self.nominal_hz - 1) > CLOCK_TOLERANCE:
            raise CameraFileError(
                f"The measured rate must be within {CLOCK_TOLERANCE:.1%} of the nominal rate."
            )
        capture = abs(self.timestamp_ratio - 1) <= CLOCK_TOLERANCE
        if (self.frame_times is FrameTimes.CAPTURE) != capture:
            raise CameraFileError("The timestamps are capture times exactly when they agree with the LED.")
        if self.led_periods < MIN_CLOCK_PERIODS:
            raise CameraFileError(f"A frame clock needs {MIN_CLOCK_PERIODS} or more LED periods.")

    def to_json(self) -> dict[str, Any]:
        return {
            "frame_rate_hz": self.frame_rate_hz,
            "frame_times": self.frame_times.value,
            "timestamp_ratio": self.timestamp_ratio,
            "led_periods": self.led_periods,
            "led_hz": self.led_hz,
            "nominal_hz": self.nominal_hz,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Self:
        return cls(
            float(raw["frame_rate_hz"]),
            FrameTimes(raw["frame_times"]),
            float(raw["timestamp_ratio"]),
            int(raw["led_periods"]),
            float(raw["led_hz"]),
            float(raw["nominal_hz"]),
        )


@dataclass(frozen=True)
class Readout:
    """The row readout of a rolling shutter, from a clip of the 1 kHz LED (protocol section 7.3)."""

    FORMAT: ClassVar[str] = "script-bench-readout"
    row_time_s: float
    band_pitch_rows: float
    led_hz: float
    frames: int
    """The frames that showed at least two band edges of one kind."""

    def __post_init__(self) -> None:
        if (
            not _finite(self.row_time_s, self.band_pitch_rows, self.led_hz)
            or min(self.row_time_s, self.band_pitch_rows, self.led_hz) <= 0
        ):
            raise CameraFileError("The row time, the band pitch, and the LED rate must be positive.")
        if abs(self.row_time_s * self.band_pitch_rows * self.led_hz - 1) > 1e-9:
            raise CameraFileError("The band pitch times the row time must equal the LED period.")
        if self.frames < 1:
            raise CameraFileError("A readout comes from one or more frames.")

    def to_json(self) -> dict[str, Any]:
        return {
            "row_time_s": self.row_time_s,
            "band_pitch_rows": self.band_pitch_rows,
            "led_hz": self.led_hz,
            "frames": self.frames,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Self:
        return cls(
            float(raw["row_time_s"]), float(raw["band_pitch_rows"]), float(raw["led_hz"]), int(raw["frames"])
        )


class CameraFile(Protocol):
    FORMAT: ClassVar[str]

    def to_json(self) -> dict[str, Any]: ...

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Self: ...


def write_camera_file(value: CameraFile, path: str | Path, sources: Iterable[str | Path] = ()) -> Path:
    """Write one camera file. `sources` are the input files of the step, named with their SHA-256."""
    path = Path(path)
    payload = {
        "format": value.FORMAT,
        "version": VERSION,
        **value.to_json(),
        "sources": [{"name": Path(s).name, "sha256": sha256_file(s)} for s in sources],
    }
    path.write_text(json.dumps(payload, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    return path


def read_camera_file[T: CameraFile](kind: type[T], path: str | Path) -> T:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("format") != kind.FORMAT or raw.get("version") != VERSION:
            raise CameraFileError(f"{path.name}: not a {kind.FORMAT} file of version {VERSION}.")
        return kind.from_json(raw)
    except CameraFileError:
        raise
    except (OSError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise CameraFileError(f"{path.name}: {e}") from e


def frames_path(clip: str | Path) -> Path:
    """The `ffprobe` frames file beside a clip: `<clip name without its suffix>.frames.json`."""
    clip = Path(clip)
    return clip.with_name(f"{clip.stem}.frames.json")


def read_frame_times(path: str | Path) -> FloatArray:
    """The presentation time of each video frame of an `ffprobe -show_frames -of json` file, in file order.

    The file may also list audio frames. It must list the frames of one video stream only, each with its
    `pts_time`.
    """
    path = Path(path)
    try:
        frames = json.loads(path.read_text(encoding="utf-8"))["frames"]
        video = [f for f in frames if f["media_type"] == "video"]
        streams = {f["stream_index"] for f in video}
        if len(streams) != 1:
            raise CameraFileError(
                f"{path.name}: the file must list frames of one video stream, not {len(streams)}."
            )
        pts = np.array([float(f["pts_time"]) for f in video], dtype=np.float64)
    except CameraFileError:
        raise
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise CameraFileError(f"{path.name}: {e}") from e
    if not np.isfinite(pts).all():
        raise CameraFileError(f"{path.name}: every presentation time must be finite.")
    return pts
