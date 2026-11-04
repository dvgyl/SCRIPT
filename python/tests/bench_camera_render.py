"""A synthetic camera for the bench camera tests (protocol section 7.5 step 5).

The camera is a pinhole with a known lens distortion and a known pose above the marker plane. Each raw
pixel knows the plane point that it sees, so the renderer draws the marker, the board, and the
backgrounds as functions of plane millimetres. A rolling shutter exposes row r of frame k at
`k / rate + r * row_time`, and the renderer moves the marker to that time on every row.

Run this file to write the fixture clip of `fixtures/bench_camera/`:

    python -I python/tests/bench_camera_render.py python/tests/fixtures/bench_camera
"""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from scriptkin.bench.calibration import calibrate_lens, calibrate_plane
from scriptkin.bench.camera import MIN_LENS_VIEWS, Calibration, Frame, Lens, Plane, Region
from scriptkin.bench.tracker import measure_marker_offset
from scriptkin.quality import Refusal
from scriptkin.recording import FloatArray

Background = Literal["ipad", "wacom"]
Motion = Callable[[FloatArray], FloatArray]
"""The stylus tip position in plane millimetres, as (n, 2), at each of n times."""

RATE_HZ = 239.987
ROW_TIME_S = 3.5e-6
DOT_RADIUS_MM = 1.5
FIELD_RADIUS_MM = 6.0
MARKER_OFFSET_MM = (-8.3, 0.6)
LED = Region(26, 26, 28, 28)
FIXTURE_LED = Region(168, 118, 24, 24)
LED_PHASE_S = 0.37
_DOT, _FIELD = 235.0, 18.0
_LED_ON, _LED_OFF = 250, 22


def _rotation(vector: tuple[float, float, float]) -> FloatArray:
    return np.asarray(cv2.Rodrigues(np.array(vector, dtype=np.float64))[0], dtype=np.float64)


_DOWN = np.diag([1.0, -1.0, -1.0])
"""The camera looks down: plane x is image x, plane y is image up."""


@dataclass(frozen=True, eq=False)
class Camera:
    size: tuple[int, int]
    matrix: FloatArray
    distortion: FloatArray
    rotation: FloatArray
    """World to camera."""
    centre: FloatArray
    """The camera centre in plane millimetres, with z the height above the marker plane."""

    def project(self, points_mm: FloatArray) -> FloatArray:
        """Plane millimetres to raw pixels."""
        world = np.column_stack(
            (np.asarray(points_mm, dtype=np.float64).reshape(-1, 2), np.zeros(len(points_mm)))
        )
        rvec = cv2.Rodrigues(self.rotation)[0]
        raw, _ = cv2.projectPoints(world, rvec, -self.rotation @ self.centre, self.matrix, self.distortion)
        return np.asarray(raw, dtype=np.float64).reshape(-1, 2)

    @cached_property
    def rays(self) -> FloatArray:
        """The undistorted normalised ray of each raw pixel, as (height, width, 2)."""
        w, h = self.size
        v, u = np.mgrid[0:h, 0:w].astype(np.float64)
        points = np.column_stack((u.ravel(), v.ravel())).reshape(-1, 1, 2)
        criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-12)
        ideal = cv2.undistortPoints(points, self.matrix, self.distortion, criteria=criteria)
        return np.asarray(ideal, dtype=np.float64).reshape(h, w, 2)

    @cached_property
    def world_rays(self) -> FloatArray:
        """The direction of each raw pixel's ray in the world, as (height, width, 3)."""
        r = self.rays
        camera = np.concatenate((r, np.ones((*r.shape[:2], 1))), axis=2)
        return camera @ self.rotation

    @cached_property
    def plane_xy(self) -> FloatArray:
        """The marker plane point that each raw pixel sees, as (height, width, 2)."""
        d = self.world_rays
        depth = -self.centre[2] / d[..., 2]
        return self.centre[:2] + depth[..., None] * d[..., :2]

    def lens(self) -> Lens:
        return Lens(self.size, self.matrix, self.distortion, 0.0, MIN_LENS_VIEWS)

    def homography(self) -> FloatArray:
        """Undistorted pixels to plane millimetres."""
        t = -self.rotation @ self.centre
        to_image = self.matrix @ np.column_stack((self.rotation[:, 0], self.rotation[:, 1], t))
        h = np.linalg.inv(to_image)
        return h / h[2, 2]

    def plane(self, pattern: tuple[int, int] = (9, 6), pitch_mm: float = 10.0) -> Plane:
        return Plane(self.lens(), self.homography(), 0.0, pattern, pitch_mm)

    def calibration(
        self, offset_mm: tuple[float, float] = MARKER_OFFSET_MM, led: Region = LED
    ) -> Calibration:
        return Calibration(self.plane(), offset_mm, led)


@functools.cache
def main_camera(turned: bool = False) -> Camera:
    """A 1920 x 1080 camera 144 mm above the plane: about 0.1152 mm per pixel. `turned` turns it a half
    turn about its axis, so that device +x points left in the image."""
    tilt = _rotation((0.017, -0.014, 0.010)) @ _DOWN
    if turned:
        tilt = _rotation((0.0, 0.0, np.pi)) @ tilt
    return Camera(
        (1920, 1080),
        np.array([[1250.0, 0.0, 959.5], [0.0, 1250.0, 539.5], [0.0, 0.0, 1.0]]),
        np.array([0.06, -0.04, 0.0005, -0.0003, 0.0]),
        tilt,
        np.array([50.0, 40.0, 144.0]),
    )


@functools.cache
def fixture_camera() -> Camera:
    """A small camera without distortion for the fixture clip: 0.115 mm per pixel."""
    return Camera(
        (200, 150),
        np.array([[1000.0, 0.0, 99.5], [0.0, 1000.0, 74.5], [0.0, 0.0, 1.0]]),
        np.zeros(5),
        _DOWN,
        np.array([0.0, 0.0, 115.0]),
    )


def _background(kind: Background, xy: FloatArray) -> FloatArray:
    x, y = xy[..., 0], xy[..., 1]
    if kind == "ipad":
        return 200.0 + 8.0 * np.sin(2 * np.pi * x / 41.0) * np.cos(2 * np.pi * y / 53.0)
    waves = ((0.8, 0.6, 0.73, 0.1), (-0.5, 0.87, 1.31, 1.7), (0.97, -0.26, 2.9, 2.3))
    texture = np.zeros_like(x)
    for cx, cy, period, phase in waves:
        texture += np.sin(2 * np.pi * (x * cx + y * cy) / period + phase)
    return 58.0 + 2.5 * texture


def line(start_mm: tuple[float, float], direction: tuple[float, float], speed: float) -> tuple[Motion, float]:
    """A 100 mm line with a 0.4 s hold, a 0.2 s ramp, the cruise, a 0.2 s ramp, and a 0.4 s hold.
    Returns the motion and its duration."""
    hold, ramp, length = 0.4, 0.2, 100.0
    cruise = (length - speed * ramp) / speed
    total = 2 * hold + 2 * ramp + cruise
    a = speed / ramp

    def motion(t: FloatArray) -> FloatArray:
        u = np.clip(np.asarray(t, dtype=np.float64) - hold, 0.0, 2 * ramp + cruise)
        w = u - ramp - cruise
        s = np.where(
            u < ramp,
            a * u**2 / 2,
            np.where(
                u <= ramp + cruise,
                speed * ramp / 2 + speed * (u - ramp),
                length - speed * ramp / 2 + speed * w - a * w**2 / 2,
            ),
        )
        s = np.where(u >= 2 * ramp + cruise, length, s)
        return np.asarray(start_mm) + s[:, None] * np.asarray(direction)

    return motion, total


def sampled(times: FloatArray, xy: FloatArray, *, hold_s: float = 0.3) -> tuple[Motion, float]:
    """A motion through dense samples, with a hold before and after. Returns the motion and its duration."""

    def motion(t: FloatArray) -> FloatArray:
        u = np.clip(np.asarray(t, dtype=np.float64) - hold_s, times[0], times[-1])
        return np.column_stack((np.interp(u, times, xy[:, 0]), np.interp(u, times, xy[:, 1])))

    return motion, float(times[-1] - times[0]) + 2 * hold_s


def still(point: tuple[float, float]) -> Motion:
    return lambda t: np.tile(np.asarray(point, dtype=np.float64), (len(np.atleast_1d(t)), 1))


def led_on(t: FloatArray) -> np.ndarray:
    return ((np.asarray(t) - LED_PHASE_S) % 1.0) < 0.5


@functools.cache
def _bank(camera: Camera, background: Background, noise: float, seed: int) -> tuple[Frame, ...]:
    """Four frames of the background with independent sensor noise, so that no two neighbouring frames
    are equal in every pixel."""
    rng = np.random.default_rng(seed)
    base = _background(background, camera.plane_xy)
    noisy = [base + rng.normal(0.0, noise, base.shape) if noise else base for _ in range(4)]
    return tuple(np.clip(np.rint(n), 0, 255).astype(np.uint8) for n in noisy)


@dataclass
class Scene:
    camera: Camera
    background: Background
    row_time_s: float = ROW_TIME_S
    rate_hz: float = RATE_HZ
    offset_mm: tuple[float, float] = MARKER_OFFSET_MM
    noise: float = 2.0
    seed: int = 0
    led: Region = LED

    def _marker(self, frame: Frame, t0: float, motion: Motion, dot: bool, rng: np.random.Generator) -> None:
        w, h = self.camera.size
        centre = self.camera.project(motion(np.array([t0])) + self.offset_mm)[0]
        half = int(FIELD_RADIUS_MM / 0.1 + 8)
        c0, c1 = max(int(centre[0]) - half, 0), min(int(centre[0]) + half, w)
        r0, r1 = max(int(centre[1]) - half, 0), min(int(centre[1]) + half, h)
        if c1 <= c0 or r1 <= r0:
            return
        q = self.camera.plane_xy[r0:r1, c0:c1]
        pixel = float(np.linalg.norm(q[q.shape[0] // 2, 1] - q[q.shape[0] // 2, 0]))
        rows = np.arange(r0, r1, dtype=np.float64)
        marker = motion(t0 + rows * self.row_time_s) + self.offset_mm
        d = np.linalg.norm(q - marker[:, None, :], axis=2)
        field_cover = np.clip(0.5 - (d - FIELD_RADIUS_MM) / pixel, 0.0, 1.0)
        dot_cover = np.clip(0.5 - (d - DOT_RADIUS_MM) / pixel, 0.0, 1.0) if dot else 0.0
        level = _background(self.background, q) * (1 - field_cover) + _FIELD * field_cover
        level = level * (1 - dot_cover) + _DOT * dot_cover
        if self.noise:
            level = level + rng.normal(0.0, self.noise, level.shape)
        frame[r0:r1, c0:c1] = np.clip(np.rint(level), 0, 255).astype(np.uint8)

    def _led(self, frame: Frame, t0: float) -> None:
        r = self.led
        rows = np.arange(r.top, r.top + r.height, dtype=np.float64)
        on = led_on(t0 + rows * self.row_time_s)
        cy, cx = r.top + r.height / 2 - 0.5, r.left + r.width / 2 - 0.5
        yy, xx = np.mgrid[r.top : r.top + r.height, r.left : r.left + r.width]
        disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= (r.width / 2 - 2) ** 2
        patch = np.where(on[:, None], _LED_ON, _LED_OFF)
        frame[r.top : r.top + r.height, r.left : r.left + r.width] = np.where(disc, patch, 0)

    def frames(
        self,
        motion: Motion,
        count: int,
        *,
        missing: frozenset[int] = frozenset(),
        duplicates: frozenset[int] = frozenset(),
    ) -> Iterator[Frame]:
        """`count` frames of the marker on `motion`. A frame in `missing` has the black field without the
        dot. A frame in `duplicates` repeats the previous frame in every pixel."""
        rng = np.random.default_rng(self.seed + 1)
        bank = _bank(self.camera, self.background, self.noise, self.seed)
        previous: Frame | None = None
        for k in range(count):
            if k in duplicates and previous is not None:
                yield previous
                continue
            frame = bank[k % len(bank)].copy()
            t0 = k / self.rate_hz
            self._marker(frame, t0, motion, k not in missing, rng)
            self._led(frame, t0)
            previous = frame
            yield frame


def _wave(u: FloatArray, half_width: FloatArray) -> FloatArray:
    """+1 and -1 on alternate unit squares, with a linear edge one pixel wide."""
    distance = 0.5 - np.abs(u - np.floor(u) - 0.5)
    sign = np.where(np.floor(u) % 2 == 0, 1.0, -1.0)
    return sign * np.clip(distance / half_width, 0.0, 1.0)


def board(
    camera: Camera,
    pose: tuple[FloatArray, FloatArray] | None = None,
    *,
    pattern: tuple[int, int] = (9, 6),
    pitch_mm: float = 10.0,
    warp_mm: float = 0.0,
    noise: float = 2.0,
    seed: int = 0,
) -> Frame:
    """A checkerboard with a white margin. With no pose, the board lies in the marker plane with its
    origin at the plane origin. A pose (rotation, translation) maps board millimetres to the world.
    `warp_mm` bends the printed pattern, so that its corners leave the regular grid."""
    if pose is None:
        xy = camera.plane_xy
        pixel = np.full(xy.shape[:2], camera.centre[2] / camera.matrix[0, 0])
    else:
        rotation, translation = pose
        d = camera.world_rays
        normal = rotation[:, 2]
        depth = (normal @ (translation - camera.centre)) / (d @ normal)
        world = camera.centre + depth[..., None] * d
        xy = ((world - translation) @ rotation)[..., :2]
        pixel = depth * np.linalg.norm(d, axis=2) / camera.matrix[0, 0]
    bx, by = xy[..., 0], xy[..., 1]
    if warp_mm:
        bx, by = bx + warp_mm * np.sin(2 * np.pi * by / 23.0), by + warp_mm * np.sin(2 * np.pi * bx / 29.0)
    cols, rows = pattern
    u, v = bx / pitch_mm, by / pitch_mm
    half = 0.5 * pixel / pitch_mm
    checker = 0.5 + 0.5 * _wave(u, half) * _wave(v, half)
    edge = np.minimum(np.minimum(u + 1, cols - u), np.minimum(v + 1, rows - v))
    inside = np.clip(0.5 + edge / (2 * half), 0.0, 1.0)
    paper = np.where((u > -1.8) & (u < cols + 0.8) & (v > -1.8) & (v < rows + 0.8), 1.0, 0.45)
    level = 25.0 + 200.0 * (inside * checker + (1 - inside) * paper)
    if noise:
        level = level + np.random.default_rng(seed).normal(0.0, noise, level.shape)
    return np.clip(np.rint(level), 0, 255).astype(np.uint8)


@functools.cache
def lens_views(camera: Camera, count: int = 24, *, seed: int = 7) -> list[Frame]:
    """`count` views of the board in poses that keep the whole board and its margin in the frame."""
    rng = np.random.default_rng(seed)
    w, h = camera.size
    outline = np.array([[-18.0, -18.0, 0.0], [98.0, -18.0, 0.0], [98.0, 68.0, 0.0], [-18.0, 68.0, 0.0]])
    views: list[Frame] = []
    while len(views) < count:
        rotation = _rotation((rng.uniform(-0.45, 0.45), rng.uniform(-0.45, 0.45), rng.uniform(-0.3, 0.3)))
        centre = np.array([rng.uniform(-40, 140), rng.uniform(-20, 100), rng.uniform(-90, 20)])
        translation = centre - rotation @ np.array([40.0, 25.0, 0.0])
        world = outline @ rotation.T + translation
        rvec = cv2.Rodrigues(camera.rotation)[0]
        raw, _ = cv2.projectPoints(
            world, rvec, -camera.rotation @ camera.centre, camera.matrix, camera.distortion
        )
        corners = np.asarray(raw).reshape(-1, 2)
        if corners.min() < 8 or (corners[:, 0] > w - 8).any() or (corners[:, 1] > h - 8).any():
            continue
        views.append(board(camera, (rotation, translation), seed=len(views)))
    return views


@functools.cache
def estimated_lens() -> Lens:
    """The lens that `calibrate_lens` finds from the views of the main camera."""
    lens = calibrate_lens(lens_views(main_camera()), (9, 6), 10.0)
    assert not isinstance(lens, Refusal), lens
    return lens


@functools.cache
def estimated_plane() -> Plane:
    plane = calibrate_plane(board(main_camera()), estimated_lens(), (9, 6), 10.0)
    assert not isinstance(plane, Refusal), plane
    return plane


def tip_still(background: Background) -> Frame:
    """A still image with the stylus tip on the marked grid point at the plane origin."""
    return next(Scene(main_camera(), background).frames(still((0.0, 0.0)), 1))


@functools.cache
def estimated_calibration(background: Background) -> Calibration:
    calibration = measure_marker_offset(tip_still(background), estimated_plane(), (-8.0, 0.0), LED)
    assert not isinstance(calibration, Refusal), calibration
    return calibration


FIXTURE_RUN = "synthetic-r1"
FIXTURE_FRAMES = 24
FIXTURE_DUPLICATE = 9
FIXTURE_MISSING = 15
FIXTURE_LED_FRAMES = 12
"""The LED is on in the first 12 frames of the fixture clip and off in the rest."""


def fixture_motion(t: FloatArray) -> FloatArray:
    """50 mm/s in +x through the plane origin, from -2.5 mm."""
    t = np.asarray(t, dtype=np.float64)
    return np.column_stack((-2.5 + 50.0 * t, np.full(len(t), 0.4)))


def fixture_frames() -> list[Frame]:
    """The BGR frames of the fixture clip at 240 frames per second, with no noise and a global shutter."""
    scene = Scene(
        fixture_camera(),
        "wacom",
        row_time_s=0.0,
        rate_hz=240.0,
        offset_mm=(0.0, 0.0),
        noise=0.0,
        led=FIXTURE_LED,
    )
    frames: list[Frame] = []
    for k, frame in enumerate(
        scene.frames(fixture_motion, FIXTURE_FRAMES, missing=frozenset({FIXTURE_MISSING}))
    ):
        frame = frame.copy()
        led = FIXTURE_LED.crop(frame)
        led[led > 0] = _LED_ON if k < FIXTURE_LED_FRAMES else _LED_OFF
        frames.append(frame)
    frames[FIXTURE_DUPLICATE] = frames[FIXTURE_DUPLICATE - 1]
    return [np.asarray(cv2.cvtColor(f, cv2.COLOR_GRAY2BGR), dtype=np.uint8) for f in frames]


def write_fixture_clip(folder: Path) -> Path:
    """Write the fixture clip with the lossless FFV1 codec of OpenCV, in AVI, whose time base is the
    frame period. Matroska would round the times to 1 ms."""
    path = folder / f"{FIXTURE_RUN}.avi"
    w, h = fixture_camera().size
    writer = cv2.VideoWriter(str(path), cv2.CAP_FFMPEG, cv2.VideoWriter.fourcc(*"FFV1"), 240.0, (w, h))
    if not writer.isOpened():
        raise RuntimeError("OpenCV cannot write FFV1.")
    for frame in fixture_frames():
        writer.write(frame)
    writer.release()
    return path


if __name__ == "__main__":
    print(write_fixture_clip(Path(sys.argv[1])))
