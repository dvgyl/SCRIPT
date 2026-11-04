"""The lens and the plane calibration of protocol section 7.2, and the maps between pixels and the plane.

The board has `pattern = (cols, rows)` inner corners: `cols` along the board x axis and `rows` along y.
The two counts must differ, so that the detector cannot exchange the axes. The board origin is the inner
corner with the smallest x and y, that is, the lower-left inner corner seen with x to the right and y up.
The operator puts it on the marked grid point and the board x axis along the device x axis.

The plane step must know which way the board x axis points in the image, because a board looks the same
after a half turn. The operator names the image direction of device +x within 45 degrees. The camera
looks down at the device, so plane y is then a quarter turn from x, counterclockwise as seen on the
device. A camera that mirrors its image breaks this rule. Phone rear cameras and machine-vision cameras
do not mirror.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from scriptkin.bench.camerafiles import (
    MAX_PLANE_RESIDUAL_MM,
    MIN_LENS_VIEWS,
    CameraFileError,
    Frame,
    Lens,
    Plane,
)
from scriptkin.quality import Refusal, RefusalCode
from scriptkin.recording import FloatArray

Direction = Literal["right", "left", "up", "down"]
DIRECTIONS: dict[Direction, tuple[float, float]] = {
    "right": (1.0, 0.0),
    "left": (-1.0, 0.0),
    "up": (0.0, -1.0),
    "down": (0.0, 1.0),
}
_SUBPIX_WINDOW = (11, 11)
_SUBPIX_CRITERIA = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-4)
_UNDISTORT_CRITERIA = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-12)


def grey(frame: Frame) -> Frame:
    return frame if frame.ndim == 2 else np.asarray(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), dtype=np.uint8)


def read_image(path: str | Path) -> Frame:
    """A still image as grey levels, as the file stores it. The Exif orientation does not turn it, as the
    rotation metadata does not turn a clip (`read_video`)."""
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
    if image is None:
        raise CameraFileError(f"{Path(path).name}: OpenCV cannot read the image.")
    return np.asarray(image, dtype=np.uint8)


def find_corners(image: Frame, pattern: tuple[int, int]) -> FloatArray | None:
    """The inner corners of the board in sub-pixel image positions, as (rows, cols, 2), or None.

    A row of the result runs along the board x axis. The detector may return the rows and the columns in
    either direction.
    """
    g = grey(image)
    found, corners = cv2.findChessboardCorners(
        g, pattern, None, cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
    )
    if not found:
        return None
    refined = cv2.cornerSubPix(g, corners, _SUBPIX_WINDOW, (-1, -1), _SUBPIX_CRITERIA)
    return np.asarray(refined, dtype=np.float64).reshape(pattern[1], pattern[0], 2)


def board_points(pattern: tuple[int, int], pitch_mm: float) -> FloatArray:
    """The board position of each inner corner, in millimetres, as (rows, cols, 2)."""
    cols, rows = pattern
    j, i = np.mgrid[0:rows, 0:cols]
    return np.stack((i, j), axis=-1).astype(np.float64) * pitch_mm


def calibrate_lens(views: Iterable[Frame], pattern: tuple[int, int], pitch_mm: float) -> Lens | Refusal:
    """The intrinsic calibration from `MIN_LENS_VIEWS` or more views of the board, with `calibrateCamera`.

    Every view must show the whole board. A view without it is refused by its index, so that the operator
    removes it and the calibration uses only the views that the operator chose.
    """
    size: tuple[int, int] | None = None
    image_points: list[np.ndarray] = []
    for index, view in enumerate(views):
        view_size = (view.shape[1], view.shape[0])
        if size is not None and view_size != size:
            return Refusal(RefusalCode.OUT_OF_RANGE, f"View {index} has another image size than view 0.")
        size = view_size
        corners = find_corners(view, pattern)
        if corners is None:
            return Refusal(RefusalCode.UNDEFINED, f"The board corners were not found in view {index}.")
        image_points.append(corners.reshape(-1, 1, 2).astype(np.float32))
    if size is None or len(image_points) < MIN_LENS_VIEWS:
        return Refusal(
            RefusalCode.TOO_FEW_VALUES, f"An intrinsic calibration needs {MIN_LENS_VIEWS} or more views."
        )
    flat = board_points(pattern, pitch_mm).reshape(-1, 2)
    objects = np.column_stack((flat, np.zeros(len(flat)))).astype(np.float32).reshape(-1, 1, 3)
    rms, matrix, distortion, _, _ = cv2.calibrateCamera(
        [objects] * len(image_points), image_points, size, None, None
    )
    matrix = np.asarray(matrix, dtype=np.float64)
    distortion = np.asarray(distortion, dtype=np.float64).ravel()
    if not (math.isfinite(rms) and np.isfinite(matrix).all() and np.isfinite(distortion).all()):
        return Refusal(RefusalCode.NONFINITE, "The intrinsic calibration did not converge.")
    return Lens(size, matrix, distortion, float(rms), len(image_points))


def undistort(points_px: FloatArray, lens: Lens) -> FloatArray:
    """Remove the lens distortion: raw pixels to the pixels of the ideal camera with the same matrix."""
    points = np.asarray(points_px, dtype=np.float64).reshape(-1, 1, 2)
    ideal = cv2.undistortPoints(
        points, lens.matrix, lens.distortion, None, None, lens.matrix, criteria=_UNDISTORT_CRITERIA
    )
    return np.asarray(ideal, dtype=np.float64).reshape(-1, 2)


def distort(points_px: FloatArray, lens: Lens) -> FloatArray:
    """Add the lens distortion: ideal pixels to raw pixels."""
    ideal = np.asarray(points_px, dtype=np.float64).reshape(-1, 2)
    rays = np.column_stack((ideal, np.ones(len(ideal)))) @ np.linalg.inv(lens.matrix).T
    raw, _ = cv2.projectPoints(rays, np.zeros(3), np.zeros(3), lens.matrix, lens.distortion)
    return np.asarray(raw, dtype=np.float64).reshape(-1, 2)


def _transform(points: FloatArray, homography: FloatArray) -> FloatArray:
    mapped = cv2.perspectiveTransform(np.asarray(points, dtype=np.float64).reshape(-1, 1, 2), homography)
    return np.asarray(mapped, dtype=np.float64).reshape(-1, 2)


def to_plane(points_px: FloatArray, plane: Plane) -> FloatArray:
    """Raw pixels to plane millimetres."""
    return _transform(undistort(points_px, plane.lens), plane.homography)


def to_image(points_mm: FloatArray, plane: Plane) -> FloatArray:
    """Plane millimetres to raw pixels."""
    return distort(_transform(points_mm, np.linalg.inv(plane.homography)), plane.lens)


def orient_corners(corners: FloatArray, x_direction: Direction) -> FloatArray | Refusal:
    """Order the detected corners so that `[j, i]` is the corner at plane (i, j) times the pitch."""
    along_x = np.mean(corners[:, 1:] - corners[:, :-1], axis=(0, 1))
    along_y = np.mean(corners[1:, :] - corners[:-1, :], axis=(0, 1))
    named = np.array(DIRECTIONS[x_direction])
    cosine = float(along_x @ named / np.linalg.norm(along_x))
    if abs(cosine) < math.cos(math.radians(45)):
        return Refusal(
            RefusalCode.UNDEFINED, f"The board x axis is more than 45 degrees from '{x_direction}'."
        )
    unit_x = along_x / np.linalg.norm(along_x) * math.copysign(1.0, cosine)
    unit_y = np.array([unit_x[1], -unit_x[0]])
    oriented = corners if cosine > 0 else corners[:, ::-1]
    return oriented if along_y @ unit_y > 0 else oriented[::-1, :]


def calibrate_plane(
    image: Frame, lens: Lens, pattern: tuple[int, int], pitch_mm: float, x_direction: Direction = "right"
) -> Plane | Refusal:
    """The plane calibration of one mounting from one view of the board in the marker plane.

    The homography maps the undistorted corners to the board positions at the measured pitch, by least
    squares over every corner. A plane whose residual exceeds `MAX_PLANE_RESIDUAL_MM` is refused.
    """
    cols, rows = pattern
    if cols == rows:
        return Refusal(
            RefusalCode.UNDEFINED, "The board needs a different number of inner corners on each axis."
        )
    if (image.shape[1], image.shape[0]) != lens.image_size:
        return Refusal(RefusalCode.OUT_OF_RANGE, "The board image size differs from the lens image size.")
    corners = find_corners(image, pattern)
    if corners is None:
        return Refusal(RefusalCode.UNDEFINED, "The board corners were not found.")
    oriented = orient_corners(corners, x_direction)
    if isinstance(oriented, Refusal):
        return oriented
    source = undistort(oriented.reshape(-1, 2), lens)
    target = board_points(pattern, pitch_mm).reshape(-1, 2)
    # findHomography returns None when it cannot fit, although its stub says otherwise.
    fitted = np.asarray(cv2.findHomography(source, target, 0)[0], dtype=np.float64)
    if fitted.shape != (3, 3) or not np.isfinite(fitted).all():
        return Refusal(RefusalCode.UNDEFINED, "The homography could not be fitted.")
    residual = float(np.sqrt(np.mean(np.sum((_transform(source, fitted) - target) ** 2, axis=1))))
    if not residual <= MAX_PLANE_RESIDUAL_MM:
        return Refusal(
            RefusalCode.OUT_OF_RANGE,
            f"The plane residual is {residual:.4f} mm RMS. "
            f"A valid plane has {MAX_PLANE_RESIDUAL_MM} mm or less.",
        )
    return Plane(lens, fitted, residual, pattern, pitch_mm)
